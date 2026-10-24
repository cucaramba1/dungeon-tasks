import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from dungeon import scheduler
from dungeon.db import DB, parse_iso
from dungeon.game import level_for, level_threshold, title_for
from dungeon.services import Service

TZ = timezone(timedelta(hours=7))


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


@pytest.fixture
def env():
    clock = Clock(datetime(2026, 9, 26, 18, 30, tzinfo=TZ))
    db = DB(":memory:")
    return Service(db, clock=clock), clock


def test_levels():
    assert level_for(0) == 1
    assert level_for(49) == 1 and level_for(50) == 2
    assert level_threshold(3) == 150
    assert title_for(1) == "Подмастерье гнома"
    assert title_for(12) == "Рунный кузнец"


def test_create_and_done_gives_xp(env):
    svc, _ = env
    [t] = svc.create("срочно позвонить маме")
    assert t["quadrant"] == 1
    xp, msgs, nxt = svc.done(t["id"])
    assert xp == 20  # 10 * 2 за Огненные залы
    assert any("Первая кровь" in m for m in msgs)
    assert nxt is None
    assert svc.game.hero()["xp"] == 20
    assert svc.done(t["id"])[0] == 0  # повторно не засчитывается


def test_multiline_creates_several(env):
    svc, _ = env
    tasks = svc.create("купить хлеб\nзавтра в 10 йога; отчёт важно")
    assert [t["title"] for t in tasks] == ["Купить хлеб", "Йога", "Отчёт"]


def test_habit_remind_defaults(env):
    svc, clock = env
    [t] = svc.create("йога")  # без времени -> habit_time 19:00 сегодня
    assert parse_iso(t["remind_at"]).astimezone(TZ) == datetime(2026, 9, 26, 19, 0, tzinfo=TZ)
    clock.dt = clock.dt.replace(hour=20)  # 20:30
    [t2] = svc.create("медитация")  # 19:00 уже прошло -> через 30 минут
    assert parse_iso(t2["remind_at"]).astimezone(TZ) == datetime(2026, 9, 26, 21, 0, tzinfo=TZ)


def test_plain_dated_task_has_no_reminder(env):
    svc, _ = env
    [t] = svc.create("оплатить аренду 1.10")
    assert t["remind_at"] is None


def test_recurring_spawns_next_and_streak(env):
    svc, clock = env
    [t] = svc.create("каждый день в 7 утра йога")
    assert parse_iso(t["due_at"]).astimezone(TZ) == datetime(2026, 9, 27, 7, 0, tzinfo=TZ)
    clock.dt = datetime(2026, 9, 27, 7, 5, tzinfo=TZ)
    xp, msgs, nxt = svc.done(t["id"])
    assert parse_iso(nxt["due_at"]).astimezone(TZ) == datetime(2026, 9, 28, 7, 0, tzinfo=TZ)
    assert nxt["series_id"] == t["id"]
    clock.dt = datetime(2026, 9, 28, 7, 10, tzinfo=TZ)
    _, msgs, nxt2 = svc.done(nxt["id"])
    assert any("Стрик рун: 2" in m for m in msgs)
    assert svc.db.one("SELECT current FROM streaks")["current"] == 2


def test_one_off_habits_share_streak_by_title(env):
    svc, clock = env
    [a] = svc.create("йога")
    svc.done(a["id"])
    clock.dt += timedelta(days=1)
    [b] = svc.create("Йога")
    assert b["series_id"] == a["series_id"]
    svc.done(b["id"])
    assert svc.db.one("SELECT current FROM streaks")["current"] == 2


def test_rollover_misses_habit_and_costs_hp(env):
    svc, clock = env
    [t] = svc.create("каждый день в 7 утра йога")
    clock.dt = datetime(2026, 9, 28, 0, 1, tzinfo=TZ)  # 27-го не сделал
    missed, msgs = svc.rollover()
    assert [m["id"] for m in missed] == [t["id"]]
    assert svc.game.hero()["hp"] == 90
    assert svc.get(t["id"])["status"] == "missed"
    nxt = svc.db.one("SELECT * FROM tasks WHERE status='open'")
    assert parse_iso(nxt["due_at"]).astimezone(TZ) == datetime(2026, 9, 28, 7, 0, tzinfo=TZ)


def test_overdue_open_excludes_habits_and_future(env):
    svc, clock = env
    [overdue] = svc.create("созвон в 20:00")  # сегодня 26.09, станет просроченным завтра
    [habit] = svc.create("йога")  # ритуал, не должен попасть
    [future] = svc.create("завтра в 10 созвон с клиентом")
    clock.dt = datetime(2026, 9, 27, 8, 0, tzinfo=TZ)
    ids = {t["id"] for t in svc.overdue_open()}
    assert ids == {overdue["id"]}
    assert habit["id"] not in ids
    assert future["id"] not in ids

    svc.mark_missed(overdue["id"])
    assert svc.get(overdue["id"])["status"] == "missed"
    assert svc.overdue_open() == []
    # штраф за просрочку не применяется — HP/XP не трогаем
    assert svc.game.hero()["hp"] == 100
    assert svc.game.hero()["xp"] == 0


def test_missed_pending_followup(env):
    svc, clock = env
    [t] = svc.create("созвон в 20:00")  # сегодня 26.09, станет просроченным завтра
    clock.dt = datetime(2026, 9, 27, 8, 0, tzinfo=TZ)

    svc.mark_missed_pending(t["id"])
    assert svc.get(t["id"])["status"] == "missed_pending"
    assert svc.overdue_open() == []  # не висит в обычном «сделал/не сделал»
    ids = {m["id"] for m in svc.missed_pending()}
    assert ids == {t["id"]}

    # «Сегодня» — реоткрывает с дедлайном на сегодня, без времени
    reopened = svc.reopen_today(t["id"])
    assert reopened["status"] == "open"
    assert parse_iso(reopened["due_at"]).astimezone(TZ).date() == clock.dt.date()
    assert reopened["has_time"] == 0
    assert svc.missed_pending() == []

    # «Не нужно» — финальное закрытие, без штрафа
    svc.mark_missed_pending(t["id"])
    svc.mark_missed(t["id"])
    assert svc.get(t["id"])["status"] == "missed"
    assert svc.missed_pending() == []
    assert svc.game.hero()["hp"] == 100

    # ответ с новой датой реанимирует «мёртвую» задачу через edit_from_text
    svc.mark_missed_pending(t["id"])
    revived = svc.edit_from_text(t["id"], "завтра в 15")
    assert revived["status"] == "open"
    assert parse_iso(revived["due_at"]).astimezone(TZ) == datetime(2026, 9, 28, 15, 0, tzinfo=TZ)


def test_fall_and_boss(env):
    svc, _ = env
    svc.db.x("UPDATE hero SET hp=5, gold=40")
    [t] = svc.create("привычка планка")
    msgs = svc.game.miss_habit(t)
    assert any("пал в бою" in m for m in msgs)
    h = svc.game.hero()
    assert h["hp"] == 50 and h["gold"] == 20 and h["fallen"] == 1

    [b] = svc.create("запустить лендинг, важно")
    svc.make_boss(b["id"])
    xp, msgs, _ = svc.done(b["id"])
    assert xp >= 300
    assert any("БОСС" in m for m in msgs)


def test_snooze_tomorrow_keeps_time(env):
    svc, _ = env
    [t] = svc.create("созвон в 20:00")
    t = svc.snooze(t["id"], tomorrow=True)
    assert parse_iso(t["due_at"]).astimezone(TZ) == datetime(2026, 9, 27, 20, 0, tzinfo=TZ)
    assert t["remind_at"] == t["due_at"]


def test_edit_from_text(env):
    svc, _ = env
    [t] = svc.create("купить хлеб")
    t = svc.edit_from_text(t["id"], "завтра в 9")
    assert t["title"] == "Купить хлеб"
    assert parse_iso(t["due_at"]).astimezone(TZ).hour == 9
    t = svc.edit_from_text(t["id"], "купить багет")
    assert t["title"] == "Купить багет"


def test_day_view(env):
    svc, clock = env
    svc.create("созвон сегодня в 20:00")
    svc.create("отчёт завтра")
    svc.create("!1 закрыть кампанию")
    d = svc.day(clock(), include_overdue=True)
    assert [t["title"] for t in d["dated"]] == ["Созвон"]
    assert [t["title"] for t in d["undated_q1"]] == ["Закрыть кампанию"]


def test_quiet_hours():
    n = datetime(2026, 9, 26, 23, 45, tzinfo=TZ)
    assert scheduler.in_quiet(n, "23:30", "07:30")
    assert scheduler.in_quiet(n.replace(hour=3), "23:30", "07:30")
    assert not scheduler.in_quiet(n.replace(hour=12), "23:30", "07:30")
    assert scheduler.quiet_end(n, "07:30") == datetime(2026, 9, 27, 7, 30, tzinfo=TZ)


class FakeIns:
    active = False

    async def sync(self, t): pass
    async def cancel(self, tid): pass
    async def disarm_due(self): pass


class FakeApp:
    """App без Telegram: копит отправленные тексты."""

    def __init__(self, svc):
        self.svc, self.db, self.ins, self.bot, self.owner = svc, svc.db, FakeIns(), None, 0
        self.sent = []
        self.ready = True
        self.forum = 0
        from dungeon.events import Events
        from dungeon.leisure import Leisure
        from dungeon.planner import Planner
        self.events = Events(svc.db)
        self.leisure = Leisure(svc.db)
        self.planner = Planner(svc, self.events)
        self.plan_busy = False
        self.events_running = True  # афиша в тестах не собирается

    def spawn(self, coro):
        asyncio.get_event_loop().create_task(coro)

    async def send(self, text, kb=None, silent=False, topic="alerts"):
        self.sent.append(text)
        return True  # как настоящий App.send: None только при сбое отправки

    async def announce(self, msgs):
        if msgs:
            self.sent.append("\n".join(msgs))

    async def changed(self, tid):
        pass


def test_tick_nags_habit_and_sends_summaries(env, monkeypatch):
    svc, clock = env
    monkeypatch.setattr("dungeon.scheduler.time.tzset", lambda: None)
    app = FakeApp(svc)
    svc.db.set("mac_popup", "0")
    [t] = svc.create("йога в 19:00")
    # 19:00 — первый нагинг и утренняя сводка вдогонку (утро пропущено)
    clock.dt = datetime(2026, 9, 26, 19, 0, 5, tzinfo=TZ)
    asyncio.run(scheduler.tick(app))
    assert any("РИТУАЛ" in s for s in app.sent)
    assert any("☀️" in s for s in app.sent)
    t = svc.get(t["id"])
    assert t["nag_count"] == 1
    assert parse_iso(t["next_nag_at"]).astimezone(TZ) == datetime(2026, 9, 26, 19, 15, 5, tzinfo=TZ)
    # ещё три повтора -> третий уровень (капс)
    for _ in range(3):
        clock.dt = parse_iso(svc.get(t["id"])["next_nag_at"]).astimezone(TZ)
        asyncio.run(scheduler.tick(app))
    assert any("НЕ СДЕЛАН" in s for s in app.sent)
    # 21:00 — вечерняя сводка
    clock.dt = datetime(2026, 9, 26, 21, 0, tzinfo=TZ)
    app.sent.clear()
    asyncio.run(scheduler.tick(app))
    assert any("Итоги дня" in s for s in app.sent)
    # сделал — повторы прекращаются
    svc.done(t["id"])
    clock.dt += timedelta(hours=1)
    app.sent.clear()
    asyncio.run(scheduler.tick(app))
    assert not any("РИТУАЛ" in s for s in app.sent)


def test_weekly_review_and_obsidian(env, tmp_path):
    from dungeon import obsidian, texts
    svc, clock = env
    [t] = svc.create("сделать отчёт, важно")
    svc.done(t["id"])
    text, stats = texts.weekly_review(svc)
    assert stats["done"] == 1 and "Хроники недели" in text
    path = obsidian.export_week(stats, svc.game.hero(), clock(), base=tmp_path)
    assert path.exists() and "[[Хроники подземелья — журнал недель]]" in path.read_text()
    hub = (tmp_path / "Хроники подземелья — журнал недель.md").read_text()
    assert f"[[{path.stem}]]" in hub
    obsidian.export_week(stats, svc.game.hero(), clock(), base=tmp_path)  # повтор без дубля ссылки
    hub = (tmp_path / "Хроники подземелья — журнал недель.md").read_text()
    assert hub.count(f"[[{path.stem}]]") == 1


def test_checklist_toggle_awards_once(env):
    svc, _ = env
    svc.set_checklist("morning", ["вода", "зарядка"])
    state, msgs = svc.checklist_toggle("morning", 0)
    assert [i["done"] for i in state] == [True, False] and not msgs
    state, msgs = svc.checklist_toggle("morning", 1)
    assert any("чек-лист закрыт" in m for m in msgs)
    svc.checklist_toggle("morning", 1)
    _, msgs = svc.checklist_toggle("morning", 1)  # снял и снова поставил — повторной награды нет
    assert not msgs
    assert svc.game.hero()["xp"] == 15


def test_morning_on_wake_and_evening_checklist(env, monkeypatch):
    svc, clock = env
    idle = {"v": 1e9}
    opened = []
    monkeypatch.setattr("dungeon.scheduler.mac.idle_seconds", lambda: idle["v"])
    monkeypatch.setattr("dungeon.scheduler.mac.open_url", opened.append)
    monkeypatch.setattr("dungeon.scheduler.time.tzset", lambda: None)
    monkeypatch.setattr("dungeon.scheduler.config.WEB_KEY", "k")
    app = FakeApp(svc)
    clock.dt = datetime(2026, 9, 29, 6, 30, tzinfo=TZ)  # вторник: без обзора и коуча
    asyncio.run(scheduler.tick(app))
    assert not any("☀️" in s for s in app.sent)  # 06:30, ноут никто не трогает
    idle["v"] = 5  # открыл ноут
    asyncio.run(scheduler.tick(app))
    assert any("Утренний чек-лист" in s for s in app.sent)
    assert opened and opened[0].endswith("#checklist")
    # вечер: в сундуке задача -> чек-лист ждёт разбора
    [t] = svc.create("купить хлеб")
    clock.dt = datetime(2026, 9, 29, 21, 0, tzinfo=TZ)
    app.sent.clear()
    asyncio.run(scheduler.tick(app))
    assert not any("Вечерний чек-лист" in s for s in app.sent)
    svc.set_quadrant(t["id"], 3)  # разобрал сундук
    asyncio.run(scheduler.tick(app))
    assert any("Вечерний чек-лист" in s for s in app.sent)


def test_duration_saved_and_edited(env):
    svc, _ = env
    [t] = svc.create("лендинг ~2ч завтра")
    assert t["duration_min"] == 120
    t = svc.edit_from_text(t["id"], "~3ч")
    assert t["duration_min"] == 180 and t["title"] == "Лендинг"


def test_coach_context(env):
    from dungeon import coach
    svc, clock = env
    [a] = svc.create("лендинг ~2ч в понедельник, важно")
    [b] = svc.create("каждый день в 8 йога")
    ctx = coach.build_context(svc)
    ids = [t["id"] for t in ctx["next_week"]["tasks"]]
    assert a["id"] in ids and b["id"] in ids
    assert ctx["next_week"]["budget_hours"] == 2 * 2 + 4 * 5 + 2 * 2  # сб, вс + неделя пн-вс
    assert "checklists" in ctx["last_week"]


def test_plan_starts_sunday_once(env, monkeypatch):
    svc, clock = env
    monkeypatch.setattr("dungeon.scheduler.time.tzset", lambda: None)
    app = FakeApp(svc)
    clock.dt = datetime(2026, 9, 27, 20, 10, tzinfo=TZ)  # воскресенье
    asyncio.run(scheduler.tick(app))
    asyncio.run(scheduler.tick(app))
    assert sum("шаг 1/4" in s for s in app.sent) == 1
    assert svc.db.get("plan_started") == "2026-W40"
    # в понедельник сессия не закончена -> одно напоминание
    clock.dt = datetime(2026, 9, 28, 10, 30, tzinfo=TZ)
    app.sent.clear()
    asyncio.run(scheduler.tick(app))
    asyncio.run(scheduler.tick(app))
    assert sum("Планирование недели ждёт" in s for s in app.sent) == 1


def test_free_time_ask_rules(env):
    from dungeon import leisure
    svc, clock = env
    clock.dt = datetime(2026, 9, 29, 13, 0, tzinfo=TZ)
    tasks = lambda: svc.open_tasks()  # noqa: E731
    assert leisure.should_ask(svc.db, tasks(), clock(), False)
    svc.create("созвон в 14:00")  # через час занят
    assert not leisure.should_ask(svc.db, tasks(), clock(), False)
    clock.dt = datetime(2026, 9, 29, 16, 0, tzinfo=TZ)
    assert leisure.should_ask(svc.db, tasks(), clock(), False)
    leisure.mark_asked(svc.db, clock())
    clock.dt += timedelta(hours=1)
    assert not leisure.should_ask(svc.db, tasks(), clock(), False)  # пауза 3 часа
    clock.dt += timedelta(hours=3)
    leisure.mark_asked(svc.db, clock())
    clock.dt += timedelta(hours=4)
    assert not leisure.should_ask(svc.db, tasks(), clock(), False)  # лимит 2 в день / окно до 21
    assert not leisure.should_ask(svc.db, tasks(), datetime(2026, 9, 30, 13, 0, tzinfo=TZ), True)  # рейд идёт
    nxt = leisure.next_up(tasks(), datetime(2026, 9, 29, 12, 0, tzinfo=TZ))
    assert nxt["title"] == "Созвон"


def test_leisure_pick_no_repeats(env, tmp_path):
    from dungeon.leisure import Leisure, scan
    svc, _ = env
    d = tmp_path / "Досуг"
    d.mkdir()
    (d / "A.md").write_text("# Фильм A\n\nОписание A\n\n## Связанные заметки\n- [[Жанр — Фильмы — Хоррор]]\n")
    (d / "B.md").write_text("# Книга B\n\nОписание B\n- [[Жанр — Книги — Классика - Философия - Сборные списки]]\n")
    (d / "Жанр — Фильмы — Хоррор.md").write_text("# хаб")
    items = scan(d)
    assert {i["kind"] for i in items} == {"film", "book"}
    ls = Leisure(svc.db)
    a = ls.pick("film", items)
    assert a["title"] == "Фильм A" and a["desc"] == "Описание A"
    assert ls.pick("film", items) is None  # недавно предлагали
    ls.mark(a["rid"], "seen")
    assert ls.pick("film", items) is None


def test_plan_week_and_clean():
    from dungeon import coach
    sat = datetime(2026, 9, 26, 12, 0, tzinfo=TZ)
    assert coach.plan_start(sat).date().isoformat() == "2026-09-28"
    assert coach.week_key(sat) == "2026-W40"
    assert coach.week_key(datetime(2026, 9, 30, tzinfo=TZ)) == "2026-W40"
    assert coach._clean({"a": ["план — пустой"]}) == {"a": ["план, пустой"]}


def test_leisure_frontmatter_and_mark_seen(tmp_path):
    from dungeon.leisure import mark_seen_in_note, scan
    d = tmp_path / "Досуг"
    d.mkdir()
    (d / "A.md").write_text("---\ntype: сериал\nstatus: не смотрел\ninterest: хочу\ntags: [не_смотрел, хочу]\n---\n# A\n\nПро A\n")
    (d / "B.md").write_text("---\ntype: фильм\nstatus: смотрел\n---\n# B\n")
    (d / "C.md").write_text("---\ntype: книга\nstatus: не читал\ninterest: не хочу\n---\n# C\n")
    items = scan(d)
    assert [(i["title"], i["kind"], i["want"]) for i in items] == [("A", "series", True)]
    assert mark_seen_in_note("A.md", "series", d)
    text = (d / "A.md").read_text()
    assert "status: смотрел" in text and "tags: [смотрел, хочу]" in text and text.endswith("Про A\n")
    assert scan(d) == []
