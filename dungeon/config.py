"""Настройки: .env (секреты) + config/*.yaml (словари, тексты)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOGS = ROOT / "logs"
STATIC = ROOT / "static"
CONFIG = ROOT / "config"

load_dotenv(ROOT / ".env")


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


BOT_TOKEN = env("BOT_TOKEN")
OWNER_ID = int(env("OWNER_ID") or 0)
WEB_HOST = env("WEB_HOST", "127.0.0.1")
WEB_PORT = int(env("WEB_PORT", "8787"))
WEB_KEY = env("WEB_KEY")
TG_API_ID = int(env("TG_API_ID") or 0)
TG_API_HASH = env("TG_API_HASH")
TG_INSURANCE_SESSION = env("TG_INSURANCE_SESSION")
WHISPER_BIN = env("WHISPER_BIN", str(Path.home() / ".local/bin/mlx_whisper"))
WHISPER_MODEL = env("WHISPER_MODEL", "mlx-community/whisper-large-v3-turbo")
FFMPEG_BIN = env("FFMPEG_BIN", "/opt/homebrew/bin/ffmpeg")
OBSIDIAN_DIR = Path(env("OBSIDIAN_DIR", str(Path.home() / "Documents/Obsidian storage/Продуктивность и привычки")))
CLAUDE_BIN = env("CLAUDE_BIN", str(Path.home() / ".npm-global/bin/claude"))
COACH_MODEL = env("COACH_MODEL", "sonnet")  # пусто = модель по умолчанию в Claude Code
GOALS_NOTE = Path(env("GOALS_NOTE", str(OBSIDIAN_DIR / "Мои цели.md")))
DB_PATH = Path(env("DB_PATH", str(DATA / "dungeon.db")))
ICLOUD_APPLE_ID = env("ICLOUD_APPLE_ID")
ICLOUD_APP_PASSWORD = env("ICLOUD_APP_PASSWORD")
ICLOUD_CALENDAR_NAME = env("ICLOUD_CALENDAR_NAME", "Dungeon Tasks")

# Значения по умолчанию для настроек, которые меняются через /set и хранятся в БД.
DEFAULT_SETTINGS = {
    "morning": "08:00",
    "evening": "21:00",
    "weekly_day": "6",          # 0 = пн … 6 = вс
    "weekly_time": "20:00",
    "quiet_from": "23:30",
    "quiet_to": "07:30",
    "nag_first": "15",          # минут между первыми повторами привычки
    "nag_later": "30",          # после трёх повторов
    "habit_time": "19:00",      # время ритуала, если не назван
    "focus_min": "25",
    "break_min": "5",
    "mac_popup": "1",
    "morning_from": "05:00",    # раньше этого открытие ноута не считается утром
    "evening_cl_fallback": "22:30",  # вечерний чек-лист, если план так и не подтвердил
    "hours_weekday": "4",       # сколько часов в будни реально есть на задачи
    "hours_weekend": "2",
    "coach_day": "6",
    "coach_time": "20:05",
    "free_from": "12:00",       # «Свободен сейчас?»: окно, лимит в день, пауза между вопросами
    "free_to": "21:00",
    "free_max": "2",
    "free_gap_h": "3",
    "events_every_min": "60",   # как часто читать афишу
    "skip_penalty_hp": "20",    # штраф за прогул ритуала (Некит сам выбрал «прогул»)
    "ritual_digest": "10:00,16:00,22:00",  # когда слать подборку анонсов из точечных чатов
    "ritual_urgent_h": "3",     # анонс на ближайшие N часов уходит сразу
    "ritual_remind_h": "2.5",   # одно напоминание, если карточка без ответа
    "rituals_enabled": "0",     # 1 = читать точечные чаты (включать после пробного прогона)
}


@lru_cache
def yaml_file(name: str) -> dict:
    with open(CONFIG / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}
