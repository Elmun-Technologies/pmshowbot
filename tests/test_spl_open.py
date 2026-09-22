"""SPL Show must accept registrations even if REGISTRATION_CLOSED is set.

The September Promotors close was a process-wide env flag. After the
multi-tenant split that flag made every bot, including SPL Show, answer
«регистрация завершена». Registration is now per tenant and open by default.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

from cryptography.fernet import Fernet

from bot.config import Config
from bot.db import SPL_EVENT_COPY, Database
from bot.texts import approved_for_tenant


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


def test_spl_schedule_is_seeded_and_stale_september_copy_is_replaced():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "spl.db")
            key = Fernet.generate_key().decode()
            db = Database(path, encryption_key=key)
            await db.init()
            created = await db.create_tenant(slug="splshow", name="SPL Show")
            assert created.event_venue_text_ru == "Tashkent INDEX"
            assert created.event_date_text_ru == "02 октября 2026 с 17:00 до 22:00"
            assert created.event_guest_date_text_ru == "03 октября 2026 с 12:00"
            assert "09:00" in created.event_note_text_ru
            assert "09:00" in created.event_note_text_uz
            assert "Tashkent INDEX" in created.event_note_text_ru
            assert created.registration_closed is False

            # Simulate the old README defaults already stored, plus one custom note.
            await db.update_tenant(
                "splshow",
                event_date_text_ru="11 сентября 2026 с 10:00 до 19:00",
                event_date_text_uz="11-sentyabr 2026, 10:00 dan 19:00 gacha",
                event_venue_text_ru="SOF EXPO",
                event_venue_text_uz="SOF EXPO",
                event_guest_date_text_ru="12 и 13 сентября с 10:00",
                event_guest_date_text_uz="12 va 13-sentyabr, 10:00 dan",
                event_note_text_ru="custom note",
            )
            await db.init()
            refreshed = await db.get_tenant("splshow")
            assert refreshed.event_date_text_ru == SPL_EVENT_COPY["event_date_text_ru"]
            assert refreshed.event_venue_text_ru == "Tashkent INDEX"
            assert refreshed.event_guest_date_text_uz == SPL_EVENT_COPY["event_guest_date_text_uz"]
            # A custom instruction is not overwritten.
            assert refreshed.event_note_text_ru == "custom note"
            assert refreshed.event_note_text_uz == SPL_EVENT_COPY["event_note_text_uz"]

            # Promotors keeps its own September copy.
            promotors = await db.get_tenant("promotors")
            assert "сентября" in promotors.event_date_text_ru
            assert promotors.event_venue_text_ru == "SOF EXPO"

    asyncio.run(run())


def test_approved_message_includes_arrival_show_start_and_car_rule():
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
    uz = approved_for_tenant("uz", tenant, 7)
    assert "№7" in ru
    assert "Заезд авто участников" in ru
    assert "02 октября 2026 с 17:00 до 22:00" in ru
    assert "Tashkent INDEX" in ru
    assert "09:00" in ru
    assert "рядом со своими автомобилями" in ru
    assert "02-oktyabr 2026" in uz
    assert "09:00" in uz
    assert "avtomobillari yonida" in uz
    # Empty note must not add a blank operational paragraph for other tenants.
    plain = approved_for_tenant(
        "ru",
        type("T", (), {"tenant_name": "Promotors Show", "event_date_text_ru": "11 сентября"})(),
        1,
    )
    assert "Заезд авто участников" in plain
    assert "09:00" not in plain
