"""Контейнер: БД, сервис, бот, страховка. Общие действия с побочными эффектами."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup

from . import config, keyboards, texts
from .db import DB
from .events import Events
from .insurance import Insurance
from .leisure import Leisure
from .planner import Planner
from .rituals import Rituals
from .services import Service

log = logging.getLogger(__name__)


class App:
    def __init__(self, db: DB | None = None):
        self.db = db or DB(config.DB_PATH)
        self.svc = Service(self.db)
        self.ins = Insurance(self.db)
        self.events = Events(self.db)
        self.rituals = Rituals(self.db, self.svc)
        self.rituals_running = False
        self.leisure = Leisure(self.db)
        self.planner = Planner(self.svc, self.events)
        self.plan_busy = False           # Claude думает над шагом, повторные нажатия игнорируем
        self.events_running = False
        self._bg: set[asyncio.Task] = set()
        self.bot = Bot(config.BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML")) \
            if config.BOT_TOKEN else None

    def spawn(self, coro) -> None:
        """Фоновая задача со ссылкой, чтобы сборщик мусора не прибил её на полпути."""
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    # --- владелец ---
    @property
    def owner(self) -> int:
        return config.OWNER_ID or int(self.db.get("owner_id", "0") or 0)

    @property
    def ready(self) -> bool:
        """Есть куда слать: бот настроен и владелец известен."""
        return bool(self.bot and self.owner)

    def claim_owner(self, user_id: int) -> bool:
        if not self.owner:
            self.db.set("owner_id", user_id)
            return True
        return self.owner == user_id

    # --- отправка ---
    async def send(self, text: str, kb: InlineKeyboardMarkup | None = None, silent: bool = False):
        if not (self.bot and self.owner):
            log.info("send skipped (нет бота/владельца): %s", text[:80])
            return None
        try:
            return await self.bot.send_message(self.owner, text, reply_markup=kb, disable_notification=silent)
        except Exception:
            log.exception("send failed")
            return None

    async def announce(self, msgs: list[str]) -> None:
        if msgs:
            await self.send("\n".join(msgs))

    # --- действия, общие для бота и веба ---
    async def created(self, tasks, notify_tg: bool) -> None:
        for t in tasks:
            await self.ins.sync(t)
            if notify_tg:
                await self.send(texts.card(t, self.svc.clock()), keyboards.card(t), silent=True)

    async def changed(self, task_id: int) -> None:
        await self.ins.sync(self.svc.get(task_id))

    async def done(self, task_id: int, notify_tg: bool = True) -> tuple[int, list[str]]:
        xp, msgs, nxt = self.svc.done(task_id)
        await self.ins.cancel(task_id)
        if nxt:
            await self.ins.sync(nxt)
        if notify_tg and msgs:
            await self.announce(msgs)
        return xp, msgs

    async def delete(self, task_id: int) -> list[str]:
        msgs = self.svc.delete(task_id)
        await self.ins.cancel(task_id)
        await self.announce(msgs)
        return msgs

    async def skip(self, task_id: int):
        nxt = self.svc.skip(task_id)
        await self.ins.cancel(task_id)
        if nxt:
            await self.ins.sync(nxt)
        return nxt
