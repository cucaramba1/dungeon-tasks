import asyncio
from datetime import date, datetime, timedelta, timezone

from dungeon import ritual_llm
from dungeon.ritual_llm import parse_items

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=TZ)


def raw(**kw):
    base = {"msg_id": 1, "kind": "announce", "title": "Встреча по целям", "date": "2026-10-19", "time": "11:00",
            "confidence": 0.9}
    return {"items": [{**base, **kw}]}


def test_announce_is_parsed_with_local_start():
    [it] = parse_items(raw(book="Atomic Habits"), TZ, NOW)
    assert it.start == datetime(2026, 10, 19, 11, 0, tzinfo=TZ) and it.has_time
    assert it.book == "Atomic Habits" and not it.needs_check


def test_announce_without_date_is_dropped():
    assert parse_items(raw(date=None), TZ, NOW) == []          # «Всем привет, ждём всех!»


def test_none_and_junk_kinds_are_dropped():
    assert parse_items(raw(kind="none"), TZ, NOW) == []
    assert parse_items({"items": [{"kind": "announce", "date": "2026-10-19"}]}, TZ, NOW) == []  # нет msg_id
    assert parse_items({}, TZ, NOW) == []


def test_low_confidence_and_past_date_need_check():
    assert parse_items(raw(confidence=0.4), TZ, NOW)[0].needs_check
    assert parse_items(raw(date="2026-10-01"), TZ, NOW)[0].needs_check


def test_cancel_may_have_no_date_but_move_may_not():
    [c] = parse_items(raw(kind="cancel", date=None, time=None), TZ, NOW)
    assert c.start is None and not c.needs_check
    assert parse_items(raw(kind="move", date=None), TZ, NOW) == []


def test_move_keeps_orig_date_and_ref():
    [m] = parse_items(raw(kind="move", date="2026-10-17", time="18:30", orig_date="2026-10-16", ref="t34"), TZ, NOW)
    assert m.orig_date == date(2026, 10, 16) and m.ref == "t34"


def test_bad_time_format_means_no_time():
    [it] = parse_items(raw(time="в 11"), TZ, NOW)
    assert not it.has_time and it.start.hour == 0


def test_classify_sends_posts_and_known_to_llm():
    calls = {}

    async def ask(instructions, data, schema, system=None):
        calls.update(instructions=instructions, data=data, schema=schema, system=system)
        return raw()

    chat = {"id": -1, "name": "Цели и привычки"}
    posts = [{"msg_id": 1, "date": "2026-09-30T10:53:45+07:00", "weekday": "среда", "author": "AlinaSnezhko",
              "topic": None, "text": "Следующая встреча пройдет 19.10.2026 в 11.00"}]
    items = asyncio.run(ritual_llm.classify(chat, posts, [], NOW, ask=ask))
    assert items[0].msg_id == 1
    assert calls["data"]["chat"] == "Цели и привычки" and calls["data"]["today"] == "2026-10-12"
    assert calls["data"]["posts"] == posts and calls["data"]["known_events"] == []
    assert "announce" in calls["instructions"] and calls["system"] == ritual_llm.SYSTEM
    assert calls["schema"]["properties"]["items"]["items"]["properties"]["kind"]["enum"] == \
        ["announce", "move", "cancel", "none"]
