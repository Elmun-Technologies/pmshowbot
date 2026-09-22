#!/usr/bin/env python3
"""Where does the photo step actually spend its time?

The complaint is "the bot is slow, and after the first photo it hangs".  The
existing tests all run against a session that answers instantly, so they can
prove *correctness* of the photo step but never its **latency**.  This script
drives the real production dispatcher (through ``tests/harness.py``) with a
session that costs what the real one costs:

* every Telegram API call takes ``API_DELAY`` (a round trip to api.telegram.org
  is 150-400 ms);
* every photo download streams ``CHUNKS`` pieces of ``CHUNK_DELAY`` each (a few
  hundred KB from Telegram's CDN);
* every SQLite operation waits on the volume (``DB_DELAY``);
* the media write costs what the volume costs (``WRITE_DELAY`` per 64 KB).

It reports, per update, the wall time and *why*: how many Telegram calls, how
many downloads, how many FSM queries, how many volume writes, and how many of
those API calls carry a whole image *up* from the volume (``up`` — the banner
re-uploads that used to happen once per registration).

Four scenarios are measured, because they behave differently:

1. **four photos, one at a time** — what the prompts ask for;
2. **four photos as one album** — Telegram delivers them as four updates at
   once;
3. **the first photo stalled** — three more photos and a text message behind a
   stream that never starts: the reported hang;
   **the whole form over a slow link** — one photo streaming for 3 s, from
   ``/start`` to the confirmation;
4. **the same banner twice** — two registrations, one upload.

Before ``bot.services.media.PhotoIngest`` the photo step answered only after the
bytes were on the volume, inside the per-user lock: this script printed 0.78 s
per photo, 3.17 s for an album, and every message behind a stalled download
waited the whole ceiling (15 s in production).

Usage:  .venv/bin/python diagnostics/perf_photo_step.py [repo_path]
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import sys
import time

REPO = os.path.abspath(
    sys.argv[1]
    if len(sys.argv) > 1
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from harness import BotHarness, FakeSession  # noqa: E402

USER = 4242

# ---- production stand-ins -------------------------------------------------
API_DELAY = 0.25      # one round trip to api.telegram.org
CHUNKS = 6            # a ~300 KB photo in 64 KB pieces
CHUNK_DELAY = 0.04    # Telegram's CDN per piece
DB_DELAY = 0.004      # one SQLite operation on the volume
WRITE_DELAY = 0.004   # one 64 KB write to the volume


class Ledger:
    """What the handlers did while one update was being processed."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.api = 0
        self.downloads = 0
        self.db = 0
        self.writes = 0
        self.uploads = 0


ledger = Ledger()


class LatentSession(FakeSession):
    """FakeSession with the timings of a real network and a real volume."""

    def __init__(self, *, api_delay: float = API_DELAY) -> None:
        super().__init__()
        self.api_delay = api_delay

    async def make_request(self, bot, method, timeout=None):
        ledger.api += 1
        photo = getattr(method, "photo", None)
        if type(method).__name__ == "SendPhoto" and not isinstance(photo, str):
            ledger.uploads += 1
        await asyncio.sleep(self.api_delay)
        return await super().make_request(bot, method, timeout)

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        ledger.downloads += 1
        for _ in range(CHUNKS):
            await asyncio.sleep(CHUNK_DELAY)
            yield b"\xff" * (chunk_size // 2)


class StallingSession(LatentSession):
    """The first photo stream never starts; everything after it is normal."""

    def __init__(self, *, stall: float) -> None:
        super().__init__()
        self.stall = stall
        self.stalls = 0

    async def stream_content(self, url, headers=None, timeout=30, chunk_size=65536, raise_for_status=True):
        if self.stalls == 0:
            self.stalls += 1
            await asyncio.sleep(self.stall)  # a connection that goes quiet
        async for chunk in super().stream_content(
            url, headers, timeout, chunk_size, raise_for_status
        ):
            yield chunk


async def new_harness(session: FakeSession | None = None) -> BotHarness:
    """A harness whose handlers pay for the network and the volume."""
    import bot.services.media as media

    harness = BotHarness()
    session = session or LatentSession()
    harness.session = session
    harness.bot.session = session
    await harness.start()

    storage = harness.dispatcher.storage
    for name in ("get_state", "get_data", "set_data", "set_state"):
        original = getattr(storage, name)

        async def counted(*args, _original=original, **kwargs):
            ledger.db += 1
            await asyncio.sleep(DB_DELAY)
            return await _original(*args, **kwargs)

        setattr(storage, name, counted)

    if not getattr(media.write_bytes, "_counted", False):
        original_write = media.write_bytes

        def counted_write(path, data, _original=original_write):
            ledger.writes += 1
            time.sleep(WRITE_DELAY * max(1, len(data) // (64 * 1024)))
            return _original(path, data)

        counted_write._counted = True  # type: ignore[attr-defined]
        media.write_bytes = counted_write
    return harness


class Recorder:
    """Times every update fed to the dispatcher and attributes the cost."""

    def __init__(self) -> None:
        self.rows: list[tuple] = []

    async def feed(self, label: str, coro_factory):
        ledger.reset()
        started = time.perf_counter()
        await coro_factory()
        elapsed = time.perf_counter() - started
        self.rows.append(
            (
                label,
                elapsed,
                ledger.api,
                ledger.downloads,
                ledger.db,
                ledger.writes,
                ledger.uploads,
            )
        )
        return elapsed


def report(title: str, rows) -> None:
    print(f"\n=== {title} ===")
    print(f"{'step':<36}{'seconds':>9}{'api':>6}{'dl':>5}{'db':>5}{'wr':>5}{'up':>5}")
    total = 0.0
    for label, elapsed, api, downloads, db, writes, uploads in rows:
        total += elapsed
        print(
            f"{label:<36}{elapsed:>9.2f}{api:>6}{downloads:>5}{db:>5}{writes:>5}{uploads:>5}"
        )
    print(f"{'TOTAL':<36}{total:>9.2f}")


async def walk_to_photos(harness: BotHarness, user_id: int = USER) -> None:
    await harness.send_command(user_id)
    await harness.tap(user_id, "lang:ru")
    await harness.tap(user_id, "country:0")
    await harness.send_text(user_id, "01A123BC")
    directions = await harness.db.list_directions(
        tenant_id=harness.tenant.id, active_only=True
    )
    if directions:
        await harness.tap(user_id, f"direction:{directions[0].id}")


async def scenario_sequential() -> None:
    harness = await new_harness()
    try:
        recorder = Recorder()
        await recorder.feed("1. /start → language", lambda: harness.send_command(USER))
        await recorder.feed("2. language", lambda: harness.tap(USER, "lang:ru"))
        await recorder.feed("3. country", lambda: harness.tap(USER, "country:0"))
        await recorder.feed("4. plate", lambda: harness.send_text(USER, "01A123BC"))
        directions = await harness.db.list_directions(
            tenant_id=harness.tenant.id, active_only=True
        )
        if directions:
            await recorder.feed(
                "5. direction", lambda: harness.tap(USER, f"direction:{directions[0].id}")
            )
        for index in range(4):
            await recorder.feed(
                f"{6 + index}. photo {index + 1} → next prompt",
                lambda index=index: harness.send_photo(USER, f"side-{index}"),
            )
        report("four photos, one at a time", recorder.rows)
    finally:
        await harness.stop()


async def scenario_album() -> None:
    """Telegram delivers an album as four near-simultaneous updates."""
    harness = await new_harness()
    try:
        await walk_to_photos(harness)
        ledger.reset()
        started = time.perf_counter()
        # aiogram runs every update in its own task (handle_as_tasks=True).
        await asyncio.gather(*(harness.send_photo(USER, f"album-{i}") for i in range(4)))
        elapsed = time.perf_counter() - started
        data = await harness.dispatcher.storage.get_data(_key(harness))
        stored = len(data.get("photo_file_ids", []))
        print("\n=== four photos sent as one album ===")
        print(f"all four updates processed in {elapsed:.2f} s")
        print(f"photos stored: {stored} of 4   api calls: {ledger.api}   "
              f"downloads: {ledger.downloads}   fsm queries: {ledger.db}")
        for text in harness.private_texts(USER)[-4:]:
            print(f"  • {text.splitlines()[0][:70]}")
    finally:
        await harness.stop()


async def scenario_stalled_first_photo() -> None:
    """The complaint itself: the first photo stalls, three more follow.

    How long does the participant wait for each answer, and how long before the
    side that failed is asked for again?  Nothing here may wait for the stalled
    download — not the second photo, not the question that ends the photo step,
    not a plain text message.
    """
    import bot.services.media as media

    harness = await new_harness(StallingSession(stall=30.0))
    try:
        await walk_to_photos(harness)
        media.DOWNLOAD_TIMEOUT_SECONDS = 1.0  # production is 15 s; keep the probe quick

        waits: dict[str, float] = {}
        for label, photo in (
            ("photo 1 (stream stalled)", "stalled"),
            ("photo 2", "side-2"),
            ("photo 3", "side-3"),
            ("photo 4", "side-4"),
        ):
            started = time.perf_counter()
            await harness.send_photo(USER, photo)
            waits[label] = time.perf_counter() - started

        started = time.perf_counter()
        await harness.send_text(USER, "salom?")
        waits["a text message behind all four"] = time.perf_counter() - started

        print("\n=== four photos, the first one stalled ===")
        for label, elapsed in waits.items():
            print(f"{label:<38}{elapsed:>7.2f} s")

        await media.ingest.wait(harness.bot, USER, timeout=10)
        texts = harness.private_texts(USER)
        asked = [t for t in texts if "ещё раз" in t or "yana" in t]
        print(f"answers to the participant            : {len(texts)}")
        print(f"asked to resend a named side           : {len(asked)}")
        if asked:
            print(f"  • {asked[0].splitlines()[0][:70]}")
        worst = max(waits.values())
        assert worst < 1.0, f"an answer waited for the stalled download: {worst:.2f} s"
    finally:
        media.DOWNLOAD_TIMEOUT_SECONDS = 15.0
        await harness.stop()


async def scenario_banner_reuse() -> None:
    """Two participants pick the same direction: the banner goes up once.

    The direction step answers with a banner image that lives on the volume.
    Sending it as ``FSInputFile`` uploads the whole file every time, on the
    update the participant is waiting on; the second registration re-sends the
    ``file_id`` the first one was given instead.
    """
    from bot.services import assets

    harness = await new_harness()
    # In production main.py points the asset helpers at the media volume; the
    # harness leaves them unconfigured, so the banner lookup needs this.
    assets.configure(harness.media_dir)
    try:
        directions = await harness.db.list_directions(
            tenant_id=harness.tenant.id, active_only=True
        )
        if not directions:
            print("\n(no directions in this tenant — banner scenario skipped)")
            return
        target = directions[0]
        directory = os.path.join(
            harness.media_dir, "_tenants", harness.tenant.slug, "_directions"
        )
        os.makedirs(directory, exist_ok=True)
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (800, 400), (200, 60, 40)).save(buffer, "PNG")
        with open(os.path.join(directory, f"{target.slug}.png"), "wb") as fh:
            fh.write(buffer.getvalue())

        print("\n=== the same banner, two registrations ===")
        uploads: dict[int, int] = {}
        for participant in (9001, 9002):
            recorder = Recorder()
            await recorder.feed(
                f"participant {participant}: /start → language",
                lambda participant=participant: harness.send_command(participant),
            )
            await recorder.feed(
                f"participant {participant}: language",
                lambda participant=participant: harness.tap(participant, "lang:ru"),
            )
            await recorder.feed(
                f"participant {participant}: country",
                lambda participant=participant: harness.tap(participant, "country:0"),
            )
            await recorder.feed(
                f"participant {participant}: plate",
                lambda participant=participant: harness.send_text(participant, "01A123BC"),
            )
            await recorder.feed(
                f"participant {participant}: direction (banner)",
                lambda participant=participant: harness.tap(
                    participant, f"direction:{target.id}"
                ),
            )
            report(f"participant {participant}", recorder.rows)
            uploads[participant] = [row[6] for row in recorder.rows if "banner" in row[0]][0]
        print(
            f"\nimage uploads from the volume: participant 9001 = {uploads[9001]}, "
            f"participant 9002 = {uploads[9002]}"
        )
    finally:
        await harness.stop()


def _key(harness: BotHarness):
    from aiogram.fsm.storage.base import StorageKey

    return StorageKey(bot_id=harness.bot.id, chat_id=USER, user_id=USER)


async def scenario_whole_form_behind_a_slow_link() -> None:
    """The whole form while Telegram is slow: does any step go quiet?

    One photo's stream takes six seconds — slow, but still inside the 15 s cap,
    so it does arrive in the end.  Nothing the participant does may wait for it:
    every answer up to and including the phone question must arrive within a
    second.  The confirmation is the one exception, because the application row
    is only written once the photos are on the volume — and even that wait has
    to be *announced* (``PHOTOS_SAVING``) instead of looking like a freeze.
    """
    import bot.services.media as media
    from bot.texts import T

    harness = await new_harness(StallingSession(stall=6.0))
    try:
        recorder = Recorder()
        await recorder.feed("1. /start → language", lambda: harness.send_command(USER))
        await recorder.feed("2. language", lambda: harness.tap(USER, "lang:ru"))
        await recorder.feed("3. country", lambda: harness.tap(USER, "country:0"))
        await recorder.feed("4. plate", lambda: harness.send_text(USER, "01A123BC"))
        directions = await harness.db.list_directions(
            tenant_id=harness.tenant.id, active_only=True
        )
        if directions:
            await recorder.feed(
                "5. direction", lambda: harness.tap(USER, f"direction:{directions[0].id}")
            )
        for index in range(4):
            await recorder.feed(
                f"{6 + index}. photo {index + 1} (one stream takes 6 s)",
                lambda index=index: harness.send_photo(USER, f"slow-{index}"),
            )
        await recorder.feed(
            "10. mods photo", lambda: harness.send_photo(USER, "slow-mod")
        )
        await recorder.feed(
            "11. mods done", lambda: harness.tap(USER, "modsdone", text="mods card")
        )
        data = await harness.dispatcher.storage.get_data(_key(harness))
        pending = [
            os.path.basename(path)
            for path, state in media.ingest.statuses(harness.bot, USER).items()
            if state == "pending"
        ]
        print(f"   still downloading before the phone step: {pending or 'nothing'}")
        await recorder.feed(
            "12. phone → confirmation", lambda: harness.send_contact(USER)
        )
        report("the whole form, one photo streaming for 6 s", recorder.rows)

        texts = harness.private_texts(USER)
        saving = T("ru").PHOTOS_SAVING in texts
        thanks = any(line.startswith(T("ru").THANKS.splitlines()[0][:20]) for line in texts)
        failed = media.ingest.failed(harness.bot, USER)
        print(f"said \"saving photos\" before the confirmation : {saving}")
        print(f"confirmed the application                      : {thanks}")
        if failed:
            print("downloads that failed:", [os.path.basename(u.path) for u in failed])

        await media.ingest.wait(harness.bot, USER, timeout=20)
        app = (await harness.db.for_tenant(harness.tenant.id).list_applications())[0]
        on_disk = [os.path.exists(p) for p in app.photo_paths + app.mod_paths]
        print(
            f"application row: {len(app.photo_paths)} sides + "
            f"{len(app.mod_paths)} mods, all on the volume: {all(on_disk)}"
        )

        worst = max(row[1] for row in recorder.rows[:-1])
        last = recorder.rows[-1][1]
        assert worst < 1.0, f"a step waited behind the slow stream: {worst:.2f} s"
        assert not failed, f"a download failed for no reason: {failed}"
        assert thanks, "the form never confirmed"
        assert saving, "the confirmation waited for the photo without saying so"
        assert len(app.photo_paths) == 4 and len(app.mod_paths) == 1, (
            f"the application lost photos: {app.photo_paths} + {app.mod_paths}"
        )
        assert all(on_disk), "a photo never reached the volume"
    finally:
        await harness.stop()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    await scenario_sequential()
    await scenario_album()
    await scenario_stalled_first_photo()
    await scenario_whole_form_behind_a_slow_link()
    await scenario_banner_reuse()


if __name__ == "__main__":
    asyncio.run(main())
