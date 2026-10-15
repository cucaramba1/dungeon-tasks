"""Инлайн-клавиатуры. callback_data: «действие:id[:арг]»."""
from __future__ import annotations

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .game import ZONES


def _quads(b: InlineKeyboardBuilder, t) -> None:
    for q in (1, 2, 3, 4):
        mark = "•" if t["quadrant"] == q else ""
        b.button(text=f"{mark}{ZONES[q][0]}", callback_data=f"q:{t['id']}:{q}")


def card(t) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _quads(b, t)
    b.button(text="🕯 ритуал" if not t["is_habit"] else "🕯 не ритуал", callback_data=f"h:{t['id']}")
    b.button(text="🐉 босс", callback_data=f"boss:{t['id']}")
    b.button(text="✅", callback_data=f"d:{t['id']}")
    b.button(text="🗑", callback_data=f"x:{t['id']}")
    b.button(text="🧱 Застрял", callback_data=f"st:{t['id']}")
    b.adjust(4, 4, 1)
    return b.as_markup()


def remind(t) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Сделал", callback_data=f"d:{t['id']}")
    b.button(text="⏰ 15м", callback_data=f"s:{t['id']}:15")
    b.button(text="⏰ 1ч", callback_data=f"s:{t['id']}:60")
    b.button(text="➡ Завтра", callback_data=f"tm:{t['id']}")
    if t["rrule"]:
        b.button(text="⏭ Пропуск", callback_data=f"sk:{t['id']}")
    b.button(text="🗑", callback_data=f"x:{t['id']}")
    b.adjust(3, 3)
    return b.as_markup()


def overdue_check(t) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Сделал", callback_data=f"d:{t['id']}")
    b.button(text="❌ Не сделал", callback_data=f"ym:{t['id']}")
    b.button(text="➡ Перенести", callback_data=f"tm:{t['id']}")
    b.adjust(3)
    return b.as_markup()


def missed_followup(t) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="📅 Сегодня", callback_data=f"rs:{t['id']}:today")
    b.button(text="🗓 Другая дата", callback_data=f"rs:{t['id']}:ask")
    b.button(text="🚫 Не нужно", callback_data=f"rs:{t['id']}:drop")
    b.adjust(3)
    return b.as_markup()


def triage(t) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    _quads(b, t)
    b.button(text="✅", callback_data=f"d:{t['id']}")
    b.button(text="🗑", callback_data=f"x:{t['id']}")
    b.adjust(6)
    return b.as_markup()


def boss_pick(tasks) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for t in tasks[:8]:
        b.button(text=f"🐉 {t['title'][:40]}", callback_data=f"boss:{t['id']}")
    b.adjust(1)
    return b.as_markup()


def focus(task_id: int | None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚔ Ещё рейд", callback_data=f"f:{task_id or 0}")
    if task_id:
        b.button(text="✅ Задача сделана", callback_data=f"d:{task_id}")
    return b.as_markup()


def checklist(kind: str, state: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    k = kind[0]  # m | e
    for i in state:
        b.button(text=("✅ " if i["done"] else "▫️ ") + i["text"][:50], callback_data=f"cl:{k}:{i['idx']}")
    b.adjust(1)
    return b.as_markup()


def plan_ready() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ План на завтра готов", callback_data="plan:0")
    return b.as_markup()


def plan(sid: int, step: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if step > 1:
        b.button(text="◀ Назад", callback_data=f"pl:back:{sid}")
    if step < 4:
        b.button(text="Далее ▶", callback_data=f"pl:next:{sid}")
    else:
        b.button(text="✅ Готово", callback_data=f"pl:done:{sid}")
    b.button(text="✖ Отмена", callback_data=f"pl:cancel:{sid}")
    b.adjust(2, 1) if step > 1 else b.adjust(1, 1)
    return b.as_markup()


def free_ask() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🙌 Да, свободен", callback_data="fr:y")
    b.button(text="Нет, занят", callback_data="fr:n")
    return b.as_markup()


def event(event_id: int, url: str | None = None) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    if url:
        b.button(text="📖 Пост", url=url)
    b.button(text="👍", callback_data=f"ev:up:{event_id}")
    b.button(text="👎 Мимо", callback_data=f"ev:dn:{event_id}")
    b.button(text="➕ В план", callback_data=f"ev:add:{event_id}")
    b.adjust(4)
    return b.as_markup()


def leisure(rid: int, kind: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    verb = "Читаю" if kind == "book" else "Смотрю"
    b.button(text=f"▶️ {verb}", callback_data=f"ls:w:{rid}")
    b.button(text="✔️ Уже было", callback_data=f"ls:s:{rid}")
    b.button(text="🔄 Другое", callback_data=f"ls:n:{rid}")
    b.adjust(3)
    return b.as_markup()


def ritual_card(row_id: int, url: str | None, mode: str) -> InlineKeyboardMarkup:
    """Карточка анонса из точечного чата. mode: plain (обычное событие), occ (серия есть), new_series (серии ещё нет)."""
    b = InlineKeyboardBuilder()
    if url:
        b.button(text="📖 Пост", url=url)
    if mode == "occ":
        b.button(text="✅ Подтвердить", callback_data=f"rt:ok:{row_id}")
        b.button(text="❌ Пропускаю", callback_data=f"rt:sk:{row_id}")
    elif mode == "new_series":
        b.button(text="🕯 Завести ритуал", callback_data=f"rt:ser:{row_id}")
        b.button(text="❌ Мимо", callback_data=f"rt:no:{row_id}")
    else:
        b.button(text="✅ Иду", callback_data=f"rt:ok:{row_id}")
        b.button(text="🕯 В ритуал", callback_data=f"rt:mk:{row_id}")
        b.button(text="❌ Мимо", callback_data=f"rt:no:{row_id}")
    b.button(text="✏ Поправить", callback_data=f"rt:fix:{row_id}")
    b.adjust(*([1] if url else []), *([2, 2] if mode == "plain" else [2, 1]))
    return b.as_markup()


def ritual_reason(row_id: int, penalty: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"🚫 Прогул (−{penalty} HP)", callback_data=f"rt:sb:{row_id}")
    b.button(text="📌 Важная причина", callback_data=f"rt:sg:{row_id}")
    b.adjust(1, 1)
    return b.as_markup()


def ritual_undo(row_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="↩ Вернуть", callback_data=f"rt:un:{row_id}")
    return b.as_markup()


def ritual_pick(row_id: int, cands: list[dict]) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for c in cands[:4]:
        b.button(text=f"{c['title'][:24]} {c['date'][8:10]}.{c['date'][5:7]}", callback_data=f"rt:pk:{row_id}:{c['ref']}")
    b.adjust(1)
    return b.as_markup()
