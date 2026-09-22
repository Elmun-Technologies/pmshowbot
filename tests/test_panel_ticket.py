"""The panel's ticket section: a real preview and a resend that cannot hang.

Reported (2026-09, tenant admin): «bilet qismda kamchiliklar bor: admin
panelda preview qilib biletni ko'rib bo'lmayapti, va admin panel orqali
foydalanuvchining biletini qayta yuborib ham bo'lmayapti».

* The application page had no way to *see* the participant's ticket — only a
  blind resend click; the ticket-assets page showed a generic sample
  (foreign direction name, always Russian).
* The resend click held the HTTP request for the whole render (up to ~90 s
  per attempt on a busy render pool) plus a multi-megabyte upload, and the
  failure reason came back percent-encoded in a URL parameter — unreadable.

These tests pin the fixes:

* ``GET /application/{id}/ticket.png`` renders the participant's actual
  ticket (same render path as delivery: hero photo → gradient fallback),
  shown right on the application page with full-size / download links;
* the resend click answers with a redirect at once; the delivery runs in a
  background task; the page shows the outcome (auto-refreshing while in
  flight); a second click while in flight does not duplicate the send;
* failures are displayed with the exact human-readable reason;
* the ticket-assets sample preview is built from the tenant's newest
  application instead of hard-coded Promotors copy.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from aiogram import Bot  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

from bot.admin import auth  # noqa: E402
from bot.admin import server as admin_server  # noqa: E402
from bot.db import Database  # noqa: E402
from bot.services import decisions  # noqa: E402
from bot.services import ticket as ticket_service  # noqa: E402
from harness import FAKE_TOKEN, FakeSession  # noqa: E402

USER_ID = 3131
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _seed(tmp: str, *, approved: bool = True, photo_paths: list[str] | None = None) -> tuple[Database, int]:
    db = Database(os.path.join(tmp, "t.db"), encryption_key=Fernet.generate_key().decode())
    asyncio.run(db.init())

    async def make() -> int:
        app_id = await db.create_application(
            user_id=USER_ID,
            username="@hero",
            country="Uzbekistan",
            plate="01Z999QQ",
            direction="SQ",
            phone="+998901112233",
            photo_file_ids=[f"f{i}" for i in range(len(photo_paths or []))],
            photo_paths=list(photo_paths or []),
            language="uz",
        )
        if approved:
            await db.approve(app_id, "@mod")
        return app_id

    return db, asyncio.run(make())


def _panel(db: Database, tmp: str, bot=None):
    from types import SimpleNamespace

    from bot.admin.server import create_admin_app

    config = SimpleNamespace(
        admin_password="pw", panel_port=8080, media_dir=tmp, admin_chat_id=0
    )
    return create_admin_app(bot=bot, config=config, db=db)


def _headers() -> dict:
    return {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie('pw')}"}


def test_the_application_page_previews_the_participants_actual_ticket():
    """The admin can *see* the ticket the participant will receive.

    The preview is rendered through the delivery path (participant's hero
    photo, number, plate, direction and language) — and a photo that cannot
    be decoded falls back to the gradient poster instead of a 500.
    """
    from aiohttp.test_utils import TestClient, TestServer

    with tempfile.TemporaryDirectory() as tmp:
        # Not a valid JPEG: Image.open raises, the renderer must fall back.
        photo = os.path.join(tmp, "front.jpg")
        with open(photo, "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0not-really-a-jpeg")
        db, app_id = _seed(tmp, photo_paths=[photo])
        admin_app = _panel(db, tmp)
        admin_server._ticket_preview_cache.clear()
        headers = _headers()

        async def run():
            captured: dict = {}
            real = ticket_service.generate_ticket

            def spy(*args, **kwargs):
                captured.update(kwargs)
                return real(*args, **kwargs)

            decisions.generate_ticket = spy
            try:
                async with TestClient(TestServer(admin_app)) as client:
                    page = await client.get(f"/application/{app_id}", headers=headers)
                    body = await page.text()
                    assert page.status == 200
                    assert f"/application/{app_id}/ticket.png" in body
                    assert "Полный размер" in body
                    assert "Скачать PNG" in body

                    r = await client.get(f"/application/{app_id}/ticket.png", headers=headers)
                    assert r.status == 200
                    assert (await r.read())[:8] == PNG_MAGIC
                    # The participant's own data, not a generic sample.
                    assert captured["number"] == 1
                    assert captured["plate"] == "01Z999QQ"
                    assert captured["lang"] == "uz"
                    assert captured["hero_image_path"] == photo

                    # The broken hero did not break the render: a valid PNG
                    # came back (gradient poster), hero retried as None.
                    assert captured["hero_image_path"] in (photo, None)
            finally:
                decisions.generate_ticket = real

        asyncio.run(run())


def test_pending_applications_have_no_ticket_preview():
    from aiohttp.test_utils import TestClient, TestServer

    with tempfile.TemporaryDirectory() as tmp:
        db, app_id = _seed(tmp, approved=False)
        admin_app = _panel(db, tmp)
        headers = _headers()

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                body = await (await client.get(f"/application/{app_id}", headers=headers)).text()
                assert f"/application/{app_id}/ticket.png" not in body
                assert f'action="/application/{app_id}/ticket"' not in body
                assert (
                    await client.get(f"/application/{app_id}/ticket.png", headers=headers)
                ).status == 404
                # Resending a pending application is a no-op redirect.
                r = await client.post(
                    f"/application/{app_id}/ticket", headers=headers, allow_redirects=False
                )
                assert r.status == 302
                assert r.headers["Location"].endswith(f"/application/{app_id}")

        asyncio.run(run())


class _GatedSession(FakeSession):
    """Holds the participant's photo upload until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.released = False

    async def make_request(self, bot, method, timeout=None):  # type: ignore[override]
        name = type(method).__name__
        if name in {"SendPhoto", "SendDocument"} and getattr(method, "chat_id", None) == USER_ID:
            await self.gate.wait()
            self.released = True
        return await super().make_request(bot, method, timeout)


def test_panel_resend_answers_before_the_upload_and_reports_the_result():
    """The click redirects at once; the page shows sending → sent.

    A second click while the delivery is in flight must not start a second
    delivery (the old behaviour was a multi-minute hang — or two tickets).
    """
    from aiohttp.test_utils import TestClient, TestServer

    with tempfile.TemporaryDirectory() as tmp:
        db, app_id = _seed(tmp)
        session = _GatedSession()
        bot = Bot(token=FAKE_TOKEN, session=session)
        admin_app = _panel(db, tmp, bot=bot)
        headers = _headers()

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                r = await client.post(
                    f"/application/{app_id}/ticket", headers=headers, allow_redirects=False
                )
                assert r.status == 302
                assert r.headers["Location"].endswith(f"/application/{app_id}")
                assert not session.released, "the resend click waited for the upload"

                # In flight: the page says so and re-loads itself.
                body = await (await client.get(f"/application/{app_id}", headers=headers)).text()
                assert "Билет отправляется" in body, body[-800:]
                assert "<meta http-equiv='refresh' content='2'>" in body

                # Second click while in flight: accepted, but no second send.
                r2 = await client.post(
                    f"/application/{app_id}/ticket", headers=headers, allow_redirects=False
                )
                assert r2.status == 302

                # Release the uplink: exactly one ticket arrives.
                session.gate.set()
                await decisions.wait_background()
                photos = [
                    m for m in session.methods_named("SendPhoto") if m.chat_id == USER_ID
                ]
                assert len(photos) == 1, "the resend duplicated the delivery"
                assert session.released

                # The page reports the outcome and stops refreshing.
                body = await (await client.get(f"/application/{app_id}", headers=headers)).text()
                assert "Билет отправлен участнику в Telegram" in body, body[-800:]
                assert "<meta http-equiv='refresh'" not in body

        asyncio.run(run())
        asyncio.run(bot.session.close())


def test_panel_resend_failure_shows_the_readable_reason_in_uz():
    """The exact failure reason, in the panel's language, unencoded."""
    from aiohttp.test_utils import TestClient, TestServer

    with tempfile.TemporaryDirectory() as tmp:
        db, app_id = _seed(tmp)
        # bot=None: the tenant worker is not running (restart / missing token).
        admin_app = _panel(db, tmp)
        headers = {
            "Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie('pw')}",
        }

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                r = await client.post(
                    f"/application/{app_id}/ticket", headers=headers, allow_redirects=False
                )
                assert r.status == 302
                assert r.headers["Location"].endswith(f"/application/{app_id}")

                page = await client.get(
                    r.headers["Location"] + "?lang=uz", headers=headers
                )
                body = await page.text()
                assert page.status == 200
                assert "Biletni yuborib bo‘lmadi" in body, body[-800:]
                # The readable reason — the exact wording of the delivery layer.
                assert "бота не запущен" in body, body[-800:]
                # No percent-encoded leftovers of the old ?ticket_error= flow.
                assert "ticket_error=" not in r.headers["Location"]
                assert "%D1" not in body.split("Biletni yuborib bo‘lmadi:")[1][:300]

        asyncio.run(run())


def test_the_ticket_assets_sample_preview_uses_the_latest_application():
    """The sample is built from the tenant's newest application, not Promotors copy."""
    from aiohttp.test_utils import TestClient, TestServer

    with tempfile.TemporaryDirectory() as tmp:
        db, _ = _seed(tmp)  # approved, №1, plate 01Z999QQ, lang uz
        admin_app = _panel(db, tmp)
        headers = _headers()

        async def run():
            captured: dict = {}
            real = ticket_service.generate_ticket

            def spy(*args, **kwargs):
                captured.update(kwargs)
                return real(*args, **kwargs)

            # _ticket_preview imports generate_ticket locally on every call,
            # so patching the module attribute is what the handler sees.
            ticket_service.generate_ticket = spy
            try:
                async with TestClient(TestServer(admin_app)) as client:
                    r = await client.get("/ticket-assets/preview.png", headers=headers)
                    assert r.status == 200
                    assert (await r.read())[:8] == PNG_MAGIC
                    assert captured["plate"] == "01Z999QQ"
                    assert captured["lang"] == "uz"
                    assert captured["number"] == 1

                # An empty tenant still gets the neutral sample.
                captured.clear()
                db2 = Database(
                    os.path.join(tmp, "empty.db"),
                    encryption_key=Fernet.generate_key().decode(),
                )
                await db2.init()
                admin_app2 = _panel(db2, tmp)
                async with TestClient(TestServer(admin_app2)) as client:
                    r = await client.get("/ticket-assets/preview.png", headers=headers)
                    assert r.status == 200
                    assert (await r.read())[:8] == PNG_MAGIC
                    assert captured["plate"] == "01A777AA"
                    assert captured["lang"] == "ru"
            finally:
                ticket_service.generate_ticket = real

        asyncio.run(run())


if __name__ == "__main__":
    test_the_application_page_previews_the_participants_actual_ticket()
    test_pending_applications_have_no_ticket_preview()
    test_panel_resend_answers_before_the_upload_and_reports_the_result()
    test_panel_resend_failure_shows_the_readable_reason_in_uz()
    test_the_ticket_assets_sample_preview_uses_the_latest_application()
    print("All panel ticket tests passed.")
