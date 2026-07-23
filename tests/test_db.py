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


if __name__ == "__main__":
    test_sequential_numbers_and_rejection_gaps()
    test_active_application_lookup()
    print("All tests passed.")
