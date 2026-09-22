"""The personal ticket must reach the participant — or the team must know.

The client reported "Не пришла сгенерированная картинка после одобрения": the
approval text arrived, the ticket did not, and nothing in the chat said so.
These tests pin the layered delivery (PNG → JPEG → file → moderation-chat
fallback), the ``/ticket`` resend command and the panel's resend button.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from aiogram.exceptions import TelegramBadRequest  # noqa: E402

from harness import BotHarness  # noqa: E402

USER = 4242
MODERATOR = 777
OTHER_USER = 5151

JPEG_MAGIC = b"\xff\xd8\xff"


async def _register(harness: BotHarness, user: int = USER) -> int:
    await harness.send_command(user, "/start")
    await harness.tap(user, "lang:ru")
    await harness.tap(user, "country:1")
    await harness.send_text(user, "01A123BC")
    for method in reversed(harness.session.methods):
        if type(method).__name__ != "SendMessage":
            continue
        markup = getattr(method, "reply_markup", None)
        if markup is None or not getattr(markup, "inline_keyboard", None):
            continue
        for row in markup.inline_keyboard:
            for button in row:
                if button.text == "SQ - Качество звучания":
                    await harness.tap(user, button.callback_data, text="directions")
                    break
            else:
                continue
            break
        else:
            continue
        break
    for index in range(4):
        await harness.send_photo(user, f"side-{index}")
    await harness.tap(user, "modsdone", text="mods")
    await harness.send_contact(user)
    app = (await harness.db.for_tenant(harness.tenant.id).list_applications())[0]
    return app.id


def test_approval_sends_the_ticket_and_reports_the_photo_send():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            app_id = await _register(harness)
            await harness.tap(MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id)

            to_user = [
                m
                for m in harness.session.methods_named("SendPhoto")
                if getattr(m, "chat_id", None) == USER
            ]
            assert to_user, "the ticket photo was not sent to the participant"
            # A PNG poster, followed by the share hint.
            assert harness.private_texts(USER)[-1].startswith("📸")
        finally:
            await harness.stop()

    asyncio.run(run())


def test_rejected_png_falls_back_to_a_jpeg_photo():
    """Telegram refusing the poster (size/dimensions) must not lose the ticket."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            app_id = await _register(harness)
            harness.session.fail_next(
                "SendPhoto",
                TelegramBadRequest(method=None, message="PHOTO_INVALID_DIMENSIONS"),
            )
            await harness.tap(MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id)

            sends = [
                m
                for m in harness.session.methods_named("SendPhoto")
                if getattr(m, "chat_id", None) == USER
            ]
            assert len(sends) == 2, [m.photo.filename for m in sends]
            assert sends[0].photo.filename.endswith(".png")
            # …retried as a compact JPEG instead of giving up.
            assert sends[1].photo.filename.endswith(".jpg"), sends[1].photo.filename
            assert bytes(sends[1].photo.data)[:3] == JPEG_MAGIC
            assert not [
                m
                for m in harness.session.methods_named("SendDocument")
                if getattr(m, "chat_id", None) == USER
            ]
            # The participant got the "share to Stories" hint after the ticket.
            assert harness.private_texts(USER)[-1].startswith("📸")
        finally:
            await harness.stop()

    asyncio.run(run())


def test_undeliverable_ticket_is_forwarded_to_the_moderation_chat():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            app_id = await _register(harness)
            error = TelegramBadRequest(method=None, message="photo is too big")
            harness.session.fail_for_chat("SendPhoto", USER, error)
            harness.session.fail_for_chat("SendDocument", USER, error)

            await harness.tap(MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id)

            group_photos = [
                m
                for m in harness.session.methods_named("SendPhoto")
                if getattr(m, "chat_id", None) == harness.admin_chat_id
            ]
            captions = [m.caption or "" for m in group_photos]
            assert any("Перешлите" in c for c in captions), captions
        finally:
            await harness.stop()

    asyncio.run(run())


def test_ticket_command_resends_on_demand():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            app_id = await _register(harness)
            await harness.tap(MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id)

            harness.session.methods.clear()
            await harness.send_text(
                MODERATOR, f"/ticket {app_id}", chat_id=harness.admin_chat_id
            )
            replies = [
                m.text
                for m in harness.session.methods_named("SendMessage")
                if getattr(m, "chat_id", None) == harness.admin_chat_id
            ]
            assert any("Билет отправлен участнику" in r for r in replies), replies
            assert [
                m
                for m in harness.session.methods_named("SendPhoto")
                if getattr(m, "chat_id", None) == USER
            ], "the resent ticket never reached the participant"

            # A pending application has no ticket yet — the chat says why.
            harness.session.methods.clear()
            await harness.send_text(MODERATOR, "/ticket 999", chat_id=harness.admin_chat_id)
            replies = [
                m.text
                for m in harness.session.methods_named("SendMessage")
                if getattr(m, "chat_id", None) == harness.admin_chat_id
            ]
            assert any("не найдена" in r for r in replies), replies

            harness.session.methods.clear()
            await harness.send_text(MODERATOR, "/ticket", chat_id=harness.admin_chat_id)
            replies = [
                m.text
                for m in harness.session.methods_named("SendMessage")
                if getattr(m, "chat_id", None) == harness.admin_chat_id
            ]
            assert any("/ticket" in r for r in replies), replies
        finally:
            await harness.stop()

    asyncio.run(run())


def test_ticket_command_is_silent_for_non_admins():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            app_id = await _register(harness)
            await harness.tap(MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id)

            harness.session.methods.clear()
            await harness.send_text(OTHER_USER, f"/ticket {app_id}")
            assert harness.session.methods_named("SendMessage") == []
        finally:
            await harness.stop()

    asyncio.run(run())


def test_panel_has_a_resend_button_for_approved_applications():
    """The team can resend the ticket from the panel, without a developer."""
    import tempfile
    from types import SimpleNamespace

    from aiohttp.test_utils import TestClient, TestServer
    from cryptography.fernet import Fernet

    from aiogram import Bot

    from bot.admin import auth
    from bot.admin.server import create_admin_app
    from bot.db import Database
    from harness import FAKE_TOKEN, FakeSession

    password = "panel-pass"
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(
            os.path.join(tmp, "panel.db"), encryption_key=Fernet.generate_key().decode()
        )
        asyncio.run(db.init())

        async def seed() -> tuple[int, int]:
            approved = await db.create_application(
                user_id=7,
                username="@tester",
                country="Узбекистан",
                plate="01A777AA",
                direction="SQ",
                phone="+998901112233",
                photo_file_ids=[],
                photo_paths=[],
            )
            await db.approve(approved, "@mod")
            pending = await db.create_application(
                user_id=8,
                username="@pending",
                country="Узбекистан",
                plate="01A888BB",
                direction="SQ",
                phone="+998901112244",
                photo_file_ids=[],
                photo_paths=[],
            )
            return approved, pending

        approved_id, pending_id = asyncio.run(seed())
        session = FakeSession()
        bot = Bot(token=FAKE_TOKEN, session=session)
        config = SimpleNamespace(
            admin_password=password,
            panel_port=8080,
            db_path=os.path.join(tmp, "panel.db"),
            media_dir=tmp,
        )
        admin_app = create_admin_app(bot=bot, config=config, db=db)
        headers = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(password)}"}

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                page = await client.get(f"/application/{approved_id}", headers=headers)
                body = await page.text()
                assert page.status == 200
                assert f'action="/application/{approved_id}/ticket"' in body
                assert "Отправить билет повторно" in body

                response = await client.post(
                    f"/application/{approved_id}/ticket",
                    headers=headers,
                    allow_redirects=False,
                )
                assert response.status == 302
                assert response.headers["Location"].endswith("?ticket=sent")
                assert [
                    m for m in session.methods_named("SendPhoto") if m.chat_id == 7
                ], "the participant did not receive the resent ticket"

                # A pending application has no ticket yet: no button at all.
                pending_page = await client.get(
                    f"/application/{pending_id}", headers=headers
                )
                assert f'action="/application/{pending_id}/ticket"' not in (
                    await pending_page.text()
                )

        asyncio.run(run())
        asyncio.run(bot.session.close())


def test_resend_button_explains_itself_when_the_bot_worker_is_down():
    """A panel click must never fail silently (the worker may be restarting)."""
    import tempfile
    from types import SimpleNamespace
    from urllib.parse import quote

    from aiohttp.test_utils import TestClient, TestServer
    from cryptography.fernet import Fernet

    from bot.admin import auth
    from bot.admin.server import create_admin_app
    from bot.db import Database

    password = "panel-pass"
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(
            os.path.join(tmp, "down.db"), encryption_key=Fernet.generate_key().decode()
        )
        asyncio.run(db.init())

        async def seed() -> int:
            app_id = await db.create_application(
                user_id=9,
                username="@tester",
                country="Узбекистан",
                plate="01A999AA",
                direction="SQ",
                phone="+998901112255",
                photo_file_ids=[],
                photo_paths=[],
            )
            await db.approve(app_id, "@mod")
            return app_id

        app_id = asyncio.run(seed())
        # bot is None: this is what the panel sees while a tenant worker restarts.
        admin_app = create_admin_app(
            bot=None,
            config=SimpleNamespace(
                admin_password=password,
                panel_port=8080,
                db_path=os.path.join(tmp, "down.db"),
                media_dir=tmp,
            ),
            db=db,
        )
        headers = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(password)}"}

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                response = await client.post(
                    f"/application/{app_id}/ticket", headers=headers, allow_redirects=False
                )
                assert response.status == 302
                location = response.headers["Location"]
                assert "ticket=failed" in location, location

                page = await client.get(location, headers=headers)
                body = await page.text()
                assert page.status == 200
                assert "Не удалось отправить билет" in body, body[-500:]

        asyncio.run(run())


def test_the_diag_self_test_ticket_shows_the_tenants_own_schedule():
    """`/diag` renders a sample ticket — it must not show another event's dates.

    The self-test is how the team checks "will the poster come out right?", so a
    September/SOF EXPO sample on a Tashkent October event reads as a wrong-date
    bug.  It now renders through the same tenant-branded path as the real one.
    """

    async def run():
        from bot.handlers import moderation
        from bot.services import ticket as ticket_service

        harness = BotHarness()
        await harness.start()
        try:
            await _register(harness)
            harness.session.methods.clear()

            real = moderation.generate_ticket
            captured: dict = {}

            def spy(*args, **kwargs):
                captured.update(kwargs)
                return real(*args, **kwargs)

            moderation.generate_ticket = spy
            try:
                await harness.send_text(
                    MODERATOR, "/diag", chat_id=harness.admin_chat_id
                )
            finally:
                moderation.generate_ticket = real

            assert captured, "the self-test never rendered a ticket"
            config = captured.get("tenant_config")
            assert config is not None, "the self-test rendered without the tenant"

            copy = ticket_service._resolve_ticket_copy(
                "ru", config, ticket_service._COPY["ru"]
            )
            assert "сентябр" not in copy["date"].lower(), copy
            assert "SOF EXPO" not in copy["place"].upper(), copy
            assert "октябр" in copy["date"].lower(), copy

            assert [
                m
                for m in harness.session.methods_named("SendPhoto")
                if getattr(m, "chat_id", None) == harness.admin_chat_id
            ], "the self-test ticket never reached the moderation chat"
        finally:
            await harness.stop()

    asyncio.run(run())
