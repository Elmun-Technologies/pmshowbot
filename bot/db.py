"""SQLite persistence for applications and the registration-number counter.

Uses the stdlib ``sqlite3`` module. Blocking calls are wrapped with
``asyncio.to_thread`` in the async helpers so they don't block the event loop.
The database is small and single-instance, so this is more than fast enough.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"


@dataclass
class Application:
    id: int
    user_id: int
    username: str
    country: str
    plate: str
    direction: str
    phone: str
    photo_file_ids: list[str]
    photo_paths: list[str]
    status: str
    reg_number: Optional[int]
    created_at: str
    processed_at: Optional[str]
    processed_by: Optional[str]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS applications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    username       TEXT NOT NULL DEFAULT '',
    country        TEXT NOT NULL DEFAULT '',
    plate          TEXT NOT NULL DEFAULT '',
    direction      TEXT NOT NULL DEFAULT '',
    phone          TEXT NOT NULL DEFAULT '',
    photo_file_ids TEXT NOT NULL DEFAULT '[]',
    photo_paths    TEXT NOT NULL DEFAULT '[]',
    status         TEXT NOT NULL DEFAULT 'pending',
    reg_number     INTEGER,
    created_at     TEXT NOT NULL,
    processed_at   TEXT,
    processed_by   TEXT
);
CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_application(row: sqlite3.Row) -> Application:
    return Application(
        id=row["id"],
        user_id=row["user_id"],
        username=row["username"],
        country=row["country"],
        plate=row["plate"],
        direction=row["direction"],
        phone=row["phone"],
        photo_file_ids=json.loads(row["photo_file_ids"]),
        photo_paths=json.loads(row["photo_paths"]),
        status=row["status"],
        reg_number=row["reg_number"],
        created_at=row["created_at"],
        processed_at=row["processed_at"],
        processed_by=row["processed_by"],
    )


class Database:
    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        # Enforce serialized writes and better concurrency behaviour.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    # --- sync core operations (run inside to_thread) ---

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _create_application(
        self,
        *,
        user_id: int,
        username: str,
        country: str,
        plate: str,
        direction: str,
        phone: str,
        photo_file_ids: list[str],
        photo_paths: list[str],
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO applications
                    (user_id, username, country, plate, direction, phone,
                     photo_file_ids, photo_paths, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    username,
                    country,
                    plate,
                    direction,
                    phone,
                    json.dumps(photo_file_ids, ensure_ascii=False),
                    json.dumps(photo_paths, ensure_ascii=False),
                    STATUS_PENDING,
                    _now(),
                ),
            )
            return int(cur.lastrowid)

    def _get_application(self, app_id: int) -> Optional[Application]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE id = ?", (app_id,)
            ).fetchone()
            return _row_to_application(row) if row else None

    def _get_latest_for_user(self, user_id: int) -> Optional[Application]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM applications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            return _row_to_application(row) if row else None

    def _has_active_application(self, user_id: int) -> Optional[Application]:
        """Return a pending or approved application for the user, if any."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM applications
                WHERE user_id = ? AND status IN (?, ?)
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, STATUS_PENDING, STATUS_APPROVED),
            ).fetchone()
            return _row_to_application(row) if row else None

    def _approve(self, app_id: int, moderator: str) -> Optional[int]:
        """Atomically assign the next registration number and mark approved.

        Returns the assigned number, or None if the application was not pending
        (already processed / not found).
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, reg_number FROM applications WHERE id = ?", (app_id,)
            ).fetchone()
            if row is None or row["status"] != STATUS_PENDING:
                conn.rollback()
                return None
            next_number = conn.execute(
                "SELECT COALESCE(MAX(reg_number), 0) + 1 FROM applications"
            ).fetchone()[0]
            conn.execute(
                """
                UPDATE applications
                SET status = ?, reg_number = ?, processed_at = ?, processed_by = ?
                WHERE id = ?
                """,
                (STATUS_APPROVED, next_number, _now(), moderator, app_id),
            )
            conn.commit()
            return int(next_number)

    def _reject(self, app_id: int, moderator: str) -> bool:
        """Mark rejected. Returns True if it was pending and got rejected."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM applications WHERE id = ?", (app_id,)
            ).fetchone()
            if row is None or row["status"] != STATUS_PENDING:
                conn.rollback()
                return False
            conn.execute(
                """
                UPDATE applications
                SET status = ?, processed_at = ?, processed_by = ?
                WHERE id = ?
                """,
                (STATUS_REJECTED, _now(), moderator, app_id),
            )
            conn.commit()
            return True

    # --- admin panel queries ---

    def _list_applications(
        self, status: Optional[str] = None, search: Optional[str] = None, limit: int = 500
    ) -> list[Application]:
        query = "SELECT * FROM applications"
        conds: list[str] = []
        params: list = []
        if status:
            conds.append("status = ?")
            params.append(status)
        if search:
            conds.append(
                "(plate LIKE ? OR phone LIKE ? OR username LIKE ? OR country LIKE ?)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like])
        if conds:
            query += " WHERE " + " AND ".join(conds)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [_row_to_application(r) for r in rows]

    def _stats(self) -> dict:
        with self._connect() as conn:
            by_status = {
                row["status"]: row["n"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
                ).fetchall()
            }
            by_direction = {
                row["direction"] or "—": row["n"]
                for row in conn.execute(
                    "SELECT direction, COUNT(*) AS n FROM applications GROUP BY direction ORDER BY n DESC"
                ).fetchall()
            }
            by_country = {
                row["country"] or "—": row["n"]
                for row in conn.execute(
                    "SELECT country, COUNT(*) AS n FROM applications GROUP BY country ORDER BY n DESC"
                ).fetchall()
            }
            max_number = conn.execute(
                "SELECT COALESCE(MAX(reg_number), 0) FROM applications"
            ).fetchone()[0]
        total = sum(by_status.values())
        return {
            "total": total,
            "pending": by_status.get(STATUS_PENDING, 0),
            "approved": by_status.get(STATUS_APPROVED, 0),
            "rejected": by_status.get(STATUS_REJECTED, 0),
            "by_direction": by_direction,
            "by_country": by_country,
            "max_number": int(max_number),
        }

    # --- async wrappers ---

    async def init(self) -> None:
        await asyncio.to_thread(self._init)

    async def list_applications(
        self, status: Optional[str] = None, search: Optional[str] = None, limit: int = 500
    ) -> list[Application]:
        return await asyncio.to_thread(self._list_applications, status, search, limit)

    async def stats(self) -> dict:
        return await asyncio.to_thread(self._stats)

    async def create_application(self, **kwargs) -> int:
        return await asyncio.to_thread(lambda: self._create_application(**kwargs))

    async def get_application(self, app_id: int) -> Optional[Application]:
        return await asyncio.to_thread(self._get_application, app_id)

    async def get_latest_for_user(self, user_id: int) -> Optional[Application]:
        return await asyncio.to_thread(self._get_latest_for_user, user_id)

    async def has_active_application(self, user_id: int) -> Optional[Application]:
        return await asyncio.to_thread(self._has_active_application, user_id)

    async def approve(self, app_id: int, moderator: str) -> Optional[int]:
        return await asyncio.to_thread(self._approve, app_id, moderator)

    async def reject(self, app_id: int, moderator: str) -> bool:
        return await asyncio.to_thread(self._reject, app_id, moderator)
