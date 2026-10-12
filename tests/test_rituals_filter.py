import asyncio
from datetime import datetime, timezone

from dungeon import rituals, tgmcp
from dungeon.rituals import author_ok, chat_link, sources, usable

SINCE = datetime(2026, 10, 5, tzinfo=timezone.utc)
CHAT = {"id": -100, "name": "Библиотерапия", "authors": ["AlinaSnezhko"]}


def post(text="Следующая встреча пройдет 19.10.2026 в 11.00", user="AlinaSnezhko", date="2026-10-10T03:00:00+00:00", **kw):
    return {"id": 1, "date": date, "text": text, "username": user, **kw}


def test_author_filter_is_case_insensitive():
    assert author_ok(CHAT, post(user="alinasnezhko"))
    assert not author_ok(CHAT, post(user="Thukydides"))
    assert not author_ok(CHAT, post(user=None))


def test_channel_accepts_any_post():
    assert author_ok({"id": 1, "name": "Покер", "channel": True}, post(user=None))


def test_usable_drops_polls_actions_old_and_short():
    assert usable(CHAT, post(), SINCE)
    assert not usable(CHAT, post(text=""), SINCE)                       # опрос без текста
    assert not usable(CHAT, post(action="MessageActionChatDeleteUser"), SINCE)
    assert not usable(CHAT, post(date="2026-09-30T03:00:00+00:00"), SINCE)  # старше окна первого запуска
    assert usable(CHAT, post(date="2026-09-30T03:00:00+00:00"), None)       # окна нет: берём всё новое


def test_sources_for_forum_and_plain_chat():
    assert sources(CHAT) == [(None, None)]
    forum = {"id": 1, "name": "Волейбол", "topics": {3: "outdoor", 132: "indoor"}}
    assert sources(forum) == [(3, "outdoor"), (132, "indoor")]


def test_chat_link_public_and_private():
    assert chat_link({"id": -100, "username": "pokerdaiviet"}, 7) == "https://t.me/pokerdaiviet/7"
    assert chat_link({"id": -1003969606547}, 7) == "https://t.me/c/3969606547/7"


def test_history_passes_topic_id(monkeypatch):
    seen = {}

    async def fake_call(tool, args, timeout=30):
        seen.update(tool=tool, args=args)
        return {"results": []}

    monkeypatch.setattr(tgmcp, "call", fake_call)
    asyncio.run(tgmcp.history(-1003116878132, 30, topic_id=3))
    assert seen["args"] == {"chat_id": -1003116878132, "limit": 30, "topic_id": 3}
    asyncio.run(tgmcp.history(-100, 30))
    assert "topic_id" not in seen["args"]


def test_cfg_reads_targeted_section(monkeypatch):
    monkeypatch.setattr(rituals.config, "yaml_file", lambda name: {"targeted": [{"id": 5, "name": "X"}]})
    assert rituals.chat_cfg(5)["name"] == "X" and rituals.chat_cfg(6) is None
