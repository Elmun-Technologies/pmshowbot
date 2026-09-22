"""Last-resort router: no update is ever left without an answer.

The reported symptom was "the bot freezes", and the only production log line
was

    Update id=322515500 is handled. Duration 1 ms by bot id=8933846661

One millisecond and no error: nothing crashed, nothing matched.  Aiogram logs
"is handled" whether the update was answered or was quietly dropped after no
handler matched, so a participant who sends something the form does not expect
sees a dead bot while the log looks perfectly healthy.

That is what this router exists for.  It is attached **last**, after
registration, moderation, badge and number handlers, so it can only ever see
updates no other handler wanted.  It answers them:

* mid-form input that no step expected (an unknown command, a sticker, a
  document) repeats the current step instead of being swallowed — this is
  measured by ``diagnostics/silence_audit.py``;
* input from a participant who has no active form gets their status when an
  application exists, and a short "press /start" note otherwise.  Before this,
  such a message produced *nothing at all*: the two existing catch-alls were
  registered with ``StateFilter(*Registration.__all_states__)``, which by
  definition cannot match a user who has no state.

It is restricted to private chats so moderation traffic in the admin group is
never touched, and every code path is wrapped so that the safety net itself can
never be the reason a participant gets silence.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import texts
from ..config import Config, TenantConfig
from ..db import Database

logger = logging.getLogger(__name__)


async def _lang(state: FSMContext) -> str:
    """Participant's language, "ru" when the form does not know it yet."""
    try:
        lang = (await state.get_data()).get("lang", "ru")
    except Exception:  # noqa: BLE001 - storage trouble must not silence us
        logger.debug("Could not read the FSM data for the language", exc_info=True)
        return "ru"
    return lang if lang in ("ru", "uz") else "ru"


async def _fallback(
    message: Message | None,
    query: CallbackQuery | None,
    state: FSMContext,
    config: Config | TenantConfig,
    db: Database,
    *,
    notify: bool = True,
) -> None:
    """Answer the current step, or explain how to start — never return silent.

    ``notify=False`` (a button pressed in the moderation group) still clears the
    callback's spinner but sends nothing into the group: the group is the
    moderators' workspace, and the safety net must not add noise there.
    """
    if not notify:
        return
    lang = await _lang(state)
    current = await state.get_state()
    if current is not None:
        # A step exists but nothing matched it: repeat the question so the
        # participant knows what the bot expects.
        from .registration import _repeat_step

        if message is not None and await _repeat_step(message, state, lang, config, db):
            return

    user = None
    if message is not None:
        user = message.from_user
    elif query is not None:
        user = query.from_user
    if user is None:
        return

    if current is not None:
        # State exists but its step is unknown (older keyboard, renamed state).
        await state.clear()

    try:
        active = await db.has_active_application(user.id)
    except Exception:  # noqa: BLE001 - answer first, diagnose later
        logger.exception("Could not look up the latest application for %s", user.id)
        active = None

    target = message if message is not None else (query.message if query else None)
    if target is None:
        return

    if active is not None:
        from .mynumber import show_status

        await show_status(target, active, config)
        return

    text = texts.T(lang).UNRECOGNIZED
    try:
        await target.answer(text)
    except Exception:  # noqa: BLE001 - the chat may be gone or too old
        logger.exception("Could not answer an unmatched update in chat %s", target.chat.id)


async def unmatched_message(
    message: Message, state: FSMContext, config: Config | TenantConfig, db: Database
) -> None:
    """Any private message no handler claimed."""
    if message.from_user is None or message.from_user.is_bot:
        return
    logger.info(
        "Unmatched message from %s (state=%s) — answering from the safety net",
        message.from_user.id,
        await state.get_state(),
    )
    await _fallback(message, None, state, config, db)


async def unmatched_callback(
    query: CallbackQuery, state: FSMContext, config: Config | TenantConfig, db: Database
) -> None:
    """Any callback query no handler claimed — answer it, or the spinner stays."""
    await _answer_query(query)
    chat_type = getattr(getattr(query.message, "chat", None), "type", "")
    logger.info(
        "Unmatched callback %r from %s (state=%s) — answering from the safety net",
        query.data,
        query.from_user.id,
        await state.get_state(),
    )
    await _fallback(
        query.message, query, state, config, db, notify=chat_type == "private"
    )


async def _answer_query(query: CallbackQuery) -> None:
    try:
        await query.answer()
    except Exception:  # noqa: BLE001 - an expired query cannot be answered
        logger.debug("Could not acknowledge callback %s", query.id, exc_info=True)


def create_router() -> Router:
    """Fresh safety-net router for one tenant dispatcher."""
    fresh = Router(name="stay_alive")
    # Private chats only: the moderation group keeps its own handlers.
    fresh.message.register(unmatched_message, F.chat.type == "private")
    fresh.callback_query.register(unmatched_callback)
    return fresh


__all__ = ["create_router", "unmatched_message", "unmatched_callback", "Any"]
