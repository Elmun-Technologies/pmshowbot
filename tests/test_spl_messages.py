"""SPL Show September changes: panel message texts, brand casing, 16 categories."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet  # noqa: E402

from bot import texts  # noqa: E402
from bot.db import (  # noqa: E402
    SPL_AUTOSOUND_CHILDREN,
    SPL_AUTOSOUND_PREVIOUS,
    Database,
    normalize_spl_brand,
)


def _tenant(**fields):
    base = {
        "tenant_name": "SPL Show",
        "channel_url": "https://t.me/splshow",
        "event_venue_text_ru": "Tashkent INDEX",
        "event_venue_text_uz": "Tashkent INDEX",
        "event_date_text_ru": "2 октября с 17:00",
        "event_date_text_uz": "2-oktyabr soat 17:00 dan",
    }
    base.update(fields)
    return type("T", (), base)()


def test_the_panel_approval_text_replaces_the_generated_one():
    tenant = _tenant(
        approved_text_ru="№{number}, {name} ({plate}) — {event}, {date}, {venue}.\n{direction}\n{channel}",
        approved_text_uz="Raqam {number} — {date}",
    )
    ru = texts.approved_for_tenant(
        "ru", tenant, 5, name="Ali", plate="01A123BC",
        direction="SPL Автозвук — SPL Show Лайт; SPL Автозвук — SPL Game 139.99",
    )
    assert ru.startswith("№5, Ali (01A123BC) — SPL Show, 2 октября с 17:00, Tashkent INDEX.")
    assert "• SPL Автозвук — SPL Show Лайт\n• SPL Автозвук — SPL Game 139.99" in ru
    assert "https://t.me/splshow" in ru
    assert "Поздравляем" not in ru
    assert texts.approved_for_tenant("uz", tenant, 5) == "Raqam 5 — 2-oktyabr soat 17:00 dan"


def test_the_panel_rejection_text_replaces_the_generated_one():
    tenant = _tenant(rejected_text_ru="К сожалению, {name}, заявка на {event} отклонена.")
    ru = texts.rejected_for_tenant("ru", tenant, name="Ali")
    assert ru == "К сожалению, Ali, заявка на SPL Show отклонена."
    # No uz text typed -> the generated neutral rejection, without any time.
    uz = texts.rejected_for_tenant("uz", tenant)
    assert "ro‘yxatdan o‘tmadingiz" in uz
    assert "17:00" not in uz


def test_a_typo_in_the_panel_text_never_breaks_the_message():
    for raw in ("Номер {numbr}!", "Скобка { и }", "{0} позиционный", "{number.x}"):
        out = texts.approved_for_tenant("ru", _tenant(approved_text_ru=raw), 3)
        assert out  # never raises, never empty
    assert texts.approved_for_tenant("ru", _tenant(approved_text_ru="Номер {numbr}"), 3) == "Номер {numbr}"


def test_spl_is_written_in_capitals():
    assert normalize_spl_brand("Spl Show") == "SPL Show"
    assert normalize_spl_brand("spl show 2026") == "SPL show 2026"
    assert normalize_spl_brand("Splendid Show") == "Splendid Show"
    assert texts.approved_for_tenant("ru", _tenant(tenant_name="Spl Show", approved_text_ru="{event}"), 1) == "SPL Show"

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "brand.db")
            db = Database(path, encryption_key=Fernet.generate_key().decode())
            await db.init()
            await db.create_tenant(slug="splshow", name="SPL Show")
            await db.init()  # the one-time migration has already run once
            # A name miscased directly in the stored data (what production had).
            conn = sqlite3.connect(path)
            conn.execute("UPDATE tenants SET name = 'Spl Show' WHERE slug = 'splshow'")
            conn.execute("DELETE FROM app_meta WHERE key LIKE 'spl_messages_%'")
            conn.commit()
            conn.close()
            await db.init()
            assert (await db.get_tenant("splshow")).name == "SPL Show"
            # …and typing it wrong in the panel is corrected as well.
            await db.update_tenant("splshow", name="Spl Show")
            assert (await db.get_tenant("splshow")).name == "SPL Show"

    asyncio.run(run())


def test_the_templates_are_stored_per_tenant():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(os.path.join(tmp, "tpl.db"), encryption_key=Fernet.generate_key().decode())
            await db.init()
            await db.create_tenant(slug="splshow", name="SPL Show")
            await db.update_tenant(
                "splshow",
                approved_text_ru="ok {number}",
                approved_text_uz="ok uz",
                rejected_text_ru="no",
                rejected_text_uz="yo‘q",
            )
            await db.init()
            t = await db.get_tenant("splshow")
            assert (t.approved_text_ru, t.approved_text_uz) == ("ok {number}", "ok uz")
            assert (t.rejected_text_ru, t.rejected_text_uz) == ("no", "yo‘q")

    asyncio.run(run())


def test_the_old_four_categories_are_replaced_by_the_confirmed_sixteen():
    assert len(SPL_AUTOSOUND_CHILDREN) == 16

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "cats.db")
            db = Database(path, encryption_key=Fernet.generate_key().decode())
            await db.init()
            tenant = await db.create_tenant(slug="splshow", name="SPL Show")
            await db.init()
            scoped = db.for_tenant(tenant.id)
            root = next(d for d in await scoped.list_all_directions() if d.canonical == "SPL Автозвук")

            # Rebuild the state of the previous build: the four old categories.
            conn = sqlite3.connect(path)
            conn.execute("DELETE FROM directions WHERE parent_id = ?", (root.id,))
            for old in SPL_AUTOSOUND_PREVIOUS:
                conn.execute(
                    "INSERT INTO directions (tenant_id, parent_id, canonical, label_ru,"
                    " label_uz, slug, sort_order, is_active, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 0, 1, 'now', 'now')",
                    (tenant.id, root.id, old["canonical"], old["canonical"],
                     old["canonical"], old["slug"]),
                )
            conn.commit()
            conn.close()

            for _ in range(2):  # idempotent across restarts
                await db.init()
            children = [d for d in await scoped.list_all_directions() if d.parent_id == root.id]
            active = sorted(d.canonical for d in children if d.is_active)
            assert active == sorted(c["canonical"] for c in SPL_AUTOSOUND_CHILDREN)
            # Old rows are kept (old applications reference them) but hidden.
            old = {d.slug: d for d in children if d.slug in {o["slug"] for o in SPL_AUTOSOUND_PREVIOUS}}
            assert len(old) == 4 and not any(d.is_active for d in old.values())
            assert len(children) == 20

    asyncio.run(run())


def test_the_team_edits_both_message_texts_in_the_panel():
    from aiohttp.test_utils import TestClient, TestServer

    from bot.admin import auth
    from bot.admin.server import create_admin_app
    from bot.config import Config

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            key = Fernet.generate_key().decode()
            path = os.path.join(tmp, "panel.db")
            db = Database(path, encryption_key=key)
            await db.init()
            spl = await db.create_tenant(
                slug="splshow", name="SPL Show", bot_token="1:x", admin_password="pw"
            )
            config = Config(
                super_admin_password="super-pw", encryption_key=key,
                google_credentials_file="", db_path=path, media_dir=tmp,
                require_subscription=False, registration_closed=False,
                panel_port=8080, admin_user_ids=frozenset(),
            )
            app = create_admin_app(config=config, db=db)
            cookie = {"Cookie": f"{auth.tenant_cookie_name(spl.slug)}={auth.make_cookie(spl.admin_password)}"}
            async with TestClient(TestServer(app)) as client:
                page = await (await client.get("/t/splshow/settings", headers=cookie)).text()
                assert 'name="approved_text_ru"' in page and 'name="rejected_text_uz"' in page
                assert "{number}" in page
                response = await client.post(
                    "/t/splshow/settings",
                    headers=cookie,
                    data={
                        "name": "Spl Show",
                        "event_venue_text_ru": "Tashkent INDEX",
                        "approved_text_ru": "Ваш номер {number}. Заезд {date}.",
                        "approved_text_uz": "Raqam {number}",
                        "rejected_text_ru": "Заявка отклонена.",
                        "rejected_text_uz": "Ariza rad etildi.",
                    },
                    allow_redirects=False,
                )
                assert response.status == 302
                page = await (await client.get("/t/splshow/settings", headers=cookie)).text()
                assert "Ваш номер {number}. Заезд {date}." in page
            stored = await db.get_tenant("splshow")
            assert stored.name == "SPL Show"
            assert stored.approved_text_ru == "Ваш номер {number}. Заезд {date}."
            assert stored.rejected_text_uz == "Ariza rad etildi."
            tenant_config = config.tenant_config(stored, bot_token="1:x")
            assert texts.rejected_for_tenant("ru", tenant_config) == "Заявка отклонена."
            assert texts.approved_for_tenant("uz", tenant_config, 9) == "Raqam 9"

    asyncio.run(run())
