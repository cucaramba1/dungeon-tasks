"""Контейнер: БД, сервис, бот, страховка. Общие действия с побочными эффектами."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.types import InlineKeyboardMarkup

from . import config, keyboards, texts
from .db import DB, iso, utcnow
from .events import Events
from .insurance import Insurance
from .leisure import Leisure
from .planner import Planner
from .rituals import Rituals
from .services import Service

log = logging.getLogger(__name__)

# Топики группы-форума: ключ -> (заголовок, ключ настройки с id темы)
TOPICS = {
    "plan": "📅 Планирование",
    "events": "🎯 События",
    "checklists": "✅ Чек-листы",
    "alerts": "🔔 Уведомления",
}
PURGE_TOPIC = "plan"   # этот топик чистится раз в неделю


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

    # --- форум: группа с топиками ---
    @property
    def forum(self) -> int:
        return config.FORUM_CHAT_ID or int(self.db.get("forum_chat_id", "0") or 0)

    def topic_id(self, topic: str) -> int | None:
        v = self.db.get(f"topic_{topic}")
        return int(v) if v else None

    def target(self, topic: str) -> tuple[int, int | None]:
        """Куда слать сообщение этого топика: (chat_id, thread_id). Форум не настроен: личка владельцу."""
        if self.forum and (tid := self.topic_id(topic)):
            return self.forum, tid
        return self.owner, None

    def in_topic(self, chat_id: int, thread_id: int | None, topic: str) -> bool:
        """Сообщение пришло в этот топик? Форум не настроен: считаем, что да (старое поведение)."""
        if not self.forum:
            return True
        return chat_id == self.forum and thread_id == self.topic_id(topic)

    def track(self, topic: str, chat_id: int, message_id: int | None) -> None:
        """Запомнить сообщение топика, который чистится раз в неделю."""
        if topic == PURGE_TOPIC and message_id and self.forum and chat_id == self.forum:
            self.db.x("INSERT OR IGNORE INTO topic_msgs(chat_id, message_id, topic, created_at) VALUES(?,?,?,?)",
                      (chat_id, message_id, topic, iso(utcnow())))

    async def setup_forum(self, chat_id: int) -> dict[str, int]:
        """Создать недостающие топики в группе-форуме и запомнить их id. Повторный вызов дублей не плодит."""
        self.db.set("forum_chat_id", chat_id)
        for key, title in TOPICS.items():
            if not self.topic_id(key):
                topic = await self.bot.create_forum_topic(chat_id, title)
                self.db.set(f"topic_{key}", topic.message_thread_id)
        return {k: self.topic_id(k) for k in TOPICS}

    async def purge_topic(self, topic: str = PURGE_TOPIC) -> tuple[int, int]:
        """Удалить сохранённые сообщения топика. Возвращает (удалено, не удалось).
        Что Telegram не дал удалить, забываем: оно слишком старое или уже стёрто руками."""
        rows = self.db.q("SELECT chat_id, message_id FROM topic_msgs WHERE topic=?", (topic,))
        done = failed = 0
        by_chat: dict[int, list[int]] = {}
        for r in rows:
            by_chat.setdefault(r["chat_id"], []).append(r["message_id"])
        for chat_id, ids in by_chat.items():
            for i in range(0, len(ids), 100):
                chunk = ids[i:i + 100]
                try:
                    await self.bot.delete_messages(chat_id, chunk)
                    done += len(chunk)
                except Exception:
                    log.warning("purge %s: пачка не удалилась, пробую по одному", topic)
                    for mid in chunk:
                        try:
                            await self.bot.delete_message(chat_id, mid)
                            done += 1
                        except Exception:
                            failed += 1
        self.db.x("DELETE FROM topic_msgs WHERE topic=?", (topic,))
        return done, failed

    # --- отправка ---
    async def send(self, text: str, kb: InlineKeyboardMarkup | None = None, silent: bool = False,
                   topic: str = "alerts"):
        chat_id, thread_id = self.target(topic)
        if not (self.bot and chat_id):
            log.info("send skipped (нет бота/владельца): %s", text[:80])
            return None
        try:
            msg = await self.bot.send_message(chat_id, text, reply_markup=kb, disable_notification=silent,
                                              message_thread_id=thread_id)
            self.track(topic, chat_id, msg.message_id)
            return msg
        except Exception:
            log.exception("send failed")
            return None

    async def send_sticker(self, file_id: str, topic: str = "alerts") -> None:
        chat_id, thread_id = self.target(topic)
        await self.bot.send_sticker(chat_id, file_id, message_thread_id=thread_id)

    async def announce(self, msgs: list[str]) -> None:
        if msgs:
            await self.send("\n".join(msgs), topic="events")

    # --- действия, общие для бота и веба ---
    async def created(self, tasks, notify_tg: bool) -> None:
        for t in tasks:
            await self.ins.sync(t)
            if notify_tg:
                await self.send(texts.card(t, self.svc.clock()), keyboards.card(t), silent=True, topic="events")

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
