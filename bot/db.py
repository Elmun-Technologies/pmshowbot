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
from dataclasses import dataclass, field
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
    language: str = "ru"
    full_name: str = ""
    # Close-ups of what the participant changed on the car (hood, trunk, audio…).
    mod_file_ids: list[str] = field(default_factory=list)
    mod_paths: list[str] = field(default_factory=list)
    # Photo submitted for the personal event badge (sent to the bot outside
    # the registration flow, in reply to a broadcast asking for one).
    badge_photo_file_id: str = ""
    badge_photo_path: str = ""


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
    processed_by   TEXT,
    language       TEXT NOT NULL DEFAULT 'ru',
    full_name      TEXT NOT NULL DEFAULT '',
    mod_file_ids   TEXT NOT NULL DEFAULT '[]',
    mod_paths      TEXT NOT NULL DEFAULT '[]',
    badge_photo_file_id TEXT NOT NULL DEFAULT '',
    badge_photo_path    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id);

CREATE TABLE IF NOT EXISTS bot_users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT NOT NULL DEFAULT '',
    language   TEXT NOT NULL DEFAULT 'ru',
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL
);
"""

# Lightweight migrations: (column, "ALTER ... ADD COLUMN ...") applied if missing.
_MIGRATIONS = [
    ("language", "ALTER TABLE applications ADD COLUMN language TEXT NOT NULL DEFAULT 'ru'"),
    ("full_name", "ALTER TABLE applications ADD COLUMN full_name TEXT NOT NULL DEFAULT ''"),
    ("mod_file_ids", "ALTER TABLE applications ADD COLUMN mod_file_ids TEXT NOT NULL DEFAULT '[]'"),
    ("mod_paths", "ALTER TABLE applications ADD COLUMN mod_paths TEXT NOT NULL DEFAULT '[]'"),
    ("badge_photo_file_id", "ALTER TABLE applications ADD COLUMN badge_photo_file_id TEXT NOT NULL DEFAULT ''"),
    ("badge_photo_path", "ALTER TABLE applications ADD COLUMN badge_photo_path TEXT NOT NULL DEFAULT ''"),
]

# Renamed directions: applications stored under the old name are moved to the
# new one so the admin panel and the stats keep counting them as one category.
# Each entry is idempotent — re-running it on an already-renamed database is a
# no-op.
_DIRECTION_RENAMES = [
    ("Дрифт", "Adrenaline Drift"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _row_to_application(row: sqlite3.Row) -> Application:
    keys = row.keys()
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
        language=row["language"] if "language" in keys else "ru",
        full_name=row["full_name"] if "full_name" in keys else "",
        mod_file_ids=json.loads(row["mod_file_ids"]) if "mod_file_ids" in keys else [],
        mod_paths=json.loads(row["mod_paths"]) if "mod_paths" in keys else [],
        badge_photo_file_id=row["badge_photo_file_id"] if "badge_photo_file_id" in keys else "",
        badge_photo_path=row["badge_photo_path"] if "badge_photo_path" in keys else "",
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
            # Apply migrations for databases created by an older schema.
            existing = {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}
            for column, ddl in _MIGRATIONS:
                if column not in existing:
                    conn.execute(ddl)
            for old, new in _DIRECTION_RENAMES:
                conn.execute(
                    "UPDATE applications SET direction = ? WHERE direction = ?",
                    (new, old),
                )
            # Anyone who already filed an application is a known bot user.
            conn.execute(
                """
                INSERT OR IGNORE INTO bot_users (user_id, username, language, first_seen, last_seen)
                SELECT user_id,
                       MAX(username),
                       MAX(language),
                       MIN(created_at),
                       MAX(created_at)
                FROM applications
                GROUP BY user_id
                """
            )

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
        language: str = "ru",
        full_name: str = "",
        mod_file_ids: list[str] | None = None,
        mod_paths: list[str] | None = None,
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO applications
                    (user_id, username, country, plate, direction, phone,
                     photo_file_ids, photo_paths, status, created_at, language, full_name,
                     mod_file_ids, mod_paths)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    language,
                    full_name,
                    json.dumps(mod_file_ids or [], ensure_ascii=False),
                    json.dumps(mod_paths or [], ensure_ascii=False),
                ),
            )
            app_id = int(cur.lastrowid)
            conn.execute(
                """
                INSERT OR IGNORE INTO bot_users (user_id, username, language, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, username or "", language or "ru", _now(), _now()),
            )
            return app_id

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

    def _set_badge_photo(self, user_id: int, file_id: str, path: str) -> Optional[int]:
        """Attach a badge photo to the user's latest application.

        Returns the application id, or None if the user has no application yet.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM applications WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            ).fetchone()
            if row is None:
                return None
            app_id = int(row["id"])
            conn.execute(
                "UPDATE applications SET badge_photo_file_id = ?, badge_photo_path = ? WHERE id = ?",
                (file_id, path, app_id),
            )
            return app_id

    def _get_user_language(self, user_id: int) -> str:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT language FROM bot_users WHERE user_id = ?", (user_id,)
            ).fetchone()
            return (row["language"] if row else None) or "ru"

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

    def _set_status(self, app_id: int, status: str, moderator: str) -> bool:
        """Admin override: force an application to any status, regardless of
        its current one. Assigns a registration number on the transition to
        approved if it doesn't already have one (keeps the existing number if
        it does, e.g. rejected-then-re-approved). Returns False if the
        application doesn't exist.
        """
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT reg_number FROM applications WHERE id = ?", (app_id,)
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            reg_number = row["reg_number"]
            if status == STATUS_APPROVED and reg_number is None:
                reg_number = conn.execute(
                    "SELECT COALESCE(MAX(reg_number), 0) + 1 FROM applications"
                ).fetchone()[0]
            conn.execute(
                """
                UPDATE applications
                SET status = ?, reg_number = ?, processed_at = ?, processed_by = ?
                WHERE id = ?
                """,
                (status, reg_number, _now(), moderator, app_id),
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
                "(plate LIKE ? OR phone LIKE ? OR username LIKE ? OR country LIKE ? OR full_name LIKE ?)"
            )
            like = f"%{search}%"
            params.extend([like, like, like, like, like])
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
            by_language = {
                row["language"] or "ru": row["n"]
                for row in conn.execute(
                    "SELECT language, COUNT(*) AS n FROM applications GROUP BY language ORDER BY n DESC"
                ).fetchall()
            }
            by_date = {
                row["dt"]: row["n"]
                for row in conn.execute(
                    "SELECT SUBSTR(created_at, 1, 10) AS dt, COUNT(*) AS n FROM applications GROUP BY dt ORDER BY dt DESC LIMIT 14"
                ).fetchall()
            }
            max_number = conn.execute(
                "SELECT COALESCE(MAX(reg_number), 0) FROM applications"
            ).fetchone()[0]
            approved_users = conn.execute(
                """
                SELECT COUNT(DISTINCT user_id) AS n
                FROM applications WHERE status = ?
                """,
                (STATUS_APPROVED,),
            ).fetchone()["n"]
        total = sum(by_status.values())
        return {
            "total": total,
            "pending": by_status.get(STATUS_PENDING, 0),
            "approved": by_status.get(STATUS_APPROVED, 0),
            "rejected": by_status.get(STATUS_REJECTED, 0),
            "by_direction": by_direction,
            "by_country": by_country,
            "by_language": by_language,
            "by_date": by_date,
            "max_number": int(max_number),
            "approved_users": int(approved_users),
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

    async def set_badge_photo(self, user_id: int, file_id: str, path: str) -> Optional[int]:
        return await asyncio.to_thread(self._set_badge_photo, user_id, file_id, path)

    async def get_user_language(self, user_id: int) -> str:
        return await asyncio.to_thread(self._get_user_language, user_id)

    async def approve(self, app_id: int, moderator: str) -> Optional[int]:
        return await asyncio.to_thread(self._approve, app_id, moderator)

    async def reject(self, app_id: int, moderator: str) -> bool:
        return await asyncio.to_thread(self._reject, app_id, moderator)

    async def set_status(self, app_id: int, status: str, moderator: str) -> bool:
        return await asyncio.to_thread(self._set_status, app_id, status, moderator)

    def _touch_user(self, user_id: int, username: str = "", language: str = "ru") -> None:
        now = _now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM bot_users WHERE user_id = ?", (user_id,)
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO bot_users (user_id, username, language, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, username or "", language or "ru", now, now),
                )
            else:
                conn.execute(
                    """
                    UPDATE bot_users
                    SET username = CASE WHEN ? != '' THEN ? ELSE username END,
                        language = CASE WHEN ? != '' THEN ? ELSE language END,
                        last_seen = ?
                    WHERE user_id = ?
                    """,
                    (username or "", username or "", language or "", language or "", now, user_id),
                )

    def _recipients(
        self,
        audience: str,
        languages: Optional[list[str]] = None,
        directions: Optional[list[str]] = None,
    ) -> list[tuple[int, str]]:
        """Unique (user_id, language) for a broadcast audience.

        ``languages`` narrows recipients to those specific language codes
        (e.g. ["uz"]) and ``directions`` to specific participation directions
        (matched against the canonical names in constants.DIRECTIONS). Either
        filter left as None/empty leaves that dimension unrestricted.
        """
        lang_filter = [l for l in (languages or []) if l]
        dir_filter = [d for d in (directions or []) if d]
        with self._connect() as conn:
            if audience == "starters":
                query = "SELECT u.user_id, u.language FROM bot_users u"
                conds: list[str] = []
                params: list = []
                if dir_filter:
                    placeholders = ",".join("?" for _ in dir_filter)
                    conds.append(
                        "EXISTS (SELECT 1 FROM applications a "
                        f"WHERE a.user_id = u.user_id AND a.direction IN ({placeholders}))"
                    )
                    params.extend(dir_filter)
                if lang_filter:
                    placeholders = ",".join("?" for _ in lang_filter)
                    conds.append(f"u.language IN ({placeholders})")
                    params.extend(lang_filter)
                if conds:
                    query += " WHERE " + " AND ".join(conds)
                rows = conn.execute(query, params).fetchall()
            elif audience == "incomplete":
                # Users with no application at all have no direction to match,
                # so a direction filter excludes this whole audience.
                if dir_filter:
                    rows = []
                else:
                    query = """
                        SELECT u.user_id, u.language
                        FROM bot_users u
                        WHERE NOT EXISTS (
                            SELECT 1 FROM applications a WHERE a.user_id = u.user_id
                        )
                    """
                    params = []
                    if lang_filter:
                        placeholders = ",".join("?" for _ in lang_filter)
                        query += f" AND u.language IN ({placeholders})"
                        params.extend(lang_filter)
                    rows = conn.execute(query, params).fetchall()
            elif audience == "all_apps":
                query = """
                    SELECT user_id, language FROM applications
                    WHERE id IN (SELECT MAX(id) FROM applications GROUP BY user_id)
                """
                params = []
                if lang_filter:
                    placeholders = ",".join("?" for _ in lang_filter)
                    query += f" AND language IN ({placeholders})"
                    params.extend(lang_filter)
                if dir_filter:
                    placeholders = ",".join("?" for _ in dir_filter)
                    query += f" AND direction IN ({placeholders})"
                    params.extend(dir_filter)
                rows = conn.execute(query, params).fetchall()
            else:
                status = {
                    "approved": STATUS_APPROVED,
                    "pending": STATUS_PENDING,
                    "rejected": STATUS_REJECTED,
                }.get(audience, STATUS_APPROVED)
                query = """
                    SELECT user_id, language FROM applications
                    WHERE id IN (
                        SELECT MAX(id) FROM applications
                        WHERE status = ?
                        GROUP BY user_id
                    )
                """
                params = [status]
                if lang_filter:
                    placeholders = ",".join("?" for _ in lang_filter)
                    query += f" AND language IN ({placeholders})"
                    params.extend(lang_filter)
                if dir_filter:
                    placeholders = ",".join("?" for _ in dir_filter)
                    query += f" AND direction IN ({placeholders})"
                    params.extend(dir_filter)
                rows = conn.execute(query, params).fetchall()
            return [(int(r["user_id"]), r["language"] or "ru") for r in rows]

    def _audience_counts(self) -> dict[str, int]:
        return {key: len(self._recipients(key)) for key in (
            "approved", "pending", "rejected", "all_apps", "incomplete", "starters"
        )}

    async def touch_user(self, user_id: int, username: str = "", language: str = "ru") -> None:
        await asyncio.to_thread(self._touch_user, user_id, username, language)

    async def recipients(
        self,
        audience: str,
        languages: Optional[list[str]] = None,
        directions: Optional[list[str]] = None,
    ) -> list[tuple[int, str]]:
        return await asyncio.to_thread(self._recipients, audience, languages, directions)

    async def audience_counts(self) -> dict[str, int]:
        return await asyncio.to_thread(self._audience_counts)
