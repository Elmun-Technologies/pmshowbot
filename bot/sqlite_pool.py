"""Connection setup shared by the two SQLite-backed stores.

Both :mod:`bot.db` (applications, tenants, users) and
:mod:`bot.services.fsm_storage` (registration forms in progress) cache one
SQLite connection per worker thread and reuse it.  Opening a connection and
tuning it is not free — on the Fly.io network volume the pragmas below are real
disk I/O — so the setup runs once per connection and lives here, in one place,
instead of being copied into each store.

``PRAGMA journal_mode=WAL`` needs a word of its own: it takes a write lock and,
unlike ordinary statements, it does **not** honour ``busy_timeout``.  When
several threads touch a brand-new database file for the first time, the losers
of that race get ``database is locked`` immediately instead of waiting.  That is
a transient race between readers of the same code, not a broken database, so it
is retried here with backoff.  Without the retry the first busy moment after a
deploy — several participants registering at once — makes whichever store loses
the race fail to initialise.
"""
from __future__ import annotations

import sqlite3
import time

# Wait for a concurrent writer instead of instantly raising "database is
# locked".  Several tenant bots plus the admin panel share one file.
DEFAULT_TIMEOUT_SECONDS = 30.0

# ``journal_mode=WAL`` ignores the timeout above, so retry it ourselves.
BUSY_RETRIES = 8
BUSY_BACKOFF_SECONDS = 0.05
BUSY_BACKOFF_CAP_SECONDS = 0.2

# ~16 MB of page cache, per connection.  The cache is filled lazily, and the
# database is far smaller than this, so the pages stay in RAM rather than being
# re-read from the network volume.
CACHE_SIZE_KIB = 16000


def is_locked_error(exc: BaseException) -> bool:
    """True when SQLite refused because another connection holds the lock."""
    message = str(exc).lower()
    return "locked" in message or "busy" in message


def _open_and_tune(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=DEFAULT_TIMEOUT_SECONDS)
    try:
        conn.row_factory = sqlite3.Row
        # WAL lets readers run while a writer holds the database, which is what
        # allows several tenant bots plus the admin panel to share one file.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        # NORMAL is the documented companion of WAL: durable across process
        # crashes, and it removes an fsync from every commit.
        conn.execute("PRAGMA synchronous=NORMAL")
        # Keep pages and temporary tables in RAM, off the network volume.
        conn.execute(f"PRAGMA cache_size=-{CACHE_SIZE_KIB}")
        conn.execute("PRAGMA temp_store=MEMORY")
    except Exception:
        # Do not leak a half-configured handle when a pragma fails.
        conn.close()
        raise
    return conn


def connect_sqlite(path: str) -> sqlite3.Connection:
    """Open ``path`` and tune it the way both stores need.

    Retries the transient ``database is locked`` that switching a brand-new file
    into WAL mode can raise when several threads open it at the same instant.
    """
    for attempt in range(BUSY_RETRIES):
        try:
            return _open_and_tune(path)
        except sqlite3.OperationalError as exc:
            if attempt == BUSY_RETRIES - 1 or not is_locked_error(exc):
                raise
            time.sleep(
                min(BUSY_BACKOFF_SECONDS * (attempt + 1), BUSY_BACKOFF_CAP_SECONDS)
            )
    raise AssertionError("unreachable")  # pragma: no cover
