"""Односторонний синк Dungeon -> Apple Calendar через iCloud CalDAV.

Одиночная задача с due_at = один VEVENT (uid хранится в tasks.caldav_uid).
Задача с rrule = один мастер-VEVENT с RRULE на всю серию, привязан не к
конкретной строке, а к series_id (uid в calendar_series) — повторы в БД
это отдельные строки (см. services._spawn_next), но в календаре это
одно повторяющееся событие.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import caldav
from icalendar import Calendar as ICal
from icalendar import Event as IEvent

from . import config

log = logging.getLogger(__name__)
DEFAULT_DURATION_MIN = 30


def enabled() -> bool:
    return bool(config.ICLOUD_APPLE_ID and config.ICLOUD_APP_PASSWORD)


@lru_cache
def _calendar() -> caldav.Calendar:
    client = caldav.DAVClient(
        url="https://caldav.icloud.com/",
        username=config.ICLOUD_APPLE_ID,
        password=config.ICLOUD_APP_PASSWORD,
    )
    principal = client.principal()
    for cal in principal.calendars():
        if cal.get_display_name() == config.ICLOUD_CALENDAR_NAME:
            return cal
    return principal.make_calendar(name=config.ICLOUD_CALENDAR_NAME)


def _rrule_dict(rrule: str) -> dict:
    """'FREQ=WEEKLY;BYDAY=FR;INTERVAL=2' -> {'FREQ': 'WEEKLY', 'BYDAY': ['FR'], 'INTERVAL': 2}."""
    out: dict = {}
    for part in rrule.split(";"):
        key, _, val = part.partition("=")
        if key == "INTERVAL":
            out[key] = int(val)
        elif key == "BYDAY":
            out[key] = val.split(",")
        elif key == "UNTIL":
            out[key] = datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        else:
            out[key] = val
    return out


def _build_ics(uid: str, title: str, start: datetime, duration_min: int | None,
                rrule: str | None) -> str:
    cal = ICal()
    cal.add("prodid", "-//Dungeon Tasks//dungeon-tasks//")
    cal.add("version", "2.0")
    ev = IEvent()
    ev.add("uid", uid)
    ev.add("summary", title)
    ev.add("dtstart", start)
    ev.add("dtend", start + timedelta(minutes=duration_min or DEFAULT_DURATION_MIN))
    ev.add("dtstamp", datetime.now(start.tzinfo))
    if rrule:
        ev.add("rrule", _rrule_dict(rrule))
    cal.add_component(ev)
    return cal.to_ical().decode()


def _find_event(calendar: caldav.Calendar, uid: str):
    """iCloud отдаёт 412 на REPORT-поиск по uid (их баг, не каддава) — и не только когда
    события нет, а периодически вообще. Поэтому ищем честно, по списку всех событий."""
    for ev in calendar.events():
        if str(ev.icalendar_component.get("uid")) == uid:
            return ev
    return None


def _upsert(uid: str, title: str, start: datetime, duration_min: int | None,
            rrule: str | None) -> str:
    ics = _build_ics(uid, title, start, duration_min, rrule)
    calendar = _calendar()
    event = _find_event(calendar, uid)
    if event:
        event.data = ics
        event.save()
    else:
        calendar.add_event(ical=ics)
    return uid


def upsert_task(uid: str | None, title: str, start: datetime, duration_min: int | None) -> str:
    """Одиночная (без rrule) задача. Возвращает uid для сохранения в tasks.caldav_uid."""
    uid = uid or uuid.uuid4().hex
    return _upsert(uid, title, start, duration_min, rrule=None)


def upsert_series(uid: str | None, title: str, start: datetime, duration_min: int | None,
                   rrule: str) -> str:
    """Серия (ритуал/повтор). uid хранится в calendar_series по series_id."""
    uid = uid or uuid.uuid4().hex
    return _upsert(uid, title, start, duration_min, rrule=rrule)


def delete(uid: str) -> None:
    event = _find_event(_calendar(), uid)
    if event:
        event.delete()
