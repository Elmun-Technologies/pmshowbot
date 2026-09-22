"""The registration state must survive a worker restart.

``MemoryStorage`` threw away every half-finished form whenever the process
restarted — a redeploy or an admin saving a tenant setting (which deliberately
hot-restarts that worker) left participants with a bot that answered nothing.
"""
from __future__ import annotations

import asyncio
import os
import sys
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


if __name__ == "__main__":  # pragma: no cover - manual run helper
    test_state_and_data_survive_a_restart()
    test_broken_database_degrades_to_memory_instead_of_crashing()
    print("ok")
