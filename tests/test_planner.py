import asyncio
from datetime import datetime, timedelta, timezone
from html import escape

import pytest

from dungeon.db import DB, parse_iso
from dungeon.events import Events
from dungeon.planner import Planner, render
from dungeon.services import Service

TZ = timezone(timedelta(hours=7))


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


A = {"context_events": [{"title": "Стоматолог", "date": "2026-09-29", "time": "10:00", "duration_min": 60},
                        {"title": "ДнД", "date": "2026-10-02", "time": "18:00", "rrule": "FREQ=WEEKLY;BYDAY=FR"}],
     "retro": "Данных мало.", "prev_goals": [], "goals": [{"goal": "Запустить mini-app <MVP>", "measure": "бот отвечает"}]}
B = {"context_events": A["context_events"], "goals": A["goals"],
     "tasks": [{"title": "Собрать MVP бота", "kind": "task", "goal": "Запустить mini-app", "duration_min": 180, "quadrant": 2}]}
C = {"items": [
    {"title": "Стоматолог", "kind": "context", "date": "2026-09-29", "time": "10:00", "duration_min": 60},
    {"title": "ДнД", "kind": "context", "date": "2026-10-02", "time": "18:00", "rrule": "FREQ=WEEKLY;BYDAY=FR"},
    {"title": "Собрать MVP бота", "kind": "task", "date": "2026-09-30", "time": "09:00", "duration_min": 180, "quadrant": 2},
    {"title": "Лекция про импрессионизм", "kind": "event", "event_id": 1, "date": "2026-10-01", "time": "19:00"},
], "load": {"planned_hours": 3, "budget_hours": 24, "verdict": "Влезает с запасом."}}


@pytest.fixture
def env():
    clock = Clock(datetime(2026, 9, 27, 20, 10, tzinfo=TZ))  # воскресенье вечер
    db = DB(":memory:")
    svc = Service(db, clock=clock)
    return Planner(svc, Events(db)), svc


def fake_ask(calls):
    async def ask(prompt, data, schema):
        calls.append((prompt[:12], data))
        return {"Шаг 1 → 2 п": A, "Шаг 2 → 3. ": B, "Шаг 3 → 4. ": C, "Шаг 4, фина": C}[prompt[:11]]
    return ask


def test_full_flow(env):
    pl, svc = env
    calls = []
    s = pl.start()
    assert pl.start()["id"] == s["id"]  # повторный старт не плодит сессии
    assert "шаг 1/4" in render(pl.get(s["id"]), escape)
    pl.add_input("во вторник в 10 стоматолог")
    assert pl.add_input("по пятницам днд в 18") == 2
    assert svc.open_tasks() == []  # ничего не создаётся, пока идём по шагам

    s = asyncio.run(pl.advance(s["id"], ask=fake_ask(calls)))
    assert s["step"] == 2
    assert calls[0][1]["inputs"]["context"] == ["во вторник в 10 стоматолог", "по пятницам днд в 18"]
    text = render(s, escape)
    assert "Стоматолог" in text and "&lt;MVP&gt;" in text  # HTML экранирован

    pl.add_input("цель ок")
    s = asyncio.run(pl.advance(s["id"], ask=fake_ask(calls)))
    assert s["step"] == 3 and "Собрать MVP бота" in render(s, escape)
    s = asyncio.run(pl.advance(s["id"], ask=fake_ask(calls)))
    assert s["step"] == 4 and "вт 29.09" in render(s, escape)

    s, created = asyncio.run(pl.finish(s["id"], ask=fake_ask(calls)))
    assert len(calls) == 3  # на шаге 4 правок не было — лишний вызов не нужен
    assert s["status"] == "done" and pl.active() is None
    titles = {t["title"]: t for t in svc.open_tasks()}
    assert set(titles) == {"Стоматолог", "ДнД", "Собрать MVP бота", "Лекция про импрессионизм"}
    assert parse_iso(titles["Стоматолог"]["due_at"]).astimezone(TZ).hour == 10
    assert titles["ДнД"]["rrule"] == "FREQ=WEEKLY;BYDAY=FR"
    assert titles["Собрать MVP бота"]["duration_min"] == 180
    assert titles["Стоматолог"]["quadrant"] is None


def test_back_and_edits_trigger_final_call(env):
    pl, svc = env
    calls = []
    s = pl.start()
    s = asyncio.run(pl.advance(s["id"], ask=fake_ask(calls)))
    s = pl.back(s["id"])
    assert s["step"] == 1
    pl.add_input("ещё врач в четверг")
    s = asyncio.run(pl.advance(s["id"], ask=fake_ask(calls)))
    assert calls[-1][1]["inputs"]["context"] == ["ещё врач в четверг"]
    s = asyncio.run(pl.advance(s["id"], ask=fake_ask(calls)))
    s = asyncio.run(pl.advance(s["id"], ask=fake_ask(calls)))
    pl.add_input("MVP перенеси на четверг")
    s, _ = asyncio.run(pl.finish(s["id"], ask=fake_ask(calls)))
    assert calls[-1][0].startswith("Шаг 4")


def test_existing_task_is_moved_not_duplicated(env):
    pl, svc = env
    [t] = svc.create("лендинг ~2ч")
    items = [{"title": "Лендинг", "kind": "existing", "existing_id": t["id"], "date": "2026-09-30", "time": "11:00"}]
    pl.apply(items)
    assert len(svc.open_tasks()) == 1
    assert parse_iso(svc.get(t["id"])["due_at"]).astimezone(TZ).day == 30


def test_cancel(env):
    pl, _ = env
    s = pl.start()
    pl.db.set("plan_started", s["week"])
    pl.cancel(s["id"])
    assert pl.active() is None
    assert pl.db.get("plan_started") == ""


def limit_ask(calls):
    async def ask(prompt, data, schema):
        from dungeon.coach import ClaudeUnavailable
        calls.append(prompt[:10])
        raise ClaudeUnavailable("Claude AI usage limit reached", limit=True)
    return ask


def test_offline_flow_when_limit(env):
    pl, svc = env
    calls = []
    s = pl.start()
    pl.add_input("Завтра в 12:00 книжный клуб. Во вторник в 16 стоматолог\nпоездка в Vinwonders когда-нибудь")
    s = asyncio.run(pl.advance(s["id"], ask=limit_ask(calls)))
    text = render(s, escape)
    assert s["step"] == 2 and "Claude сейчас недоступен" in text and "лимит" in text
    titles = [e["title"] for e in s["drafts"]["a"]["context_events"]]
    assert "Книжный клуб" in titles and "Стоматолог" in titles
    assert "Vinwonders" in text  # непонятое без даты вынесено в вопрос
    pl.add_input("Запустить mini-app\nСнять видео про отели")
    s = asyncio.run(pl.advance(s["id"], ask=limit_ask(calls)))
    assert [g["goal"] for g in s["drafts"]["b"]["goals"]] == ["Запустить mini-app", "Снять видео про отели"]
    pl.add_input("лендинг ~3ч\nмонтаж видео ~2ч в среду")
    s = asyncio.run(pl.advance(s["id"], ask=limit_ask(calls)))
    items = s["drafts"]["c"]["items"]
    assert {i["title"] for i in items} >= {"Книжный клуб", "Стоматолог", "Лендинг", "Монтаж видео"}
    assert all(i.get("date") for i in items)
    s, created = asyncio.run(pl.finish(s["id"], ask=limit_ask(calls)))
    assert s["status"] == "done" and len(created) == 4
    assert len(calls) == 3


def test_stale_session_expires(env):
    pl, svc = env
    s = pl.start()
    assert pl.expire_stale() is None
    pl.db.x("UPDATE plan_sessions SET updated_at='2026-09-20T00:00:00+00:00' WHERE id=?", (s["id"],))
    assert pl.expire_stale()["status"] == "cancelled"
    assert pl.active() is None


def test_load_verdict_70_30():
    from dungeon.offline import load_verdict
    assert load_verdict(14, 20).startswith("Влезает")  # ровно 70%
    assert load_verdict(14.1, 20).startswith("Впритык")
    assert load_verdict(20, 20).startswith("Впритык")
    assert load_verdict(21, 20).startswith("Перебор")
    assert load_verdict(0, 0).startswith("Влезает")
    assert load_verdict(1, 0).startswith("Перебор")
