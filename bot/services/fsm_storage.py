"""SQLite-backed FSM storage.

The registration form spans a dozen updates (language → country → plate →
direction → 4 photos → modifications → phone).  ``MemoryStorage`` keeps that
progress in the process, so **any restart wipes it**: a redeploy, a Fly machine
restart, or just saving a tenant setting in the admin panel (the worker is
hot-restarted on purpose) leaves every participant mid-form with a bot that no
longer answers — the "бот завис" participants reported during the event.

This storage keeps states in the same SQLite file as the applications, so a
participant can continue after a restart.  Each call runs in a worker thread
(``asyncio.to_thread``) exactly like the rest of the data layer, and a broken
database degrades to in-memory storage instead of taking the bot down.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS {table} (
    key        TEXT PRIMARY KEY,
    state      TEXT,
    data       TEXT NOT NULL DEFAULT '{{}}',
    updated_at REAL NOT NULL
);
"""

# A form that has not been touched for a month belongs to nobody.
MAX_AGE_SECONDS = 30 * 24 * 3600


class _BrokenStorage(RuntimeError):
    """Raised internally when the state database cannot be used."""


class _Unset:
    """Sentinel meaning "leave this column alone"."""


_UNSET = _Unset()


def _key_of(key: StorageKey) -> str:
    """Stable string key for one chat/thread — bot id included, tenants isolated."""
    return "|".join(
        str(part)
        for part in (
            key.bot_id,
            key.chat_id,
            key.user_id,
            key.thread_id,
            key.business_connection_id,
            key.destiny,
        )
    )


class SqliteFSMStorage(BaseStorage):
    """State storage that survives process restarts."""

    def __init__(self, path: str, *, table: str = "fsm_storage") -> None:
        super().__init__()
        self._path = path
        self._table = table
        self._fallback = MemoryStorage()
        self._ready = False
        self._broken = not bool(path)
        if path:
            parent = os.path.dirname(path)
            if parent:
                try:
                    os.makedirs(parent, exist_ok=True)
                except OSError:
                    logger.warning(
                        "Cannot create %s for FSM states — using memory storage", parent
                    )
                    self._broken = True

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _sync_init(self) -> None:
        with self._connect() as conn:
            conn.execute(_SCHEMA.format(table=self._table))
            conn.execute(
                f"DELETE FROM {self._table} WHERE updated_at < ?",
                (time.time() - MAX_AGE_SECONDS,),
            )
        self._ready = True

    async def _run(self, func, *args):
        """Run one SQLite operation in a thread, downgrading to memory if it fails."""
        if self._broken:
            raise _BrokenStorage
        if not self._ready:
            try:
                await asyncio.to_thread(self._sync_init)
            except Exception:
                logger.exception(
                    "FSM storage database unavailable (%s) — keeping states in memory",
                    self._path,
                )
                self._broken = True
                raise _BrokenStorage from None
        return await asyncio.to_thread(func, *args)

    def _sync_set(self, key: str, state: Any, data: Optional[dict]) -> None:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT state, data FROM {self._table} WHERE key = ?", (key,)
            ).fetchone()
            current_state = row["state"] if row else None
            current_data = row["data"] if row else "{}"
            new_state = current_state if isinstance(state, _Unset) else state
            new_data = current_data if data is None else json.dumps(data, ensure_ascii=False)
            conn.execute(
                f"INSERT INTO {self._table} (key, state, data, updated_at) VALUES (?, ?, ?, ?) "
                f"ON CONFLICT(key) DO UPDATE SET state = excluded.state, "
                f"data = excluded.data, updated_at = excluded.updated_at",
                (key, new_state, new_data, time.time()),
            )

    def _sync_state(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT state FROM {self._table} WHERE key = ?", (key,)
            ).fetchone()
        return row["state"] if row else None

    def _sync_data(self, key: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT data FROM {self._table} WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return {}
        try:
            value = json.loads(row["data"] or "{}")
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    # ------------------------------------------------------------------
    # BaseStorage API
    # ------------------------------------------------------------------
    async def set_state(self, key: StorageKey, state: Any = None) -> None:
        value = state.state if isinstance(state, State) else state
        try:
            await self._run(self._sync_set, _key_of(key), value, None)
        except _BrokenStorage:
            await self._fallback.set_state(key, state)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        try:
            return await self._run(self._sync_state, _key_of(key))
        except _BrokenStorage:
            return await self._fallback.get_state(key)

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        try:
            await self._run(self._sync_set, _key_of(key), _UNSET, dict(data))
        except _BrokenStorage:
            await self._fallback.set_data(key, data)

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        try:
            return await self._run(self._sync_data, _key_of(key))
        except _BrokenStorage:
            return await self._fallback.get_data(key)

    async def close(self) -> None:
        await self._fallback.close()
