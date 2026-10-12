"""Точечные чаты: анонсы ритуалов и событий от конкретных людей.

Модуль без Telegram-бота: читает чаты через telegram-mcp, кладёт строки в events_feed (kind announce/move/cancel),
меняет задачи по решениям Некита. Сообщения шлёт ritual_flows.py. Разбор постов делает ritual_llm.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta, timezone  # noqa: F401

from . import config, ritual_llm, tgmcp
from .db import DB, iso, parse_iso, utcnow  # noqa: F401
from .parser import Parsed  # noqa: F401
from .services import Service, hm  # noqa: F401

log = logging.getLogger(__name__)
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
FIRST_RUN_DAYS = 7
DIGEST_CAP = 10


def cfg() -> list[dict]:
    return config.yaml_file("events").get("targeted", [])


def chat_cfg(chat_id: int) -> dict | None:
    return next((c for c in cfg() if c["id"] == chat_id), None)


def author_ok(chat: dict, post: dict) -> bool:
    if chat.get("channel"):
        return True  # канал пишет от своего имени: берём все посты
    return (post.get("username") or "").lower() in {a.lower() for a in chat.get("authors", [])}


def usable(chat: dict, post: dict, since: datetime | None) -> bool:
    """Пост с текстом от нужного автора (не опрос, не служебное, не старше окна первого запуска)."""
    if len((post.get("text") or "").strip()) < 8 or post.get("action") or not author_ok(chat, post):
        return False
    return since is None or datetime.fromisoformat(post["date"]) >= since


def sources(chat: dict) -> list[tuple[int | None, str | None]]:
    """Что читать в чате: форум = его топики с подписями, обычный чат = один поток."""
    topics = chat.get("topics")
    return list(topics.items()) if topics else [(None, None)]


def chat_link(chat: dict, msg_id: int) -> str:
    if chat.get("username"):
        return f"https://t.me/{chat['username']}/{msg_id}"
    cid = str(chat["id"])
    cid = cid.removeprefix("-100") if cid.startswith("-100") else cid.lstrip("-")
    return f"https://t.me/c/{cid}/{msg_id}"


def as_date(v) -> date | None:
    if not v:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


class Rituals:
    def __init__(self, db: DB, svc: Service):
        self.db = db
        self.svc = svc

    @property
    def tz(self):
        return self.svc.clock().tzinfo

    def feed(self, row_id: int):
        return self.db.one("SELECT * FROM events_feed WHERE id=?", (row_id,))

    # ---------- что уже известно по чату ----------
    def occurrence_task(self, chat: dict):
        """Ближайшая открытая запись серии, к которой привязан чат (D&D, йога)."""
        if not chat.get("series"):
            return None
        return self.db.one("SELECT * FROM tasks WHERE is_habit=1 AND status='open' AND lower(title)=lower(?) "
                           "ORDER BY due_at LIMIT 1", (chat["series"],))

    def known(self, chat: dict, now: datetime) -> list[dict]:
        """Что в этом чате можно переносить и отменять: открытые анонсы + запись серии. Это же контекст для LLM."""
        rows = self.db.q("SELECT id, title, starts_at, task_id FROM events_feed WHERE chat_id=? AND kind='announce' "
                         "AND status IN ('new','accepted') AND starts_at>=? ORDER BY starts_at",
                         (chat["id"], iso(now - timedelta(days=1))))
        fmt = lambda s: parse_iso(s).astimezone(now.tzinfo).strftime("%Y-%m-%d %H:%M")  # noqa: E731
        out = [{"ref": f"e{r['id']}", "title": r["title"], "date": fmt(r["starts_at"])} for r in rows]
        t = self.occurrence_task(chat)
        if t and t["id"] not in {r["task_id"] for r in rows}:
            out.append({"ref": f"t{t['id']}", "title": t["title"], "date": fmt(t["due_at"])})
        return out

    # ---------- сбор ----------
    def _last_key(self, chat_id: int, topic: int | None) -> str:
        return f"rt_last:{chat_id}:{topic or 0}"

    async def collect(self, tz, now: datetime | None = None, fetch=tgmcp.history, ask=None, pause: float = 1.0) -> dict:
        """Пройти по точечным чатам, разобрать новые посты нужных авторов. LLM зовём только если есть что разбирать."""
        now = now or datetime.now(tz)
        stats = {"posts": 0, "items": 0, "errors": 0}
        fails = 0
        for chat in cfg():
            fresh: list[tuple[dict, str | None]] = []
            marks: dict[str, int] = {}
            try:
                for topic, label in sources(chat):
                    key = self._last_key(chat["id"], topic)
                    last = int(self.db.get(key, "0") or 0)
                    posts = await fetch(chat["id"], 30 if last else 100, topic)
                    marks[key] = max([p.get("id", 0) for p in posts] + [last])
                    floor = None if last else now - timedelta(days=FIRST_RUN_DAYS)
                    fresh += [(p, label) for p in posts if p.get("id", 0) > last and usable(chat, p, floor)]
            except Exception as e:
                stats["errors"] += 1
                fails += 1
                log.warning("rituals: %s не прочитался: %s", chat["name"], str(e)[:200])
                if fails >= 2:  # telegram-mcp лежит: не ждём по минуте на каждый чат
                    stats["aborted"] = True
                    break
                continue
            fails = 0
            stats["posts"] += len(fresh)
            if fresh:
                payload = [self._payload(p, label, tz) for p, label in fresh]
                try:
                    items = await ritual_llm.classify(chat, payload, self.known(chat, now), now, ask=ask)
                except Exception as e:  # claude не ответил: курсор не двигаем, посты разберём в следующий раз
                    stats["errors"] += 1
                    stats["claude_down"] = True
                    log.warning("rituals: claude не разобрал %s: %s", chat["name"], str(e)[:200])
                    continue
                by_id = {p["id"]: (p, label) for p, label in fresh}
                for it in items:
                    if it.msg_id in by_id:
                        stats["items"] += self.ingest(chat, *by_id[it.msg_id], it, now)
            for key, mark in marks.items():
                self.db.set(key, mark)
            await asyncio.sleep(pause)  # не душим telegram-mcp
        return stats

    @staticmethod
    def _payload(post: dict, label: str | None, tz) -> dict:
        d = datetime.fromisoformat(post["date"]).astimezone(tz)
        wd = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][d.weekday()]
        return {"msg_id": post["id"], "date": d.strftime("%Y-%m-%d %H:%M"), "weekday": wd,
                "author": post.get("username"), "topic": label, "text": (post.get("text") or "")[:1500]}

    # ---------- разбор одного элемента ----------
    def _relevant(self, chat: dict, it) -> bool:
        """Занятия вне наших дней (пятничная йога) и после until не интересуют."""
        until, days = as_date(chat.get("until")), chat.get("attend_days")
        dates = [d for d in (it.start.date() if it.start else None, it.orig_date) if d]
        if until and dates and all(d > until for d in dates):
            return False
        if days and dates and not any(DAYS[d.weekday()] in days for d in dates):
            return False
        return True

    def find_target(self, chat: dict, it, now: datetime):
        """Какое известное событие двигают или отменяют. Возвращает (ref_id, ref_task, кандидаты)."""
        cands = self.known(chat, now)
        by_ref = {c["ref"]: c for c in cands}
        pick = None
        if it.ref in by_ref:
            pick = it.ref
        else:
            day = it.orig_date or (it.start.date() if it.kind == "cancel" and it.start else None)
            pool = [c for c in cands if c["date"][:10] == day.isoformat()] if day else cands
            if len(pool) == 1:
                pick = pool[0]["ref"]
        if not pick:
            return None, None, cands
        return (int(pick[1:]), None, cands) if pick[0] == "e" else (None, int(pick[1:]), cands)

    def _insert(self, base: dict, **cols) -> int:
        data = {**base, **cols, "created_at": iso(utcnow())}
        return self.db.x(f"INSERT INTO events_feed({','.join(data)}) VALUES({','.join('?' * len(data))})",
                         tuple(data.values()))

    def ingest(self, chat: dict, post: dict, label: str | None, it, now: datetime) -> int:
        """Положить разобранный пост в events_feed. Возвращает 1, если появилась новая строка."""
        key = f"rt:{chat['id']}:{post['id']}"
        if self.db.one("SELECT 1 FROM events_feed WHERE key=?", (key,)) or not self._relevant(chat, it):
            return 0
        extra = {k: v for k, v in {"book": it.book, "place": it.place, "description": it.description,
                                   "variant": label}.items() if v}
        base = {"key": key, "chat_id": chat["id"], "channel": chat["name"], "msg_id": post["id"],
                "link": chat_link(chat, post["id"]), "title": it.title[:120], "text": (post.get("text") or "")[:700],
                "author": post.get("username"), "ritual": chat["name"], "kind": it.kind,
                "llm_raw": json.dumps(it.raw, ensure_ascii=False), "needs_check": int(it.needs_check)}
        if it.kind == "announce":
            if self.db.one("SELECT 1 FROM events_feed WHERE chat_id=? AND kind='announce' AND starts_at=? "
                           "AND status NOT IN ('declined','expired')", (chat["id"], iso(it.start))):
                return 0  # автор поправил или повторил анонс
            self._insert(base, extra=json.dumps(extra, ensure_ascii=False), status="new",
                         starts_at=iso(it.start), has_time=int(it.has_time))
            return 1
        ref_id, ref_task, cands = self.find_target(chat, it, now)
        status = "new" if (ref_id or ref_task) else ("ambiguous" if cands else "unresolved")
        if status == "ambiguous":
            extra["candidates"] = cands
        start = it.start or self._target_start(ref_id, ref_task) or datetime.fromisoformat(post["date"])
        self._insert(base, extra=json.dumps(extra, ensure_ascii=False), status=status, starts_at=iso(start),
                     has_time=int(it.has_time), ref_id=ref_id, ref_task=ref_task)
        return 1

    def _target_start(self, ref_id: int | None, ref_task: int | None) -> datetime | None:
        if ref_id and (e := self.feed(ref_id)):
            return parse_iso(e["starts_at"])
        if ref_task and (t := self.svc.get(ref_task)) and t["due_at"]:
            return parse_iso(t["due_at"])
        return None
