"""Opening a brand-new SQLite file from several threads at once must not fail.

``PRAGMA journal_mode=WAL`` takes a write lock and, unlike ordinary statements,
ignores ``busy_timeout``: when several worker threads touch a fresh database
file for the first time, the losers of that race get "database is locked"
immediately instead of waiting.

That race is not exotic — it is the moment after a deploy or at the start of an
event, when many participants register at once — and its consequences were
silent.  ``SqliteFSMStorage`` ran the schema setup from every concurrent update,
because ``_ready`` only becomes true once that setup finished; losing the race
raised inside the setup, which marked the storage broken and moved every
in-progress form to ``MemoryStorage``.  The forms this module exists to persist
were then thrown away at the busiest possible moment.

These tests pin the three halves of the fix: a transient lock is retried, the
retry stays bounded, and the schema is created exactly once no matter how many
updates arrive together.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import os
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

import bot.sqlite_pool as sqlite_pool  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.services.fsm_storage import SqliteFSMStorage  # noqa: E402
from bot.sqlite_pool import connect_sqlite  # noqa: E402


def _fresh_path(tmp: str) -> str:
    """A file that does not exist yet — the state that triggers the race."""
    path = os.path.join(tmp, "fresh.db")
    assert not os.path.exists(path)
    return path


class _held_lock:
    """Hold an EXCLUSIVE lock on ``path`` for the duration of the block.

    Waits until the lock is really held before yielding, so the test cannot race
    the holder thread's own startup.
    """

    def __init__(self, path: str, seconds: float) -> None:
        self._path = path
        self._seconds = seconds
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    def _hold(self) -> None:
        conn = sqlite3.connect(self._path, timeout=30.0, isolation_level=None)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            self._ready.set()
            time.sleep(self._seconds)
            conn.execute("COMMIT")
        finally:
            conn.close()

    def __enter__(self) -> "_held_lock":
        self._thread = threading.Thread(target=self._hold, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=10)
        assert self._ready.is_set(), "could not take the lock"
        return self

    def __exit__(self, *exc) -> None:
        if self._thread is not None:
            self._thread.join(timeout=10)


class _flaky_open:
    """Replace ``_open_and_tune`` for one test, counting how often it ran."""

    def __init__(self, behaviour) -> None:
        self.attempts: list[int] = []
        self._behaviour = behaviour
        self._real = sqlite_pool._open_and_tune

    def _fake(self, path: str):
        self.attempts.append(1)
        return self._behaviour(self._real, path, len(self.attempts))

    def __enter__(self) -> "_flaky_open":
        sqlite_pool._open_and_tune = self._fake
        return self

    def __exit__(self, *exc) -> None:
        sqlite_pool._open_and_tune = self._real


def test_connect_sqlite_retries_a_transient_lock() -> None:
    """The first attempts fail with SQLITE_BUSY; a later one must get through."""

    def busy_twice(real, path, attempt):
        if attempt <= 2:
            raise sqlite3.OperationalError("database is locked")
        return real(path)

    with tempfile.TemporaryDirectory() as tmp:
        with _flaky_open(busy_twice) as flaky:
            conn = connect_sqlite(_fresh_path(tmp))
            try:
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                assert mode.lower() == "wal", mode
            finally:
                conn.close()
        assert len(flaky.attempts) == 3, flaky.attempts


def test_connect_sqlite_gives_up_after_its_budget() -> None:
    """A lock that never clears is reported, not waited on forever."""
    saved = (
        sqlite_pool.BUSY_RETRIES,
        sqlite_pool.BUSY_BACKOFF_SECONDS,
        sqlite_pool.BUSY_BACKOFF_CAP_SECONDS,
    )
    sqlite_pool.BUSY_RETRIES = 3
    sqlite_pool.BUSY_BACKOFF_SECONDS = 0.0
    sqlite_pool.BUSY_BACKOFF_CAP_SECONDS = 0.0

    def always_locked(real, path, attempt):
        raise sqlite3.OperationalError("database is locked")

    try:
        with tempfile.TemporaryDirectory() as tmp:
            with _flaky_open(always_locked) as flaky:
                try:
                    connect_sqlite(_fresh_path(tmp))
                except sqlite3.OperationalError as exc:
                    assert sqlite_pool.is_locked_error(exc), exc
                else:  # pragma: no cover - would hide a real lock
                    raise AssertionError("a permanent lock must surface")
        assert len(flaky.attempts) == 3, flaky.attempts
    finally:
        (
            sqlite_pool.BUSY_RETRIES,
            sqlite_pool.BUSY_BACKOFF_SECONDS,
            sqlite_pool.BUSY_BACKOFF_CAP_SECONDS,
        ) = saved


def test_broken_paths_are_not_retried() -> None:
    """A wrong path must fail at once, not sleep through the retry budget."""
    calls: list[int] = []

    def unopenable(real, path, attempt):
        calls.append(1)
        raise sqlite3.OperationalError("unable to open database file")

    with _flaky_open(unopenable):
        try:
            connect_sqlite("/nonexistent-directory-9d2f/x.db")
        except sqlite3.OperationalError as exc:
            assert not sqlite_pool.is_locked_error(exc), exc
        else:  # pragma: no cover - the path cannot be opened
            raise AssertionError("a bad path should raise")
    assert len(calls) == 1, f"retried a fatal error {len(calls)} times"


def test_connect_sqlite_waits_out_a_real_lock() -> None:
    """A sibling process holding the file must not make the open fail."""
    with tempfile.TemporaryDirectory() as tmp:
        path = _fresh_path(tmp)
        with _held_lock(path, 0.3):
            conn = connect_sqlite(path)
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal", mode
        finally:
            conn.close()


def test_concurrent_first_touch_of_a_new_database_file() -> None:
    """Twelve threads opening the same fresh file must all succeed.

    Opening it the old way — ``sqlite3.connect`` followed straight by the WAL
    pragma — loses this race every few hundred opens.
    """

    def open_and_write(path: str, index: int) -> str:
        conn = connect_sqlite(path)
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY)")
            with conn:
                conn.execute(
                    "INSERT INTO t (k) VALUES (?) ON CONFLICT(k) DO NOTHING",
                    (str(index),),
                )
        finally:
            conn.close()
        return "ok"

    errors: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = _fresh_path(tmp)
        for _ in range(20):
            with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
                results = pool.map(lambda i: open_and_write(path, i), range(12))
                errors.extend(r for r in results if r != "ok")
        probe = sqlite3.connect(path)
        try:
            rows = probe.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        finally:
            probe.close()
        assert rows == 12, rows
    assert errors == [], errors


def test_schema_is_created_once_under_concurrent_updates() -> None:
    """The first touch is single-flight: one setup, and nobody else races it."""
    calls: list[int] = []

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = SqliteFSMStorage(_fresh_path(tmp))
            original = storage._sync_init

            def counting_init() -> None:
                calls.append(1)
                time.sleep(0.05)  # widen the window the race used to win
                original()

            storage._sync_init = counting_init
            await asyncio.gather(
                *(
                    storage.get_data(StorageKey(bot_id=1, chat_id=u, user_id=u))
                    for u in range(40)
                )
            )
            await storage.close()

    asyncio.run(run())
    assert len(calls) == 1, f"schema setup ran {len(calls)} times, expected once"


def test_concurrent_first_touch_of_fsm_database_keeps_state() -> None:
    """Participant form state must survive a stampede, in SQLite, not memory."""
    users = 40

    async def run(tmp: str) -> None:
        storage = SqliteFSMStorage(_fresh_path(tmp))

        async def fill(user_id: int) -> None:
            key = StorageKey(bot_id=1, chat_id=user_id, user_id=user_id)
            for step in range(6):
                await storage.set_data(key, {"user": user_id, "step": step})
                data = await storage.get_data(key)
                assert data.get("user") == user_id, data
                assert data.get("step") == step, data

        # Every coroutine starts on the same not-yet-initialised storage.
        await asyncio.gather(*(fill(u) for u in range(users)))
        assert storage._broken is False, "storage fell back to memory"
        await storage.close()

    with tempfile.TemporaryDirectory() as tmp:
        path = _fresh_path(tmp)
        asyncio.run(run(tmp))
        probe = sqlite3.connect(path)
        try:
            rows = probe.execute("SELECT COUNT(*) FROM fsm_storage").fetchone()[0]
        finally:
            probe.close()
        assert rows == users, f"{rows} rows in SQLite, expected {users}"


def test_both_stores_use_the_same_connection_setup() -> None:
    """One helper owns the pragmas, so the two stores cannot drift apart."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(
            os.path.join(tmp, "app.db"), encryption_key=Fernet.generate_key().decode()
        )
        storage = SqliteFSMStorage(os.path.join(tmp, "fsm.db"))
        try:
            app_conn = db._connect()
            fsm_conn = storage._connect()
            for name, conn in (("db", app_conn), ("fsm", fsm_conn)):
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
                assert mode.lower() == "wal", (name, mode)
                # 1 == NORMAL: durable across crashes, one less fsync per commit.
                sync = conn.execute("PRAGMA synchronous").fetchone()[0]
                assert sync == 1, (name, sync)
                assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1, name
                # 2 == MEMORY: temporary tables never touch the volume.
                assert conn.execute("PRAGMA temp_store").fetchone()[0] == 2, name
                cache = conn.execute("PRAGMA cache_size").fetchone()[0]
                assert cache == -sqlite_pool.CACHE_SIZE_KIB, (name, cache)
            # Both stores hand back the same handle within one thread.
            assert db._connect() is app_conn
            assert storage._connect() is fsm_conn
        finally:
            db.close()
            storage._close_connections()


if __name__ == "__main__":  # pragma: no cover - manual run helper
    test_connect_sqlite_retries_a_transient_lock()
    test_connect_sqlite_gives_up_after_its_budget()
    test_broken_paths_are_not_retried()
    test_connect_sqlite_waits_out_a_real_lock()
    test_concurrent_first_touch_of_a_new_database_file()
    test_schema_is_created_once_under_concurrent_updates()
    test_concurrent_first_touch_of_fsm_database_keeps_state()
    test_both_stores_use_the_same_connection_setup()
    print("ok")
