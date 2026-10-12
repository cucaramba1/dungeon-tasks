# Точечные чаты: анонсы ритуалов и событий. План реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Бот Dungeon Tasks сам читает 10 точечных Telegram-чатов, разбирает анонсы, переносы и отмены через `claude -p`, шлёт Некиту карточки (3 подборки в день, срочное сразу) и двигает задачи серии.

**Architecture:** Новый модуль `rituals.py` (данные и решения, без Telegram и бота) + `ritual_llm.py` (контракт с `claude -p`) + `ritual_flows.py` (сообщения, кнопки). Анонсы лежат в той же таблице `events_feed` (расширенной колонками), точечные чаты описаны в `config/events.yaml` секцией `targeted` и исключены из общей афиши. Включается флагом `rituals_enabled` после ручного прогона.

**Tech Stack:** Python 3.12, SQLite (`dungeon/db.py`), aiogram 3, FastAPI + PWA на чистом JS, pytest, `claude -p` через `coach.ask_claude`, telegram-mcp через `tgmcp.py`, python-dateutil.

**Spec:** `docs/superpowers/specs/2026-10-12-targeted-chats-rituals-design.md`

## Global Constraints

- Запуск тестов: `uv run pytest` из `/Users/cucaramba/Claude Code/dungeon-tasks`. До начала работ тесты зелёные (README: 108 тестов), после каждой задачи они остаются зелёными.
- Тесты не ходят в сеть и не запускают `claude`: `conftest.py` подменяет `CLAUDE_BIN` на `/usr/bin/false`, LLM в тестах всегда подменяется фейком через параметр `ask`.
- Тексты для Некита: русский, без длинного тире (—), на «ты», в стиле существующих сообщений бота.
- Таймзона везде берётся из `svc.clock().tzinfo` (Нячанг, UTC+7), в БД время хранится в UTC ISO (`db.iso`).
- Штраф за прогул: `skip_penalty_hp` (по умолчанию 20), хранится в `kv` через `DEFAULT_SETTINGS`.
- Подборки анонсов: `ritual_digest` = `10:00,16:00,22:00`. Анонс на ближайшие `ritual_urgent_h` = 3 часа уходит сразу. Напоминание один раз через `ritual_remind_h` = 2.5 часа. В подборке максимум 10 карточек.
- Опрос чатов: раз в `events_every_min` (60 минут), как афиша. Первый запуск читает только последние 7 дней.
- Тихие часы (`quiet_from`/`quiet_to`): в БД изменения применяются, сообщения уходят после.
- Коммиты: в репозитории пока нет ни одного коммита, и Некит коммитить не просил. Шаги `Commit` выполнять только если Некит разрешил, иначе пропустить (оставить `git add`). Если коммитишь, в конец сообщения: `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>`.
- Чтение чатов идёт с аккаунта Некита через telegram-mcp (`tgmcp.py`), писать в чаты нельзя и не нужно.

## Review Focus

1. Пост от нужного автора без даты («Всем привет, ждём всех!») не создаёт карточку (`parse_items`, Task 4).
2. Автор отредактировал или повторил анонс: второй карточки на тот же старт нет (`ingest`, Task 5).
3. Перенос без исходной даты при нескольких кандидатах не двигает ничего наугад, а спрашивает кнопками (`find_target`, Task 5; `send_change`, Task 7).
4. LLM не уверен или дата уже в прошлом: карточка идёт с пометкой «проверь», протухшие анонсы гаснут сами и не рассылаются (Task 4, Task 6).
5. telegram-mcp или `claude` недоступны: курсор чата не двигается, посты не теряются, уведомление об ошибке одно в сутки (`collect`, Task 5; `ritual_flows.collect`, Task 7).
6. Пятничная йога (не наш день) и анонсы после `until` не создают карточек и не будят LLM лишний раз (Task 5).
7. Известное ограничение (не баг): анонс на занятие серии, которое ещё не появилось в плане (больше 3 дней вперёд), только записывается, время серии не меняется. Описано в README (Task 10).

## Структура файлов

| Файл | Что делает |
|---|---|
| `dungeon/db.py` (изм.) | Миграция: новые колонки `events_feed` |
| `dungeon/config.py` (изм.) | Новые настройки по умолчанию |
| `dungeon/events.py` (изм.) | Афиша пропускает точечные чаты; `upcoming` только `kind='afisha'` |
| `dungeon/tgmcp.py` (изм.) | `history(..., topic_id)` для форума |
| `dungeon/game.py`, `dungeon/services.py` (изм.) | Штраф за прогул, `skip_missed`, `reopen` |
| `dungeon/texts.py`, `dungeon/caldav_sync.py` (изм.) | Поддержка `UNTIL` в `rrule` |
| `dungeon/coach.py` (изм.) | `ask_claude(..., system=)` |
| `dungeon/ritual_llm.py` (нов.) | Схема, инструкция и разбор ответа `claude -p` |
| `dungeon/rituals.py` (нов.) | Конфиг чатов, сбор, `ingest`, цели переносов, действия Некита, очереди рассылки |
| `dungeon/ritual_flows.py` (нов.) | Тексты карточек, отправка, обработка кнопок |
| `dungeon/keyboards.py`, `dungeon/bot.py`, `dungeon/app.py`, `dungeon/scheduler.py` (изм.) | Кнопки, колбэки, подключение |
| `dungeon/web.py`, `static/*` (изм.) | Раздел PWA «Афиша» |
| `config/events.yaml` (изм.) | Секция `targeted` |
| `scripts/ritual_dryrun.py` (нов.) | Пробный прогон по реальным чатам без записи в боевую БД |
| `tests/test_rituals_*.py` (нов.) | Тесты |

---

### Task 1: Миграция БД, настройки, афиша не трогает точечные чаты

**Files:**
- Modify: `dungeon/db.py` (`_migrate`)
- Modify: `dungeon/config.py` (`DEFAULT_SETTINGS`)
- Modify: `dungeon/events.py` (`collect`, `upcoming`)
- Test: `tests/test_rituals_db.py`

**Interfaces:**
- Produces: колонки `events_feed`: `kind`, `author`, `ritual`, `extra`, `status`, `task_id`, `ref_id`, `ref_task`, `notified_at`, `reminded_at`, `llm_raw`, `undo`, `needs_check`. Настройки `skip_penalty_hp`, `ritual_digest`, `ritual_urgent_h`, `ritual_remind_h`, `rituals_enabled`. `Events.upcoming` возвращает только `kind='afisha'`. `Events.collect` пропускает чаты из `config.yaml_file("events")["targeted"]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rituals_db.py
import sqlite3
from datetime import datetime, timedelta, timezone

from dungeon import events as events_mod
from dungeon.db import DB, iso
from dungeon.events import Events

TZ = timezone(timedelta(hours=7))
NEW_COLS = {"kind", "author", "ritual", "extra", "status", "task_id", "ref_id", "ref_task",
            "notified_at", "reminded_at", "llm_raw", "undo", "needs_check"}


def cols(db):
    return {r[1] for r in db.conn.execute("PRAGMA table_info(events_feed)")}


def test_fresh_db_has_ritual_columns():
    assert NEW_COLS <= cols(DB(":memory:"))


def test_old_db_is_migrated_without_data_loss(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE events_feed (id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, chat_id INTEGER, "
                "channel TEXT, msg_id INTEGER, link TEXT, starts_at TEXT NOT NULL, has_time INTEGER DEFAULT 0, "
                "title TEXT, text TEXT, category TEXT, price TEXT, vote INTEGER DEFAULT 0, created_at TEXT NOT NULL)")
    con.execute("INSERT INTO events_feed(key, starts_at, title, created_at) VALUES('k','2026-10-20T00:00:00+00:00','Квиз','x')")
    con.commit()
    con.close()
    db = DB(path)
    assert NEW_COLS <= cols(db)
    row = db.one("SELECT * FROM events_feed WHERE key='k'")
    assert row["title"] == "Квиз" and row["kind"] == "afisha" and row["status"] == "new"


def test_upcoming_ignores_ritual_rows():
    db = DB(":memory:")
    evs = Events(db)
    start = datetime(2026, 10, 14, 19, 0, tzinfo=TZ)
    for key, kind in (("a", "afisha"), ("b", "announce"), ("c", "move")):
        db.x("INSERT INTO events_feed(key, starts_at, title, kind, created_at) VALUES(?,?,?,?,?)",
             (key, iso(start), key, kind, iso(start)))
    got = evs.upcoming(start - timedelta(hours=1), start + timedelta(hours=1))
    assert [g["key"] for g in got] == ["a"]


def test_afisha_skips_targeted_chats(monkeypatch):
    import asyncio
    cfg = {"channels": [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}], "targeted": [{"id": 2, "name": "B"}],
           "categories": {}, "exclude": []}
    monkeypatch.setattr(events_mod, "cfg", lambda: cfg)
    asked = []

    async def fetch(chat_id, limit):
        asked.append(chat_id)
        return []

    asyncio.run(Events(DB(":memory:")).collect(TZ, fetch=fetch, pause=0))
    assert asked == [1]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rituals_db.py -v`
Expected: FAIL (колонок нет, `upcoming` отдаёт все строки, афиша читает чат 2).

- [ ] **Step 3: Implement**

`dungeon/db.py`: над классом `DB` добавить константу, в `_migrate` дописать цикл:

```python
RITUAL_COLS = {
    "kind": "TEXT DEFAULT 'afisha'",   # afisha | announce | move | cancel
    "author": "TEXT",
    "ritual": "TEXT",                  # имя чата из events.yaml (targeted)
    "extra": "TEXT",                   # JSON: book, place, description, variant, candidates
    "status": "TEXT DEFAULT 'new'",    # new | accepted | declined | skipped | cancelled | applied | undone | expired | ambiguous | unresolved
    "task_id": "INTEGER",
    "ref_id": "INTEGER",               # для move/cancel: какую строку events_feed двигаем
    "ref_task": "INTEGER",             # для move/cancel серии: какую задачу двигаем
    "notified_at": "TEXT",
    "reminded_at": "TEXT",
    "llm_raw": "TEXT",
    "undo": "TEXT",
    "needs_check": "INTEGER DEFAULT 0",
}
```

В конец `_migrate`:

```python
        feed = {r[1] for r in self.conn.execute("PRAGMA table_info(events_feed)")}
        for col, ddl in RITUAL_COLS.items():
            if col not in feed:
                self.conn.execute(f"ALTER TABLE events_feed ADD COLUMN {col} {ddl}")
```

`dungeon/config.py`, в конец `DEFAULT_SETTINGS`:

```python
    "skip_penalty_hp": "20",    # штраф за прогул ритуала (Некит сам выбрал «прогул»)
    "ritual_digest": "10:00,16:00,22:00",  # когда слать подборку анонсов из точечных чатов
    "ritual_urgent_h": "3",     # анонс на ближайшие N часов уходит сразу
    "ritual_remind_h": "2.5",   # одно напоминание, если карточка без ответа
    "rituals_enabled": "0",     # 1 = читать точечные чаты (включать после пробного прогона)
```

`dungeon/events.py`, в `Events.collect` первой строкой цикла:

```python
        skip = {c["id"] for c in cfg().get("targeted", [])}
        for ch in cfg()["channels"]:
            if ch["id"] in skip:  # эти чаты читает rituals.py
                continue
```
(старую строку `for ch in cfg()["channels"]:` заменить; тело цикла без изменений). В `upcoming` условие SQL стало: `"SELECT * FROM events_feed WHERE starts_at>=? AND starts_at<? AND vote>=0 AND kind='afisha'"`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rituals_db.py tests/test_events.py -v`
Expected: PASS (старые тесты афиши тоже зелёные).

- [ ] **Step 5: Commit**

```bash
git add dungeon/db.py dungeon/config.py dungeon/events.py tests/test_rituals_db.py
git commit -m "feat(rituals): events_feed columns, settings, afisha skips targeted chats"
```

---

### Task 2: Чтение форумных топиков и каркас `rituals.py` (конфиг, фильтр автора)

**Files:**
- Modify: `dungeon/tgmcp.py` (`history`)
- Create: `dungeon/rituals.py`
- Test: `tests/test_rituals_filter.py`

**Interfaces:**
- Produces: `tgmcp.history(chat_id: int, limit: int = 30, topic_id: int | None = None) -> list[dict]`. В `rituals.py`: `cfg() -> list[dict]`, `chat_cfg(chat_id) -> dict | None`, `author_ok(chat, post) -> bool`, `usable(chat, post, since: datetime | None) -> bool`, `sources(chat) -> list[tuple[int | None, str | None]]`, `chat_link(chat, msg_id) -> str`, `DAYS`, `class Rituals(db, svc)` с `feed(id)` и `tz`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rituals_filter.py
import asyncio
from datetime import datetime, timedelta, timezone

from dungeon import rituals, tgmcp
from dungeon.rituals import author_ok, chat_link, sources, usable

SINCE = datetime(2026, 10, 5, tzinfo=timezone.utc)
CHAT = {"id": -100, "name": "Библиотерапия", "authors": ["AlinaSnezhko"]}


def post(text="Следующая встреча пройдет 19.10.2026 в 11.00", user="AlinaSnezhko", date="2026-10-10T03:00:00+00:00", **kw):
    return {"id": 1, "date": date, "text": text, "username": user, **kw}


def test_author_filter_is_case_insensitive():
    assert author_ok(CHAT, post(user="alinasnezhko"))
    assert not author_ok(CHAT, post(user="Thukydides"))
    assert not author_ok(CHAT, post(user=None))


def test_channel_accepts_any_post():
    assert author_ok({"id": 1, "name": "Покер", "channel": True}, post(user=None))


def test_usable_drops_polls_actions_old_and_short():
    assert usable(CHAT, post(), SINCE)
    assert not usable(CHAT, post(text=""), SINCE)                       # опрос без текста
    assert not usable(CHAT, post(action="MessageActionChatDeleteUser"), SINCE)
    assert not usable(CHAT, post(date="2026-09-30T03:00:00+00:00"), SINCE)  # старше окна первого запуска
    assert usable(CHAT, post(date="2026-09-30T03:00:00+00:00"), None)       # окна нет: берём всё новое


def test_sources_for_forum_and_plain_chat():
    assert sources(CHAT) == [(None, None)]
    forum = {"id": 1, "name": "Волейбол", "topics": {3: "outdoor", 132: "indoor"}}
    assert sources(forum) == [(3, "outdoor"), (132, "indoor")]


def test_chat_link_public_and_private():
    assert chat_link({"id": -100, "username": "pokerdaiviet"}, 7) == "https://t.me/pokerdaiviet/7"
    assert chat_link({"id": -1003969606547}, 7) == "https://t.me/c/3969606547/7"


def test_history_passes_topic_id(monkeypatch):
    seen = {}

    async def fake_call(tool, args, timeout=30):
        seen.update(tool=tool, args=args)
        return {"results": []}

    monkeypatch.setattr(tgmcp, "call", fake_call)
    asyncio.run(tgmcp.history(-1003116878132, 30, topic_id=3))
    assert seen["args"] == {"chat_id": -1003116878132, "limit": 30, "topic_id": 3}
    asyncio.run(tgmcp.history(-100, 30))
    assert "topic_id" not in seen["args"]


def test_cfg_reads_targeted_section(monkeypatch):
    monkeypatch.setattr(rituals.config, "yaml_file", lambda name: {"targeted": [{"id": 5, "name": "X"}]})
    assert rituals.chat_cfg(5)["name"] == "X" and rituals.chat_cfg(6) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rituals_filter.py -v`
Expected: FAIL (`ImportError: cannot import name 'author_ok'`).

- [ ] **Step 3: Implement**

`dungeon/tgmcp.py`, заменить `history`:

```python
async def history(chat_id: int, limit: int = 30, topic_id: int | None = None) -> list[dict]:
    args = {"chat_id": chat_id, "limit": limit}
    if topic_id:
        args["topic_id"] = topic_id  # форум: читаем один топик
    data = await call("get_history", args)
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    raise RuntimeError(f"telegram-mcp: неожиданный ответ {str(data)[:200]}")
```

`dungeon/rituals.py`:

```python
"""Точечные чаты: анонсы ритуалов и событий от конкретных людей.

Модуль без Telegram-бота: читает чаты через telegram-mcp, кладёт строки в events_feed (kind announce/move/cancel),
меняет задачи по решениям Некита. Сообщения шлёт ritual_flows.py. Разбор постов делает ritual_llm.py.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, time, timedelta, timezone

from . import config, tgmcp
from .db import DB, iso, parse_iso
from .parser import Parsed
from .services import Service, hm

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
    return f"https://t.me/c/{str(chat['id']).removeprefix('-100')}/{msg_id}"


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
```

В `chat_link` для username берётся ключ `username` из конфига чата (в `events.yaml` он уже есть у каналов афиши).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rituals_filter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dungeon/tgmcp.py dungeon/rituals.py tests/test_rituals_filter.py
git commit -m "feat(rituals): topic-aware history, targeted chat config and author filter"
```

---

### Task 3: Штраф за прогул, `reopen`, `UNTIL` в `rrule`

**Files:**
- Modify: `dungeon/game.py` (`miss_habit`)
- Modify: `dungeon/services.py` (новые `skip_missed`, `reopen`)
- Modify: `dungeon/texts.py` (`human_rrule`)
- Modify: `dungeon/caldav_sync.py` (`_rrule_dict`, только если тест Step 1 упадёт)
- Modify: `dungeon/bot.py` (`SETTABLE`)
- Test: `tests/test_rituals_services.py`

**Interfaces:**
- Produces: `Game.miss_habit(task, hp: int | None = None) -> list[str]` (по умолчанию `HABIT_MISS_HP`); `Service.skip_missed(tid) -> tuple[list[str], sqlite3.Row | None]` (штраф `skip_penalty_hp`, статус `missed`, следующий экземпляр серии); `Service.reopen(tid) -> Row`; `texts.human_rrule` понимает `;UNTIL=`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rituals_services.py
from datetime import datetime, timedelta, timezone

from dungeon.db import DB, parse_iso
from dungeon.parser import Parsed
from dungeon.services import Service
from dungeon.texts import human_rrule

TZ = timezone(timedelta(hours=7))


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


def make():
    svc = Service(DB(":memory:"), clock=Clock(datetime(2026, 10, 12, 20, 0, tzinfo=TZ)))
    t = svc.create_parsed(Parsed(title="Йога", quadrant=2, is_habit=True,
                                 due_at=datetime(2026, 10, 13, 9, 0, tzinfo=TZ), has_time=True,
                                 rrule="FREQ=WEEKLY;BYDAY=TU,SU"), "test")
    return svc, t


def test_skip_missed_penalizes_and_continues_series():
    svc, t = make()
    msgs, nxt = svc.skip_missed(t["id"])
    assert svc.game.hero()["hp"] == 80                    # skip_penalty_hp = 20
    assert svc.get(t["id"])["status"] == "missed"
    assert parse_iso(nxt["due_at"]).astimezone(TZ).day == 18   # следующее воскресенье
    svc.clock.dt = datetime(2026, 10, 14, 0, 5, tzinfo=TZ)
    svc.rollover()
    assert svc.game.hero()["hp"] == 80                    # rollover второй штраф не берёт


def test_skip_missed_respects_setting_and_closed_tasks():
    svc, t = make()
    svc.db.set("skip_penalty_hp", "30")
    svc.skip_missed(t["id"])
    assert svc.game.hero()["hp"] == 70
    assert svc.skip_missed(t["id"]) == ([], None)         # повторно не штрафует


def test_plain_skip_has_no_penalty():
    svc, t = make()
    nxt = svc.skip(t["id"])
    assert svc.game.hero()["hp"] == 100 and nxt is not None


def test_reopen_brings_back_a_skipped_task():
    svc, t = make()
    svc.skip(t["id"])
    assert svc.reopen(t["id"])["status"] == "open"


def test_human_rrule_with_until():
    assert human_rrule("FREQ=WEEKLY;BYDAY=TU,SU;UNTIL=20261031T165959Z") == "по вт, вс"
    assert human_rrule("FREQ=WEEKLY;BYDAY=FR") == "по пт"


def test_caldav_ics_accepts_until():
    from dungeon.caldav_sync import _build_ics
    ics = _build_ics("u1", "Йога", datetime(2026, 10, 13, 9, 0, tzinfo=TZ), 60,
                     "FREQ=WEEKLY;BYDAY=TU,SU;UNTIL=20261031T165959Z")
    assert "UNTIL=20261031T165959Z" in ics
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rituals_services.py -v`
Expected: FAIL (`skip_missed`/`reopen` нет; `human_rrule` кидает `KeyError: 'SU;UNTIL=20261031T165959Z'`; caldav-тест может пройти или упасть).

- [ ] **Step 3: Implement**

`dungeon/game.py`:

```python
    def miss_habit(self, task, hp: int | None = None) -> list[str]:
        sid = task["series_id"] or task["id"]
        self.db.x("UPDATE streaks SET current=0 WHERE series_id=?", (sid,))
        self._event("missed", task["id"], task["quadrant"])
        return self._add(hp=-(HABIT_MISS_HP if hp is None else hp))
```

`dungeon/services.py`, рядом с `skip`:

```python
    def skip_missed(self, tid: int):
        """Прогул ритуала по решению Некита: штраф skip_penalty_hp, стрик в ноль, серия идёт дальше."""
        t = self.get(tid)
        if not t or t["status"] != "open":
            return [], None
        self.db.x("UPDATE tasks SET status='missed' WHERE id=?", (tid,))
        msgs = self.game.miss_habit(t, hp=int(self.db.get("skip_penalty_hp")))
        return msgs, self._spawn_next(t)

    def reopen(self, tid: int):
        """Вернуть снятую задачу в открытые (отмена отмены)."""
        self.db.x("UPDATE tasks SET status='open' WHERE id=?", (tid,))
        self._reschedule(tid)
        self._queue_calendar(tid, "upsert")
        return self.get(tid)
```

`dungeon/texts.py`, ветка `BYDAY`:

```python
    if "BYDAY=" in r:
        days = r.split("BYDAY=")[1].split(";")[0].split(",")
        return "по " + ", ".join(RU_DAYS[d] for d in days)
```

`dungeon/bot.py`, в `SETTABLE` добавить две строки (значения цифрами, `TIME_KEYS` не трогаем):
`"skip_penalty_hp": "штраф HP за прогул ритуала",` и `"rituals_enabled": "читать точечные чаты 1/0",`.

Если `test_caldav_ics_accepts_until` упал: в `dungeon/caldav_sync.py` в `_rrule_dict` добавить ветку (и `timezone` в импорт `datetime`, если его там нет):

```python
        elif key == "UNTIL":
            out[key] = datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rituals_services.py tests/test_services.py tests/test_caldav_sync.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dungeon/game.py dungeon/services.py dungeon/texts.py dungeon/caldav_sync.py dungeon/bot.py tests/test_rituals_services.py
git commit -m "feat(rituals): skip penalty, reopen, UNTIL support in rrule"
```

---

### Task 4: Контракт с `claude -p` (`ritual_llm.py`)

**Files:**
- Modify: `dungeon/coach.py` (`ask_claude`)
- Create: `dungeon/ritual_llm.py`
- Test: `tests/test_rituals_llm.py`

**Interfaces:**
- Consumes: `coach.ask_claude(instructions, data, schema, system=None)`.
- Produces: `ritual_llm.Item` (поля: `msg_id:int, kind:str, title:str, start:datetime|None, has_time:bool, orig_date:date|None, ref:str|None, place, book, description: str|None, confidence:float, needs_check:bool, raw:dict`), `parse_items(raw: dict, tz, now: datetime) -> list[Item]`, `async classify(chat: dict, posts: list[dict], known: list[dict], now: datetime, ask=None) -> list[Item]`. Элемент `posts`: `{"msg_id","date","weekday","author","topic","text"}`; элемент `known`: `{"ref","title","date"}` (`ref` вида `e12` или `t34`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rituals_llm.py
import asyncio
from datetime import date, datetime, timedelta, timezone

from dungeon import ritual_llm
from dungeon.ritual_llm import parse_items

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=TZ)


def raw(**kw):
    base = {"msg_id": 1, "kind": "announce", "title": "Встреча по целям", "date": "2026-10-19", "time": "11:00",
            "confidence": 0.9}
    return {"items": [{**base, **kw}]}


def test_announce_is_parsed_with_local_start():
    [it] = parse_items(raw(book="Atomic Habits"), TZ, NOW)
    assert it.start == datetime(2026, 10, 19, 11, 0, tzinfo=TZ) and it.has_time
    assert it.book == "Atomic Habits" and not it.needs_check


def test_announce_without_date_is_dropped():
    assert parse_items(raw(date=None), TZ, NOW) == []          # «Всем привет, ждём всех!»


def test_none_and_junk_kinds_are_dropped():
    assert parse_items(raw(kind="none"), TZ, NOW) == []
    assert parse_items({"items": [{"kind": "announce", "date": "2026-10-19"}]}, TZ, NOW) == []  # нет msg_id
    assert parse_items({}, TZ, NOW) == []


def test_low_confidence_and_past_date_need_check():
    assert parse_items(raw(confidence=0.4), TZ, NOW)[0].needs_check
    assert parse_items(raw(date="2026-10-01"), TZ, NOW)[0].needs_check


def test_cancel_may_have_no_date_but_move_may_not():
    [c] = parse_items(raw(kind="cancel", date=None, time=None), TZ, NOW)
    assert c.start is None and not c.needs_check
    assert parse_items(raw(kind="move", date=None), TZ, NOW) == []


def test_move_keeps_orig_date_and_ref():
    [m] = parse_items(raw(kind="move", date="2026-10-17", time="18:30", orig_date="2026-10-16", ref="t34"), TZ, NOW)
    assert m.orig_date == date(2026, 10, 16) and m.ref == "t34"


def test_bad_time_format_means_no_time():
    [it] = parse_items(raw(time="в 11"), TZ, NOW)
    assert not it.has_time and it.start.hour == 0


def test_classify_sends_posts_and_known_to_llm():
    calls = {}

    async def ask(instructions, data, schema, system=None):
        calls.update(instructions=instructions, data=data, schema=schema, system=system)
        return raw()

    chat = {"id": -1, "name": "Цели и привычки"}
    posts = [{"msg_id": 1, "date": "2026-09-30T10:53:45+07:00", "weekday": "среда", "author": "AlinaSnezhko",
              "topic": None, "text": "Следующая встреча пройдет 19.10.2026 в 11.00"}]
    items = asyncio.run(ritual_llm.classify(chat, posts, [], NOW, ask=ask))
    assert items[0].msg_id == 1
    assert calls["data"]["chat"] == "Цели и привычки" and calls["data"]["today"] == "2026-10-12"
    assert calls["data"]["posts"] == posts and calls["data"]["known_events"] == []
    assert "announce" in calls["instructions"] and calls["system"] == ritual_llm.SYSTEM
    assert calls["schema"]["properties"]["items"]["items"]["properties"]["kind"]["enum"] == \
        ["announce", "move", "cancel", "none"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rituals_llm.py -v`
Expected: FAIL (`ModuleNotFoundError: dungeon.ritual_llm`).

- [ ] **Step 3: Implement**

`dungeon/coach.py`: сигнатура и аргумент системного промпта:

```python
async def ask_claude(instructions: str, data: dict, schema: dict, system: str | None = None) -> dict:
```
и в списке `args`: `"--system-prompt", system or SYSTEM,` (вместо `SYSTEM`).

`dungeon/ritual_llm.py`:

```python
"""Контракт с claude -p для точечных чатов: что просим, в каком виде ответ, как его проверяем."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time

from . import coach

SYSTEM = ("Ты разбираешь посты из Telegram-чатов Нячанга (Вьетнам, UTC+7) про встречи, занятия и мероприятия. "
          "Отвечай строго по схеме. Ничего не выдумывай: чего нет в посте, там null.")

INSTRUCTIONS = """Тебе дан JSON: чат, сегодняшняя дата, новые посты (posts) и уже известные события чата (known_events).
Для КАЖДОГО поста про встречу, занятие или мероприятие верни один элемент items. Посты не про встречи (болтовня, \
благодарности, опросы) пропусти или верни kind=none.

kind:
- announce: объявлена встреча или занятие. Фраза «следующая встреча пройдет 19.10 в 11.00» тоже announce.
- move: встречу перенесли на другое время или день. date/time = НОВЫЕ, orig_date = старая дата, если она названа или \
однозначно следует из known_events.
- cancel: встречи не будет. date = дата отменённой встречи, если названа («завтра» считай от даты поста), иначе null.

Поля: msg_id (как в посте), title (коротко, 2-6 слов, что за встреча), date (YYYY-MM-DD), time (HH:MM, 24 часа, \
если время не названо то null), place, book (название книги, если встреча про разбор книги), description (о чём \
встреча, 1-2 фразы, если пост это объясняет), orig_date, ref, confidence (0..1: насколько ты уверен в дате и типе).
Даты считай от даты ПОСТА (поле date у поста, там же день недели): «завтра», «в среду», «в эту субботу».
ref: если move или cancel относится к событию из known_events, верни его ref (например e12 или t34), иначе null.
Если пост про перенос, а старая дата нигде не видна, orig_date = null, не угадывай.
Тема встречи («обсудим систему планирования 12 недель») идёт в description, а не в book. book только если названа книга."""

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "msg_id": {"type": "integer"},
            "kind": {"type": "string", "enum": ["announce", "move", "cancel", "none"]},
            "title": {"type": "string"},
            "date": {"type": ["string", "null"]},
            "time": {"type": ["string", "null"]},
            "orig_date": {"type": ["string", "null"]},
            "ref": {"type": ["string", "null"]},
            "place": {"type": ["string", "null"]},
            "book": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]},
            "confidence": {"type": "number"},
        },
        "required": ["msg_id", "kind"],
    }}},
    "required": ["items"],
}


@dataclass
class Item:
    msg_id: int
    kind: str
    title: str
    start: datetime | None
    has_time: bool
    orig_date: date | None
    ref: str | None
    place: str | None
    book: str | None
    description: str | None
    confidence: float
    needs_check: bool
    raw: dict = field(default_factory=dict)


def _date(s) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _time(s) -> time | None:
    try:
        return time.fromisoformat(s) if s else None
    except ValueError:
        return None


def parse_items(raw: dict, tz, now: datetime) -> list[Item]:
    """Проверяем ответ модели: лишнее выкидываем, сомнительное помечаем needs_check."""
    out: list[Item] = []
    for r in (raw or {}).get("items", []):
        kind, msg_id = r.get("kind"), r.get("msg_id")
        if kind not in ("announce", "move", "cancel") or not isinstance(msg_id, int):
            continue
        d, t = _date(r.get("date")), _time(r.get("time"))
        if kind in ("announce", "move") and not d:
            continue  # без даты это не анонс, а перенос без новой даты применить нечем
        start = datetime.combine(d, t or time(0, 0), tz) if d else None
        conf = float(r.get("confidence") or 0)
        past = bool(start and kind != "cancel" and start.date() < now.date())
        out.append(Item(msg_id=msg_id, kind=kind, title=(r.get("title") or "").strip() or "Встреча", start=start,
                        has_time=bool(t), orig_date=_date(r.get("orig_date")), ref=r.get("ref") or None,
                        place=r.get("place") or None, book=r.get("book") or None,
                        description=r.get("description") or None, confidence=conf,
                        needs_check=conf < 0.6 or past, raw=r))
    return out


async def classify(chat: dict, posts: list[dict], known: list[dict], now: datetime, ask=None) -> list[Item]:
    ask = ask or coach.ask_claude
    data = {"chat": chat["name"], "today": now.date().isoformat(), "posts": posts, "known_events": known}
    raw = await ask(INSTRUCTIONS, data, SCHEMA, system=SYSTEM)
    return parse_items(raw, now.tzinfo, now)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rituals_llm.py tests/test_planner.py -v`
Expected: PASS (планировщик использует `ask_claude` без `system`, должен остаться зелёным).

- [ ] **Step 5: Commit**

```bash
git add dungeon/coach.py dungeon/ritual_llm.py tests/test_rituals_llm.py
git commit -m "feat(rituals): claude -p contract for announces, moves and cancels"
```

---

### Task 5: Сбор постов, `ingest`, поиск цели переноса

**Files:**
- Modify: `dungeon/rituals.py` (добавить методы в `class Rituals` и импорты `ritual_llm`)
- Test: `tests/test_rituals_collect.py`

**Interfaces:**
- Consumes: Task 2 (`cfg`, `sources`, `usable`, `chat_link`, `DAYS`, `as_date`), Task 4 (`ritual_llm.classify`, `Item`).
- Produces: `Rituals.collect(tz, now=None, fetch=tgmcp.history, ask=None, pause=1.0) -> dict` (ключи `posts`, `items`, `errors`, `aborted`, `claude_down`); `Rituals.known(chat, now) -> list[dict]`; `Rituals.occurrence_task(chat)`; `Rituals.find_target(chat, item, now) -> tuple[int|None, int|None, list[dict]]`; `Rituals.ingest(chat, post, label, item, now) -> int`. Строки `events_feed`: `key='rt:<chat>:<msg>'`, `kind`, `status` (`new` для анонса и для разобранного переноса/отмены; `ambiguous`, `unresolved` для неразобранных).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rituals_collect.py
import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from dungeon import rituals
from dungeon.coach import ClaudeUnavailable
from dungeon.db import DB, parse_iso
from dungeon.parser import Parsed
from dungeon.rituals import Rituals
from dungeon.services import Service

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=TZ)   # понедельник
YOGA, DND, GOALS, VOLLEY, POKER = -1, -2, -3, -4, -5
CHATS = [
    {"id": YOGA, "name": "Йога FYSM", "authors": ["Panushkinn"], "series": "Йога",
     "until": date(2026, 10, 31), "attend_days": ["TU", "SU"]},
    {"id": DND, "name": "Днд неверленд", "authors": ["Mortemanes"], "series": "Настольная игра Dungeons & Dragons"},
    {"id": GOALS, "name": "Цели и привычки", "authors": ["AlinaSnezhko"]},
    {"id": VOLLEY, "name": "Волейбол", "authors": ["alex_viuk"], "topics": {3: "outdoor", 132: "indoor"}},
    {"id": POKER, "name": "Покер", "channel": True},
]


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(rituals, "cfg", lambda: CHATS)
    db = DB(":memory:")
    svc = Service(db, clock=Clock(NOW))
    return db, svc, Rituals(db, svc)


def post(pid, text="текст анонса", user="Panushkinn", date_="2026-10-12T10:00:00+00:00"):
    return {"id": pid, "date": date_, "text": text, "username": user}


def item(msg_id, kind="announce", date_="2026-10-13", time_="09:00", **kw):
    return {"msg_id": msg_id, "kind": kind, "title": "Занятие", "date": date_, "time": time_,
            "confidence": 0.9, **kw}


def fetcher(feeds):
    calls = []

    async def fetch(chat_id, limit, topic_id=None):
        calls.append((chat_id, topic_id))
        return feeds.get((chat_id, topic_id), [])

    fetch.calls = calls
    return fetch


def asker(*items, boom=None):
    calls = []

    async def ask(instructions, data, schema, system=None):
        calls.append(data)
        if boom:
            raise boom
        return {"items": list(items)}

    ask.calls = calls
    return ask


def run(r, fetch, ask):
    return asyncio.run(r.collect(TZ, now=NOW, fetch=fetch, ask=ask, pause=0))


def rows(db, kind="announce"):
    return db.q("SELECT * FROM events_feed WHERE kind=? ORDER BY id", (kind,))


def dnd_task(svc):
    return svc.create_parsed(Parsed(title="Настольная игра Dungeons & Dragons", quadrant=2, is_habit=True,
                                    due_at=datetime(2026, 10, 16, 18, 30, tzinfo=TZ), has_time=True,
                                    rrule="FREQ=WEEKLY;BYDAY=FR"), "t")


def test_no_posts_from_authors_means_no_llm_call(env):
    db, _, r = env
    ask = asker(boom=AssertionError("LLM не должен вызываться"))
    fetch = fetcher({(GOALS, None): [post(1, user="Thukydides")]})
    stats = run(r, fetch, ask)
    assert stats["items"] == 0 and ask.calls == []
    assert db.get("rt_last:-3:0") == "1"                        # курсор двинулся, повторно не читаем


def test_announce_is_stored_with_extra(env):
    db, _, r = env
    ask = asker(item(1, date_="2026-10-19", time_="11:00", book="Atomic Habits", place="Кафе"))
    run(r, fetcher({(GOALS, None): [post(1, user="AlinaSnezhko")]}), ask)
    [row] = rows(db)
    assert row["status"] == "new" and row["ritual"] == "Цели и привычки" and row["has_time"] == 1
    assert json.loads(row["extra"]) == {"book": "Atomic Habits", "place": "Кафе"}
    assert row["link"] == "https://t.me/c/3/1" and row["author"] == "AlinaSnezhko"
    assert parse_iso(row["starts_at"]).astimezone(TZ) == datetime(2026, 10, 19, 11, 0, tzinfo=TZ)


def test_second_run_does_not_reread_or_duplicate(env):
    db, _, r = env
    fetch = fetcher({(GOALS, None): [post(1, user="AlinaSnezhko")]})
    run(r, fetch, asker(item(1)))
    stats = run(r, fetch, asker(boom=AssertionError("LLM не должен вызываться")))
    assert stats["posts"] == 0 and len(rows(db)) == 1


def test_edited_or_repeated_announce_makes_no_second_card(env):
    db, _, r = env
    feeds = {(GOALS, None): [post(1, user="AlinaSnezhko"), post(2, "Напоминаю про встречу", "AlinaSnezhko")]}
    run(r, fetcher(feeds), asker(item(1), item(2)))              # оба поста про один и тот же старт
    assert len(rows(db)) == 1


def test_yoga_friday_and_after_until_are_ignored(env):
    db, _, r = env
    feeds = {(YOGA, None): [post(1), post(2), post(3), post(4)]}
    ask = asker(item(1, date_="2026-10-13"),                     # вторник: наш день
                item(2, date_="2026-10-16", time_="18:00"),      # пятница: занята D&D
                item(3, date_="2026-10-18"),                     # воскресенье: наш день
                item(4, date_="2026-11-03"))                     # после until
    run(r, fetcher(feeds), ask)
    assert [parse_iso(x["starts_at"]).astimezone(TZ).day for x in rows(db)] == [13, 18]


def test_volleyball_reads_topics_and_marks_variant(env):
    db, _, r = env
    fetch = fetcher({(VOLLEY, 3): [post(10, user="alex_viuk")], (VOLLEY, 132): [post(11, user="alex_viuk")]})
    ask = asker(item(10, time_="18:00"), item(11, date_="2026-10-14", time_="20:00"))
    run(r, fetch, ask)
    assert (VOLLEY, 3) in fetch.calls and (VOLLEY, 132) in fetch.calls
    variants = {json.loads(x["extra"])["variant"] for x in rows(db)}
    assert variants == {"outdoor", "indoor"}
    assert db.get("rt_last:-4:3") == "10" and db.get("rt_last:-4:132") == "11"


def test_first_run_only_looks_back_seven_days(env):
    db, _, r = env
    feeds = {(POKER, None): [post(1, user=None, date_="2026-10-01T10:00:00+00:00"),
                             post(2, user=None, date_="2026-10-11T10:00:00+00:00")]}
    ask = asker(item(2))
    run(r, fetcher(feeds), ask)
    assert [p["msg_id"] for p in ask.calls[0]["posts"]] == [2]


def test_llm_failure_keeps_cursor_so_posts_are_not_lost(env):
    db, _, r = env
    feeds = {(GOALS, None): [post(1, user="AlinaSnezhko")]}
    stats = run(r, fetcher(feeds), asker(boom=ClaudeUnavailable("лимит", limit=True)))
    assert stats["errors"] == 1 and stats["claude_down"] and db.get("rt_last:-3:0") in (None, "0")
    run(r, fetcher(feeds), asker(item(1)))                       # claude ожил: пост не потерялся
    assert len(rows(db)) == 1


def test_telegram_failure_aborts_after_two_chats(env):
    db, _, r = env

    async def fetch(chat_id, limit, topic_id=None):
        raise RuntimeError("mcp лежит")

    stats = run(r, fetch, asker(boom=AssertionError("не должен вызываться")))
    assert stats["errors"] == 2 and stats["aborted"]


def test_move_of_series_occurrence_is_resolved_by_ref(env):
    db, svc, r = env
    t = dnd_task(svc)
    feeds = {(DND, None): [post(1, "Переносим на субботу", "Mortemanes")]}
    ask = asker(item(1, kind="move", date_="2026-10-17", time_="18:30", orig_date="2026-10-16", ref=f"t{t['id']}"))
    run(r, fetcher(feeds), ask)
    [row] = rows(db, "move")
    assert row["status"] == "new" and row["ref_task"] == t["id"] and row["ref_id"] is None
    assert ask.calls[0]["known_events"][0]["ref"] == f"t{t['id']}"


def test_move_without_orig_date_and_two_candidates_is_ambiguous(env):
    db, svc, r = env
    for d, pid in (("2026-10-20", 1), ("2026-10-27", 2)):
        run(r, fetcher({(GOALS, None): [post(pid, user="AlinaSnezhko")]}), asker(item(pid, date_=d)))
    run(r, fetcher({(GOALS, None): [post(3, "Перенос", "AlinaSnezhko")]}),
        asker(item(3, kind="move", date_="2026-10-22")))
    [row] = rows(db, "move")
    assert row["status"] == "ambiguous" and row["ref_id"] is None
    assert len(json.loads(row["extra"])["candidates"]) == 2


def test_move_matches_announce_by_orig_date(env):
    db, svc, r = env
    run(r, fetcher({(GOALS, None): [post(1, user="AlinaSnezhko")]}), asker(item(1, date_="2026-10-19", time_="11:00")))
    [ev] = rows(db)
    run(r, fetcher({(GOALS, None): [post(2, "Перенос", "AlinaSnezhko")]}),
        asker(item(2, kind="move", date_="2026-10-20", time_="11:00", orig_date="2026-10-19")))
    [mv] = rows(db, "move")
    assert mv["status"] == "new" and mv["ref_id"] == ev["id"]


def test_cancel_with_nothing_to_cancel_is_unresolved(env):
    db, _, r = env
    run(r, fetcher({(GOALS, None): [post(1, "Встречи не будет", "AlinaSnezhko")]}),
        asker(item(1, kind="cancel", date_=None, time_=None)))
    [row] = rows(db, "cancel")
    assert row["status"] == "unresolved"


def test_friday_yoga_cancel_is_ignored(env):
    db, _, r = env
    run(r, fetcher({(YOGA, None): [post(1, "В пятницу занятия не будет")]}),
        asker(item(1, kind="cancel", date_="2026-10-16", time_=None)))
    assert rows(db, "cancel") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rituals_collect.py -v`
Expected: FAIL (`AttributeError: 'Rituals' object has no attribute 'collect'`).

- [ ] **Step 3: Implement**

В `dungeon/rituals.py` в импорт добавить `ritual_llm`: `from . import config, ritual_llm, tgmcp`. Методы внутри `class Rituals`:

```python
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
            out.append({"ref": f"t{t['id']}", "title": t["title"], "date": fmt(t["due_at"])})
        return out

    # ---------- сбор ----------
    def _last_key(self, chat_id: int, topic: int | None) -> str:
        return f"rt_last:{chat_id}:{topic or 0}"

    async def collect(self, tz, now: datetime | None = None, fetch=tgmcp.history, ask=None, pause: float = 1.0) -> dict:
        """Пройти по точечным чатам, разобрать новые посты нужных авторов. LLM зовём только если есть что разбирать."""
        import asyncio
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
                    posts = await fetch(chat["id"], 30 if last else 100, topic)
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
                for it in items:
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

    def find_target(self, chat: dict, it, now: datetime):
        """Какое известное событие двигают или отменяют. Возвращает (ref_id, ref_task, кандидаты)."""
        cands = self.known(chat, now)
        by_ref = {c["ref"]: c for c in cands}
        pick = None
        if it.ref in by_ref:
            pick = it.ref
        else:
            day = it.orig_date or (it.start.date() if it.kind == "cancel" and it.start else None)
            pool = [c for c in cands if c["date"][:10] == day.isoformat()] if day else cands
            if len(pool) == 1:
                pick = pool[0]["ref"]
        if not pick:
            return None, None, cands
        return (int(pick[1:]), None, cands) if pick[0] == "e" else (None, int(pick[1:]), cands)

    def _insert(self, base: dict, **cols) -> int:
        from .db import utcnow
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
        base = {"key": key, "chat_id": chat["id"], "channel": chat["name"], "msg_id": post["id"],
                "link": chat_link(chat, post["id"]), "title": it.title[:120], "text": (post.get("text") or "")[:700],
                "author": post.get("username"), "ritual": chat["name"], "kind": it.kind,
                "llm_raw": json.dumps(it.raw, ensure_ascii=False), "needs_check": int(it.needs_check)}
        if it.kind == "announce":
            if self.db.one("SELECT 1 FROM events_feed WHERE chat_id=? AND kind='announce' AND starts_at=? "
                           "AND status NOT IN ('declined','expired')", (chat["id"], iso(it.start))):
                return 0  # автор поправил или повторил анонс
            self._insert(base, extra=json.dumps(extra, ensure_ascii=False), status="new",
                         starts_at=iso(it.start), has_time=int(it.has_time))
            return 1
        ref_id, ref_task, cands = self.find_target(chat, it, now)
        status = "new" if (ref_id or ref_task) else ("ambiguous" if cands else "unresolved")
        if status == "ambiguous":
            extra["candidates"] = cands
        start = it.start or self._target_start(ref_id, ref_task) or datetime.fromisoformat(post["date"])
        self._insert(base, extra=json.dumps(extra, ensure_ascii=False), status=status, starts_at=iso(start),
                     has_time=int(it.has_time), ref_id=ref_id, ref_task=ref_task)
        return 1

    def _target_start(self, ref_id: int | None, ref_task: int | None) -> datetime | None:
        if ref_id and (e := self.feed(ref_id)):
            return parse_iso(e["starts_at"])
        if ref_task and (t := self.svc.get(ref_task)) and t["due_at"]:
            return parse_iso(t["due_at"])
        return None
```

`import asyncio` вынести наверх модуля (рядом с `json`), локальный импорт в `collect` и `from .db import utcnow` в `_insert` заменить на верхнеуровневые: `import asyncio` и `from .db import DB, iso, parse_iso, utcnow`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rituals_collect.py -v`
Expected: PASS (все 14).

- [ ] **Step 5: Commit**

```bash
git add dungeon/rituals.py tests/test_rituals_collect.py
git commit -m "feat(rituals): collect posts, ingest announces, resolve move and cancel targets"
```

---

### Task 6: Действия: применить перенос/отмену, отмена действия, «Иду», ритуал, прогул, очереди рассылки

**Files:**
- Modify: `dungeon/rituals.py` (методы в `class Rituals`)
- Test: `tests/test_rituals_actions.py`

**Interfaces:**
- Consumes: Task 3 (`svc.skip_missed`, `svc.skip`, `svc.reopen`, `svc.set_due`, `svc.delete`), Task 5 (строки `events_feed`).
- Produces (все синхронные):
  - `new_changes() -> list[Row]` (move/cancel со `status='new'`), `apply_change(row_id) -> list[int]` (ids затронутых задач), `undo(row_id) -> list[int]`, `pick_target(row_id, ref: str) -> None` (разрешает `ambiguous`: `ref` вида `e12`/`t34`, ставит `status='new'`).
  - `accept(event_id) -> dict` (`{"task_id": int|None, "title": str, "text": str}`), `make_ritual(event_id) -> dict` (то же), `decline(event_id) -> None`, `skip(event_id, bad: bool) -> tuple[list[str], list[int]]` (сообщения игры, затронутые задачи), `retime(event_id, due: datetime, has_time: bool) -> None`, `occurrence_near(chat, start) -> Row | None`, `series_exists(chat) -> bool`.
  - Очереди: `unsent_changes() -> list[Row]`, `unsent_cards(now, within=None) -> list[Row]`, `reminders(now) -> list[Row]`, `mark_sent(row_id, now)`, `mark_reminded(row_id, now)`, `expire(now)`, `digest_due(now) -> bool`, `listing(now) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rituals_actions.py
import json
from datetime import date, datetime, timedelta, timezone

import pytest
from dateutil.rrule import rrulestr

from dungeon import rituals
from dungeon.db import DB, iso, parse_iso
from dungeon.parser import Parsed
from dungeon.rituals import Rituals
from dungeon.services import Service

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=TZ)
YOGA, DND, GOALS = -1, -2, -3
CHATS = [
    {"id": YOGA, "name": "Йога FYSM", "authors": ["Panushkinn"], "series": "Йога",
     "until": date(2026, 10, 31), "attend_days": ["TU", "SU"]},
    {"id": DND, "name": "Днд неверленд", "authors": ["Mortemanes"], "series": "Настольная игра Dungeons & Dragons"},
    {"id": GOALS, "name": "Цели и привычки", "authors": ["AlinaSnezhko"]},
]


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(rituals, "cfg", lambda: CHATS)
    db = DB(":memory:")
    svc = Service(db, clock=Clock(NOW))
    return db, svc, Rituals(db, svc)


def dnd(svc):
    return svc.create_parsed(Parsed(title="Настольная игра Dungeons & Dragons", quadrant=2, is_habit=True,
                                    due_at=datetime(2026, 10, 16, 18, 30, tzinfo=TZ), has_time=True,
                                    rrule="FREQ=WEEKLY;BYDAY=FR"), "t")


def add(db, chat, start, kind="announce", status="new", title="Занятие", has_time=1, **cols):
    data = {"key": f"k{db.one('SELECT COUNT(*) c FROM events_feed')['c']}", "chat_id": chat,
            "channel": "c", "msg_id": 1, "link": "https://t.me/x/1", "starts_at": iso(start), "has_time": has_time,
            "title": title, "text": "t", "kind": kind, "status": status, "ritual": "r", "extra": "{}",
            "created_at": iso(NOW), **cols}
    return db.x(f"INSERT INTO events_feed({','.join(data)}) VALUES({','.join('?' * len(data))})", tuple(data.values()))


def due(svc, tid):
    return parse_iso(svc.get(tid)["due_at"]).astimezone(TZ)


# ---------- перенос и отмена ----------
def test_move_series_occurrence_and_undo(env):
    db, svc, r = env
    t = dnd(svc)
    mv = add(db, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    assert r.new_changes()[0]["id"] == mv
    assert r.apply_change(mv) == [t["id"]]
    assert due(svc, t["id"]) == datetime(2026, 10, 17, 18, 30, tzinfo=TZ)
    assert r.feed(mv)["status"] == "applied"
    assert r.undo(mv) == [t["id"]]
    assert due(svc, t["id"]) == datetime(2026, 10, 16, 18, 30, tzinfo=TZ)
    assert r.feed(mv)["status"] == "undone" and r.undo(mv) == []   # второй раз ничего не делает


def test_move_without_time_keeps_time_of_the_lesson(env):
    db, svc, r = env
    t = dnd(svc)
    mv = add(db, DND, datetime(2026, 10, 17, 0, 0, tzinfo=TZ), kind="move", ref_task=t["id"], has_time=0)
    r.apply_change(mv)
    assert due(svc, t["id"]) == datetime(2026, 10, 17, 18, 30, tzinfo=TZ)


def test_move_announce_row_also_moves_its_accepted_task(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    res = r.accept(ev)
    mv = add(db, GOALS, datetime(2026, 10, 20, 11, 0, tzinfo=TZ), kind="move", ref_id=ev)
    r.apply_change(mv)
    assert parse_iso(r.feed(ev)["starts_at"]).astimezone(TZ).day == 20
    assert due(svc, res["task_id"]).day == 20


def test_cancel_series_occurrence_has_no_penalty_and_series_continues(env):
    db, svc, r = env
    t = dnd(svc)
    cx = add(db, DND, datetime(2026, 10, 16, 18, 30, tzinfo=TZ), kind="cancel", ref_task=t["id"])
    touched = r.apply_change(cx)
    assert svc.get(t["id"])["status"] == "skipped" and svc.game.hero()["hp"] == 100
    nxt = [x for x in touched if x != t["id"]][0]
    assert due(svc, nxt).day == 23
    r.undo(cx)
    assert svc.get(t["id"])["status"] == "open" and svc.get(nxt)["status"] == "deleted"


def test_cancel_announce_row_removes_one_off_task(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    res = r.accept(ev)
    cx = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), kind="cancel", ref_id=ev)
    r.apply_change(cx)
    assert r.feed(ev)["status"] == "cancelled" and svc.get(res["task_id"])["status"] == "deleted"
    r.undo(cx)
    assert r.feed(ev)["status"] == "accepted" and svc.get(res["task_id"])["status"] == "open"


def test_pick_target_resolves_ambiguous_row(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    mv = add(db, GOALS, datetime(2026, 10, 20, 11, 0, tzinfo=TZ), kind="move", status="ambiguous")
    r.pick_target(mv, f"e{ev}")
    row = r.feed(mv)
    assert row["status"] == "new" and row["ref_id"] == ev


# ---------- решения Некита ----------
def test_accept_confirms_series_occurrence_and_changes_its_time(env):
    db, svc, r = env
    t = dnd(svc)
    ev = add(db, DND, datetime(2026, 10, 16, 19, 0, tzinfo=TZ))
    res = r.accept(ev)
    assert res["task_id"] == t["id"] and due(svc, t["id"]).hour == 19
    assert r.feed(ev)["status"] == "accepted" and "https://t.me/x/1" in svc.get(t["id"])["notes"]


def test_accept_far_announce_is_recorded_without_touching_series(env):
    db, svc, r = env
    t = dnd(svc)
    ev = add(db, DND, datetime(2026, 11, 20, 19, 0, tzinfo=TZ))
    res = r.accept(ev)
    assert res["task_id"] is None and due(svc, t["id"]).day == 16
    assert r.feed(ev)["status"] == "accepted"


def test_accept_plain_chat_creates_one_off_task(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), title="Встреча по 12 неделям",
             extra=json.dumps({"book": "Atomic Habits"}))
    res = r.accept(ev)
    t = svc.get(res["task_id"])
    assert t["title"] == "Встреча по 12 неделям" and not t["is_habit"] and due(svc, t["id"]).hour == 11
    assert "Atomic Habits" in t["notes"]


def test_make_ritual_in_plain_chat_creates_weekly_series(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    t = svc.get(r.make_ritual(ev)["task_id"])
    assert t["is_habit"] and t["rrule"] == "FREQ=WEEKLY;BYDAY=MO" and t["title"] == "Цели и привычки"


def test_yoga_series_is_created_from_first_announce_tue_sun_until_oct_31(env):
    db, svc, r = env
    ev = add(db, YOGA, datetime(2026, 10, 13, 9, 0, tzinfo=TZ), title="Йога")
    assert not r.series_exists(rituals.chat_cfg(YOGA))
    t = svc.get(r.make_ritual(ev)["task_id"])
    assert t["title"] == "Йога" and t["rrule"].startswith("FREQ=WEEKLY;BYDAY=TU,SU;UNTIL=")
    days = [d.astimezone(TZ) for d in rrulestr(t["rrule"], dtstart=due(svc, t["id"]))]
    assert [d.day for d in days] == [13, 18, 20, 25, 27]        # вторники и воскресенья, 1 ноября уже нет
    assert r.series_exists(rituals.chat_cfg(YOGA))


def test_skip_bad_reason_costs_20_hp_good_reason_is_free(env):
    db, svc, r = env
    t = dnd(svc)
    ev = add(db, DND, datetime(2026, 10, 16, 18, 30, tzinfo=TZ))
    msgs, touched = r.skip(ev, bad=True)
    assert svc.game.hero()["hp"] == 80 and svc.get(t["id"])["status"] == "missed" and t["id"] in touched
    assert r.feed(ev)["status"] == "skipped"
    t2 = [x for x in svc.open_tasks() if x["is_habit"]][0]
    ev2 = add(db, DND, parse_iso(t2["due_at"]))
    r.skip(ev2, bad=False)
    assert svc.game.hero()["hp"] == 80 and svc.get(t2["id"])["status"] == "skipped"


def test_skip_one_off_deletes_task_without_penalty(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    res = r.accept(ev)
    r.skip(ev, bad=True)
    assert svc.get(res["task_id"])["status"] == "deleted" and svc.game.hero()["hp"] == 100


def test_decline_and_retime(env):
    db, svc, r = env
    ev = add(db, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), needs_check=1)
    res = r.accept(ev)
    r.retime(ev, datetime(2026, 10, 19, 12, 30, tzinfo=TZ), True)
    assert due(svc, res["task_id"]).hour == 12 and r.feed(ev)["needs_check"] == 0
    ev2 = add(db, GOALS, datetime(2026, 10, 26, 11, 0, tzinfo=TZ))
    r.decline(ev2)
    assert r.feed(ev2)["status"] == "declined"


# ---------- очереди рассылки ----------
def test_digest_slots_fire_once_each(env):
    db, _, r = env
    at = lambda h, m=0: datetime(2026, 10, 12, h, m, tzinfo=TZ)  # noqa: E731
    assert not r.digest_due(at(9, 59))
    assert r.digest_due(at(10, 0)) and not r.digest_due(at(10, 1))
    assert not r.digest_due(at(15, 59))
    assert r.digest_due(at(16, 0)) and r.digest_due(at(22, 5)) and not r.digest_due(at(23, 0))


def test_cards_urgent_reminder_expire(env):
    db, _, r = env
    soon = add(db, GOALS, NOW + timedelta(hours=2))
    later = add(db, GOALS, NOW + timedelta(days=2))
    past = add(db, GOALS, NOW - timedelta(hours=5))
    assert {x["id"] for x in r.unsent_cards(NOW)} == {soon, later}
    assert [x["id"] for x in r.unsent_cards(NOW, within=timedelta(hours=3))] == [soon]
    r.expire(NOW)
    assert r.feed(past)["status"] == "expired"
    r.mark_sent(later, NOW - timedelta(hours=3))
    assert [x["id"] for x in r.reminders(NOW)] == [later]
    r.mark_reminded(later, NOW)
    assert r.reminders(NOW) == []
    assert later not in {x["id"] for x in r.unsent_cards(NOW)}


def test_date_only_event_today_is_not_expired(env):
    db, _, r = env
    ev = add(db, GOALS, datetime(2026, 10, 12, 0, 0, tzinfo=TZ), has_time=0)
    r.expire(NOW)
    assert r.feed(ev)["status"] == "new" and ev in {x["id"] for x in r.unsent_cards(NOW)}


def test_unsent_changes_and_listing(env):
    db, svc, r = env
    t = dnd(svc)
    mv = add(db, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    r.apply_change(mv)
    amb = add(db, GOALS, NOW, kind="move", status="ambiguous")
    assert {x["id"] for x in r.unsent_changes()} == {mv, amb}
    r.mark_sent(mv, NOW)
    assert {x["id"] for x in r.unsent_changes()} == {amb}
    ev = add(db, GOALS, NOW + timedelta(days=1), title="Квиз", extra=json.dumps({"book": "Книга"}))
    [item] = r.listing(NOW)
    assert item["id"] == ev and item["title"] == "Квиз" and "Книга" in item["extra"] and item["status"] == "new"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rituals_actions.py -v`
Expected: FAIL (`AttributeError: 'Rituals' object has no attribute 'new_changes'`).

- [ ] **Step 3: Implement**

Методы в `class Rituals` (`dungeon/rituals.py`):

```python
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
        t = self.svc.get(ev["task_id"] if ev else row["ref_task"]) if (ev or row["ref_task"]) else None
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
        chat = chat_cfg(ev["chat_id"]) or {}
        start = parse_iso(ev["starts_at"]).astimezone(self.tz)
        t = self.occurrence_near(chat, start)
        if t:
            when, has = self._merge_time(start, bool(ev["has_time"]), bool(t["has_time"]), t["due_at"])
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
        self.db.x("UPDATE events_feed SET status='declined' WHERE id=?", (event_id,))

    def skip(self, event_id: int, bad: bool) -> tuple[list[str], list[int]]:
        """Некит пропускает занятие. Прогул (bad) штрафуется, важная причина нет. Разовая задача просто удаляется."""
        ev = self.feed(event_id)
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
    _ALIVE = "((has_time=1 AND starts_at>=:now) OR (has_time=0 AND starts_at>=:day))"

    def _times(self, now: datetime) -> dict:
        return {"now": iso(now), "day": iso(now.replace(hour=0, minute=0, second=0, microsecond=0))}

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
        t = self._times(now)
        self.db.x("UPDATE events_feed SET status='expired' WHERE kind='announce' AND status='new' AND "
                  "((has_time=1 AND starts_at<:now) OR (has_time=0 AND starts_at<:day))", t)

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rituals_actions.py tests/test_rituals_collect.py tests/test_services.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add dungeon/rituals.py tests/test_rituals_actions.py
git commit -m "feat(rituals): apply moves and cancels with undo, accept, series, skip reasons, mailing queues"
```

---

### Task 7: Бот: карточки, кнопки, подборки, подключение в тикер

**Files:**
- Create: `dungeon/ritual_flows.py`
- Modify: `dungeon/keyboards.py` (клавиатуры)
- Modify: `dungeon/app.py` (`self.rituals`, `self.rituals_running`)
- Modify: `dungeon/bot.py` (колбэки `rt:*`, ответ на карточку «✏ Поправить»)
- Modify: `dungeon/scheduler.py` (сбор раз в час + `ritual_flows.tick`)
- Test: `tests/test_rituals_flows.py`

**Interfaces:**
- Consumes: всё из Task 6; `app.send(text, kb) -> Message | None`, `app.changed(task_id)`, `app.announce(msgs)`.
- Produces:
  - `keyboards.ritual_card(row_id, url, mode)` (`mode` из `plain | occ | new_series`), `keyboards.ritual_reason(row_id, penalty)`, `keyboards.ritual_undo(row_id)`, `keyboards.ritual_pick(row_id, cands)`; callback_data `rt:<act>:<id>[:<ref>]`, act из `ok ser mk no sk sb sg un fix pk`.
  - `ritual_flows.card_text(app, row, now) -> str` (содержит маркер `[a<row_id>]`), `mode_for(app, row) -> str`, `async send_card(app, row, now)`, `async send_change(app, row, now)`, `async tick(app, now, quiet)`, `async collect(app)`, `async handle(app, act, row_id, arg) -> tuple[str, str | None]` (текст ответа и необязательный новый текст), `async retime_from_reply(app, row_id, text) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rituals_flows.py
import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dungeon import keyboards, ritual_flows, rituals
from dungeon.db import DB, iso
from dungeon.parser import Parsed
from dungeon.rituals import Rituals
from dungeon.services import Service

TZ = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 12, 20, 0, tzinfo=TZ)
T0 = datetime(2026, 10, 12, 9, 0, tzinfo=TZ)     # до первого слота подборки (10:00)
YOGA, DND, GOALS = -1, -2, -3
CHATS = [
    {"id": YOGA, "name": "Йога FYSM", "authors": ["Panushkinn"], "series": "Йога",
     "until": date(2026, 10, 31), "attend_days": ["TU", "SU"]},
    {"id": DND, "name": "Днд неверленд", "authors": ["Mortemanes"], "series": "Настольная игра Dungeons & Dragons"},
    {"id": GOALS, "name": "Цели и привычки", "authors": ["AlinaSnezhko"]},
]


class Clock:
    def __init__(self, dt):
        self.dt = dt

    def __call__(self):
        return self.dt


class FakeApp:
    def __init__(self):
        self.db = DB(":memory:")
        self.svc = Service(self.db, clock=Clock(NOW))
        self.rituals = Rituals(self.db, self.svc)
        self.sent, self.changed_ids, self.rituals_running = [], [], False

    async def send(self, text, kb=None, silent=False):
        self.sent.append((text, kb))
        return object()

    async def changed(self, tid):
        self.changed_ids.append(tid)

    async def announce(self, msgs):
        if msgs:
            self.sent.append(("\n".join(msgs), None))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setattr(rituals, "cfg", lambda: CHATS)
    a = FakeApp()
    a.db.set("rituals_enabled", "1")
    return a


def add(app, chat, start, kind="announce", status="new", title="Занятие", has_time=1, **cols):
    n = app.db.one("SELECT COUNT(*) c FROM events_feed")["c"]
    data = {"key": f"k{n}", "chat_id": chat, "channel": "c", "msg_id": 1, "link": "https://t.me/x/1",
            "starts_at": iso(start), "has_time": has_time, "title": title, "text": "t", "kind": kind,
            "status": status, "ritual": "r", "extra": "{}", "created_at": iso(NOW), **cols}
    return app.db.x(f"INSERT INTO events_feed({','.join(data)}) VALUES({','.join('?' * len(data))})",
                    tuple(data.values()))


def dnd(app):
    return app.svc.create_parsed(Parsed(title="Настольная игра Dungeons & Dragons", quadrant=2, is_habit=True,
                                        due_at=datetime(2026, 10, 16, 18, 30, tzinfo=TZ), has_time=True,
                                        rrule="FREQ=WEEKLY;BYDAY=FR"), "t")


def labels(kb):
    return [b.text for row in kb.inline_keyboard for b in row]


def data(kb):
    return [b.callback_data for row in kb.inline_keyboard for b in row if b.callback_data]


def run(coro):
    return asyncio.run(coro)


def test_mode_depends_on_series_state(app):
    ev = add(app, GOALS, NOW + timedelta(days=1))
    assert ritual_flows.mode_for(app, app.rituals.feed(ev)) == "plain"
    y = add(app, YOGA, datetime(2026, 10, 13, 9, 0, tzinfo=TZ))
    assert ritual_flows.mode_for(app, app.rituals.feed(y)) == "new_series"
    dnd(app)
    d = add(app, DND, datetime(2026, 10, 16, 19, 0, tzinfo=TZ))
    assert ritual_flows.mode_for(app, app.rituals.feed(d)) == "occ"


def test_card_text_and_keyboards(app):
    ev = add(app, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), title="Встреча по 12 неделям",
             extra=json.dumps({"book": "Atomic Habits"}), needs_check=1)
    row = app.rituals.feed(ev)
    text = ritual_flows.card_text(app, row, NOW)
    assert "Встреча по 12 неделям" in text and "пн 19.10 11:00" in text and "Atomic Habits" in text
    assert "проверь" in text and f"[a{ev}]" in text and "#" not in text      # «#123» ловил бы редактор задач
    assert "—" not in text
    kb = keyboards.ritual_card(ev, row["link"], "plain")
    assert data(kb) == [f"rt:ok:{ev}", f"rt:mk:{ev}", f"rt:no:{ev}", f"rt:fix:{ev}"]
    assert labels(keyboards.ritual_card(ev, None, "occ"))[:2] == ["✅ Подтвердить", "❌ Пропускаю"]
    assert "🕯 Завести ритуал" in labels(keyboards.ritual_card(ev, None, "new_series"))
    assert "20 HP" in labels(keyboards.ritual_reason(ev, 20))[0]


def test_card_text_warns_about_overlap(app):
    app.svc.create_parsed(Parsed(title="Созвон", quadrant=1, due_at=datetime(2026, 10, 19, 11, 30, tzinfo=TZ),
                                 has_time=True), "t")
    ev = add(app, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    assert "Созвон" in ritual_flows.card_text(app, app.rituals.feed(ev), NOW)


def test_tick_disabled_or_quiet_sends_nothing(app):
    add(app, GOALS, NOW + timedelta(hours=1))
    app.db.set("rituals_enabled", "0")
    run(ritual_flows.tick(app, NOW, quiet=False))
    assert app.sent == []
    app.db.set("rituals_enabled", "1")
    run(ritual_flows.tick(app, NOW, quiet=True))
    assert app.sent == []


def test_urgent_card_goes_immediately_and_only_once(app):
    soon = add(app, GOALS, T0 + timedelta(hours=1))
    far = add(app, GOALS, T0 + timedelta(days=3))
    run(ritual_flows.tick(app, T0, quiet=False))              # 09:00, до первого слота подборки: уходит только срочное
    assert len(app.sent) == 1 and f"rt:ok:{soon}" in data(app.sent[0][1])
    run(ritual_flows.tick(app, T0, quiet=False))
    assert len(app.sent) == 1                                 # второй раз не шлём
    assert app.rituals.feed(far)["notified_at"] is None


def test_digest_sends_header_and_cards_once_per_slot(app):
    far = add(app, GOALS, NOW + timedelta(days=3))
    run(ritual_flows.tick(app, NOW, quiet=False))                             # 20:00: догнали слот 16:00
    assert len(app.sent) == 2 and "Анонсы из чатов: 1" in app.sent[0][0]
    assert app.rituals.feed(far)["notified_at"] is not None
    run(ritual_flows.tick(app, NOW + timedelta(minutes=5), quiet=False))
    assert len(app.sent) == 2


def test_empty_digest_is_not_sent(app):
    run(ritual_flows.tick(app, NOW, quiet=False))
    assert app.sent == []


def test_reminder_once_for_unanswered_card(app):
    add(app, GOALS, T0 + timedelta(days=2), notified_at=iso(T0 - timedelta(hours=3)))
    run(ritual_flows.tick(app, T0, quiet=False))
    assert len(app.sent) == 1 and "Ещё не ответил" in app.sent[0][0]
    run(ritual_flows.tick(app, T0, quiet=False))
    assert len(app.sent) == 1


def test_move_is_applied_and_reported_with_undo_button(app):
    t = dnd(app)
    mv = add(app, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    run(ritual_flows.tick(app, NOW, quiet=False))
    assert t["id"] in app.changed_ids
    text, kb = app.sent[0]
    assert "Перенёс" in text and "пт 16.10 18:30" in text and "сб 17.10 18:30" in text
    assert data(kb) == [f"rt:un:{mv}"]


def test_move_is_applied_during_quiet_hours_but_reported_after(app):
    t = dnd(app)
    add(app, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    run(ritual_flows.tick(app, NOW, quiet=True))
    assert app.sent == [] and app.svc.get(t["id"])["due_at"].startswith("2026-10-17")
    run(ritual_flows.tick(app, NOW, quiet=False))
    assert len(app.sent) == 1


def test_ambiguous_move_asks_with_candidate_buttons(app):
    a = add(app, GOALS, datetime(2026, 10, 20, 11, 0, tzinfo=TZ), title="Первая")
    b = add(app, GOALS, datetime(2026, 10, 27, 11, 0, tzinfo=TZ), title="Вторая")
    mv = add(app, GOALS, datetime(2026, 10, 22, 11, 0, tzinfo=TZ), kind="move", status="ambiguous",
             extra=json.dumps({"candidates": [{"ref": f"e{a}", "title": "Первая", "date": "2026-10-20 11:00"},
                                               {"ref": f"e{b}", "title": "Вторая", "date": "2026-10-27 11:00"}]}))
    run(ritual_flows.tick(app, T0, quiet=False))
    text, kb = app.sent[0]
    assert "какое" in text and data(kb) == [f"rt:pk:{mv}:e{a}", f"rt:pk:{mv}:e{b}"]
    reply, _ = run(ritual_flows.handle(app, "pk", mv, f"e{a}"))
    assert app.rituals.feed(mv)["status"] == "applied" and "Перенёс" in reply


def test_handle_ok_creates_task_and_reports(app):
    ev = add(app, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ), title="Встреча")
    reply, _ = run(ritual_flows.handle(app, "ok", ev, None))
    assert "В плане" in reply and app.changed_ids and app.rituals.feed(ev)["status"] == "accepted"


def test_skip_flow_asks_reason_then_penalizes_or_not(app):
    dnd(app)
    ev = add(app, DND, datetime(2026, 10, 16, 18, 30, tzinfo=TZ))
    ask, kb = run(ritual_flows.handle(app, "sk", ev, None))
    assert "Прогул" in labels(keyboards.ritual_reason(ev, 20))[0] and "причин" in ask.lower()
    reply, _ = run(ritual_flows.handle(app, "sb", ev, None))
    assert app.svc.game.hero()["hp"] == 80 and "20" in reply


def test_good_reason_skip_offers_the_next_announce(app):
    dnd(app)
    ev = add(app, DND, datetime(2026, 10, 16, 18, 30, tzinfo=TZ))
    nxt = add(app, DND, datetime(2026, 10, 23, 18, 30, tzinfo=TZ), title="Следующее")
    run(ritual_flows.handle(app, "sg", ev, None))
    assert app.svc.game.hero()["hp"] == 100
    assert any(f"rt:ok:{nxt}" in data(kb) for _, kb in app.sent if kb)


def test_undo_and_retime(app):
    t = dnd(app)
    mv = add(app, DND, datetime(2026, 10, 17, 18, 30, tzinfo=TZ), kind="move", ref_task=t["id"])
    run(ritual_flows.tick(app, NOW, quiet=False))
    reply, _ = run(ritual_flows.handle(app, "un", mv, None))
    assert "Вернул" in reply and app.svc.get(t["id"])["due_at"].startswith("2026-10-16")
    ev = add(app, GOALS, datetime(2026, 10, 19, 11, 0, tzinfo=TZ))
    out = run(ritual_flows.retime_from_reply(app, ev, "завтра в 19:30"))
    row = app.rituals.feed(ev)
    assert "19:30" in out and row["has_time"] == 1
    assert run(ritual_flows.retime_from_reply(app, ev, "хз когда")).startswith("Не понял")


def test_claude_down_notice_is_sent_once_per_day(app, monkeypatch):
    async def fake_collect(tz, **kw):
        return {"posts": 1, "items": 0, "errors": 1, "claude_down": True}

    monkeypatch.setattr(app.rituals, "collect", fake_collect)
    run(ritual_flows.collect(app))
    run(ritual_flows.collect(app))
    assert len([s for s in app.sent if "claude" in s[0].lower()]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rituals_flows.py -v`
Expected: FAIL (`ImportError: cannot import name 'ritual_flows'`).

- [ ] **Step 3: Implement**

`dungeon/keyboards.py` (в конец файла):

```python
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
```

`dungeon/app.py`: импорт `from .rituals import Rituals`; в `__init__` после `self.events = Events(self.db)`:

```python
        self.rituals = Rituals(self.db, self.svc)
        self.rituals_running = False
```

`dungeon/ritual_flows.py`:

```python
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
    start = parse_iso(row["starts_at"]).astimezone(now.tzinfo)
    if not row["has_time"]:
        return None
    chat = rituals.chat_cfg(row["chat_id"]) or {}
    own = app.rituals.occurrence_near(chat, start)
    for t in app.svc.open_tasks():
        if not t["due_at"] or not t["has_time"] or (own and t["id"] == own["id"]):
            continue
        d = parse_iso(t["due_at"]).astimezone(now.tzinfo)
        if start - timedelta(minutes=60) <= d <= start + timedelta(minutes=90):
            return f"⚠️ Пересекается: {t['title']} ({d:%H:%M})"
    return None


def card_text(app, row, now: datetime) -> str:
    tz = now.tzinfo
    extra = json.loads(row["extra"] or "{}")
    lines = [f"🕯 <b>{escape(row['title'])}</b>", f"{fmt(row['starts_at'], row['has_time'], tz)} · {escape(row['ritual'] or '')}"]
    for key, label in (("book", "Книга"), ("variant", "Формат"), ("place", "Место"), ("description", "О чём")):
        if extra.get(key):
            lines.append(f"{label}: {escape(str(extra[key]))}")
    if row["needs_check"]:
        lines.append("❓ Дата или время неуверенные, проверь по посту")
    if (ov := _overlap(app, row, now)):
        lines.append(ov)
    lines.append(f"<i>[a{row['id']}]</i>")
    return "\n".join(lines)


async def send_card(app, row, now: datetime, prefix: str = "") -> None:
    msg = await app.send(prefix + card_text(app, row, now), keyboards.ritual_card(row["id"], row["link"], mode_for(app, row)))
    if msg is not None:
        app.rituals.mark_sent(row["id"], now)


def _change_text(row, now: datetime) -> str:
    tz = now.tzinfo
    u = json.loads(row["undo"] or "{}")
    title = escape(u.get("title") or row["title"])
    old = u.get("old")
    was = fmt(old["starts_at"], old["has_time"], tz) if old and old.get("starts_at") else fmt(row["starts_at"], row["has_time"], tz)
    if row["kind"] == "move":
        new = u.get("new") or {"starts_at": row["starts_at"], "has_time": row["has_time"]}
        return f"🔀 Перенёс <b>{title}</b>\n{was} → {fmt(new['starts_at'], new['has_time'], tz)}"
    return f"🚫 Отмена: <b>{title}</b>, {was}\nШтрафа нет, серия идёт дальше."


async def send_change(app, row, now: datetime) -> None:
    if row["status"] == "applied":
        msg = await app.send(_change_text(row, now) + f"\n<a href=\"{row['link']}\">пост</a>", keyboards.ritual_undo(row["id"]))
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
        await flows._heal_tgmcp(app, dead=bool(stats.get("aborted") or (stats["errors"] and not stats["posts"] and not stats.get("claude_down"))))
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
    """Кнопки rt:*. Возвращает (ответ, ref для клавиатуры или None)."""
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
                         "AND id!=? AND starts_at>? ORDER BY starts_at LIMIT 1", (row["chat_id"], row_id, row["starts_at"]))
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
    """Ответ на карточку: Некит называет верную дату и время обычным текстом."""
    p = parse(text, app.svc.clock())
    if not p.due_at:
        return "Не понял дату. Пример: «в среду в 19:30» или «20.10 в 11»"
    app.rituals.retime(row_id, p.due_at, p.has_time)
    row = app.rituals.feed(row_id)
    if row["task_id"]:
        await app.changed(row["task_id"])
    return f"✏ Поправил: {fmt(row['starts_at'], row['has_time'], app.svc.clock().tzinfo)}"
```

Для `act == "sk"` `handle` возвращает второй элемент `"reason"`: обработчик в `bot.py` в этом случае показывает клавиатуру `keyboards.ritual_reason` (см. ниже).

`dungeon/bot.py`. Импорт: `from . import flows, keyboards, leisure, ritual_flows, stt, texts`. После `event_cb` добавить:

```python
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
```

В обработчике текстовых сообщений (`bot.py`, место `reply = m.reply_to_message`) ПЕРЕД веткой `#(\d+)` добавить:

```python
    if reply and reply.from_user and reply.from_user.is_bot and (mr := re.search(r"\[a(\d+)\]", reply.text or "")):
        await m.answer(await ritual_flows.retime_from_reply(app, int(mr.group(1)), text))
        return
```
(`reply = m.reply_to_message` определяется строкой выше; если порядок строк иной, добавить ветку сразу после определения `reply`.)

`dungeon/scheduler.py`: импорт `ritual_flows` в блок `from . import (...)` (по алфавиту, после `obsidian`). После блока `# 9. афиша` добавить:

```python
    # 9.5 точечные чаты: раз в час читаем (фоном), каждый тик применяем переносы и шлём карточки
    if db.get("rituals_enabled") == "1":
        last = db.get("rt_last_run")
        if not app.rituals_running and (not last or now - datetime.fromisoformat(last) >= every):
            db.set("rt_last_run", now.isoformat())
            app.spawn(ritual_flows.collect(app))
        await ritual_flows.tick(app, now, quiet=in_quiet(now, db.get("quiet_from"), db.get("quiet_to")))
```
(`every` определена в блоке 9 выше.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rituals_flows.py -v && uv run pytest -q`
Expected: PASS (весь набор зелёный, старые тесты бота и планировщика не сломаны).

- [ ] **Step 5: Commit**

```bash
git add dungeon/ritual_flows.py dungeon/keyboards.py dungeon/app.py dungeon/bot.py dungeon/scheduler.py tests/test_rituals_flows.py
git commit -m "feat(rituals): bot cards, digests, urgent moves, buttons, scheduler wiring"
```

---

### Task 8: Раздел PWA «Афиша»

**Files:**
- Modify: `dungeon/web.py` (`state`, новый эндпоинт)
- Modify: `static/index.html`, `static/app.js`, `static/style.css`
- Test: `tests/test_rituals_web.py`

**Interfaces:**
- Consumes: `Rituals.listing(now)`, `Rituals.accept(id)`.
- Produces: `GET /api/state` содержит `rituals: list[dict]` (поля как в `listing`); `POST /api/rituals/{eid}/accept` -> `{"say": str}`, 404 если нет такой строки.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rituals_web.py
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from dungeon.app import App
from dungeon.db import DB, iso
from dungeon.web import create_app

TZ = timezone(timedelta(hours=7))


def make():
    app = App(DB(":memory:"))
    start = datetime.now(TZ) + timedelta(days=2)
    eid = app.db.x("INSERT INTO events_feed(key, chat_id, channel, msg_id, link, starts_at, has_time, title, text, "
                   "kind, status, ritual, extra, created_at) VALUES('k',-3,'c',1,'https://t.me/x/1',?,1,'Встреча',"
                   "'t','announce','new','Цели и привычки','{}',?)", (iso(start), iso(start)))
    return TestClient(create_app(app)), app, eid


def test_state_lists_rituals_and_accept_creates_task():
    client, app, eid = make()
    items = client.get("/api/state").json()["rituals"]
    assert [i["id"] for i in items] == [eid] and items[0]["status"] == "new"
    r = client.post(f"/api/rituals/{eid}/accept")
    assert r.status_code == 200 and "Встреча" in r.json()["say"]
    assert client.get("/api/state").json()["rituals"][0]["status"] == "accepted"
    assert any(t["title"] == "Встреча" for t in client.get("/api/state").json()["open"])


def test_accept_unknown_event_is_404():
    client, _, _ = make()
    assert client.post("/api/rituals/999/accept").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rituals_web.py -v`
Expected: FAIL (`KeyError: 'rituals'`).

- [ ] **Step 3: Implement**

`dungeon/web.py`, в словарь, который возвращает `state()`, добавить ключ:

```python
            "rituals": app.rituals.listing(now),
```
и эндпоинт рядом с остальными (`/api/tasks/...`):

```python
    @api.post("/api/rituals/{eid}/accept", dependencies=[Depends(auth)])
    async def ritual_accept(eid: int):
        row = app.rituals.feed(eid)
        if not row or row["kind"] != "announce":
            raise HTTPException(404, "нет такого анонса")
        res = app.rituals.accept(eid)
        if res["task_id"]:
            await app.changed(res["task_id"])
        return {"say": f"{res['text']}"}
```

`static/index.html`: в `<main>` добавить `<section class="view" data-view="rituals" id="v-rituals" hidden></section>` после `v-raid`; в `<nav>` кнопку `<button data-tab="rituals"><span>🎭</span>Афиша</button>` после «Рейд».

`static/app.js`: в `render()` в словарь функций добавить `rituals: renderRituals`; функцию:

```js
function renderRituals() {
  const rows = (S.rituals || []).map((r) => `
    <div class="task" data-id="rt${r.id}">
      <button class="check ${r.status === "accepted" ? "on" : ""}" data-rt="${r.id}" ${r.status === "accepted" ? "disabled" : ""} aria-label="Записаться"></button>
      <div class="body">
        <div class="title">${esc(r.title)}${r.needs_check ? " ❓" : ""}</div>
        <div class="meta"><span>${esc(r.when)}</span><span>${esc(r.ritual)}</span>${r.extra ? `<span>${esc(r.extra)}</span>` : ""}
          <a class="rt-link" href="${esc(r.link)}" target="_blank" rel="noopener">пост</a></div>
      </div>
    </div>`).join("");
  $("#v-rituals").innerHTML = `<h2>🎭 Афиша <span class="hint">анонсы из твоих чатов</span></h2>
    ${rows ? `<div class="list">${rows}</div>` : '<div class="empty">Пока анонсов нет</div>'}`;
}
```
В обработчик кликов: в селектор `closest(...)` добавить `[data-rt]`, а после ветки `if (el.dataset.done)...` добавить:

```js
  if (el.dataset.rt) { const r = await post(`/api/rituals/${el.dataset.rt}/accept`); toast(r.say, 3500); return load(); }
```

`static/style.css` (в конец):

```css
.check.on { background: var(--gold); border-color: var(--gold); }
.rt-link { color: var(--muted); text-decoration: underline; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rituals_web.py -q && uv run pytest -q`
Expected: PASS. Затем ручная проверка вёрстки: `scripts/setup.sh restart`, открыть PWA, вкладка «Афиша» открывается, на узком экране пять-шесть вкладок помещаются (если нет, уменьшить `max-width` у `.tabs button` до 110px).

- [ ] **Step 5: Commit**

```bash
git add dungeon/web.py static/index.html static/app.js static/style.css tests/test_rituals_web.py
git commit -m "feat(rituals): PWA 'Афиша' tab with sign-up checkbox"
```

---

### Task 9: Конфиг чатов и пробный прогон по настоящим чатам

**Files:**
- Modify: `config/events.yaml` (секция `targeted`)
- Create: `scripts/ritual_dryrun.py`
- Test: `tests/test_rituals_config.py`

**Interfaces:**
- Produces: `config/events.yaml` секция `targeted` (10 чатов из спека); `scripts/ritual_dryrun.py [название чата ...]` печатает, что `claude` разобрал по реальным постам (БД в памяти, в боевую ничего не пишет, в Telegram не пишет, сообщений Некиту не шлёт).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rituals_config.py
from datetime import date

from dungeon import rituals

EXPECT = {
    -1003493020432: "Контент", -1003626669139: "Общество мёртвых читателей", -1003116878132: "Волейбол",
    -1003969606547: "Библиотерапия", -1004426706927: "Цели и привычки", -1001595490870: "Лекции по искусству",
    -1002188099155: "Покер", -1003952363073: "D&D", -1002093016263: "Настолки Neverland",
    -1004482307437: "Йога FYSM",
}


def test_all_ten_chats_are_configured():
    chats = {c["id"]: c for c in rituals.cfg()}
    assert set(chats) == set(EXPECT)
    for cid, name in EXPECT.items():
        assert chats[cid]["name"] == name
        assert chats[cid].get("authors") or chats[cid].get("channel"), name


def test_special_rules():
    chats = {c["id"]: c for c in rituals.cfg()}
    volley = chats[-1003116878132]
    assert volley["topics"] == {3: "outdoor", 132: "indoor"} and volley["authors"] == ["alex_viuk"]
    yoga = chats[-1004482307437]
    assert yoga["series"] == "Йога" and yoga["attend_days"] == ["TU", "SU"] and rituals.as_date(yoga["until"]) == date(2026, 10, 31)
    dnd = chats[-1003952363073]
    assert dnd["series"] == "Настольная игра Dungeons & Dragons" and set(dnd["authors"]) == {"VladimirGoanskiy", "Mortemanes"}
    assert chats[-1003626669139]["fields"] == ["book"] and chats[-1003969606547]["fields"] == ["book"]
    assert chats[-1002188099155]["channel"] and chats[-1001595490870]["channel"]


def test_targeted_chats_are_not_read_by_afisha():
    from dungeon import events
    skip = {c["id"] for c in events.cfg()["targeted"]}
    channels = {c["id"] for c in events.cfg()["channels"]}
    assert skip & channels                       # пересечение есть, и афиша его пропускает (Task 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rituals_config.py -v`
Expected: FAIL (`KeyError`/пустой набор: секции `targeted` нет).

- [ ] **Step 3: Implement**

`config/events.yaml` (добавить в конец файла):

```yaml

# Точечные чаты: ритуалы и события от конкретных людей. Читает rituals.py, разбор через claude -p.
# authors: username без @; channel: true = канал пишет от своего имени, берём все посты.
# series: имя ритуала-серии в боте (D&D, йога). attend_days: на какие дни недели ходит Некит (остальные игнорируем).
# until: после этой даты анонсы игнорируются. topics: форум, {id топика: подпись}.
# fields: справочное поле (что важно вытащить из анонса). Книгу, место и описание LLM достаёт сам по инструкции.
targeted:
  - {id: -1003493020432, name: "Контент", authors: [vasileria]}
  - {id: -1003626669139, name: "Общество мёртвых читателей", authors: [dekobrina], fields: [book]}
  - id: -1003116878132
    name: "Волейбол"
    username: volley_nhatrang
    authors: [alex_viuk]
    topics: {3: outdoor, 132: indoor}
  - {id: -1003969606547, name: "Библиотерапия", authors: [AlinaSnezhko], fields: [book]}
  - {id: -1004426706927, name: "Цели и привычки", authors: [AlinaSnezhko], fields: [book]}
  - {id: -1001595490870, name: "Лекции по искусству", username: morethanart, channel: true}
  - {id: -1002188099155, name: "Покер", username: pokerdaiviet, channel: true}
  - id: -1003952363073
    name: "D&D"
    authors: [VladimirGoanskiy, Mortemanes]
    series: "Настольная игра Dungeons & Dragons"
  - {id: -1002093016263, name: "Настолки Neverland", username: NeverlandCommunity, authors: [Mortemanes]}
  - id: -1004482307437
    name: "Йога FYSM"
    authors: [Panushkinn]
    series: "Йога"
    attend_days: [TU, SU]
    until: 2026-10-31
```

`scripts/ritual_dryrun.py`:

```python
"""Пробный прогон точечных чатов на настоящих постах. Зовёт telegram-mcp и claude (тратит подписку),
но пишет в БД в памяти, ничего не шлёт Некиту и ничего не пишет в чаты.

Запуск: uv run python scripts/ritual_dryrun.py ["Цели и привычки" "Йога FYSM" ...]   (без аргументов: все чаты)
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon import rituals  # noqa: E402
from dungeon.db import DB, parse_iso  # noqa: E402
from dungeon.services import Service  # noqa: E402


async def main(names: list[str]) -> None:
    chats = [c for c in rituals.cfg() if not names or c["name"] in names]
    rituals.cfg = lambda: chats  # только выбранные чаты
    db = DB(":memory:")
    svc = Service(db)
    r = rituals.Rituals(db, svc)
    stats = await r.collect(svc.clock().tzinfo)
    print("итого:", stats)
    for row in db.q("SELECT * FROM events_feed ORDER BY starts_at"):
        when = parse_iso(row["starts_at"]).astimezone(svc.clock().tzinfo)
        flag = " ❓" if row["needs_check"] else ""
        print(f"[{row['kind']:8}] {when:%a %d.%m %H:%M} {row['has_time']} | {row['ritual']} | {row['title']}{flag}"
              f" | {row['extra']} | {row['status']} | {row['link']}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
```

- [ ] **Step 4: Run tests, then the live dry run**

Run: `uv run pytest tests/test_rituals_config.py -v`
Expected: PASS.

Живой прогон (нужны работающий telegram-mcp и `claude`; тратит подписку, примерно по одному вызову на чат):
Run: `uv run python scripts/ritual_dryrun.py "Цели и привычки" "Йога FYSM"`
Expected: напечатаны строки по реальным постам. Сверить глазами с чатами: даты и время верные; «Следующая встреча пройдет 19.10.2026 в 11.00» даёт announce на пн 19.10 11:00; пятничная йога не появляется. Если разбор неверный, поправить `INSTRUCTIONS` в `ritual_llm.py` (и добавить пример в тесты Task 4), повторить. Остальные чаты прогнать тем же скриптом по одному: `Волейбол`, `Библиотерапия`, `D&D`, `Покер`, `Контент`, `Общество мёртвых читателей`, `Лекции по искусству`, `Настолки Neverland`.
Если `get_history` для канала без вступления вернёт ошибку, записать, какой чат не читается, и сказать Некиту (открытый вопрос из спека).

- [ ] **Step 5: Commit**

```bash
git add config/events.yaml scripts/ritual_dryrun.py tests/test_rituals_config.py
git commit -m "feat(rituals): configure 10 targeted chats and add dry-run script"
```

---

### Task 10: Документация, правка спека, финальная проверка, включение

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/specs/2026-10-12-targeted-chats-rituals-design.md`
- Create: `tmp/plans/2026-10-12-targeted-chats-rituals.md` (копия плана)

- [ ] **Step 1: Fix the spec to match what is built**

В спеке заменить текст про «важную причину». Было:

```
- `📌 Важная причина` → без штрафа: занятие просто переносится (бот предлагает новую дату из ближайших анонсов,
  иначе остаётся «без даты» в плане), серия не ломается, стрик не сгорает.
```
Стало:

```
- `📌 Важная причина` → без штрафа: занятие снимается (`skip`), серия идёт дальше, стрик не сгорает;
  если в чате уже есть следующий анонс, бот сразу присылает его карточкой.
```
Там же в разделе «Открытые вопросы» пункт 2 заменить на: `2. Проверено в плане: перенос и отмена серии идут через svc.skip и set_due, штраф за прогул только через skip_missed.`

- [ ] **Step 2: README**

В `README.md` добавить раздел после «Свободен сейчас? и афиша Нячанга»:

```markdown
## Точечные чаты: анонсы ритуалов и событий
Бот раз в час читает 10 чатов из `config/events.yaml` (секция `targeted`), берёт только посты нужных людей и разбирает их через `claude -p` (лимит подписки тратится только если в чате есть новые посты от этих людей).
- **Анонсы** приходят подборкой в 10:00, 16:00, 22:00, анонс на ближайшие 3 часа сразу. Кнопки: ✅ Иду / 🕯 В ритуал / ❌ Мимо / ✏ Поправить (ответь на карточку датой). Для D&D и йоги: ✅ Подтвердить / ❌ Пропускаю.
- **Пропуск занятия серии:** спросит причину. 🚫 Прогул: −20 HP (`/set skip_penalty_hp 20`), стрик сгорает. 📌 Важная причина: без штрафа.
- **Перенос:** если известна исходная дата, бот сам сдвигает ближайшую запись серии и пишет об этом с кнопкой ↩. **Отмена организатора:** занятие снимается без штрафа.
- **Раздел «Афиша» в PWA:** ближайшие анонсы с чекбоксом «записаться».
- Йога: ритуал заводится по первому анонсу (вт и вс, до 31.10), пятницу бот игнорирует.
- Включение: `/set rituals_enabled 1` после пробного прогона `uv run python scripts/ritual_dryrun.py`. Выключено по умолчанию.
- Ограничение: анонс занятия серии, которое ещё не появилось в плане (дальше 3 дней), только записывается, время серии не меняется.
```
Также обновить строку про тесты (`uv run pytest # N тестов: ...`) на фактическое число после Step 3 и добавить «точечные чаты» в перечень.

- [ ] **Step 3: Full verification**

Run: `uv run pytest -q`
Expected: все тесты зелёные (старые 108 + новые). Записать число.

Run: `uv run python -c "import dungeon.scheduler, dungeon.bot, dungeon.web, dungeon.ritual_flows"`
Expected: без ошибок импорта.

Run: `scripts/setup.sh restart && scripts/setup.sh status && scripts/setup.sh logs | tail -20`
Expected: сервис поднят, в логе нет трейсбеков. (Флаг `rituals_enabled` ещё 0, чаты не читаются.)

- [ ] **Step 4: Copy the plan and hand over for enabling**

Run: `mkdir -p tmp/plans && cp docs/superpowers/plans/2026-10-12-targeted-chats-rituals.md tmp/plans/`

Передать Некиту: пробный прогон показал X; для включения `/set rituals_enabled 1`. Первая подборка придёт в ближайший слот 10:00, 16:00 или 22:00 (максимум 10 карточек, глубина 7 дней).

- [ ] **Step 5: Commit**

```bash
git add README.md docs/superpowers/specs/2026-10-12-targeted-chats-rituals-design.md tmp/plans/2026-10-12-targeted-chats-rituals.md
git commit -m "docs(rituals): README section, spec aligned with implementation"
```
