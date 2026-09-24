"""Tests for concurrent, hot-reloadable tenant polling workers."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet

from bot.bot_manager import BotManager
from bot.db import Database


class _FakeSession:
    """Async session stand-in recorded by the fake Telegram client."""

    def __init__(self, bot):
        self.bot = bot

    async def close(self):
        self.bot.closed = True


class _FakeBot:
    """No-network Bot implementation used to observe manager lifecycle calls."""

    created: list["_FakeBot"] = []

    def __init__(self, token: str, **_kwargs):
        self.token = token
        self.webhook_deleted = False
        self.command_calls = 0
        self.closed = False
        self.session = _FakeSession(self)
        self.__class__.created.append(self)

    async def delete_webhook(self, **_kwargs):
        self.webhook_deleted = True

    async def set_my_commands(self, *_args, **_kwargs):
        self.command_calls += 1


class _FakeOuterMiddleware:
    def outer_middleware(self, _middleware):
        return None


class _FakeDispatcher:
    """Polls until manager asks it to stop, without talking to Telegram."""

    created: list["_FakeDispatcher"] = []

    def __init__(self, **_kwargs):
        self.update = _FakeOuterMiddleware()
        self.routers = []
        self.values = {}
        self.stopped = asyncio.Event()
        self.__class__.created.append(self)

    def __setitem__(self, key, value):
        self.values[key] = value

    def include_router(self, router):
        self.routers.append(router)

    async def start_polling(self, _bot, **_kwargs):
        await self.stopped.wait()

    async def stop_polling(self):
        self.stopped.set()


async def _exercise_manager(tmp: str) -> None:
    key = Fernet.generate_key().decode("ascii")
    db = Database(os.path.join(tmp, "bots.db"), encryption_key=key)
    await db.init()
    promotors = await db.get_tenant("promotors")
    await db.update_tenant(promotors.id, bot_token="promotors-token", admin_chat_id=-1001)
    adrenaline = await db.create_tenant(
        slug="adrenaline",
        name="Adrenaline Rush",
        bot_token="adrenaline-token",
        admin_chat_id=-1002,
        admin_password="tenant-password",
    )

    _FakeBot.created.clear()
    _FakeDispatcher.created.clear()
    manager = BotManager(
        db,
        SimpleNamespace(media_dir=tmp, require_subscription=True, registration_closed=False),
        bot_factory=_FakeBot,
        dispatcher_factory=_FakeDispatcher,
    )
    await manager.start()
    await asyncio.sleep(0)  # let both polling tasks enter _poll

    assert set(runtime.tenant.slug for runtime in manager.runtimes.values()) == {
        "promotors", "adrenaline"
    }
    assert manager.get_bot(promotors.id).token == "promotors-token"
    assert manager.get_bot("adrenaline").token == "adrenaline-token"
    assert len(_FakeDispatcher.created) == 2
    # Fresh routers and separate scoped DB facades prove FSM/handler state was
    # not reused between the two bots.  The last router is the safety net that
    # answers updates nothing else claimed (bot/handlers/stay_alive.py), and it
    # has to come after every other router.
    for dispatcher in _FakeDispatcher.created:
        assert len(dispatcher.routers) == 4, [r.name for r in dispatcher.routers]
        assert [router.name for router in dispatcher.routers][-1] == "stay_alive"
        assert dispatcher.routers[0].name == "registration"
    assert {
        dispatcher.values["db"].tenant_id for dispatcher in _FakeDispatcher.created
    } == {promotors.id, adrenaline.id}

    old_adrenaline_bot = manager.get_bot(adrenaline.id)
    await db.update_tenant(adrenaline.id, bot_token="adrenaline-token-v2")
    assert await manager.restart_tenant(adrenaline.id)
    await asyncio.sleep(0)
    assert manager.get_bot(adrenaline.id).token == "adrenaline-token-v2"
    assert manager.get_bot(adrenaline.id) is not old_adrenaline_bot
    assert old_adrenaline_bot.closed

    # Deactivation is a hot stop, not a restart of other tenant workers.
    await db.update_tenant(adrenaline.id, is_active=False)
    await manager.refresh()
    assert manager.get_bot(adrenaline.id) is None
    assert manager.get_bot(promotors.id).token == "promotors-token"
    await manager.shutdown()


def test_manager_polls_active_tenants_in_parallel_and_hot_reloads():
    """Two valid tenant tokens produce two isolated polling workers."""
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(_exercise_manager(tmp))


class _FlakyDispatcher(_FakeDispatcher):
    """Polling dies once (network blip / Telegram conflict), then stays up."""

    attempts: list[int] = []

    async def start_polling(self, _bot, **_kwargs):
        self.__class__.attempts.append(1)
        if len(self.__class__.attempts) == 1:
            raise RuntimeError("simulated polling failure")
        await self.stopped.wait()


async def _exercise_revival(tmp: str) -> None:
    """A dead polling worker is restarted by the supervisor, without a redeploy."""
    import bot.bot_manager as manager_module

    key = Fernet.generate_key().decode("ascii")
    db = Database(os.path.join(tmp, "revive.db"), encryption_key=key)
    await db.init()
    promotors = await db.get_tenant("promotors")
    await db.update_tenant(promotors.id, bot_token="promotors-token")

    delays = (manager_module.REVIVE_DELAY_SECONDS, manager_module.REVIVE_MAX_DELAY_SECONDS)
    manager_module.REVIVE_DELAY_SECONDS = 0.05
    manager_module.REVIVE_MAX_DELAY_SECONDS = 0.05
    _FlakyDispatcher.attempts.clear()
    try:
        manager = BotManager(
            db,
            SimpleNamespace(media_dir=tmp, require_subscription=True, registration_closed=False),
            bot_factory=_FakeBot,
            dispatcher_factory=_FlakyDispatcher,
        )
        await manager.start()
        revived = False
        for _ in range(100):
            await asyncio.sleep(0.02)
            if len(_FlakyDispatcher.attempts) >= 2:
                revived = True
                break
        assert revived, "the worker was never restarted after it died"
        assert manager.get_bot(promotors.id) is not None
        await manager.shutdown()
    finally:
        manager_module.REVIVE_DELAY_SECONDS, manager_module.REVIVE_MAX_DELAY_SECONDS = delays


def test_dead_polling_worker_is_revived():
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(_exercise_revival(tmp))


if __name__ == "__main__":
    test_manager_polls_active_tenants_in_parallel_and_hot_reloads()
    print("Multi-bot manager tests passed.")