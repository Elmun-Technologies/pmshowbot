"""Concurrent lifecycle manager for database-configured Telegram bots.

A tenant gets its own ``Bot``, ``Dispatcher``, FSM memory storage and handler
routers.  This avoids cross-tenant state leakage while still running all long
polling tasks in one asyncio event loop and one Fly machine.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Optional

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from .config import Config, TenantConfig
from .db import Database, Tenant
from .handlers.factories import create_tenant_routers
from .middlewares import RegistrationClosedMiddleware, SerializePerUserMiddleware

logger = logging.getLogger(__name__)

_PUBLIC_COMMANDS = [
    ("start", "Начать регистрацию / Ro‘yxatdan o‘tish"),
    ("mynumber", "Узнать свой номер / Raqamimni bilish"),
]
_ADMIN_COMMANDS = _PUBLIC_COMMANDS + [
    ("assets", "Логотипы и баннеры: что загружено"),
    ("help_assets", "Как загрузить логотип / баннер"),
    ("delasset", "Удалить логотип или баннер"),
    ("stats", "Статистика заявок"),
    ("export", "Выгрузить заявки в Excel"),
    ("diag", "Диагностика: канал, подписка, билет"),
    ("whoami", "Мои id и права доступа"),
]


@dataclass
class BotRuntime:
    """Live resources owned by one tenant polling worker."""

    tenant: Tenant
    config: TenantConfig
    bot: Any
    dispatcher: Any
    task: asyncio.Task
    signature: tuple[Any, ...]


async def publish_commands(bot: Bot, config: TenantConfig) -> None:
    """Publish public and moderation-chat command menus for one bot."""
    try:
        await bot.set_my_commands(
            [BotCommand(command=command, description=description) for command, description in _PUBLIC_COMMANDS],
            scope=BotCommandScopeDefault(),
        )
        if config.admin_chat_id:
            await bot.set_my_commands(
                [BotCommand(command=command, description=description) for command, description in _ADMIN_COMMANDS],
                scope=BotCommandScopeChat(chat_id=config.admin_chat_id),
            )
        logger.info("[%s] Command menu published", config.tenant_slug)
    except Exception:  # noqa: BLE001 - Telegram menu setup must not stop polling
        logger.exception("[%s] Could not publish command menu", config.tenant_slug)


class BotManager:
    """Start, stop and hot-reload all active tenant polling workers.

    The manager intentionally does not cache decrypted tokens in SQLite-facing
    objects.  A token is decrypted only when a worker is started and remains in
    that worker's in-memory Bot client for as long as polling is active.
    """

    def __init__(
        self,
        db: Database,
        config: Config | Any,
        *,
        bot_factory: Callable[..., Any] = Bot,
        dispatcher_factory: Callable[..., Any] = Dispatcher,
    ) -> None:
        self.db = db
        self.config = config
        self._bot_factory = bot_factory
        self._dispatcher_factory = dispatcher_factory
        self._runtimes: dict[int, BotRuntime] = {}
        self._lock = asyncio.Lock()
        self._stopping = False
        self._wake = asyncio.Event()
        self._supervisor: Optional[asyncio.Task] = None

    @property
    def runtimes(self) -> dict[int, BotRuntime]:
        """A copy-safe view used by diagnostics and tests."""
        return dict(self._runtimes)

    def get_bot(self, tenant_id: int | str) -> Any | None:
        """Return the currently live Bot for a tenant, if polling is running."""
        if isinstance(tenant_id, int):
            runtime = self._runtimes.get(tenant_id)
            return runtime.bot if runtime else None
        for runtime in self._runtimes.values():
            if runtime.tenant.slug == tenant_id:
                return runtime.bot
        return None

    def get_config(self, tenant_id: int | str) -> TenantConfig | None:
        """Return a live worker config without ever exposing its token in HTML."""
        if isinstance(tenant_id, int):
            runtime = self._runtimes.get(tenant_id)
            return runtime.config if runtime else None
        for runtime in self._runtimes.values():
            if runtime.tenant.slug == tenant_id:
                return runtime.config
        return None

    def _make_config(self, tenant: Tenant, token: str) -> TenantConfig:
        if hasattr(self.config, "tenant_config"):
            return self.config.tenant_config(tenant, bot_token=token)
        # Small fallback that makes the manager convenient to unit-test with a
        # SimpleNamespace, while production always uses Config.  Match the
        # public TenantConfig shape rather than reading legacy BOT_TOKEN values.
        media_root = getattr(self.config, "media_dir", "media")
        return TenantConfig(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
            bot_token=token,
            required_channel=tenant.required_channel,
            admin_chat_id=tenant.admin_chat_id,
            google_credentials_file=getattr(self.config, "google_credentials_file", "credentials.json"),
            spreadsheet_id=tenant.spreadsheet_id,
            drive_folder_id=tenant.drive_folder_id,
            media_dir=f"{media_root}/_tenants/{tenant.slug}",
            asset_scope=tenant.slug,
            require_subscription=bool(getattr(self.config, "require_subscription", True)),
            registration_closed=bool(getattr(self.config, "registration_closed", False)),
            admin_user_ids=getattr(self.config, "admin_user_ids", frozenset()),
            channel_url=tenant.channel_url,
            instagram_handle=tenant.instagram_handle,
            instagram_url=tenant.instagram_url,
        )

    @staticmethod
    def _signature(tenant: Tenant, token: str) -> tuple[Any, ...]:
        return (
            token,
            tenant.is_active,
            tenant.name,
            tenant.admin_chat_id,
            tenant.required_channel,
            tenant.channel_url,
            tenant.instagram_handle,
            tenant.instagram_url,
            tenant.spreadsheet_id,
            tenant.drive_folder_id,
        )

    def _new_bot(self, token: str) -> Any:
        try:
            return self._bot_factory(
                token=token,
                default=DefaultBotProperties(parse_mode=ParseMode.HTML),
            )
        except TypeError:
            # Lightweight fake factories in unit tests often only accept token.
            return self._bot_factory(token=token)

    def _new_dispatcher(self, tenant_config: TenantConfig) -> Any:
        try:
            dispatcher = self._dispatcher_factory(storage=MemoryStorage())
        except TypeError:
            dispatcher = self._dispatcher_factory()
        # Fake dispatchers used in tests may intentionally only implement the
        # polling API; build as much real configuration as they expose.
        if hasattr(dispatcher, "update") and hasattr(dispatcher.update, "outer_middleware"):
            dispatcher.update.outer_middleware(SerializePerUserMiddleware())
        try:
            dispatcher["config"] = tenant_config
            dispatcher["db"] = self.db.for_tenant(tenant_config.tenant_id)
        except (TypeError, AttributeError):
            setattr(dispatcher, "tenant_config", tenant_config)
            setattr(dispatcher, "tenant_db", self.db.for_tenant(tenant_config.tenant_id))
        if hasattr(dispatcher, "include_router"):
            registration_router, moderation_router, badge_router, number_router = create_tenant_routers()
            registration_router.message.outer_middleware(RegistrationClosedMiddleware())
            registration_router.callback_query.outer_middleware(RegistrationClosedMiddleware())
            dispatcher.include_router(registration_router)
            dispatcher.include_router(moderation_router)
            dispatcher.include_router(badge_router)
            dispatcher.include_router(number_router)
        return dispatcher

    async def _poll(self, tenant_config: TenantConfig, bot: Any, dispatcher: Any) -> None:
        slug = tenant_config.tenant_slug
        logger.info("[%s] Starting bot long polling", slug)
        try:
            if hasattr(bot, "delete_webhook"):
                await bot.delete_webhook(drop_pending_updates=True)
            await publish_commands(bot, tenant_config)
            # Signal handling belongs to the process's main asyncio runner, not
            # to every tenant dispatcher.  Real aiogram supports both kwargs.
            try:
                await dispatcher.start_polling(
                    bot, handle_signals=False, close_bot_session=False
                )
            except TypeError:
                await dispatcher.start_polling(bot)
        except asyncio.CancelledError:
            logger.info("[%s] Polling task cancelled", slug)
            raise
        except Exception:  # noqa: BLE001 - one bad tenant must not stop others
            logger.exception("[%s] Polling stopped with an error", slug)
        finally:
            logger.info("[%s] Polling task exited", slug)

    async def _close_runtime(self, runtime: BotRuntime) -> None:
        slug = runtime.tenant.slug
        dispatcher = runtime.dispatcher
        try:
            stop = getattr(dispatcher, "stop_polling", None)
            if stop is not None:
                result = stop()
                if inspect.isawaitable(result):
                    await result
        except Exception:  # noqa: BLE001
            logger.debug("[%s] Dispatcher stop failed", slug, exc_info=True)
        if not runtime.task.done():
            runtime.task.cancel()
        try:
            await runtime.task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.debug("[%s] Polling task cleanup failed", slug, exc_info=True)
        try:
            session = getattr(runtime.bot, "session", None)
            close = getattr(session, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
        except Exception:  # noqa: BLE001
            logger.debug("[%s] Bot session close failed", slug, exc_info=True)

    async def _start_tenant_locked(self, tenant: Tenant) -> bool:
        if not tenant.is_active:
            return False
        try:
            token = await self.db.get_tenant_token(tenant.id)
        except EncryptionError:
            logger.exception("[%s] Cannot decrypt bot token", tenant.slug)
            return False
        if not token:
            logger.warning("[%s] Active tenant has no bot token; polling is skipped", tenant.slug)
            return False
        tenant_config = self._make_config(tenant, token)
        try:
            bot = self._new_bot(token)
            dispatcher = self._new_dispatcher(tenant_config)
        except Exception:  # noqa: BLE001
            logger.exception("[%s] Could not create Telegram client", tenant.slug)
            return False
        task = asyncio.create_task(
            self._poll(tenant_config, bot, dispatcher), name=f"tenant-poll:{tenant.slug}"
        )
        self._runtimes[tenant.id] = BotRuntime(
            tenant=tenant,
            config=tenant_config,
            bot=bot,
            dispatcher=dispatcher,
            task=task,
            signature=self._signature(tenant, token),
        )
        self._wake.set()
        return True

    async def start(self) -> None:
        """Start every active tenant that has a decryptable bot token."""
        self._stopping = False
        await self.refresh()
        if self._supervisor is None or self._supervisor.done():
            self._supervisor = asyncio.create_task(
                self._supervise(), name="tenant-bot-supervisor"
            )

    async def refresh(self) -> None:
        """Reconcile active DB tenants with live polling workers.

        This is safe to call after tenant create/edit/deactivate.  Changed
        tokens/settings replace only that tenant's worker; unrelated tenants
        continue polling uninterrupted.
        """
        async with self._lock:
            active = await self.db.list_tenants(active_only=True)
            active_by_id = {tenant.id: tenant for tenant in active}
            # Stop removed/deactivated workers first.
            for tenant_id, runtime in list(self._runtimes.items()):
                tenant = active_by_id.get(tenant_id)
                if tenant is None:
                    logger.info("[%s] Stopping inactive tenant", runtime.tenant.slug)
                    self._runtimes.pop(tenant_id, None)
                    await self._close_runtime(runtime)
                    continue
                try:
                    token = await self.db.get_tenant_token(tenant_id)
                except EncryptionError:
                    token = "__unreadable__"
                if runtime.signature != self._signature(tenant, token):
                    logger.info("[%s] Reloading changed tenant configuration", tenant.slug)
                    self._runtimes.pop(tenant_id, None)
                    await self._close_runtime(runtime)
            # Start any active tenant not currently running.
            for tenant in active:
                if tenant.id not in self._runtimes:
                    await self._start_tenant_locked(tenant)
            self._wake.set()

    async def restart_tenant(self, identifier: int | str) -> bool:
        """Hot-restart one tenant after a super-admin change."""
        async with self._lock:
            tenant = await self.db.get_tenant(identifier)
            if tenant is None:
                return False
            runtime = self._runtimes.pop(tenant.id, None)
            if runtime is not None:
                logger.info("[%s] Restarting tenant on admin request", tenant.slug)
                await self._close_runtime(runtime)
            if tenant.is_active:
                return await self._start_tenant_locked(tenant)
            self._wake.set()
            return True

    async def wait_for_pollers(self) -> None:
        """Await a snapshot of all workers concurrently with ``asyncio.gather``.

        It is useful to process runners and tests that deliberately want to
        wait for worker completion.  The normal supervisor below remains
        hot-reloadable rather than blocking forever on an old task snapshot.
        """
        tasks = [runtime.task for runtime in self._runtimes.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _supervise(self) -> None:
        """Observe workers while allowing refresh/restart calls to wake it."""
        while not self._stopping:
            tasks = [runtime.task for runtime in self._runtimes.values() if not runtime.task.done()]
            self._wake.clear()
            if not tasks:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
                continue
            wake_task = asyncio.create_task(self._wake.wait())
            try:
                await asyncio.wait([*tasks, wake_task], return_when=asyncio.FIRST_COMPLETED)
            finally:
                if not wake_task.done():
                    wake_task.cancel()

    async def shutdown(self) -> None:
        """Stop all polling tasks and close their HTTP sessions."""
        self._stopping = True
        self._wake.set()
        async with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
            for runtime in runtimes:
                await self._close_runtime(runtime)
        if self._supervisor and not self._supervisor.done():
            self._supervisor.cancel()
            try:
                await self._supervisor
            except asyncio.CancelledError:
                pass
