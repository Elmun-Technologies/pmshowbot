"""Tests for per-user update serialization.

The bug this guards against: Telegram delivers an album as several updates at
once, aiogram runs each in its own task, and the photo handler's
read → download → write cycle interleaves. Every task then sees "0 photos so
far", picks the same side, and writes to the same file — three of the four
photos are lost.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.middlewares import SerializePerUserMiddleware  # noqa: E402

SIDES = ["left", "right", "front", "back"]


def _album_run(middleware, user_ids):
    """Run the photo handler's read-modify-write for each update concurrently."""
    state: dict[int, list[str]] = {}

    async def handler(event, data):
        uid = data["event_from_user"].id
        stored = state.setdefault(uid, [])
        index = len(stored)                     # read
        side = SIDES[index] if index < len(SIDES) else "OVERFLOW"
        await asyncio.sleep(0)                  # bot.download(...) yields here
        await asyncio.sleep(0)
        stored.append(side)                     # write
        return side

    async def run():
        return await asyncio.gather(*(
            middleware(handler, object(), {"event_from_user": SimpleNamespace(id=uid)})
            for uid in user_ids
        ))

    return asyncio.run(run()), state


def test_album_photos_get_distinct_sides():
    mw = SerializePerUserMiddleware()
    sides, state = _album_run(mw, [7, 7, 7, 7])
    assert sides == SIDES, sides
    assert state[7] == SIDES
    assert "OVERFLOW" not in sides


def test_without_the_middleware_the_album_collides():
    # Guards the test itself: if the fixture stopped interleaving, the test
    # above would pass for the wrong reason.
    async def passthrough(handler, event, data):
        return await handler(event, data)

    sides, _ = _album_run(passthrough, [7, 7, 7, 7])
    assert sides == ["left"] * 4, sides


def test_different_users_are_not_blocked_by_each_other():
    mw = SerializePerUserMiddleware()
    sides, state = _album_run(mw, [1, 2, 3, 4])
    assert sides == ["left"] * 4          # each user's first photo
    assert all(state[uid] == ["left"] for uid in (1, 2, 3, 4))


def test_lock_table_is_emptied_after_use():
    mw = SerializePerUserMiddleware()
    _album_run(mw, [7, 7, 8, 8])
    # One entry per user who ever messaged the bot would be a slow leak.
    assert mw._locks == {} and mw._waiting == {}


def test_events_without_a_user_pass_through():
    mw = SerializePerUserMiddleware()

    async def handler(event, data):
        return "ok"

    async def run():
        return await mw(handler, object(), {})

    assert asyncio.run(run()) == "ok"


if __name__ == "__main__":
    test_album_photos_get_distinct_sides()
    test_without_the_middleware_the_album_collides()
    test_different_users_are_not_blocked_by_each_other()
    test_lock_table_is_emptied_after_use()
    test_events_without_a_user_pass_through()
    print("All middleware tests passed.")
