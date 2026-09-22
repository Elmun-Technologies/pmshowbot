"""Slow Google work must not be able to freeze the bot.

Connection reuse (``tests/test_connection_reuse.py``) removed the per-query
SQLite opens, but every blocking call in the process — SQLite queries, Google
Sheets appends, Drive uploads, ticket rendering, Excel export — still shared
asyncio's *default* executor.  On a 2-vCPU Fly machine that is six threads for
the whole process, and Google's client libraries default to **no timeout at
all**: a connection that goes quiet without closing held its thread until the
OS gave up, minutes later.  Six of those and the next participant's message
queued behind work that had already stopped making progress.  The bot was not
busy, it was waiting for a thread — which from the outside is exactly what a
frozen bot looks like.

Two things are pinned here:

* slow work runs on its own bounded pool, so it can never take the database's
  threads away (the database is on the critical path of every single update);
* the Google clients get real timeouts, so a stalled call fails and releases
  its thread instead of holding it forever.

The first two tests fail on the old code: they occupy the default executor and
then call the Sheets/Drive helpers, which used to queue behind it.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot import executors  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.services import drive, google_auth, sheets  # noqa: E402
from bot.services.google_auth import TIMEOUT  # noqa: E402

# Anything slower than this means the call waited for a busy pool rather than
# running: the blockers hold their threads for 30s, the work here is trivial.
PROMPT_SECONDS = 2.0


async def _default_executor_workers() -> int:
    """Threads in asyncio's default executor, creating it if needed.

    The executor is created lazily on first use, so reading ``_max_workers``
    before anything has run would hit ``None``.
    """
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(lambda: None)
    return loop._default_executor._max_workers  # type: ignore[union-attr]


async def _with_saturated_default_executor(call) -> float:
    """Occupy every default-executor thread, then time ``call``.

    These are the threads the database uses, so anything still queued on them
    here is by definition competing with every participant's next message.
    """
    loop = asyncio.get_running_loop()
    workers = await _default_executor_workers()
    gate = threading.Event()
    blockers = [loop.run_in_executor(None, gate.wait, 30) for _ in range(workers)]
    await asyncio.sleep(0)  # let the blockers claim their threads
    try:
        started = time.perf_counter()
        try:
            await asyncio.wait_for(call(), timeout=PROMPT_SECONDS)
        except asyncio.TimeoutError:
            raise AssertionError(
                f"the call did not finish within {PROMPT_SECONDS:.0f}s while the default "
                "executor was busy — this is the frozen-bot symptom: it is queued behind "
                "the database's threads instead of running on the slow-work pool"
            ) from None
        return time.perf_counter() - started
    finally:
        gate.set()
        for blocker in blockers:
            blocker.cancel()


def test_sheets_append_does_not_queue_behind_a_busy_default_executor() -> None:
    """A stalled Google call must not delay the Sheets append that follows it."""

    async def run() -> None:
        real = sheets._append_application
        sheets._append_application = lambda *a, **k: None
        try:
            config = SimpleNamespace(google_credentials_file="c.json", spreadsheet_id="s")
            elapsed = await _with_saturated_default_executor(
                lambda: sheets.append_application(config, object(), [], [])
            )
        finally:
            sheets._append_application = real

        assert elapsed < PROMPT_SECONDS, (
            f"Sheets append waited {elapsed:.1f}s — it is still sharing the default "
            "executor with the database, so a slow Google call can freeze the bot"
        )

    asyncio.run(run())


def test_drive_upload_does_not_queue_behind_a_busy_default_executor() -> None:
    """Same for photo uploads, the slowest Google call of all."""

    async def run() -> None:
        real = drive._upload_photos
        drive._upload_photos = lambda *a, **k: []
        try:
            elapsed = await _with_saturated_default_executor(
                lambda: drive.upload_photos("c.json", "folder", [])
            )
        finally:
            drive._upload_photos = real

        assert elapsed < PROMPT_SECONDS, (
            f"Drive upload waited {elapsed:.1f}s — unbounded Google calls are still "
            "competing with the database for threads"
        )

    asyncio.run(run())


def test_database_stays_responsive_while_the_heavy_pool_is_full() -> None:
    """The point of the split: a full slow-work pool is invisible to the database."""

    async def run() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "starve.db"))
            await db.init()
            await db.get_tenant("promotors")  # warm the pool before measuring

            gate = threading.Event()
            loop = asyncio.get_running_loop()
            busy = [
                asyncio.ensure_future(executors.run_heavy(gate.wait, 30))
                for _ in range(executors.MAX_WORKERS)
            ]
            await asyncio.sleep(0.2)  # every heavy thread is now blocked

            try:
                started = time.perf_counter()
                await asyncio.wait_for(db.get_tenant("promotors"), timeout=PROMPT_SECONDS)
                elapsed = time.perf_counter() - started
            finally:
                gate.set()
                for task in busy:
                    task.cancel()

            assert elapsed < PROMPT_SECONDS, (
                f"a database query took {elapsed:.1f}s while slow work was queued — the "
                "database and the Google calls are sharing one pool again"
            )
            db.close()

    asyncio.run(run())


def test_heavy_pool_is_separate_and_bounded() -> None:
    """Separate from asyncio's default executor, and capped."""

    async def run() -> None:
        await executors.run_heavy(lambda: None)  # force creation
        pool = executors.get_executor()

        assert pool is not asyncio.get_running_loop()._default_executor  # type: ignore[union-attr]
        assert isinstance(pool, ThreadPoolExecutor)
        assert executors.MAX_WORKERS > 0
        assert pool._max_workers == executors.MAX_WORKERS, "the slow-work pool is unbounded"

    asyncio.run(run())


def test_run_heavy_returns_values_and_propagates_errors() -> None:
    """It has to be a drop-in for the ``asyncio.to_thread`` calls it replaced."""

    async def run() -> None:
        assert await executors.run_heavy(lambda a, b=0: a + b, 2, b=3) == 5
        assert await executors.run_heavy(lambda: None) is None

        def boom() -> None:
            raise ValueError("kaboom")

        try:
            await executors.run_heavy(boom)
        except ValueError as exc:
            assert "kaboom" in str(exc)
        else:  # pragma: no cover - only on failure
            raise AssertionError("run_heavy swallowed an exception")

    asyncio.run(run())


def test_sheets_client_carries_a_request_timeout() -> None:
    """gspread defaults to ``timeout=None``, i.e. wait forever."""
    assert all(part is not None and part > 0 for part in TIMEOUT), TIMEOUT

    import gspread

    real_authorize, real_creds = sheets.gspread.authorize, sheets.get_credentials
    sheets.get_credentials = lambda _f: object()
    sheets.gspread.authorize = lambda _c: gspread.Client(auth=None, session=object())
    try:
        client = sheets._client("creds.json")
    finally:
        sheets.gspread.authorize = real_authorize
        sheets.get_credentials = real_creds

    assert client.http_client.timeout == TIMEOUT, (
        "the Sheets client has no request timeout — a stalled Google connection "
        "would hold its worker thread until the OS gave up"
    )


def test_drive_service_is_built_with_a_timeout_transport() -> None:
    """``discovery.build`` otherwise creates an ``httplib2.Http`` that waits forever."""
    from google_auth_httplib2 import AuthorizedHttp

    captured: dict = {}
    real_build = drive.build
    real_creds = drive.get_credentials
    real_shared = google_auth.get_credentials
    # ``authorized_http`` looks the credentials up inside google_auth, so both
    # names have to be replaced to keep this test off the filesystem.
    drive.get_credentials = lambda _f: object()
    google_auth.get_credentials = lambda _f: object()
    drive.build = lambda *a, **k: captured.update(k) or SimpleNamespace()
    try:
        drive._upload_photos("creds.json", "folder", [])
    finally:
        drive.build = real_build
        drive.get_credentials = real_creds
        google_auth.get_credentials = real_shared

    http = captured.get("http")
    assert isinstance(http, AuthorizedHttp), (
        "the Drive service was built without an explicit transport, so it falls back "
        "to the httplib2 default of no timeout"
    )
    assert http.timeout == TIMEOUT, http.timeout


def test_shutdown_is_safe_and_the_pool_recreates() -> None:
    """Shutdown runs on the way out; the pool must come back if work arrives."""

    async def run() -> None:
        executors.shutdown()
        assert await executors.run_heavy(lambda: "alive") == "alive"
        executors.shutdown()  # idempotent

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover - manual run helper
    test_sheets_append_does_not_queue_behind_a_busy_default_executor()
    test_drive_upload_does_not_queue_behind_a_busy_default_executor()
    test_database_stays_responsive_while_the_heavy_pool_is_full()
    test_heavy_pool_is_separate_and_bounded()
    test_run_heavy_returns_values_and_propagates_errors()
    test_sheets_client_carries_a_request_timeout()
    test_drive_service_is_built_with_a_timeout_transport()
    test_shutdown_is_safe_and_the_pool_recreates()
    print("ok")
