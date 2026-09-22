"""Saving participant uploads to the media volume, without stalling the bot.

Three production lessons are encoded here.

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

**Nothing a participant sends may wait for one of those downloads.**  A ceiling
turns a five minute freeze into a fifteen second one, which is still a bot that
"hangs after the first photo": the participant sent four photos in a row and the
second one is handled in the same handler, one after another, so the third waits
for the second's download, the fourth for the third's, and the "what did you
change?" question waits for all of them.  A slow photo therefore delays every
later photo *and* every later message.  :class:`PhotoIngest` is the fix: the
update records the photo and answers the participant immediately, and the
download runs as its own task.  Four photos posted as an album are then fetched
concurrently instead of one after another, and a stalled one delays nothing but
itself — the participant is told to resend that single side, and the slot it
belongs to is the slot it fills.

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
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from ..executors import run_heavy

logger = logging.getLogger(__name__)

# One ceiling for the whole download.  Telegram's own CDN answers in well under
# a second; this only exists so that a stalled connection cannot hold a
# participant (and their queued messages) hostage.
DOWNLOAD_TIMEOUT_SECONDS = 15.0

# A volume that stops answering must not hold the participant forever either.
WRITE_TIMEOUT_SECONDS = 10.0

# How long a finished upload stays in the ledger that answers "is this side
# already on the volume?".  Longer than any realistic registration, short enough
# that a busy event does not grow the table without bound.
RECORD_TTL_SECONDS = 3600.0

# Ceiling on remembered uploads per participant (four sides plus mod photos, and
# a retry of each).
MAX_RECORDS_PER_USER = 32


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


async def files_exist(paths: list[str]) -> list[bool]:
    """Stat ``paths`` in a worker thread; ``True`` where a file is there.

    Used by the registration flow to tell "this side is on the volume" from
    "this side is missing" (a failed download, or a process that restarted in
    the middle of one, leaving the FSM pointing at a file that was never
    written).  It runs on the *fast* pool — the same one SQLite uses — because a
    stat is cheap; the slow-work pool is for Drive uploads and ticket rendering.
    """
    if not paths:
        return []

    def _check() -> list[bool]:
        return [bool(path) and os.path.exists(path) for path in paths]

    return await asyncio.to_thread(_check)


# Telegram hands out a file_id for every uploaded photo, and that id can be sent
# again without re-uploading anything.  Direction banners are re-sent at the
# start of *every* registration, so the first participant of the day pays for
# the upload and everybody else sends an id.  The key carries size and mtime, so
# replacing the banner in the admin panel invalidates the entry by itself.
_ASSET_FILE_IDS: dict[tuple[int, str, float, int], str] = {}
_ASSET_CACHE_LIMIT = 64


def forget_asset_file_id(bot: Any, path: str) -> None:
    """Drop the cached file_id of one asset (used when an upload replaces it)."""
    for key in [key for key in _ASSET_FILE_IDS if key[0] == id(bot) and key[1] == path]:
        _ASSET_FILE_IDS.pop(key, None)


async def send_cached_photo(
    bot: Any,
    chat_id: int,
    path: str,
    caption: str = "",
    *,
    reply_markup: Any = None,
) -> bool:
    """Send an image from the volume, uploading it at most once per bot.

    ``answer_photo(FSInputFile(path))`` streams the whole file to Telegram every
    time it is called — on the event loop, inside the participant's update, for
    a file that has not changed since the last registration.  This sends the
    remembered ``file_id`` instead, and only uploads when there is no usable id
    (first send, or the file was replaced).

    Returns ``False`` when Telegram refused both, so the caller can fall back to
    a plain text message instead of leaving the participant without an answer.
    """
    try:
        stat = os.stat(path)
    except OSError:
        return False
    key = (id(bot), path, stat.st_mtime, stat.st_size)

    cached = _ASSET_FILE_IDS.get(key)
    if cached:
        try:
            await bot.send_photo(chat_id, cached, caption=caption or None)
            return True
        except Exception:  # noqa: BLE001 - a stale id is expected sometimes
            logger.info("Telegram refused the cached file_id for %s — uploading it again", path)
            _ASSET_FILE_IDS.pop(key, None)

    try:
        from aiogram.types import FSInputFile

        sent = await bot.send_photo(
            chat_id, FSInputFile(path), caption=caption or None, reply_markup=reply_markup
        )
    except Exception:  # noqa: BLE001 - the caller falls back to a text message
        logger.exception("Could not send the image %s", path)
        return False

    photo = getattr(sent, "photo", None)
    if photo:
        if len(_ASSET_FILE_IDS) >= _ASSET_CACHE_LIMIT:
            _ASSET_FILE_IDS.clear()
        _ASSET_FILE_IDS[key] = photo[-1].file_id
    return True


@dataclass
class Upload:
    """One photo on its way to the volume."""

    user_id: int
    chat_id: int
    file_id: str
    path: str
    # "side" (one of the four required sides) or "mod" (an optional close-up).
    kind: str = "side"
    # Position of this photo in its group: the side index, or the mod number.
    index: int = 0
    # Human label for the "send it again" message ("левая сторона", "фото
    # изменений №1").  Built by the handler, so the wording stays in texts.py.
    label: str = ""
    task: Optional[asyncio.Task] = None
    started_at: float = field(default_factory=time.monotonic)
    finished_at: float = 0.0
    ok: Optional[bool] = None

    @property
    def finished(self) -> bool:
        return self.ok is not None

    @property
    def pending(self) -> bool:
        return self.ok is None and self.task is not None and not self.task.done()


class PhotoIngest:
    """Downloads participant photos off the critical path of the update.

    The registration step used to do this inline: read the FSM, download, write
    to the volume, answer.  Everything the participant sent after that photo —
    the next photo, a question, the phone number — sat in the per-user queue
    until the download finished, which is what "the bot hangs after the first
    photo" looked like from the client's side (``repro_slowdisk.py`` measures it:
    a stalled download delayed the next message by 4.81 s before the ceiling was
    added, and by the whole ceiling after).

    Two things are tracked here:

    * **pending** uploads, so the end of the form can wait for the photos that
      are still in flight (the application row must not point at files that do
      not exist yet);
    * **failed** ones, so the side a participant was asked to resend is the side
      their next photo fills — not the next empty slot, which would silently
      swap the sides of the car.

    The ledger is per process and in memory on purpose: the FSM in SQLite is the
    source of truth for *which* photos were sent, and this only remembers how
    each download went.  A restart loses the ledger, which the flow treats as
    "unknown" and resolves by looking at the volume (see
    :func:`files_exist`).
    """

    def __init__(self) -> None:
        # bot -> user id -> path -> Upload.  The Bot itself is the key, held
        # weakly: it keeps two tenants (each with its own bot and its own
        # volume) apart without an API call to learn the bot's id, and a worker
        # that is replaced on a hot reload does not keep its client alive here.
        self._records: "weakref.WeakKeyDictionary[Any, dict[int, dict[str, Upload]]]" = (
            weakref.WeakKeyDictionary()
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------
    def _ledger(self, bot: Any, user_id: int) -> dict[str, Upload]:
        """The per-participant part of the ledger, created on first use."""
        per_user = self._records.get(bot)
        if per_user is None:
            per_user = {}
            self._records[bot] = per_user
        return per_user.setdefault(int(user_id), {})

    def _prune(self, records: dict[str, Upload]) -> None:
        """Drop old, successful entries so the ledger stays bounded."""
        if len(records) <= MAX_RECORDS_PER_USER:
            return
        now = time.monotonic()
        stale = [
            path
            for path, upload in records.items()
            if upload.finished
            and upload.ok
            and now - upload.finished_at > RECORD_TTL_SECONDS
        ]
        for path in stale:
            records.pop(path, None)
        if len(records) > MAX_RECORDS_PER_USER:
            finished = sorted(
                (u for u in records.values() if u.finished),
                key=lambda u: u.finished_at,
            )
            for upload in finished[: len(records) - MAX_RECORDS_PER_USER]:
                records.pop(upload.path, None)

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    def submit(
        self,
        bot: Any,
        *,
        user_id: int,
        chat_id: int,
        file_id: str,
        path: str,
        kind: str = "side",
        index: int = 0,
        label: str = "",
        notify: Optional[Callable[[Upload], Any]] = None,
    ) -> Upload:
        """Start downloading ``file_id`` into ``path`` and return immediately.

        ``notify`` is awaited (or called) when the download fails, so the
        participant learns that *that* photo has to be sent again while the
        conversation keeps moving.
        """
        records = self._ledger(bot, user_id)
        self._prune(records)
        upload = Upload(
            user_id=int(user_id),
            chat_id=int(chat_id),
            file_id=file_id,
            path=path,
            kind=kind,
            index=index,
            label=label,
        )
        records[path] = upload
        upload.task = asyncio.create_task(
            self._download_with_bot(bot, upload, notify),
            name=f"photo-download:{user_id}:{kind}:{index}",
        )
        return upload

    async def _download_with_bot(
        self, bot: Any, upload: Upload, notify: Optional[Callable[[Upload], Any]]
    ) -> None:
        """``_download`` with the bot bound: one obvious place holds the client."""
        try:
            upload.ok = await save_telegram_photo(bot, upload.file_id, upload.path)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a download must never kill its task
            logger.exception("Photo download task failed for %s", upload.path)
            upload.ok = False
        upload.finished_at = time.monotonic()

        if upload.ok:
            logger.debug(
                "Photo %s saved to %s in %.2f s",
                upload.file_id,
                upload.path,
                upload.finished_at - upload.started_at,
            )
            return

        logger.warning(
            "%s photo %s did not reach %s — asking the participant to resend it",
            upload.kind,
            upload.file_id,
            upload.path,
        )
        if notify is None:
            return
        try:
            result = notify(upload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:  # noqa: BLE001 - the notice is best effort
            logger.debug("Could not ask the participant to resend %s", upload.path, exc_info=True)

    def statuses(self, bot: Any, user_id: int) -> dict[str, str]:
        """``path -> "pending" | "done" | "failed"`` for one participant.

        A path that is not in the ledger is simply absent: the caller treats
        that as "unknown" and checks the volume instead.
        """
        out: dict[str, str] = {}
        for path, upload in self._ledger(bot, user_id).items():
            if upload.pending:
                out[path] = "pending"
            elif upload.ok:
                out[path] = "done"
            elif upload.finished:
                out[path] = "failed"
        return out

    def failed(self, bot: Any, user_id: int, *, kind: Optional[str] = None) -> list[Upload]:
        """Uploads of this participant that failed, oldest first."""
        uploads = [
            upload
            for upload in self._ledger(bot, user_id).values()
            if upload.finished and not upload.ok and (kind is None or upload.kind == kind)
        ]
        return sorted(uploads, key=lambda u: u.started_at)

    def is_pending(self, bot: Any, user_id: int) -> bool:
        """True while any of this participant's photos is still being fetched."""
        return any(upload.pending for upload in self._ledger(bot, user_id).values())

    async def wait(
        self, bot: Any, user_id: int, *, timeout: float
    ) -> list[Upload]:
        """Wait up to ``timeout`` for this participant's downloads to finish.

        Returns the uploads that are *still* not on the volume afterwards —
        failed, or too slow to wait for any longer.  Never raises: at the end of
        the form the participant must get an answer even if the volume is
        misbehaving.
        """
        records = list(self._ledger(bot, user_id).values())
        tasks = [u.task for u in records if u.task is not None and not u.task.done()]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)
        return [
            upload
            for upload in records
            if not upload.ok and not upload.pending
        ] + [upload for upload in records if upload.pending]

    def forget(self, bot: Any, user_id: int, path: str) -> None:
        """Drop the record of one path (the participant replaced that photo)."""
        self._ledger(bot, user_id).pop(path, None)

    async def cancel_pending(self) -> None:
        """Cancel in-flight downloads.  Called on shutdown; safe to call twice."""
        tasks = [
            upload.task
            for per_user in self._records.values()
            for records in per_user.values()
            for upload in records.values()
            if upload.task is not None and not upload.task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._records.clear()


# One ledger for the process: every tenant's dispatcher shares it, exactly like
# the event loop and the executors they run on.
ingest = PhotoIngest()

__all__ = [
    "save_telegram_photo",
    "write_bytes",
    "files_exist",
    "PhotoIngest",
    "Upload",
    "ingest",
    "DOWNLOAD_TIMEOUT_SECONDS",
    "WRITE_TIMEOUT_SECONDS",
]
