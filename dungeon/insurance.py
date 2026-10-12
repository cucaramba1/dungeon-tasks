"""Страховка на случай, когда ноут спит.

Для каждой напоминалки ставим отложенное сообщение в «Избранное» своего аккаунта.
Telegram присылает такие сообщения как «Напоминание» даже при закрытом ноуте.
Если ноут бодрствует, за ~90 секунд до срока страховка снимается,
и напоминание шлёт бот (с кнопками), без дублей.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from . import config
from .db import DB, iso, parse_iso, utcnow

log = logging.getLogger(__name__)
HORIZON = timedelta(days=7)
DISARM_BEFORE = timedelta(seconds=90)
HABIT_EXTRA = (timedelta(minutes=30), timedelta(minutes=60))


class Insurance:
    def __init__(self, db: DB):
        self.db = db
        self.client: TelegramClient | None = None

    @property
    def active(self) -> bool:
        return self.client is not None

    async def start(self) -> None:
        if not (config.TG_API_ID and config.TG_API_HASH and config.TG_INSURANCE_SESSION):
            log.info("insurance: выключена (нет TG_API_ID / TG_INSURANCE_SESSION)")
            return
        client = TelegramClient(StringSession(config.TG_INSURANCE_SESSION), config.TG_API_ID,
                                config.TG_API_HASH, device_model="Dungeon Tasks", app_version="1.0")
        try:
            await client.connect()
            if not await client.is_user_authorized():
                log.warning("insurance: сессия не авторизована, запусти scripts/telethon_login.py")
                await client.disconnect()
                return
        except Exception:
            log.exception("insurance: не подключилась")
            return
        self.client = client
        log.info("insurance: активна")

    async def stop(self) -> None:
        if self.client:
            await self.client.disconnect()

    async def cancel(self, task_id: int) -> None:
        rows = self.db.q("SELECT msg_id, fire_at FROM insurance WHERE task_id=?", (task_id,))
        self.db.x("DELETE FROM insurance WHERE task_id=?", (task_id,))
        await self._delete([r["msg_id"] for r in rows if parse_iso(r["fire_at"]) > utcnow()])

    async def _delete(self, ids: list[int]) -> None:
        if not ids or not self.client:
            return
        try:
            await self.client(functions.messages.DeleteScheduledMessagesRequest(
                peer=types.InputPeerSelf(), id=ids))
        except Exception:
            log.exception("insurance: не удалось снять %s", ids)

    async def sync(self, task) -> None:
        """Привести страховку задачи в соответствие с её состоянием."""
        if not self.client or task is None:
            return
        await self.cancel(task["id"])
        if task["status"] != "open":
            return
        at = parse_iso(task["next_nag_at"] or task["remind_at"])
        now = utcnow()
        if not at or at - now < timedelta(minutes=2) or at - now > HORIZON:
            return
        times = [at] + ([at + d for d in HABIT_EXTRA] if task["is_habit"] else [])
        icon = "🕯" if task["is_habit"] else "⏰"
        for i, when in enumerate(times):
            text = f"{icon} {task['title']}" + ("" if i == 0 else " — ВСЁ ЕЩЁ НЕ СДЕЛАНО")
            text += "\n(страховка Dungeon Tasks: ноут спал)"
            try:
                msg = await self.client.send_message("me", text, schedule=when)
                self.db.x("INSERT INTO insurance(task_id, msg_id, fire_at) VALUES(?,?,?)",
                          (task["id"], msg.id, iso(when)))
            except Exception:
                log.exception("insurance: не удалось поставить напоминание")
                return

    async def disarm_due(self) -> None:
        """Ноут не спит: снимаем страховку, которая вот-вот сработает, бот сделает это сам."""
        if not self.client:
            return
        now = utcnow()
        rows = self.db.q("SELECT rowid, msg_id, fire_at FROM insurance WHERE fire_at<=?",
                         (iso(now + DISARM_BEFORE),))
        if not rows:
            return
        self.db.x("DELETE FROM insurance WHERE fire_at<=?", (iso(now + DISARM_BEFORE),))
        # уже сработавшие (ноут спал) удалять не надо, они в чате как обычные сообщения
        await self._delete([r["msg_id"] for r in rows if parse_iso(r["fire_at"]) > now])

    async def resync_all(self, tasks) -> None:
        for t in tasks:
            await self.sync(t)

