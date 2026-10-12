import json
from datetime import date, datetime, timedelta, timezone

import pytest
from dateutil.rrule import rrulestr

from dungeon import rituals
from dungeon.db import DB, iso, parse_iso
from dungeon.parser import Parsed
from dungeon.rituals import Rituals
from dungeon.services import Service

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=TZ)
YOGA, DND, GOALS = -1, -2, -3
CHATS = [
    {"id": YOGA, "name": "Йога FYSM", "authors": ["Panushkinn"], "series": "Йога",
     "until": date(2026, 10, 31), "attend_days": ["TU", "SU"]},
    {"id": DND, "name": "Днд неверленд", "authors": ["Mortemanes"], "series": "Настольная игра Dungeons & Dragons",
     "keep_time": True},
    {"id": GOALS, "name": "Цели и привычки", "authors": ["AlinaSnezhko"]},
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


def dnd(svc):
    return svc.create_parsed(Parsed(title="Настольная игра Dungeons & Dragons", quadrant=2, is_habit=True,
                                    due_at=datetime(2026, 10, 16, 18, 30, tzinfo=TZ), has_time=True,
                                    rrule="FREQ=WEEKLY;BYDAY=FR"), "t")


def add(db, chat, start, kind="announce", status="new", title="Занятие", has_time=1, **cols):
    data = {"key": f"k{db.one('SELECT COUNT(*) c FROM events_feed')['c']}", "chat_id": chat,
            "channel": "c", "msg_id": 1, "link": "https://t.me/x/1", "starts_at": iso(start), "has_time": has_time,
            "title": title, "text": "t", "kind": kind, "status": status, "ritual": "r", "extra": "{}",
            "created_at": iso(NOW), **cols}
    return db.x(f"INSERT INTO events_feed({','.join(data)}) VALUES({','.join('?' * len(data))})", tuple(data.values()))


def due(svc, tid):
    return parse_iso(svc.get(tid)["due_at"]).astimezone(TZ)


# ---------- перенос и отмена ----------
def test_move_series_occurrence_and_undo(env):
    db, svc, r = env
    t = dnd(svc)
    mv = add(db, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    assert r.new_changes()[0]["id"] == mv
    assert r.apply_change(mv) == [t["id"]]
    assert due(svc, t["id"]) == datetime(2026, 10, 17, 18, 30, tzinfo=TZ)
    assert r.feed(mv)["status"] == "applied"
    assert r.undo(mv) == [t["id"]]
    assert due(svc, t["id"]) == datetime(2026, 10, 16, 18, 30, tzinfo=TZ)
    assert r.feed(mv)["status"] == "undone" and r.undo(mv) == []   # второй раз ничего не делает


def test_move_without_time_keeps_time_of_the_lesson(env):
    db, svc, r = env
    t = dnd(svc)
    mv = add(db, DND, datetime(2026, 10, 17, 0, 0, tzinfo=TZ), kind="move", ref_task=t["id"], has_time=0)
    r.apply_change(mv)
    assert due(svc, t["id"]) == datetime(2026, 10, 17, 18, 30, tzinfo=TZ)


def test_move_announce_row_also_moves_its_accepted_task(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    res = r.accept(ev)
    mv = add(db, GOALS, datetime(2026, 10, 20, 11, 0, tzinfo=TZ), kind="move", ref_id=ev)
    r.apply_change(mv)
    assert parse_iso(r.feed(ev)["starts_at"]).astimezone(TZ).day == 20
    assert due(svc, res["task_id"]).day == 20


def test_cancel_series_occurrence_has_no_penalty_and_series_continues(env):
    db, svc, r = env
    t = dnd(svc)
    cx = add(db, DND, datetime(2026, 10, 16, 18, 30, tzinfo=TZ), kind="cancel", ref_task=t["id"])
    touched = r.apply_change(cx)
    assert svc.get(t["id"])["status"] == "skipped" and svc.game.hero()["hp"] == 100
    nxt = [x for x in touched if x != t["id"]][0]
    assert due(svc, nxt).day == 23
    r.undo(cx)
    assert svc.get(t["id"])["status"] == "open" and svc.get(nxt)["status"] == "deleted"


def test_cancel_announce_row_removes_one_off_task(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    res = r.accept(ev)
    cx = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), kind="cancel", ref_id=ev)
    r.apply_change(cx)
    assert r.feed(ev)["status"] == "cancelled" and svc.get(res["task_id"])["status"] == "deleted"
    r.undo(cx)
    assert r.feed(ev)["status"] == "accepted" and svc.get(res["task_id"])["status"] == "open"


def test_pick_target_resolves_ambiguous_row(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    mv = add(db, GOALS, datetime(2026, 10, 20, 11, 0, tzinfo=TZ), kind="move", status="ambiguous")
    r.pick_target(mv, f"e{ev}")
    row = r.feed(mv)
    assert row["status"] == "new" and row["ref_id"] == ev


# ---------- решения Некита ----------
def test_accept_confirms_series_occurrence_and_keeps_its_time(env):
    db, svc, r = env
    t = dnd(svc)
    ev = add(db, DND, datetime(2026, 10, 16, 19, 0, tzinfo=TZ))        # в чате пишут «в 19:00», у Некита 18:30
    res = r.accept(ev)
    assert res["task_id"] == t["id"] and due(svc, t["id"]) == datetime(2026, 10, 16, 18, 30, tzinfo=TZ)
    assert r.feed(ev)["status"] == "accepted" and "https://t.me/x/1" in svc.get(t["id"])["notes"]


def test_accept_far_announce_is_recorded_without_touching_series(env):
    db, svc, r = env
    t = dnd(svc)
    ev = add(db, DND, datetime(2026, 11, 20, 19, 0, tzinfo=TZ))
    res = r.accept(ev)
    assert res["task_id"] is None and due(svc, t["id"]).day == 16
    assert r.feed(ev)["status"] == "accepted"


def test_accept_plain_chat_creates_one_off_task(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), title="Встреча по 12 неделям",
             extra=json.dumps({"book": "Atomic Habits"}))
    res = r.accept(ev)
    t = svc.get(res["task_id"])
    assert t["title"] == "Встреча по 12 неделям" and not t["is_habit"] and due(svc, t["id"]).hour == 11
    assert "Atomic Habits" in t["notes"]


def test_make_ritual_in_plain_chat_creates_weekly_series(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    t = svc.get(r.make_ritual(ev)["task_id"])
    assert t["is_habit"] and t["rrule"] == "FREQ=WEEKLY;BYDAY=MO" and t["title"] == "Цели и привычки"


def test_yoga_series_is_created_from_first_announce_tue_sun_until_oct_31(env):
    db, svc, r = env
    ev = add(db, YOGA, datetime(2026, 10, 13, 9, 0, tzinfo=TZ), title="Йога")
    assert not r.series_exists(rituals.chat_cfg(YOGA))
    t = svc.get(r.make_ritual(ev)["task_id"])
    assert t["title"] == "Йога" and t["rrule"].startswith("FREQ=WEEKLY;BYDAY=TU,SU;UNTIL=")
    days = [d.astimezone(TZ) for d in rrulestr(t["rrule"], dtstart=due(svc, t["id"]))]
    assert [d.day for d in days] == [13, 18, 20, 25, 27]        # вторники и воскресенья, 1 ноября уже нет
    assert r.series_exists(rituals.chat_cfg(YOGA))


def test_skip_bad_reason_costs_20_hp_good_reason_is_free(env):
    db, svc, r = env
    t = dnd(svc)
    ev = add(db, DND, datetime(2026, 10, 16, 18, 30, tzinfo=TZ))
    msgs, touched = r.skip(ev, bad=True)
    assert svc.game.hero()["hp"] == 80 and svc.get(t["id"])["status"] == "missed" and t["id"] in touched
    assert r.feed(ev)["status"] == "skipped"
    t2 = [x for x in svc.open_tasks() if x["is_habit"]][0]
    ev2 = add(db, DND, parse_iso(t2["due_at"]))
    r.skip(ev2, bad=False)
    assert svc.game.hero()["hp"] == 80 and svc.get(t2["id"])["status"] == "skipped"


def test_skip_one_off_deletes_task_without_penalty(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    res = r.accept(ev)
    r.skip(ev, bad=True)
    assert svc.get(res["task_id"])["status"] == "deleted" and svc.game.hero()["hp"] == 100


def test_decline_and_retime(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), needs_check=1)
    res = r.accept(ev)
    r.retime(ev, datetime(2026, 10, 19, 12, 30, tzinfo=TZ), True)
    assert due(svc, res["task_id"]).hour == 12 and r.feed(ev)["needs_check"] == 0
    ev2 = add(db, GOALS, datetime(2026, 10, 26, 11, 0, tzinfo=TZ))
    r.decline(ev2)
    assert r.feed(ev2)["status"] == "declined"


# ---------- очереди рассылки ----------
def test_digest_slots_fire_once_each(env):
    db, _, r = env
    at = lambda h, m=0: datetime(2026, 10, 12, h, m, tzinfo=TZ)  # noqa: E731
    assert not r.digest_due(at(9, 59))
    assert r.digest_due(at(10, 0)) and not r.digest_due(at(10, 1))
    assert not r.digest_due(at(15, 59))
    assert r.digest_due(at(16, 0)) and r.digest_due(at(22, 5)) and not r.digest_due(at(23, 0))


def test_cards_urgent_reminder_expire(env):
    db, _, r = env
    soon = add(db, GOALS, NOW + timedelta(hours=2))
    later = add(db, GOALS, NOW + timedelta(days=2))
    past = add(db, GOALS, NOW - timedelta(hours=5))
    assert {x["id"] for x in r.unsent_cards(NOW)} == {soon, later}
    assert [x["id"] for x in r.unsent_cards(NOW, within=timedelta(hours=3))] == [soon]
    r.expire(NOW)
    assert r.feed(past)["status"] == "expired"
    r.mark_sent(later, NOW - timedelta(hours=3))
    assert [x["id"] for x in r.reminders(NOW)] == [later]
    r.mark_reminded(later, NOW)
    assert r.reminders(NOW) == []
    assert later not in {x["id"] for x in r.unsent_cards(NOW)}


def test_date_only_event_today_is_not_expired(env):
    db, _, r = env
    ev = add(db, GOALS, datetime(2026, 10, 12, 0, 0, tzinfo=TZ), has_time=0)
    r.expire(NOW)
    assert r.feed(ev)["status"] == "new" and ev in {x["id"] for x in r.unsent_cards(NOW)}


def test_unsent_changes_and_listing(env):
    db, svc, r = env
    t = dnd(svc)
    mv = add(db, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    r.apply_change(mv)
    amb = add(db, GOALS, NOW, kind="move", status="ambiguous")
    assert {x["id"] for x in r.unsent_changes()} == {mv, amb}
    r.mark_sent(mv, NOW)
    assert {x["id"] for x in r.unsent_changes()} == {amb}
    ev = add(db, GOALS, NOW + timedelta(days=1), title="Квиз", extra=json.dumps({"book": "Книга"}))
    [item] = r.listing(NOW)
    assert item["id"] == ev and item["title"] == "Квиз" and "Книга" in item["extra"] and item["status"] == "new"


# ---------- финальное ревью: повторные нажатия, серия + неподтверждённый анонс ----------
def test_double_accept_and_make_ritual_create_one_thing(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    first = r.accept(ev)
    again = r.accept(ev)
    assert first["task_id"] and again["task_id"] == first["task_id"] and "Уже" in again["text"]
    assert len([t for t in svc.open_tasks() if t["title"] == "Занятие"]) == 1
    ev2 = add(db, GOALS, datetime(2026, 10, 26, 11, 0, tzinfo=TZ))
    r.make_ritual(ev2)
    r.make_ritual(ev2)
    assert len([t for t in svc.open_tasks() if t["is_habit"]]) == 1


def test_double_skip_penalizes_once_and_keeps_next_class(env):
    db, svc, r = env
    t = svc.create_parsed(Parsed(title="Йога", quadrant=2, is_habit=True, due_at=datetime(2026, 10, 18, 9, 0, tzinfo=TZ),
                                 has_time=True, rrule="FREQ=WEEKLY;BYDAY=TU,SU"), "t")
    ev = add(db, YOGA, datetime(2026, 10, 18, 9, 0, tzinfo=TZ))
    r.skip(ev, bad=True)                                    # воскресенье: прогул
    r.skip(ev, bad=True)                                    # вторая карточка/напоминание с живой кнопкой
    assert svc.game.hero()["hp"] == 80
    nxt = [x for x in svc.open_tasks() if x["is_habit"]]    # вторник 20.10 должен остаться живым
    assert len(nxt) == 1 and nxt[0]["id"] != t["id"] and nxt[0]["status"] == "open"


def test_pick_target_twice_does_not_overwrite_undo(env):
    db, svc, r = env
    t = dnd(svc)
    mv = add(db, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", status="ambiguous")
    r.pick_target(mv, f"t{t['id']}")
    r.apply_change(mv)
    r.pick_target(mv, f"t{t['id']}")                        # повторное нажатие кнопки выбора
    assert r.feed(mv)["status"] == "applied"
    r.undo(mv)
    assert due(svc, t["id"]) == datetime(2026, 10, 16, 18, 30, tzinfo=TZ)


def test_cancel_via_unconfirmed_series_announce_also_cancels_the_task(env):
    db, svc, r = env
    t = dnd(svc)
    ev = add(db, DND, datetime(2026, 10, 16, 19, 0, tzinfo=TZ))            # анонс есть, «Подтвердить» не нажато
    chat = rituals.chat_cfg(DND)
    assert [c["ref"] for c in r.known(chat, NOW)] == [f"e{ev}"]            # слот один, а не два кандидата
    cx = add(db, DND, datetime(2026, 10, 16, 19, 0, tzinfo=TZ), kind="cancel", ref_id=ev)
    touched = r.apply_change(cx)
    assert svc.get(t["id"])["status"] == "skipped" and r.feed(ev)["status"] == "cancelled" and t["id"] in touched
    assert svc.game.hero()["hp"] == 100


def test_move_via_unconfirmed_series_announce_also_moves_the_task(env):
    db, svc, r = env
    t = dnd(svc)
    ev = add(db, DND, datetime(2026, 10, 16, 19, 0, tzinfo=TZ))
    mv = add(db, DND, datetime(2026, 10, 17, 19, 0, tzinfo=TZ), kind="move", ref_id=ev)
    r.apply_change(mv)
    assert due(svc, t["id"]) == datetime(2026, 10, 17, 19, 0, tzinfo=TZ)
    r.undo(mv)
    assert due(svc, t["id"]) == datetime(2026, 10, 16, 18, 30, tzinfo=TZ)


def test_recent_past_dated_card_stays_alive_old_one_expires(env):
    db, _, r = env
    recent = add(db, GOALS, datetime(2026, 10, 5, 11, 0, tzinfo=TZ), needs_check=2)       # created_at = NOW
    stale = add(db, GOALS, datetime(2026, 10, 5, 12, 0, tzinfo=TZ), needs_check=1)
    r.expire(NOW)
    assert r.feed(recent)["status"] == "new" and r.feed(stale)["status"] == "expired"
    assert [x["id"] for x in r.unsent_cards(NOW)] == [recent]
    assert recent in {x["id"] for x in r.unsent_cards(NOW, within=timedelta(hours=3))}
    r.retime(recent, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), True)                       # Некит поправил дату
    assert r.feed(recent)["needs_check"] == 0 and recent in {x["id"] for x in r.unsent_cards(NOW)}
