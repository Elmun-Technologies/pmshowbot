"""Connection pooling: the bot must not re-open SQLite on every query.

The bot felt slow in production for a reason that never showed up on a laptop:
``Database._connect`` and ``SqliteFSMStorage._connect`` opened a brand-new
SQLite connection for *every* query and re-issued ``PRAGMA journal_mode=WAL``
each time.  A single Telegram update costs 5-9 of those, because aiogram reads
and writes the FSM state several times per update.

On a local SSD an open is microseconds, so nobody noticed.  On the Fly.io
network volume the bot actually runs on, an open plus the WAL pragma is
milliseconds of real disk I/O, and the per-update cost is multiplied by the
number of opens — which is what participants experienced as a laggy bot.

These tests pin the fix: connections are cached per worker thread and reused,
while data stays correct across threads and concurrent tenants.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import threading

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

from bot.db import Database  # noqa: E402
from bot.services.fsm_storage import SqliteFSMStorage  # noqa: E402


class _ConnectionCounter:
    """Count ``sqlite3.connect`` calls while the block runs."""

    def __init__(self) -> None:
        self.count = 0
        self._original = sqlite3.connect
        self._lock = threading.Lock()

    def __enter__(self) -> "_ConnectionCounter":
        def counting(*args, **kwargs):
            with self._lock:
                self.count += 1
            return self._original(*args, **kwargs)

        sqlite3.connect = counting
        return self

    def __exit__(self, *exc) -> None:
        sqlite3.connect = self._original


def _database(path: str) -> Database:
    return Database(path, encryption_key=Fernet.generate_key().decode())


def test_database_reuses_one_connection_per_thread() -> None:
    """Repeated queries must not keep re-opening the database file."""

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _database(os.path.join(tmp, "reuse.db"))
            await db.init()
            # Warm the pool so the first (legitimate) open is not counted.
            await db.get_tenant("promotors")

            with _ConnectionCounter() as counter:
                for _ in range(25):
                    await db.get_tenant("promotors")
                    await db.list_directions(tenant_id="promotors")
                    await db.has_active_application(4242, tenant_id="promotors")

            # 75 queries used to mean 75 opens; pooling makes it a handful at
            # most (one per worker thread asyncio happens to use).
            assert counter.count <= 4, (
                f"{counter.count} new SQLite connections for 75 queries — "
                "connection pooling regressed"
            )
            db.close()

    asyncio.run(run())


def test_fsm_storage_reuses_one_connection_per_thread() -> None:
    """The FSM state is touched several times per update — pool it too."""

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = SqliteFSMStorage(os.path.join(tmp, "fsm.db"))
            key = StorageKey(bot_id=1, chat_id=4242, user_id=4242)
            await storage.set_data(key, {"lang": "ru"})  # warm the pool

            with _ConnectionCounter() as counter:
                for step in range(25):
                    await storage.set_data(key, {"lang": "ru", "step": step})
                    await storage.get_data(key)
                    await storage.get_state(key)

            assert counter.count <= 4, (
                f"{counter.count} new SQLite connections for 75 FSM calls — "
                "connection pooling regressed"
            )
            await storage.close()

    asyncio.run(run())


def test_pooled_connections_stay_correct_under_concurrency() -> None:
    """Pooling must not leak data between tenants or between users."""

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _database(os.path.join(tmp, "concurrent.db"))
            await db.init()
            await db.create_tenant(slug="splshow", name="SPL Show")

            async def register(tenant: str, base: int, count: int) -> None:
                for index in range(count):
                    await db.create_application(
                        tenant_id=tenant,
                        user_id=base + index,
                        username=f"user{base + index}",
                        country="Узбекистан",
                        plate=f"01A{base + index}",
                        direction="drift",
                        phone="+998901234567",
                        photo_paths=[],
                        photo_file_ids=[],
                        language="ru",
                    )

            await asyncio.gather(
                register("promotors", 10_000, 20),
                register("splshow", 20_000, 20),
                register("promotors", 30_000, 20),
                register("splshow", 40_000, 20),
            )

            promotors = await db.list_applications(tenant_id="promotors")
            spl = await db.list_applications(tenant_id="splshow")
            assert len(promotors) == 40, len(promotors)
            assert len(spl) == 40, len(spl)
            # Tenant isolation must survive a shared, reused connection.
            assert {a.user_id for a in spl}.isdisjoint({a.user_id for a in promotors})
            db.close()

    asyncio.run(run())


def test_concurrent_users_keep_their_own_fsm_state() -> None:
    """Two participants filling the form at once must not share state."""

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = SqliteFSMStorage(os.path.join(tmp, "fsm.db"))

            async def fill(user_id: int) -> None:
                key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
                for step in range(15):
                    await storage.set_data(key, {"user": user_id, "step": step})
                    data = await storage.get_data(key)
                    assert data["user"] == user_id, data
                    assert data["step"] == step, data

            await asyncio.gather(*(fill(user) for user in range(12)))
            await storage.close()

    asyncio.run(run())


def test_wal_mode_is_enabled_once_and_persists() -> None:
    """WAL is a property of the file; it should not be re-set per query."""

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "wal.db")
            db = _database(path)
            await db.init()
            await db.get_tenant("promotors")
            db.close()

            probe = sqlite3.connect(path)
            try:
                mode = probe.execute("PRAGMA journal_mode").fetchone()[0]
            finally:
                probe.close()
            assert mode.lower() == "wal", mode

    asyncio.run(run())


def test_close_is_idempotent() -> None:
    """Shutting down twice must not raise (bot restarts call it)."""

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = _database(os.path.join(tmp, "close.db"))
            await db.init()
            db.close()
            db.close()
            # The pool refills on demand, so the database stays usable.
            assert await db.get_tenant("promotors") is not None
            db.close()

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover - manual run helper
    test_database_reuses_one_connection_per_thread()
    test_fsm_storage_reuses_one_connection_per_thread()
    test_pooled_connections_stay_correct_under_concurrency()
    test_concurrent_users_keep_their_own_fsm_state()
    test_wal_mode_is_enabled_once_and_persists()
    test_close_is_idempotent()
    print("ok")
