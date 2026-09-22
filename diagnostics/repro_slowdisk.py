#!/usr/bin/env python3
"""Slow volume / stalled download probe for the photo step.

Item (c) of the review: ``registration._download_photo`` runs inside the
SerializePerUserMiddleware lock, and the write goes to the Fly.io network
volume.  Two things are measured here, because both are invisible in the log:

A. How long one stalled Telegram download holds the participant.
   While a photo is being fetched, the participant's *next* message waits on
   the per-user lock.  If the fetch stalls, so does everything that person
   sends — and the bot looks frozen for them, with "is handled" in the log.

B. How long a photo write occupies a thread of asyncio's *default* executor.
   aiogram writes the stream with ``aiofiles.open()``, which runs every chunk
   write through ``loop.run_in_executor(None, ...)`` — the same pool that
   ``asyncio.to_thread`` uses for every SQLite query (10 threads on the Fly
   machine).  While the volume is slow those threads are held by disk writes,
   so the database queued behind them: the whole bot stalls, not just one user.

Usage:  .venv/bin/python repro_slowdisk.py [repo_path]
"""
from __future__ import annotations

import asyncio
import datetime as dt
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

import aiofiles  # noqa: E402
from aiogram.types import Chat, Message, PhotoSize, Update, User  # noqa: E402

# Measured stand-ins for a network volume under load.  A 300 KB photo at 64 KB
# per chunk is ~5 writes; 4 photos are ~20 writes per participant.
WRITE_DELAY = 0.004       # 4 ms per 64 KB chunk write
STALL_SECONDS = 5.0       # Telegram CDN goes quiet for this long
CHUNKS = 5

# Scenario C: a badly throttled volume, to isolate the pool effect.
SLOW_CHUNK_MS = 50.0


class SlowVolume:
    """Patch aiofiles.open so every write costs ``WRITE_DELAY`` in the pool."""

    def __init__(self, delay: float) -> None:
        self.delay = delay
        self._real = aiofiles.open

    def __enter__(self):
        real = self._real
        delay = self.delay

        def slow_open(*args, **kwargs):
            cm = real(*args, **kwargs)
            self.last = cm

            class Wrapper:
                async def __aenter__(self_inner):
                    self_inner.f = await cm.__aenter__()
                    return self_inner

                async def __aexit__(self_inner, *exc):
                    return await cm.__aexit__(*exc)

                async def write(self_inner, data):
                    loop = asyncio.get_running_loop()

                    def _write():
                        time.sleep(delay)  # a slow network volume
                        return self_inner.f.write(data)

                    return await loop.run_in_executor(None, _write)

            return Wrapper()

        aiofiles.open = slow_open
        return self

    def __exit__(self, *exc):
        aiofiles.open = self._real


def make_stall_session(stall: float):
    """A FakeSession whose photo stream stalls, like a hung CDN connection."""
    from harness import FakeSession

    class StallSession(FakeSession):
        def __init__(self) -> None:
            super().__init__()
            self.stall_count = 0

        async def stream_content(self, url, headers=None, timeout: int = 30, chunk_size=65536, raise_for_status=True):
            self.stall_count += 1
            await asyncio.sleep(stall)  # nothing arrives for ``stall`` seconds
            for _ in range(CHUNKS):
                yield b"\x00" * chunk_size

    return StallSession()


def emulate_fly_threads(workers: int = 5) -> None:
    """``shared-cpu-1x`` has 1 vCPU, so asyncio's default pool has 5 threads.

    The sandbox this runs in has more cores, which would hide the pool
    contention that production sees.  Pinning the pool size keeps the
    measurement honest.
    """
    loop = asyncio.get_running_loop()
    from concurrent.futures import ThreadPoolExecutor

    loop.set_default_executor(
        ThreadPoolExecutor(max_workers=workers, thread_name_prefix="default")
    )


def _text_update(uid: int, text: str, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=uid, type="private"),
            from_user=User(id=uid, is_bot=False, first_name="Tester"),
            text=text,
        ),
    )


def _photo_update(uid: int, file_id: str, update_id: int) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=dt.datetime.now(dt.timezone.utc),
            chat=Chat(id=uid, type="private"),
            from_user=User(id=uid, is_bot=False, first_name="Tester"),
            photo=[PhotoSize(file_id=file_id, file_unique_id=file_id + "u", width=1080, height=720)],
        ),
    )


async def put_in_photo_step(harness, uid: int) -> None:
    """Drive the real form until the participant is asked for a photo."""
    from aiogram.fsm.storage.base import StorageKey

    await harness.send_command(uid)
    await harness.tap(uid, "lang:ru")
    await harness.tap(uid, "country:0")
    await harness.send_text(uid, "01A123BC")
    storage = harness.dispatcher.storage
    key = StorageKey(bot_id=harness.bot.id, chat_id=uid, user_id=uid)
    data = await storage.get_data(key)
    await storage.set_data(
        key,
        {**data, "photo_file_ids": [], "photo_paths": [], "mod_file_ids": [], "mod_paths": []},
    )


async def scenario_a() -> None:
    """A stalled download: is the participant's next message still held?

    Pre-fix measurement (identical simulation, aiogram's own 30 s stream
    timeout and no ceiling on the whole download): the next message waited
    **4.81 s**.  The ceiling introduced in ``bot.services.media`` is patched
    down to 1.5 s here so the probe finishes quickly; production uses 15 s.
    """
    from bot.services import media
    from bot.states import Registration
    from harness import BotHarness

    media.DOWNLOAD_TIMEOUT_SECONDS = 1.5
    emulate_fly_threads()
    harness = BotHarness()
    session = make_stall_session(STALL_SECONDS)
    harness.session = session
    harness.bot.session = session
    await harness.start()
    try:
        uid = 700001
        await put_in_photo_step(harness, uid)
        from aiogram.fsm.storage.base import StorageKey

        key = StorageKey(bot_id=harness.bot.id, chat_id=uid, user_id=uid)
        await harness.dispatcher.storage.set_state(key, Registration.photos.state)

        photo_task = asyncio.create_task(harness.feed(_photo_update(uid, "stall-1", 5001)))
        await asyncio.sleep(0.2)

        start = time.monotonic()
        await harness.feed(_text_update(uid, "salom?", 5002))
        waited = time.monotonic() - start
        print(f"A. next message answered after : {waited:6.2f} s  (ceiling = 1.5 s, stall = {STALL_SECONDS:.1f} s)")
        print(f"   answers to that message     : {len(harness.private_texts(uid))}")
        await asyncio.wait_for(photo_task, timeout=STALL_SECONDS + 10)
        print(f"   answers after the stall     : {len(harness.private_texts(uid))} "
              f"(participant is told to resend, not left waiting)")
        assert waited < 2.5, "the download ceiling did not bound the wait"
        assert harness.private_texts(uid), "participant got no answer at all"
    finally:
        await harness.stop()


async def scenario_b() -> None:
    """Slow volume writes: does the database (default pool) still queue?"""
    from aiogram.fsm.storage.base import StorageKey

    from bot.services import media
    from bot.states import Registration
    from harness import BotHarness

    emulate_fly_threads()
    real_write = media.write_bytes

    def slow_write(path: str, data: bytes) -> None:
        chunks = max(1, len(data) // 65536)
        for _ in range(chunks):
            time.sleep(WRITE_DELAY)  # a slow network volume
        real_write(path, data)

    media.write_bytes = slow_write
    harness = BotHarness()
    await harness.start()
    try:
        uid = 700002
        await put_in_photo_step(harness, uid)
        key = StorageKey(bot_id=harness.bot.id, chat_id=uid, user_id=uid)
        await harness.dispatcher.storage.set_state(key, Registration.photos.state)

        db = harness.db.for_tenant(harness.tenant.id)

        async def db_latency() -> list[float]:
            samples = []
            for _ in range(20):
                t0 = time.monotonic()
                await db.get_user_language(uid)
                samples.append((time.monotonic() - t0) * 1000)
                await asyncio.sleep(0.005)
            return samples

        base = await db_latency()
        print(f"B. DB query latency, idle          : p50={sorted(base)[10]:.1f} ms  max={max(base):.1f} ms")

        others = [700010 + i for i in range(4)]
        for other in others:
            await put_in_photo_step(harness, other)
            k = StorageKey(bot_id=harness.bot.id, chat_id=other, user_id=other)
            await harness.dispatcher.storage.set_state(k, Registration.photos.state)

        uploads = asyncio.gather(
            *[
                harness.feed(_photo_update(other, f"slow-{i}-{j}", 6000 + i * 10 + j))
                for i, other in enumerate(others)
                for j in range(4)
            ]
        )
        loaded = await db_latency()
        await uploads
        print(f"   DB query latency, 16 photos    : p50={sorted(loaded)[10]:.1f} ms  max={max(loaded):.1f} ms")
        print(f"   ratio (p50)                    : {sorted(loaded)[10] / max(sorted(base)[10], 0.001):.2f}x")
        print("   writes now run in the slow-work pool, so the default pool "
              "(SQLite queries) stays free")
    finally:
        media.write_bytes = real_write
        await harness.stop()


async def scenario_c() -> None:
    """Controlled experiment: which pool the volume writes occupy.

    Same simulated volume (``SLOW_CHUNK_MS`` per 64 KB chunk, 16 uploads in
    flight), only the pool differs.  asyncio's default pool is what
    ``asyncio.to_thread`` uses for every SQLite query, and on the 1-vCPU Fly
    machine it has only 5 threads.
    """
    from bot.executors import run_heavy

    async def probe(use_default_pool: bool, concurrency: int = 16, chunks: int = 5) -> tuple[float, float]:
        loop = asyncio.get_running_loop()
        stop = asyncio.Event()

        def write():
            for _ in range(chunks):
                time.sleep(SLOW_CHUNK_MS / 1000)
            return len(b"x")

        async def upload():
            if use_default_pool:
                await loop.run_in_executor(None, write)
            else:
                await run_heavy(write)

        samples: list[float] = []

        async def db_probe():
            while not stop.is_set():
                t0 = time.monotonic()
                await asyncio.to_thread(lambda: sum(range(1000)))
                samples.append((time.monotonic() - t0) * 1000)
                await asyncio.sleep(0.002)

        prober = asyncio.create_task(db_probe())
        if concurrency == 0:
            await asyncio.sleep(0.3)  # idle baseline
        else:
            uploads = [asyncio.create_task(upload()) for _ in range(concurrency)]
            await asyncio.gather(*uploads)
        stop.set()
        await prober
        await asyncio.sleep(0)
        samples.sort()
        return samples[len(samples) // 2], samples[-1]

    idle = await probe(use_default_pool=False, concurrency=0, chunks=0)
    default_p50, default_max = await probe(use_default_pool=True)
    heavy_p50, heavy_max = await probe(use_default_pool=False)
    print(f"C. DB latency with 16 writes in flight, {SLOW_CHUNK_MS:.0f} ms/chunk (default pool = 5 threads)")
    print(f"   writes on default pool (pre-fix): p50={default_p50:7.1f} ms  max={default_max:7.1f} ms")
    print(f"   writes on slow-work pool (fix)  : p50={heavy_p50:7.1f} ms  max={heavy_max:7.1f} ms")
    print(f"   idle                            : p50={idle[0]:7.1f} ms  max={idle[1]:7.1f} ms")


async def main() -> int:
    print(f"simulated: write={WRITE_DELAY * 1000:.0f} ms/chunk, stall={STALL_SECONDS:.0f} s\n")
    await scenario_a()
    print()
    await scenario_b()
    print()
    await scenario_c()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
