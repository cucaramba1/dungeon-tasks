"""Сценарии с побочными эффектами в Telegram: планирование недели, «Свободен сейчас?», афиша.
Общие для бота (кнопки, команды) и тикера (расписание)."""
from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from html import escape

from . import coach, keyboards, leisure, obsidian, texts, tgmcp
from .app import App
from .db import parse_iso
from .events import human
from .parser import Parsed

log = logging.getLogger(__name__)


# ---------- планирование недели ----------

async def plan_show(app: App, sid: int) -> None:
    s = app.planner.get(sid)
    await app.send(_trim(app, s), keyboards.plan(sid, s["step"]), topic="plan")


def _trim(app: App, s: dict) -> str:
    from .planner import render
    text = render(s, escape)
    return text if len(text) < 4000 else text[:3950] + "\n…"


async def plan_start(app: App) -> None:
    s = app.planner.start()
    app.db.set("plan_started", s["week"])
    await plan_show(app, s["id"])


async def plan_next(app: App, sid: int) -> str:
    """«Далее» / «Готово». Возвращает короткий ответ для всплывашки кнопки."""
    s = app.planner.get(sid)
    if not s or s["status"] != "active":
        return "Эта сессия уже закрыта"
    if app.plan_busy:
        return "Коуч ещё думает…"
    if s["step"] == 1 and not s["inputs"].get("1"):
        return "Сначала расскажи про неделю: хоть одно сообщение"
    app.plan_busy = True
    try:
        await app.send("⏳ Коуч думает над следующим шагом, до пары минут…", silent=True, topic="plan")
        if s["step"] < 4:
            await app.planner.advance(sid)
            await plan_show(app, sid)
            return "Дальше"
        s, created = await app.planner.finish(sid)
        for t in created:
            await app.ins.sync(t)
        path = obsidian.export_plan(s, app.svc.clock(), coach.plan_start(app.svc.clock()))
        tail = f"\n📖 В Obsidian: <i>{escape(path.stem)}</i>" if path else ""
        await app.send(f"✅ Неделя спланирована: {len(created)} задач и встреч в подземелье.{tail}\n"
                       "Посмотреть: /week", topic="plan")
        return "Готово"
    except Exception as e:
        log.exception("planner step failed")
        await app.send(f"🧙 Коуч споткнулся: {escape(str(e)[:300])}\nЖми кнопку ещё раз.", topic="plan")
        return "Ошибка"
    finally:
        app.plan_busy = False


async def plan_input(app: App, text: str) -> int:
    return app.planner.add_input(text)


# ---------- «Свободен сейчас?» ----------

async def free_ask(app: App) -> None:
    leisure.mark_asked(app.db, app.svc.clock())
    await app.send("🧭 Свободен сейчас?", keyboards.free_ask(), topic="events")


async def free_no(app: App) -> str:
    now = app.svc.clock()
    t = leisure.next_up(app.svc.open_tasks(), now)
    if not t:
        return "Ок, не отвлекаю. По расписанию сегодня больше ничего. Освободишься, жми /free"
    due = parse_iso(t["due_at"]).astimezone(now.tzinfo)
    icon = "🕯" if t["is_habit"] else "⏰"
    return (f"Ок, не отвлекаю. Не забудь: {icon} <b>{escape(t['title'])}</b> в {due:%H:%M} "
            f"(через {leisure.in_human(due - now)}).")


async def free_yes(app: App) -> None:
    now = app.svc.clock()
    end = now.replace(hour=23, minute=59)
    evs = app.events.upcoming(now + timedelta(minutes=30), end, limit=3)
    await app.send("🙌 Тогда вот варианты. Бери, что ляжет:", topic="events")
    if evs:
        ev = evs[0]
        await app.send(escape(human(ev, now.tzinfo)), keyboards.event(ev["id"], ev["link"]), topic="events")
    for kind in random.sample(["film", "series"], 1) + ["book"]:
        item = app.leisure.pick(kind)
        if item:
            await app.send(f"{leisure.KINDS[kind]}: <b>{escape(item['title'])}</b>\n<i>{escape(item['desc'])}</i>",
                           keyboards.leisure(item["rid"], kind), topic="events")
    lib = [t for t in app.svc.open_tasks() if t["quadrant"] == 2 and not t["due_at"] and not t["is_habit"]]
    if lib:
        t = random.choice(lib)
        await app.send(f"📜 Или продвинь важное: <b>{escape(t['title'])}</b>", keyboards.focus(t["id"]), topic="events")


async def event_to_task(app: App, event_id: int) -> str:
    ev = app.events.get(event_id)
    if not ev:
        return "Событие не найдено"
    start = parse_iso(ev["starts_at"]).astimezone(app.svc.clock().tzinfo)
    t = app.svc.create_parsed(Parsed(title=ev["title"][:120], quadrant=4, due_at=start,
                                     has_time=bool(ev["has_time"])), "afisha")
    app.db.x("UPDATE tasks SET notes=? WHERE id=?", (ev["link"], t["id"]))
    app.events.vote(event_id, up=True)
    await app.ins.sync(app.svc.get(t["id"]))
    return f"➕ В плане: {texts.when(app.svc.get(t['id']), app.svc.clock())}"


# ---------- афиша ----------

async def _heal_tgmcp(app: App, dead: bool) -> None:
    """Два мёртвых раунда подряд — перезапускаем telegram-mcp, но не чаще раза в 6 часов."""
    n = int(app.db.get("ev_dead_rounds", "0") or 0) + 1 if dead else 0
    app.db.set("ev_dead_rounds", n)
    last = app.db.get("tgmcp_restarted_at")
    now = datetime.now().astimezone()
    if n >= 2 and (not last or now - datetime.fromisoformat(last) > timedelta(hours=6)):
        app.db.set("tgmcp_restarted_at", now.isoformat())
        log.warning("afisha: telegram-mcp не отвечает %s раунда, перезапускаю службу", n)
        tgmcp.restart_service()


async def collect_events(app: App) -> dict:
    if app.events_running:
        return {}
    app.events_running = True
    try:
        stats = await app.events.collect(app.svc.clock().tzinfo)
        app.db.set("ev_last_run", datetime.now().astimezone().isoformat())
        log.info("afisha: %s", stats)
        await _heal_tgmcp(app, dead=stats.get("aborted") or (stats["errors"] and not stats["posts"]))
        return stats
    except Exception:
        log.exception("afisha collect failed")
        return {}
    finally:
        app.events_running = False
