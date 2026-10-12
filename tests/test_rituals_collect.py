import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from dungeon import rituals
from dungeon.coach import ClaudeUnavailable
from dungeon.db import DB, parse_iso
from dungeon.parser import Parsed
from dungeon.rituals import Rituals
from dungeon.services import Service

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=TZ)   # понедельник
YOGA, DND, GOALS, VOLLEY, POKER = -1, -2, -3, -4, -5
CHATS = [
    {"id": YOGA, "name": "Йога FYSM", "authors": ["Panushkinn"], "series": "Йога",
     "until": date(2026, 10, 31), "attend_days": ["TU", "SU"]},
    {"id": DND, "name": "Днд неверленд", "authors": ["Mortemanes"], "series": "Настольная игра Dungeons & Dragons"},
    {"id": GOALS, "name": "Цели и привычки", "authors": ["AlinaSnezhko"]},
    {"id": VOLLEY, "name": "Волейбол", "authors": ["alex_viuk"], "topics": {3: "outdoor", 132: "indoor"}},
    {"id": POKER, "name": "Покер", "channel": True},
]


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(rituals, "cfg", lambda: CHATS)
    db = DB(":memory:")
    svc = Service(db, clock=Clock(NOW))
    return db, svc, Rituals(db, svc)


def post(pid, text="текст анонса", user="Panushkinn", date_="2026-10-12T10:00:00+00:00"):
    return {"id": pid, "date": date_, "text": text, "username": user}


def item(msg_id, kind="announce", date_="2026-10-13", time_="09:00", **kw):
    return {"msg_id": msg_id, "kind": kind, "title": "Занятие", "date": date_, "time": time_,
            "confidence": 0.9, **kw}


def fetcher(feeds):
    calls = []

    async def fetch(chat_id, limit, topic_id=None):
        calls.append((chat_id, topic_id))
        return feeds.get((chat_id, topic_id), [])

    fetch.calls = calls
    return fetch


def asker(*items, boom=None):
    calls = []

    async def ask(instructions, data, schema, system=None):
        calls.append(data)
        if boom:
            raise boom
        return {"items": list(items)}

    ask.calls = calls
    return ask


def run(r, fetch, ask):
    return asyncio.run(r.collect(TZ, now=NOW, fetch=fetch, ask=ask, pause=0))


def rows(db, kind="announce"):
    return db.q("SELECT * FROM events_feed WHERE kind=? ORDER BY id", (kind,))


def dnd_task(svc):
    return svc.create_parsed(Parsed(title="Настольная игра Dungeons & Dragons", quadrant=2, is_habit=True,
                                    due_at=datetime(2026, 10, 16, 18, 30, tzinfo=TZ), has_time=True,
                                    rrule="FREQ=WEEKLY;BYDAY=FR"), "t")


def test_no_posts_from_authors_means_no_llm_call(env):
    db, _, r = env
    ask = asker(boom=AssertionError("LLM не должен вызываться"))
    fetch = fetcher({(GOALS, None): [post(1, user="Thukydides")]})
    stats = run(r, fetch, ask)
    assert stats["items"] == 0 and ask.calls == []
    assert db.get("rt_last:-3:0") == "1"                        # курсор двинулся, повторно не читаем


def test_announce_is_stored_with_extra(env):
    db, _, r = env
    ask = asker(item(1, date_="2026-10-19", time_="11:00", book="Atomic Habits", place="Кафе"))
    run(r, fetcher({(GOALS, None): [post(1, user="AlinaSnezhko")]}), ask)
    [row] = rows(db)
    assert row["status"] == "new" and row["ritual"] == "Цели и привычки" and row["has_time"] == 1
    assert json.loads(row["extra"]) == {"book": "Atomic Habits", "place": "Кафе"}
    assert row["link"] == "https://t.me/c/3/1" and row["author"] == "AlinaSnezhko"
    assert parse_iso(row["starts_at"]).astimezone(TZ) == datetime(2026, 10, 19, 11, 0, tzinfo=TZ)


def test_second_run_does_not_reread_or_duplicate(env):
    db, _, r = env
    fetch = fetcher({(GOALS, None): [post(1, user="AlinaSnezhko")]})
    run(r, fetch, asker(item(1)))
    stats = run(r, fetch, asker(boom=AssertionError("LLM не должен вызываться")))
    assert stats["posts"] == 0 and len(rows(db)) == 1


def test_edited_or_repeated_announce_makes_no_second_card(env):
    db, _, r = env
    feeds = {(GOALS, None): [post(1, user="AlinaSnezhko"), post(2, "Напоминаю про встречу", "AlinaSnezhko")]}
    run(r, fetcher(feeds), asker(item(1), item(2)))              # оба поста про один и тот же старт
    assert len(rows(db)) == 1


def test_yoga_friday_and_after_until_are_ignored(env):
    db, _, r = env
    feeds = {(YOGA, None): [post(1), post(2), post(3), post(4)]}
    ask = asker(item(1, date_="2026-10-13"),                     # вторник: наш день
                item(2, date_="2026-10-16", time_="18:00"),      # пятница: занята D&D
                item(3, date_="2026-10-18"),                     # воскресенье: наш день
                item(4, date_="2026-11-03"))                     # после until
    run(r, fetcher(feeds), ask)
    assert [parse_iso(x["starts_at"]).astimezone(TZ).day for x in rows(db)] == [13, 18]


def test_volleyball_reads_topics_and_marks_variant(env):
    db, _, r = env
    fetch = fetcher({(VOLLEY, 3): [post(10, user="alex_viuk")], (VOLLEY, 132): [post(11, user="alex_viuk")]})
    ask = asker(item(10, time_="18:00"), item(11, date_="2026-10-14", time_="20:00"))
    run(r, fetch, ask)
    assert (VOLLEY, 3) in fetch.calls and (VOLLEY, 132) in fetch.calls
    variants = {json.loads(x["extra"])["variant"] for x in rows(db)}
    assert variants == {"outdoor", "indoor"}
    assert db.get("rt_last:-4:3") == "10" and db.get("rt_last:-4:132") == "11"


def test_first_run_only_looks_back_seven_days(env):
    db, _, r = env
    feeds = {(POKER, None): [post(1, user=None, date_="2026-10-01T10:00:00+00:00"),
                             post(2, user=None, date_="2026-10-11T10:00:00+00:00")]}
    ask = asker(item(2))
    run(r, fetcher(feeds), ask)
    assert [p["msg_id"] for p in ask.calls[0]["posts"]] == [2]


def test_llm_failure_keeps_cursor_so_posts_are_not_lost(env):
    db, _, r = env
    feeds = {(GOALS, None): [post(1, user="AlinaSnezhko")]}
    stats = run(r, fetcher(feeds), asker(boom=ClaudeUnavailable("лимит", limit=True)))
    assert stats["errors"] == 1 and stats["claude_down"] and db.get("rt_last:-3:0") in (None, "0")
    run(r, fetcher(feeds), asker(item(1)))                       # claude ожил: пост не потерялся
    assert len(rows(db)) == 1


def test_telegram_failure_aborts_after_two_chats(env):
    db, _, r = env

    async def fetch(chat_id, limit, topic_id=None):
        raise RuntimeError("mcp лежит")

    stats = run(r, fetch, asker(boom=AssertionError("не должен вызываться")))
    assert stats["errors"] == 2 and stats["aborted"]


def test_move_of_series_occurrence_is_resolved_by_ref(env):
    db, svc, r = env
    t = dnd_task(svc)
    feeds = {(DND, None): [post(1, "Переносим на субботу", "Mortemanes")]}
    ask = asker(item(1, kind="move", date_="2026-10-17", time_="18:30", orig_date="2026-10-16", ref=f"t{t['id']}"))
    run(r, fetcher(feeds), ask)
    [row] = rows(db, "move")
    assert row["status"] == "new" and row["ref_task"] == t["id"] and row["ref_id"] is None
    assert ask.calls[0]["known_events"][0]["ref"] == f"t{t['id']}"


def test_move_without_orig_date_and_two_candidates_is_ambiguous(env):
    db, svc, r = env
    for d, pid in (("2026-10-20", 1), ("2026-10-27", 2)):
        run(r, fetcher({(GOALS, None): [post(pid, user="AlinaSnezhko")]}), asker(item(pid, date_=d)))
    run(r, fetcher({(GOALS, None): [post(3, "Встречу переносим", "AlinaSnezhko")]}),
        asker(item(3, kind="move", date_="2026-10-22")))
    [row] = rows(db, "move")
    assert row["status"] == "ambiguous" and row["ref_id"] is None
    assert len(json.loads(row["extra"])["candidates"]) == 2


def test_move_matches_announce_by_orig_date(env):
    db, svc, r = env
    run(r, fetcher({(GOALS, None): [post(1, user="AlinaSnezhko")]}), asker(item(1, date_="2026-10-19", time_="11:00")))
    [ev] = rows(db)
    run(r, fetcher({(GOALS, None): [post(2, "Встречу переносим", "AlinaSnezhko")]}),
        asker(item(2, kind="move", date_="2026-10-20", time_="11:00", orig_date="2026-10-19")))
    [mv] = rows(db, "move")
    assert mv["status"] == "new" and mv["ref_id"] == ev["id"]


def test_cancel_with_nothing_to_cancel_is_unresolved(env):
    db, _, r = env
    run(r, fetcher({(GOALS, None): [post(1, "Встречи не будет", "AlinaSnezhko")]}),
        asker(item(1, kind="cancel", date_=None, time_=None)))
    [row] = rows(db, "cancel")
    assert row["status"] == "unresolved"


def test_friday_yoga_cancel_is_ignored(env):
    db, _, r = env
    run(r, fetcher({(YOGA, None): [post(1, "В пятницу занятия не будет")]}),
        asker(item(1, kind="cancel", date_="2026-10-16", time_=None)))
    assert rows(db, "cancel") == []
