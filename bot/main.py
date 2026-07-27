"""Bot entrypoint: wires up the dispatcher and starts long polling."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault
from aiohttp import web

from .admin.server import create_admin_app
from .config import Config, load_config
from .db import Database
from .services import assets
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

    # Brand assets uploaded by admins land next to the participant photos, so
    # they live on the persistent volume and survive restarts/redeploys.
    assets.configure(config.media_dir)

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

    await _publish_commands(bot, config)

    logger.info("Starting bot (long polling)...")
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        if web_runner is not None:
            await web_runner.cleanup()


# Shown to participants in the "/" menu.
_PUBLIC_COMMANDS = [
    ("start", "Начать регистрацию / Ro‘yxatdan o‘tish"),
    ("mynumber", "Узнать свой номер / Raqamimni bilish"),
]

# Additionally shown inside the moderation chat, so admins can discover the
# tools instead of having to remember them.
_ADMIN_COMMANDS = _PUBLIC_COMMANDS + [
    ("assets", "Логотипы и баннеры: что загружено"),
    ("help_assets", "Как загрузить логотип / баннер"),
    ("delasset", "Удалить логотип или баннер"),
    ("stats", "Статистика заявок"),
    ("export", "Выгрузить заявки в Excel"),
    ("diag", "Диагностика: канал, подписка, билет"),
    ("whoami", "Мои id и права доступа"),
]


async def _publish_commands(bot: Bot, config: Config) -> None:
    """Register the "/" menu with Telegram: public commands everywhere, plus
    the admin tools inside the moderation chat."""
    try:
        await bot.set_my_commands(
            [BotCommand(command=c, description=d) for c, d in _PUBLIC_COMMANDS],
            scope=BotCommandScopeDefault(),
        )
        await bot.set_my_commands(
            [BotCommand(command=c, description=d) for c, d in _ADMIN_COMMANDS],
            scope=BotCommandScopeChat(chat_id=config.admin_chat_id),
        )
        logger.info("Command menu published (public + admin chat).")
    except Exception:  # noqa: BLE001 - a menu failure must not stop the bot
        logger.exception("Could not publish the command menu")


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
