"""Dispatcher-level error handling.

aiogram separates an update from its error: when a handler raises, the
exception is propagated as an ``error`` event and, if nothing handles it, it is
only *logged*.  For a participant that is indistinguishable from a frozen bot —
they tapped a button, got no answer, and tried again.

This handler answers whatever is answerable (a callback query gets an alert, a
private message gets a short "technical error, press /start" note), logs the
failure with the tenant slug and update id, and marks the event as handled so
the loop keeps processing the rest of the queue.
"""
from __future__ import annotations

import logging
from typing import Any

from aiogram.types import ErrorEvent

from . import texts

logger = logging.getLogger(__name__)


async def handle_error(event: ErrorEvent, **kwargs: Any) -> Any:
    """Report a failed update to the user and to the log. Never raises."""
    update = event.update
    config = kwargs.get("config")
    slug = getattr(config, "tenant_slug", "unknown")
    logger.error(
        "[%s] Update %s failed: %s: %s",
        slug,
        getattr(update, "update_id", "?"),
        type(event.exception).__name__,
        event.exception,
        exc_info=event.exception,
    )

    lang = await _language(kwargs.get("state"))
    text = texts.T(lang).RECOVER_TECHNICAL if lang else texts.RECOVER_TECHNICAL_BILINGUAL

    callback = getattr(update, "callback_query", None)
    if callback is not None:
        try:
            await callback.answer(text, show_alert=True)
        except Exception:  # noqa: BLE001 - the callback may be too old to answer
            logger.debug("Could not answer the failed callback query", exc_info=True)

    message = getattr(update, "message", None)
    if message is not None and getattr(message.chat, "type", "") == "private":
        try:
            await message.answer(text, parse_mode=None)
        except Exception:  # noqa: BLE001 - the chat may be gone
            logger.debug("Could not notify the participant about the failure", exc_info=True)
    return True


async def _language(state: Any) -> str:
    """Language chosen in the current form, when the FSM data is available."""
    if state is None:
        return ""
    try:
        data = await state.get_data()
    except Exception:  # noqa: BLE001 - storage may be unavailable
        return ""
    lang = (data or {}).get("lang")
    return lang if lang in ("ru", "uz") else ""
