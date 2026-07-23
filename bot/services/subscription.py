"""Channel subscription check.

The bot must be an administrator of ``REQUIRED_CHANNEL`` for ``get_chat_member``
to work reliably.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)

_SUBSCRIBED_STATUSES = {"member", "administrator", "creator"}


def normalize_channel(channel: str):
    """Return an int chat id for numeric channels, else the string as-is.

    Telegram accepts both, but passing a real int avoids any string/int
    ambiguity for ids like ``-1002078702028``.
    """
    c = channel.strip()
    body = c[1:] if c.startswith("-") else c
    if body.isdigit():
        return int(c)
    return c


async def is_subscribed(bot: Bot, channel: str, user_id: int) -> bool:
    """Return True if ``user_id`` is a member of ``channel``.

    On API errors (e.g. bot not admin, channel misconfigured) this logs a
    warning and returns False so the user is asked to subscribe rather than
    being let through silently.
    """
    try:
        member = await bot.get_chat_member(
            chat_id=normalize_channel(channel), user_id=user_id
        )
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        logger.warning(
            "Subscription check failed for channel %s — the bot is most likely "
            "NOT an admin of that channel (or the id is wrong): %s",
            channel,
            exc,
        )
        return False
    return member.status in _SUBSCRIBED_STATUSES


def channel_url(channel: str, override: str = "") -> str:
    """Build a t.me URL for the subscribe button.

    Priority: explicit ``override`` (CHANNEL_URL) > @username > full http(s) url.
    For a bare numeric id with no override there is no reliable public link, so
    this returns "" and the caller omits the link button.
    """
    if override and override.strip():
        return override.strip()
    channel = channel.strip()
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    if channel.startswith("https://") or channel.startswith("http://"):
        return channel
    return ""
