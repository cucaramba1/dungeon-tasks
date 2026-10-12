"""Тексты сообщений (Telegram HTML)."""
from __future__ import annotations

import random
from datetime import datetime, timedelta
from html import escape

from . import config
from .db import iso, parse_iso
from .game import ACH_BY_CODE, ZONES
from .services import Service

WD_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
RRULE_HUMAN = {
    "FREQ=DAILY": "каждый день",
    "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR": "по будням",
    "FREQ=WEEKLY;BYDAY=SA,SU": "по выходным",
}
RU_DAYS = {"MO": "пн", "TU": "вт", "WE": "ср", "TH": "чт", "FR": "пт", "SA": "сб", "SU": "вс"}


def bar(cur: int, total: int, n: int = 10) -> str:
    fill = 0 if total <= 0 else max(0, min(n, round(n * cur / total)))
    return "▰" * fill + "▱" * (n - fill)


def dur(minutes: int | None) -> str:
    if not minutes:
        return ""
    h, m = divmod(int(minutes), 60)
    return f"{h}ч{m:02d}" if h and m else f"{h}ч" if h else f"{m}м"


def human_rrule(r: str | None) -> str:
    if not r:
        return ""
    if r in RRULE_HUMAN:
        return RRULE_HUMAN[r]
    if "INTERVAL=" in r:
        return f"каждые {r.split('INTERVAL=')[1]} дн."
    if "BYDAY=" in r:
        return "по " + ", ".join(RU_DAYS[d] for d in r.split("BYDAY=")[1].split(","))
    return r


def when(t, now: datetime) -> str:
    due = parse_iso(t["due_at"])
    if not due:
        return ""
    due = due.astimezone(now.tzinfo)
    d = (due.date() - now.date()).days
    day = {0: "сегодня", 1: "завтра", -1: "вчера"}.get(d, f"{WD_SHORT[due.weekday()]} {due:%d.%m}")
    return f"{day} {due:%H:%M}" if t["has_time"] else day


def overdue_check(t, now: datetime) -> str:
    return f"❓ Сделал: <b>{escape(t['title'])}</b>?\n<i>Дедлайн был {when(t, now)}</i>"


def missed_followup(t) -> str:
    return f"❌ <s>{escape(t['title'])}</s>  не сделано. Будешь делать?"


def missed_ask_date(t) -> str:
    return (f"✍️ Ответь на это сообщение, когда сделаешь «{escape(t['title'])}» "
            f"(например «завтра в 15» или «пятница»)  <code>#{t['id']}</code>")


def task_line(t, now: datetime, with_day: bool = False) -> str:
    icon = ZONES[t["quadrant"]][0]
    parts = [icon]
    due = parse_iso(t["due_at"])
    if due and with_day:
        parts.append(f"<i>{when(t, now)}</i>")
    elif due and t["has_time"]:
        parts.append(f"<b>{due.astimezone(now.tzinfo):%H:%M}</b>")
    parts.append(escape(t["title"]))
    if t["is_habit"]:
        parts.append("🕯")
    if t["duration_min"]:
        parts.append(f"<i>⏱{dur(t['duration_min'])}</i>")
    if t["rrule"]:
        parts.append("🔁")
    if t["is_boss"]:
        parts.append("🐉")
    return " ".join(parts)


def card(t, now: datetime) -> str:
    icon, zone = ZONES[t["quadrant"]]
    lines = [f"<b>{escape(t['title'])}</b>  <code>#{t['id']}</code>", f"{icon} {zone}"]
    if t["due_at"]:
        lines.append(f"🗓 {when(t, now)}")
    if t["rrule"]:
        lines.append(f"🔁 {human_rrule(t['rrule'])}")
    if t["duration_min"]:
        lines.append(f"⏱ {dur(t['duration_min'])}")
    if t["is_habit"]:
        lines.append("🕯 Ритуал: буду доставать, пока не сделаешь")
    if t["is_boss"]:
        lines.append("🐉 Босс недели")
    if t["quadrant"] is None:
        lines.append("<i>Зона не ясна — выбери кнопкой или разберём вечером.</i>")
    lines.append("<i>Ответь на это сообщение, чтобы поменять срок или название.</i>")
    return "\n".join(lines)


def hero_line(svc: Service) -> str:
    h = svc.game.hero()
    return (f"🧝 <b>Ур. {h['level']}</b> {h['title']}\n"
            f"❤️ {bar(h['hp'], h['max_hp'])} {h['hp']}/{h['max_hp']}\n"
            f"⭐ {bar(h['xp'] - h['cur_at'], h['next_at'] - h['cur_at'])} "
            f"{h['xp'] - h['cur_at']}/{h['next_at'] - h['cur_at']} XP · 🪙 {h['gold']}")


def _zone_blocks(tasks: list, now: datetime, with_day=False) -> list[str]:
    out = []
    hints = {3: "делегируй или закрой за 5 минут", 4: "может, просто выкинуть?"}
    for q in (1, 2, 3, 4, None):
        items = [t for t in tasks if t["quadrant"] == q]
        if not items:
            continue
        icon, zone = ZONES[q]
        head = f"\n<b>{icon} {zone}</b>"
        if q in hints:
            head += f" <i>— {hints[q]}</i>"
        out.append(head)
        out += [task_line(t, now, with_day) for t in items]
    return out


def _sorted(tasks):
    return sorted(tasks, key=lambda t: (t["due_at"] is None or not t["has_time"], t["due_at"] or "", t["id"]))


def day_summary(svc: Service, day: datetime, title: str, include_overdue: bool, show_hero: bool = True) -> str:
    now = svc.clock()
    d = svc.day(day, include_overdue)
    lines = [title, "", hero_line(svc)] if show_hero else [title]
    boss = svc.boss()
    if boss:
        lines.append(f"\n🐉 <b>Босс недели:</b> {escape(boss['title'])}")
    if d["overdue"]:
        lines.append("\n<b>⚠️ Хвосты из прошлого</b>")
        lines += [task_line(t, now, with_day=True) for t in _sorted(d["overdue"])]
    tasks = _sorted(d["dated"] + d["undated_q1"])
    if tasks:
        lines += _zone_blocks(tasks, now)
    else:
        lines.append("\nНа этот день в подземелье тихо. Самое время для библиотеки 📜")
    if d["undated_q2"]:
        lines.append("\n<b>📜 Библиотека ждёт</b> <i>(важное без срока)</i>")
        lines += [task_line(t, now) for t in d["undated_q2"][:3]]
    inbox = svc.inbox()
    if inbox:
        lines.append(f"\n📥 В сундуке у входа {len(inbox)} неразобранных.")
    return "\n".join(lines)


def morning(svc: Service) -> str:
    now = svc.clock()
    phrase = random.choice(config.yaml_file("motivation")["morning"])
    return day_summary(svc, now, f"☀️ <b>{phrase}</b>\n<i>{WD_SHORT[now.weekday()]}, {now:%d.%m}</i>", True)


def evening(svc: Service) -> str:
    now = svc.clock()
    phrase = random.choice(config.yaml_file("motivation")["evening"])
    start = now.replace(hour=0, minute=0, second=0)
    ev = svc.db.q("SELECT * FROM events WHERE at>=?", (iso(start),))
    done = [e for e in ev if e["kind"] == "done"]
    xp = sum(e["xp"] for e in ev)
    missed = svc.db.q("SELECT title FROM tasks WHERE status='open' AND is_habit=1 AND due_at<?",
                      (iso(start + timedelta(days=1)),))
    res = [f"🌙 <b>{phrase}</b>", "", f"<b>Итоги дня:</b> ⚔ {len(done)} закрыто · ⭐ +{xp} XP"]
    if missed:
        res.append("🕯 Ритуалы ещё не сделаны: " + ", ".join(escape(m["title"]) for m in missed)
                   + "\n<i>До полуночи можно успеть, потом −HP.</i>")
    tomorrow = now + timedelta(days=1)
    res.append("")
    res.append(day_summary(svc, tomorrow, f"<b>🗺 Завтра, {WD_SHORT[tomorrow.weekday()]} {tomorrow:%d.%m}</b>",
                           include_overdue=False, show_hero=False))
    return "\n".join(res)


def matrix(svc: Service) -> str:
    now = svc.clock()
    lines = ["<b>🗺 Карта подземелья</b>"]
    lines += _zone_blocks(_sorted(svc.open_tasks()), now, with_day=True) or ["\nПусто. Подземелье зачищено!"]
    return "\n".join(lines)


def week(svc: Service) -> str:
    now = svc.clock()
    tasks = _sorted(svc.week())
    if not tasks:
        return "На неделю ничего не запланировано."
    lines = ["<b>📅 Неделя похода</b>"]
    cur = None
    for t in tasks:
        due = parse_iso(t["due_at"]).astimezone(now.tzinfo)
        key = due.date() if due.date() >= now.date() else now.date()
        if key != cur:
            cur = key
            lines.append(f"\n<b>{WD_SHORT[key.weekday()]} {key:%d.%m}</b>")
        lines.append(task_line(t, now))
    return "\n".join(lines)


def hero(svc: Service) -> str:
    lines = ["<b>🛡 Лист героя</b>", "", hero_line(svc)]
    streaks = svc.db.q("SELECT * FROM streaks WHERE current>0 ORDER BY current DESC LIMIT 8")
    if streaks:
        lines.append("\n<b>🔮 Стрики рун</b>")
        lines += [f"🕯 {escape(s['title'])}: {s['current']} дн. (рекорд {s['best']})" for s in streaks]
    ach = svc.game.achievements()
    got = [a for a in ach if a["unlocked_at"]]
    lines.append(f"\n<b>🏅 Ачивки {len(got)}/{len(ach)}</b>")
    lines += [f"{a['icon']} {a['name']}" for a in got]
    locked = [a for a in ach if not a["unlocked_at"]][:3]
    if locked:
        lines.append("<i>Дальше: " + "; ".join(f"{a['name']} — {a['desc']}" for a in locked) + "</i>")
    return "\n".join(lines)


def weekly_review(svc: Service) -> tuple[str, dict]:
    now = svc.clock()
    since = now - timedelta(days=7)
    ev = svc.db.q("SELECT * FROM events WHERE at>=?", (iso(since),))
    by_q = {q: 0 for q in (1, 2, 3, 4, None)}
    for e in ev:
        if e["kind"] == "done":
            by_q[e["quadrant"]] += 1
    stats = {
        "done": sum(by_q.values()),
        "by_q": by_q,
        "xp": sum(e["xp"] for e in ev),
        "missed": sum(e["kind"] == "missed" for e in ev),
        "focus": sum(e["kind"] == "focus" for e in ev),
        "boss": any(e["kind"] == "boss" for e in ev),
        "achievements": [ACH_BY_CODE[r["code"]] for r in
                         svc.db.q("SELECT code FROM achievements WHERE unlocked_at>=?", (iso(since),))],
        "streaks": [dict(s) for s in svc.db.q("SELECT * FROM streaks WHERE current>0 ORDER BY current DESC")],
        "open_q1": len([t for t in svc.open_tasks() if t["quadrant"] == 1]),
    }
    lines = [f"<b>📖 Хроники недели</b> <i>{since:%d.%m} – {now:%d.%m}</i>", "", hero_line(svc), "",
             f"⚔ Закрыто: <b>{stats['done']}</b> · ⭐ +{stats['xp']} XP · 🛡 рейдов: {stats['focus']}"]
    lines.append(" · ".join(f"{ZONES[q][0]} {n}" for q, n in by_q.items() if n))
    share_q2 = by_q[2] / stats["done"] if stats["done"] else 0
    if stats["done"]:
        if share_q2 >= 0.4:
            lines.append("📜 Отлично: больше трети сил ушло в Эльфийскую библиотеку. Так и растут.")
        elif by_q[1] + by_q[3] > by_q[2] * 2:
            lines.append("🔥 Неделя в режиме пожарного. Запланируй важное заранее, пока не загорелось.")
    if stats["missed"]:
        lines.append(f"🕯 Пропущено ритуалов: {stats['missed']}")
    if stats["streaks"]:
        lines.append("🔮 Стрики: " + ", ".join(f"{escape(s['title'])} {s['current']}" for s in stats["streaks"][:5]))
    if stats["achievements"]:
        lines.append("🏅 Новые ачивки: " + ", ".join(f"{a.icon} {a.name}" for a in stats["achievements"]))
    lines.append("🐉 Босс недели повержен!" if stats["boss"] else "🐉 Босс недели не повержен.")
    lines.append("\n<b>Кого назначим боссом следующей недели?</b>")
    return "\n".join(lines), stats


def nag(t, level: int) -> tuple[str, str]:
    """Текст напоминания ритуала и эмодзи-«стикер» по уровню эскалации 1..3."""
    m = config.yaml_file("motivation")
    phrase = random.choice(m[f"level{level}"]).format(task=t["title"])
    emoji = random.choice(m["emoji"][f"level{level}"])
    return phrase, emoji


CHECKLIST_TITLE = {"morning": "☀️ <b>Утренний чек-лист</b>", "evening": "🌙 <b>Вечерний чек-лист</b>"}


def checklist(kind: str, state: list[dict]) -> str:
    done = sum(i["done"] for i in state)
    tail = "\n<i>Всё закрыто. Красавчик.</i>" if state and done == len(state) else "\n<i>Жми на пункты, чтобы отметить.</i>"
    return f"{CHECKLIST_TITLE[kind]}  {done}/{len(state)}{tail}"
