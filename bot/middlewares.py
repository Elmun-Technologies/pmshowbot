"""Dispatcher middlewares."""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from . import texts
from .states import Registration


class RegistrationClosedMiddleware(BaseMiddleware):
    """Block the registration form from progressing once registration is closed.

    Runs only on the registration router. When ``registration_closed`` is on
    and the update arrives while the user is inside one of the form steps
    (country → phone), the middleware drops the FSM state and answers with the
    "registration finished" notice instead of letting the step handler run. A
    participant who started the form before the deadline therefore cannot
    finish it afterwards.

    ``/start`` and the language step are deliberately left to their own
    handlers: they need the bilingual notice (before a language is chosen) and
    the status view for people who already have an application.
    """

    _BLOCKED_STATES = frozenset(
        s for s in Registration.__all_states__ if s != Registration.language
    )

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        config = data.get("config")
        if config is None or not config.registration_closed:
            return await handler(event, data)

        state = data.get("state")
        if state is None:
            return await handler(event, data)

        current = await state.get_state()
        if current not in self._BLOCKED_STATES:
            return await handler(event, data)

        # Someone with a pending/approved application should still reach their
        # status (e.g. /start) rather than being cut off mid-form.
        db = data.get("db")
        user = data.get("event_from_user")
        if db is not None and user is not None:
            if await db.has_active_application(user.id) is not None:
                return await handler(event, data)

        lang = (await state.get_data()).get("lang", "ru")
        await state.clear()

        text = texts.registration_closed_for_tenant(lang, config)
        if isinstance(event, CallbackQuery):
            await event.answer()
            if event.message is not None:
                await event.message.answer(text)
        elif isinstance(event, Message):
            await event.answer(text)
        return None


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
