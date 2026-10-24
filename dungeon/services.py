"""Операции над задачами. Общий слой для бота, веба и планировщика."""
from __future__ import annotations

import json
import re
from datetime import datetime, time, timedelta

from dateutil.rrule import rrulestr

from . import caldav_sync, config
from .db import DB, iso, parse_iso, utcnow
from .game import Game, game_date
from .parser import Parsed, parse


def local_now() -> datetime:
    return datetime.now().astimezone().replace(microsecond=0)


def hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


class Service:
    def __init__(self, db: DB, clock=local_now):
        self.db = db
        self.clock = clock
        self.game = Game(db, clock)

    # ---------- чтение ----------
    def get(self, task_id: int):
        return self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))

    def open_tasks(self):
        return self.db.q("SELECT * FROM tasks WHERE status='open' ORDER BY due_at IS NULL, due_at, quadrant, id")

    def day(self, day: datetime, include_overdue: bool) -> dict:
        """Задачи на локальную дату day. Для сегодня добавляем просроченные и Q1 без даты."""
        start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        res = {"dated": [], "overdue": [], "undated_q1": [], "undated_q2": []}
        for t in self.open_tasks():
            due = parse_iso(t["due_at"])
            if due:
                due = due.astimezone(start.tzinfo)
                if start <= due < end:
                    res["dated"].append(t)
                elif due < start and include_overdue:
                    res["overdue"].append(t)
            elif t["quadrant"] == 1:
                res["undated_q1"].append(t)
            elif t["quadrant"] == 2 and not t["is_habit"]:
                res["undated_q2"].append(t)
        return res

    def inbox(self):
        return self.db.q("SELECT * FROM tasks WHERE status='open' AND quadrant IS NULL ORDER BY id")

    def week(self) -> list:
        now = self.clock()
        end = (now + timedelta(days=7)).replace(hour=23, minute=59)
        return [t for t in self.open_tasks() if t["due_at"] and parse_iso(t["due_at"]) <= end]

    def boss(self):
        return self.db.one("SELECT * FROM tasks WHERE is_boss=1 AND status='open'")

    # ---------- создание ----------
    def create(self, text: str, source: str = "tg") -> list:
        parts = [p.strip(" -•\t") for p in re.split(r"[\n;]+", text) if p.strip(" -•\t")]
        return [self.create_parsed(parse(p, self.clock()), source) for p in parts]

    def create_parsed(self, p: Parsed, source: str):
        now = self.clock()
        series = None
        if p.is_habit and not p.rrule:
            # разовые «йога» копят общий стрик по названию
            row = self.db.one("SELECT series_id FROM streaks WHERE lower(title)=lower(?)", (p.title,))
            series = row["series_id"] if row else None
        tid = self.db.x(
            "INSERT INTO tasks(title, quadrant, is_habit, due_at, has_time, rrule, source, created_at, series_id,"
            " duration_min) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (p.title, p.quadrant, int(p.is_habit), iso(p.due_at), int(p.has_time), p.rrule, source,
             iso(utcnow()), series, p.duration_min))
        if series is None and (p.rrule or p.is_habit):
            self.db.x("UPDATE tasks SET series_id=? WHERE id=?", (tid, tid))
        self._reschedule(tid, now)
        self._queue_calendar(tid, "upsert")
        return self.get(tid)

    def _queue_calendar(self, tid: int, action: str = "upsert") -> None:
        """Отложить отправку в Apple Calendar — сетевой вызов делает scheduler, не этот слой.
        Для upsert инстансы серии (source='repeat') пропускаем: их покрывает мастер-событие."""
        if not caldav_sync.enabled():
            return
        t = self.get(tid)
        if not t:
            return
        if action == "upsert" and (t["source"] == "repeat" or not t["due_at"]):
            return
        self.db.x("INSERT INTO calendar_outbox(task_id, action, requested_at) VALUES(?,?,?)",
                  (tid, action, iso(utcnow())))

    def _reschedule(self, tid: int, now: datetime | None = None) -> None:
        """Пересчитать remind_at по due_at / привычке."""
        now = now or self.clock()
        t = self.get(tid)
        due = parse_iso(t["due_at"])
        remind = None
        if due and t["has_time"]:
            remind = due
        elif t["is_habit"]:
            day = due.astimezone(now.tzinfo) if due else now
            remind = datetime.combine(day.date(), hm(self.db.get("habit_time")), now.tzinfo)
            if remind <= now and day.date() == now.date():
                remind = now + timedelta(minutes=30)
        self.db.x("UPDATE tasks SET remind_at=?, next_nag_at=?, nag_count=0 WHERE id=?",
                  (iso(remind), iso(remind), tid))

    # ---------- изменения ----------
    def set_quadrant(self, tid: int, q: int | None):
        self.db.x("UPDATE tasks SET quadrant=? WHERE id=?", (q, tid))
        return self.get(tid)

    def toggle_habit(self, tid: int):
        self.db.x("UPDATE tasks SET is_habit=1-is_habit, series_id=COALESCE(series_id, id) WHERE id=?", (tid,))
        self._reschedule(tid)
        self._queue_calendar(tid, "upsert")
        return self.get(tid)

    def rename(self, tid: int, title: str):
        self.db.x("UPDATE tasks SET title=? WHERE id=?", (title.strip(), tid))
        self._queue_calendar(tid, "upsert")
        return self.get(tid)

    def edit_from_text(self, tid: int, text: str):
        """Ответ на карточку: «завтра в 15» меняет срок, «~2ч» — длительность, «важно» — зону.
        Если ничего такого в ответе нет, текст становится новым названием."""
        p = parse(text, self.clock())
        has_meta = bool(p.due_at or p.rrule or p.duration_min or p.important is not None or p.urgent is not None)
        if p.duration_min:
            self.db.x("UPDATE tasks SET duration_min=? WHERE id=?", (p.duration_min, tid))
        if p.due_at or p.rrule:
            t = self.get(tid)
            reopen = ", status='open'" if t["status"] in ("missed", "missed_pending") else ""
            self.db.x(f"UPDATE tasks SET due_at=?, has_time=?, rrule=COALESCE(?, rrule){reopen} WHERE id=?",
                      (iso(p.due_at), int(p.has_time), p.rrule, tid))
            self._reschedule(tid)
            self._queue_calendar(tid, "upsert")
        if not has_meta:
            self.rename(tid, p.title)
        if p.quadrant and (p.important is not None or p.urgent is not None):
            self.set_quadrant(tid, p.quadrant)
        return self.get(tid)

    def set_due(self, tid: int, due: datetime | None, has_time: bool):
        self.db.x("UPDATE tasks SET due_at=?, has_time=? WHERE id=?", (iso(due), int(has_time), tid))
        self._reschedule(tid)
        self._queue_calendar(tid, "upsert")
        return self.get(tid)

    def snooze(self, tid: int, minutes: int | None = None, tomorrow: bool = False):
        now = self.clock()
        t = self.get(tid)
        if tomorrow:
            due = parse_iso(t["due_at"])
            tmr = now.date() + timedelta(days=1)
            if due and t["has_time"]:
                new = datetime.combine(tmr, due.astimezone(now.tzinfo).timetz())
            else:
                new = datetime.combine(tmr, time(0, 0), now.tzinfo)
            return self.set_due(tid, new, bool(due and t["has_time"]))
        nxt = now + timedelta(minutes=minutes or 15)
        self.db.x("UPDATE tasks SET next_nag_at=?, remind_at=COALESCE(remind_at, ?) WHERE id=?",
                  (iso(nxt), iso(nxt), tid))
        return self.get(tid)

    def make_boss(self, tid: int):
        self.db.x("UPDATE tasks SET is_boss=0 WHERE status='open'")
        self.db.x("UPDATE tasks SET is_boss=1 WHERE id=?", (tid,))
        return self.get(tid)

    # ---------- завершение ----------
    def done(self, tid: int) -> tuple[int, list[str], object]:
        t = self.get(tid)
        if not t or t["status"] != "open":
            return 0, [], None
        self.db.x("UPDATE tasks SET status='done', done_at=? WHERE id=?", (iso(utcnow()), tid))
        if not t["rrule"]:
            self._queue_calendar(tid, "delete")  # серию в календаре не трогаем, это одна её строка
        xp, msgs = self.game.complete(t)
        nxt = self._spawn_next(t)
        return xp, msgs, nxt

    def skip(self, tid: int):
        """Пропустить экземпляр повторяющейся задачи без штрафа."""
        t = self.get(tid)
        if not t or t["status"] != "open":  # старая карточка после «Сделал» или двойной тап: без дубля экземпляра
            return None
        self.db.x("UPDATE tasks SET status='skipped' WHERE id=?", (tid,))
        return self._spawn_next(t)

    def skip_missed(self, tid: int):
        """Прогул ритуала по решению Некита: штраф skip_penalty_hp, стрик в ноль, серия идёт дальше."""
        t = self.get(tid)
        if not t or t["status"] != "open":
            return [], None
        self.db.x("UPDATE tasks SET status='missed' WHERE id=?", (tid,))
        msgs = self.game.miss_habit(t, hp=int(self.db.get("skip_penalty_hp")))
        return msgs, self._spawn_next(t)

    def reopen(self, tid: int):
        """Вернуть снятую задачу в открытые (отмена отмены)."""
        self.db.x("UPDATE tasks SET status='open' WHERE id=?", (tid,))
        self._reschedule(tid)
        self._queue_calendar(tid, "upsert")
        return self.get(tid)

    def delete(self, tid: int) -> list[str]:
        t = self.get(tid)
        if not t:
            return []
        self.db.x("UPDATE tasks SET status='deleted' WHERE id=?", (tid,))
        self._queue_calendar(tid, "delete")  # удаляем и мастер-событие серии, если задача была её частью
        return self.game.deleted(t)

    def overdue_open(self) -> list:
        """Обычные (не ритуальные) задачи с прошедшим дедлайном, которые всё ещё open.
        Для утреннего опроса «сделал или нет» — штрафа тут нет, только фиксация факта."""
        now = self.clock()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        for t in self.db.q("SELECT * FROM tasks WHERE status='open' AND is_habit=0 AND due_at IS NOT NULL"):
            if parse_iso(t["due_at"]).astimezone(start.tzinfo) < start:
                out.append(t)
        return out

    def mark_missed(self, tid: int) -> None:
        self.db.x("UPDATE tasks SET status='missed' WHERE id=?", (tid,))

    def mark_missed_pending(self, tid: int) -> None:
        """Отмечено «не сделал», но решение — делать ли и когда — ещё не принято."""
        self.db.x("UPDATE tasks SET status='missed_pending' WHERE id=?", (tid,))

    def missed_pending(self) -> list:
        """Задачи, которые отметили «не сделал», но не решили, когда делать — для повторного вопроса."""
        return self.db.q("SELECT * FROM tasks WHERE status='missed_pending'")

    def reopen_today(self, tid: int):
        """«Будешь делать? → Сегодня»: возвращает задачу в открытые с дедлайном на сегодня, без времени."""
        today = self.clock().replace(hour=0, minute=0, second=0, microsecond=0)
        self.db.x("UPDATE tasks SET status='open', due_at=?, has_time=0 WHERE id=?", (iso(today), tid))
        self._reschedule(tid)
        self._queue_calendar(tid, "upsert")
        return self.get(tid)

    def _spawn_next(self, t):
        if not t["rrule"]:
            return None
        now = self.clock()
        due = parse_iso(t["due_at"]).astimezone(now.tzinfo)
        rule = rrulestr(t["rrule"], dtstart=due)
        nxt = rule.after(max(due, now))
        if not nxt:
            return None
        tid = self.db.x(
            "INSERT INTO tasks(title, notes, quadrant, is_habit, due_at, has_time, rrule, series_id, source, created_at,"
            " duration_min) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (t["title"], t["notes"], t["quadrant"], t["is_habit"], iso(nxt), t["has_time"], t["rrule"],
             t["series_id"] or t["id"], "repeat", iso(utcnow()), t["duration_min"]))
        self._reschedule(tid, now)
        return self.get(tid)

    def resting(self, day=None) -> bool:
        """Режим отдыха (/rest, идея из Habitica): до rest_until включительно ритуалы не штрафуют и не напоминают."""
        until = self.db.get("rest_until", "") or ""
        return bool(until) and (day or self.clock().date()).isoformat() <= until

    # ---------- смена суток ----------
    def rollover(self) -> tuple[list, list[str]]:
        """Ритуалы, не сделанные до конца своего дня, считаются пропущенными.
        Просроченные повторяющиеся задачи порождают следующий экземпляр."""
        now = self.clock()
        ds = self.db.get("day_start") or "00:00"
        start = datetime.combine(game_date(now, ds), hm(ds), now.tzinfo)  # начало игрового дня
        missed, msgs = [], []
        for t in self.db.q("SELECT * FROM tasks WHERE status='open' AND due_at IS NOT NULL"):
            due = parse_iso(t["due_at"]).astimezone(now.tzinfo)
            if due >= start:
                continue
            if t["is_habit"] and self.resting(game_date(due, self.db.get("day_start"))):  # отпуск/болезнь: ни штрафа, ни сгоревшего стрика
                self.db.x("UPDATE tasks SET status='skipped' WHERE id=?", (t["id"],))
                self._spawn_next(t)
            elif t["is_habit"]:
                self.db.x("UPDATE tasks SET status='missed' WHERE id=?", (t["id"],))
                msgs += self.game.miss_habit(t)
                missed.append(t)
                self._spawn_next(t)
            elif t["rrule"]:
                self.db.x("UPDATE tasks SET status='missed' WHERE id=?", (t["id"],))
                self._spawn_next(t)
        return missed, msgs

    # ---------- фокус ----------
    def focus_start(self, tid: int | None):
        self.db.x("UPDATE focus SET status='cancelled' WHERE status='running'")
        now = utcnow()
        mins = int(self.db.get("focus_min"))
        fid = self.db.x("INSERT INTO focus(task_id, started_at, ends_at) VALUES(?,?,?)",
                        (tid, iso(now), iso(now + timedelta(minutes=mins))))
        return self.db.one("SELECT * FROM focus WHERE id=?", (fid,))

    def focus_current(self):
        return self.db.one("SELECT * FROM focus WHERE status='running' ORDER BY id DESC LIMIT 1")

    def focus_cancel(self):
        self.db.x("UPDATE focus SET status='cancelled' WHERE status='running'")

    def focus_finish_due(self) -> list:
        """Закрыть рейды, у которых вышло время. Возвращает список (focus, xp, msgs)."""
        out = []
        for f in self.db.q("SELECT * FROM focus WHERE status='running' AND ends_at<=?", (iso(utcnow()),)):
            self.db.x("UPDATE focus SET status='done' WHERE id=?", (f["id"],))
            xp, msgs = self.game.focus_done()
            out.append((f, xp, msgs))
        return out

    # ---------- таверна ----------
    def rewards(self):
        return self.db.q("SELECT * FROM rewards ORDER BY price")

    def add_reward(self, title: str, price: int):
        return self.db.x("INSERT INTO rewards(title, price) VALUES(?,?)", (title, price))

    def buy(self, rid: int) -> str:
        r = self.db.one("SELECT * FROM rewards WHERE id=?", (rid,))
        if not r:
            return "Такой награды нет в таверне."
        gold = self.game.hero()["gold"]
        if gold < r["price"]:
            return f"Не хватает золота: {gold}/{r['price']} 🪙. Иди в подземелье."
        self.db.x("UPDATE hero SET gold=gold-? WHERE id=1", (r["price"],))
        return f"🍺 Куплено: {r['title']} (−{r['price']} 🪙). Заслужил, наслаждайся."

    # ---------- чек-листы ----------
    def checklist_items(self, kind: str) -> list[str]:
        custom = self.db.get(f"checklist_{kind}")
        if custom:
            return json.loads(custom)
        return list(config.yaml_file("checklists").get(kind, []))

    def set_checklist(self, kind: str, items: list[str] | None) -> None:
        if items:
            self.db.set(f"checklist_{kind}", json.dumps(items, ensure_ascii=False))
        else:
            self.db.x("DELETE FROM kv WHERE key=?", (f"checklist_{kind}",))

    def checklist_state(self, kind: str, day: str | None = None) -> list[dict]:
        day = day or self.clock().date().isoformat()
        done = {r["idx"] for r in self.db.q("SELECT idx FROM checklist WHERE day=? AND kind=?", (day, kind))}
        return [{"idx": i, "text": t, "done": i in done} for i, t in enumerate(self.checklist_items(kind))]

    def checklist_toggle(self, kind: str, idx: int) -> tuple[list[dict], list[str]]:
        """Отметить/снять пункт. Первое полное закрытие за день даёт награду."""
        day = self.clock().date().isoformat()
        if self.db.one("SELECT 1 FROM checklist WHERE day=? AND kind=? AND idx=?", (day, kind, idx)):
            self.db.x("DELETE FROM checklist WHERE day=? AND kind=? AND idx=?", (day, kind, idx))
        else:
            self.db.x("INSERT INTO checklist(day, kind, idx, done_at) VALUES(?,?,?,?)", (day, kind, idx, iso(utcnow())))
        state = self.checklist_state(kind, day)
        msgs: list[str] = []
        flag = f"cl_awarded:{day}:{kind}"
        if state and all(i["done"] for i in state) and not self.db.get(flag):
            self.db.set(flag, "1")
            msgs = self.game.checklist_done(kind)
        return state, msgs
