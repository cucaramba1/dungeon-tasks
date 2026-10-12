"""Планирование без Claude: запасной путь, когда лимит подписки кончился или CLI не отвечает.

Отдаёт ответы тех же форм, что и схемы planner.SCHEMA_A/B/C, только собранные правилами:
контекст разбирает наш парсер фраз, цели переносятся или берутся из сообщений Некита,
задачи раскладываются по дням жадно под бюджет часов.
"""
from __future__ import annotations

import re
from datetime import datetime

from .parser import parse

NOTE = "⚠️ Claude сейчас недоступен, этот шаг собран без него, по правилам."


def _lines(texts: list[str]) -> list[str]:
    """Сообщения -> отдельные пункты: по строкам, «;» и точкам между предложениями."""
    out = []
    for t in texts:
        for part in re.split(r"[\n;]+|(?<=[.!?])\s+(?=[А-ЯA-Z0-9])", t):
            part = part.strip(" -•.\t")
            if len(part) >= 3:
                out.append(part)
    return out


def _item(p, kind: str) -> dict:
    d = {"title": p.title, "kind": kind}
    if p.due_at:
        d["date"] = p.due_at.date().isoformat()
        if p.has_time:
            d["time"] = f"{p.due_at:%H:%M}"
    if p.duration_min:
        d["duration_min"] = p.duration_min
    if p.rrule:
        d["rrule"] = p.rrule
    if p.is_habit:
        d["is_habit"] = True
    if p.quadrant:
        d["quadrant"] = p.quadrant
    return d


def step_a(data: dict, now: datetime) -> dict:
    events, rest = [], []
    for line in _lines(data["inputs"]["context"]):
        p = parse(line, now)
        if p.due_at:
            events.append(_item(p, "context"))
        else:
            rest.append(line)
    lw = data["last_week"]
    done = sum(lw.get("done_by_zone", {}).values())
    retro = (f"Закрыто задач: {done}, пропущено ритуалов: {len(lw.get('missed_habits', []))}, "
             f"фокус-рейдов: {lw.get('focus_sessions', 0)}.")
    prev = [g for w in data.get("previous_week_goals", [])[:1] for g in w.get("goals", [])]
    return {
        "context_events": events,
        "retro": retro,
        "prev_goals": [{"goal": g["goal"], "status": "partial", "note": "статус проверь сам"} for g in prev],
        "goals": prev,
        "questions": ([f"Без даты не понял: {'; '.join(rest[:5])}. Если это дела на неделю, допиши день."] if rest else [])
        + ([] if prev else ["Напиши 1-3 цели на неделю, каждую отдельной строкой."]),
        "offline": True,
    }


def step_b(data: dict, now: datetime) -> dict:
    a = data["drafts"]["a"]
    goal_lines = _lines(data["inputs"]["goals"])
    goals = [{"goal": g, "measure": "сам решишь в конце недели"} for g in goal_lines] or a.get("goals", [])
    tasks = [{"title": t["title"], "kind": "existing", "existing_id": t["id"], "quadrant": t.get("quadrant"),
              "duration_min": t.get("duration_min"), "goal": ""} for t in data["open_tasks"]]
    return {"context_events": a.get("context_events", []), "goals": goals, "tasks": tasks,
            "comment": "Новые задачи впиши сообщениями, по одной на строку: «лендинг ~2ч», «монтаж видео в среду».",
            "offline": True}


def step_c(data: dict, now: datetime) -> dict:
    b = data["drafts"]["b"]
    new = [_item(parse(line, now), "task") for line in _lines(data["inputs"]["tasks"])]
    return schedule(b.get("context_events", []), b.get("tasks", []) + new, data)


def step_d(data: dict, now: datetime) -> dict:
    c = data["drafts"]["c"]
    extra = [_item(parse(line, now), "task") for line in _lines(data["inputs"]["schedule"])]
    fixed = [i for i in c["items"] if i["kind"] == "context"]
    rest = [i for i in c["items"] if i["kind"] != "context"]
    return schedule(fixed, rest + extra, data)


def schedule(context: list[dict], tasks: list[dict], data: dict) -> dict:
    """Жадно: задачи с датой остаются где есть, без даты идут в самый свободный день под бюджет."""
    days = [d.split()[0] for d in data["week"]["days"]]
    rule = data["week"]["budget_rule"]  # «будни 4 ч/день, выходные 2 ч/день»
    nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)\s*ч", rule)] or [4.0, 2.0]
    cap = {d: (nums[-1] if datetime.fromisoformat(d).weekday() >= 5 else nums[0]) * 60 for d in days}
    used = {d: 0 for d in days}
    items = [dict(c, kind="context") for c in context]
    for t in tasks:
        t = dict(t)
        mins = t.get("duration_min") or 60
        if not t.get("date") or t["date"] not in used:
            free = sorted(days, key=lambda d: (used[d] + mins > cap[d], used[d] / max(cap[d], 1)))
            t["date"] = free[0] if free else None
        if t.get("date") in used:
            used[t["date"]] += mins
        items.append(t)
    planned = sum(used.values()) / 60
    budget = data["week"]["budget_hours"]
    verdict = "Влезает." if planned <= budget else f"Перебор на {planned - budget:.1f} ч, что-то стоит выкинуть."
    return {"items": items, "load": {"planned_hours": round(planned, 1), "budget_hours": budget, "verdict": verdict},
            "comment": "Расставил без Claude: задачи без даты разложены по самым свободным дням. Поправь сообщениями.",
            "offline": True}
