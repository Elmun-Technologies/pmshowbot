#!/usr/bin/env python3
"""A/B probe: does one Telegram update still open a connection per query?

The first fix of this series (commit 9f32e09, now in main via PR #38) stopped
opening a fresh SQLite connection — and re-running ``PRAGMA journal_mode=WAL``
— for every single query.  On a Fly.io network volume those pragmas are real
disk I/O, and one ``/start`` opened nine connections.

This script verifies the claim on the *current* code instead of trusting the
commit message:

* it counts every ``connect_sqlite`` call while the real dispatcher walks a
  complete four-step registration;
* it times the same walk with a 4 ms stand-in for the pragma I/O, both with
  connection reuse (current code) and with a fresh connection per operation
  (the pre-fix behaviour, emulated by patching the two ``_connect`` methods).

Usage:  .venv/bin/python perf_ab.py [repo_path]
"""
from __future__ import annotations

import asyncio
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

from harness import BotHarness  # noqa: E402

USER = 90210
PRAGMA_IO_SECONDS = 0.004  # a network volume answering a write+fsync


async def walk_the_form(harness: BotHarness) -> None:
    """Country -> plate -> direction -> first photo: the reported 4-step path."""
    await harness.send_command(USER)
    await harness.tap(USER, "lang:ru")
    await harness.tap(USER, "country:0")
    await harness.send_text(USER, "01A123BC")
    directions = await harness.db.list_directions(
        tenant_id=harness.tenant.id, active_only=True
    )
    if directions:
        await harness.tap(USER, f"direction:{directions[0].id}")
    await harness.send_photo(USER, "perf-1")


async def measure(*, fresh_connection_per_query: bool) -> tuple[int, float, int]:
    """Return (connections opened, seconds elapsed, update count)."""
    import bot.sqlite_pool as pool

    opened = {"n": 0}
    real_connect = pool.connect_sqlite

    def counting_connect(path: str):
        opened["n"] += 1
        time.sleep(PRAGMA_IO_SECONDS)  # the pragmas cost one disk round-trip
        return real_connect(path)

    pool.connect_sqlite = counting_connect
    # Both stores import the name at module level, so patch the references too.
    import bot.db as db_module
    import bot.services.fsm_storage as fsm_module

    db_module.connect_sqlite = counting_connect
    fsm_module.connect_sqlite = counting_connect

    harness = BotHarness()
    await harness.start()
    context_patches = []
    if fresh_connection_per_query:
        # Emulate the pre-fix behaviour: every operation forgets its connection.
        def forgetful(cls):
            original = cls._connect

            def _connect(self):
                # Database keeps ``path``; the FSM storage keeps ``_path``.
                path = getattr(self, "path", None) or getattr(self, "_path")
                return counting_connect(path)

            context_patches.append((cls, original))
            cls._connect = _connect

        forgetful(db_module.Database)
        forgetful(fsm_module.SqliteFSMStorage)

    try:
        started = time.monotonic()
        await walk_the_form(harness)
        elapsed = time.monotonic() - started
        updates = USER and len(harness.session.methods)
    finally:
        await harness.stop()
        for cls, original in context_patches:
            cls._connect = original
        pool.connect_sqlite = real_connect
        db_module.connect_sqlite = real_connect
        fsm_module.connect_sqlite = real_connect

    return opened["n"], elapsed, updates


async def main() -> int:
    print(f"simulated pragma I/O: {PRAGMA_IO_SECONDS * 1000:.0f} ms per connection\n")
    reused_conn, reused_s, _ = await measure(fresh_connection_per_query=False)
    fresh_conn, fresh_s, _ = await measure(fresh_connection_per_query=True)

    print(f"connection reuse (current code) : {reused_conn:3d} connections, {reused_s * 1000:7.1f} ms")
    print(f"fresh connection per query (old): {fresh_conn:3d} connections, {fresh_s * 1000:7.1f} ms")
    if reused_s > 0:
        print(f"speed-up                        : {fresh_s / reused_s:.1f}x")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
