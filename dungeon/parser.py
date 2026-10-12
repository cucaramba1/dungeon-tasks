"""Разбор фразы в задачу. Только правила и словари, без LLM.

"завтра в 10 йога, важно" -> title="Йога", due=завтра 10:00, habit, Q2.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from . import config

WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3,
    "пятниц": 4, "суббот": 5, "воскресень": 6,
}
RRULE_DAYS = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
MONTHS = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}
NUM_WORDS = {
    "час": 1, "один": 1, "одну": 1, "два": 2, "две": 2, "пару": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "пятнадцать": 15, "двадцать": 20,
    "тридцать": 30, "сорок": 40,
}
PART_OF_DAY = {"утр": 9, "дн": 13, "днём": 13, "днем": 13, "вечер": 19, "ноч": 23}

WD = r"(понедельник\w*|вторник\w*|сред\w*|четверг\w*|пятниц\w*|суббот\w*|воскресень\w*)"
NUM = r"(\d{1,2}|" + "|".join(sorted(NUM_WORDS, key=len, reverse=True)) + r")"
QUAD_MARK = re.compile(r"(?:^|\s)(?:!|#q|#к)([1-4])\b", re.I)


@dataclass
class Parsed:
    title: str
    quadrant: int | None = None
    is_habit: bool = False
    due_at: datetime | None = None      # локальное aware-время
    has_time: bool = False
    rrule: str | None = None
    duration_min: int | None = None     # оценка «~2ч», «на 30 мин»
    important: bool | None = None
    urgent: bool | None = None


def _num(s: str) -> int:
    return int(s) if s.isdigit() else NUM_WORDS[s.lower()]


def _weekday(word: str) -> int:
    w = word.lower()
    for stem, i in WEEKDAYS.items():
        if w.startswith(stem):
            return i
    raise ValueError(word)


def _month(word: str) -> int | None:
    w = word.lower()
    for stem, i in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
        if w.startswith(stem):
            return i
    return None


def _has_stem(text: str, stems: list[str]) -> str | None:
    """Ищет стем в начале слова. Возвращает найденный стем или None."""
    for s in sorted(stems, key=len, reverse=True):
        if re.search(r"(?<![\w-])" + re.escape(s.lower()), text):
            return s
    return None


def _stem_re(stem: str) -> str:
    return r"(?<![\w-])" + re.escape(stem.lower()).replace("е", "[её]") + r"\w*"


class _Cut:
    """Текст, из которого вырезаются распознанные куски."""

    def __init__(self, text: str):
        self.text = text

    def take(self, pattern: str, flags=re.I):
        m = re.search(pattern, self.text, flags)
        if m:
            self.text = (self.text[:m.start()] + " " + self.text[m.end():])
        return m


def parse(text: str, now: datetime) -> Parsed:
    """now — локальное aware-время."""
    kw = config.yaml_file("keywords")
    raw = " ".join(text.split())
    c = _Cut(" " + raw + " ")
    low = raw.lower().replace("ё", "е")
    p = Parsed(title=raw)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    date: datetime | None = None
    hour = minute = None
    default_hour = None

    # --- явная метка квадранта: !1 / #q2 ---
    m = QUAD_MARK.search(c.text)
    if m:
        p.quadrant = int(m.group(1))
        c.text = c.text[:m.start()] + " " + c.text[m.end():]

    # --- повтор ---
    if c.take(r"\b(каждый день|ежедневно|каждые сутки)\b"):
        p.rrule = "FREQ=DAILY"
    elif m := c.take(r"\bкажд(?:ое|ый) (утро|вечер)\b"):
        p.rrule = "FREQ=DAILY"
        default_hour = 9 if m.group(1) == "утро" else 20
    elif c.take(r"\bпо будн(?:ям|ие дни)\b"):
        p.rrule = "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"
    elif c.take(r"\bпо выходным\b"):
        p.rrule = "FREQ=WEEKLY;BYDAY=SA,SU"
    elif m := c.take(r"\bкаждые (\d+) (?:дня|дней)\b"):
        p.rrule = f"FREQ=DAILY;INTERVAL={int(m.group(1))}"
    elif m := c.take(r"\b(?:по|каждый|каждую|каждое)\s+" + WD + r"((?:\s*(?:,|и)\s*" + WD + r")*)"):
        days = sorted({_weekday(w) for w in re.findall(WD, m.group(0), re.I)})
        p.rrule = "FREQ=WEEKLY;BYDAY=" + ",".join(RRULE_DAYS[d] for d in days)
    elif c.take(r"\b(раз в неделю|еженедельно)\b"):
        p.rrule = f"FREQ=WEEKLY;BYDAY={RRULE_DAYS[now.weekday()]}"

    # --- относительное время: через 2 часа / через полчаса ---
    if c.take(r"\bчерез полчаса\b"):
        p.due_at, p.has_time = now + timedelta(minutes=30), True
    elif m := c.take(r"\bчерез\s+(?:" + NUM + r"\s+)?(минут\w*|мин|час\w*|дн\w*|день|недел\w*)\b"):
        n = _num(m.group(1)) if m.group(1) else 1
        unit = m.group(2).lower()
        if unit.startswith("мин"):
            p.due_at, p.has_time = now + timedelta(minutes=n), True
        elif unit.startswith("час"):
            p.due_at, p.has_time = now + timedelta(hours=n), True
        elif unit.startswith("недел"):
            date = today + timedelta(weeks=n)
        else:
            date = today + timedelta(days=n)

    # --- длительность: «~2ч», «на 30 мин», «(1.5ч)», «на полчаса» ---
    # раньше времени, иначе «на 2 часа» станет 14:00
    if m := c.take(r"(?:~|≈|\()\s*(\d+(?:[.,]\d+)?)\s*(ч|час\w*|м|мин\w*)\)?(?![\w])"):
        p.duration_min = _minutes(m.group(1), m.group(2))
    elif m := c.take(r"\bна\s+(\d+(?:[.,]\d+)?)\s*(ч|час\w*|мин\w*)(?![\w])"):
        p.duration_min = _minutes(m.group(1), m.group(2))
    elif m := c.take(r"\bна\s+(полчаса|пару часов|час)\b"):
        p.duration_min = {"полчаса": 30, "пару часов": 120, "час": 60}[m.group(1).lower()]

    # --- дата ---
    if p.due_at is None:
        if c.take(r"\bпослезавтра\b"):
            date = today + timedelta(days=2)
        elif c.take(r"\bзавтра\b"):
            date = today + timedelta(days=1)
        elif c.take(r"\bсегодня\b"):
            date = today
        elif c.take(r"\bна выходных\b"):
            date = today if now.weekday() >= 5 else today + timedelta(days=5 - now.weekday())
        elif c.take(r"\bна следующей неделе\b"):
            date = today + timedelta(days=7 - now.weekday())
        elif m := c.take(r"(?<![вкВК] )(?<!на )\b(\d{1,2})[./](0?[1-9]|1[0-2])(?:[./](\d{2,4}))?\b"):
            d, mo = int(m.group(1)), int(m.group(2))
            y = int(m.group(3)) if m.group(3) else now.year
            if y < 100:
                y += 2000
            date = _safe_date(today, y, mo, d, roll=not m.group(3))
        elif m := c.take(r"\b(\d{1,2})\s+(январ\w*|феврал\w*|март\w*|апрел\w*|ма[яй]\w*|июн\w*|июл\w*|август\w*|"
                         r"сентябр\w*|октябр\w*|ноябр\w*|декабр\w*)\b"):
            date = _safe_date(today, now.year, _month(m.group(2)), int(m.group(1)), roll=True)
        elif m := c.take(r"\b(?:в|во|к|до|на)\s+" + WD + r"\b"):
            ahead = (_weekday(m.group(1)) - now.weekday()) % 7 or 7
            date = today + timedelta(days=ahead)

        # --- время ---
        if c.take(r"\bв полдень\b"):
            hour, minute = 12, 0
        elif c.take(r"\bв полночь\b"):
            hour, minute = 23, 59
        elif m := c.take(r"\b(?:в|к|на)\s+(\d{1,2})[:.](\d{2})(?:\s*(утра|дня|вечера|ночи))?\b"):
            hour, minute = _fix_hour(int(m.group(1)), m.group(3), explicit_minutes=True), int(m.group(2))
        elif m := c.take(r"\b(?:в|к|на)\s+" + NUM + r"(?:\s+(\d{2}))?(?:\s*(?:час\w*|ч))?(?:\s*(утра|дня|вечера|ночи))?(?![\w])"
                         r"(?!\s*(?:раз|процент|%|штук|рубл|доллар|бакс|человек|минут|кг|км|страниц|\$))"):
            h = _num(m.group(1))
            if h <= 23:
                hour = _fix_hour(h, m.group(3), explicit_minutes=False)
                minute = int(m.group(2)) if m.group(2) and int(m.group(2)) < 60 else 0
            else:
                c.text = c.text + " " + m.group(0)  # не время, вернём обратно
        elif m := c.take(r"\b(\d{1,2}):(\d{2})\b"):
            hour, minute = int(m.group(1)), int(m.group(2))
        if hour is None:
            if m := c.take(r"\b(?:с\s+)?(утр(?:ом|а)|дн[её]м|вечер(?:ом|а)|ночью)\b"):
                word = m.group(1).lower().replace("ё", "е")
                hour, minute = next(v for k, v in PART_OF_DAY.items() if word.startswith(k)), 0
            elif default_hour is not None:
                hour, minute = default_hour, 0

        if hour is not None and hour <= 23 and minute <= 59:
            base = date or today
            p.due_at = base.replace(hour=hour, minute=minute)
            p.has_time = True
            if date is None and p.due_at <= now:
                p.due_at += timedelta(days=1)  # "в 9" вечером = завтра в 9
        elif date is not None:
            p.due_at = date

    # --- повтор без даты стартует с ближайшего подходящего дня ---
    if p.rrule and p.due_at is None:
        p.due_at = today
    if p.rrule and "BYDAY" in p.rrule and p.due_at is not None:
        allowed = [RRULE_DAYS.index(d) for d in p.rrule.split("BYDAY=")[1].split(",")]
        while p.due_at.weekday() not in allowed:
            p.due_at += timedelta(days=1)

    # --- приоритет ---
    work = c.text.lower().replace("ё", "е")
    for key, attr, val in (("not_important", "important", False), ("important", "important", True),
                           ("not_urgent", "urgent", False), ("urgent", "urgent", True)):
        if getattr(p, attr) is not None:
            continue
        stems = kw.get(key, [])
        if _has_stem(work, stems):
            setattr(p, attr, val)
            while stem := _has_stem(work, stems):
                if not c.take(_stem_re(stem)):
                    break
                work = c.text.lower().replace("ё", "е")

    # --- привычка ---
    marker = _has_stem(work, kw.get("habit_markers", []))
    if marker:
        p.is_habit = True
        c.take(_stem_re(marker))
    elif _has_stem(low, kw.get("habit", [])):
        p.is_habit = True

    # --- квадрант ---
    if p.quadrant is None:
        p.quadrant = _quadrant(p, now)

    p.title = _clean_title(c.text) or raw
    return p


def _minutes(num: str, unit: str) -> int:
    n = float(num.replace(",", "."))
    return int(round(n * 60)) if unit.lower().startswith("ч") else int(round(n))


def _fix_hour(h: int, part: str | None, explicit_minutes: bool) -> int:
    if part:
        part = part.lower()
        if part in ("дня", "вечера") and h < 12:
            return h + 12
        if part == "ночи" and h == 12:
            return 0
        return h
    # "в 3" без уточнения почти всегда про день, а не про ночь
    if 1 <= h <= 6 and not explicit_minutes:
        return h + 12
    return h


def _safe_date(today: datetime, y: int, mo: int | None, d: int, roll: bool) -> datetime | None:
    try:
        dt = today.replace(year=y, month=mo, day=d)
    except (ValueError, TypeError):
        return None
    if roll and dt < today:
        dt = dt.replace(year=y + 1)
    return dt


def _quadrant(p: Parsed, now: datetime) -> int | None:
    soon = p.due_at is not None and p.due_at - now <= timedelta(hours=48)
    if p.is_habit and p.important is None and p.urgent is None:
        return 2  # ритуалы живут в Эльфийской библиотеке
    if p.important is True:
        return 1 if (p.urgent or (soon and p.urgent is not False)) else 2
    if p.important is False:
        return 3 if (p.urgent or (soon and p.urgent is not False)) else 4
    if p.urgent is True:
        return 1
    if p.urgent is False:
        return 2 if p.is_habit else None
    return None  # важность неизвестна -> инбокс, разберём вечером


def _clean_title(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s+([,.;:!?])", r"\1", s)
    s = re.sub(r"([,;:])\1+", r"\1", s)
    for _ in range(3):
        s = re.sub(r"^(?:[,.;:—\-–!]+|\s|(?:и|а|в|во|к|на|до|это)\s)+", "", s, flags=re.I)
        s = re.sub(r"(?:[,;:—\-–]+|\s|\s(?:и|а|в|во|к|на|до|это))+$", "", s, flags=re.I)
    s = s.strip(" .,")
    return s[:1].upper() + s[1:] if s else s
