"""Досуг: «Свободен сейчас?» и что предложить. Без LLM.

Фильмы, сериалы и книги берутся из Obsidian «Досуг» (тип и жанр по ссылке на хаб
«Жанр — Фильмы — …»). Просмотренное и отвергнутое больше не предлагается.
"""
from __future__ import annotations

import random
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import config
from .db import DB, iso, parse_iso, utcnow

GENRE = re.compile(r"\[\[Жанр — ([^\]]+)\]\]")
KINDS = {"film": "🎬 Фильм", "series": "📺 Сериал", "book": "📚 Книга"}
_cache: dict = {"mtime": None, "items": []}


def leisure_dir() -> Path:
    return Path(config.env("LEISURE_DIR", str(config.OBSIDIAN_DIR.parent / "Досуг")))


def _kind(genre: str) -> str | None:
    g = genre.lower()
    if g.startswith("книги"):
        return "book"
    if g.startswith("сериалы") or g.startswith("мини-сериалы"):
        return "series"
    if g.startswith(("фильмы", "мультфильмы", "документальное")):
        return "film"
    return None


TYPE_KIND = {"фильм": "film", "мульт-аниме": "film", "документалка": "film", "сериал": "series", "книга": "book"}
DONE_STATUS = {"смотрел", "читал", "играл"}
SEEN_STATUS = {"film": "смотрел", "series": "смотрел", "book": "читал"}
CACHE_TTL = 600  # свайпалка правит заметки на месте, mtime папки не меняется — перечитываем раз в 10 минут


def frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm


def scan(folder: Path | None = None) -> list[dict]:
    """Заметки досуга, которые ещё можно предложить: не просмотренные и не «не хочу».
    Тип и статус из frontmatter (его ведёт свайпалка media-swipe), иначе по ссылке на жанровый хаб."""
    folder = folder or leisure_dir()
    if not folder.exists():
        return []
    key = (folder, folder.stat().st_mtime)
    if _cache["mtime"] == key and time.time() - _cache.get("at", 0) < CACHE_TTL:
        return _cache["items"]
    items = []
    for p in folder.glob("*.md"):
        if p.name.startswith("Жанр —"):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        fm = frontmatter(text)
        m = GENRE.search(text)
        kind = TYPE_KIND.get(fm.get("type", "")) or (_kind(m.group(1)) if m else None)
        if not kind or fm.get("status") in DONE_STATUS or fm.get("interest") == "не хочу":
            continue
        body = text[text.find("\n---", 3) + 4:] if fm else text
        lines = [ln.strip() for ln in body.splitlines()]
        title = next((ln[2:] for ln in lines if ln.startswith("# ")), p.stem)
        desc = next((ln for ln in lines if ln and not ln.startswith(("#", "-", "Упомина"))), "")
        items.append({"path": p.name, "title": title, "desc": desc[:300], "kind": kind,
                      "genre": fm.get("genre") or (m.group(1).split(" — ")[-1] if m else ""),
                      "want": fm.get("interest") == "хочу"})
    _cache.update(mtime=key, at=time.time(), items=items)
    return items


def mark_seen_in_note(path: str, kind: str, folder: Path | None = None) -> bool:
    """«Уже было» в боте -> status во frontmatter, чтобы свайпалка и холст тоже знали."""
    p = (folder or leisure_dir()) / path
    if not p.exists() or kind not in SEEN_STATUS:
        return False
    text = p.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    head, rest = text[:end], text[end:]
    seen = SEEN_STATUS[kind]
    head = re.sub(r"(?m)^status:.*$", f"status: {seen}", head) if re.search(r"(?m)^status:", head) \
        else head + f"\nstatus: {seen}"
    head = head.replace("не_смотрел", "смотрел").replace("не_читал", "читал")
    p.write_text(head + rest, encoding="utf-8")
    _cache["mtime"] = None
    return True


class Leisure:
    def __init__(self, db: DB):
        self.db = db

    def pick(self, kind: str, items: list[dict] | None = None) -> dict | None:
        items = scan() if items is None else items
        recent = iso(utcnow() - timedelta(days=21))
        blocked = {r["path"] for r in self.db.q(
            "SELECT path FROM leisure_seen WHERE status IN ('seen','skip','watching') OR at>?", (recent,))}
        pool = [i for i in items if i["kind"] == kind and i["path"] not in blocked]
        if not pool:
            return None
        wanted = [i for i in pool if i.get("want")]
        item = random.choice(wanted or pool)
        self.db.x("INSERT INTO leisure_seen(path, status, at) VALUES(?, 'suggested', ?) "
                  "ON CONFLICT(path) DO UPDATE SET status='suggested', at=excluded.at", (item["path"], iso(utcnow())))
        item["rid"] = self.db.one("SELECT rowid FROM leisure_seen WHERE path=?", (item["path"],))[0]
        return item

    def mark(self, rid: int, status: str) -> str | None:
        row = self.db.one("SELECT path FROM leisure_seen WHERE rowid=?", (rid,))
        if not row:
            return None
        self.db.x("UPDATE leisure_seen SET status=?, at=? WHERE rowid=?", (status, iso(utcnow()), rid))
        return row["path"]

    def kind_of(self, rid: int) -> str | None:
        row = self.db.one("SELECT path FROM leisure_seen WHERE rowid=?", (rid,))
        if not row:
            return None
        return next((i["kind"] for i in scan() if i["path"] == row["path"]), None)


# ---------- «Свободен сейчас?» ----------

def busy_soon(tasks, now: datetime, hours: float = 2) -> bool:
    """Есть задача со временем в ближайшие hours или идёт прямо сейчас (с учётом длительности)."""
    for t in tasks:
        if not t["has_time"] or not t["due_at"]:
            continue
        start = parse_iso(t["due_at"])
        end = start + timedelta(minutes=t["duration_min"] or 60)
        if start <= now + timedelta(hours=hours) and end > now:
            return True
    return False


def should_ask(db: DB, tasks, now: datetime, focus_running: bool) -> bool:
    frm, to = db.get("free_from"), db.get("free_to")
    hhmm = f"{now:%H:%M}"
    if not (frm <= hhmm < to) or focus_running:
        return False
    today = now.date().isoformat()
    if int(db.get(f"free_count:{today}", "0") or 0) >= int(db.get("free_max")):
        return False
    last = db.get("free_last")
    if last and now - parse_iso(last) < timedelta(hours=float(db.get("free_gap_h"))):
        return False
    return not busy_soon(tasks, now)


def mark_asked(db: DB, now: datetime) -> None:
    today = now.date().isoformat()
    db.set(f"free_count:{today}", int(db.get(f"free_count:{today}", "0") or 0) + 1)
    db.set("free_last", iso(now))


def next_up(tasks, now: datetime):
    """Ближайшее дело со временем до конца дня."""
    end = now.replace(hour=23, minute=59, second=59)
    timed = [t for t in tasks if t["has_time"] and t["due_at"] and now < parse_iso(t["due_at"]) <= end]
    return min(timed, key=lambda t: t["due_at"]) if timed else None


def in_human(delta: timedelta) -> str:
    mins = int(delta.total_seconds() // 60)
    h, m = divmod(mins, 60)
    return f"{h}ч{m:02d}" if h and m else f"{h}ч" if h else f"{m} мин"
