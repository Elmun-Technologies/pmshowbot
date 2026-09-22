"""Regression tests for tenant migration, isolation, encryption and assets."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet
from aiohttp.test_utils import TestClient, TestServer

from bot.admin import auth
from bot.admin.server import create_admin_app
from bot.config import Config
from bot.db import Database
from bot.security import verify_password
from bot.services import assets


def _run(coro):
    return asyncio.run(coro)


def _application(db: Database, tenant_id: int, user_id: int, plate: str) -> int:
    return _run(
        db.create_application(
            tenant_id=tenant_id,
            user_id=user_id,
            username=f"@u{user_id}",
            country="Узбекистан",
            plate=plate,
            direction="Adrenaline Drift",
            phone="+998900000000",
            photo_file_ids=[],
            photo_paths=[],
            language="uz",
        )
    )


def test_legacy_database_becomes_encrypted_promotors_tenant_without_data_loss():
    """Old global rows migrate into promotors and receive an actual FK."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "legacy.db")
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
            """
            INSERT INTO applications (user_id, username, plate, created_at)
            VALUES (77, '@historic', '01A777AA', '2025-01-01T00:00:00+00:00')
            """
        )
        conn.commit()
        conn.close()

        key = Fernet.generate_key().decode("ascii")
        db = Database(
            path,
            encryption_key=key,
            bootstrap={
                "legacy_bot_token": "123:historic-token",
                "legacy_admin_chat_id": -100123,
                "legacy_required_channel": "@promotorsshow",
                "legacy_admin_password": "tenant-password",
            },
        )
        _run(db.init())

        tenant = _run(db.get_tenant("promotors"))
        assert tenant is not None
        assert tenant.admin_chat_id == -100123
        assert tenant.required_channel == "@promotorsshow"
        assert tenant.bot_token_encrypted != "123:historic-token"
        assert _run(db.get_tenant_token(tenant.id)) == "123:historic-token"
        assert verify_password(tenant.admin_password, "tenant-password")

        app = _run(db.get_application(1, tenant_id=tenant.id))
        assert app is not None
        assert app.tenant_id == tenant.id
        assert app.plate == "01A777AA"

        conn = sqlite3.connect(path)
        fk_rows = conn.execute("PRAGMA foreign_key_list(applications)").fetchall()
        assert any(row[2] == "tenants" and row[3] == "tenant_id" for row in fk_rows)
        bot_user_columns = conn.execute("PRAGMA table_info(bot_users)").fetchall()
        assert [row[1] for row in bot_user_columns[:2]] == ["tenant_id", "user_id"]


def test_applications_users_numbers_and_broadcasts_are_tenant_isolated():
    """The same Telegram user can safely exist in two unrelated bot tenants."""
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "tenants.db"), encryption_key=Fernet.generate_key().decode())
        _run(db.init())
        promotors = _run(db.get_tenant("promotors"))
        adrenaline = _run(
            db.create_tenant(
                slug="adrenaline", name="Adrenaline Rush", admin_password="adrenaline-pw"
            )
        )

        first_promotors = _application(db, promotors.id, 9, "01A009AA")
        first_adrenaline = _application(db, adrenaline.id, 9, "80A009AA")
        second_adrenaline = _application(db, adrenaline.id, 10, "80A010AA")

        # Numbers are sequential within, not across, tenants.
        assert _run(db.approve(first_promotors, "@mod", tenant_id=promotors.id)) == 1
        assert _run(db.approve(first_adrenaline, "@mod", tenant_id=adrenaline.id)) == 1
        assert _run(db.approve(second_adrenaline, "@mod", tenant_id=adrenaline.id)) == 2

        assert _run(db.get_application(first_promotors, tenant_id=adrenaline.id)) is None
        assert [app.plate for app in _run(db.list_applications(tenant_id=promotors.id))] == ["01A009AA"]
        assert {app.plate for app in _run(db.list_applications(tenant_id=adrenaline.id))} == {
            "80A009AA", "80A010AA"
        }
        assert _run(db.stats(tenant_id=promotors.id))["approved"] == 1
        assert _run(db.stats(tenant_id=adrenaline.id))["approved"] == 2
        assert {user_id for user_id, _ in _run(db.recipients("approved", tenant_id=promotors.id))} == {9}
        assert {user_id for user_id, _ in _run(db.recipients("approved", tenant_id=adrenaline.id))} == {9, 10}


def test_legacy_assets_move_once_and_tenant_uploads_never_mix():
    """Old uploads land in promotors; later tenant files stay in their own roots."""
    with tempfile.TemporaryDirectory() as tmp:
        old_sponsors = os.path.join(tmp, "_sponsors")
        os.makedirs(old_sponsors)
        with open(os.path.join(old_sponsors, "1_old.png"), "wb") as fh:
            fh.write(b"old")

        moved = assets.migrate_legacy_assets(tmp, "promotors")
        assert moved["sponsors"] == 1
        migrated = os.path.join(tmp, "_tenants", "promotors", "_sponsors", "1_old.png")
        assert os.path.exists(migrated)
        assert not os.path.exists(old_sponsors)

        assets.configure(tmp)
        try:
            assets.save_sponsor("1_prom", b"prom", tenant_id="promotors")
            assets.save_sponsor("1_adrenaline", b"adr", tenant_id="adrenaline")
            promotors_files = [os.path.basename(path) for path in assets.sponsor_files("promotors")]
            adrenaline_files = [os.path.basename(path) for path in assets.sponsor_files("adrenaline")]
            assert "1_prom.png" in promotors_files
            assert "1_adrenaline.png" not in promotors_files
            assert adrenaline_files == ["1_adrenaline.png"]
            assert assets.delete_asset("sponsors", "1_adrenaline", "promotors") is False
        finally:
            assets.configure(None)


async def _exercise_scoped_admin_panel(tmp: str) -> None:
    key = Fernet.generate_key().decode("ascii")
    db = Database(os.path.join(tmp, "panel.db"), encryption_key=key)
    await db.init()
    promotors = await db.get_tenant("promotors")
    await db.update_tenant(promotors.id, admin_password="promotors-pw")
    adrenaline = await db.create_tenant(
        slug="adrenaline",
        name="Adrenaline Rush",
        bot_token="private-adrenaline-token",
        admin_password="adrenaline-pw",
    )
    promotors_app = await db.create_application(
        tenant_id=promotors.id,
        user_id=1,
        username="@promotors_user",
        country="Узбекистан",
        plate="01A001AA",
        direction="Adrenaline Drift",
        phone="+998900000000",
        photo_file_ids=[],
        photo_paths=[],
    )
    adrenaline_app = await db.create_application(
        tenant_id=adrenaline.id,
        user_id=2,
        username="@adrenaline_user",
        country="Узбекистан",
        plate="80A002AA",
        direction="Adrenaline Drift",
        phone="+998900000000",
        photo_file_ids=[],
        photo_paths=[],
    )
    config = Config(
        super_admin_password="super-pw",
        encryption_key=key,
        google_credentials_file="",
        db_path=os.path.join(tmp, "panel.db"),
        media_dir=tmp,
        require_subscription=False,
        registration_closed=False,
        panel_port=8080,
        admin_user_ids=frozenset(),
    )
    app = create_admin_app(config=config, db=db)
    tenant_cookie = {
        "Cookie": f"{auth.tenant_cookie_name(adrenaline.slug)}="
        f"{auth.make_cookie(adrenaline.admin_password)}"
    }
    super_cookie = {"Cookie": f"{auth.SUPER_COOKIE_NAME}={auth.make_cookie('super-pw')}"}
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/t/adrenaline/applications", headers=tenant_cookie)
        body = await response.text()
        assert response.status == 200
        assert "80A002AA" in body and "01A001AA" not in body
        # Guessing another tenant's globally allocated application id is blocked
        # by the scoped Database facade, not just hidden by the HTML list.
        assert (
            await client.get(
                f"/t/adrenaline/application/{promotors_app}", headers=tenant_cookie
            )
        ).status == 404
        assert (
            await client.get(
                f"/t/adrenaline/application/{adrenaline_app}", headers=tenant_cookie
            )
        ).status == 200

        response = await client.get("/super-admin/", headers=super_cookie)
        super_html = await response.text()
        assert response.status == 200
        assert "Adrenaline Rush" in super_html
        assert "private-adrenaline-token" not in super_html
        # The masked status marker (locale-independent "***") proves the real
        # token is never rendered in any interface language.
        assert "***" in super_html


def test_scoped_admin_panel_hides_other_tenant_rows_and_tokens():
    """HTTP auth context enforces the same tenant boundary as Database methods."""
    with tempfile.TemporaryDirectory() as tmp:
        asyncio.run(_exercise_scoped_admin_panel(tmp))


if __name__ == "__main__":
    test_legacy_database_becomes_encrypted_promotors_tenant_without_data_loss()
    test_applications_users_numbers_and_broadcasts_are_tenant_isolated()
    test_legacy_assets_move_once_and_tenant_uploads_never_mix()
    test_scoped_admin_panel_hides_other_tenant_rows_and_tokens()
    print("Tenant migration and isolation tests passed.")
