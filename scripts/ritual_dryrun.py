"""Пробный прогон точечных чатов на настоящих постах. Зовёт telegram-mcp и claude (тратит подписку),
но пишет в БД в памяти, ничего не шлёт Некиту и ничего не пишет в чаты.

Запуск: uv run python scripts/ritual_dryrun.py [--days=30] ["Цели и привычки" "Йога FYSM" ...]   (без названий: все чаты)
--days: как далеко в прошлое смотреть при первом чтении (по умолчанию 7, как в боте).
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dungeon import rituals  # noqa: E402
from dungeon.db import DB, parse_iso  # noqa: E402
from dungeon.services import Service  # noqa: E402


async def main(names: list[str], days: int | None) -> None:
    if days:
        rituals.FIRST_RUN_DAYS = days
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
    args = sys.argv[1:]
    days = next((int(a.split("=")[1]) for a in args if a.startswith("--days=")), None)
    asyncio.run(main([a for a in args if not a.startswith("--days=")], days))
