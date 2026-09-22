"""A worker restart must not lose updates or a half-finished form.

Two separate ways a restart used to swallow a participant's message:

1. ``_poll`` called ``delete_webhook(drop_pending_updates=True)`` on *every*
   start.  A worker is restarted on each deploy and every time an admin saves a
   tenant setting, and anything sent in those seconds was deleted from
   Telegram's queue — the participant got no answer and no error appeared
   anywhere.
2. The form state has to survive the same restart, which is what
   ``SqliteFSMStorage`` is for; this test drives the real handlers across a
   simulated hot restart (new storage object, new dispatcher, same database).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from aiogram.types import Chat, Message, PhotoSize, Update, User  # noqa: E402

from harness import BotHarness  # noqa: E402
from bot.bot_manager import BotManager  # noqa: E402
from bot.services.fsm_storage import SqliteFSMStorage  # noqa: E402
from bot.states import Registration  # noqa: E402

USER = 6060


class _RecordingBot:
    def __init__(self) -> None:
        self.delete_webhook_calls: list[dict] = []
        self.commands = 0

    async def delete_webhook(self, **kwargs):
        self.delete_webhook_calls.append(kwargs)

    async def set_my_commands(self, *_args, **_kwargs):
        self.commands += 1


class _IdleDispatcher:
    """``start_polling`` returns at once so ``_poll`` can be awaited directly."""

    def __init__(self) -> None:
        self.polled = False

    async def start_polling(self, *args, **kwargs):
        self.polled = True


def test_startup_keeps_pending_updates():
    """Telegram's queue must not be deleted when a worker starts."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            manager = BotManager(harness.db, harness.config)
            bot = _RecordingBot()
            dispatcher = _IdleDispatcher()
            await manager._poll(harness.tenant_config, bot, dispatcher)

            assert dispatcher.polled, "the worker did not start polling"
            assert bot.delete_webhook_calls, "delete_webhook was not called"
            assert bot.delete_webhook_calls[0].get("drop_pending_updates") is False, (
                "pending updates are still dropped on every worker start: "
                f"{bot.delete_webhook_calls[0]}"
            )
        finally:
            await harness.stop()

    asyncio.run(run())


def _restart_worker(harness: BotHarness):
    """A hot restart: new storage object + new dispatcher on the same database."""
    storage = SqliteFSMStorage(harness.db_path)
    manager = BotManager(harness.db, harness.config, storage=storage)
    return manager._new_dispatcher(harness.tenant_config), storage


def _photo_update(file_id: str, update_id: int = 100) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=USER, type="private"),
            from_user=User(id=USER, is_bot=False, first_name="Tester"),
            photo=[
                PhotoSize(
                    file_id=file_id, file_unique_id=f"{file_id}-u", width=1080, height=720
                )
            ],
        ),
    )


def test_a_form_survives_a_worker_restart_and_continues():
    """The exact scenario from the incident: keep the form, restart, continue."""
    from aiogram.fsm.storage.base import StorageKey

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await harness.send_command(USER)
            await harness.tap(USER, "lang=ru" if False else "lang:ru")
            await harness.tap(USER, "country:0")
            await harness.send_text(USER, "01A123BC")
            directions = await harness.db.list_directions(
                tenant_id=harness.tenant.id, active_only=True
            )
            if directions:
                await harness.tap(USER, f"direction:{directions[0].id}")
            key = StorageKey(bot_id=harness.bot.id, chat_id=USER, user_id=USER)
            assert await harness.dispatcher.storage.get_state(key) == Registration.photos.state

            # --- the restart (deploy, or an admin saving a tenant setting) ---
            dispatcher, storage = _restart_worker(harness)

            # The state is still there and the photo is still accepted.
            assert await storage.get_state(key) == Registration.photos.state
            before = len(harness.session.methods)
            await dispatcher.feed_update(harness.bot, _photo_update("after-restart"))
            assert len(harness.session.methods) > before, (
                "the photo sent after the restart got no answer"
            )
            data = await storage.get_data(key)
            assert data.get("photo_file_ids") == ["after-restart"], data.get("photo_file_ids")
            await storage.close()
        finally:
            await harness.stop()

    asyncio.run(run())


class _NullSession:
    async def close(self):
        return None


class _NullBot:
    """Accepts a token and does nothing, so no HTTP client is ever created."""

    def __init__(self, token: str, **_kwargs) -> None:
        self.token = token
        self.session = _NullSession()

    async def delete_webhook(self, **_kwargs):
        return None

    async def set_my_commands(self, *_args, **_kwargs):
        return None


def test_tenant_refresh_keeps_the_form():
    """``BotManager.refresh`` (panel save) must not clear participants' forms."""
    from aiogram.fsm.storage.base import StorageKey

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            key = StorageKey(bot_id=harness.bot.id, chat_id=USER, user_id=USER)
            await harness.dispatcher.storage.set_state(key, Registration.plate.state)
            await harness.dispatcher.storage.set_data(key, {"lang": "uz", "country": "Узбекистан"})

            manager = BotManager(
                harness.db,
                harness.config,
                storage=harness._storage,
                bot_factory=_NullBot,
                dispatcher_factory=_IdleDispatcher,
            )
            await harness.db.update_tenant(harness.tenant.id, name="SPL Show 2026")
            await manager.refresh()

            # The storage object is shared by every worker of this process, so a
            # refresh may not touch the rows.
            assert await harness.dispatcher.storage.get_state(key) == Registration.plate.state
            assert (await harness.dispatcher.storage.get_data(key))["lang"] == "uz"
            await manager.shutdown()
        finally:
            await harness.stop()

    asyncio.run(run())


if __name__ == "__main__":
    test_startup_keeps_pending_updates()
    test_a_form_survives_a_worker_restart_and_continues()
    test_tenant_refresh_keeps_the_form()
    print("All worker-restart tests passed.")
