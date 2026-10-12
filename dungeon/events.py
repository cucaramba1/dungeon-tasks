"""Афиша Нячанга: сбор анонсов из Telegram-каналов и отбор под интересы. Без LLM.

Пост -> дата (тег «#дата: 28.09» или текст), время, категория по словарю, исключения.
Одно событие часто постят в 2-3 канала, склеиваем по дате и началу текста.
Оценки 👍/👎 двигают вес категории: так фильтр подстраивается.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta

from . import config, tgmcp
from .db import DB, iso, parse_iso, utcnow
from .parser import _month

log = logging.getLogger(__name__)

WEEKDAYS = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресень"]
DATE_TAG = re.compile(r"#\s*дата\s*:?\s*(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?", re.I)
DATE_NUM = re.compile(r"(?<![\d.:])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d:])")
DATE_WORD = re.compile(r"(?<!\d)(\d{1,2})\s+(январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)"
                       r"[а-я]*", re.I)
TIME_COLON = re.compile(r"(?<![\d.])([01]?\d|2[0-3])[:：]([0-5]\d)(?![\d])")
TIME_AFTER_DATE = re.compile(r"(?:\d{1,2}\s+[а-я]{3,}|\d{1,2}[./]\d{1,2})[,\s|]+([01]?\d|2[0-3])[.:]([0-5]\d)\b", re.I)
TIME_WORD = re.compile(r"(?:\bв|\bс|начало|время|старт|сбор)\s*:?\s*([01]?\d|2[0-3])[.\-]([0-5]\d)\b", re.I)
PRICE = re.compile(r"(бесплатн\w*|донейш\w*|донат\w*|вход свободный|free entry|"
                   r"\d[\d .]*\s?(?:к|k|000)?\s?(?:донг\w*|vnd|vnđ|₫|к\b|k\b))", re.I)
BASE_WEIGHT = 1.0
NO_CATEGORY_WEIGHT = 0.3


def cfg() -> dict:
    return config.yaml_file("events")


def _stem_hit(text: str, stems: list[str]) -> str | None:
    for s in stems:
        if re.search(r"(?<![\w])" + re.escape(s.lower()).replace("е", "[её]"), text):
            return s
    return None


def category(text: str) -> str | None:
    """Сначала по заголовку (первая непустая строка), потом по всему тексту:
    «Пробежка / Бежим до Театра» — это спорт, а не культура."""
    head = next((ln for ln in text.splitlines() if ln.strip()), "").lower()
    for part in (head, text.lower()):
        for cat, stems in cfg()["categories"].items():
            if _stem_hit(part, stems):
                return cat
    return None


def excluded(text: str) -> str | None:
    return _stem_hit(text.lower(), cfg()["exclude"])


def _date(y: int, m: int, d: int) -> datetime | None:
    try:
        return datetime(y, m, d)
    except ValueError:
        return None


def event_date(text: str, posted: datetime) -> datetime | None:
    """Дата события (без времени, naive) относительно даты поста."""
    tz = posted.tzinfo
    base = posted.astimezone(tz).replace(tzinfo=None, hour=0, minute=0, second=0, microsecond=0)

    def fix(d: datetime | None) -> datetime | None:
        if d and d < base - timedelta(days=1):  # «5.01» в декабре = следующий год
            d = d.replace(year=d.year + 1)
        return d

    if m := DATE_TAG.search(text):
        y = int(m.group(3)) if m.group(3) else base.year
        return fix(_date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1))))
    low = text.lower()
    candidates = []
    for m in DATE_WORD.finditer(low):
        d = _date(base.year, _month(m.group(2)), int(m.group(1)))
        candidates.append(fix(d))
    for m in DATE_NUM.finditer(low):
        if int(m.group(2)) <= 12:
            y = int(m.group(3)) if m.group(3) else base.year
            candidates.append(fix(_date(y + 2000 if y < 100 else y, int(m.group(2)), int(m.group(1)))))
    candidates = [c for c in candidates if c and base <= c <= base + timedelta(days=60)]
    if candidates:
        return min(candidates)
    if re.search(r"\bсегодня\b", low):
        return base
    if re.search(r"\bзавтра\b", low):
        return base + timedelta(days=1)
    for i, stem in enumerate(WEEKDAYS):
        if re.search(r"\b(?:в|во|этот|это|эту|ближайш\w*)?\s*" + stem, low):
            return base + timedelta(days=(i - base.weekday()) % 7)
    return None


def event_time(text: str) -> tuple[int, int] | None:
    for rx in (TIME_COLON, TIME_WORD, TIME_AFTER_DATE):
        if m := rx.search(text):
            return int(m.group(1)), int(m.group(2))
    return None


def title_of(text: str) -> str:
    for line in text.splitlines():
        clean = re.sub(r"[^\w\s«»\"'!?.,:&()-]", "", line).strip(" -—:.")
        if len(clean) >= 4:
            return clean[:90]
    return text[:90]


def price_of(text: str) -> str | None:
    m = PRICE.search(text)
    return m.group(0).strip() if m else None


def dedup_key(day: datetime, text: str) -> str:
    norm = re.sub(r"[^а-яёa-z0-9]", "", text.lower())[:80]
    return day.strftime("%Y%m%d") + ":" + hashlib.md5(norm.encode()).hexdigest()[:12]


def link(ch: dict, msg_id: int) -> str:
    if ch.get("username"):
        return f"https://t.me/{ch['username']}/{msg_id}"
    return f"https://t.me/c/{str(ch['id']).removeprefix('-100')}/{msg_id}"


def parse_post(post: dict, ch: dict, tz) -> dict | None:
    """Пост из get_history -> событие или None (не событие / исключено / прошло)."""
    text = (post.get("text") or "").strip()
    if len(text) < 40:
        return None
    if excluded(text):
        return None
    posted = datetime.fromisoformat(post["date"]).astimezone(tz)
    day = event_date(text, posted)
    if not day:
        return None
    cat = category(text)
    if ch.get("chat") and not cat:
        return None  # в групповых чатах без категории это болтовня, а не анонс
    t = event_time(text)
    start = datetime.combine(day.date(), datetime.min.time(), tz)
    if t:
        start = start.replace(hour=t[0], minute=t[1])
    return {
        "key": dedup_key(day, text),
        "chat_id": ch["id"],
        "channel": ch["name"],
        "msg_id": post["id"],
        "link": link(ch, post["id"]),
        "starts_at": start,
        "has_time": bool(t),
        "title": title_of(text),
        "text": text[:700],
        "category": cat,
        "price": price_of(text),
    }


class Events:
    def __init__(self, db: DB):
        self.db = db

    def save(self, ev: dict) -> bool:
        if self.db.one("SELECT 1 FROM events_feed WHERE key=?", (ev["key"],)):
            return False
        self.db.x("INSERT INTO events_feed(key, chat_id, channel, msg_id, link, starts_at, has_time, title, text,"
                  " category, price, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                  (ev["key"], ev["chat_id"], ev["channel"], ev["msg_id"], ev["link"], iso(ev["starts_at"]),
                   int(ev["has_time"]), ev["title"], ev["text"], ev["category"], ev["price"], iso(utcnow())))
        return True

    async def collect(self, tz, fetch=tgmcp.history, pause: float = 1.0) -> dict:
        """Пройти по каналам, забрать новые посты. Возвращает статистику."""
        stats = {"posts": 0, "events": 0, "errors": 0}
        fails = 0
        skip = {c["id"] for c in cfg().get("targeted", [])}
        for ch in cfg()["channels"]:
            if ch["id"] in skip:  # эти чаты читает rituals.py
                continue
            last = int(self.db.get(f"ev_last:{ch['id']}", "0") or 0)
            try:
                posts = await fetch(ch["id"], 30 if last else 50)
            except Exception as e:
                stats["errors"] += 1
                fails += 1
                log.warning("events: %s не прочитался: %s", ch["name"], str(e)[:200])
                if fails >= 2:  # telegram-mcp лежит: не ждём по минуте на каждый канал
                    stats["aborted"] = True
                    break
                continue
            fails = 0
            fresh = [p for p in posts if p.get("id", 0) > last]
            stats["posts"] += len(fresh)
            for p in fresh:
                ev = parse_post(p, ch, tz)
                if ev and self.save(ev):
                    stats["events"] += 1
            if posts:
                self.db.set(f"ev_last:{ch['id']}", max(p.get("id", 0) for p in posts))
            await asyncio.sleep(pause)  # не душим telegram-mcp
        self.db.x("DELETE FROM events_feed WHERE kind='afisha' AND starts_at<?", (iso(utcnow() - timedelta(days=14)),))
        return stats

    # --- отбор ---
    def weight(self, cat: str | None) -> float:
        if not cat:
            return NO_CATEGORY_WEIGHT
        return float(self.db.get(f"ev_w:{cat}", str(BASE_WEIGHT)) or BASE_WEIGHT)

    def vote(self, event_id: int, up: bool) -> dict | None:
        ev = self.db.one("SELECT * FROM events_feed WHERE id=?", (event_id,))
        if not ev:
            return None
        self.db.x("UPDATE events_feed SET vote=? WHERE id=?", (1 if up else -1, event_id))
        if ev["category"]:
            w = self.weight(ev["category"]) + (0.2 if up else -0.3)
            self.db.set(f"ev_w:{ev['category']}", round(max(0.05, min(3.0, w)), 2))
        return dict(ev)

    def upcoming(self, frm: datetime, to: datetime, limit: int = 10) -> list[dict]:
        """События в окне, лучшие первыми. Отвергнутые и уже предложенные сегодня не берём."""
        rows = self.db.q("SELECT * FROM events_feed WHERE starts_at>=? AND starts_at<? AND vote>=0 AND kind='afisha'",
                         (iso(frm), iso(to)))
        scored = sorted(rows, key=lambda r: (-self.weight(r["category"]) - 0.5 * r["vote"], r["starts_at"]))
        return [dict(r) for r in scored[:limit]]

    def get(self, event_id: int):
        return self.db.one("SELECT * FROM events_feed WHERE id=?", (event_id,))


def human(ev, tz) -> str:
    start = parse_iso(ev["starts_at"]).astimezone(tz)
    when = f"{start:%d.%m %H:%M}" if ev["has_time"] else f"{start:%d.%m}"
    icon = {"кино": "🎬", "настолки": "🎲", "вечеринка": "🎶", "культура": "🎭", "спорт": "🏃",
            "поездка": "🌴", "нетворкинг": "🤝", "языки": "🗣"}.get(ev["category"], "📌")
    price = f" · {ev['price']}" if ev["price"] else ""
    return f"{icon} {ev['title']}\n{when}{price} · {ev['channel']}"
