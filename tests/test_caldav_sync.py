from datetime import datetime, timedelta, timezone

import pytest

from dungeon import caldav_sync, scheduler
from dungeon.db import DB
from dungeon.services import Service

TZ = timezone(timedelta(hours=7))


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(caldav_sync, "enabled", lambda: True)
    clock = Clock(datetime(2026, 9, 26, 18, 30, tzinfo=TZ))
    db = DB(":memory:")
    return Service(db, clock=clock), db, clock


def test_rrule_dict():
    assert caldav_sync._rrule_dict("FREQ=WEEKLY;BYDAY=FR") == {"FREQ": "WEEKLY", "BYDAY": ["FR"]}
    assert caldav_sync._rrule_dict("FREQ=DAILY;INTERVAL=2") == {"FREQ": "DAILY", "INTERVAL": 2}
    assert caldav_sync._rrule_dict("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR") == {
        "FREQ": "WEEKLY", "BYDAY": ["MO", "TU", "WE", "TH", "FR"]}


def test_create_with_due_queues_upsert(env):
    svc, db, _ = env
    [t] = svc.create("завтра в 10 позвонить маме")
    rows = db.q("SELECT * FROM calendar_outbox")
    assert len(rows) == 1
    assert rows[0]["task_id"] == t["id"] and rows[0]["action"] == "upsert"


def test_create_without_due_does_not_queue(env):
    svc, db, _ = env
    svc.create("разобраться с налогами")
    assert db.q("SELECT * FROM calendar_outbox") == []


def test_spawned_instance_is_not_queued(env):
    """Повтор серии (source='repeat') не должен плодить своё событие — его накрывает мастер."""
    svc, db, _clock = env
    [t] = svc.create("каждый день в 9 йога")
    db.x("DELETE FROM calendar_outbox")  # создание мастера нас тут не интересует
    _xp, _msgs, nxt = svc.done(t["id"])
    assert nxt is not None and nxt["source"] == "repeat"
    rows = db.q("SELECT * FROM calendar_outbox")
    # для разовой (без rrule) задачи done() шлёт delete; у серии — не должно быть ни upsert на повтор,
    # ни delete на мастер (серия продолжается)
    assert all(r["action"] != "upsert" or r["task_id"] != nxt["id"] for r in rows)


def test_done_one_off_queues_delete(env):
    svc, db, _ = env
    [t] = svc.create("позвонить маме")
    db.x("DELETE FROM calendar_outbox")
    svc.done(t["id"])
    rows = db.q("SELECT * FROM calendar_outbox WHERE task_id=?", (t["id"],))
    assert any(r["action"] == "delete" for r in rows)


def test_delete_disabled_when_caldav_off(monkeypatch):
    monkeypatch.setattr(caldav_sync, "enabled", lambda: False)
    clock = Clock(datetime(2026, 9, 26, 18, 30, tzinfo=TZ))
    db = DB(":memory:")
    svc = Service(db, clock=clock)
    svc.create("завтра в 10 позвонить маме")
    assert db.q("SELECT * FROM calendar_outbox") == []


def test_sync_one_upsert_series_reuses_uid(env, monkeypatch):
    svc, db, _ = env
    calls = []
    monkeypatch.setattr(caldav_sync, "upsert_series",
                        lambda uid, title, start, dur, rrule: calls.append((uid, title, rrule)) or (uid or "new-uid"))
    [t] = svc.create("каждую пятницу в 9 йога")
    [row] = db.q("SELECT * FROM calendar_outbox WHERE task_id=?", (t["id"],))
    scheduler._sync_one(db, row)
    assert calls == [(None, "Йога", "FREQ=WEEKLY;BYDAY=FR")]
    series = db.one("SELECT uid FROM calendar_series WHERE series_id=?", (t["series_id"],))
    assert series["uid"] == "new-uid"

    # повторный вызов на тот же series_id должен передать существующий uid, а не создавать новый
    calls.clear()
    db.x("DELETE FROM calendar_outbox")
    db.x("INSERT INTO calendar_outbox(task_id, action, requested_at) VALUES(?,?,?)",
         (t["id"], "upsert", "2026-09-26T00:00:00+00:00"))
    [row2] = db.q("SELECT * FROM calendar_outbox WHERE task_id=?", (t["id"],))
    scheduler._sync_one(db, row2)
    assert calls == [("new-uid", "Йога", "FREQ=WEEKLY;BYDAY=FR")]


def test_sync_one_delete_series_removes_mapping(env, monkeypatch):
    svc, db, _ = env
    monkeypatch.setattr(caldav_sync, "upsert_series", lambda uid, *a: uid or "new-uid")
    deleted = []
    monkeypatch.setattr(caldav_sync, "delete", lambda uid: deleted.append(uid))
    [t] = svc.create("каждую пятницу в 9 йога")
    [row] = db.q("SELECT * FROM calendar_outbox WHERE task_id=?", (t["id"],))
    scheduler._sync_one(db, row)

    svc.delete(t["id"])
    [drow] = db.q("SELECT * FROM calendar_outbox WHERE task_id=? AND action='delete'", (t["id"],))
    scheduler._sync_one(db, drow)
    assert deleted == ["new-uid"]
    assert db.one("SELECT * FROM calendar_series WHERE series_id=?", (t["series_id"],)) is None
