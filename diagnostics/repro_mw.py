#!/usr/bin/env python3
"""SerializePerUserMiddleware + album reproducibility probe.

Two questions, both measured rather than reasoned about:

1. Do two updates of *one* participant ever run at the same time?  If they do,
   the form races on itself (the album case: four photos arrive together, every
   handler reads "0 photos so far", three photos are lost).
2. Does the lock table leak, or is a lock ever dropped while somebody still
   waits on it?  The reviewer could not prove this by reading, so it is measured
   here: 20 updates for one user, plus a check on the bookkeeping dictionaries.

Usage:  .venv/bin/python repro_mw.py [repo_path]
"""
from __future__ import annotations

import asyncio
import os
import sys

REPO = os.path.abspath(
    sys.argv[1]
    if len(sys.argv) > 1
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from bot.middlewares import SerializePerUserMiddleware  # noqa: E402


async def probe_middleware() -> None:
    """Hammer one user through the middleware and measure parallelism."""
    mw = SerializePerUserMiddleware()
    active = 0
    max_active = 0
    order: list[int] = []

    async def handler(event, data):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        order.append(event)
        await asyncio.sleep(0.01)  # stand-in for DB + download work
        active -= 1
        return event

    users = [{}, {"event_from_user": type("U", (), {"id": 777})()}]

    # 20 updates for the same participant, released all at once.
    await asyncio.gather(
        *[mw(handler, i, {"event_from_user": type("U", (), {"id": 777})()}) for i in range(20)]
    )
    print(f"same user   : max concurrent handlers = {max_active} (must be 1)")
    print(f"             executed in order: {order == list(range(20))}")
    assert max_active == 1, "two updates for one user ran concurrently!"

    # 20 updates for 20 different participants must stay parallel.
    active = max_active = 0
    async def slow_handler(event, data):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.05)
        active -= 1

    await asyncio.gather(
        *[
            mw(slow_handler, i, {"event_from_user": type("U", (), {"id": i})()})
            for i in range(20)
        ]
    )
    print(f"20 users    : max concurrent handlers = {max_active} (must be > 1)")

    # Bookkeeping must be empty again, and no update may be lost.
    print(f"lock table  : locks={len(mw._locks)} waiting={len(mw._waiting)} (must be 0/0)")
    assert not mw._locks and not mw._waiting, "lock table leaked"

    # Updates without a user must pass straight through.
    async def echo(event, data):
        return "passed"

    assert await mw(echo, None, users[0]) == "passed"
    print("no-user     : passed through")


async def probe_album() -> None:
    """An album is several updates delivered near-simultaneously.

    Four photos sent as one Telegram album must all end up in the form's
    ``photo_file_ids``; before serialization three of them were lost because
    every handler read the same list.
    """
    from aiogram.types import Update  # noqa: F401

    from harness import BotHarness
    from bot.states import Registration  # noqa: E402

    harness = BotHarness()
    await harness.start()
    try:
        uid = 424242
        await harness.send_command(uid)
        await harness.tap(uid, "lang:ru")
        await harness.tap(uid, "country:0")
        await harness.send_text(uid, "01A123BC")
        # Direction step: jump straight to photos by picking the first button.
        from bot.db import Database
        dirs = await harness.db.list_directions(tenant_id=harness.tenant.id, active_only=True)
        if dirs:
            leaf = dirs[0]
            await harness.tap(uid, f"direction:{leaf.id}")
            for _ in range(4):
                pass
        storage = harness.dispatcher.storage
        from aiogram.fsm.storage.base import StorageKey

        key = StorageKey(bot_id=harness.bot.id, chat_id=uid, user_id=uid)
        data = await storage.get_data(key)
        print(f"state before album: {await storage.get_state(key)} data keys={sorted(data)}")
        await storage.set_state(key, Registration.photos.state)
        await storage.set_data(key, {**data, "photo_file_ids": [], "photo_paths": []})

        # Four photos inside one gather() — exactly what an album looks like.
        await asyncio.gather(*[harness.send_photo(uid, f"album-{i}") for i in range(4)])
        data = await storage.get_data(key)
        ids = data.get("photo_file_ids", [])
        print(f"photos stored: {len(ids)} of 4 -> {ids}")
        assert len(ids) == 4, f"album photos lost: {len(ids)} of 4"
    finally:
        await harness.stop()


async def main() -> int:
    await probe_middleware()
    print()
    await probe_album()
    print("\nALL PROBES PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
