"""SQLite. Все запросы короткие, поэтому синхронный sqlite3 под локом, без aiosqlite."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    notes TEXT DEFAULT '',
    quadrant INTEGER,              -- 1..4, NULL = инбокс
    is_habit INTEGER DEFAULT 0,
    is_boss INTEGER DEFAULT 0,
    due_at TEXT,                   -- UTC ISO; при has_time=0 значима только дата
    has_time INTEGER DEFAULT 0,
    remind_at TEXT,                -- UTC ISO
    next_nag_at TEXT,              -- следующий повтор напоминания
    nag_count INTEGER DEFAULT 0,
    rrule TEXT,
    duration_min INTEGER,          -- оценка длительности, для расчёта нагрузки
    series_id INTEGER,             -- общий id для повторяющейся серии
    status TEXT DEFAULT 'open',    -- open | done | missed | missed_pending | deleted
    source TEXT DEFAULT 'tg',
    created_at TEXT NOT NULL,
    done_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS insurance (
    task_id INTEGER NOT NULL,
    msg_id INTEGER NOT NULL,
    fire_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hero (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    xp INTEGER DEFAULT 0,
    hp INTEGER DEFAULT 100,
    gold INTEGER DEFAULT 0,
    fallen INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS streaks (
    series_id INTEGER PRIMARY KEY,
    title TEXT,
    current INTEGER DEFAULT 0,
    best INTEGER DEFAULT 0,
    last_date TEXT
);

CREATE TABLE IF NOT EXISTS achievements (
    code TEXT PRIMARY KEY,
    unlocked_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,            -- done | missed | focus | boss | fall | delete | inbox_zero
    task_id INTEGER,
    quadrant INTEGER,
    xp INTEGER DEFAULT 0,
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS focus (
    id INTEGER PRIMARY KEY,
    task_id INTEGER,
    started_at TEXT NOT NULL,
    ends_at TEXT NOT NULL,
    status TEXT DEFAULT 'running'  -- running | done | cancelled
);

CREATE TABLE IF NOT EXISTS rewards (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    price INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS stickers (
    file_id TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checklist (
    day TEXT NOT NULL,
    kind TEXT NOT NULL,            -- morning | evening
    idx INTEGER NOT NULL,
    done_at TEXT NOT NULL,
    PRIMARY KEY (day, kind, idx)
);

CREATE TABLE IF NOT EXISTS coach (
    id INTEGER PRIMARY KEY,
    week TEXT NOT NULL,            -- 2026-W40
    created_at TEXT NOT NULL,
    data TEXT NOT NULL             -- JSON ответа коуча
);

CREATE TABLE IF NOT EXISTS events_feed (
    id INTEGER PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,      -- дата + хэш начала текста, склейка дублей между каналами
    chat_id INTEGER,
    channel TEXT,
    msg_id INTEGER,
    link TEXT,
    starts_at TEXT NOT NULL,
    has_time INTEGER DEFAULT 0,
    title TEXT,
    text TEXT,
    category TEXT,
    price TEXT,
    vote INTEGER DEFAULT 0,        -- 1 интересно, -1 мимо
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leisure_seen (
    path TEXT PRIMARY KEY,         -- заметка из Obsidian «Досуг»
    status TEXT NOT NULL,          -- watching | seen | skip
    at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS plan_sessions (
    id INTEGER PRIMARY KEY,
    week TEXT NOT NULL,
    step INTEGER DEFAULT 1,        -- 1 контекст, 2 цели, 3 задачи, 4 расстановка, 5 готово
    status TEXT DEFAULT 'active',  -- active | done | cancelled
    inputs TEXT DEFAULT '{}',      -- {"1": ["текст", ...], ...}
    drafts TEXT DEFAULT '{}',      -- ответы Claude по шагам
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_msgs (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    topic TEXT NOT NULL,           -- plan | events | checklists | alerts
    created_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS calendar_series (
    series_id INTEGER PRIMARY KEY,
    uid TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calendar_outbox (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    action TEXT NOT NULL,          -- upsert | delete
    requested_at TEXT NOT NULL
);
INSERT OR IGNORE INTO hero (id) VALUES (1);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat() if dt else None


def parse_iso(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


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


class DB:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(tasks)")}
        if "duration_min" not in cols:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN duration_min INTEGER")
        if "caldav_uid" not in cols:
            self.conn.execute("ALTER TABLE tasks ADD COLUMN caldav_uid TEXT")
        feed = {r[1] for r in self.conn.execute("PRAGMA table_info(events_feed)")}
        for col, ddl in RITUAL_COLS.items():
            if col not in feed:
                self.conn.execute(f"ALTER TABLE events_feed ADD COLUMN {col} {ddl}")

    def q(self, sql: str, args: tuple | dict = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def one(self, sql: str, args: tuple | dict = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def x(self, sql: str, args: tuple | dict = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur.lastrowid

    # --- key/value ---
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.one("SELECT value FROM kv WHERE key=?", (key,))
        if row:
            return row["value"]
        return config.DEFAULT_SETTINGS.get(key, default)

    def set(self, key: str, value) -> None:
        self.x("INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, str(value)))

    def backup(self, keep: int = 7) -> None:
        """Ежедневный бэкап файла БД, держим последние keep копий."""
        if self.path == ":memory:":
            return
        bdir = Path(self.path).parent / "backups"
        bdir.mkdir(exist_ok=True)
        target = bdir / f"dungeon-{datetime.now():%Y-%m-%d}.db"
        with self.lock:
            dst = sqlite3.connect(target)
            self.conn.backup(dst)
            dst.close()
        for old in sorted(bdir.glob("dungeon-*.db"))[:-keep]:
            old.unlink()

