"""Данные для недельного коуча и вызов Claude.

Единственное место в проекте, где тратится модель: 3-4 вызова `claude -p` в неделю
на шагах планирования (planner.py). Инструменты у модели выключены, она получает готовый
JSON с данными и отвечает строго по схеме. Сама ничего не меняет в задачах.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

from . import config
from .db import iso, parse_iso
from .game import ZONES
from .services import Service
from .texts import weekly_review

log = logging.getLogger(__name__)

SYSTEM = """Ты недельный коуч Некита по тайм-менеджменту, привычкам и рискам. Он соло-предприниматель \
(медиабаинг, SMM, ИИ-продукты), живёт во Вьетнаме, строит личный бренд.
Говори по-братски, на «ты», прямо и конкретно, без воды и мотивационных штампов.
Никогда не используй длинное тире (—): вместо него запятая, двоеточие или точка.
Не упоминай названия полей из JSON (next_week, goals, done_by_zone и т.п.), пиши человеческими словами.
Каждый текстовый раздел: 2-4 коротких предложения.
Опирайся только на переданные данные. Ничего не выдумывай: если данных мало, так и скажи.
Если план не влезает в бюджет часов или не бьётся с целями, говори это прямо и предлагай, что перенести или выкинуть."""

def plan_start(now: datetime) -> datetime:
    """Неделя, которую планируем: в выходные это следующий понедельник, в будни текущий день."""
    days = 7 - now.weekday() if now.weekday() >= 5 else 0
    return (now + timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)


def week_key(now: datetime) -> str:
    y, w, _ = plan_start(now).isocalendar()
    return f"{y}-W{w:02d}"


def _clean(v):
    """Страховка от длинных тире и en dash, даже если модель их вставит."""
    if isinstance(v, str):
        return v.replace(" — ", ", ").replace("—", ", ").replace(" – ", ", ")
    if isinstance(v, list):
        return [_clean(x) for x in v]
    if isinstance(v, dict):
        return {k: _clean(x) for k, x in v.items()}
    return v


def _task(t, tz) -> dict:
    due = parse_iso(t["due_at"])
    d = {"id": t["id"], "title": t["title"], "zone": ZONES[t["quadrant"]][1], "quadrant": t["quadrant"]}
    if due:
        due = due.astimezone(tz)
        d["date"] = due.date().isoformat()
        if t["has_time"]:
            d["time"] = f"{due:%H:%M}"
    for k in ("duration_min", "rrule"):
        if t[k]:
            d[k] = t[k]
    if t["is_habit"]:
        d["habit"] = True
    if t["is_boss"]:
        d["boss"] = True
    return d


def build_context(svc: Service) -> dict:
    now = svc.clock()
    tz = now.tzinfo
    since = now - timedelta(days=7)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = plan_start(now)
    end = start + timedelta(days=7)  # окно: с сегодня (включая остаток выходных) до конца планируемой недели
    db = svc.db
    _, stats = weekly_review(svc)

    done = db.q("SELECT t.* FROM tasks t JOIN events e ON e.task_id=t.id WHERE e.kind='done' AND e.at>=?",
                (iso(since),))
    missed = db.q("SELECT t.* FROM tasks t JOIN events e ON e.task_id=t.id WHERE e.kind='missed' AND e.at>=?",
                  (iso(since),))
    days = [(since + timedelta(days=i)).date().isoformat() for i in range(1, 8)]
    checklists = {}
    for kind in ("morning", "evening"):
        n = len(svc.checklist_items(kind))
        full = 0
        per_item = [0] * n
        for day in days:
            got = {r["idx"] for r in db.q("SELECT idx FROM checklist WHERE day=? AND kind=?", (day, kind))}
            full += n > 0 and len(got) >= n
            for i in got:
                if i < n:
                    per_item[i] += 1
        checklists[kind] = {"full_days": full, "of_days": 7,
                            "items": [{"text": t, "done_days": c} for t, c in zip(svc.checklist_items(kind), per_item)]}

    open_tasks = svc.open_tasks()
    upcoming, backlog, overdue = [], [], []
    for t in open_tasks:
        due = parse_iso(t["due_at"])
        if due and today <= due.astimezone(tz) < end:
            upcoming.append(_task(t, tz))
        elif due and due.astimezone(tz) < today:
            overdue.append(_task(t, tz))
        elif not due and t["quadrant"] in (1, 2):
            backlog.append(_task(t, tz))

    wd, we = float(db.get("hours_weekday")), float(db.get("hours_weekend"))
    ndays = (end - today).days
    budget = sum(we if (today + timedelta(days=i)).weekday() >= 5 else wd for i in range(ndays))
    goals = config.GOALS_NOTE.read_text(encoding="utf-8") if config.GOALS_NOTE.exists() else "Заметки с целями нет."
    h = svc.game.hero()
    return {
        "today": now.date().isoformat(),
        "weekday_today": ["пн", "вт", "ср", "чт", "пт", "сб", "вс"][now.weekday()],
        "goals": goals,
        "hero": {k: h[k] for k in ("level", "title", "hp", "xp", "gold", "best_streak")},
        "last_week": {
            "from": since.date().isoformat(), "to": now.date().isoformat(),
            "done_by_zone": {ZONES[q][1]: n for q, n in stats["by_q"].items() if n},
            "done": [_task(t, tz) for t in done][:60],
            "missed_habits": [t["title"] for t in missed],
            "focus_sessions": stats["focus"],
            "boss_defeated": stats["boss"],
            "streaks": [{"title": s["title"], "current": s["current"], "best": s["best"]} for s in stats["streaks"]],
            "checklists": checklists,
        },
        "next_week": {
            "from": today.date().isoformat(), "to": (end - timedelta(days=1)).date().isoformat(),
            "week_starts": start.date().isoformat(),
            "tasks": upcoming,
            "budget_hours": budget,
            "budget_rule": f"будни {wd:g} ч/день, выходные {we:g} ч/день",
        },
        "overdue": overdue,
        "backlog_important_undated": backlog[:30],
        "inbox_count": len(svc.inbox()),
        "current_boss": _task(svc.boss(), tz) if svc.boss() else None,
    }


class ClaudeUnavailable(RuntimeError):
    """Claude не ответил: лимит подписки, нет сети, CLI упал. Планировщик уходит в офлайн-режим."""

    def __init__(self, msg: str, limit: bool = False):
        super().__init__(msg)
        self.limit = limit


def _explain(out: bytes, err: bytes) -> tuple[str, bool]:
    """Достаём человеческую причину ошибки: claude пишет её в JSON на stdout, а не в stderr."""
    text = ""
    try:
        data = json.loads(out)
        text = str(data.get("result") or data.get("error") or data.get("subtype") or "")
    except Exception:
        text = (out or b"").decode(errors="ignore")[-400:]
    text = text or (err or b"").decode(errors="ignore")[-400:] or "без текста ошибки"
    limit = any(w in text.lower() for w in ("limit", "лимит", "usage", "quota", "rate"))
    return text.strip()[:300], limit


async def ask_claude(instructions: str, data: dict, schema: dict, system: str | None = None) -> dict:
    """Один вызов `claude -p` без инструментов: инструкция + данные JSON -> ответ строго по схеме.
    Модель по умолчанию Sonnet: планированию хватает, а недельный лимит подписки тратится медленнее."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    prompt = instructions + "\n\n```json\n" + payload + "\n```"
    env = {**os.environ, "PATH": f"{os.path.dirname(config.CLAUDE_BIN)}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"}
    args = [config.CLAUDE_BIN, "-p", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--output-format", "json", "--tools", "", "--no-session-persistence",
            "--system-prompt", system or SYSTEM, "--json-schema", json.dumps(schema, ensure_ascii=False)]
    if config.COACH_MODEL:
        args += ["--model", config.COACH_MODEL]
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            cwd=str(config.DATA), env=env)
        out, err = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout=600)
    except (OSError, asyncio.TimeoutError) as e:
        raise ClaudeUnavailable(f"claude не запустился или завис: {e}") from e
    finally:
        if proc is not None and proc.returncode is None:  # таймаут или отмена: не оставляем claude жить
            proc.kill()
            await proc.wait()
    if proc.returncode != 0:
        raise ClaudeUnavailable(*_explain(out, err))
    try:
        result = json.loads(out)
    except json.JSONDecodeError as e:
        raise ClaudeUnavailable("claude вернул не JSON") from e
    if result.get("is_error") or not result.get("structured_output"):
        raise ClaudeUnavailable(*_explain(out, err))
    return _clean(result["structured_output"])
