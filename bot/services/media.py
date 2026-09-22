"""Saving participant uploads to the media volume, without stalling the bot.

Two production lessons are encoded here.

**The download must be bounded.**  ``bot.download(file_id, destination=path)``
uses aiogram's defaults: a 30 second limit on *streaming* plus the session's own
request timeout for ``getFile``, and no limit at all on a connection that simply
goes quiet.  Photo downloads run inside :class:`SerializePerUserMiddleware`, so
one stalled photo did not just delay that photo — it held *everything else that
participant sent* for as long as the stall lasted, with no answer and no error
in the log.  Measured in ``diagnostics/repro_slowdisk.py``: a 5 second stall
delayed the participant's next message by 4.81 seconds.  The whole operation
(``getFile`` plus the stream) is therefore wrapped in one explicit timeout, and
a failure is reported to the participant so they can resend.

**The write must not steal the database's threads.**  aiogram streams into the
destination with ``aiofiles``, which runs every chunk write through
``loop.run_in_executor(None, ...)`` — the *default* executor, the same pool that
``asyncio.to_thread`` uses for every SQLite query (5 threads on a 1-vCPU Fly
machine).  A slow network volume filled that pool with disk writes and the
database queued behind them, which is a whole-bot stall rather than one user's
delay.  The bytes are therefore read into memory (a Telegram photo is at most
10 MB) and written by :func:`bot.executors.run_heavy`, on the pool reserved for
slow work.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from ..executors import run_heavy

logger = logging.getLogger(__name__)

# One ceiling for the whole download.  Telegram's own CDN answers in well under
# a second; this only exists so that a stalled connection cannot hold a
# participant (and their queued messages) hostage.
DOWNLOAD_TIMEOUT_SECONDS = 15.0

# A volume that stops answering must not hold the participant forever either.
WRITE_TIMEOUT_SECONDS = 10.0


def write_bytes(path: str, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically.

    The temporary file sits next to the target so ``os.replace`` stays inside
    one filesystem (a rename across devices is a copy).  A crash mid-write
    leaves the old file intact instead of a truncated photo.
    """
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.part"
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


async def save_telegram_photo(
    bot: Any,
    file_id: str,
    path: str,
    *,
    timeout: float | None = None,
) -> bool:
    """Download one Telegram photo into ``path``; ``False`` instead of raising.

    The caller answers the participant when this returns ``False``, which is the
    difference between "send it again, please" and silence.
    """
    if timeout is None:
        timeout = DOWNLOAD_TIMEOUT_SECONDS
    buffer = None
    try:
        # No destination: aiogram returns a BytesIO, so nothing is written from
        # the event loop and the download itself is cancellable.
        buffer = await asyncio.wait_for(bot.download(file_id), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning(
            "Download of photo %s timed out after %.0f s — asking the participant to resend",
            file_id,
            timeout,
        )
        return False
    except Exception:  # noqa: BLE001 - network/CDN failures are expected
        logger.exception("Could not download photo %s", file_id)
        return False

    if buffer is None:
        logger.warning("Telegram returned no content for photo %s", file_id)
        return False

    try:
        data = buffer.getvalue()
    finally:
        try:
            buffer.close()
        except Exception:  # noqa: BLE001 - a BytesIO close cannot fail meaningfully
            logger.debug("Could not close the download buffer", exc_info=True)

    try:
        await asyncio.wait_for(
            run_heavy(write_bytes, path, data), timeout=WRITE_TIMEOUT_SECONDS
        )
    except asyncio.TimeoutError:
        logger.error(
            "Writing photo %s to %s timed out after %.0f s — is the volume stuck?",
            file_id,
            path,
            WRITE_TIMEOUT_SECONDS,
        )
        return False
    except OSError:
        logger.exception("Could not write photo %s to %s", file_id, path)
        return False
    return True


__all__ = ["save_telegram_photo", "write_bytes", "DOWNLOAD_TIMEOUT_SECONDS"]
