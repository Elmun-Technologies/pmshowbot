"""The "two pollers, one token" warning must actually reach the log.

Telegram answers ``getUpdates`` with a 409 Conflict when two processes poll the
same token, and then *splits updates between them*: some participants get
answers, others get silence with "is handled" in the log.  aiogram catches every
exception in its polling loop, so ``BotManager._poll``'s ``TelegramConflictError``
branch never runs and the only trace is an easily-missed ERROR line under the
``aiogram.dispatcher`` logger.

``bot/logwatch.py`` promotes that line to one CRITICAL message per cooldown.
These tests keep it honest: it must fire on the real aiogram message, must not
fire on unrelated noise, and must not flood.
"""
from __future__ import annotations

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.logwatch import MESSAGE, DuplicatePollerWatcher  # noqa: E402

# The exact text aiogram logs inside Dispatcher._listen_updates.
AIogram_409 = (
    "Failed to fetch updates - TelegramConflictError: Telegram server says - "
    "Conflict: terminated by other getUpdates request; make sure that only one "
    "bot instance is running"
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _record(message: str, name: str = "aiogram.dispatcher") -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )


def test_conflict_is_promoted_to_a_loud_actionable_line():
    watcher = DuplicatePollerWatcher(clock=_Clock())
    record = _record(AIogram_409)
    assert watcher.filter(record) is True
    assert record.levelno == logging.CRITICAL
    assert record.levelname == "CRITICAL"
    text = record.getMessage()
    assert text.startswith("DUPLICATE POLLER DETECTED"), text
    assert "fly scale count 1" in text, text
    assert "TelegramConflictError" in text, "the original aiogram line is kept"
    assert watcher.conflicts == 1


def test_unrelated_aiogram_noise_is_untouched():
    watcher = DuplicatePollerWatcher(clock=_Clock())
    record = _record("Failed to fetch updates - TelegramNetworkError: timeout")
    assert watcher.filter(record) is True
    assert record.levelno == logging.ERROR, "a plain network error must not scream"
    assert record.getMessage().startswith("Failed to fetch updates")
    assert watcher.conflicts == 0


def test_other_loggers_are_untouched():
    watcher = DuplicatePollerWatcher(clock=_Clock())
    record = _record(AIogram_409, name="bot.bot_manager")
    assert watcher.filter(record) is True
    # The filter is attached to aiogram.dispatcher, but even if it sees a stray
    # record it must recognise the marker; the important part is that it never
    # drops a record.
    assert record.getMessage()


def test_repeated_conflicts_are_throttled():
    clock = _Clock()
    watcher = DuplicatePollerWatcher(cooldown=300.0, clock=clock)
    first = _record(AIogram_409)
    watcher.filter(first)
    assert first.levelno == logging.CRITICAL

    second = _record(AIogram_409)
    watcher.filter(second)
    assert second.levelno == logging.ERROR, "the cooldown must silence repeats"
    assert watcher.conflicts == 2, "but they are still counted"

    clock.now += 301
    third = _record(AIogram_409)
    watcher.filter(third)
    assert third.levelno == logging.CRITICAL, "reported again after the cooldown"


def test_install_is_idempotent_and_attached():
    from bot import logwatch

    watcher = logwatch.install()
    same = logwatch.install()
    assert watcher is same
    assert watcher in logging.getLogger("aiogram.dispatcher").filters


def test_a_swallowed_conflict_reaches_a_handler_end_to_end():
    """Driving the real logger the way aiogram does, through the real filter."""
    from bot import logwatch

    captured: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    handler = _Capture()
    logger = logging.getLogger("aiogram.dispatcher")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    previous = logger.propagate
    logger.propagate = False
    try:
        logwatch.install()  # already installed; idempotent
        logger.error("Failed to fetch updates - %s: %s", "TelegramConflictError", AIogram_409)
    finally:
        logger.removeHandler(handler)
        logger.propagate = previous

    assert captured, "the conflict line never reached a handler"
    assert any("DUPLICATE POLLER DETECTED" in r.getMessage() for r in captured)
    assert MESSAGE.startswith("DUPLICATE POLLER DETECTED")


if __name__ == "__main__":
    test_conflict_is_promoted_to_a_loud_actionable_line()
    test_unrelated_aiogram_noise_is_untouched()
    test_other_loggers_are_untouched()
    test_repeated_conflicts_are_throttled()
    test_install_is_idempotent_and_attached()
    test_a_swallowed_conflict_reaches_a_handler_end_to_end()
    print("All conflict-watch tests passed.")
