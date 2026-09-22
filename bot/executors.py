"""A second thread pool for slow, network-bound work.

Everything this process cannot do on the event loop used to share asyncio's
default executor: SQLite queries, Google Sheets appends, Drive uploads, ticket
rendering and Excel export.  On a 2-vCPU Fly machine that executor has six
threads for the whole process.

Google's libraries default to *no timeout at all*, so a connection that goes
quiet without closing — which is what a degraded network looks like — held its
thread until the operating system gave up, minutes later.  Six of those and the
next participant's "send your plate number" queued behind work that had already
stopped making progress.  The bot was not busy; it was waiting for a thread,
which from the outside is indistinguishable from a frozen bot.

The database is what every single update needs, and it is fast.  It keeps the
default executor.  Slow work moves here, to its own bounded pool, where being
slow can no longer take the database's threads away.  The Google clients also
get real timeouts (see :mod:`bot.services.google_auth`), so a stalled call
eventually fails and releases its thread instead of holding it forever.
"""
from __future__ import annotations

import asyncio
import functools
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

# Bounded on purpose.  These calls are I/O-bound and a single photo upload can
# stay open for seconds, but a pool that grows without limit only hides the
# problem: on a 2-vCPU machine it turns one slow Google into hundreds of
# threads.  A bounded pool makes the work queue instead, which is visible in the
# logs and keeps memory predictable.
MAX_WORKERS = 8

_executor: Optional[ThreadPoolExecutor] = None
_lock = threading.Lock()


def get_executor() -> ThreadPoolExecutor:
    """The shared pool for slow work, created on first use."""
    global _executor
    if _executor is None:
        with _lock:
            # Double-checked: several updates can call this at the same time.
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=MAX_WORKERS,
                    thread_name_prefix="heavy",
                )
    return _executor


async def run_heavy(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run blocking ``func`` in the slow-work pool rather than the default one.

    Same calling convention as ``asyncio.to_thread``, only the pool differs.
    """
    call = functools.partial(func, *args, **kwargs)
    return await asyncio.get_running_loop().run_in_executor(get_executor(), call)


def shutdown(*, wait: bool = False) -> None:
    """Stop the pool.  Safe to call when it was never created."""
    global _executor
    with _lock:
        executor, _executor = _executor, None
    if executor is not None:
        executor.shutdown(wait=wait)
