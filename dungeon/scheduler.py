"""Тикер раз в 15 секунд. Всё расписание — это состояние в БД, поэтому после сна
ноута тикер просто догоняет пропущенное: без cron и без потерь."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta
from html import escape

from . import (
    caldav_sync,
    coach,
    config,
    flows,
    keyboards,
    leisure,
    mac,
    obsidian,
    texts,
)
from .app import App
from .bot import send_triage
from .db import iso, parse_iso
from .services import hm

log = logging.getLogger(__name__)
TICK = 15
LATE = timedelta(minutes=10)
CALENDAR_BATCH = 20


def in_quiet(now: datetime, frm: str, to: str) -> bool:
    a, b, t = hm(frm), hm(to), now.time()
    return (a <= t or t < b) if a > b else (a <= t < b)


def quiet_end(now: datetime, to: str) -> datetime:
    end = datetime.combine(now.date(), hm(to), now.tzinfo)
    return end if end > now else end + timedelta(days=1)


async def run(app: App) -> None:
    while True:
        try:
            await tick(app)
        except Exception:
            log.exception("tick failed")
        await asyncio.sleep(TICK)


async def tick(app: App) -> None:
    time.tzset()  # переехал в другой часовой пояс — подхватываем без рестарта
    svc, db = app.svc, app.db
    now = svc.clock()
    today = now.date().isoformat()

    # 1. смена суток: штрафы за ритуалы, бэкап
    if db.get("last_day") != today:
        missed, msgs = svc.rollover()
        db.set("last_day", today)
        db.backup()
        if missed:
            names = ", ".join(escape(t["title"]) for t in missed)
            await app.send(f"💀 Пропущены ритуалы: {names}. −10 HP за каждый, стрики сгорели.\n"
                           + "\n".join(msgs))

    # 2. ноут не спит — снимаем страховку, бот напомнит сам
    await app.ins.disarm_due()

    if not app.ready:
        return  # бот ещё не подключён: напоминания и сводки подождут, ничего не теряем

    # 3. напоминания
    due = db.q("SELECT * FROM tasks WHERE status='open' AND next_nag_at IS NOT NULL AND next_nag_at<=? "
               "ORDER BY next_nag_at", (iso(now),))
    for t in due:
        if t["is_habit"]:
            await nag(app, t, now)
        else:
            late = now - parse_iso(t["next_nag_at"]) > LATE
            prefix = "⏳ <i>Пропущено, пока ноут спал</i>\n" if late else ""
            await app.send(f"{prefix}⏰ <b>{escape(t['title'])}</b>", keyboards.remind(t))
            db.x("UPDATE tasks SET next_nag_at=NULL, nag_count=nag_count+1 WHERE id=?", (t["id"],))

    # 4. фокус-рейды
    for f, xp, msgs in svc.focus_finish_due():
        brk = db.get("break_min")
        await app.send(f"🏁 Рейд окончен! +{xp} XP. Отдых {brk} мин, потом снова в бой.",
                       keyboards.focus(f["task_id"]))
        await app.announce(msgs)
        if db.get("mac_popup") == "1":
            mac.popup("Dungeon Tasks", f"Рейд окончен! +{xp} XP. Отдых {brk} минут.", loud=True)

    # 4.5 Apple Calendar: разгребаем очередь отложенной синхронизации
    if caldav_sync.enabled():
        await process_calendar_outbox(app)

    # 5. утро: сводка + чек-лист, как только сел за ноут (или в morning, если ноут и так открыт)
    morning, evening = hm(db.get("morning")), hm(db.get("evening"))
    if db.get("last_morning") != today and hm(db.get("morning_from")) <= now.time() < evening:
        active = mac.idle_seconds() < 120
        if active or now.time() >= morning:
            db.set("last_morning", today)
            await app.send(texts.morning(svc))
            await send_overdue_check(app, svc, now)
            await send_missed_followup(app, svc)
            await send_checklist(app, "morning", open_mac=active)

    # 6. вечер: сводка + разбор сундука, чек-лист — когда план на завтра готов
    if db.get("last_evening") != today and now.time() >= evening:
        db.set("last_evening", today)
        await app.send(texts.evening(svc))
        n = await send_triage(app)
        await app.announce(svc.game.inbox_check(n == 0))
        if n:
            await app.send("Когда раскидаешь сундук и проверишь завтра, жми:", keyboards.plan_ready())
        db.set("evening_cl_pending", today)
    if db.get("evening_cl_pending") == today and (
            not svc.inbox() or db.get("plan_ready") == today or now.time() >= hm(db.get("evening_cl_fallback"))):
        db.set("evening_cl_pending", "")
        await send_checklist(app, "evening", open_mac=mac.idle_seconds() < 300)

    # 7. воскресенье: хроника недели, потом коуч
    if (db.get("last_weekly") != today and now.weekday() == int(db.get("weekly_day"))
            and now.time() >= hm(db.get("weekly_time"))):
        db.set("last_weekly", today)
        await send_weekly(app)
    # 8. планирование недели: брошенное закрываем, вс в coach_time начинаем, в пн напоминаем или догоняем
    if stale := app.planner.expire_stale():
        n = sum(len(v) for v in stale["inputs"].values())
        await app.send(f"🧙 Планирование недели {stale['week']} висело больше полутора суток, закрыл его. "
                       f"Твои {n} сообщ. сохранены. Сообщения боту снова становятся задачами. Начать заново: /plan")
    key = coach.week_key(now)
    coach_day = int(db.get("coach_day"))
    sunday = now.weekday() == coach_day and now.time() >= hm(db.get("coach_time"))
    monday = now.weekday() == (coach_day + 1) % 7 and now.time() >= hm("10:00")
    if (sunday or monday) and db.get("plan_started") != key and not app.planner.active():
        await flows.plan_start(app)
    elif monday and db.get("plan_nudged") != key and (s := app.planner.active()) and s["week"] == key:
        db.set("plan_nudged", key)
        await app.send("🧙 Планирование недели ждёт. Продолжим с того же шага:")
        await flows.plan_show(app, s["id"])

    # 9. афиша: раз в час читаем каналы (фоном, тикер не ждёт)
    last = db.get("ev_last_run")
    every = timedelta(minutes=int(db.get("events_every_min")))
    if not app.events_running and (not last or now - datetime.fromisoformat(last) >= every):
        db.set("ev_last_run", now.isoformat())
        app.spawn(flows.collect_events(app))

    # 10. «Свободен сейчас?»
    if not app.planner.active() and leisure.should_ask(db, svc.open_tasks(), now, bool(svc.focus_current())):
        await flows.free_ask(app)


async def send_overdue_check(app: App, svc, now: datetime) -> None:
    for t in svc.overdue_open():
        await app.send(texts.overdue_check(t, now), keyboards.overdue_check(t))


async def send_missed_followup(app: App, svc) -> None:
    for t in svc.missed_pending():
        await app.send(texts.missed_followup(t), keyboards.missed_followup(t))


async def send_checklist(app: App, kind: str, open_mac: bool) -> None:
    state = app.svc.checklist_state(kind)
    if not state:
        return
    await app.send(texts.checklist(kind, state), keyboards.checklist(kind, state))
    if open_mac and config.WEB_KEY:
        mac.open_url(f"http://127.0.0.1:{config.WEB_PORT}/?key={config.WEB_KEY}#checklist")


async def nag(app: App, t, now: datetime) -> None:
    db = app.db
    if t["nag_count"] > 0 and in_quiet(now, db.get("quiet_from"), db.get("quiet_to")):
        db.x("UPDATE tasks SET next_nag_at=? WHERE id=?", (iso(quiet_end(now, db.get("quiet_to"))), t["id"]))
        await app.changed(t["id"])
        return
    n = t["nag_count"]
    level = 1 if n == 0 else 2 if n < 3 else 3
    phrase, emoji = texts.nag(t, level)
    stickers = db.q("SELECT file_id FROM stickers")
    if stickers and level >= 2 and app.bot and app.owner:
        try:
            await app.bot.send_sticker(app.owner, random.choice(stickers)["file_id"])
        except Exception:
            await app.send(emoji)
    else:
        await app.send(emoji)
    head = "🕯 <b>РИТУАЛ</b>" if level < 3 else "🚨🕯 <b>РИТУАЛ НЕ СДЕЛАН</b> 🕯🚨"
    await app.send(f"{head}\n{escape(phrase)}", keyboards.remind(t))
    if db.get("mac_popup") == "1":
        loop = asyncio.get_running_loop()

        def clicked(button: str, tid=t["id"]):
            action = {"✅ Сделал": popup_done, "⏰ Через 15 мин": popup_snooze}.get(button)
            if action:
                asyncio.run_coroutine_threadsafe(action(app, tid), loop)

        mac.popup("🕯 Ритуал: " + t["title"], phrase, loud=True,
                  buttons=["⏰ Через 15 мин", "✅ Сделал"], on_click=clicked)
    gap = int(db.get("nag_first") if n < 3 else db.get("nag_later"))
    db.x("UPDATE tasks SET next_nag_at=?, nag_count=nag_count+1 WHERE id=?",
         (iso(now + timedelta(minutes=gap)), t["id"]))
    await app.changed(t["id"])  # страховка переедет на следующий повтор


async def popup_done(app: App, tid: int) -> None:
    t = app.svc.get(tid)
    if not t or t["status"] != "open":
        return
    xp, msgs = await app.done(tid, notify_tg=False)
    await app.send(f"✅ {escape(t['title'])}: закрыто с мака, +{xp} XP", silent=True)
    await app.announce(msgs)


async def popup_snooze(app: App, tid: int) -> None:
    if app.svc.get(tid):
        app.svc.snooze(tid, minutes=15)
        await app.changed(tid)


async def process_calendar_outbox(app: App) -> None:
    """Отправляет накопленные изменения в Apple Calendar. Сетевой вызов — в отдельном потоке,
    чтобы медленный/недоступный iCloud не подвешивал тикер и бота. Ошибка — выходим из тика,
    очередь останется и попробуем снова через 15 сек."""
    db = app.db
    for row in db.q("SELECT * FROM calendar_outbox ORDER BY id LIMIT ?", (CALENDAR_BATCH,)):
        try:
            await asyncio.to_thread(_sync_one, db, row)
        except Exception:
            log.exception("calendar sync failed, outbox id=%s task_id=%s", row["id"], row["task_id"])
            return
        db.x("DELETE FROM calendar_outbox WHERE id=?", (row["id"],))


def _sync_one(db, row) -> None:
    t = db.one("SELECT * FROM tasks WHERE id=?", (row["task_id"],))
    if not t:
        return
    if row["action"] == "delete":
        series = db.one("SELECT uid FROM calendar_series WHERE series_id=?", (t["series_id"],)) if t["series_id"] else None
        uid = series["uid"] if series else t["caldav_uid"]
        if not uid:
            return
        caldav_sync.delete(uid)
        if series:
            db.x("DELETE FROM calendar_series WHERE series_id=?", (t["series_id"],))
        else:
            db.x("UPDATE tasks SET caldav_uid=NULL WHERE id=?", (t["id"],))
        return

    due = parse_iso(t["due_at"])
    if not due:
        return  # без даты в календаре нечего показывать
    start = due.astimezone()
    if t["rrule"] and t["series_id"]:
        existing = db.one("SELECT uid FROM calendar_series WHERE series_id=?", (t["series_id"],))
        uid = caldav_sync.upsert_series(existing["uid"] if existing else None, t["title"], start,
                                        t["duration_min"], t["rrule"])
        if not existing:
            db.x("INSERT INTO calendar_series(series_id, uid) VALUES(?,?)", (t["series_id"], uid))
    else:
        uid = caldav_sync.upsert_task(t["caldav_uid"], t["title"], start, t["duration_min"])
        if not t["caldav_uid"]:
            db.x("UPDATE tasks SET caldav_uid=? WHERE id=?", (uid, t["id"]))


async def send_weekly(app: App) -> None:
    svc = app.svc
    text, stats = texts.weekly_review(svc)
    cands = [t for t in svc.open_tasks() if t["quadrant"] in (1, 2) and not t["is_habit"]]
    await app.send(text, keyboards.boss_pick(cands) if cands else None)
    try:
        path = obsidian.export_week(stats, svc.game.hero(), svc.clock())
        if path:
            await app.send(f"📖 Хроника недели записана в Obsidian: <i>{escape(path.stem)}</i>", silent=True)
    except Exception:
        log.exception("obsidian export")
