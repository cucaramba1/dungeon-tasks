"""Режим «застрял»: 7 вопросов против прокрастинации, на выходе первый шаг до 2 минут.
Без LLM, состояние в kv (одна активная сессия)."""
from __future__ import annotations

import json
import re

from .db import DB
from .parser import Parsed

KEY = "stuck_session"
MAX_STEP_MIN = 2

QUESTIONS = [
    "Какое дело ты сейчас откладываешь?",
    "Опиши его в двух словах.",
    "Что в нём кажется неприятным или трудным?",
    "Что хорошего даст его выполнение?",
    "Какой самый простой шаг можно сделать прямо сейчас?",
    "Сколько минут займёт этот шаг? Число.",
    "Чем поощришь себя после?",
]


def active(db: DB) -> dict | None:
    raw = db.get(KEY)
    return json.loads(raw) if raw else None


def _save(db: DB, s: dict | None) -> None:
    db.set(KEY, json.dumps(s, ensure_ascii=False) if s else "")


def cancel(db: DB) -> bool:
    was = active(db) is not None
    _save(db, None)
    return was


def start(db: DB, task: dict | None = None) -> str:
    """Начать сессию. Если задача известна, вопрос 1 пропускаем."""
    s = {"step": 0, "answers": [], "tid": None}
    if task:
        s.update(tid=task["id"], quadrant=task["quadrant"], answers=[task["title"]], step=1)
    _save(db, s)
    return _ask(s)


def _ask(s: dict) -> str:
    n = s["step"]
    intro = "🧱 <b>Застрял.</b> 7 коротких вопросов, отвечай как есть. Выйти: /cancel\n\n" if n == 0 else ""
    return f"{intro}({n + 1}/{len(QUESTIONS)}) {QUESTIONS[n]}"


def answer(db: DB, text: str) -> tuple[str, dict | None]:
    """Принять ответ. Возвращает (что показать, итог или None). Итог: {title, minutes, reward, tid, quadrant, ...}."""
    s = active(db)
    text = text.strip()
    if s["step"] == 5:
        m = re.search(r"\d+", text)
        mins = int(m.group()) if m else None
        if mins is None:
            return "Нужно число минут, например 2.", None
        if mins > MAX_STEP_MIN:
            s["step"] = 4
            s["answers"] = s["answers"][:4]
            _save(db, s)
            return f"{mins} мин многовато. Режь до {MAX_STEP_MIN} минут.\n\n" + _ask(s), None
        text = str(mins)
    s["answers"].append(text)
    s["step"] += 1
    if s["step"] < len(QUESTIONS):
        _save(db, s)
        return _ask(s), None
    _save(db, None)
    a = s["answers"]
    return "", {"task": a[0], "hard": a[2], "gain": a[3], "step": a[4], "minutes": int(a[5]), "reward": a[6],
                "tid": s.get("tid"), "quadrant": s.get("quadrant") or 2}


def to_parsed(res: dict) -> Parsed:
    return Parsed(title=f"Первый шаг: {res['step']}"[:120], quadrant=res["quadrant"], duration_min=res["minutes"])


def summary(res: dict) -> str:
    return (f"✅ План против залипания.\n<b>Первый шаг ({res['minutes']} мин):</b> {res['step']}\n"
            f"Зачем: {res['gain']}\nПосле: {res['reward']}")
