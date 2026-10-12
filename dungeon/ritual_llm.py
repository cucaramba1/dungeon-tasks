"""Контракт с claude -p для точечных чатов: что просим, в каком виде ответ, как его проверяем."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from . import coach

SYSTEM = ("Ты разбираешь посты из Telegram-чатов Нячанга (Вьетнам, UTC+7) про встречи, занятия и мероприятия. "
          "Отвечай строго по схеме. Ничего не выдумывай: чего нет в посте, там null.")

INSTRUCTIONS = """Тебе дан JSON: чат, сегодняшняя дата, новые посты (posts) и уже известные события чата (known_events).
Для КАЖДОГО поста про встречу, занятие или мероприятие верни один элемент items. Посты не про встречи (болтовня, \
благодарности, опросы) пропусти или верни kind=none.

kind:
- announce: объявлена встреча или занятие. Фраза «следующая встреча пройдет 19.10 в 11.00» тоже announce.
- move: встречу перенесли на другое время или день. date/time = НОВЫЕ, orig_date = старая дата, если она названа или \
однозначно следует из known_events.
- cancel: встречи не будет. date = дата отменённой встречи, если названа («завтра» считай от даты поста), иначе null.

Поля: msg_id (как в посте), title (коротко, 2-6 слов, что за встреча), date (YYYY-MM-DD), time (HH:MM, 24 часа, \
если время не названо то null), place, book (название книги, если встреча про разбор книги), description (о чём \
встреча, 1-2 фразы, если пост это объясняет), orig_date, ref, confidence (0..1: насколько ты уверен в дате и типе).
Даты считай от даты ПОСТА (поле date у поста, там же день недели): «завтра», «в среду», «в эту субботу».
ref: если move или cancel относится к событию из known_events, верни его ref (например e12 или t34), иначе null.
Если пост про перенос, а старая дата нигде не видна, orig_date = null, не угадывай.
Тема встречи («обсудим систему планирования 12 недель») идёт в description, а не в book. book только если названа книга."""

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "msg_id": {"type": "integer"},
            "kind": {"type": "string", "enum": ["announce", "move", "cancel", "none"]},
            "title": {"type": "string"},
            "date": {"type": ["string", "null"]},
            "time": {"type": ["string", "null"]},
            "orig_date": {"type": ["string", "null"]},
            "ref": {"type": ["string", "null"]},
            "place": {"type": ["string", "null"]},
            "book": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
        },
        "required": ["msg_id", "kind"],
    }}},
    "required": ["items"],
}


@dataclass
class Item:
    msg_id: int
    kind: str
    title: str
    start: datetime | None
    has_time: bool
    orig_date: date | None
    ref: str | None
    place: str | None
    book: str | None
    description: str | None
    confidence: float
    needs_check: bool
    raw: dict = field(default_factory=dict)


def _date(s) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _time(s) -> time | None:
    try:
        return time.fromisoformat(s) if s else None
    except ValueError:
        return None


def parse_items(raw: dict, tz, now: datetime) -> list[Item]:
    """Проверяем ответ модели: лишнее выкидываем, сомнительное помечаем needs_check."""
    out: list[Item] = []
    for r in (raw or {}).get("items", []):
        kind, msg_id = r.get("kind"), r.get("msg_id")
        if kind not in ("announce", "move", "cancel") or not isinstance(msg_id, int):
            continue
        d, t = _date(r.get("date")), _time(r.get("time"))
        if kind in ("announce", "move") and not d:
            continue  # без даты это не анонс, а перенос без новой даты применить нечем
        start = datetime.combine(d, t or time(0, 0), tz) if d else None
        conf = float(r.get("confidence") or 0)
        past = bool(start and kind != "cancel" and start.date() < now.date())
        out.append(Item(msg_id=msg_id, kind=kind, title=(r.get("title") or "").strip() or "Встреча", start=start,
                        has_time=bool(t), orig_date=_date(r.get("orig_date")), ref=r.get("ref") or None,
                        place=r.get("place") or None, book=r.get("book") or None,
                        description=r.get("description") or None, confidence=conf,
                        needs_check=conf < 0.6 or past, raw=r))
    return out


async def classify(chat: dict, posts: list[dict], known: list[dict], now: datetime, ask=None) -> list[Item]:
    ask = ask or coach.ask_claude
    data = {"chat": chat["name"], "today": now.date().isoformat(), "posts": posts, "known_events": known}
    raw = await ask(INSTRUCTIONS, data, SCHEMA, system=SYSTEM)
    return parse_items(raw, now.tzinfo, now)
