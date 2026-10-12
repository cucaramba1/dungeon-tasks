"""Бот-часть точечных чатов: карточки анонсов, подборки, переносы, кнопки. Данные и решения в rituals.py."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from html import escape

from . import keyboards, rituals
from .db import parse_iso
from .parser import parse

log = logging.getLogger(__name__)
WD = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def fmt(iso_s: str, has_time, tz) -> str:
    d = parse_iso(iso_s).astimezone(tz)
    return f"{WD[d.weekday()]} {d:%d.%m}" + (f" {d:%H:%M}" if has_time else "")


def mode_for(app, row) -> str:
    chat = rituals.chat_cfg(row["chat_id"]) or {}
    if not chat.get("series"):
        return "plain"
    return "occ" if app.rituals.series_exists(chat) else "new_series"


def _overlap(app, row, now: datetime) -> str | None:
    if not row["has_time"]:
        return None
    start = parse_iso(row["starts_at"]).astimezone(now.tzinfo)
    chat = rituals.chat_cfg(row["chat_id"]) or {}
    own = app.rituals.occurrence_near(chat, start)
    for t in app.svc.open_tasks():
        if not t["due_at"] or not t["has_time"] or (own and t["id"] == own["id"]):
            continue
        d = parse_iso(t["due_at"]).astimezone(now.tzinfo)
        if start - timedelta(minutes=60) <= d <= start + timedelta(minutes=90):
            return f"⚠️ Пересекается: {escape(t['title'])} ({d:%H:%M})"
    return None


def card_text(app, row, now: datetime) -> str:
    tz = now.tzinfo
    extra = json.loads(row["extra"] or "{}")
    lines = [f"🕯 <b>{escape(row['title'])}</b>",
             f"{fmt(row['starts_at'], row['has_time'], tz)} · {escape(row['ritual'] or '')}"]
    for key, label in (("book", "Книга"), ("variant", "Формат"), ("place", "Место"), ("description", "О чём")):
        if extra.get(key):
            lines.append(f"{label}: {escape(str(extra[key]))}")
    if row["needs_check"]:
        lines.append("❓ Дата или время неуверенные, проверь по посту")
    if ov := _overlap(app, row, now):
        lines.append(ov)
    lines.append(f"<i>[a{row['id']}]</i>")
    return "\n".join(lines)


async def send_card(app, row, now: datetime, prefix: str = "") -> None:
    msg = await app.send(prefix + card_text(app, row, now),
                         keyboards.ritual_card(row["id"], row["link"], mode_for(app, row)))
    if msg is not None:
        app.rituals.mark_sent(row["id"], now)


def _change_text(row, now: datetime) -> str:
    tz = now.tzinfo
    u = json.loads(row["undo"] or "{}")
    title = escape(u.get("title") or row["title"])
    old = u.get("old")
    was = (fmt(old["starts_at"], old["has_time"], tz) if old and old.get("starts_at")
           else fmt(row["starts_at"], row["has_time"], tz))
    if row["kind"] == "move":
        new = u.get("new") or {"starts_at": row["starts_at"], "has_time": row["has_time"]}
        return f"🔀 Перенёс <b>{title}</b>\n{was} → {fmt(new['starts_at'], new['has_time'], tz)}"
    return f"🚫 Отмена: <b>{title}</b>, {was}\nШтрафа нет, серия идёт дальше."


async def send_change(app, row, now: datetime) -> None:
    if row["status"] == "applied":
        msg = await app.send(_change_text(row, now) + f"\n<a href=\"{row['link']}\">пост</a>",
                             keyboards.ritual_undo(row["id"]))
    elif row["status"] == "ambiguous":
        cands = json.loads(row["extra"] or "{}").get("candidates", [])
        what = "перенесли" if row["kind"] == "move" else "отменили"
        msg = await app.send(f"🤔 В {escape(row['ritual'] or '')} что-то {what}, но я не понял, какое именно занятие.\n"
                             f"<a href=\"{row['link']}\">Пост</a>. Выбери:", keyboards.ritual_pick(row["id"], cands))
    else:  # unresolved
        msg = await app.send(f"🤔 В {escape(row['ritual'] or '')} пост про перенос или отмену, но в плане нет занятия, "
                             f"которое можно двигать. <a href=\"{row['link']}\">Пост</a>")
    if msg is not None:
        app.rituals.mark_sent(row["id"], now)


async def tick(app, now: datetime, quiet: bool) -> None:
    """Каждый тик планировщика: применить переносы, разослать карточки. Тихие часы: в БД меняем, пишем потом."""
    if app.db.get("rituals_enabled") != "1":
        return
    r = app.rituals
    for row in r.new_changes():
        for tid in r.apply_change(row["id"]):
            await app.changed(tid)
    r.expire(now)
    if quiet:
        return
    for row in r.unsent_changes():
        await send_change(app, row, now)
    urgent = timedelta(hours=float(app.db.get("ritual_urgent_h")))
    for row in r.unsent_cards(now, within=urgent):
        await send_card(app, row, now)
    if r.digest_due(now):
        rows = r.unsent_cards(now)[:rituals.DIGEST_CAP]
        if rows:
            await app.send(f"🕯 Анонсы из чатов: {len(rows)}")
        for row in rows:
            await send_card(app, row, now)
    for row in r.reminders(now):
        await send_card(app, row, now, prefix="⏳ Ещё не ответил:\n")
        r.mark_reminded(row["id"], now)


async def collect(app) -> dict:
    """Раз в час: прочитать чаты. Одно уведомление в сутки, если claude недоступен."""
    from . import flows
    if app.rituals_running:
        return {}
    app.rituals_running = True
    try:
        stats = await app.rituals.collect(app.svc.clock().tzinfo)
        log.info("rituals: %s", stats)
        dead = bool(stats.get("aborted") or (stats["errors"] and not stats["posts"] and not stats.get("claude_down")))
        await flows._heal_tgmcp(app, dead=dead)
        today = app.svc.clock().date().isoformat()
        if stats.get("claude_down") and app.db.get("rt_claude_notice") != today:
            app.db.set("rt_claude_notice", today)
            await app.send("⚠️ Не могу разобрать анонсы из чатов: claude недоступен (лимит или сеть). "
                           "Посты не потеряются, разберу, когда оживёт.")
        return stats
    except Exception:
        log.exception("rituals collect failed")
        return {}
    finally:
        app.rituals_running = False


async def handle(app, act: str, row_id: int, arg: str | None) -> tuple[str, str | None]:
    """Кнопки rt:*. Возвращает (ответ, подсказка для клавиатуры или None)."""
    r, now = app.rituals, app.svc.clock()
    if act in ("ok", "mk", "ser"):
        res = r.accept(row_id) if act == "ok" else r.make_ritual(row_id)
        if res["task_id"]:
            await app.changed(res["task_id"])
        return f"✅ {res['text']}", None
    if act == "no":
        r.decline(row_id)
        return "Ок, мимо", None
    if act == "sk":
        return "Какая причина пропуска? От неё зависит штраф.", "reason"
    if act in ("sb", "sg"):
        row = r.feed(row_id)
        msgs, touched = r.skip(row_id, bad=act == "sb")
        for tid in touched:
            await app.changed(tid)
        await app.announce(msgs)
        if act == "sb":
            return f"🚫 Прогул записан: −{app.db.get('skip_penalty_hp')} HP", None
        nxt = app.db.one("SELECT * FROM events_feed WHERE chat_id=? AND kind='announce' AND status='new' "
                         "AND id!=? AND starts_at>? ORDER BY starts_at LIMIT 1",
                         (row["chat_id"], row_id, row["starts_at"]))
        if nxt:
            await send_card(app, nxt, now, prefix="Ближайший анонс этого чата:\n")
        return "📌 Без штрафа, серия идёт дальше", None
    if act == "un":
        for tid in r.undo(row_id):
            await app.changed(tid)
        return "↩ Вернул как было", None
    if act == "pk":
        r.pick_target(row_id, arg)
        for tid in r.apply_change(row_id):
            await app.changed(tid)
        return _change_text(r.feed(row_id), now) + "\n(по твоему выбору)", None
    if act == "fix":
        return "✏ Ответь на карточку новой датой и временем, например «в среду в 19:30»", None
    return "Не знаю такую кнопку", None


async def retime_from_reply(app, row_id: int, text: str) -> str:
    """Ответ на карточку: Некит называет верные дату и время обычным текстом."""
    p = parse(text, app.svc.clock())
    if not p.due_at:
        return "Не понял дату. Пример: «в среду в 19:30» или «20.10 в 11»"
    app.rituals.retime(row_id, p.due_at, p.has_time)
    row = app.rituals.feed(row_id)
    if row["task_id"]:
        await app.changed(row["task_id"])
    return f"✏ Поправил: {fmt(row['starts_at'], row['has_time'], app.svc.clock().tzinfo)}"
