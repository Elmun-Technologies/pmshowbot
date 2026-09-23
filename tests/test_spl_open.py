"""SPL Show must accept registrations even if REGISTRATION_CLOSED is set.

The September Promotors close was a process-wide env flag. After the
multi-tenant split that flag made every bot, including SPL Show, answer
«регистрация завершена». Registration is now per tenant and open by default.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet  # noqa: E402

from bot.config import Config  # noqa: E402
from bot.db import SPL_EVENT_COPY, Database  # noqa: E402
from bot.texts import approved_for_tenant, rejected_for_tenant  # noqa: E402


def _config(path: str, key: str, *, closed: bool) -> Config:
    return Config(
        super_admin_password="super-pw",
        encryption_key=key,
        google_credentials_file="",
        db_path=path,
        media_dir=os.path.dirname(path),
        require_subscription=False,
        registration_closed=closed,
        panel_port=8080,
        admin_user_ids=frozenset(),
    )


def test_process_flag_does_not_close_any_tenant():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "open.db")
            key = Fernet.generate_key().decode()
            config = _config(path, key, closed=True)
            db = Database(path, encryption_key=key, bootstrap=config)
            await db.init()
            promotors = await db.get_tenant("promotors")
            spl = await db.create_tenant(slug="splshow", name="SPL Show", bot_token="spl-token")
            assert promotors.registration_closed is False
            assert spl.registration_closed is False
            assert config.tenant_config(promotors, bot_token="x").registration_closed is False
            assert config.tenant_config(spl, bot_token="x").registration_closed is False
            # An admin can still close one tenant without touching the other.
            await db.update_tenant(promotors.id, registration_closed=True)
            promotors = await db.get_tenant("promotors")
            spl = await db.get_tenant("splshow")
            assert config.tenant_config(promotors).registration_closed is True
            assert config.tenant_config(spl).registration_closed is False

    asyncio.run(run())


def test_spl_schedule_is_not_invented_and_old_seeds_are_cleared_once():
    """The SPL date/time comes from the panel only.

    Earlier builds re-seeded the arrival date and note on every start, which is
    how the approval kept arriving with the wrong date and time even after the
    team corrected it.  Now a new tenant gets the venue only, and a one-time
    migration clears values an earlier build wrote (hand-typed ones stay).
    """
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spl.db")
            key = Fernet.generate_key().decode()
            db = Database(path, encryption_key=key)
            await db.init()
            created = await db.create_tenant(slug="splshow", name="Spl Show")
            assert created.name == "SPL Show"
            assert created.event_venue_text_ru == "Tashkent INDEX"
            assert created.event_date_text_ru == ""
            assert created.event_note_text_ru == ""
            assert created.event_guest_date_text_ru == ""
            assert created.registration_closed is False

            # Old seeds already stored, plus one custom note.
            await db.update_tenant(
                "splshow",
                event_date_text_ru="02 октября 2026 с 17:00 до 22:00",
                event_date_text_uz="11-sentyabr 2026, 10:00 dan 19:00 gacha",
                event_guest_date_text_ru="12 и 13 сентября с 10:00",
                event_guest_date_text_uz="12 va 13-sentyabr, 10:00 dan",
                event_note_text_ru="custom note",
            )
            await db.init()
            refreshed = await db.get_tenant("splshow")
            assert refreshed.event_date_text_ru == ""
            assert refreshed.event_date_text_uz == ""
            assert refreshed.event_guest_date_text_ru == ""
            assert refreshed.event_guest_date_text_uz == ""
            assert refreshed.event_note_text_ru == "custom note"
            assert refreshed.event_venue_text_ru == "Tashkent INDEX"

            # The migration runs once: what the team types afterwards stays,
            # even if it happens to equal an old seed.
            await db.update_tenant(
                "splshow", event_date_text_ru="02 октября 2026 с 17:00 до 22:00"
            )
            await db.init()
            again = await db.get_tenant("splshow")
            assert again.event_date_text_ru == "02 октября 2026 с 17:00 до 22:00"

            # Promotors keeps its own September copy.
            promotors = await db.get_tenant("promotors")
            assert "сентября" in promotors.event_date_text_ru
            assert promotors.event_venue_text_ru == "SOF EXPO"

    asyncio.run(run())


def test_approved_message_carries_exactly_the_panel_schedule():
    tenant = type(
        "T",
        (),
        {
            "tenant_name": "SPL Show",
            "channel_url": "https://t.me/splshow",
            **SPL_EVENT_COPY,
        },
    )()
    ru = approved_for_tenant("ru", tenant, 7)
    assert "№7" in ru
    # Nothing typed in the panel -> no date or time is invented.
    assert "Заезд участников" not in ru
    assert "октябр" not in ru and "09:00" not in ru

    typed = type(
        "T",
        (),
        {
            "tenant_name": "SPL Show",
            "channel_url": "https://t.me/splshow",
            "event_date_text_ru": "2 октября с 18:00",
            "event_date_text_uz": "2-oktyabr soat 18:00 dan",
            "event_note_text_ru": "С 08:00 будьте у машин.",
        },
    )()
    ru = approved_for_tenant("ru", typed, 7)
    uz = approved_for_tenant("uz", typed, 7)
    assert "Заезд участников — <b>2 октября с 18:00</b>" in ru
    assert "С 08:00 будьте у машин." in ru
    assert "2-oktyabr soat 18:00 dan" in uz
    assert "17:00" not in ru and "22:00" not in ru
    # Empty note must not add a blank operational paragraph for other tenants.
    plain = approved_for_tenant(
        "ru",
        type("T", (), {"tenant_name": "Promotors Show", "event_date_text_ru": "11 сентября"})(),
        1,
    )
    assert "Заезд участников — " in plain
    assert "09:00" not in plain


def test_rejection_message_has_no_event_time():
    """«При отклонении заявки приходит сообщение с неправильным временем».

    The client's rule: the show is for registered participants only
    ("hozircha faqat uchastniklar uchun"), so the rejection must not invite the
    person as a guest — and must not print any date, time or venue that could
    be wrong.
    """
    tenant = type(
        "T",
        (),
        {
            "tenant_name": "SPL Show",
            "channel_url": "https://t.me/splshow",
            **SPL_EVENT_COPY,
        },
    )()

    ru = rejected_for_tenant("ru", tenant)
    uz = rejected_for_tenant("uz", tenant)
    assert "не прошли регистрацию" in ru
    assert "ro‘yxatdan o‘tmadingiz" in uz
    assert "ro‘yxatdan o‘tgan ishtirokchilar" in uz

    for text in (ru, uz):
        assert "12:00" not in text
        assert "17:00" not in text
        assert "октябр" not in text and "oktyabr" not in text
        assert "Tashkent INDEX" not in text
        assert "гост" not in text and "mehmon" not in text

    # A tenant that advertises guests keeps the historic invitation…
    guest = type(
        "T",
        (),
        {
            "tenant_name": "Promotors Show",
            "event_guest_date_text_ru": "12 и 13 сентября с 10:00",
            "event_venue_text_ru": "SOF EXPO",
            "event_guest_date_text_uz": "12 va 13-sentyabr, 10:00 dan",
            "event_venue_text_uz": "SOF EXPO",
        },
    )()
    assert "гостя" in rejected_for_tenant("ru", guest)
    assert "12 и 13 сентября с 10:00" in rejected_for_tenant("ru", guest)
    assert "mehmon" in rejected_for_tenant("uz", guest)
    # …while an empty "Дата для гостей" (the SPL switch) keeps it neutral.
    no_guest = type(
        "T",
        (),
        {"tenant_name": "SPL Show", "event_venue_text_ru": "Tashkent INDEX"},
    )()
    assert "Tashkent INDEX" not in rejected_for_tenant("ru", no_guest)
    assert "Tashkent INDEX" not in rejected_for_tenant("uz", no_guest)
