"""Accepting from the group: answered in a second, ticket delivered behind it.

The reported sequence was:

* «judayam sekin ishlayapti» — Accept stayed spinning for a long time;
* a second tap answered «эта заявка уже обработана»;
* «foydalanuvchiga bilet berilmayapti» — the participant never got a ticket.

Measured cause (``diagnostics/perf_accept.py``): the whole delivery was awaited
*inside* the callback handler — number, notification, render, a 2.97 MB upload,
share hint — and only then ``query.answer``.  With a realistic phone photo the
tap was answered 6.63 s later (and on the event's uplink much later), so the
moderator tapped again, and the only thing they saw was "already processed".

These tests pin the new ordering:

* the decision (one SQLite write) is what is awaited;
* the card is marked and the tap is answered *before* the ticket upload;
* the delivery finishes in the background and the card then says
  «🎫 Билет отправлен участнику» — or the exact failure with the ``/ticket`` id;
* a real second tap names the decision («Уже принята — №1 (@mod)»).
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

from bot.db import Database  # noqa: E402
from bot.services import decisions  # noqa: E402
from harness import FAKE_TOKEN, BotHarness, FakeSession  # noqa: E402

USER = 4242
MODERATOR = 777
JPEG_MAGIC = b"\xff\xd8\xff"


class GatedSession(FakeSession):
    """Records Telegram calls, holding the participant's photo until released.

    The gate is what makes the ordering observable: the ticket upload is inside
    the background delivery, so while it is held the test can prove that the
    moderator's tap was already answered and the card already updated.
    """

    def __init__(self) -> None:
        super().__init__()
        self.gate = asyncio.Event()
        self.released = False

    async def make_request(self, bot: Bot, method, timeout=None):  # type: ignore[override]
        name = type(method).__name__
        if (
            name in {"SendPhoto", "SendDocument"}
            and getattr(method, "chat_id", None) == USER
        ):
            await self.gate.wait()
            self.released = True
        return await super().make_request(bot, method, timeout)


async def _harness(session: FakeSession | None = None) -> BotHarness:
    harness = BotHarness()
    if session is not None:
        # The harness builds its Bot in __init__, so both have to be replaced
        # together before the dispatcher starts using them.
        harness.session = session
        harness.bot = Bot(token=FAKE_TOKEN, session=session)
    await harness.start()
    return harness


def _direction_rows(harness: BotHarness) -> list[list]:
    """The last inline direction keyboard the bot sent to the participant."""
    for method in reversed(harness.session.methods):
        if type(method).__name__ != "SendMessage":
            continue
        markup = getattr(method, "reply_markup", None)
        rows = getattr(markup, "inline_keyboard", None)
        if rows:
            return rows
    return []


async def _register(harness: BotHarness, user: int = USER, language: str = "ru") -> int:
    await harness.send_command(user, "/start")
    await harness.tap(user, f"lang:{language}")
    await harness.tap(user, "country:1")
    await harness.send_text(user, "01A123BC")
    for row in _direction_rows(harness):
        for button in row:
            if button.text == "SQ - Качество звучания":
                await harness.tap(user, button.callback_data, text="directions")
                break
        else:
            continue
        break
    await harness.tap(user, "dirdone", text="directions")
    for index in range(4):
        await harness.send_photo(user, f"side-{index}")
    await harness.tap(user, "modsdone", text="mods")
    await harness.send_contact(user)
    app = (await harness.db.for_tenant(harness.tenant.id).list_applications())[0]
    return app.id


def _card_edits(harness: BotHarness) -> list[str]:
    return [
        m.text or ""
        for m in harness.session.methods_named("EditMessageText")
        if getattr(m, "chat_id", None) == harness.admin_chat_id
    ]


def test_the_tap_is_answered_before_the_ticket_is_uploaded():
    async def run():
        session = GatedSession()
        harness = await _harness(session)
        try:
            app_id = await _register(harness)
            # Fresh recording: the registration's own photos are not the subject.
            session.methods.clear()

            await harness.tap(
                MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id, settle=False
            )
            # Let the handler and the spawned delivery run up to the held upload.
            for _ in range(5):
                await asyncio.sleep(0.01)

            # The decision is stored...
            stored = await harness.db.for_tenant(harness.tenant.id).get_application(app_id)
            assert stored.status == "approved" and stored.reg_number == 1
            # ...the moderator already has their answer...
            answers = harness.session.methods_named("AnswerCallbackQuery")
            assert answers, "the moderator's tap was never answered"
            assert "№1" in (answers[-1].text or "")
            # ...the card already says what is happening...
            edits = _card_edits(harness)
            assert edits, "the moderation card was not updated"
            assert "Принято" in edits[-1] and "№1" in edits[-1], edits[-1]
            assert "Билет отправляется" in edits[-1], edits[-1]
            # ...and the upload is still in flight (the gate is closed).
            assert not session.released, "the ticket upload ran inside the handler"
            assert not [
                m for m in harness.session.methods_named("SendPhoto")
                if getattr(m, "chat_id", None) == USER
            ]

            # Release the uplink: the delivery finishes and reports on the card.
            session.gate.set()
            await harness.settle()
            photos = [
                m for m in harness.session.methods_named("SendPhoto")
                if getattr(m, "chat_id", None) == USER
            ]
            assert photos, "the participant never received the ticket"
            final = _card_edits(harness)[-1]
            assert "Билет отправлен участнику" in final, final
        finally:
            session.gate.set()
            await harness.stop()

    asyncio.run(run())


def test_a_second_tap_names_the_decision_that_is_already_there():
    async def run():
        harness = await _harness()
        try:
            app_id = await _register(harness)
            await harness.tap(MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id)

            # The moderator taps again (a slow connection, two people watching).
            await harness.tap(
                MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id, settle=False
            )
            answer = harness.session.methods_named("AnswerCallbackQuery")[-1]
            assert answer.show_alert is True
            assert "Уже принята" in (answer.text or ""), answer.text
            assert "№1" in (answer.text or ""), answer.text

            # Rejecting an approved application says the same thing, the other way.
            await harness.tap(
                MODERATOR, f"reject:{app_id}", chat_id=harness.admin_chat_id, settle=False
            )
            answer = harness.session.methods_named("AnswerCallbackQuery")[-1]
            assert "Уже принята" in (answer.text or ""), answer.text
        finally:
            await harness.stop()

    asyncio.run(run())


def test_a_multimegabyte_ticket_is_uploaded_as_a_jpeg():
    """A 2.77 MB PNG poster is re-encoded, so the upload is seconds not minutes.

    Telegram re-encodes every photo it accepts, so the JPEG is what the
    participant would have stored anyway — only the upload is several times
    shorter (measured: 2.77 MB → 0.5 MB on a realistic hero photo).
    """
    from PIL import Image

    async def run():
        harness = await _harness()
        try:
            app_id = await _register(harness)
            # A poster as heavy as the real one: photographic noise is what makes
            # the PNG big (flat test images compress to ~99 KB).
            noise = Image.frombytes("RGB", (1080, 1920), os.urandom(1080 * 1920 * 3))
            buffer = __import__("io").BytesIO()
            noise.save(buffer, format="PNG")
            big_png = buffer.getvalue()
            assert len(big_png) > decisions._JPEG_FIRST_BYTES, len(big_png)

            real = decisions.generate_ticket
            decisions.generate_ticket = lambda *a, **k: big_png
            try:
                await harness.tap(
                    MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id
                )
            finally:
                decisions.generate_ticket = real

            photos = [
                m for m in harness.session.methods_named("SendPhoto")
                if getattr(m, "chat_id", None) == USER
            ]
            assert photos, "the participant never received the ticket"
            filename = photos[0].photo.filename
            assert filename.endswith(".jpg"), filename
            data = bytes(photos[0].photo.data)
            assert data[:3] == JPEG_MAGIC, data[:3]
            assert len(data) < len(big_png), (len(data), len(big_png))
            # The share hint follows the ticket, as before.
            assert harness.private_texts(USER)[-1].startswith("📸")
        finally:
            await harness.stop()

    asyncio.run(run())


def test_an_undelivered_ticket_is_reported_on_the_card():
    """The card carries the failure and the way to fix it, not just a log line."""
    from aiogram.exceptions import TelegramBadRequest

    async def run():
        harness = await _harness()
        try:
            app_id = await _register(harness)
            error = TelegramBadRequest(method=None, message="photo is too big")
            harness.session.fail_for_chat("SendPhoto", USER, error)
            harness.session.fail_for_chat("SendDocument", USER, error)
            await harness.tap(MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id)

            final = _card_edits(harness)[-1]
            assert "Билет НЕ ушёл" in final, final
            assert f"/ticket {app_id}" in final, final
        finally:
            await harness.stop()

    asyncio.run(run())


def test_a_hand_typed_spl_date_is_never_rewritten_by_a_restart():
    """A date typed in the panel survives every redeploy — even «3 октября».

    The old startup «correction» rewrote any arrival naming the 3rd to the
    2nd on every boot, so the team could not fix the approval text.
    """

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(
                os.path.join(tmp, "dates.db"), encryption_key=Fernet.generate_key().decode()
            )
            await db.init()
            spl = await db.create_tenant(
                slug="splshow",
                name="SPL Show",
                bot_token="123:abc",
                admin_chat_id=-100,
                admin_password="pw",
                event_date_text_ru="3 октября 2026 с 18:00",
                event_date_text_uz="3-oktyabr 2026, soat 18:00 dan",
            )
            for _ in range(2):
                await db.init()
            spl = await db.get_tenant(spl.id)
            assert spl.event_date_text_ru == "3 октября 2026 с 18:00"
            assert spl.event_date_text_uz == "3-oktyabr 2026, soat 18:00 dan"

            await db.update_tenant(spl.id, event_date_text_ru="01 октября 2026 с 10:00")
            await db.init()
            spl = await db.get_tenant(spl.id)
            assert spl.event_date_text_ru == "01 октября 2026 с 10:00"

    asyncio.run(run())


def test_a_hand_named_spl_tenant_is_recognised_too():
    """«SPL Show 2026» / ``spl-show-tashkent`` are the same event: seed them too."""
    from bot.db import is_spl_tenant

    assert is_spl_tenant("splshow", "SPL Show")
    assert is_spl_tenant("spl-show-tashkent", "SPL Show 2026")
    assert is_spl_tenant("spl_show", "SPL SHOW")
    assert not is_spl_tenant("drift2026", "Adrenaline Drift")
    assert not is_spl_tenant("promotors", "Promotors Show")


def test_the_pattern_matches_the_usual_spellings():
    from bot.db import mentions_third_of_october

    for text in (
        "3 октября с 12:00",
        "03 октября 2026 с 09:00",
        "3-oktyabr 2026, soat 18:00 dan",
        "03.10.2026 с 17:00",
        "2-3 октября 2026",
    ):
        assert mentions_third_of_october(text), text
    for text in ("2 октября 2026 с 17:00 до 22:00", "02-oktyabr 2026", "13 октября", "23 октября"):
        assert not mentions_third_of_october(text), text


def test_the_panel_accept_redirects_without_waiting_for_the_upload():
    """A panel click must not sit on the ticket upload (it used to: 30+ seconds).

    The request answers as soon as the decision is stored; the participant's
    notification, the ticket and the sheet append run in a background task that
    survives the response.
    """
    from types import SimpleNamespace

    from aiohttp.test_utils import TestClient, TestServer

    from bot.admin import auth
    from bot.admin.server import create_admin_app

    async def run():
        session = GatedSession()
        bot = Bot(token=FAKE_TOKEN, session=session)
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(
                os.path.join(tmp, "panel.db"), encryption_key=Fernet.generate_key().decode()
            )
            await db.init()
            app_id = await db.create_application(
                user_id=USER,
                username="@tester",
                country="Узбекистан",
                plate="01A123BC",
                direction="SQ",
                phone="+998901112233",
                photo_file_ids=[],
                photo_paths=[],
            )
            config = SimpleNamespace(
                admin_password="pw",
                panel_port=8080,
                db_path=os.path.join(tmp, "panel.db"),
                media_dir=tmp,
                admin_chat_id=0,
            )
            admin_app = create_admin_app(bot=bot, config=config, db=db)
            headers = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie('pw')}"}

            async with TestClient(TestServer(admin_app)) as client:
                response = await client.post(
                    f"/application/{app_id}/approve", headers=headers, allow_redirects=False
                )
                assert response.status == 302, response.status
                assert response.headers["Location"].endswith(f"/application/{app_id}")
                stored = await db.get_application(app_id)
                assert stored.status == "approved" and stored.reg_number == 1
                assert not session.released, "the panel waited for the ticket upload"

                session.gate.set()
                await decisions.wait_background()
                assert session.released, "the ticket was never delivered after the redirect"
            await bot.session.close()

    asyncio.run(run())
