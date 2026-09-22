"""Tests for the admin panel RU/UZ localization system.

Covers:
1. Russian as the default locale on every login surface;
2. the Uzbek locale rendering Uzbek strings;
3. safe ``ru`` fallback for invalid cookie/query locale values;
4. locale switching never weakening auth, tenant isolation or token masking.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cryptography.fernet import Fernet  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from bot.admin import auth, i18n  # noqa: E402
from bot.admin.i18n import t  # noqa: E402
from bot.admin.server import create_admin_app  # noqa: E402
from bot.config import Config  # noqa: E402
from bot.db import Database  # noqa: E402

SUPER_PW = "super-pw"
TENANT_PW = "tenant-pw"


# ---------------------------------------------------------------------------
# Unit-level dictionary behaviour
# ---------------------------------------------------------------------------

def test_normalize_lang_accepts_only_ru_and_uz():
    assert i18n.normalize_lang("ru") == "ru"
    assert i18n.normalize_lang("uz") == "uz"
    # Everything else — including hostile values — is a safe fallback.
    for bad in ("en", "UZ", "RU", "", None, "fr", "?lang=uz", "../../etc/passwd"):
        assert i18n.normalize_lang(bad) == "ru", bad


def test_t_falls_back_to_russian_then_key():
    assert t("ru", "common.login") == "Войти"
    assert t("uz", "common.login") == "Kirish"
    assert t("de", "common.login") == "Войти"          # unknown locale → ru
    assert t("uz", "no.such.key") == "no.such.key"     # unknown key → key itself


def test_every_uz_key_exists_in_ru_and_vice_versa():
    ru, uz = i18n._STRINGS["ru"], i18n._STRINGS["uz"]
    assert set(ru) == set(uz), (
        f"ru-only keys: {sorted(set(ru) - set(uz))}; "
        f"uz-only keys: {sorted(set(uz) - set(ru))}"
    )
    for table in (ru, uz):
        for key, value in table.items():
            assert value and value.strip(), f"empty translation for {key}"


def test_lang_switcher_only_emits_whitelisted_locales():
    html = i18n.lang_switcher("uz")
    assert 'href="?lang=ru"' in html and 'href="?lang=uz"' in html
    assert "cur" in html  # current locale highlighted
    # extra query state (already urlencoded) rides along after the lang param
    html = i18n.lang_switcher("ru", extra_query="status=pending&search=a%2Bb")
    assert 'href="?lang=uz&status=pending&search=a%2Bb"' in html


# ---------------------------------------------------------------------------
# Shared fixtures: one platform with promotors + adrenaline tenants
# ---------------------------------------------------------------------------

def _make_db(path: str) -> Database:
    return Database(path, encryption_key=Fernet.generate_key().decode())


async def _setup(db: Database) -> tuple:
    await db.init()
    promotors = await db.get_tenant("promotors")
    await db.update_tenant(promotors.id, admin_password=TENANT_PW, name="Promotors Show")
    adrenaline = await db.create_tenant(
        slug="adrenaline", name="Adrenaline Rush", admin_password="adr-pw"
    )
    promotors_app = await db.create_application(
        tenant_id=promotors.id, user_id=1, username="@p1", country="Узбекистан",
        plate="01A001AA", direction="Adrenaline Drift", phone="+998900000001",
        photo_file_ids=[], photo_paths=[],
    )
    return promotors, adrenaline, promotors_app


def _super_client(db: Database, config: Config) -> TestClient:
    return TestClient(TestServer(create_admin_app(config=config, db=db)))


def _config(tmp: str, db_path: str) -> Config:
    return Config(
        super_admin_password=SUPER_PW,
        encryption_key=Fernet.generate_key().decode(),
        google_credentials_file="",
        db_path=db_path,
        media_dir=tmp,
        require_subscription=False,
        registration_closed=False,
        panel_port=8080,
        admin_user_ids=frozenset(),
    )


def _tenant_cookie_header(tenant: Database.tenant_model if False else object) -> str:  # noqa: F821
    """Sign with the stored (hashed) password, exactly like a real session."""
    return (
        f"{auth.tenant_cookie_name(tenant.slug)}="
        f"{auth.make_cookie(tenant.admin_password)}"
    )


# ---------------------------------------------------------------------------
# 1 + 2: default ru, and uz rendering
# ---------------------------------------------------------------------------

def test_default_locale_is_russian_on_all_login_pages():
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(os.path.join(tmp, "i18n.db"))
        promotors, adrenaline, _ = asyncio.run(_setup(db))
        config = _config(tmp, db.path)

        async def run():
            async with _super_client(db, config) as client:
                # No cookie and no query param → everything in Russian.
                for path in (
                    "/super-admin/login",
                    "/login",
                    f"/t/{adrenaline.slug}/login",
                    f"/t/{adrenaline.slug}/login",
                ):
                    r = await client.get(path)
                    html = await r.text()
                    assert r.status == 200, path
                    assert "<html lang='ru'>" in html, path
                    assert "Войти" in html, path
                    assert "Kirish" not in html, path
                    # The switcher is present even before authentication.
                    assert 'class="lang-switch"' in html, path
                    assert 'href="?lang=uz"' in html, path

        asyncio.run(run())


def test_uzbek_locale_renders_uzbek_strings_and_persists_via_cookie():
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(os.path.join(tmp, "i18n.db"))
        promotors, adrenaline, _ = asyncio.run(_setup(db))
        config = _config(tmp, db.path)

        async def run():
            async with _super_client(db, config) as client:
                # ?lang=uz switches immediately…
                r = await client.get("/super-admin/login?lang=uz", allow_redirects=False)
                html = await r.text()
                assert r.status == 200
                assert "<html lang='uz'>" in html
                assert "Kirish" in html and "Войти" not in html
                # …and the choice is stored in the pm_lang cookie.
                set_cookie = r.headers.get("Set-Cookie", "")
                assert "pm_lang=uz" in set_cookie
                assert "HttpOnly" in set_cookie and "SameSite=Lax" in set_cookie

                # The cookie alone (no query param) keeps Uzbek on next pages.
                uz_cookie = {"Cookie": "pm_lang=uz"}
                r = await client.get("/login", headers=uz_cookie)
                html = await r.text()
                assert "<html lang='uz'>" in html
                assert "Tenant admin paneliga kirish" in html

                # Uzbek super-admin dashboard.
                hdr = {
                    "Cookie": f"pm_lang=uz; {auth.SUPER_COOKIE_NAME}="
                              f"{auth.make_cookie(SUPER_PW)}"
                }
                r = await client.get("/super-admin/", headers=hdr)
                html = await r.text()
                assert "Tenant yaratish" in html
                assert "Diagnostika" in html and "Arxiv" in html
                assert "Создать tenant" not in html

                # Uzbek tenant panel (a super admin may preview it).
                hdr = {
                    "Cookie": f"pm_lang=uz; {_tenant_cookie_header(adrenaline)}"
                }
                r = await client.get(f"/t/{adrenaline.slug}/", headers=hdr)
                html = await r.text()
                assert "Jami arizalar" in html
                assert "Boshqaruv paneli" in html
                assert "Ommaviy xabar" in html and "Sozlamalar" in html

                # Uzbek broadcast page with its filters and buttons.
                r = await client.get(f"/t/{adrenaline.slug}/broadcast", headers=hdr)
                html = await r.text()
                assert "Telegramga ommaviy xabar" in html
                assert 'name="text_uz"' in html and 'name="text_ru"' in html

        asyncio.run(run())


def test_tenant_panel_in_russian_by_default_and_uzbek_when_switched():
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(os.path.join(tmp, "i18n.db"))
        promotors, adrenaline, _ = asyncio.run(_setup(db))
        config = _config(tmp, db.path)

        async def run():
            async with _super_client(db, config) as client:
                tenant_hdr = {"Cookie": _tenant_cookie_header(adrenaline)}

                # ru (default) tenant dashboard.
                r = await client.get(f"/t/{adrenaline.slug}/", headers=tenant_hdr)
                html = await r.text()
                assert "Всего заявок" in html and "Дашборд" in html
                assert "🚗 Adrenaline Rush — Админ" in html  # brand localized per tenant

                # Same page switched to uz; brand switches language too.
                r = await client.get(
                    f"/t/{adrenaline.slug}/?lang=uz", headers=tenant_hdr
                )
                html = await r.text()
                assert "Jami arizalar" in html and "Boshqaruv paneli" in html
                assert "🚗 Adrenaline Rush — Admin" in html
                assert "Всего заявок" not in html

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 3: invalid locale values fall back to ru safely
# ---------------------------------------------------------------------------

def test_invalid_locale_values_safely_fall_back_to_russian():
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(os.path.join(tmp, "i18n.db"))
        promotors, adrenaline, _ = asyncio.run(_setup(db))
        config = _config(tmp, db.path)

        async def run():
            async with _super_client(db, config) as client:
                for bad in ("en", "DE", "../../etc/passwd", "%22%3E%3Cscript%3E"):
                    r = await client.get(f"/super-admin/login?lang={bad}")
                    html = await r.text()
                    assert "<html lang='ru'>" in html, bad
                    assert "Войти" in html, bad
                    # No reflection of the hostile value anywhere.
                    assert "script" not in html.replace("<script>", ""), bad

                # An invalid value does not overwrite a valid stored cookie…
                r = await client.get(
                    "/super-admin/login?lang=xx", headers={"Cookie": "pm_lang=uz"}
                )
                # …but per the documented rule the page itself renders ru.
                assert "<html lang='ru'>" in (await r.text())
                set_cookie = r.headers.get("Set-Cookie", "")
                assert "pm_lang" not in set_cookie  # untouched

                # Invalid cookie value also just falls back to ru.
                r = await client.get("/login", headers={"Cookie": "pm_lang=javascript"})
                assert "<html lang='ru'>" in (await r.text())

        asyncio.run(run())


# ---------------------------------------------------------------------------
# 4: locale switching never weakens auth / isolation / masking
# ---------------------------------------------------------------------------

def test_locale_switch_keeps_auth_and_isolation_and_masks_tokens():
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(os.path.join(tmp, "i18n.db"))
        promotors, adrenaline, promotors_app = asyncio.run(_setup(db))
        config = _config(tmp, db.path)

        async def run():
            async with _super_client(db, config) as client:
                # Unauthenticated: ?lang=uz on a protected page must not grant
                # access — still redirected to the login page.
                r = await client.get(
                    "/super-admin/?lang=uz", headers={"Cookie": "pm_lang=uz"},
                    allow_redirects=False,
                )
                assert r.status == 302
                assert r.headers["Location"] == "/super-admin/login"

                r = await client.get(
                    f"/t/{adrenaline.slug}/applications?lang=uz",
                    headers={"Cookie": "pm_lang=uz"},
                    allow_redirects=False,
                )
                assert r.status == 302
                assert r.headers["Location"] == f"/t/{adrenaline.slug}/login"

                # Wrong tenant password still fails in any locale.
                r = await client.post(
                    f"/t/{adrenaline.slug}/login?lang=uz",
                    data={"password": "wrong"},
                    allow_redirects=False,
                )
                assert r.status == 302
                assert "error=1" in r.headers["Location"]

                # Tenant cookie signed for adrenaline: uz page shows only
                # adrenaline's rows, never promotors'.
                hdr = {"Cookie": _tenant_cookie_header(adrenaline)}
                r = await client.get(
                    f"/t/{adrenaline.slug}/applications?lang=uz", headers=hdr
                )
                html = await r.text()
                assert r.status == 200
                assert "Arizalar (0)" in html
                assert "01A001AA" not in html  # promotors application

                # Guessing the other tenant's application id is still a 404.
                r = await client.get(
                    f"/t/{adrenaline.slug}/application/{promotors_app}?lang=uz",
                    headers=hdr,
                )
                assert r.status == 404
                assert (await r.text()) == "Ariza topilmadi"  # localized 404

                # Super-admin view in uz still masks the (would-be) token.
                super_hdr = {
                    "Cookie": f"pm_lang=uz; {auth.SUPER_COOKIE_NAME}="
                              f"{auth.make_cookie(SUPER_PW)}"
                }
                r = await client.get("/super-admin/?lang=uz", headers=super_hdr)
                html = await r.text()
                assert "*** kiritilgan" in html or "— kiritilmagan" in html
                assert "pmshowbot-secret-token" not in html

                # The locale cookie never substitutes for the auth cookie:
                # clearing super auth while keeping pm_lang still redirects.
                r = await client.get(
                    "/super-admin/", headers={"Cookie": "pm_lang=uz"},
                    allow_redirects=False,
                )
                assert r.status == 302

        asyncio.run(run())


def test_tenant_login_sets_cookie_with_language_preserved():
    """A full login POST → redirect keeps the locale cookie untouched."""
    with tempfile.TemporaryDirectory() as tmp:
        db = _make_db(os.path.join(tmp, "i18n.db"))
        promotors, adrenaline, _ = asyncio.run(_setup(db))
        config = _config(tmp, db.path)

        async def run():
            async with _super_client(db, config) as client:
                r = await client.post(
                    f"/t/{adrenaline.slug}/login?lang=uz",
                    data={"password": "adr-pw"},
                    allow_redirects=False,
                )
                assert r.status == 302
                set_cookies = " | ".join(r.headers.getall("Set-Cookie", []))
                # Auth cookie present…
                assert auth.tenant_cookie_name(adrenaline.slug) in set_cookies
                # …and the language choice rode along on the redirect.
                assert "pm_lang=uz" in set_cookies

        asyncio.run(run())


if __name__ == "__main__":
    test_normalize_lang_accepts_only_ru_and_uz()
    test_t_falls_back_to_russian_then_key()
    test_every_uz_key_exists_in_ru_and_vice_versa()
    test_lang_switcher_only_emits_whitelisted_locales()
    test_default_locale_is_russian_on_all_login_pages()
    test_uzbek_locale_renders_uzbek_strings_and_persists_via_cookie()
    test_tenant_panel_in_russian_by_default_and_uzbek_when_switched()
    test_invalid_locale_values_safely_fall_back_to_russian()
    test_locale_switch_keeps_auth_and_isolation_and_masks_tokens()
    test_tenant_login_sets_cookie_with_language_preserved()
    print("All i18n tests passed.")
