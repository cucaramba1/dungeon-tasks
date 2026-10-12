import sqlite3
from datetime import datetime, timedelta, timezone

from dungeon import events as events_mod
from dungeon.db import DB, iso
from dungeon.events import Events

TZ = timezone(timedelta(hours=7))
NEW_COLS = {"kind", "author", "ritual", "extra", "status", "task_id", "ref_id", "ref_task",
            "notified_at", "reminded_at", "llm_raw", "undo", "needs_check"}


def cols(db):
    return {r[1] for r in db.conn.execute("PRAGMA table_info(events_feed)")}


def test_fresh_db_has_ritual_columns():
    assert NEW_COLS <= cols(DB(":memory:"))


def test_old_db_is_migrated_without_data_loss(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE events_feed (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, chat_id INTEGER, "
                "channel TEXT, msg_id INTEGER, link TEXT, starts_at TEXT NOT NULL, has_time INTEGER DEFAULT 0, "
                "title TEXT, text TEXT, category TEXT, price TEXT, vote INTEGER DEFAULT 0, created_at TEXT NOT NULL)")
    con.execute("INSERT INTO events_feed(key, starts_at, title, created_at) VALUES('k','2026-10-20T00:00:00+00:00','Квиз','x')")
    con.commit()
    con.close()
    db = DB(path)
    assert NEW_COLS <= cols(db)
    row = db.one("SELECT * FROM events_feed WHERE key='k'")
    assert row["title"] == "Квиз" and row["kind"] == "afisha" and row["status"] == "new"


def test_upcoming_ignores_ritual_rows():
    db = DB(":memory:")
    evs = Events(db)
    start = datetime(2026, 10, 14, 19, 0, tzinfo=TZ)
    for key, kind in (("a", "afisha"), ("b", "announce"), ("c", "move")):
        db.x("INSERT INTO events_feed(key, starts_at, title, kind, created_at) VALUES(?,?,?,?,?)",
             (key, iso(start), key, kind, iso(start)))
    got = evs.upcoming(start - timedelta(hours=1), start + timedelta(hours=1))
    assert [g["key"] for g in got] == ["a"]


def test_afisha_skips_targeted_chats(monkeypatch):
    import asyncio
    cfg = {"channels": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "targeted": [{"id": 2, "name": "B"}],
           "categories": {}, "exclude": []}
    monkeypatch.setattr(events_mod, "cfg", lambda: cfg)
    asked = []

    async def fetch(chat_id, limit):
        asked.append(chat_id)
        return []

    asyncio.run(Events(DB(":memory:")).collect(TZ, fetch=fetch, pause=0))
    assert asked == [1]


def test_afisha_cleanup_keeps_ritual_rows(monkeypatch):
    import asyncio
    monkeypatch.setattr(events_mod, "cfg", lambda: {"channels": [], "targeted": [], "categories": {}, "exclude": []})
    db = DB(":memory:")
    old = datetime.now(TZ) - timedelta(days=30)
    for key, kind in (("old-afisha", "afisha"), ("old-ritual", "announce")):
        db.x("INSERT INTO events_feed(key, starts_at, title, kind, created_at) VALUES(?,?,?,?,?)",
             (key, iso(old), key, kind, iso(old)))
    asyncio.run(Events(db).collect(TZ, fetch=None, pause=0))
    assert [r["key"] for r in db.q("SELECT key FROM events_feed")] == ["old-ritual"]   # llm_raw и undo не теряем
