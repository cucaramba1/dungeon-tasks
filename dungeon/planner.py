"""Планирование недели в 4 шага с коучем.

  1 контекст  → Некит надиктовывает встречи и дела          → [Далее] Claude A
  2 цели      → итоги прошлой недели, цели прошлых недель,
                предложенные цели                           → [Далее] Claude B
  3 задачи    → задачи под цели                             → [Далее] Claude C
  4 неделя    → расстановка по дням + события афиши         → [Готово] (Claude D, если были правки)

Между шагами копятся ответы (текст и голос), дальше только по кнопке.
Задачи появляются в базе только на «Готово».
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from . import coach, offline
from .db import DB, iso, parse_iso, utcnow
from .events import Events
from .game import ZONES
from .parser import Parsed
from .services import Service

STEP_NAMES = {1: "Контекст недели", 2: "Цели", 3: "Задачи", 4: "Неделя по дням"}
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

# ---------- схемы ответов ----------
EVENT_ITEM = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "date": {"type": "string", "description": "YYYY-MM-DD"},
        "time": {"type": "string", "description": "HH:MM или пусто"},
        "duration_min": {"type": "integer"},
        "rrule": {"type": "string", "description": "FREQ=WEEKLY;BYDAY=FR для регулярных, иначе пусто"},
        "is_habit": {"type": "boolean"},
    },
    "required": ["title", "date"],
}
GOAL = {
    "type": "object",
    "properties": {"goal": {"type": "string"}, "measure": {"type": "string"}, "why": {"type": "string"}},
    "required": ["goal", "measure"],
}
PLAN_ITEM = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "kind": {"type": "string", "enum": ["context", "task", "existing", "event"]},
        "existing_id": {"type": "integer"},
        "event_id": {"type": "integer"},
        "goal": {"type": "string", "description": "к какой цели относится, пусто для контекста"},
        "date": {"type": "string", "description": "YYYY-MM-DD, пусто если без даты"},
        "time": {"type": "string", "description": "HH:MM или пусто"},
        "duration_min": {"type": "integer"},
        "quadrant": {"type": "integer", "description": "1-4"},
        "is_habit": {"type": "boolean"},
        "rrule": {"type": "string"},
    },
    "required": ["title", "kind"],
}

SCHEMA_A = {
    "type": "object",
    "properties": {
        "context_events": {"type": "array", "items": EVENT_ITEM},
        "retro": {"type": "string"},
        "prev_goals": {"type": "array", "items": {
            "type": "object",
            "properties": {"goal": {"type": "string"}, "status": {"type": "string", "enum": ["done", "partial", "missed"]},
                           "note": {"type": "string"}},
            "required": ["goal", "status"]}},
        "goals": {"type": "array", "items": GOAL},
        "questions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["context_events", "retro", "prev_goals", "goals"],
}
SCHEMA_B = {
    "type": "object",
    "properties": {
        "context_events": {"type": "array", "items": EVENT_ITEM},
        "goals": {"type": "array", "items": GOAL},
        "tasks": {"type": "array", "items": PLAN_ITEM},
        "comment": {"type": "string"},
    },
    "required": ["context_events", "goals", "tasks"],
}
SCHEMA_C = {
    "type": "object",
    "properties": {
        "items": {"type": "array", "items": PLAN_ITEM},
        "load": {"type": "object", "properties": {
            "planned_hours": {"type": "number"}, "budget_hours": {"type": "number"}, "verdict": {"type": "string"}},
            "required": ["planned_hours", "budget_hours", "verdict"]},
        "comment": {"type": "string"},
    },
    "required": ["items", "load"],
}

COMMON = """Правила: даты только в окне недели из данных (week.days). Не выдумывай встреч и фактов, которых нет \
в словах Некита или данных. Пиши по-русски, коротко, без длинного тире. Названия задач: глагол + объект, до 60 символов.
Чек-листы утра и вечера ведутся отдельно, задачи под них не создавай. \
Если событие или задача уже есть в open_tasks (включая повторяющиеся: йога, ДнД), не дублируй: kind=existing и existing_id."""

PROMPT_A = """Шаг 1 → 2 планирования недели. Некит надиктовал контекст недели (inputs.context): встречи, дела, врачи, поездки, \
регулярные занятия. Сделай:
1) context_events: каждое упомянутое событие с датой (YYYY-MM-DD), временем если назвал, длительностью (оцени, если не \
сказал), rrule для регулярных («по пятницам» → FREQ=WEEKLY;BYDAY=FR). Привычки (йога, спорт) помечай is_habit.
2) retro: 2-3 предложения, как прошла прошлая неделя по данным last_week (факты, без воды).
3) prev_goals: цели прошлых недель (previous_week_goals) и что с ними по факту (done/partial/missed + короткий note).
4) goals: 2-4 цели на эту неделю. Опирайся на долгосрочные цели (goals_note), незакрытое с прошлых недель и контекст. \
У каждой measure: как поймём, что достигнута.
5) questions: 0-2 уточнения, если без них никак.
""" + COMMON

PROMPT_B = """Шаг 2 → 3. У тебя есть твой черновик шага 2 (drafts.a) и правки Некита к нему (inputs.goals). \
Также он мог поправить контекст. Сделай:
1) context_events: итоговый список событий с учётом правок.
2) goals: итоговые цели с учётом правок.
3) tasks: конкретные задачи на неделю, которые приближают к каждой цели (2-5 на цель). kind=task для новых. \
Если среди открытых задач (open_tasks) есть подходящая, возьми её: kind=existing и existing_id, не дублируй. \
У каждой: goal, duration_min (честная оценка), quadrant (1 срочно+важно, 2 важно, 3 срочно, 4 ни то ни другое).
4) comment: одно предложение, если есть риск (например, задач больше, чем часов).
""" + COMMON

PROMPT_C = """Шаг 3 → 4. Черновик задач (drafts.b.tasks), правки Некита (inputs.tasks). Итоговые цели и контекст в drafts.b. \
Расставь неделю:
1) items: все пункты недели. kind=context для событий контекста (с их датой и временем), kind=task/existing для задач \
(поставь date и, где уместно, time в свободные окна вокруг контекста; тяжёлое утром, по 1-2 важные задачи в день), \
kind=event для 1-3 событий из афиши (afisha), которые реально помогают целям или отдыху и не конфликтуют с планом, \
с event_id, их датой и временем. Учитывай бюджет часов week.budget_hours и budget_rule, выходные легче будней.
2) load: planned_hours (сумма duration задач, без контекста и событий), budget_hours, verdict одной фразой.
3) comment: что перенёс или выкинул и почему, если что-то не влезло.
""" + COMMON

PROMPT_D = """Шаг 4, финал. Твоя расстановка (drafts.c.items) и правки Некита к ней (inputs.schedule). \
Верни итоговые items с учётом правок, не меняя то, что он не трогал, и пересчитай load.
""" + COMMON


class Planner:
    def __init__(self, svc: Service, events: Events):
        self.svc = svc
        self.db: DB = svc.db
        self.events = events

    # ---------- сессия ----------
    def active(self):
        return self.db.one("SELECT * FROM plan_sessions WHERE status='active' ORDER BY id DESC LIMIT 1")

    def start(self) -> dict:
        cur = self.active()
        if cur:
            return dict(cur)
        now = iso(utcnow())
        sid = self.db.x("INSERT INTO plan_sessions(week, created_at, updated_at) VALUES(?,?,?)",
                        (coach.week_key(self.svc.clock()), now, now))
        return dict(self.db.one("SELECT * FROM plan_sessions WHERE id=?", (sid,)))

    def get(self, sid: int):
        row = self.db.one("SELECT * FROM plan_sessions WHERE id=?", (sid,))
        if not row:
            return None
        d = dict(row)
        d["inputs"] = json.loads(d["inputs"])
        d["drafts"] = json.loads(d["drafts"])
        return d

    def _save(self, s: dict) -> None:
        self.db.x("UPDATE plan_sessions SET step=?, status=?, inputs=?, drafts=?, updated_at=? WHERE id=?",
                  (s["step"], s["status"], json.dumps(s["inputs"], ensure_ascii=False),
                   json.dumps(s["drafts"], ensure_ascii=False), iso(utcnow()), s["id"]))

    def add_input(self, text: str) -> int:
        s = self.get(self.active()["id"])
        key = str(s["step"])
        s["inputs"].setdefault(key, []).append(text.strip())
        self._save(s)
        return len(s["inputs"][key])

    def back(self, sid: int) -> dict:
        s = self.get(sid)
        s["step"] = max(1, s["step"] - 1)
        self._save(s)
        return s

    def cancel(self, sid: int) -> None:
        s = self.get(sid)
        s["status"] = "cancelled"
        self._save(s)
        self.db.set("plan_started", "")  # отменённое не считается: в вс/пн планирование снова предложится

    # ---------- данные для Claude ----------
    def data(self, s: dict) -> dict:
        base = coach.build_context(self.svc)
        now = self.svc.clock()
        start = coach.plan_start(now)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        days = [(today + timedelta(days=i)) for i in range((start + timedelta(days=7) - today).days)]
        prev = self.db.q("SELECT week, drafts FROM plan_sessions WHERE status='done' AND week<? ORDER BY week DESC LIMIT 4",
                         (s["week"],))
        prev_goals = [{"week": r["week"], "goals": json.loads(r["drafts"]).get("b", {}).get("goals", [])} for r in prev]
        afisha = [{"event_id": e["id"], "title": e["title"], "category": e["category"], "price": e["price"],
                   "starts": parse_iso(e["starts_at"]).astimezone(now.tzinfo).strftime("%Y-%m-%d %H:%M")
                   if e["has_time"] else parse_iso(e["starts_at"]).astimezone(now.tzinfo).strftime("%Y-%m-%d")}
                  for e in self.events.upcoming(today, start + timedelta(days=7), limit=15)]
        return {
            "now": now.strftime("%Y-%m-%d %H:%M"),
            "week": {"key": s["week"], "days": [f"{d:%Y-%m-%d} {WD[d.weekday()]}" for d in days],
                     "budget_hours": base["next_week"]["budget_hours"], "budget_rule": base["next_week"]["budget_rule"]},
            "goals_note": base["goals"],
            "previous_week_goals": prev_goals,
            "last_week": {**base["last_week"], "done": base["last_week"]["done"][:25]},
            "open_tasks": base["next_week"]["tasks"] + base["overdue"] + base["backlog_important_undated"],
            "afisha": afisha,
            "inputs": {"context": s["inputs"].get("1", []), "goals": s["inputs"].get("2", []),
                       "tasks": s["inputs"].get("3", []), "schedule": s["inputs"].get("4", [])},
            "drafts": s["drafts"],
        }

    # ---------- переходы ----------
    async def _ask(self, prompt: str, schema: dict, data: dict, fallback, ask) -> dict:
        """Claude, а если он недоступен (лимит, сеть) — офлайн-сборка шага, чтобы планирование не вставало."""
        try:
            return await ask(prompt, data, schema)
        except coach.ClaudeUnavailable as e:
            draft = fallback(data, self.svc.clock())
            draft["offline_reason"] = ("лимит подписки Claude: " if e.limit else "") + str(e)
            return draft

    async def advance(self, sid: int, ask=coach.ask_claude) -> dict:
        """«Далее»: Claude готовит следующий шаг. Возвращает обновлённую сессию."""
        s = self.get(sid)
        step = s["step"]
        prompt, schema, key, fallback = {1: (PROMPT_A, SCHEMA_A, "a", offline.step_a),
                                         2: (PROMPT_B, SCHEMA_B, "b", offline.step_b),
                                         3: (PROMPT_C, SCHEMA_C, "c", offline.step_c)}[step]
        s["drafts"][key] = await self._ask(prompt, schema, self.data(s), fallback, ask)
        # черновики после текущего шага устарели
        for later in "abc"[("abc".index(key) + 1):]:
            s["drafts"].pop(later, None)
        s["step"] = step + 1
        self._save(s)
        return s

    async def finish(self, sid: int, ask=coach.ask_claude) -> tuple[dict, list]:
        """«Готово» на шаге 4: учесть правки, создать задачи. Возвращает (сессия, созданные задачи)."""
        s = self.get(sid)
        if s["inputs"].get("4"):
            s["drafts"]["c"] = await self._ask(PROMPT_D, SCHEMA_C, self.data(s), offline.step_d, ask)
        created = self.apply(s["drafts"]["c"]["items"])
        s["status"], s["step"] = "done", 5
        self._save(s)
        return s, created

    def expire_stale(self, hours: float = 36) -> dict | None:
        """Брошенное планирование закрываем, чтобы сообщения боту снова становились задачами."""
        s = self.active()
        if s and utcnow() - parse_iso(s["updated_at"]) > timedelta(hours=hours):
            self.cancel(s["id"])
            return self.get(s["id"])
        return None

    def apply(self, items: list[dict]) -> list:
        tz = self.svc.clock().tzinfo
        out = []
        for it in items:
            due, has_time = None, False
            if it.get("date"):
                day = datetime.fromisoformat(it["date"]).date()
                tm = (it.get("time") or "").strip()
                if tm:
                    h, m = tm.split(":")[:2]
                    due, has_time = datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=tz), True
                else:
                    due = datetime(day.year, day.month, day.day, tzinfo=tz)
            if it["kind"] == "existing" and it.get("existing_id") and self.svc.get(it["existing_id"]):
                t = self.svc.get(it["existing_id"])
                if t["status"] == "open" and due:
                    self.svc.set_due(t["id"], due, has_time)
                    if it.get("duration_min"):
                        self.db.x("UPDATE tasks SET duration_min=? WHERE id=?", (it["duration_min"], t["id"]))
                out.append(self.svc.get(t["id"]))
                continue
            q = it.get("quadrant") if it.get("quadrant") in (1, 2, 3, 4) else (None if it["kind"] == "context" else 2)
            if it["kind"] == "event":
                q = 4 if q is None else q
            p = Parsed(title=it["title"][:120], quadrant=q, is_habit=bool(it.get("is_habit")), due_at=due,
                       has_time=has_time, rrule=(it.get("rrule") or None), duration_min=it.get("duration_min") or None)
            t = self.svc.create_parsed(p, "plan")
            if it["kind"] == "event" and it.get("event_id"):
                ev = self.events.get(it["event_id"])
                if ev:
                    self.db.x("UPDATE tasks SET notes=? WHERE id=?", (ev["link"], t["id"]))
            out.append(self.svc.get(t["id"]))
        return out


# ---------- тексты шагов ----------

def _goal_lines(goals: list[dict], esc) -> list[str]:
    return [f"{i}. <b>{esc(g['goal'])}</b>" + (f"\n   <i>как поймём: {esc(g['measure'])}</i>" if g.get("measure") else "")
            for i, g in enumerate(goals, 1)]


def _when(it: dict) -> str:
    if not it.get("date"):
        return "без даты"
    d = datetime.fromisoformat(it["date"])
    return f"{WD[d.weekday()]} {d:%d.%m}" + (f" {it['time']}" if it.get("time") else "")


def render(s: dict, esc) -> str:
    """Сообщение текущего шага. esc — html.escape."""
    step, d = s["step"], s["drafts"]
    head = f"🧙 <b>Планирование недели · шаг {min(step, 4)}/4: {STEP_NAMES.get(step, 'Готово')}</b>\n\n"
    prev_key = {2: "a", 3: "b", 4: "c"}.get(step)
    draft = d.get(prev_key, {}) if prev_key else {}
    if draft.get("offline"):
        head += f"{offline.NOTE}\n<i>{esc(draft.get('offline_reason', ''))}</i>\n\n"
    n = len(s["inputs"].get(str(step), []))
    got = f"\n\n<i>Принято сообщений: {n}</i>" if n else ""
    if step == 1:
        return head + ("Расскажи, что уже есть на этой неделе: встречи, созвоны, врачи, поездки, регулярные занятия "
                       "(ДнД, йога, книжный клуб: в какие дни и во сколько). Можно несколькими войсами или текстами.\n"
                       "Когда всё сказал, жми «Далее».") + got
    if step == 2:
        a = d["a"]
        lines = [head + "<b>Прошлая неделя</b>\n" + esc(a["retro"])]
        if a.get("prev_goals"):
            icon = {"done": "✅", "partial": "🟡", "missed": "❌"}
            lines.append("<b>Цели прошлых недель</b>\n" + "\n".join(
                f"{icon.get(g['status'], '•')} {esc(g['goal'])}" + (f": {esc(g['note'])}" if g.get("note") else "")
                for g in a["prev_goals"]))
        if a.get("context_events"):
            lines.append("<b>Понял из контекста</b>\n" + "\n".join(
                f"📌 {_when(e)} {esc(e['title'])}" + (" 🔁" if e.get("rrule") else "") for e in a["context_events"]))
        lines.append("<b>Цели на неделю</b>\n" + "\n".join(_goal_lines(a["goals"], esc)))
        if a.get("questions"):
            lines.append("<b>Уточни</b>\n" + "\n".join(f"❓ {esc(q)}" for q in a["questions"]))
        lines.append("Поправь цели (или контекст) голосом или текстом. Если всё ок, сразу «Далее».")
        return "\n\n".join(lines) + got
    if step == 3:
        b = d["b"]
        by_goal: dict[str, list] = {}
        for t in b["tasks"]:
            by_goal.setdefault(t.get("goal") or "Прочее", []).append(t)
        lines = [head + "<b>Цели</b>\n" + "\n".join(esc(g["goal"]) for g in b["goals"])]
        for g, ts in by_goal.items():
            lines.append(f"<b>🎯 {esc(g)}</b>\n" + "\n".join(
                f"{ZONES.get(t.get('quadrant'), ZONES[None])[0]} {esc(t['title'])}"
                + (f" <i>⏱{t['duration_min']}м</i>" if t.get("duration_min") else "")
                + (" <i>(уже есть)</i>" if t["kind"] == "existing" else "") for t in ts))
        if b.get("comment"):
            lines.append(f"<i>{esc(b['comment'])}</i>")
        lines.append("Добавь, убери или поправь задачи. Потом «Далее», и я раскидаю их по неделе.")
        return "\n\n".join(lines) + got
    if step == 4:
        c = d["c"]
        return head + week_text(c, esc) + "\n\nПоправь расстановку («лендинг перенеси на среду утро»), потом «Готово». " \
            "Задачи появятся только после «Готово»." + got
    return head


def week_text(c: dict, esc) -> str:
    icons = {"context": "📌", "event": "🎭"}
    days: dict[str, list] = {}
    for it in c["items"]:
        days.setdefault(it.get("date") or "", []).append(it)
    lines = []
    for day in sorted(k for k in days if k):
        dt = datetime.fromisoformat(day)
        items = sorted(days[day], key=lambda i: i.get("time") or "99")
        lines.append(f"<b>{WD[dt.weekday()]} {dt:%d.%m}</b>")
        for it in items:
            icon = icons.get(it["kind"]) or ZONES.get(it.get("quadrant"), ZONES[None])[0]
            tm = f"{it['time']} " if it.get("time") else ""
            dur = f" <i>⏱{it['duration_min']}м</i>" if it.get("duration_min") and it["kind"] != "context" else ""
            lines.append(f"{icon} {tm}{esc(it['title'])}{dur}")
    if days.get(""):
        lines.append("<b>Без даты</b>\n" + "\n".join(f"• {esc(i['title'])}" for i in days[""]))
    load = c["load"]
    lines.append(f"\n⚖️ Нагрузка: {load['planned_hours']:g} ч из {load['budget_hours']:g} ч. {esc(load['verdict'])}")
    if c.get("comment"):
        lines.append(f"<i>{esc(c['comment'])}</i>")
    return "\n".join(lines)
