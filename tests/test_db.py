"""Tests for sequential registration numbering and status transitions."""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.db import Database, STATUS_APPROVED, STATUS_REJECTED  # noqa: E402


def _make_app(db: Database, user_id: int) -> int:
    return asyncio.run(
        db.create_application(
            user_id=user_id,
            username=f"@user{user_id}",
            country="Узбекистан",
            plate="01A123BC",
            direction="Тюнинг",
            phone="+998900000000",
            photo_file_ids=["a", "b", "c", "d"],
            photo_paths=["p1", "p2", "p3", "p4"],
        )
    )


def test_sequential_numbers_and_rejection_gaps():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        asyncio.run(db.init())

        a1 = _make_app(db, 1)
        a2 = _make_app(db, 2)
        a3 = _make_app(db, 3)

        # Approve first -> №1
        assert asyncio.run(db.approve(a1, "@mod")) == 1
        # Reject second -> consumes no number
        assert asyncio.run(db.reject(a2, "@mod")) is True
        # Approve third -> №2 (rejection did not increment the counter)
        assert asyncio.run(db.approve(a3, "@mod")) == 2

        # Double-processing is a no-op.
        assert asyncio.run(db.approve(a1, "@mod")) is None
        assert asyncio.run(db.reject(a1, "@mod")) is False

        app1 = asyncio.run(db.get_application(a1))
        app2 = asyncio.run(db.get_application(a2))
        app3 = asyncio.run(db.get_application(a3))
        assert app1.status == STATUS_APPROVED and app1.reg_number == 1
        assert app2.status == STATUS_REJECTED and app2.reg_number is None
        assert app3.status == STATUS_APPROVED and app3.reg_number == 2


def test_active_application_lookup():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        asyncio.run(db.init())

        a1 = _make_app(db, 42)
        # Pending counts as active.
        assert asyncio.run(db.has_active_application(42)) is not None
        # After rejection, no active application remains.
        asyncio.run(db.reject(a1, "@mod"))
        assert asyncio.run(db.has_active_application(42)) is None


def test_language_roundtrip_and_migration():
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.db")

        # Simulate an OLD database created before the `language` column existed.
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                username TEXT DEFAULT '', country TEXT DEFAULT '', plate TEXT DEFAULT '',
                direction TEXT DEFAULT '', phone TEXT DEFAULT '',
                photo_file_ids TEXT DEFAULT '[]', photo_paths TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending', reg_number INTEGER,
                created_at TEXT NOT NULL, processed_at TEXT, processed_by TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO applications (user_id, created_at) VALUES (1, '2020-01-01')"
        )
        conn.commit()
        conn.close()

        db = Database(path)
        asyncio.run(db.init())  # should ADD the language column

        # Old row defaults to ru and empty full_name
        old = asyncio.run(db.get_application(1))
        assert old.language == "ru" and old.full_name == ""

        # New row can store uz and full_name and read it back
        new_id = asyncio.run(
            db.create_application(
                user_id=2, username="@u", full_name="Nazir Elmurodov", country="Узбекистан", plate="X", direction="Adrenaline Drift",
                phone="+998", photo_file_ids=[], photo_paths=[], language="uz",
            )
        )
        app = asyncio.run(db.get_application(new_id))
        assert app.language == "uz" and app.full_name == "Nazir Elmurodov"


def test_modification_photos_roundtrip_and_migration():
    import sqlite3

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.db")

        # A database created before the modification-photo columns existed.
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                username TEXT DEFAULT '', country TEXT DEFAULT '', plate TEXT DEFAULT '',
                direction TEXT DEFAULT '', phone TEXT DEFAULT '',
                photo_file_ids TEXT DEFAULT '[]', photo_paths TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending', reg_number INTEGER,
                created_at TEXT NOT NULL, processed_at TEXT, processed_by TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO applications (user_id, created_at) VALUES (1, '2020-01-01')"
        )
        conn.commit()
        conn.close()

        db = Database(path)
        asyncio.run(db.init())

        # An application filed before the question existed simply has none.
        assert asyncio.run(db.get_application(1)).mod_paths == []

        new_id = asyncio.run(
            db.create_application(
                user_id=2, username="@u", country="Узбекистан", plate="01A123BC",
                direction="SPL Тюнинг", phone="+998900000000",
                photo_file_ids=["a", "b", "c", "d"],
                photo_paths=["p1", "p2", "p3", "p4"],
                mod_file_ids=["m1", "m2"],
                mod_paths=["/media/2/mod_1.jpg", "/media/2/mod_2.jpg"],
            )
        )
        app = asyncio.run(db.get_application(new_id))
        assert app.mod_file_ids == ["m1", "m2"]
        assert app.mod_paths == ["/media/2/mod_1.jpg", "/media/2/mod_2.jpg"]
        # Untouched by the new columns.
        assert len(app.photo_paths) == 4


def test_renamed_direction_is_migrated():
    # Applications registered under the old name follow the rename, so the
    # admin panel and the stats keep them in one category.
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "t.db")
        db = Database(path)
        asyncio.run(db.init())

        app_id = asyncio.run(
            db.create_application(
                user_id=7, username="@u", full_name="Old Row", country="Узбекистан",
                plate="01A123BC", direction="Дрифт", phone="+998",
                photo_file_ids=[], photo_paths=[],
            )
        )

        # Restarting the bot applies the rename.
        asyncio.run(Database(path).init())
        assert asyncio.run(db.get_application(app_id)).direction == "Adrenaline Drift"
        assert asyncio.run(db.stats())["by_direction"].get("Adrenaline Drift") == 1


def test_broadcast_audiences():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        asyncio.run(db.init())
        a1 = _make_app(db, 1)
        a2 = _make_app(db, 2)
        a3 = _make_app(db, 3)
        asyncio.run(db.approve(a1, "@mod"))
        asyncio.run(db.reject(a2, "@mod"))
        asyncio.run(db.touch_user(99, username="@ghost", language="uz"))

        approved = asyncio.run(db.recipients("approved"))
        pending = asyncio.run(db.recipients("pending"))
        rejected = asyncio.run(db.recipients("rejected"))
        incomplete = asyncio.run(db.recipients("incomplete"))
        starters = asyncio.run(db.recipients("starters"))
        assert {u for u, _ in approved} == {1}
        assert {u for u, _ in pending} == {3}
        assert {u for u, _ in rejected} == {2}
        assert {u for u, _ in incomplete} == {99}
        assert {u for u, _ in starters} == {1, 2, 3, 99}
        counts = asyncio.run(db.audience_counts())
        assert counts["approved"] == 1 and counts["incomplete"] == 1


def test_recipients_filter_by_language_and_direction():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        asyncio.run(db.init())

        def app(user_id: int, direction: str, lang: str) -> int:
            return asyncio.run(
                db.create_application(
                    user_id=user_id,
                    username=f"@u{user_id}",
                    country="Узбекистан",
                    plate=f"01A00{user_id}AA",
                    direction=direction,
                    phone="+998900000000",
                    photo_file_ids=[],
                    photo_paths=[],
                    language=lang,
                )
            )

        a1 = app(1, "Adrenaline Drift", "uz")
        a2 = app(2, "SPL Тюнинг", "ru")
        a3 = app(3, "Adrenaline Drift", "ru")
        for a in (a1, a2, a3):
            asyncio.run(db.approve(a, "@mod"))
        asyncio.run(db.touch_user(99, username="@ghost", language="uz"))

        only_uz = asyncio.run(db.recipients("approved", languages=["uz"]))
        assert {u for u, _ in only_uz} == {1}

        only_drift = asyncio.run(
            db.recipients("approved", directions=["Adrenaline Drift"])
        )
        assert {u for u, _ in only_drift} == {1, 3}

        drift_ru = asyncio.run(
            db.recipients(
                "approved", languages=["ru"], directions=["Adrenaline Drift"]
            )
        )
        assert {u for u, _ in drift_ru} == {3}

        # A direction filter makes no sense for users with no application at
        # all — they never get matched.
        incomplete_with_direction = asyncio.run(
            db.recipients("incomplete", directions=["Adrenaline Drift"])
        )
        assert incomplete_with_direction == []

        # "starters" spans bot_users + applications, so a direction filter has
        # to look the user's application up.
        starters_drift = asyncio.run(
            db.recipients("starters", directions=["Adrenaline Drift"])
        )
        assert {u for u, _ in starters_drift} == {1, 3}


def test_badge_photo_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        asyncio.run(db.init())

        # No application yet → nothing to attach the photo to.
        assert asyncio.run(db.set_badge_photo(5, "file123", "/media/5/badge.jpg")) is None

        app_id = _make_app(db, 5)
        got = asyncio.run(db.set_badge_photo(5, "file123", "/media/5/badge.jpg"))
        assert got == app_id

        app = asyncio.run(db.get_application(app_id))
        assert app.badge_photo_file_id == "file123"
        assert app.badge_photo_path == "/media/5/badge.jpg"

        # Sending a new photo replaces the old one on the same (latest) app.
        asyncio.run(db.set_badge_photo(5, "file456", "/media/5/badge2.jpg"))
        app = asyncio.run(db.get_application(app_id))
        assert app.badge_photo_file_id == "file456"


def test_get_user_language():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        asyncio.run(db.init())
        assert asyncio.run(db.get_user_language(1)) == "ru"  # unknown user → default
        asyncio.run(db.touch_user(1, language="uz"))
        assert asyncio.run(db.get_user_language(1)) == "uz"


if __name__ == "__main__":
    test_sequential_numbers_and_rejection_gaps()
    test_active_application_lookup()
    test_language_roundtrip_and_migration()
    test_modification_photos_roundtrip_and_migration()
    test_renamed_direction_is_migrated()
    test_broadcast_audiences()
    test_recipients_filter_by_language_and_direction()
    test_badge_photo_roundtrip()
    test_get_user_language()
    print("All tests passed.")
