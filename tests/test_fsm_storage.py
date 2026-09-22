"""The registration state must survive a worker restart.

``MemoryStorage`` threw away every half-finished form whenever the process
restarted — a redeploy or an admin saving a tenant setting (which deliberately
hot-restarts that worker) left participants with a bot that answered nothing.
"""
from __future__ import annotations

import asyncio
import os
import sys
import sqlite3
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiogram.fsm.storage.base import StorageKey  # noqa: E402

from bot.services.fsm_storage import SqliteFSMStorage  # noqa: E402
from bot.states import Registration  # noqa: E402


def _key(bot_id: int = 1, user_id: int = 42) -> StorageKey:
    return StorageKey(bot_id=bot_id, chat_id=user_id, user_id=user_id)


def test_state_and_data_survive_a_restart():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.db")
            storage = SqliteFSMStorage(path)
            key = _key()
            await storage.set_state(key, Registration.photos)
            await storage.update_data(
                key, data={"lang": "uz", "photo_file_ids": ["a", "b"]}
            )

            # A fresh object on the same file = a restarted worker.
            restarted = SqliteFSMStorage(path)
            assert await restarted.get_state(key) == Registration.photos.state
            data = await restarted.get_data(key)
            assert data["lang"] == "uz" and data["photo_file_ids"] == ["a", "b"]

            # Another bot id (another tenant) never sees the state.
            assert await restarted.get_state(_key(bot_id=2)) is None
            assert await restarted.get_data(_key(bot_id=2)) == {}

            # aiogram semantics: clearing the state keeps the collected data.
            await restarted.set_state(key, None)
            assert await restarted.get_state(key) is None
            assert (await restarted.get_data(key))["photo_file_ids"] == ["a", "b"]

            await storage.close()
            await restarted.close()

    asyncio.run(run())


def test_broken_database_degrades_to_memory_instead_of_crashing():
    async def run():
        # A path that cannot be created: the storage must keep working in memory
        # rather than taking the whole bot down.
        storage = SqliteFSMStorage("/proc/definitely/not/writable/state.db")
        key = _key()
        await storage.set_state(key, Registration.phone)
        assert await storage.get_state(key) == Registration.phone.state
        await storage.set_data(key, {"lang": "ru"})
        assert (await storage.get_data(key))["lang"] == "ru"
        await storage.close()

    asyncio.run(run())


def test_a_transient_failure_does_not_degrade_the_process_forever():
    """The bug that stayed invisible until the next restart.

    ``_ensure_ready`` used to set ``_broken`` once and never look at the
    database again: a single transient "database is locked" during start-up put
    the whole process on ``MemoryStorage`` for the rest of its life, so every
    half-finished form was silently lost on the next restart.  The storage now
    retries the database after a cooldown, and reports it when it recovers.
    """
    import logging

    from bot.services import fsm_storage as module

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.db")
            storage = module.SqliteFSMStorage(path)
            key = _key()

            calls = {"n": 0}
            real_init = storage._sync_init

            def flaky_init():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise sqlite3.OperationalError("database is locked")
                return real_init()

            storage._sync_init = flaky_init  # type: ignore[method-assign]

            records: list[logging.LogRecord] = []

            class _Capture(logging.Handler):
                def emit(self, record):
                    records.append(record)

            handler = _Capture()
            module.logger.addHandler(handler)
            module.logger.setLevel(logging.WARNING)
            saved_cooldown = module.RECOVERY_COOLDOWN_SECONDS
            module.RECOVERY_COOLDOWN_SECONDS = 0.05
            try:
                # First touch fails: the form continues in memory, without an
                # exception reaching the handler.
                await storage.set_state(key, Registration.plate)
                assert storage._broken is True
                await storage.set_data(key, {"lang": "uz", "plate": "01A123BC"})
                assert (await storage.get_data(key))["plate"] == "01A123BC"

                # Inside the cooldown the database is not retried.
                await storage.set_state(key, Registration.photos)
                assert calls["n"] == 1, "retried the broken database immediately"

                await asyncio.sleep(0.06)  # the cooldown expires
                await storage.set_state(key, Registration.photos)
                assert calls["n"] == 2, "the database was never retried"
                assert storage._broken is False, "the storage stayed on the memory fallback"

                # The form collected in memory during the outage is now in SQLite.
                restarted = module.SqliteFSMStorage(path)
                assert await restarted.get_state(key) == Registration.photos.state, (
                    "recovering lost the form that was filled in during the outage"
                )
                fresh_data = await restarted.get_data(key)
                assert fresh_data.get("plate") == "01A123BC", fresh_data
                await restarted.close()
            finally:
                module.RECOVERY_COOLDOWN_SECONDS = saved_cooldown
                module.logger.removeHandler(handler)
                module.logger.setLevel(logging.NOTSET)
                await storage.close()

            messages = [record.getMessage() for record in records]
            assert any("available again" in m for m in messages), messages

    asyncio.run(run())


def test_a_permanently_missing_path_is_never_retried():
    """No path configured means memory storage, with no pointless retries."""

    async def run():
        from bot.services import fsm_storage as module

        storage = module.SqliteFSMStorage("")
        key = _key()
        await storage.set_state(key, Registration.country)
        assert await storage.get_state(key) == Registration.country.state
        assert storage._permanent is True
        await storage.close()

    asyncio.run(run())

if __name__ == "__main__":  # pragma: no cover - manual run helper
    test_state_and_data_survive_a_restart()
    test_broken_database_degrades_to_memory_instead_of_crashing()
    test_a_transient_failure_does_not_degrade_the_process_forever()
    test_a_permanently_missing_path_is_never_retried()
    print("ok")


