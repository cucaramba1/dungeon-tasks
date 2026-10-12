"""Точечные чаты: анонсы ритуалов и событий от конкретных людей.

Модуль без Telegram-бота: читает чаты через telegram-mcp, кладёт строки в events_feed (kind announce/move/cancel),
меняет задачи по решениям Некита. Сообщения шлёт ritual_flows.py. Разбор постов делает ritual_llm.py.
"""
from __future__ import annotations

import asyncio  # noqa: F401  (нужен collect, Task 5)
import json  # noqa: F401
import logging
from datetime import date, datetime, time, timedelta, timezone  # noqa: F401

from . import config, tgmcp  # noqa: F401
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
    return f"https://t.me/c/{str(chat['id']).removeprefix('-100')}/{msg_id}"


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
