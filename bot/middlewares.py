"""Dispatcher middlewares."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class SerializePerUserMiddleware(BaseMiddleware):
    """Handle one update at a time per user.

    aiogram runs every update in its own task, so updates that arrive together
    interleave. The registration form reads the FSM state, awaits a photo
    download, then writes the state back — with an album (Telegram delivers its
    photos as several near-simultaneous updates) every handler reads the same
    "0 photos so far", picks the same side, and writes to the same file. Three
    of the four photos are lost that way.

    Serializing per user removes the interleaving without slowing anything
    down: different participants still register concurrently.
    """

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        self._waiting: dict[int, int] = {}
        self._guard = asyncio.Lock()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:  # channel posts and the like have no user to serialize on
            return await handler(event, data)

        lock = await self._acquire_slot(user.id)
        try:
            async with lock:
                return await handler(event, data)
        finally:
            await self._release_slot(user.id)

    async def _acquire_slot(self, user_id: int) -> asyncio.Lock:
        # The bookkeeping is itself guarded, so two updates for the same user
        # can never end up with two different locks.
        async with self._guard:
            lock = self._locks.get(user_id)
            if lock is None:
                lock = self._locks[user_id] = asyncio.Lock()
            self._waiting[user_id] = self._waiting.get(user_id, 0) + 1
            return lock

    async def _release_slot(self, user_id: int) -> None:
        # Drop the lock once nobody is using it, so the table doesn't grow by
        # one entry per person who ever messaged the bot.
        async with self._guard:
            remaining = self._waiting.get(user_id, 1) - 1
            if remaining <= 0:
                self._waiting.pop(user_id, None)
                self._locks.pop(user_id, None)
            else:
                self._waiting[user_id] = remaining
