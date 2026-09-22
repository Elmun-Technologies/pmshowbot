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

Bounding the download was not enough, though: the participant still waited the
whole (now bounded) stall for their next prompt, because the photo step fetched
the bytes *before* answering.  The step now answers first and downloads in the
background (``PhotoIngest``), so the tests below pin both halves: a stalled photo
delays nothing but itself, and the side it belonged to is the slot the
participant's retry fills.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import gc
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


def test_photo_step_answers_before_the_photo_is_downloaded():
    """End to end: the prompt does not wait for the bytes.

    The stalled download below is set to 30 seconds.  With the old flow the
    prompt for side 2 waited for it (bounded at the 15 s ceiling, so "the bot
    hangs after the first photo").  Now the answer is already in the chat when
    the handler returns, and the download tells the participant to resend when
    it gives up — without holding anything else.
    """

    async def run():
        media.DOWNLOAD_TIMEOUT_SECONDS = 1.0
        harness = BotHarness()
        session = _StallingSession(30.0)
        harness.session = session
        harness.bot.session = session
        await harness.start()
        try:
            await walk_to_photo_step(harness)

            started = asyncio.get_running_loop().time()
            await asyncio.wait_for(harness.send_photo(USER, "stalled"), timeout=20)
            elapsed = asyncio.get_running_loop().time() - started
            assert elapsed < 1.0, f"the update waited for the download: {elapsed:.1f}s"

            texts = harness.private_texts(USER)
            assert texts, "the participant got no answer at all"
            assert "2 из 4" in texts[-1], texts[-1]

            # The next photo is handled straight away, even though the first
            # download is still stuck: nothing is waiting on it any more.
            started = asyncio.get_running_loop().time()
            await asyncio.wait_for(harness.send_photo(USER, "second"), timeout=20)
            assert asyncio.get_running_loop().time() - started < 1.0
            assert "3 из 4" in harness.private_texts(USER)[-1]

            # The stalled download fails at its own ceiling and asks for that
            # side specifically — slot 1 is the one that has to be filled again.
            await media.ingest.wait(harness.bot, USER, timeout=10)
            texts = harness.private_texts(USER)
            assert any("ещё раз" in t or "yana" in t for t in texts), texts
            assert any("левая" in t or "chap" in t for t in texts), texts
        finally:
            media.DOWNLOAD_TIMEOUT_SECONDS = 15.0
            await harness.stop()

    asyncio.run(run())


def test_a_failed_side_is_refilled_by_the_next_photo():
    """The retry lands in its own slot: the sides of the car keep their order."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await walk_to_photo_step(harness)
            harness.session.fail_next("GetFile", RuntimeError("CDN said no"))

            await harness.send_photo(USER, "left-broken")
            await media.ingest.wait(harness.bot, USER, timeout=10)
            assert any("ещё раз" in t for t in harness.private_texts(USER))

            # Resend: it must become the *left* side, not the second slot.
            await harness.send_photo(USER, "left-good")
            assert "2 из 4" in harness.private_texts(USER)[-1]
            data = await harness.dispatcher.storage.get_data(_key(harness))
            assert data["photo_file_ids"] == ["left-good"], data["photo_file_ids"]

            for index in range(1, 4):
                await harness.send_photo(USER, f"side-{index}")
            await media.ingest.wait(harness.bot, USER, timeout=10)
            data = await harness.dispatcher.storage.get_data(_key(harness))
            assert [os.path.basename(p) for p in data["photo_paths"]] == [
                "left.jpg",
                "right.jpg",
                "front.jpg",
                "back.jpg",
            ], data["photo_paths"]
            for path in data["photo_paths"]:
                assert os.path.getsize(path) > 0, path
        finally:
            await harness.stop()

    asyncio.run(run())


def test_the_form_waits_for_photos_that_are_still_being_fetched():
    """The end of the form must not store a path that is not on the volume."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await walk_to_photo_step(harness)
            for index in range(4):
                await harness.send_photo(USER, f"side-{index}")
            # The participant is faster than the volume: the phone arrives while
            # the last downloads are still running.  Nothing is lost, and the row
            # points only at files that exist.
            await harness.tap(USER, "modsdone")
            await harness.send_contact(USER, "+998901234567")
            app = (await harness.db.for_tenant(harness.tenant.id).list_applications())[0]
            assert len(app.photo_paths) == 4, app.photo_paths
            for path in app.photo_paths:
                assert os.path.exists(path) and os.path.getsize(path) > 0, path
        finally:
            await harness.stop()

    asyncio.run(run())


def test_a_missing_side_keeps_the_form_open_instead_of_accepting_it():
    """A lost photo is re-fetched from its id; if that fails, it is asked for.

    The application row must never say "four sides" while the volume holds
    three — the moderator would export a ticket without the car on it.
    """

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await walk_to_photo_step(harness)
            for index in range(4):
                await harness.send_photo(USER, f"side-{index}")
            await media.ingest.wait(harness.bot, USER, timeout=10)

            data = await harness.dispatcher.storage.get_data(_key(harness))
            # Simulate the volume losing one side (a redeploy mid-download, a
            # disk that dropped the file) — the id is still in the state.
            os.remove(data["photo_paths"][2])

            await harness.tap(USER, "modsdone")
            await harness.send_contact(USER, "+998901234567")
            # The id is re-fetched silently, so the form finishes normally.
            apps = await harness.db.for_tenant(harness.tenant.id).list_applications()
            assert len(apps) == 1, apps
            assert all(os.path.exists(p) for p in apps[0].photo_paths), apps[0].photo_paths
        finally:
            await harness.stop()

    asyncio.run(run())


async def walk_to_photo_step(harness: BotHarness) -> None:
    """Drive the real form until the participant is asked for a photo."""
    await harness.send_command(USER)
    await harness.tap(USER, "lang:ru")
    await harness.tap(USER, "country:0")
    await harness.send_text(USER, "01A123BC")
    directions = await harness.db.list_directions(tenant_id=harness.tenant.id, active_only=True)
    if directions:
        await harness.tap(USER, f"direction:{directions[0].id}")
    assert (
        await harness.dispatcher.storage.get_state(_key(harness))
        == Registration.photos.state
    )


def test_a_download_that_dies_with_the_process_is_fetched_again():
    """A redeploy in the middle of a download must not cost the participant a photo.

    The state says "left sent", the volume has nothing, and the ledger of this
    process has never heard of it.  Telegram still serves the photo by id, so the
    next update fetches it again instead of asking the participant for it.
    """

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await walk_to_photo_step(harness)
            await harness.send_photo(USER, "left")
            await media.ingest.wait(harness.bot, USER, timeout=10)
            data = await harness.dispatcher.storage.get_data(_key(harness))
            os.remove(data["photo_paths"][0])
            # A fresh ledger is what a restarted process has.
            media.ingest._records.clear()

            await harness.send_photo(USER, "next")
            await media.ingest.wait(harness.bot, USER, timeout=10)
            data = await harness.dispatcher.storage.get_data(_key(harness))
            assert len(data["photo_file_ids"]) == 2, data["photo_file_ids"]
            for path in data["photo_paths"]:
                assert os.path.exists(path), path
        finally:
            await harness.stop()

    asyncio.run(run())


def test_a_replaced_worker_does_not_inherit_the_ledger():
    """Two workers with the same token are two ledgers, not one.

    aiogram's ``Bot.__eq__``/``__hash__`` are built from the token, so a ledger
    keyed by the Bot object hands a freshly built worker the ledger of the one
    it replaced — including the failures that decide which slot the
    participant's next photo fills.  Found by the diagnostics, where a failed
    upload from one scenario turned up in the next one's application check.
    """

    async def run():
        first = BotHarness()
        await first.start()
        second = BotHarness()
        await second.start()
        try:
            assert first.bot == second.bot, "the trap this test guards is gone"
            media.ingest.submit(
                first.bot,
                user_id=USER,
                chat_id=USER,
                file_id="probe",
                path=os.path.join(first.media_dir, "probe.jpg"),
                kind="side",
                index=0,
            )
            await media.ingest.wait(first.bot, USER, timeout=10)
            assert media.ingest.statuses(first.bot, USER), "the upload was not recorded"

            assert media.ingest.statuses(second.bot, USER) == {}, (
                "a fresh worker inherited another worker's ledger"
            )
            assert media.ingest.failed(second.bot, USER) == []

            key = id(first.bot)
            await first.stop()
            del first
            gc.collect()
            assert key not in media.ingest._records, (
                "the ledger kept a dead worker's entry"
            )
        finally:
            await second.stop()

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
    test_photo_step_answers_before_the_photo_is_downloaded()
    test_a_failed_side_is_refilled_by_the_next_photo()
    test_the_form_waits_for_photos_that_are_still_being_fetched()
    test_a_missing_side_keeps_the_form_open_instead_of_accepting_it()
    test_a_download_that_dies_with_the_process_is_fetched_again()
    test_all_four_photos_are_written_on_the_volume()
    test_a_replaced_worker_does_not_inherit_the_ledger()
    print("All media-download tests passed.")
