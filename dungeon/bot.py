"""Telegram-бот: ввод задач текстом и голосом, кнопки, команды."""
from __future__ import annotations

import functools
import logging
import re
import tempfile
from datetime import timedelta
from html import escape
from pathlib import Path

from aiogram import BaseMiddleware, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import BotCommand, CallbackQuery, Message

from . import flows, keyboards, leisure, ritual_flows, stt, stuck, texts
from .app import App

log = logging.getLogger(__name__)

HELP = """<b>⚔ Dungeon Tasks</b>

Просто пиши или шли войс, каждая строка = задача:
• <i>завтра в 10 йога</i>
• <i>срочно позвонить в банк</i>
• <i>по будням в 9 почитать</i>
• <i>отчёт к пятнице, важно</i>
Метки: <code>!1</code>…<code>!4</code> — зона вручную. Слова «важно», «срочно», «неважно», «не срочно» раскидают по матрице.
Ответь на карточку задачи — поменяешь срок («завтра в 15») или название.

<b>Зоны</b>
🔥 Огненные залы — срочно и важно
📜 Эльфийская библиотека — важно, не срочно
👺 Гоблинские поручения — срочно, не важно
🌫 Туманное болото — ни то ни другое
🕯 ритуалы (привычки) будут доставать, пока не сделаешь

/today /tomorrow /week /matrix /inbox /hero
/focus [id] — фокус-рейд · /stop
/boss — выбрать босса недели
/shop · /reward Пицца 200 · /buy id
/checklist — чек-листы утра и вечера (и как их поменять)
/plan — спланировать неделю с коучем (4 шага) · /cancel — выйти
/free — «я свободен», предложи досуг
/settings · /set morning 08:00
/rest 5 — отпуск/болезнь на 5 дней: ритуалы без штрафов и напоминаний · /rest off
/setup_forum — в группе с Темами: создать топики бота
Длительность: <i>лендинг ~2ч</i>, <i>монтаж на 40 мин</i> — коуч считает по ней нагрузку."""

COMMANDS = [
    ("today", "Сводка на сегодня"), ("tomorrow", "План на завтра"), ("week", "Неделя"),
    ("matrix", "Карта подземелья (матрица)"), ("inbox", "Разобрать сундук"), ("hero", "Лист героя"),
    ("focus", "Фокус-рейд"), ("stop", "Прервать рейд"), ("boss", "Босс недели"), ("shop", "Таверна"),
    ("checklist", "Чек-листы утра и вечера"), ("plan", "Спланировать неделю с коучем"), ("free", "Я свободен: что поделать?"),
    ("stuck", "Застрял: 7 вопросов против прокрастинации"),
    ("rest", "Отпуск/болезнь: ритуалы без штрафов"), ("settings", "Настройки"), ("help", "Помощь"),
]
SETTABLE = {
    "morning": "время утренней сводки", "evening": "время вечерней сводки",
    "quiet_from": "начало тихих часов", "quiet_to": "конец тихих часов",
    "habit_time": "время ритуалов без времени", "nag_first": "минут между первыми повторами",
    "nag_later": "минут между поздними повторами", "focus_min": "длина рейда, мин",
    "skip_penalty_hp": "штраф HP за прогул ритуала", "rituals_enabled": "читать точечные чаты 1/0",
    "weekly_time": "время воскресного обзора", "mac_popup": "попап на маке 1/0",
    "morning_from": "с какого времени открытие ноута = утро", "evening_cl_fallback": "вечерний чек-лист не позже",
    "hours_weekday": "часов на задачи в будни", "hours_weekend": "часов на задачи в выходные",
    "coach_time": "время планирования недели в воскресенье",
    "free_from": "«Свободен?» не раньше", "free_to": "«Свободен?» не позже", "free_max": "«Свободен?» раз в день",
}
TIME_KEYS = {"morning", "evening", "quiet_from", "quiet_to", "habit_time", "weekly_time", "morning_from",
             "evening_cl_fallback", "coach_time", "free_from", "free_to"}


class OwnerOnly(BaseMiddleware):
    def __init__(self, app: App):
        self.app = app

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None:
            return None
        is_start = isinstance(event, Message) and (event.text or "").startswith("/start")
        if self.app.owner == user.id or (is_start and not self.app.owner):
            return await handler(event, data)
        if isinstance(event, Message):
            await event.answer("Это личное подземелье. Вход закрыт 🔒")
        return None


def build(app: App) -> Router:
    r = Router()
    r.message.outer_middleware(OwnerOnly(app))
    r.callback_query.outer_middleware(OwnerOnly(app))
    svc = app.svc

    # ---------- команды ----------
    @r.message(CommandStart())
    async def start(m: Message):
        app.claim_owner(m.from_user.id)
        await m.answer(HELP)

    @r.message(Command("help"))
    async def help_(m: Message):
        await m.answer(HELP)

    @r.message(Command("today"))
    async def today(m: Message):
        await m.answer(texts.morning(svc))

    @r.message(Command("tomorrow"))
    async def tomorrow(m: Message):
        await m.answer(texts.evening(svc))

    @r.message(Command("week"))
    async def week(m: Message):
        await m.answer(texts.week(svc))

    @r.message(Command("matrix"))
    async def matrix(m: Message):
        await m.answer(texts.matrix(svc))

    @r.message(Command("hero"))
    async def hero(m: Message):
        await m.answer(texts.hero(svc))

    @r.message(Command("inbox"))
    async def inbox(m: Message):
        await send_triage(app, m)

    @r.message(Command("boss"))
    async def boss(m: Message):
        cands = [t for t in svc.open_tasks() if t["quadrant"] in (1, 2) and not t["is_habit"]]
        if not cands:
            await m.answer("Нет кандидатов в боссы: добавь важную задачу 📜 или 🔥.")
            return
        await m.answer("Кого назначим боссом недели? Победа даст сундук: +300 XP 🐉",
                       reply_markup=keyboards.boss_pick(cands))

    @r.message(Command("focus"))
    async def focus(m: Message, command: CommandObject):
        tid = int(command.args) if command.args and command.args.strip().isdigit() else None
        await start_focus(app, m, tid)

    @r.message(Command("stop"))
    async def stop(m: Message):
        svc.focus_cancel()
        await m.answer("Рейд прерван. Без добычи 🏳")

    @r.message(Command("shop"))
    async def shop(m: Message):
        items = svc.rewards()
        gold = svc.game.hero()["gold"]
        if not items:
            await m.answer(f"🍺 Таверна пуста. Добавь награду: <code>/reward Серия сериала 50</code>\n🪙 {gold}")
            return
        lines = [f"<b>🍺 Таверна</b> · у тебя 🪙 {gold}", ""]
        lines += [f"<code>/buy {i['id']}</code> {escape(i['title'])} — {i['price']} 🪙" for i in items]
        await m.answer("\n".join(lines))

    @r.message(Command("reward"))
    async def reward(m: Message, command: CommandObject):
        mt = re.match(r"(.+?)\s+(\d+)$", (command.args or "").strip())
        if not mt:
            await m.answer("Формат: <code>/reward Пицца 200</code>")
            return
        svc.add_reward(mt.group(1), int(mt.group(2)))
        await m.answer(f"Награда в таверне: {escape(mt.group(1))} за {mt.group(2)} 🪙")

    @r.message(Command("buy"))
    async def buy(m: Message, command: CommandObject):
        arg = (command.args or "").strip()
        await m.answer(svc.buy(int(arg)) if arg.isdigit() else "Формат: <code>/buy 1</code>")

    @r.message(Command("settings"))
    async def settings(m: Message):
        lines = ["<b>⚙ Настройки</b>", ""]
        lines += [f"<code>{k}</code> = {app.db.get(k)} — {d}" for k, d in SETTABLE.items()]
        lines += ["", "Сменить: <code>/set morning 07:30</code>",
                  f"Страховка при спящем ноуте: {'✅' if app.ins.active else '❌ выключена'}"]
        await m.answer("\n".join(lines))

    @r.message(Command("set"))
    async def set_(m: Message, command: CommandObject):
        parts = (command.args or "").split()
        if len(parts) != 2 or parts[0] not in SETTABLE:
            await m.answer("Формат: <code>/set morning 07:30</code>. Ключи в /settings")
            return
        k, v = parts
        if k in TIME_KEYS:
            t = re.fullmatch(r"(\d{1,2}):(\d{2})", v)
            if not t or int(t[1]) > 23 or int(t[2]) > 59:  # 25:00 ронял тикер каждые 15 с
                await m.answer("Время в формате ЧЧ:ММ")
                return
            v = f"{int(t[1]):02d}:{t[2]}"  # 9:00 → 09:00, иначе строковые сравнения времени врут
        if k not in TIME_KEYS and not re.fullmatch(r"\d+(\.\d+)?", v):
            await m.answer("Нужно число")
            return
        app.db.set(k, v)
        await m.answer(f"✅ {k} = {v}")

    @r.message(Command("rest"))
    async def rest(m: Message, command: CommandObject):
        arg = (command.args or "").strip().lower()
        if arg in ("off", "0", "стоп"):
            app.db.set("rest_until", "")
            await m.answer("⚔ Отдых закончен, ритуалы снова в строю.")
            return
        if not re.fullmatch(r"\d{1,2}", arg) or not 1 <= int(arg) <= 30:
            until = app.db.get("rest_until", "") or ""
            await m.answer((f"🏝 Отдых до {until}. " if app.svc.resting() else "")
                           + "Формат: <code>/rest 5</code> (дней, до 30) или <code>/rest off</code>")
            return
        until = (app.svc.clock().date() + timedelta(days=int(arg) - 1)).isoformat()
        app.db.set("rest_until", until)
        await m.answer(f"🏝 Отдых до {until} включительно: ритуалы не штрафуют, не напоминают, стрики не сгорают.")

    @r.message(Command("checklist"))
    async def checklist_cmd(m: Message, command: CommandObject):
        """/checklist — показать; /checklist morning + пункты с новой строки — заменить; /checklist reset morning"""
        args = (command.args or "").strip()
        head, _, rest = args.partition("\n")
        words = head.split()
        if words[:1] == ["reset"] and len(words) == 2 and words[1] in ("morning", "evening"):
            svc.set_checklist(words[1], None)
            await m.answer(f"Чек-лист {words[1]} сброшен к стартовому.")
            return
        if words[:1] and words[0] in ("morning", "evening"):
            items = [i.strip(" -•\t") for i in rest.splitlines() if i.strip(" -•\t")]
            if not items:
                await m.answer("Пункты пиши с новой строки после команды:\n<code>/checklist morning\nВода\nЗарядка</code>")
                return
            svc.set_checklist(words[0], items)
            await m.answer(f"✅ Чек-лист {words[0]} обновлён: {len(items)} пунктов.")
            return
        for kind in ("morning", "evening"):
            state = svc.checklist_state(kind)
            await m.answer(texts.checklist(kind, state), reply_markup=keyboards.checklist(kind, state))
        await m.answer("Поменять: <code>/checklist morning</code> и пункты с новой строки. "
                       "Вернуть стартовый: <code>/checklist reset morning</code>")

    @r.message(Command("setup_forum"))
    async def setup_forum_cmd(m: Message):
        if m.chat.type != "supergroup" or not m.chat.is_forum:
            await m.answer("Запусти это в группе, где включены Темы (Topics): настройки группы → Темы.")
            return
        try:
            ids = await app.setup_forum(m.chat.id)
        except Exception as e:
            log.exception("setup_forum")
            await m.answer("Не вышло создать топики. Нужно сделать бота админом с правом «Управление темами».\n"
                           f"<code>{escape(str(e)[:200])}</code>")
            return
        await m.answer("✅ Топики готовы: " + ", ".join(app_topics(ids)) + ".\n"
                       "Задачи и события пиши в 🎯 События, планирование недели живёт в 📅 Планирование.")

    @r.message(Command("plan"))
    async def plan_cmd(m: Message):
        await flows.plan_start(app)

    @r.message(Command("stuck"))
    async def stuck_cmd(m: Message, command: CommandObject):
        arg = (command.args or "").strip().lstrip("#")
        t = svc.get(int(arg)) if arg.isdigit() else None
        await m.answer(stuck.start(app.db, t))

    @r.message(Command("cancel"))
    async def cancel_cmd(m: Message):
        if stuck.cancel(app.db):
            await m.answer("Выход из режима «застрял».")
            return
        sess = app.planner.active()
        if not sess:
            await m.answer("Нечего отменять.")
            return
        app.planner.cancel(sess["id"])
        await m.answer("Планирование отменено. Начать заново: /plan")

    @r.message(Command("free"))
    async def free_cmd(m: Message):
        await flows.free_yes(app)

    @r.message(Command("summary"))
    async def summary(m: Message, command: CommandObject):
        """Ручной запуск сводок, для проверки: /summary morning|evening|weekly"""
        from .scheduler import send_weekly
        kind = (command.args or "morning").strip()
        if kind == "weekly":
            await send_weekly(app)
        elif kind == "evening":
            await m.answer(texts.evening(svc))
            await send_triage(app, m)
        else:
            await m.answer(texts.morning(svc))

    # ---------- ввод ----------
    @r.message(F.sticker)
    async def sticker(m: Message):
        app.db.x("INSERT OR IGNORE INTO stickers(file_id) VALUES(?)", (m.sticker.file_id,))
        await m.answer("Стикер добавлен в арсенал напоминалок ⚔")

    @r.message(F.voice | F.audio | F.video_note)
    async def voice(m: Message):
        media = m.voice or m.audio or m.video_note
        wait = await m.answer("🎙 Слушаю…")
        if in_plan_topic(app, m):
            app.track("plan", m.chat.id, wait.message_id)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "voice.ogg"
            await m.bot.download(media, destination=path)
            try:
                text = await stt.transcribe(path)
            except Exception:
                log.exception("stt")
                await wait.edit_text("Не разобрал голос 😵 Попробуй ещё раз или текстом.")
                return
        if not text:
            await wait.edit_text("Тишина в эфире. Ничего не услышал.")
            return
        await wait.edit_text(f"🎙 <i>{escape(text)}</i>")
        await handle_text(app, m, text, "voice")

    @r.message(F.text & ~F.text.startswith("/"))
    async def text(m: Message):
        await handle_text(app, m, m.text, "tg")

    # ---------- кнопки ----------
    @r.callback_query(F.data.regexp(r"^(q|h|boss|d|x|s|tm|sk|f|ym|st):"))
    async def cb(c: CallbackQuery):
        parts = c.data.split(":")
        act, tid = parts[0], int(parts[1])
        now = svc.clock()
        if act == "f":
            await c.answer()
            await start_focus(app, c.message, tid or None)
            return
        t = svc.get(tid)
        if not t:
            await c.answer("Задача не найдена")
            return
        if act == "q":
            t = svc.set_quadrant(tid, int(parts[2]))
            await c.message.edit_text(texts.card(t, now), reply_markup=keyboards.card(t))
            await c.answer("Перенёс")
        elif act == "h":
            t = svc.toggle_habit(tid)
            await app.changed(tid)
            await c.message.edit_text(texts.card(t, now), reply_markup=keyboards.card(t))
            await c.answer()
        elif act == "st":
            await c.answer()
            await c.message.answer(stuck.start(app.db, t))
        elif act == "boss":
            t = svc.make_boss(tid)
            await c.answer("Босс назначен 🐉")
            await c.message.answer(f"🐉 Босс недели: <b>{escape(t['title'])}</b>. Одолей его — получишь сундук.")
        elif act == "d":
            if t["status"] != "open":
                await c.answer("Уже закрыто")
                return
            xp, msgs = await app.done(tid, notify_tg=False)
            await c.message.edit_text(f"✅ <s>{escape(t['title'])}</s>  +{xp} XP")
            await c.answer(f"+{xp} XP")
            await app.announce(msgs)
        elif act == "x":
            await app.delete(tid)
            await c.message.edit_text(f"🗑 <s>{escape(t['title'])}</s>")
            await c.answer("Удалено")
        elif act == "s":
            mins = int(parts[2])
            svc.snooze(tid, minutes=mins)
            await app.changed(tid)
            await c.message.edit_text(f"⏰ {escape(t['title'])} — напомню через {mins} мин")
            await c.answer()
        elif act == "tm":
            t = svc.snooze(tid, tomorrow=True)
            await app.changed(tid)
            await c.message.edit_text(f"➡ {escape(t['title'])} — {texts.when(t, now)}")
            await c.answer("Перенёс на завтра")
        elif act == "sk":
            if t["status"] != "open":
                await c.answer("Уже закрыто")
                return
            nxt = await app.skip(tid)
            tail = f", следующий раз {texts.when(nxt, now)}" if nxt else ""
            await c.message.edit_text(f"⏭ {escape(t['title'])} — пропущено без штрафа{tail}")
            await c.answer()
        elif act == "ym":
            if t["status"] != "open":
                await c.answer("Уже закрыто")
                return
            svc.mark_missed_pending(tid)
            await c.message.edit_text(texts.missed_followup(t), reply_markup=keyboards.missed_followup(t))
            await c.answer("Отмечено")

    @r.callback_query(F.data.regexp(r"^rs:\d+:(today|ask|drop)$"))
    async def missed_followup_cb(c: CallbackQuery):
        _, tid, act = c.data.split(":")
        tid = int(tid)
        t = svc.get(tid)
        if not t or t["status"] != "missed_pending":
            await c.answer("Уже решено")
            return
        if act == "today":
            t = svc.reopen_today(tid)
            await app.changed(tid)
            await c.message.edit_text(f"➡ <b>{escape(t['title'])}</b> — перенесено на сегодня")
            await c.answer("Перенёс на сегодня")
        elif act == "ask":
            await c.message.edit_text(texts.missed_ask_date(t))
            await c.answer()
        elif act == "drop":
            svc.mark_missed(tid)
            await c.message.edit_text(f"🚫 <s>{escape(t['title'])}</s>  не нужно, закрыто")
            await c.answer("Закрыто")

    @r.callback_query(F.data.regexp(r"^cl:[me]:\d+$"))
    async def cl(c: CallbackQuery):
        _, k, idx = c.data.split(":")
        kind = "morning" if k == "m" else "evening"
        state, msgs = svc.checklist_toggle(kind, int(idx))
        await c.message.edit_text(texts.checklist(kind, state), reply_markup=keyboards.checklist(kind, state))
        await c.answer()
        await app.announce(msgs)

    @r.callback_query(F.data == "plan:0")
    async def plan(c: CallbackQuery):
        app.db.set("plan_ready", svc.clock().date().isoformat())
        await c.message.edit_text("✅ План на завтра готов. Вечерний чек-лист сейчас прилетит.")
        await c.answer()

    @r.callback_query(F.data.regexp(r"^pl:(next|back|done|cancel):\d+$"))
    async def plan_cb(c: CallbackQuery):
        _, act, sid = c.data.split(":")
        sid = int(sid)
        if act == "cancel":
            app.planner.cancel(sid)
            await c.message.edit_reply_markup(reply_markup=None)
            await c.answer("Отменено")
            await c.message.answer("Планирование отменено. Начать заново: /plan")
            return
        if act == "back":
            if app.plan_busy:
                await c.answer("Коуч ещё думает…")
                return
            app.planner.back(sid)
            await c.message.edit_reply_markup(reply_markup=None)
            await c.answer()
            await flows.plan_show(app, sid)
            return
        sess = app.planner.get(sid)
        if sess and sess["status"] == "active" and not app.plan_busy and (sess["step"] > 1 or sess["inputs"].get("1")):
            await c.message.edit_reply_markup(reply_markup=None)
        await c.answer(await flows.plan_next(app, sid))

    @r.callback_query(F.data.in_({"fr:y", "fr:n"}))
    async def free_cb(c: CallbackQuery):
        await c.message.edit_reply_markup(reply_markup=None)
        await c.answer()
        if c.data == "fr:y":
            await flows.free_yes(app)
        else:
            await c.message.answer(await flows.free_no(app))

    @r.callback_query(F.data.regexp(r"^ev:(up|dn|add):\d+$"))
    async def event_cb(c: CallbackQuery):
        _, act, eid = c.data.split(":")
        if act == "add":
            msg = await flows.event_to_task(app, int(eid))
            await c.message.edit_reply_markup(reply_markup=None)
            await c.answer("Добавил")
            await c.message.answer(msg)
            return
        app.events.vote(int(eid), up=act == "up")
        await c.message.edit_reply_markup(reply_markup=None)
        await c.answer("Учту 👍" if act == "up" else "Учту, такое реже")

    @r.callback_query(F.data.regexp(r"^rt:[a-z]+:\d+(:[et]\d+)?$"))
    async def ritual_cb(c: CallbackQuery):
        parts = c.data.split(":")
        act, rid, arg = parts[1], int(parts[2]), parts[3] if len(parts) > 3 else None
        reply, kind = await ritual_flows.handle(app, act, rid, arg)
        if kind == "reason":
            await c.message.edit_reply_markup(reply_markup=None)
            await c.message.answer(reply, reply_markup=keyboards.ritual_reason(rid, int(app.db.get("skip_penalty_hp"))))
            await c.answer()
            return
        if act != "fix":
            await c.message.edit_reply_markup(reply_markup=None)
        await c.answer(reply[:190])
        if act != "no":
            await c.message.answer(reply)

    @r.callback_query(F.data.regexp(r"^ls:(w|s|n):\d+$"))
    async def leisure_cb(c: CallbackQuery):
        _, act, rid = c.data.split(":")
        kind = app.leisure.kind_of(int(rid))
        if act == "n":
            app.leisure.mark(int(rid), "skip")
            item = app.leisure.pick(kind) if kind else None
            if not item:
                await c.answer("Больше вариантов нет")
                return
            await c.message.edit_text(f"{leisure.KINDS[kind]}: <b>{escape(item['title'])}</b>\n<i>{escape(item['desc'])}</i>",
                                      reply_markup=keyboards.leisure(item["rid"], kind))
            await c.answer()
            return
        path = app.leisure.mark(int(rid), "watching" if act == "w" else "seen")
        if act == "s" and path and kind:
            leisure.mark_seen_in_note(path, kind)
        await c.message.edit_reply_markup(reply_markup=None)
        await c.answer("Приятного!" if act == "w" else "Вычеркнул, больше не предложу")

    return r


def app_topics(ids: dict[str, int]) -> list[str]:
    from .app import TOPICS
    return [TOPICS[k] for k in ids]


def in_plan_topic(app: App, m: Message) -> bool:
    """Сообщение из топика «Планирование» (форум не настроен: любое, как раньше)."""
    return app.in_topic(m.chat.id, m.message_thread_id if m.is_topic_message else None, "plan")


async def handle_text(app: App, m: Message, text: str, source: str) -> None:
    in_plan = in_plan_topic(app, m)
    if in_plan:
        app.track("plan", m.chat.id, m.message_id)
    if not in_plan and stuck.active(app.db):
        await stuck_answer(app, m, text)
        return
    if app.planner.active() and in_plan:
        n = await flows.plan_input(app, text)
        note = await m.answer(f"👌 записал в планирование недели ({n}). Ещё что-то или жми кнопку под шагом.\n"
                              "<i>Пока идёт планирование, сообщения в этом топике идут в план. Выйти: /cancel</i>")
        app.track("plan", m.chat.id, note.message_id)
        return
    if app.forum and in_plan:
        note = await m.answer("Здесь живёт планирование недели. Начать: /plan. "
                              "Задачи и события пиши в 🎯 События.")
        app.track("plan", m.chat.id, note.message_id)
        return
    reply = m.reply_to_message
    if reply and reply.from_user and reply.from_user.is_bot and (mr := re.search(r"\[a(\d+)\]", reply.text or "")):
        await m.answer(await ritual_flows.retime_from_reply(app, int(mr.group(1)), text))
        return
    if reply and reply.from_user and reply.from_user.is_bot and (mt := re.search(r"#(\d+)", reply.text or "")):
        tid = int(mt.group(1))
        if app.svc.get(tid):
            t = app.svc.edit_from_text(tid, text)
            await app.changed(tid)
            await m.answer(texts.card(t, app.svc.clock()), reply_markup=keyboards.card(t))
            return
    tasks = app.svc.create(text, source)
    for t in tasks:
        await app.ins.sync(t)
        await m.answer(texts.card(t, app.svc.clock()), reply_markup=keyboards.card(t))
    if tasks:
        msgs = app.svc.game.check_achievements()
        await app.announce(msgs)


async def stuck_answer(app: App, m: Message, text: str) -> None:
    msg, res = stuck.answer(app.db, text)
    if not res:
        await m.answer(msg)
        return
    t = app.svc.create_parsed(stuck.to_parsed(res), "stuck")
    app.db.x("UPDATE tasks SET notes=? WHERE id=?", (f"Награда: {res['reward']}", t["id"]))
    await app.ins.sync(app.svc.get(t["id"]))
    await m.answer(stuck.summary(res), reply_markup=keyboards.focus(t["id"]))


async def send_triage(app: App, m: Message | None = None) -> int:
    items = app.svc.inbox()
    send = m.answer if m else functools.partial(app.send, topic="events")
    if not items:
        if m:
            await m.answer("📥 Сундук у входа пуст. Чистота как в эльфийском храме.")
        return 0
    await send(f"📥 <b>Разбор сундука</b>: {len(items)} шт. Раскидай по зонам:")
    for t in items[:10]:
        text = texts.task_line(t, app.svc.clock(), with_day=True) + f"  <code>#{t['id']}</code>"
        if m:
            await m.answer(text, reply_markup=keyboards.triage(t))
        else:
            await app.send(text, keyboards.triage(t), silent=True, topic="events")
    return len(items)


async def start_focus(app: App, m: Message, tid: int | None) -> None:
    f = app.svc.focus_start(tid)
    t = app.svc.get(tid) if tid else None
    mins = int(app.db.get("focus_min"))
    target = f" на «{escape(t['title'])}»" if t else ""
    end = app.svc.clock() + timedelta(minutes=mins)
    await m.answer(f"⚔ Рейд{target} начат: {mins} мин, до {end:%H:%M}. Телефон экраном вниз.\n"
                   f"<i>/stop — прервать</i>")
    log.info("focus %s started", f["id"])


async def set_commands(app: App) -> None:
    await app.bot.set_my_commands([BotCommand(command=c, description=d) for c, d in COMMANDS])

