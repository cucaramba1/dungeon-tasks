"""Группа-форум: роутинг по топикам, ввод не уходит в планирование, недельная чистка «Планирования»."""
import asyncio
from types import SimpleNamespace

import pytest

from dungeon import bot, config, scheduler
from dungeon.app import App
from dungeon.db import DB

FORUM = -1001
TOPIC = {"plan": 11, "events": 12, "checklists": 13, "alerts": 14}


class FakeBot:
    def __init__(self, fail_batch=False, fail_single=()):
        self.sent, self.deleted, self.topics = [], [], []
        self.fail_batch, self.fail_single = fail_batch, set(fail_single)
        self._id = 100

    async def send_message(self, chat_id, text, reply_markup=None, disable_notification=False, message_thread_id=None):
        self._id += 1
        self.sent.append((chat_id, message_thread_id, text))
        return SimpleNamespace(message_id=self._id)

    async def delete_messages(self, chat_id, ids):
        if self.fail_batch:
            raise RuntimeError("too old")
        self.deleted.extend(ids)

    async def delete_message(self, chat_id, mid):
        if mid in self.fail_single:
            raise RuntimeError("too old")
        self.deleted.append(mid)

    async def create_forum_topic(self, chat_id, title):
        self.topics.append(title)
        return SimpleNamespace(message_thread_id=200 + len(self.topics))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(config, "FORUM_CHAT_ID", 0)
    monkeypatch.setattr(config, "OWNER_ID", 7)
    a = App(DB(":memory:"))
    a.bot = FakeBot()
    return a


def configure(app):
    app.db.set("forum_chat_id", FORUM)
    for k, v in TOPIC.items():
        app.db.set(f"topic_{k}", v)


def msg(app, text="созвон завтра в 15", thread=None, chat=FORUM):
    answers = []

    async def answer(t, **kw):
        answers.append(t)
        return SimpleNamespace(message_id=900 + len(answers))

    return SimpleNamespace(chat=SimpleNamespace(id=chat), message_thread_id=thread, is_topic_message=thread is not None,
                           message_id=500, answer=answer, reply_to_message=None, text=text), answers


def test_target_without_forum_is_owner_dm(app):
    assert app.target("plan") == (7, None)


def test_target_with_forum_uses_topic(app):
    configure(app)
    assert app.target("plan") == (FORUM, 11)
    assert app.target("alerts") == (FORUM, 14)


def test_send_goes_to_topic_and_tracks_only_plan(app):
    configure(app)
    asyncio.run(app.send("обзор", topic="plan"))
    asyncio.run(app.send("карточка", topic="events"))
    assert app.bot.sent == [(FORUM, 11, "обзор"), (FORUM, 12, "карточка")]
    assert [r["message_id"] for r in app.db.q("SELECT * FROM topic_msgs")] == [101]


def test_no_tracking_without_forum(app):
    asyncio.run(app.send("обзор", topic="plan"))
    assert app.db.q("SELECT * FROM topic_msgs") == []


def test_setup_forum_idempotent(app):
    ids = asyncio.run(app.setup_forum(FORUM))
    assert set(ids) == set(TOPIC) and len(app.bot.topics) == 4
    asyncio.run(app.setup_forum(FORUM))
    assert len(app.bot.topics) == 4
    assert app.forum == FORUM


def test_purge_deletes_and_clears(app):
    configure(app)
    for _ in range(3):
        asyncio.run(app.send("x", topic="plan"))
    assert asyncio.run(app.purge_topic()) == (3, 0)
    assert app.bot.deleted == [101, 102, 103]
    assert app.db.q("SELECT * FROM topic_msgs") == []


def test_purge_falls_back_to_single_deletes(app):
    configure(app)
    app.bot = FakeBot(fail_batch=True, fail_single={102})
    for _ in range(3):
        asyncio.run(app.send("x", topic="plan"))
    assert asyncio.run(app.purge_topic()) == (2, 1)
    assert app.db.q("SELECT * FROM topic_msgs") == []


def test_event_in_events_topic_becomes_task_while_planning_hangs(app):
    configure(app)
    app.planner.start()
    m, answers = msg(app, thread=12)
    asyncio.run(bot.handle_text(app, m, "созвон завтра в 15", "voice"))
    assert app.planner.active() is not None
    assert [t["title"] for t in app.svc.open_tasks()] == ["Созвон"]
    assert app.planner.active()["inputs"] in ({}, "{}")


def test_text_in_plan_topic_goes_to_plan(app):
    configure(app)
    app.planner.start()
    m, answers = msg(app, thread=11)
    asyncio.run(bot.handle_text(app, m, "на неделе хочу запустить бота", "tg"))
    assert app.planner.active()["inputs"]
    assert app.svc.open_tasks() == []
    assert {r["message_id"] for r in app.db.q("SELECT * FROM topic_msgs")} == {500, 901}


def test_plan_topic_without_session_hints_not_task(app):
    configure(app)
    m, answers = msg(app, thread=11)
    asyncio.run(bot.handle_text(app, m, "купить молоко", "tg"))
    assert app.svc.open_tasks() == [] and "/plan" in answers[0]


def test_without_forum_old_behaviour_planning_intercepts(app):
    app.planner.start()
    m, answers = msg(app, chat=7)
    asyncio.run(bot.handle_text(app, m, "про неделю", "tg"))
    assert app.planner.active()["inputs"] and app.svc.open_tasks() == []


def test_weekly_purges_only_after_chronicle_written(app, tmp_path):
    configure(app)
    asyncio.run(app.send("старый обзор", topic="plan"))
    asyncio.run(scheduler.send_weekly(app))          # vault нет: хроника не записана
    assert app.bot.deleted == []
    (tmp_path / "vault").mkdir()
    asyncio.run(scheduler.send_weekly(app))          # vault есть: факты недели записаны, чистим
    assert 101 in app.bot.deleted
    assert any("Хроника недели" in s[2] for s in app.bot.sent)


def test_weekly_keeps_messages_while_planning_active(app, tmp_path):
    configure(app)
    (tmp_path / "vault").mkdir()
    asyncio.run(app.send("старый обзор", topic="plan"))
    app.planner.start()
    asyncio.run(scheduler.send_weekly(app))
    assert app.bot.deleted == []
