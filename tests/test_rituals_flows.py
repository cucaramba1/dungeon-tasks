import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from dungeon import keyboards, ritual_flows, rituals
from dungeon.db import DB, iso
from dungeon.parser import Parsed
from dungeon.rituals import Rituals
from dungeon.services import Service

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=TZ)
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=TZ)     # до первого слота подборки (10:00)
YOGA, DND, GOALS = -1, -2, -3
CHATS = [
    {"id": YOGA, "name": "Йога FYSM", "authors": ["Panushkinn"], "series": "Йога",
     "until": date(2026, 10, 31), "attend_days": ["TU", "SU"]},
    {"id": DND, "name": "Днд неверленд", "authors": ["Mortemanes"], "series": "Настольная игра Dungeons & Dragons"},
    {"id": GOALS, "name": "Цели и привычки", "authors": ["AlinaSnezhko"]},
]


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


class FakeApp:
    def __init__(self):
        self.db = DB(":memory:")
        self.svc = Service(self.db, clock=Clock(NOW))
        self.rituals = Rituals(self.db, self.svc)
        self.sent, self.changed_ids, self.rituals_running = [], [], False

    async def send(self, text, kb=None, silent=False):
        self.sent.append((text, kb))
        return object()

    async def changed(self, tid):
        self.changed_ids.append(tid)

    async def announce(self, msgs):
        if msgs:
            self.sent.append(("\n".join(msgs), None))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(rituals, "cfg", lambda: CHATS)
    a = FakeApp()
    a.db.set("rituals_enabled", "1")
    return a


def add(app, chat, start, kind="announce", status="new", title="Занятие", has_time=1, **cols):
    n = app.db.one("SELECT COUNT(*) c FROM events_feed")["c"]
    data = {"key": f"k{n}", "chat_id": chat, "channel": "c", "msg_id": 1, "link": "https://t.me/x/1",
            "starts_at": iso(start), "has_time": has_time, "title": title, "text": "t", "kind": kind,
            "status": status, "ritual": "r", "extra": "{}", "created_at": iso(NOW), **cols}
    return app.db.x(f"INSERT INTO events_feed({','.join(data)}) VALUES({','.join('?' * len(data))})",
                    tuple(data.values()))


def dnd(app):
    return app.svc.create_parsed(Parsed(title="Настольная игра Dungeons & Dragons", quadrant=2, is_habit=True,
                                        due_at=datetime(2026, 10, 16, 18, 30, tzinfo=TZ), has_time=True,
                                        rrule="FREQ=WEEKLY;BYDAY=FR"), "t")


def labels(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def data(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]


def run(coro):
    return asyncio.run(coro)


def test_mode_depends_on_series_state(app):
    ev = add(app, GOALS, NOW + timedelta(days=1))
    assert ritual_flows.mode_for(app, app.rituals.feed(ev)) == "plain"
    y = add(app, YOGA, datetime(2026, 10, 13, 9, 0, tzinfo=TZ))
    assert ritual_flows.mode_for(app, app.rituals.feed(y)) == "new_series"
    dnd(app)
    d = add(app, DND, datetime(2026, 10, 16, 19, 0, tzinfo=TZ))
    assert ritual_flows.mode_for(app, app.rituals.feed(d)) == "occ"


def test_card_text_and_keyboards(app):
    ev = add(app, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), title="Встреча по 12 неделям",
             extra=json.dumps({"book": "Atomic Habits"}), needs_check=1)
    row = app.rituals.feed(ev)
    text = ritual_flows.card_text(app, row, NOW)
    assert "Встреча по 12 неделям" in text and "пн 19.10 11:00" in text and "Atomic Habits" in text
    assert "проверь" in text and f"[a{ev}]" in text and "#" not in text      # «#123» ловил бы редактор задач
    assert "—" not in text
    kb = keyboards.ritual_card(ev, row["link"], "plain")
    assert data(kb) == [f"rt:ok:{ev}", f"rt:mk:{ev}", f"rt:no:{ev}", f"rt:fix:{ev}"]
    assert labels(keyboards.ritual_card(ev, None, "occ"))[:2] == ["✅ Подтвердить", "❌ Пропускаю"]
    assert "🕯 Завести ритуал" in labels(keyboards.ritual_card(ev, None, "new_series"))
    assert "20 HP" in labels(keyboards.ritual_reason(ev, 20))[0]


def test_card_text_warns_about_overlap(app):
    app.svc.create_parsed(Parsed(title="Созвон", quadrant=1, due_at=datetime(2026, 10, 19, 11, 30, tzinfo=TZ),
                                 has_time=True), "t")
    ev = add(app, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    assert "Созвон" in ritual_flows.card_text(app, app.rituals.feed(ev), NOW)


def test_tick_disabled_or_quiet_sends_nothing(app):
    add(app, GOALS, NOW + timedelta(hours=1))
    app.db.set("rituals_enabled", "0")
    run(ritual_flows.tick(app, NOW, quiet=False))
    assert app.sent == []
    app.db.set("rituals_enabled", "1")
    run(ritual_flows.tick(app, NOW, quiet=True))
    assert app.sent == []


def test_urgent_card_goes_immediately_and_only_once(app):
    soon = add(app, GOALS, T0 + timedelta(hours=1))
    far = add(app, GOALS, T0 + timedelta(days=3))
    run(ritual_flows.tick(app, T0, quiet=False))              # 09:00, до первого слота подборки: уходит только срочное
    assert len(app.sent) == 1 and f"rt:ok:{soon}" in data(app.sent[0][1])
    run(ritual_flows.tick(app, T0, quiet=False))
    assert len(app.sent) == 1                                 # второй раз не шлём
    assert app.rituals.feed(far)["notified_at"] is None


def test_digest_sends_header_and_cards_once_per_slot(app):
    far = add(app, GOALS, NOW + timedelta(days=3))
    run(ritual_flows.tick(app, NOW, quiet=False))                             # 20:00: догнали слот 16:00
    assert len(app.sent) == 2 and "Анонсы из чатов: 1" in app.sent[0][0]
    assert app.rituals.feed(far)["notified_at"] is not None
    run(ritual_flows.tick(app, NOW + timedelta(minutes=5), quiet=False))
    assert len(app.sent) == 2


def test_empty_digest_is_not_sent(app):
    run(ritual_flows.tick(app, NOW, quiet=False))
    assert app.sent == []


def test_reminder_once_for_unanswered_card(app):
    add(app, GOALS, T0 + timedelta(days=2), notified_at=iso(T0 - timedelta(hours=3)))
    run(ritual_flows.tick(app, T0, quiet=False))
    assert len(app.sent) == 1 and "Ещё не ответил" in app.sent[0][0]
    run(ritual_flows.tick(app, T0, quiet=False))
    assert len(app.sent) == 1


def test_move_is_applied_and_reported_with_undo_button(app):
    t = dnd(app)
    mv = add(app, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    run(ritual_flows.tick(app, NOW, quiet=False))
    assert t["id"] in app.changed_ids
    text, kb = app.sent[0]
    assert "Перенёс" in text and "пт 16.10 18:30" in text and "сб 17.10 18:30" in text
    assert data(kb) == [f"rt:un:{mv}"]


def test_move_is_applied_during_quiet_hours_but_reported_after(app):
    t = dnd(app)
    add(app, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    run(ritual_flows.tick(app, NOW, quiet=True))
    assert app.sent == [] and app.svc.get(t["id"])["due_at"].startswith("2026-10-17")
    run(ritual_flows.tick(app, NOW, quiet=False))
    assert len(app.sent) == 1


def test_ambiguous_move_asks_with_candidate_buttons(app):
    a = add(app, GOALS, datetime(2026, 10, 20, 11, 0, tzinfo=TZ), title="Первая")
    b = add(app, GOALS, datetime(2026, 10, 27, 11, 0, tzinfo=TZ), title="Вторая")
    mv = add(app, GOALS, datetime(2026, 10, 22, 11, 0, tzinfo=TZ), kind="move", status="ambiguous",
             extra=json.dumps({"candidates": [{"ref": f"e{a}", "title": "Первая", "date": "2026-10-20 11:00"},
                                               {"ref": f"e{b}", "title": "Вторая", "date": "2026-10-27 11:00"}]}))
    run(ritual_flows.tick(app, T0, quiet=False))
    text, kb = app.sent[0]
    assert "какое" in text and data(kb) == [f"rt:pk:{mv}:e{a}", f"rt:pk:{mv}:e{b}"]
    reply, _ = run(ritual_flows.handle(app, "pk", mv, f"e{a}"))
    assert app.rituals.feed(mv)["status"] == "applied" and "Перенёс" in reply


def test_handle_ok_creates_task_and_reports(app):
    ev = add(app, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), title="Встреча")
    reply, _ = run(ritual_flows.handle(app, "ok", ev, None))
    assert "В плане" in reply and app.changed_ids and app.rituals.feed(ev)["status"] == "accepted"


def test_skip_flow_asks_reason_then_penalizes_or_not(app):
    dnd(app)
    ev = add(app, DND, datetime(2026, 10, 16, 18, 30, tzinfo=TZ))
    ask, kind = run(ritual_flows.handle(app, "sk", ev, None))
    assert kind == "reason" and "причин" in ask.lower()
    reply, _ = run(ritual_flows.handle(app, "sb", ev, None))
    assert app.svc.game.hero()["hp"] == 80 and "20" in reply


def test_good_reason_skip_offers_the_next_announce(app):
    dnd(app)
    ev = add(app, DND, datetime(2026, 10, 16, 18, 30, tzinfo=TZ))
    nxt = add(app, DND, datetime(2026, 10, 23, 18, 30, tzinfo=TZ), title="Следующее")
    run(ritual_flows.handle(app, "sg", ev, None))
    assert app.svc.game.hero()["hp"] == 100
    assert any(f"rt:ok:{nxt}" in data(kb) for _, kb in app.sent if kb)


def test_undo_and_retime(app):
    t = dnd(app)
    mv = add(app, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    run(ritual_flows.tick(app, NOW, quiet=False))
    reply, _ = run(ritual_flows.handle(app, "un", mv, None))
    assert "Вернул" in reply and app.svc.get(t["id"])["due_at"].startswith("2026-10-16")
    ev = add(app, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    out = run(ritual_flows.retime_from_reply(app, ev, "завтра в 19:30"))
    row = app.rituals.feed(ev)
    assert "19:30" in out and row["has_time"] == 1
    assert run(ritual_flows.retime_from_reply(app, ev, "хз когда")).startswith("Не понял")


def test_claude_down_notice_is_sent_once_per_day(app, monkeypatch):
    async def fake_collect(tz, **kw):
        return {"posts": 1, "items": 0, "errors": 1, "claude_down": True}

    monkeypatch.setattr(app.rituals, "collect", fake_collect)
    run(ritual_flows.collect(app))
    run(ritual_flows.collect(app))
    assert len([s for s in app.sent if "claude" in s[0].lower()]) == 1
