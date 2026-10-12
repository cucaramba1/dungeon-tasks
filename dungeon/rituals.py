"""Точечные чаты: анонсы ритуалов и событий от конкретных людей.

Модуль без Telegram-бота: читает чаты через telegram-mcp, кладёт строки в events_feed (kind announce/move/cancel),
меняет задачи по решениям Некита. Сообщения шлёт ritual_flows.py. Разбор постов делает ritual_llm.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta, timezone  # noqa: F401

from . import config, ritual_llm, tgmcp
from .db import DB, iso, parse_iso, utcnow  # noqa: F401
from .parser import Parsed  # noqa: F401
from .services import Service, hm  # noqa: F401

log = logging.getLogger(__name__)
DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
FIRST_RUN_DAYS = 7
DIGEST_CAP = 10


def cfg() -> list[dict]:
    return config.yaml_file("events").get("targeted", [])


def chat_cfg(chat_id: int) -> dict | None:
    return next((c for c in cfg() if c["id"] == chat_id), None)


def author_ok(chat: dict, post: dict) -> bool:
    if chat.get("channel"):
        return True  # канал пишет от своего имени: берём все посты
    return (post.get("username") or "").lower() in {a.lower() for a in chat.get("authors", [])}


def usable(chat: dict, post: dict, since: datetime | None) -> bool:
    """Пост с текстом от нужного автора (не опрос, не служебное, не старше окна первого запуска)."""
    if len((post.get("text") or "").strip()) < 8 or post.get("action") or not author_ok(chat, post):
        return False
    return since is None or datetime.fromisoformat(post["date"]) >= since


def sources(chat: dict) -> list[tuple[int | None, str | None]]:
    """Что читать в чате: форум = его топики с подписями, обычный чат = один поток."""
    topics = chat.get("topics")
    return list(topics.items()) if topics else [(None, None)]


def chat_link(chat: dict, msg_id: int) -> str:
    if chat.get("username"):
        return f"https://t.me/{chat['username']}/{msg_id}"
    cid = str(chat["id"])
    cid = cid.removeprefix("-100") if cid.startswith("-100") else cid.lstrip("-")
    return f"https://t.me/c/{cid}/{msg_id}"


def as_date(v) -> date | None:
    if not v:
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v))


class Rituals:
    def __init__(self, db: DB, svc: Service):
        self.db = db
        self.svc = svc

    @property
    def tz(self):
        return self.svc.clock().tzinfo

    def feed(self, row_id: int):
        return self.db.one("SELECT * FROM events_feed WHERE id=?", (row_id,))

    # ---------- что уже известно по чату ----------
    def occurrence_task(self, chat: dict):
        """Ближайшая открытая запись серии, к которой привязан чат (D&D, йога)."""
        if not chat.get("series"):
            return None
        return self.db.one("SELECT * FROM tasks WHERE is_habit=1 AND status='open' AND lower(title)=lower(?) "
                           "ORDER BY due_at LIMIT 1", (chat["series"],))

    def known(self, chat: dict, now: datetime) -> list[dict]:
        """Что в этом чате можно переносить и отменять: открытые анонсы + запись серии. Это же контекст для LLM."""
        rows = self.db.q("SELECT id, title, starts_at, task_id FROM events_feed WHERE chat_id=? AND kind='announce' "
                         "AND status IN ('new','accepted') AND starts_at>=? ORDER BY starts_at",
                         (chat["id"], iso(now - timedelta(days=1))))
        fmt = lambda s: parse_iso(s).astimezone(now.tzinfo).strftime("%Y-%m-%d %H:%M")  # noqa: E731
        out = [{"ref": f"e{r['id']}", "title": r["title"], "date": fmt(r["starts_at"])} for r in rows]
        t = self.occurrence_task(chat)
        if t and t["id"] not in {r["task_id"] for r in rows}:
            due = parse_iso(t["due_at"])
            if not any(abs(parse_iso(r["starts_at"]) - due) <= timedelta(days=3) for r in rows):  # слот уже представлен анонсом
                out.append({"ref": f"t{t['id']}", "title": t["title"], "date": fmt(t["due_at"])})
        return out

    # ---------- сбор ----------
    def _last_key(self, chat_id: int, topic: int | None) -> str:
        return f"rt_last:{chat_id}:{topic or 0}"

    async def collect(self, tz, now: datetime | None = None, fetch=tgmcp.history, ask=None, pause: float = 1.0) -> dict:
        """Пройти по точечным чатам, разобрать новые посты нужных авторов. LLM зовём только если есть что разбирать."""
        now = now or datetime.now(tz)
        stats = {"posts": 0, "items": 0, "errors": 0}
        fails = 0
        for chat in cfg():
            fresh: list[tuple[dict, str | None]] = []
            marks: dict[str, int] = {}
            try:
                for topic, label in sources(chat):
                    key = self._last_key(chat["id"], topic)
                    last = int(self.db.get(key, "0") or 0)
                    posts = await fetch(chat["id"], 100, topic)  # 100 = весь кэш telegram-mcp: плотный чат не теряет посты между опросами
                    if last and len(posts) >= 100 and min(p.get("id", 0) for p in posts) > last:
                        log.warning("rituals: %s: между опросами больше 100 сообщений, часть могла пропасть", chat["name"])
                    marks[key] = max([p.get("id", 0) for p in posts] + [last])
                    floor = None if last else now - timedelta(days=FIRST_RUN_DAYS)
                    fresh += [(p, label) for p in posts if p.get("id", 0) > last and usable(chat, p, floor)]
            except Exception as e:
                stats["errors"] += 1
                fails += 1
                log.warning("rituals: %s не прочитался: %s", chat["name"], str(e)[:200])
                if fails >= 2:  # telegram-mcp лежит: не ждём по минуте на каждый чат
                    stats["aborted"] = True
                    break
                continue
            fails = 0
            stats["posts"] += len(fresh)
            if fresh:
                payload = [self._payload(p, label, tz) for p, label in fresh]
                try:
                    items = await ritual_llm.classify(chat, payload, self.known(chat, now), now, ask=ask)
                except Exception as e:  # claude не ответил: курсор не двигаем, посты разберём в следующий раз
                    stats["errors"] += 1
                    stats["claude_down"] = True
                    log.warning("rituals: claude не разобрал %s: %s", chat["name"], str(e)[:200])
                    continue
                by_id = {p["id"]: (p, label) for p, label in fresh}
                # сначала анонсы, потом переносы и отмены: им нужны уже известные события
                for it in sorted(items, key=lambda i: (i.kind != "announce", i.msg_id)):
                    if it.msg_id in by_id:
                        stats["items"] += self.ingest(chat, *by_id[it.msg_id], it, now)
            for key, mark in marks.items():
                self.db.set(key, mark)
            await asyncio.sleep(pause)  # не душим telegram-mcp
        return stats

    @staticmethod
    def _payload(post: dict, label: str | None, tz) -> dict:
        d = datetime.fromisoformat(post["date"]).astimezone(tz)
        wd = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][d.weekday()]
        return {"msg_id": post["id"], "date": d.strftime("%Y-%m-%d %H:%M"), "weekday": wd,
                "author": post.get("username"), "topic": label, "text": (post.get("text") or "")[:1500]}

    # ---------- разбор одного элемента ----------
    def _relevant(self, chat: dict, it) -> bool:
        """Занятия вне наших дней (пятничная йога) и после until не интересуют."""
        until, days = as_date(chat.get("until")), chat.get("attend_days")
        dates = [d for d in (it.start.date() if it.start else None, it.orig_date) if d]
        if until and dates and all(d > until for d in dates):
            return False
        if days and dates and not any(DAYS[d.weekday()] in days for d in dates):
            return False
        return True

    def _change_day(self, it) -> date | None:
        """О какой дате речь в переносе или отмене (старая дата, а для отмены ещё и названная)."""
        return it.orig_date or (it.start.date() if it.kind == "cancel" and it.start else None)

    def find_target(self, chat: dict, it, now: datetime):
        """Какое известное событие двигают или отменяют. Возвращает (ref_id, ref_task, подходящие кандидаты)."""
        cands = self.known(chat, now)
        by_ref = {c["ref"]: c for c in cands}
        pick, pool = None, cands
        if it.ref in by_ref:
            pick = it.ref
        else:
            day = self._change_day(it)
            pool = [c for c in cands if c["date"][:10] == day.isoformat()] if day else cands
            if len(pool) == 1:
                pick = pool[0]["ref"]
        if not pick:
            return None, None, pool  # названная дата ничему не соответствует: кандидатов нет, это не «неоднозначно»
        return (int(pick[1:]), None, pool) if pick[0] == "e" else (None, int(pick[1:]), pool)

    def _insert(self, base: dict, **cols) -> int:
        data = {**base, **cols, "created_at": iso(utcnow())}
        return self.db.x(f"INSERT INTO events_feed({','.join(data)}) VALUES({','.join('?' * len(data))})",
                         tuple(data.values()))

    def ingest(self, chat: dict, post: dict, label: str | None, it, now: datetime) -> int:
        """Положить разобранный пост в events_feed. Возвращает 1, если появилась новая строка."""
        key = f"rt:{chat['id']}:{post['id']}"
        if self.db.one("SELECT 1 FROM events_feed WHERE key=?", (key,)) or not self._relevant(chat, it):
            return 0
        extra = {k: v for k, v in {"book": it.book, "place": it.place, "description": it.description,
                                   "variant": label}.items() if v}
        check = int(it.needs_check)
        if (it.kind == "announce" and it.needs_check and it.start and it.start < now
                and now - datetime.fromisoformat(post["date"]) <= timedelta(hours=24)):
            check = 2  # свежий пост с датой в прошлом: скорее всего LLM ошиблась, покажем с пометкой «проверь»
        base = {"key": key, "chat_id": chat["id"], "channel": chat["name"], "msg_id": post["id"],
                "link": chat_link(chat, post["id"]), "title": it.title[:120], "text": (post.get("text") or "")[:700],
                "author": post.get("username"), "ritual": chat["name"], "kind": it.kind,
                "llm_raw": json.dumps(it.raw, ensure_ascii=False), "needs_check": check}
        if it.kind == "announce":
            if self._same_day_duplicate(chat, it, extra):
                return 0  # автор поправил или повторил анонс
            self._insert(base, extra=json.dumps(extra, ensure_ascii=False), status="new",
                         starts_at=iso(it.start), has_time=int(it.has_time))
            return 1
        ref_id, ref_task, cands = self.find_target(chat, it, now)
        status = "new" if (ref_id or ref_task) else ("ambiguous" if cands else "unresolved")
        if status == "new" and it.needs_check:
            status = "ambiguous"  # модель не уверена или дата в прошлом: сначала спросим, потом применим
        day = self._change_day(it)
        if status == "unresolved" and day and day < now.date():
            return 0  # про прошлое Некита не будим
        if status == "ambiguous":
            extra["candidates"] = cands
        start = it.start or self._target_start(ref_id, ref_task) or datetime.fromisoformat(post["date"])
        self._insert(base, extra=json.dumps(extra, ensure_ascii=False), status=status, starts_at=iso(start),
                     has_time=int(it.has_time), ref_id=ref_id, ref_task=ref_task)
        return 1

    def _same_day_duplicate(self, chat: dict, it, extra: dict) -> bool:
        """Повтор анонса: тот же старт, напоминание без времени при уже известном времени, либо время уточнило
        запись без времени (тогда обновляем её, а не заводим вторую карточку)."""
        day = datetime.combine(it.start.date(), time(0, 0), it.start.tzinfo)
        same = self.db.q("SELECT * FROM events_feed WHERE chat_id=? AND kind='announce' AND starts_at>=? AND starts_at<? "
                         "AND status NOT IN ('declined','expired','cancelled')",
                         (chat["id"], iso(day), iso(day + timedelta(days=1))))
        if any(r["starts_at"] == iso(it.start) for r in same) or (not it.has_time and same):
            return True
        date_only = next((r for r in same if not r["has_time"]), None)
        if it.has_time and date_only:
            merged = {**json.loads(date_only["extra"] or "{}"), **extra}
            self.db.x("UPDATE events_feed SET starts_at=?, has_time=1, title=?, extra=? WHERE id=?",
                      (iso(it.start), it.title[:120], json.dumps(merged, ensure_ascii=False), date_only["id"]))
            return True
        return False

    def _target_start(self, ref_id: int | None, ref_task: int | None) -> datetime | None:
        if ref_id and (e := self.feed(ref_id)):
            return parse_iso(e["starts_at"])
        if ref_task and (t := self.svc.get(ref_task)) and t["due_at"]:
            return parse_iso(t["due_at"])
        return None

    # ---------- серия ----------
    def series_exists(self, chat: dict) -> bool:
        return self.occurrence_task(chat) is not None

    def occurrence_near(self, chat: dict, start: datetime):
        """Открытая запись серии в пределах трёх дней от даты анонса."""
        if not chat.get("series"):
            return None
        best = None
        for t in self.db.q("SELECT * FROM tasks WHERE is_habit=1 AND status='open' AND lower(title)=lower(?) "
                           "AND due_at IS NOT NULL", (chat["series"],)):
            gap = abs(parse_iso(t["due_at"]) - start)
            if gap <= timedelta(days=3) and (best is None or gap < best[0]):
                best = (gap, t)
        return best[1] if best else None

    @staticmethod
    def _merge_time(new: datetime, has_time: bool, has_old: bool, old_iso: str | None):
        """Перенос «на субботу» без времени не должен терять время занятия."""
        if has_time or not has_old or not old_iso:
            return new, has_time
        old = parse_iso(old_iso).astimezone(new.tzinfo)
        return datetime.combine(new.date(), old.timetz()), True

    # ---------- переносы и отмены ----------
    def new_changes(self) -> list:
        return self.db.q("SELECT * FROM events_feed WHERE kind IN ('move','cancel') AND status='new' ORDER BY id")

    def pick_target(self, row_id: int, ref: str) -> None:
        """Некит выбрал кнопкой, какое событие переносили: теперь изменение можно применить."""
        row = self.feed(row_id)
        if not row or row["status"] != "ambiguous":
            return  # повторное нажатие или уже применено
        if ref[0] == "e":
            self.db.x("UPDATE events_feed SET ref_id=?, status='new', notified_at=NULL WHERE id=?", (int(ref[1:]), row_id))
        else:
            self.db.x("UPDATE events_feed SET ref_task=?, status='new', notified_at=NULL WHERE id=?",
                      (int(ref[1:]), row_id))

    def apply_change(self, row_id: int) -> list[int]:
        """Применить перенос или отмену. Возвращает id задач, которые изменились (для страховки и календаря)."""
        row = self.feed(row_id)
        new = parse_iso(row["starts_at"]).astimezone(self.tz)
        ev = self.feed(row["ref_id"]) if row["ref_id"] else None
        if ev:
            t = self.svc.get(ev["task_id"]) if ev["task_id"] else self.occurrence_near(
                chat_cfg(ev["chat_id"]) or {}, parse_iso(ev["starts_at"]).astimezone(self.tz))
        else:
            t = self.svc.get(row["ref_task"]) if row["ref_task"] else None
        if t and t["status"] != "open":
            t = None
        undo: dict = {"title": ev["title"] if ev else (t["title"] if t else row["title"])}
        touched: list[int] = []
        if row["kind"] == "move":
            src_iso, src_has = (ev["starts_at"], ev["has_time"]) if ev else ((t["due_at"], t["has_time"]) if t else (None, 0))
            when, w_has = self._merge_time(new, bool(row["has_time"]), bool(src_has), src_iso)
            undo["old"] = {"starts_at": src_iso, "has_time": src_has}
            undo["new"] = {"starts_at": iso(when), "has_time": int(w_has)}
            if ev:
                undo["event"] = {"id": ev["id"], "starts_at": ev["starts_at"], "has_time": ev["has_time"]}
                self.db.x("UPDATE events_feed SET starts_at=?, has_time=? WHERE id=?", (iso(when), int(w_has), ev["id"]))
            if t:
                undo["task"] = {"id": t["id"], "due_at": t["due_at"], "has_time": t["has_time"]}
                self.svc.set_due(t["id"], when, w_has)
                touched.append(t["id"])
        else:
            if ev:
                undo["event"] = {"id": ev["id"], "status": ev["status"]}
                undo["old"] = {"starts_at": ev["starts_at"], "has_time": ev["has_time"]}
                self.db.x("UPDATE events_feed SET status='cancelled' WHERE id=?", (ev["id"],))
            if t:
                undo.setdefault("old", {"starts_at": t["due_at"], "has_time": t["has_time"]})
                spawned = None
                if t["is_habit"] and t["rrule"]:
                    nxt = self.svc.skip(t["id"])      # отмена организатора: без штрафа, серия идёт дальше
                    spawned = nxt["id"] if nxt else None
                else:
                    self.svc.delete(t["id"])
                undo["task"] = {"id": t["id"], "spawned": spawned}
                touched += [t["id"]] + ([spawned] if spawned else [])
        self.db.x("UPDATE events_feed SET status='applied', undo=?, notified_at=NULL WHERE id=?",
                  (json.dumps(undo, ensure_ascii=False), row_id))
        return touched

    def undo(self, row_id: int) -> list[int]:
        row = self.feed(row_id)
        if not row or row["status"] != "applied":
            return []
        u = json.loads(row["undo"] or "{}")
        touched: list[int] = []
        if e := u.get("event"):
            if row["kind"] == "move":
                self.db.x("UPDATE events_feed SET starts_at=?, has_time=? WHERE id=?",
                          (e["starts_at"], e["has_time"], e["id"]))
            else:
                self.db.x("UPDATE events_feed SET status=? WHERE id=?", (e["status"], e["id"]))
        if t := u.get("task"):
            if row["kind"] == "move":
                self.svc.set_due(t["id"], parse_iso(t["due_at"]), bool(t["has_time"]))
            else:
                self.svc.reopen(t["id"])
                if t.get("spawned"):
                    self.svc.delete(t["spawned"])
                    touched.append(t["spawned"])
            touched.append(t["id"])
        self.db.x("UPDATE events_feed SET status='undone' WHERE id=?", (row_id,))
        return touched

    # ---------- решения Некита по карточке ----------
    def _notes(self, ev) -> str:
        extra = json.loads(ev["extra"] or "{}")
        lines = [ev["link"]]
        for key, label in (("book", "Книга"), ("place", "Место"), ("variant", "Формат"), ("description", "О чём")):
            if extra.get(key):
                lines.append(f"{label}: {extra[key]}")
        return "\n".join(x for x in lines if x)

    def accept(self, event_id: int) -> dict:
        """«Иду» / «Подтвердить»: занятие серии подтверждается, обычное событие становится разовой задачей."""
        ev = self.feed(event_id)
        if ev["status"] != "new":
            return {"task_id": ev["task_id"], "title": ev["title"], "text": "Уже решено"}
        chat = chat_cfg(ev["chat_id"]) or {}
        start = parse_iso(ev["starts_at"]).astimezone(self.tz)
        t = self.occurrence_near(chat, start)
        if t:
            # keep_time: время занятия у Некита своё (D&D 18:30, в чате «начало в 19:00»), подтверждаем только день
            has_time = bool(ev["has_time"]) and not chat.get("keep_time")
            when, has = self._merge_time(start, has_time, bool(t["has_time"]), t["due_at"])
            if parse_iso(t["due_at"]).astimezone(self.tz) != when:
                self.svc.set_due(t["id"], when, has)
            text = f"Подтвердил: {t['title']}"
        elif self.series_exists(chat):
            self.db.x("UPDATE events_feed SET status='accepted' WHERE id=?", (event_id,))
            return {"task_id": None, "title": ev["title"],
                    "text": "Записал. Занятие появится в плане, когда дойдёт очередь серии."}
        else:
            t = self.svc.create_parsed(Parsed(title=ev["title"], quadrant=4, due_at=start,
                                              has_time=bool(ev["has_time"])), "ritual")
            text = f"В плане: {t['title']}"
        self.db.x("UPDATE tasks SET notes=? WHERE id=?", (self._notes(ev), t["id"]))
        self.db.x("UPDATE events_feed SET status='accepted', task_id=? WHERE id=?", (t["id"], event_id))
        return {"task_id": t["id"], "title": t["title"], "text": text}

    def _until_utc(self, chat: dict) -> str | None:
        until = as_date(chat.get("until"))
        if not until:
            return None
        end = datetime.combine(until, time(23, 59, 59), self.tz)
        return end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def make_ritual(self, event_id: int) -> dict:
        """«В ритуал» (или «Завести ритуал» для йоги): серия на каждую неделю с этого анонса."""
        ev = self.feed(event_id)
        if ev["status"] != "new":
            return {"task_id": ev["task_id"], "title": ev["title"], "text": "Уже решено"}
        chat = chat_cfg(ev["chat_id"]) or {}
        start = parse_iso(ev["starts_at"]).astimezone(self.tz)
        byday = ",".join(chat["attend_days"]) if chat.get("attend_days") else DAYS[start.weekday()]
        rule = f"FREQ=WEEKLY;BYDAY={byday}" + (f";UNTIL={u}" if (u := self._until_utc(chat)) else "")
        title = chat.get("series") or chat["name"]
        t = self.svc.create_parsed(Parsed(title=title, quadrant=2, is_habit=True, due_at=start,
                                          has_time=bool(ev["has_time"]), rrule=rule), "ritual")
        self.db.x("UPDATE tasks SET notes=? WHERE id=?", (self._notes(ev), t["id"]))
        self.db.x("UPDATE events_feed SET status='accepted', task_id=? WHERE id=?", (t["id"], event_id))
        return {"task_id": t["id"], "title": title, "text": f"🕯 Завёл ритуал: {title}"}

    def decline(self, event_id: int) -> None:
        self.db.x("UPDATE events_feed SET status='declined' WHERE id=? AND status='new'", (event_id,))

    def skip(self, event_id: int, bad: bool) -> tuple[list[str], list[int]]:
        """Некит пропускает занятие. Прогул (bad) штрафуется, важная причина нет. Разовая задача просто удаляется."""
        ev = self.feed(event_id)
        if ev["status"] not in ("new", "accepted"):
            return [], []  # уже решено: повторное нажатие не штрафует второй раз и не трогает следующее занятие
        chat = chat_cfg(ev["chat_id"]) or {}
        t = self.svc.get(ev["task_id"]) if ev["task_id"] else None
        if t is None:
            t = self.occurrence_near(chat, parse_iso(ev["starts_at"]).astimezone(self.tz))
        msgs: list[str] = []
        touched: list[int] = []
        if t and t["status"] == "open":
            if t["is_habit"] and t["rrule"]:
                if bad:
                    msgs, nxt = self.svc.skip_missed(t["id"])
                else:
                    nxt = self.svc.skip(t["id"])
                touched = [t["id"]] + ([nxt["id"]] if nxt else [])
            else:
                msgs = self.svc.delete(t["id"])
                touched = [t["id"]]
        self.db.x("UPDATE events_feed SET status='skipped' WHERE id=?", (event_id,))
        return msgs, touched

    def retime(self, event_id: int, due: datetime, has_time: bool) -> None:
        """«✏ Поправить»: Некит сам назвал верные дату и время."""
        ev = self.feed(event_id)
        self.db.x("UPDATE events_feed SET starts_at=?, has_time=?, needs_check=0 WHERE id=?",
                  (iso(due), int(has_time), event_id))
        if ev["task_id"] and (t := self.svc.get(ev["task_id"])) and t["status"] == "open":
            self.svc.set_due(t["id"], due, has_time)

    # ---------- очереди рассылки ----------
    _ALIVE = ("((has_time=1 AND starts_at>=:now) OR (has_time=0 AND starts_at>=:day)"
              " OR (needs_check=2 AND created_at>=:recent))")

    def _times(self, now: datetime) -> dict:
        return {"now": iso(now), "day": iso(now.replace(hour=0, minute=0, second=0, microsecond=0)),
                "recent": iso(now - timedelta(hours=24))}

    def unsent_changes(self) -> list:
        return self.db.q("SELECT * FROM events_feed WHERE kind IN ('move','cancel') AND notified_at IS NULL "
                         "AND status IN ('applied','ambiguous','unresolved') ORDER BY id")

    def unsent_cards(self, now: datetime, within: timedelta | None = None) -> list:
        args = self._times(now)
        sql = (f"SELECT * FROM events_feed WHERE kind='announce' AND status='new' AND notified_at IS NULL "
               f"AND {self._ALIVE}")
        if within:
            sql += " AND starts_at<=:soon"
            args["soon"] = iso(now + within)
        return self.db.q(sql + " ORDER BY starts_at", args)

    def reminders(self, now: datetime) -> list:
        gap = timedelta(hours=float(self.db.get("ritual_remind_h")))
        return self.db.q("SELECT * FROM events_feed WHERE kind='announce' AND status='new' AND notified_at IS NOT NULL "
                         f"AND reminded_at IS NULL AND notified_at<=:gap AND {self._ALIVE}",
                         {**self._times(now), "gap": iso(now - gap)})

    def mark_sent(self, row_id: int, now: datetime) -> None:
        self.db.x("UPDATE events_feed SET notified_at=? WHERE id=?", (iso(now), row_id))

    def mark_reminded(self, row_id: int, now: datetime) -> None:
        self.db.x("UPDATE events_feed SET reminded_at=? WHERE id=?", (iso(now), row_id))

    def expire(self, now: datetime) -> None:
        """Анонс, который уже начался (или день прошёл), гаснет сам."""
        self.db.x("UPDATE events_feed SET status='expired' WHERE kind='announce' AND status='new' AND "
                  "((has_time=1 AND starts_at<:now) OR (has_time=0 AND starts_at<:day)) "
                  "AND NOT (needs_check=2 AND created_at>=:recent)", self._times(now))

    def digest_slots(self) -> list[time]:
        return [hm(s.strip()) for s in (self.db.get("ritual_digest") or "").split(",") if s.strip()]

    def digest_due(self, now: datetime) -> bool:
        """Подборка по слотам 10:00, 16:00, 22:00: каждый слот срабатывает один раз (с догоном после сна ноута)."""
        passed = [s for s in self.digest_slots() if s <= now.time()]
        if not passed:
            return False
        mark = f"{now.date().isoformat()}:{max(passed):%H:%M}"
        if self.db.get("rt_digest_done") == mark:
            return False
        self.db.set("rt_digest_done", mark)
        return True

    def listing(self, now: datetime) -> list[dict]:
        """Для раздела PWA: ближайшие анонсы, которые ещё актуальны."""
        rows = self.db.q(f"SELECT * FROM events_feed WHERE kind='announce' AND status IN ('new','accepted') "
                         f"AND {self._ALIVE} ORDER BY starts_at", self._times(now))
        wd = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
        out = []
        for r in rows:
            d = parse_iso(r["starts_at"]).astimezone(now.tzinfo)
            extra = json.loads(r["extra"] or "{}")
            out.append({"id": r["id"], "title": r["title"], "ritual": r["ritual"], "link": r["link"],
                        "status": r["status"], "needs_check": bool(r["needs_check"]),
                        "when": f"{wd[d.weekday()]} {d:%d.%m}" + (f" {d:%H:%M}" if r["has_time"] else ""),
                        "extra": " · ".join(str(extra[k]) for k in ("book", "variant", "place") if extra.get(k))})
        return out
