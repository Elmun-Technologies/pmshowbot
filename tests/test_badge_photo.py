"""Tests for the standalone badge-photo intake handler.

Exercises the handler function directly with lightweight fakes instead of a
real aiogram dispatcher/bot, since the routing (StateFilter(None), private
chat only) is already covered by main.py's router wiring.
"""
import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot import texts  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.handlers.badge_photo import receive_badge_photo  # noqa: E402


class _FakePhotoSize:
    def __init__(self, file_id: str):
        self.file_id = file_id


class _FakeMessage:
    def __init__(self, user_id: int, username: str = "", full_name: str = "Test User"):
        self.from_user = SimpleNamespace(id=user_id, username=username, full_name=full_name)
        self.chat = SimpleNamespace(type="private")
        self.photo = [_FakePhotoSize("filexyz")]
        self.answers: list[str] = []

    async def answer(self, text: str) -> None:
        self.answers.append(text)


class _FakeBot:
    def __init__(self):
        self.downloaded: list[tuple[str, str]] = []
        self.sent_photos: list[tuple[int, str, str]] = []

    async def download(self, photo, destination: str) -> None:
        self.downloaded.append((photo.file_id, destination))
        with open(destination, "wb") as fh:
            fh.write(b"fake-jpeg-bytes")

    async def send_photo(self, chat_id: int, photo: str, caption: str = "") -> None:
        self.sent_photos.append((chat_id, photo, caption))


def test_badge_photo_saved_and_admin_notified_when_registered():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        asyncio.run(db.init())
        app_id = asyncio.run(
            db.create_application(
                user_id=1,
                username="@u1",
                country="Узбекистан",
                plate="01A001AA",
                direction="Adrenaline Drift",
                phone="+998900000000",
                photo_file_ids=[],
                photo_paths=[],
                language="uz",
            )
        )
        config = SimpleNamespace(media_dir=tmp, admin_chat_id=-100999)
        bot = _FakeBot()
        message = _FakeMessage(1, username="u1")

        asyncio.run(receive_badge_photo(message, bot, config, db))

        # The applicant gets a confirmation in their own language.
        assert message.answers == [texts.UZ.BADGE_PHOTO_SAVED]
        assert bot.downloaded

        app = asyncio.run(db.get_application(app_id))
        assert app.badge_photo_file_id == "filexyz"
        assert os.path.exists(app.badge_photo_path)

        # The admin chat gets the photo + who it belongs to.
        assert len(bot.sent_photos) == 1
        chat_id, photo, caption = bot.sent_photos[0]
        assert chat_id == -100999
        assert photo == "filexyz"
        assert f"#{app_id}" in caption
        assert "01A001AA" in caption
        assert "Adrenaline Drift" in caption
        assert "@u1" in caption


def test_badge_photo_without_application_skips_admin_notice():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "t.db"))
        asyncio.run(db.init())
        config = SimpleNamespace(media_dir=tmp, admin_chat_id=-100999)
        bot = _FakeBot()
        message = _FakeMessage(42, username="ghost")

        asyncio.run(receive_badge_photo(message, bot, config, db))

        assert message.answers == [texts.RU.BADGE_PHOTO_NO_APP]
        # No application to attribute the photo to → the admin isn't pinged.
        assert bot.sent_photos == []


if __name__ == "__main__":
    test_badge_photo_saved_and_admin_notified_when_registered()
    test_badge_photo_without_application_skips_admin_notice()
    print("All badge-photo tests passed.")
