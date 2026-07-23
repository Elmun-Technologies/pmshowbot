"""Bot entrypoint: wires up the dispatcher and starts long polling."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import web

from .admin.server import create_admin_app
from .config import Config, load_config
from .db import Database
from .handlers import moderation, mynumber, registration

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    config = load_config()

    db = Database(config.db_path)
    await db.init()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # Contextual data injected into handlers by parameter name.
    dp["config"] = config
    dp["db"] = db

    dp.include_router(registration.router)
    dp.include_router(moderation.router)
    dp.include_router(mynumber.router)

    # Start the admin web panel (same process → shares the DB and bot).
    web_runner = await _start_admin_panel(bot, config, db)

    logger.info("Starting bot (long polling)...")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        if web_runner is not None:
            await web_runner.cleanup()


async def _start_admin_panel(bot: Bot, config: Config, db: Database):
    """Launch the aiohttp admin panel on the same event loop. Returns the
    AppRunner (for cleanup), or None if it could not start.

    The server always listens (serving /health and the login page) so the Fly
    http_service healthcheck passes; admin routes require ADMIN_PASSWORD.
    """
    admin_app = create_admin_app(bot, config, db)
    runner = web.AppRunner(admin_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.panel_port)
    await site.start()
    if config.panel_enabled:
        logger.info("Admin panel listening on port %s", config.panel_port)
    else:
        logger.info(
            "Admin panel port %s is up but LOCKED — set ADMIN_PASSWORD to enable it",
            config.panel_port,
        )
    return runner


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
