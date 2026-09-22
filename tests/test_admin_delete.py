"""Deleting an application from the admin panel.

The client could not remove a registered participant from the database:
an approved application keeps answering ``/start`` with its old status, so
testers had no way to run the form again.  The panel now deletes the row
(and its photos), which frees both the participant and the registration number.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from cryptography.fernet import Fernet  # noqa: E402

from bot.admin import auth  # noqa: E402
from bot.admin.server import create_admin_app  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.db import STATUS_APPROVED, Database  # noqa: E402

PW = "panel-pass"


async def _seed(tmp: str) -> tuple[Database, int, str]:
    """One approved application with a photo on disk."""
    key = Fernet.generate_key().decode()
    db = Database(os.path.join(tmp, "del.db"), encryption_key=key)
    await db.init()
    photo = os.path.join(tmp, "left.jpg")
    with open(photo, "wb") as fh:
        fh.write(b"\xff\xd8\xff\xe0photo")
    app_id = await db.create_application(
        user_id=7,
        username="@tester",
        country="Узбекистан",
        plate="01A777AA",
        direction="SPL Автозвук — SPL Front",
        phone="+998901112233",
        photo_file_ids=["f0"],
        photo_paths=[photo],
        language="ru",
    )
    await db.approve(app_id, "@mod")
    return db, app_id, photo


def _legacy_config(tmp: str) -> SimpleNamespace:
    """Historic single-tenant panel: routes live at the root path."""
    return SimpleNamespace(
        admin_password=PW,
        panel_port=8080,
        db_path=os.path.join(tmp, "del.db"),
        media_dir=tmp,
    )


def test_delete_removes_application_and_lets_the_person_register_again():
    with tempfile.TemporaryDirectory() as tmp:
        db, app_id, photo = asyncio.run(_seed(tmp))
        admin_app = create_admin_app(bot=None, config=_legacy_config(tmp), db=db)
        hdr = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(PW)}"}

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                page = await client.get(f"/application/{app_id}", headers=hdr)
                body = await page.text()
                assert page.status == 200
                # The danger zone is on the application page…
                assert f'action="/application/{app_id}/delete"' in body
                assert "Удаление заявки" in body

                response = await client.post(
                    f"/application/{app_id}/delete", headers=hdr, allow_redirects=False
                )
                assert response.status == 302
                assert response.headers["Location"].endswith("/applications?deleted=1")

                # …the row, its status and its photo are really gone.
                assert await db.get_application(app_id) is None
                assert await db.has_active_application(7) is None
                assert not os.path.exists(photo)

                listing = await client.get("/applications?deleted=1", headers=hdr)
                listed = await listing.text()
                assert listing.status == 200
                assert "Заявка удалена" in listed
                assert "01A777AA" not in listed

                # The freed number is handed to the next approved application.
                new_id = await db.create_application(
                    user_id=8,
                    username="@next",
                    country="Узбекистан",
                    plate="01A888BB",
                    direction="Adrenaline Drift",
                    phone="+998900000001",
                    photo_file_ids=[],
                    photo_paths=[],
                )
                assert await db.approve(new_id, "@mod") == 1
                assert (await db.get_application(new_id)).status == STATUS_APPROVED

                # Unknown id → 404, no accidental deletion of a neighbouring row.
                assert (
                    await client.post("/application/999999/delete", headers=hdr)
                ).status == 404

        asyncio.run(run())


def test_tenant_panel_delete_is_scoped_and_prefixed():
    with tempfile.TemporaryDirectory() as tmp:
        key = Fernet.generate_key().decode()
        db = Database(os.path.join(tmp, "t.db"), encryption_key=key)
        asyncio.run(db.init())

        async def setup():
            promotors = await db.get_tenant("promotors")
            await db.update_tenant(promotors.id, admin_password="promotors-pw")
            spl = await db.create_tenant(
                slug="splshow",
                name="SPL Show",
                bot_token="spl-token",
                admin_password="spl-pw",
            )
            spl_app = await db.create_application(
                tenant_id=spl.id,
                user_id=1,
                username="@spl_user",
                country="Узбекистан",
                plate="80A002AA",
                direction="SPL Автозвук — SPL Front",
                phone="+998900000000",
                photo_file_ids=[],
                photo_paths=[],
            )
            other_app = await db.create_application(
                tenant_id=promotors.id,
                user_id=2,
                username="@promotors_user",
                country="Узбекистан",
                plate="01A001AA",
                direction="Adrenaline Drift",
                phone="+998900000000",
                photo_file_ids=[],
                photo_paths=[],
            )
            return spl, spl_app, other_app

        spl, spl_app, other_app = asyncio.run(setup())
        config = Config(
            super_admin_password="super-pw",
            encryption_key=key,
            google_credentials_file="",
            db_path=os.path.join(tmp, "t.db"),
            media_dir=tmp,
            require_subscription=False,
            registration_closed=False,
            panel_port=8080,
            admin_user_ids=frozenset(),
        )
        admin_app = create_admin_app(config=config, db=db)
        cookie = {
            "Cookie": f"{auth.tenant_cookie_name(spl.slug)}="
            f"{auth.make_cookie(spl.admin_password)}"
        }

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                page = await client.get(f"/t/splshow/application/{spl_app}", headers=cookie)
                body = await page.text()
                assert page.status == 200
                # Root-relative actions are rewritten for the scoped panel.
                assert f'action="/t/splshow/application/{spl_app}/delete"' in body

                # Another tenant's application cannot be deleted through this URL.
                assert (
                    await client.post(
                        f"/t/splshow/application/{other_app}/delete", headers=cookie
                    )
                ).status == 404
                assert await db.get_application(other_app) is not None

                response = await client.post(
                    f"/t/splshow/application/{spl_app}/delete",
                    headers=cookie,
                    allow_redirects=False,
                )
                assert response.status == 302
                assert response.headers["Location"].endswith(
                    "/t/splshow/applications?deleted=1"
                )
                assert await db.get_application(spl_app) is None

        asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover - manual run helper
    test_delete_removes_application_and_lets_the_person_register_again()
    test_tenant_panel_delete_is_scoped_and_prefixed()
    print("ok")
