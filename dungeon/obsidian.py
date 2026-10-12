"""Еженедельный экспорт итогов в Obsidian: одна заметка на неделю + хаб-журнал."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from . import config
from .game import ZONES

log = logging.getLogger(__name__)
HUB = "Хроники подземелья — журнал недель"
FOLDER = "Хроники подземелья"


def week_title(now: datetime) -> str:
    y, w, _ = now.isocalendar()
    return f"Хроники подземелья — неделя {w}, {y}"


def export_week(stats: dict, hero: dict, now: datetime, base: Path | None = None) -> Path | None:
    base = base or config.OBSIDIAN_DIR
    if not base.exists():
        log.warning("obsidian: нет папки %s", base)
        return None
    title = week_title(now)
    folder = base / FOLDER
    folder.mkdir(exist_ok=True)
    zones = "\n".join(f"- {ZONES[q][0]} {ZONES[q][1]}: {n}" for q, n in stats["by_q"].items() if n) or "- ничего"
    streaks = "\n".join(f"- {s['title']}: {s['current']} дн. (рекорд {s['best']})"
                        for s in stats["streaks"]) or "- нет активных"
    ach = "\n".join(f"- {a.icon} {a.name}: {a.desc}" for a in stats["achievements"]) or "- без новых"
    body = f"""# {title}

Итоги недели из Dungeon Tasks, сгенерировано {now:%d.%m.%Y %H:%M}.

## Герой
- Уровень {hero['level']}, {hero['title']}
- HP {hero['hp']}/{hero['max_hp']}, золото {hero['gold']}, всего XP {hero['xp']}

## Добыча
- Закрыто задач: {stats['done']}
- Опыт за неделю: +{stats['xp']} XP
- Фокус-рейдов: {stats['focus']}
- Пропущено ритуалов: {stats['missed']}
- Босс недели: {"повержен" if stats['boss'] else "не повержен"}

## По зонам
{zones}

## Стрики ритуалов
{streaks}

## Новые ачивки
{ach}

## Связанные заметки
- [[{HUB}]]
- [[Раздел — Дисциплина и привычки]]
- [[Раздел — Тайм-менеджмент и планирование]]
"""
    path = folder / f"{title}.md"
    path.write_text(body, encoding="utf-8")
    _link_in_hub(base, title)
    return path


def _link_in_hub(base: Path, title: str) -> None:
    hub = base / f"{HUB}.md"
    if not hub.exists():
        hub.write_text(
            f"# {HUB}\n\nЕженедельные итоги из RPG таск-менеджера Dungeon Tasks: закрытые задачи по зонам "
            "матрицы Эйзенхауэра, стрики ритуалов, ачивки.\n\n## Недели\n\n"
            "## Связанные заметки\n- [[Продуктивность и привычки — карта заметок]]\n"
            "- [[Раздел — Дисциплина и привычки]]\n- [[Как выработать систему планирования дел]]\n",
            encoding="utf-8")
    text = hub.read_text(encoding="utf-8")
    link = f"- [[{title}]]"
    if link in text:
        return
    marker = "## Недели\n"
    if marker in text:
        head, tail = text.split(marker, 1)
        text = head + marker + link + "\n" + tail  # свежая неделя сверху
    else:
        text += f"\n{link}\n"
    hub.write_text(text, encoding="utf-8")


def export_plan(session: dict, now: datetime, week_start: datetime, base: Path | None = None) -> Path | None:
    """Заметка недели: итоги прошлой, цели, контекст, расстановка по дням."""
    base = base or config.OBSIDIAN_DIR
    if not base.exists():
        return None
    y, w, _ = week_start.isocalendar()
    title = f"План недели {w}, {y}"
    a, b, c = (session["drafts"].get(k, {}) for k in "abc")
    wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
    icon = {"done": "✅", "partial": "🟡", "missed": "❌"}
    prev = "\n".join(f"- {icon.get(g['status'], '')} {g['goal']}" + (f": {g['note']}" if g.get("note") else "")
                     for g in a.get("prev_goals", [])) or "- нет данных"
    goals = "\n".join(f"- {g['goal']} (как поймём: {g.get('measure', '')})" for g in b.get("goals", [])) or "- нет"
    days: dict[str, list] = {}
    for it in c.get("items", []):
        days.setdefault(it.get("date") or "", []).append(it)
    sched = []
    for day in sorted(k for k in days if k):
        dt = datetime.fromisoformat(day)
        sched.append(f"### {wd[dt.weekday()]} {dt:%d.%m}")
        for it in sorted(days[day], key=lambda i: i.get("time") or "99"):
            mark = {"context": "📌", "event": "🎭"}.get(it["kind"], "- [ ]")
            dur = f" ({it['duration_min']} мин)" if it.get("duration_min") and it["kind"] != "context" else ""
            sched.append(f"{mark} {it.get('time') or ''} {it['title']}{dur}".replace("  ", " "))
    load = c.get("load", {})
    body = f"""# {title}

Спланировано с недельным коучем Dungeon Tasks {now:%d.%m.%Y %H:%M}. Долгосрочные цели: [[Мои цели]].

## Прошлая неделя
{a.get('retro', '')}

## Цели прошлых недель
{prev}

## Цели этой недели
{goals}

## Неделя по дням
{chr(10).join(sched) or 'пусто'}

## Нагрузка
- {load.get('planned_hours', 0):g} ч из {load.get('budget_hours', 0):g} ч. {load.get('verdict', '')}

## Связанные заметки
- [[{HUB}]]
- [[Мои цели]]
- [[Планировать действие, а не абстрактную цель]]
"""
    folder = base / FOLDER
    folder.mkdir(exist_ok=True)
    path = folder / f"{title}.md"
    path.write_text(body, encoding="utf-8")
    _link_in_hub(base, title)
    return path
