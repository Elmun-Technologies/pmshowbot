"""Multi-tenant process entrypoint.

One Fly machine hosts the aiohttp control panel and a :class:`BotManager` that
runs one long-polling task per active tenant.  No Telegram token is read from
the process environment after the legacy ``promotors`` migration bootstrap.
"""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from . import executors, logwatch
from .admin.server import create_admin_app
from .bot_manager import BotManager, publish_commands as _publish_commands
from .config import Config, load_config
from .db import DEFAULT_TENANT_SLUG, Database
from .services import assets, decisions, media

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# aiogram swallows the 409 Conflict that a second poller causes, so promote it
# to an explicit CRITICAL line (see bot/logwatch.py).
logwatch.install()


async def main() -> None:
    """Initialise migrations, admin HTTP, and all database-configured bots."""
    config = load_config()
    db = Database(
        config.db_path,
        encryption_key=config.encryption_key,
        bootstrap=config,
    )
    await db.init()

    # Move old global uploads exactly once into the migration tenant. New
    # requests always pass an explicit tenant scope to assets.py.
    assets.configure(config.media_dir)
    default_tenant = await db.get_tenant(DEFAULT_TENANT_SLUG)
    if default_tenant is not None:
        moved = assets.migrate_legacy_assets(config.media_dir, default_tenant.slug)
        if any(moved.values()):
            logger.info("[promotors] Migrated legacy ticket assets: %s", moved)

    manager = BotManager(db, config)
    web_runner = await _start_admin_panel(config, db, manager)
    await manager.start()
    if config.registration_closed:
        logger.warning(
            "REGISTRATION_CLOSED is set in the environment but ignored. "
            "Registration is per tenant and stays open unless that tenant's "
            "admin checkbox is on. A leftover secret no longer makes every bot "
            "answer «регистрация завершена»."
        )
    logger.info("Tenant bot manager started; waiting for shutdown.")

    try:
        # Polling workers are background tasks managed by BotManager. Keeping
        # this coroutine alive lets aiohttp and every tenant share one loop.
        await asyncio.Event().wait()
    finally:
        await manager.shutdown()
        if web_runner is not None:
            await web_runner.cleanup()
        # A decision answered a second ago may still be delivering its ticket.
        # Give those tasks a moment: a redeploy is exactly when "the participant
        # never got the ticket" would otherwise happen.
        await decisions.wait_background(timeout=20.0)
        # Photo downloads run as their own tasks (they must not hold an update
        # back): stop the ones that are still in flight before the loop closes.
        await media.ingest.cancel_pending()
        # Release the pooled SQLite connections held by the worker threads.
        await asyncio.to_thread(db.close)
        # Let queued Google/Excel work finish, then drop the slow-work pool.
        executors.shutdown()


async def _start_admin_panel(config: Config, db: Database, manager: BotManager):
    """Launch the tenant/super-admin panel and the Fly health endpoint."""
    admin_app = create_admin_app(bot=None, config=config, db=db, bot_manager=manager)
    runner = web.AppRunner(admin_app)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=config.panel_port)
    await site.start()
    if config.panel_enabled:
        logger.info("Admin panel listening on port %s", config.panel_port)
    else:
        logger.info(
            "Admin panel port %s is up but super admin login is locked — set SUPER_ADMIN_PASSWORD",
            config.panel_port,
        )
    return runner


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
