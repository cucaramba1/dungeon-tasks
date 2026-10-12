"""Точка входа: один процесс, один event loop — бот, веб и тикер вместе."""
from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler

import uvicorn
from aiogram import Dispatcher

from . import bot as bot_mod
from . import config, scheduler
from .app import App
from .web import create_app


def setup_logging() -> None:
    config.LOGS.mkdir(exist_ok=True)
    handler = RotatingFileHandler(config.LOGS / "dungeon.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(level=logging.INFO, handlers=[handler, logging.StreamHandler()],
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # сигналы ловит asyncio.run
        pass


async def main() -> None:
    setup_logging()
    log = logging.getLogger("dungeon")
    app = App()
    await app.ins.start()
    # после рестарта страховка могла устареть — пересобираем по открытым задачам
    await app.ins.resync_all(app.svc.open_tasks())

    web = _Server(uvicorn.Config(create_app(app), host=config.WEB_HOST, port=config.WEB_PORT,
                                 log_level="warning", access_log=False))
    jobs = [asyncio.create_task(web.serve(), name="web"), asyncio.create_task(scheduler.run(app), name="tick")]
    if app.bot:
        dp = Dispatcher()
        dp.include_router(bot_mod.build(app))
        await bot_mod.set_commands(app)
        jobs.append(asyncio.create_task(dp.start_polling(app.bot, handle_signals=False), name="bot"))
    else:
        log.warning("BOT_TOKEN не задан: работает только веб")
    log.info("Dungeon Tasks запущен: http://%s:%s", config.WEB_HOST, config.WEB_PORT)
    try:
        await asyncio.gather(*jobs)
    finally:
        await app.ins.stop()
        if app.bot:
            await app.bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
