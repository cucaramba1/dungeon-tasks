from datetime import datetime, timedelta, timezone

from dungeon.db import DB, parse_iso
from dungeon.parser import Parsed
from dungeon.services import Service
from dungeon.texts import human_rrule

TZ = timezone(timedelta(hours=7))


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def make():
    svc = Service(DB(":memory:"), clock=Clock(datetime(2026, 10, 12, 20, 0, tzinfo=TZ)))
    t = svc.create_parsed(Parsed(title="Йога", quadrant=2, is_habit=True,
                                 due_at=datetime(2026, 10, 13, 9, 0, tzinfo=TZ), has_time=True,
                                 rrule="FREQ=WEEKLY;BYDAY=TU,SU"), "test")
    return svc, t


def test_skip_missed_penalizes_and_continues_series():
    svc, t = make()
    msgs, nxt = svc.skip_missed(t["id"])
    assert svc.game.hero()["hp"] == 80                    # skip_penalty_hp = 20
    assert svc.get(t["id"])["status"] == "missed"
    assert parse_iso(nxt["due_at"]).astimezone(TZ).day == 18   # следующее воскресенье
    svc.clock.dt = datetime(2026, 10, 14, 0, 5, tzinfo=TZ)
    svc.rollover()
    assert svc.game.hero()["hp"] == 80                    # rollover второй штраф не берёт


def test_skip_missed_respects_setting_and_closed_tasks():
    svc, t = make()
    svc.db.set("skip_penalty_hp", "30")
    svc.skip_missed(t["id"])
    assert svc.game.hero()["hp"] == 70
    assert svc.skip_missed(t["id"]) == ([], None)         # повторно не штрафует


def test_plain_skip_has_no_penalty():
    svc, t = make()
    nxt = svc.skip(t["id"])
    assert svc.game.hero()["hp"] == 100 and nxt is not None


def test_reopen_brings_back_a_skipped_task():
    svc, t = make()
    svc.skip(t["id"])
    assert svc.reopen(t["id"])["status"] == "open"


def test_human_rrule_with_until():
    assert human_rrule("FREQ=WEEKLY;BYDAY=TU,SU;UNTIL=20261031T165959Z") == "по вт, вс"
    assert human_rrule("FREQ=WEEKLY;BYDAY=FR") == "по пт"


def test_caldav_ics_accepts_until():
    from dungeon.caldav_sync import _build_ics
    ics = _build_ics("u1", "Йога", datetime(2026, 10, 13, 9, 0, tzinfo=TZ), 60,
                     "FREQ=WEEKLY;BYDAY=TU,SU;UNTIL=20261031T165959Z")
    assert "UNTIL=20261031T165959Z" in ics
