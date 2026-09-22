"""A photo upload must never leave the participant waiting without an answer.

Production case: four photos at a few hundred KB each, written to a Fly.io
network volume, each download performed *inside* the per-user lock.  aiogram's
defaults bounded only the stream (30 s) and not the whole operation, so one
stalled connection held that participant — and everything they sent afterwards —
for as long as the stall lasted.  Measured with
``diagnostics/repro_slowdisk.py``: a 5 second stall delayed the participant's
next message by 4.81 seconds.

``bot.services.media`` therefore bounds the whole download, reports failure
instead of raising, and writes the bytes on the slow-work pool so a throttled
volume cannot take SQLite's threads with it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from aiogram.types import Chat, Message, PhotoSize, Update, User  # noqa: E402

from harness import BotHarness, FakeSession  # noqa: E402
from bot.services import media  # noqa: E402
from bot.states import Registration  # noqa: E402

USER = 5150


class _Bot:
    """Minimal stand-in for the two ``Bot.download`` outcomes we care about."""

    def __init__(self, *, data: bytes = b"jpeg", delay: float = 0.0, error: Exception | None = None):
        self.data = data
        self.delay = delay
        self.error = error
        self.calls = 0

    async def download(self, file, destination=None):
        self.calls += 1
        await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return io.BytesIO(self.data)


def test_happy_path_writes_the_file_atomically():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "user", "left.jpg")
            bot = _Bot(data=b"x" * 2048)
            assert await media.save_telegram_photo(bot, "f1", path) is True
            assert open(path, "rb").read() == b"x" * 2048
            # No half-written temp file left behind for the ticket renderer.
            assert not os.path.exists(path + ".part")
            assert bot.calls == 1

    asyncio.run(run())


def test_stalled_download_is_cut_off_and_reported():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "left.jpg")
            bot = _Bot(delay=5.0)
            started = asyncio.get_running_loop().time()
            ok = await media.save_telegram_photo(bot, "f1", path, timeout=0.4)
            elapsed = asyncio.get_running_loop().time() - started
            assert ok is False, "a stalled download must report failure"
            assert elapsed < 2.0, f"the ceiling did not bound the wait: {elapsed:.2f}s"
            assert not os.path.exists(path)

    asyncio.run(run())


def test_download_error_is_reported_not_raised():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            bot = _Bot(error=RuntimeError("CDN said no"))
            assert await media.save_telegram_photo(bot, "f1", os.path.join(tmp, "a.jpg")) is False

            class NoContent(_Bot):
                async def download(self, file, destination=None):
                    return None

            assert (
                await media.save_telegram_photo(NoContent(), "f1", os.path.join(tmp, "b.jpg"))
                is False
            )

    asyncio.run(run())


def test_unwritable_volume_is_reported_not_raised():
    """A read-only or full media volume must not crash the handler."""

    async def run():
        bot = _Bot()
        # /proc is not writable: the closest portable stand-in for a full volume.
        ok = await media.save_telegram_photo(bot, "f1", "/proc/definitely/not/writable.jpg")
        assert ok is False

    asyncio.run(run())


class _StallingSession(FakeSession):
    """Telegram answers getFile but the photo stream never starts."""

    def __init__(self, stall: float) -> None:
        super().__init__()
        self.stall = stall

    async def stream_content(self, url, headers=None, timeout: int = 30, chunk_size=65536, raise_for_status=True):
        await asyncio.sleep(self.stall)
        yield b"x" * 1024


def test_photo_step_answers_the_participant_when_the_download_stalls():
    """End to end: a stalled photo produces a retry request, not silence."""

    async def run():
        media.DOWNLOAD_TIMEOUT_SECONDS = 1.0
        harness = BotHarness()
        session = _StallingSession(30.0)
        harness.session = session
        harness.bot.session = session
        await harness.start()
        try:
            await harness.send_command(USER)
            await harness.tap(USER, "lang:ru")
            await harness.tap(USER, "country:0")
            await harness.send_text(USER, "01A123BC")
            # Fill the direction step by picking the tenant's first direction.
            directions = await harness.db.list_directions(
                tenant_id=harness.tenant.id, active_only=True
            )
            if directions:
                await harness.tap(USER, f"direction:{directions[0].id}")
            assert await harness.dispatcher.storage.get_state(
                _key(harness)
            ) == Registration.photos.state

            started = asyncio.get_running_loop().time()
            await asyncio.wait_for(harness.send_photo(USER, "stalled"), timeout=20)
            elapsed = asyncio.get_running_loop().time() - started
            assert elapsed < 5, f"the handler blocked for {elapsed:.1f}s"

            texts = harness.private_texts(USER)
            assert texts, "the participant got no answer at all"
            assert any("ещё раз" in t or "yana" in t for t in texts), texts
        finally:
            media.DOWNLOAD_TIMEOUT_SECONDS = 15.0
            await harness.stop()

    asyncio.run(run())


def _key(harness: BotHarness):
    from aiogram.fsm.storage.base import StorageKey

    return StorageKey(bot_id=harness.bot.id, chat_id=USER, user_id=USER)


def test_all_four_photos_are_written_on_the_volume():
    """The healthy path still stores four distinct sides, in order."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await harness.send_command(USER)
            await harness.tap(USER, "lang:ru")
            await harness.tap(USER, "country:0")
            await harness.send_text(USER, "01A123BC")
            directions = await harness.db.list_directions(
                tenant_id=harness.tenant.id, active_only=True
            )
            if directions:
                await harness.tap(USER, f"direction:{directions[0].id}")
            for index in range(4):
                await harness.send_photo(USER, f"side-{index}")
            data = await harness.dispatcher.storage.get_data(_key(harness))
            assert len(data["photo_file_ids"]) == 4, data["photo_file_ids"]
            for path in data["photo_paths"]:
                assert os.path.exists(path), path
                assert os.path.getsize(path) > 0
        finally:
            await harness.stop()

    asyncio.run(run())


if __name__ == "__main__":
    test_happy_path_writes_the_file_atomically()
    test_stalled_download_is_cut_off_and_reported()
    test_download_error_is_reported_not_raised()
    test_unwritable_volume_is_reported_not_raised()
    test_photo_step_answers_the_participant_when_the_download_stalls()
    test_all_four_photos_are_written_on_the_volume()
    print("All media-download tests passed.")
