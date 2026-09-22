"""Make "two pollers, one token" visible in the log.

If the same bot token is polled from two places — a second Fly machine, a
leftover local ``python -m bot.main``, or an old worker that has not shut down
— Telegram answers ``getUpdates`` with a 409 Conflict and then *splits the
updates between the two processes*.  Each participant's next message goes to
whichever process wins the race, so the bot answers some updates and silently
ignores others.  From the outside that is indistinguishable from a frozen bot.

The trap is that the conflict never surfaces as an error anybody notices:
aiogram catches **every** exception inside its polling loop and retries, so the
``TelegramConflictError`` branch in :meth:`bot.bot_manager.BotManager._poll`
never runs.  The only trace is this INFO/ERROR line, repeated with a backoff,
under the ``aiogram.dispatcher`` logger:

    Failed to fetch updates - TelegramConflictError: Conflict: terminated by
    other getUpdates request; make sure that only one bot instance is running

This filter promotes that line, at most once per ``COOLDOWN_SECONDS``, to a
single CRITICAL message that says what it means and what to do.  It is
throttled so that a duplicate poller cannot flood the log.
"""
from __future__ import annotations

import logging
import time

CONFLICT_MARKERS = (
    "TelegramConflictError",
    "terminated by other getUpdates request",
)

# One loud line per five minutes is enough to identify the problem; the
# underlying error keeps being logged by aiogram in between.
COOLDOWN_SECONDS = 300.0

MESSAGE = (
    "DUPLICATE POLLER DETECTED: Telegram refuses getUpdates because another "
    "process is polling this same bot token. Telegram now splits updates "
    "between the two processes, so some participants get answers and others get "
    "silence with 'is handled' in the log. Fix: run exactly one machine "
 "(`fly scale count 1`), stop any local `python -m bot.main`, and check for a "
    "second deployment. (Details: %s)"
)


class DuplicatePollerWatcher(logging.Filter):
    """Rewrite aiogram's swallowed conflict error into an actionable warning."""

    def __init__(self, *, cooldown: float = COOLDOWN_SECONDS, clock=time.monotonic) -> None:
        super().__init__(name="aiogram.dispatcher")
        self._cooldown = cooldown
        self._clock = clock
        self._last = -cooldown
        self.conflicts = 0

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        if not any(marker in message for marker in CONFLICT_MARKERS):
            return True

        self.conflicts += 1
        now = self._clock()
        if now - self._last < self._cooldown:
            return True  # already reported; keep aiogram's own line

        self._last = now
        record.msg = MESSAGE % message
        record.args = ()
        record.levelno = logging.CRITICAL
        record.levelname = "CRITICAL"
        return True


_watcher: DuplicatePollerWatcher | None = None


def install() -> DuplicatePollerWatcher:
    """Attach the watcher to aiogram's dispatcher logger (idempotent)."""
    global _watcher
    if _watcher is not None:
        return _watcher
    _watcher = DuplicatePollerWatcher()
    logging.getLogger("aiogram.dispatcher").addFilter(_watcher)
    return _watcher


__all__ = ["DuplicatePollerWatcher", "install", "CONFLICT_MARKERS", "MESSAGE"]
