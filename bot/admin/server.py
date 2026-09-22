"""aiohttp control plane for super admins and isolated tenant admins.

The HTTP process is shared with every polling bot, but request middleware turns
each tenant URL into a tenant-scoped database/config/bot context before a
handler runs.  That boundary is deliberately server-side: changing an id in a
URL can never expose another tenant's applications or assets.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import re
import time
from typing import Any, Optional

from aiohttp import web

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile

from ..config import Config
from ..constants import DIRECTIONS, DIRECTIONS_CANON
from ..db import Database, Tenant, STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED
from ..executors import run_heavy
from ..services import assets, decisions, subscription
from ..security import EncryptionError
from . import auth, i18n, views
from .i18n import t
from urllib.parse import quote

logger = logging.getLogger(__name__)

_VALID_STATUSES = {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED}
_AUDIENCES = {
    "approved", "pending", "rejected", "incomplete", "all_apps", "starters",
}
_MAX_UPLOAD_SIZE = 20 * 1024 * 1024

# Known create/update tenant failures, mapped onto localized form errors.
_FORM_ERROR_KEYS = {
    "Tenant slug must contain lowercase letters, digits and hyphens only":
        "tenant.form.err_slug",
    "Tenant name is required": "tenant.form.err_name",
    "admin_chat_id must be an integer": "tenant.form.err_chat_id",
    "Admin chat ID must be an integer": "tenant.form.err_chat_id",
}


def create_admin_app(
    bot=None,
    config: Config | Any = None,
    db: Database | None = None,
    bot_manager=None,
) -> web.Application:
    """Create the multi-tenant aiohttp application.

    ``bot`` remains an optional compatibility injection for the historic root
    panel and focused tests.  Production routes resolve a bot from
    ``bot_manager`` by tenant id.
    """
    if config is None or db is None:
        raise ValueError("config and db are required")
    app = web.Application(
        middlewares=[_locale_middleware, _auth_middleware],
        client_max_size=_MAX_UPLOAD_SIZE,
    )
    app["bot"] = bot
    app["bot_manager"] = bot_manager
    app["config"] = config
    app["db"] = db

    app.router.add_get("/health", _health)

    # Tenant selector/login and scoped tenant panel.
    app.router.add_get("/login", _login_get)
    app.router.add_post("/login", _login_post)
    app.router.add_get("/logout", _logout)
    _register_tenant_routes(app, "/t/{slug}")

    # Super-admin control plane.
    app.router.add_get("/super-admin/login", _super_login_get)
    app.router.add_post("/super-admin/login", _super_login_post)
    app.router.add_get("/super-admin/logout", _super_logout)
    app.router.add_get("/super-admin", _super_dashboard)
    app.router.add_get("/super-admin/", _super_dashboard)
    app.router.add_get("/super-admin/tenants/new", _super_tenant_new_get)
    app.router.add_post("/super-admin/tenants/new", _super_tenant_new_post)
    app.router.add_get("/super-admin/tenants/{slug}/edit", _super_tenant_edit_get)
    app.router.add_post("/super-admin/tenants/{slug}/edit", _super_tenant_edit_post)
    app.router.add_post("/super-admin/tenants/{slug}/toggle", _super_tenant_toggle)
    app.router.add_post("/super-admin/tenants/{slug}/archive", _super_tenant_archive)
    # `/delete` is a data-safe archive alias: application history is retained.
    app.router.add_post("/super-admin/tenants/{slug}/delete", _super_tenant_archive)
    app.router.add_post("/super-admin/tenants/{slug}/restart", _super_tenant_restart)
    app.router.add_get("/super-admin/tenants/{slug}/diag", _super_tenant_diag)
    app.router.add_get("/super-admin/tenants/{slug}/directions", _super_tenant_directions)
    app.router.add_get("/super-admin/tenants/{slug}/directions/new", _super_direction_new_get)
    app.router.add_post("/super-admin/tenants/{slug}/directions/new", _super_direction_new_post)
    app.router.add_get("/super-admin/tenants/{slug}/directions/{direction_id}/edit", _super_direction_edit_get)
    app.router.add_post("/super-admin/tenants/{slug}/directions/{direction_id}/edit", _super_direction_edit_post)
    app.router.add_post("/super-admin/tenants/{slug}/directions/{direction_id}/delete", _super_direction_delete)
    return app


def _register_tenant_routes(app: web.Application, prefix: str) -> None:
    """Register a complete tenant panel underneath ``/t/{slug}``."""
    app.router.add_get(f"{prefix}/login", _tenant_login_get)
    app.router.add_post(f"{prefix}/login", _tenant_login_post)
    app.router.add_get(f"{prefix}/logout", _tenant_logout)
    app.router.add_get(prefix, _dashboard)
    app.router.add_get(f"{prefix}/", _dashboard)
    app.router.add_get(f"{prefix}/applications", _applications)
    app.router.add_get(f"{prefix}/application/{{id}}", _application_detail)
    app.router.add_post(f"{prefix}/application/{{id}}/approve", _approve)
    app.router.add_post(f"{prefix}/application/{{id}}/reject", _reject)
    app.router.add_post(f"{prefix}/application/{{id}}/message", _send_individual_message)
    app.router.add_post(f"{prefix}/application/{{id}}/status", _change_status)
    app.router.add_get(f"{prefix}/photo/{{id}}/{{idx}}", _photo)
    app.router.add_get(f"{prefix}/modphoto/{{id}}/{{idx}}", _mod_photo)
    app.router.add_get(f"{prefix}/badgephoto/{{id}}", _badge_photo)
    app.router.add_post(f"{prefix}/application/{{id}}/delete", _delete_application)
    app.router.add_post(f"{prefix}/application/{{id}}/ticket", _resend_ticket)
    app.router.add_get(f"{prefix}/export.csv", _export_csv)
    app.router.add_get(f"{prefix}/export.xlsx", _export_excel)
    app.router.add_get(f"{prefix}/broadcast", _broadcast_get)
    app.router.add_post(f"{prefix}/broadcast", _broadcast_post)
    app.router.add_get(f"{prefix}/settings", _tenant_settings_get)
    app.router.add_post(f"{prefix}/settings", _tenant_settings_post)
    app.router.add_get(f"{prefix}/ticket-assets", _ticket_assets)
    app.router.add_get(f"{prefix}/ticket-assets/preview.png", _ticket_preview)
    app.router.add_get(f"{prefix}/assets/file/{{kind}}/{{filename}}", _asset_file)
    app.router.add_post(f"{prefix}/ticket-assets/brand/upload", _brand_upload)
    app.router.add_post(f"{prefix}/ticket-assets/brand/delete", _brand_delete)
    app.router.add_post(f"{prefix}/ticket-assets/sponsor/upload", _sponsor_upload)
    app.router.add_post(f"{prefix}/ticket-assets/sponsor/delete", _sponsor_delete)
    app.router.add_post(f"{prefix}/ticket-assets/direction/upload", _direction_upload)
    app.router.add_post(f"{prefix}/ticket-assets/direction/delete", _direction_delete)

    # Historic single-tenant routes stay live for installations/tests that use
    # a SimpleNamespace with ADMIN_PASSWORD. New Config disables this branch.
    if prefix == "/t/{slug}":
        app.router.add_get("/", _dashboard)
        app.router.add_get("/applications", _applications)
        app.router.add_get("/application/{id}", _application_detail)
        app.router.add_post("/application/{id}/approve", _approve)
        app.router.add_post("/application/{id}/reject", _reject)
        app.router.add_post("/application/{id}/message", _send_individual_message)
        app.router.add_post("/application/{id}/status", _change_status)
        app.router.add_get("/photo/{id}/{idx}", _photo)
        app.router.add_get("/modphoto/{id}/{idx}", _mod_photo)
        app.router.add_get("/badgephoto/{id}", _badge_photo)
        app.router.add_post("/application/{id}/delete", _delete_application)
        app.router.add_post("/application/{id}/ticket", _resend_ticket)
        app.router.add_get("/export.csv", _export_csv)
        app.router.add_get("/export.xlsx", _export_excel)
        app.router.add_get("/broadcast", _broadcast_get)
        app.router.add_post("/broadcast", _broadcast_post)
        app.router.add_get("/settings", _tenant_settings_get)
        app.router.add_post("/settings", _tenant_settings_post)
        app.router.add_get("/ticket-assets", _ticket_assets)
        app.router.add_get("/ticket-assets/preview.png", _ticket_preview)
        app.router.add_get("/assets/file/{kind}/{filename}", _asset_file)
        app.router.add_post("/ticket-assets/brand/upload", _brand_upload)
        app.router.add_post("/ticket-assets/brand/delete", _brand_delete)
        app.router.add_post("/ticket-assets/sponsor/upload", _sponsor_upload)
        app.router.add_post("/ticket-assets/sponsor/delete", _sponsor_delete)
        app.router.add_post("/ticket-assets/direction/upload", _direction_upload)
        app.router.add_post("/ticket-assets/direction/delete", _direction_delete)


def _legacy_panel_enabled(config: Any) -> bool:
    return bool(getattr(config, "legacy_panel_enabled", True))


def _super_secret(config: Any) -> str:
    return str(getattr(config, "super_admin_password", "") or "")


# ---------------------------------------------------------------------------
# Locale (RU/UZ) handling
# ---------------------------------------------------------------------------

def _set_lang_cookie(response: web.StreamResponse, lang: str) -> None:
    """Persist the panel locale in its own cookie, next to the auth ones.

    The locale has no security meaning: it neither grants access nor scopes
    data, so it lives in a separate cookie and never touches the tenant or
    super-admin session cookies.
    """
    response.set_cookie(
        i18n.LANG_COOKIE,
        i18n.normalize_lang(lang),
        max_age=365 * 24 * 3600,
        httponly=True,
        samesite="Lax",
        secure=True,
        path="/",
    )


@web.middleware
async def _locale_middleware(request: web.Request, handler):
    """Resolve the panel locale before dispatch and persist explicit switches.

    Precedence: validated ``?lang=`` query parameter, then the ``pm_lang``
    cookie, then ``ru``.  A *valid* query parameter is stored in the cookie so
    the choice survives navigation; an invalid one simply renders ``ru``
    without touching the stored value.
    """
    raw_param = request.query.get("lang")
    lang = i18n.normalize_lang(raw_param or request.cookies.get(i18n.LANG_COOKIE))
    request["lang"] = lang
    persist = i18n.is_supported(raw_param)
    try:
        response = await handler(request)
    except web.HTTPException as exc:
        # Redirects raised with `raise web.HTTPFound(...)` are Response
        # subclasses, so the cookie can ride along on them too.
        if persist and isinstance(exc, web.Response):
            _set_lang_cookie(exc, lang)
        raise
    if persist and isinstance(response, web.Response):
        _set_lang_cookie(response, lang)
    return response


def _lang(request: web.Request) -> str:
    """Panel locale resolved by the middleware (safe ``ru`` fallback)."""
    return i18n.normalize_lang(request.get("lang"))


def _form_error_text(lang: str, exc: Exception) -> str:
    """Localized message for tenant create/update validation failures."""
    key = _FORM_ERROR_KEYS.get(str(exc))
    if key is not None:
        return t(lang, key)
    return t(lang, "tenant.form.err_generic", detail=str(exc))



def _is_super_request(request: web.Request) -> bool:
    secret = _super_secret(request.app["config"])
    return bool(secret and auth.valid_cookie(secret, request.cookies.get(auth.SUPER_COOKIE_NAME)))


def _set_cookie(response: web.StreamResponse, name: str, secret: str, *, path: str = "/") -> None:
    response.set_cookie(
        name,
        auth.make_cookie(secret),
        max_age=auth.MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=True,
        path=path,
    )


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    """Attach an authenticated tenant context before dispatching a route."""
    path = request.path
    config = request.app["config"]
    db = _db(request)
    if path == "/health" or path == "/login":
        return await handler(request)

    if path.startswith("/super-admin"):
        if path == "/super-admin/login":
            return await handler(request)
        secret = _super_secret(config)
        if not secret:
            return web.Response(
                text=views.super_panel_disabled_page(_lang(request)),
                content_type="text/html",
                status=503,
            )
        if not _is_super_request(request):
            raise web.HTTPFound("/super-admin/login")
        request["is_super"] = True
        return await handler(request)

    if path.startswith("/t/"):
        slug = request.match_info.get("slug", "")
        tenant = await db.get_tenant(slug)
        if tenant is None:
            raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
        request["tenant"] = tenant
        request["tenant_db"] = db.for_tenant(tenant.id)
        request["tenant_prefix"] = f"/t/{tenant.slug}"
        if hasattr(config, "tenant_config"):
            request["tenant_config"] = config.tenant_config(tenant)
        else:
            request["tenant_config"] = config
        manager = request.app.get("bot_manager")
        request["tenant_bot"] = manager.get_bot(tenant.id) if manager is not None else request.app.get("bot")
        if path == f"/t/{slug}/login":
            if not tenant.is_active and not _is_super_request(request):
                return web.Response(
                    text=views.tenant_inactive_page(_lang(request)),
                    content_type="text/html",
                    status=403,
                )
            return await handler(request)
        if _is_super_request(request):
            request["is_super"] = True
            return await handler(request)
        if not tenant.is_active or not tenant.admin_password:
            return web.Response(
                text=views.panel_disabled_page(_lang(request)),
                content_type="text/html",
                status=503,
            )
        cookie = request.cookies.get(auth.tenant_cookie_name(tenant.slug))
        if not auth.valid_cookie(tenant.admin_password, cookie):
            raise web.HTTPFound(f"/t/{tenant.slug}/login")
        return await handler(request)

    # Legacy root panel only. Production Config deliberately turns it off.
    if not _legacy_panel_enabled(config):
        raise web.HTTPFound("/login")
    password = str(getattr(config, "admin_password", "") or "")
    if not password:
        return web.Response(
            text=views.panel_disabled_page(_lang(request)),
            content_type="text/html",
            status=503,
        )
    if not auth.valid_cookie(password, request.cookies.get(auth.COOKIE_NAME)):
        raise web.HTTPFound("/login")
    return await handler(request)


def _db(request: web.Request):
    return request.get("tenant_db", request.app["db"])


def _config(request: web.Request):
    return request.get("tenant_config", request.app["config"])


def _bot(request: web.Request):
    return request.get("tenant_bot", request.app.get("bot"))


def _asset_scope(request: web.Request):
    config = _config(request)
    return getattr(config, "asset_scope", None)


def _url(request: web.Request, path: str) -> str:
    """Build a tenant-scoped URL from a root-relative panel path."""
    prefix = request.get("tenant_prefix", "")
    if prefix and path.startswith("/"):
        return prefix + path
    return path


def _html(request: web.Request, html: str, *, status: int = 200) -> web.Response:
    """Prefix legacy server-rendered links/actions for scoped tenant pages."""
    prefix = request.get("tenant_prefix", "")
    if prefix:
        html = html.replace('href="/', f'href="{prefix}/')
        html = html.replace('action="/', f'action="{prefix}/')
        html = html.replace('src="/', f'src="{prefix}/')
        tenant = request.get("tenant")
        if tenant is not None:
            from html import escape

            # The layout ships the platform brand; scoped pages show the
            # tenant name instead, in the language currently rendered.
            brand_pattern = re.compile(r"🚗 Promotors Show — [^<]+")
            brand_html = (
                f"🚗 {escape(tenant.name)} — "
                f"{escape(t(_lang(request), 'nav.brand_suffix'))}"
            )
            html = brand_pattern.sub(lambda _m: brand_html, html)
    return web.Response(text=html, content_type="text/html", status=status)


async def _health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _login_get(request: web.Request) -> web.Response:
    config = request.app["config"]
    lang = _lang(request)
    if _legacy_panel_enabled(config):
        password = str(getattr(config, "admin_password", "") or "")
        if not password:
            return _html(request, views.panel_disabled_page(lang), status=503)
        return _html(request, views.login_page(lang, error=request.query.get("error") == "1"))
    tenants = await request.app["db"].list_tenants(active_only=True)
    return _html(
        request,
        views.tenant_selector_login_page(lang, tenants, request.query.get("error") == "1"),
    )


async def _login_post(request: web.Request) -> web.Response:
    config = request.app["config"]
    data = await request.post()
    submitted = str(data.get("password", ""))
    if _legacy_panel_enabled(config):
        password = str(getattr(config, "admin_password", "") or "")
        if password and auth.password_matches(password, submitted):
            response = web.HTTPFound("/")
            _set_cookie(response, auth.COOKIE_NAME, password)
            raise response
        raise web.HTTPFound("/login?error=1")

    slug = str(data.get("slug", "")).strip()
    tenant = await request.app["db"].get_tenant(slug)
    if tenant and tenant.is_active and auth.password_matches(tenant.admin_password, submitted):
        response = web.HTTPFound(f"/t/{tenant.slug}/")
        _set_cookie(response, auth.tenant_cookie_name(tenant.slug), tenant.admin_password, path=f"/t/{tenant.slug}")
        raise response
    raise web.HTTPFound("/login?error=1")


async def _logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/login")
    response.del_cookie(auth.COOKIE_NAME)
    raise response


async def _tenant_login_get(request: web.Request) -> web.Response:
    tenant: Tenant = request["tenant"]
    # This view contains an explicit /t/<slug>/login action and a global
    # /login chooser link, so it deliberately bypasses generic link prefixing.
    return web.Response(
        text=views.tenant_login_page(
            _lang(request), tenant, error=request.query.get("error") == "1"
        ),
        content_type="text/html",
    )


async def _tenant_login_post(request: web.Request) -> web.Response:
    tenant: Tenant = request["tenant"]
    data = await request.post()
    if tenant.is_active and auth.password_matches(tenant.admin_password, str(data.get("password", ""))):
        response = web.HTTPFound(_url(request, "/"))
        _set_cookie(response, auth.tenant_cookie_name(tenant.slug), tenant.admin_password, path=f"/t/{tenant.slug}")
        raise response
    raise web.HTTPFound(_url(request, "/login?error=1"))


async def _tenant_logout(request: web.Request) -> web.Response:
    tenant: Tenant = request["tenant"]
    response = web.HTTPFound(_url(request, "/login"))
    response.del_cookie(auth.tenant_cookie_name(tenant.slug), path=f"/t/{tenant.slug}")
    raise response


async def _super_login_get(request: web.Request) -> web.Response:
    if not _super_secret(request.app["config"]):
        return _html(request, views.super_panel_disabled_page(_lang(request)), status=503)
    return _html(
        request,
        views.super_login_page(_lang(request), request.query.get("error") == "1"),
    )


async def _super_login_post(request: web.Request) -> web.Response:
    secret = _super_secret(request.app["config"])
    data = await request.post()
    if secret and auth.password_matches(secret, str(data.get("password", ""))):
        response = web.HTTPFound("/super-admin/")
        _set_cookie(response, auth.SUPER_COOKIE_NAME, secret)
        raise response
    raise web.HTTPFound("/super-admin/login?error=1")


async def _super_logout(request: web.Request) -> web.Response:
    response = web.HTTPFound("/super-admin/login")
    response.del_cookie(auth.SUPER_COOKIE_NAME)
    raise response


async def _dashboard(request: web.Request) -> web.Response:
    db = _db(request)
    stats = await db.stats()
    return _html(request, views.dashboard_page(_lang(request), stats))


async def _applications(request: web.Request) -> web.Response:
    db = _db(request)
    status = request.query.get("status")
    if status not in _VALID_STATUSES:
        status = None
    search = request.query.get("search", "").strip()
    apps = await db.list_applications(status=status, search=search or None)
    deleted_notice = (
        t(_lang(request), "apps.deleted_notice")
        if request.query.get("deleted") == "1"
        else ""
    )
    return _html(
        request,
        views.applications_page(
            _lang(request), apps, status, search, notice=deleted_notice
        ),
    )


async def _application_detail(request: web.Request) -> web.Response:
    db = _db(request)
    lang = _lang(request)
    app_id = _int_or_404(request.match_info["id"])
    app = await db.get_application(app_id)
    if app is None:
        raise web.HTTPNotFound(text=t(lang, "error.app_not_found"))
    msg = request.query.get("msg")
    status_flag = request.query.get("status_change")
    ticket = request.query.get("ticket")
    ticket_error = request.query.get("ticket_error", "")
    return _html(
        request,
        views.application_detail_page(
            lang,
            app,
            msg_sent=msg == "sent",
            msg_error=(
                t(lang, "error.msg_blocked") if msg == "blocked"
                else (t(lang, "error.msg_empty") if msg == "empty" else "")
            ),
            status_changed=status_flag == "ok",
            status_error=t(lang, "error.status_change") if status_flag == "error" else "",
            ticket_sent=ticket == "sent",
            ticket_error=(
                t(lang, "ticket.failed_notice", error=quote(ticket_error[:200]))
                if ticket == "failed"
                else ""
            ),
        ),
    )


async def _approve(request: web.Request) -> web.Response:
    """Decide in SQLite, then deliver behind the redirect.

    The HTTP request used to wait for the whole delivery (render + upload), so a
    panel click could sit for half a minute on a slow uplink and the redirect
    only came back when the participant already had their ticket.  The decision
    is synchronous; the participant's notification, the ticket and the sheet
    append are spawned and survive the response.
    """
    db = _db(request)
    bot = _bot(request)
    config = _config(request)
    lang = _lang(request)
    app_id = _int_or_404(request.match_info["id"])
    moderator = t(lang, "moderation.via_panel")
    app = await decisions.claim_approval(db, app_id, moderator)
    if app is not None:
        decisions.spawn(
            decisions.deliver_approval(
                bot, config, app, moderator=moderator, announce_in_chat=True
            )
        )
    raise web.HTTPFound(_url(request, f"/application/{app_id}"))


async def _reject(request: web.Request) -> web.Response:
    """Reject in SQLite, then deliver behind the redirect (see :func:`_approve`)."""
    db = _db(request)
    bot = _bot(request)
    config = _config(request)
    lang = _lang(request)
    app_id = _int_or_404(request.match_info["id"])
    moderator = t(lang, "moderation.via_panel")
    app = await decisions.claim_rejection(db, app_id, moderator)
    if app is not None:
        decisions.spawn(
            decisions.deliver_rejection(
                bot, config, app, moderator=moderator, announce_in_chat=True
            )
        )
    raise web.HTTPFound(_url(request, f"/application/{app_id}"))


async def _send_individual_message(request: web.Request) -> web.Response:
    db = _db(request)
    bot = _bot(request)
    app_id = _int_or_404(request.match_info["id"])
    app = await db.get_application(app_id)
    if app is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.app_not_found"))
    data = await request.post()
    text = str(data.get("text", "")).strip()
    if not text:
        raise web.HTTPFound(_url(request, f"/application/{app_id}?msg=empty"))
    if bot is None:
        raise web.HTTPFound(_url(request, f"/application/{app_id}?msg=blocked"))
    try:
        await bot.send_message(chat_id=app.user_id, text=text)
    except Exception as exc:  # noqa: BLE001 — Telegram blocks, deleted chats, etc.
        logger.warning("individual message to %s (app %s) failed: %s", app.user_id, app_id, exc)
        raise web.HTTPFound(_url(request, f"/application/{app_id}?msg=blocked"))
    raise web.HTTPFound(_url(request, f"/application/{app_id}?msg=sent"))


async def _change_status(request: web.Request) -> web.Response:
    db = _db(request)
    bot = _bot(request)
    config = _config(request)
    lang = _lang(request)
    app_id = _int_or_404(request.match_info["id"])
    app = await db.get_application(app_id)
    if app is None:
        raise web.HTTPNotFound(text=t(lang, "error.app_not_found"))
    data = await request.post()
    status = str(data.get("status", ""))
    if status not in _VALID_STATUSES or bot is None:
        raise web.HTTPFound(_url(request, f"/application/{app_id}?status_change=error"))
    moderator = t(lang, "moderation.via_panel")
    app = await decisions.claim_status(db, app_id, status, moderator)
    if app is not None:
        # Delivery (notification, ticket, sheet) runs behind the redirect: the
        # panel must not hang on a multi-megabyte upload.
        decisions.spawn(
            decisions.deliver_status(
                bot, config, app, status, moderator=moderator, announce_in_chat=True
            )
        )
    raise web.HTTPFound(_url(request, f"/application/{app_id}?status_change={'ok' if app is not None else 'error'}"))


async def _resend_ticket(request: web.Request) -> web.Response:
    """POST /application/{id}/ticket — regenerate and send the ticket again.

    Covers the "the picture never arrived" report without a developer: the
    panel regenerates the ticket with the current branding and reports the
    outcome on the application page.
    """
    db = _db(request)
    bot = _bot(request)
    config = _config(request)
    app_id = _int_or_404(request.match_info["id"])
    app = await db.get_application(app_id)
    if app is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.app_not_found"))

    result = await decisions.send_ticket(bot, config, app, report_failure=False)
    if result:
        raise web.HTTPFound(_url(request, f"/application/{app_id}?ticket=sent"))
    raise web.HTTPFound(
        _url(
            request,
            f"/application/{app_id}?ticket=failed&ticket_error={quote(str(result.error or ''))}",
        )
    )


async def _delete_application(request: web.Request) -> web.Response:
    """Remove one application permanently from the tenant's database.

    Testers need to run the whole form again and again, which is impossible
    while an approved application keeps answering ``/start`` with its old
    status.  This frees the person (and the registration number) right away;
    the participant photos on the volume are removed with it.
    """
    db = _db(request)
    lang = _lang(request)
    app_id = _int_or_404(request.match_info["id"])
    app = await db.get_application(app_id)
    if app is None:
        raise web.HTTPNotFound(text=t(lang, "error.app_not_found"))
    removed = await db.delete_application(app_id)
    if removed is None:
        raise web.HTTPNotFound(text=t(lang, "error.app_not_found"))
    logger.info(
        "Application %s (tenant %s) deleted via panel — plate %s, number %s",
        app_id,
        getattr(_config(request), "tenant_slug", "?"),
        removed.plate,
        removed.reg_number,
    )
    raise web.HTTPFound(_url(request, "/applications?deleted=1"))


async def _photo(request: web.Request) -> web.StreamResponse:
    return await _serve_photo(request, "photo_paths")


async def _mod_photo(request: web.Request) -> web.StreamResponse:
    """Serve one of the "what did you change?" close-ups."""
    return await _serve_photo(request, "mod_paths")


async def _badge_photo(request: web.Request) -> web.StreamResponse:
    db = _db(request)
    app_id = _int_or_404(request.match_info["id"])
    app = await db.get_application(app_id)
    path = getattr(app, "badge_photo_path", "") if app is not None else ""
    if app is None or not path:
        raise web.HTTPNotFound()
    if not os.path.exists(path):
        raise web.HTTPNotFound(text=t(_lang(request), "error.photo_not_on_disk"))
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


async def _serve_photo(request: web.Request, attr: str) -> web.StreamResponse:
    db = _db(request)
    app_id = _int_or_404(request.match_info["id"])
    idx = _int_or_404(request.match_info["idx"])
    app = await db.get_application(app_id)
    paths = getattr(app, attr, []) if app is not None else []
    if app is None or idx < 0 or idx >= len(paths):
        raise web.HTTPNotFound()
    path = paths[idx]
    if not os.path.exists(path):
        raise web.HTTPNotFound(text=t(_lang(request), "error.photo_not_on_disk"))
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


async def _export_csv(request: web.Request) -> web.Response:
    db = _db(request)
    lang = _lang(request)
    apps = await db.list_applications(limit=100000)
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens UTF-8 (Cyrillic) correctly
    writer = csv.writer(buf)
    writer.writerow(
        [t(lang, "csv.id"), t(lang, "csv.reg_number"), t(lang, "csv.status"),
         t(lang, "csv.country"), t(lang, "csv.plate"), t(lang, "csv.direction"),
         t(lang, "csv.phone"), t(lang, "csv.username"), t(lang, "csv.language"),
         t(lang, "csv.created_at"), t(lang, "csv.processed_at"), t(lang, "csv.processed_by")]
    )
    for a in apps:
        writer.writerow(
            [a.id, a.reg_number or "", a.status, a.country, a.plate, a.direction,
             a.phone, a.username, a.language, a.created_at, a.processed_at or "", a.processed_by or ""]
        )
    slug = getattr(_config(request), "tenant_slug", "applications")
    return web.Response(
        body=buf.getvalue().encode("utf-8"),
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": f'attachment; filename="{slug}_applications.csv"',
        },
    )


async def _export_excel(request: web.Request) -> web.Response:
    db = _db(request)
    apps = await db.list_applications(limit=100000)
    from ..services.excel import generate_excel

    xlsx_bytes = generate_excel(apps)
    slug = getattr(_config(request), "tenant_slug", "promotors")
    return web.Response(
        body=xlsx_bytes,
        headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": f'attachment; filename="{slug}_applications.xlsx"',
        },
    )


async def _broadcast_get(request: web.Request) -> web.Response:
    db = _db(request)
    counts = await db.audience_counts()
    return _html(request, views.broadcast_page(_lang(request), counts))


async def _broadcast_post(request: web.Request) -> web.Response:
    db = _db(request)
    bot = _bot(request)
    lang = _lang(request)
    data = await request.post()
    text_uz = str(data.get("text_uz", "")).strip()
    text_ru = str(data.get("text_ru", "")).strip()
    confirm = str(data.get("confirm", "")) == "1"
    action = str(data.get("action", "send"))
    audience = str(data.get("audience", "approved"))
    if audience not in _AUDIENCES:
        audience = "approved"

    # An empty selection means "don't filter by this" rather than "match nobody".
    langs = [v for v in data.getall("langs", []) if v in ("uz", "ru")] or None
    # Directions filter: accept any direction string (tenant-specific), not just global canon
    raw_dirs = [v for v in data.getall("directions", []) if v.strip()]
    directions = raw_dirs or None

    photo_uz_field = data.get("photo_uz")
    photo_ru_field = data.get("photo_ru")
    has_photo_uz = isinstance(photo_uz_field, web.FileField) and bool(photo_uz_field.filename)
    has_photo_ru = isinstance(photo_ru_field, web.FileField) and bool(photo_ru_field.filename)

    counts = await db.audience_counts()

    def page(
        error: str = "", result: Optional[dict] = None, preview: Optional[int] = None
    ) -> str:
        return views.broadcast_page(
            lang,
            counts,
            audience=audience,
            error=error,
            result=result,
            last_text_uz=text_uz,
            last_text_ru=text_ru,
            langs=langs,
            directions=directions,
            preview_count=preview,
        )

    if action == "preview":
        n = len(await db.recipients(audience, languages=langs, directions=directions))
        return _html(request, page(preview=n))

    if not text_uz and not text_ru:
        return _html(request, page(error=t(lang, "bcast.err_no_text")))
    if not confirm:
        return _html(request, page(error=t(lang, "bcast.err_no_confirm")))
    if bot is None:
        return _html(request, page(error=t(lang, "bcast.err_no_bot")), status=503)
    # Only one language filled in → everyone gets that text.
    body_uz = text_uz or text_ru
    body_ru = text_ru or text_uz

    # Each language keeps its own photo when one was uploaded; a language
    # left empty falls back to the other's — same rule as the text above.
    # ``source`` names which physical upload a language actually uses, so a
    # shared fallback photo is uploaded to Telegram once, not twice.
    photo_sources: dict[str, tuple[bytes, str]] = {}
    if has_photo_uz:
        photo_sources["uz"] = (photo_uz_field.file.read(), photo_uz_field.filename or "broadcast.jpg")
    if has_photo_ru:
        photo_sources["ru"] = (photo_ru_field.file.read(), photo_ru_field.filename or "broadcast.jpg")
    lang_photo_source = {
        "uz": "uz" if has_photo_uz else ("ru" if has_photo_ru else None),
        "ru": "ru" if has_photo_ru else ("uz" if has_photo_uz else None),
    }
    has_any_photo = bool(photo_sources)

    if lang_photo_source["uz"] and len(body_uz) > 1024:
        return _html(request, page(error=t(lang, "bcast.err_uz_too_long")))
    if lang_photo_source["ru"] and len(body_ru) > 1024:
        return _html(request, page(error=t(lang, "bcast.err_ru_too_long")))

    recipients = await db.recipients(audience, languages=langs, directions=directions)
    if not recipients:
        return _html(request, page(error=t(lang, "bcast.err_no_recipients")))

    # Each source photo is uploaded to Telegram once (on its first send) and
    # then reused by the returned file_id for every other recipient.
    photo_refs: dict[str, object] = {}

    def get_photo_ref(source_key: str):
        if source_key not in photo_refs:
            data_bytes, filename = photo_sources[source_key]
            photo_refs[source_key] = BufferedInputFile(data_bytes, filename=filename)
        return photo_refs[source_key]

    ok = fail = ok_uz = ok_ru = 0
    for user_id, lang in recipients:
        is_uz = str(lang or "").strip().lower().startswith("uz")
        lang_key = "uz" if is_uz else "ru"
        text = body_uz if is_uz else body_ru
        source_key = lang_photo_source[lang_key]
        try:
            if source_key is not None:
                sent = await bot.send_photo(
                    chat_id=user_id, photo=get_photo_ref(source_key), caption=text
                )
                photo_refs[source_key] = sent.photo[-1].file_id
            else:
                await bot.send_message(chat_id=user_id, text=text)
            ok += 1
            if is_uz:
                ok_uz += 1
            else:
                ok_ru += 1
        except Exception as exc:  # noqa: BLE001 — Telegram blocks, deleted chats, etc.
            fail += 1
            logger.warning("broadcast to %s failed: %s", user_id, exc)
        await asyncio.sleep(0.05)
    logger.info(
        "broadcast audience=%s langs=%s directions=%s ok=%s (uz=%s ru=%s) fail=%s total=%s",
        audience, langs, directions, ok, ok_uz, ok_ru, fail, len(recipients),
    )
    return _html(
        request,
        page(
            result={
                "ok": ok,
                "fail": fail,
                "ok_uz": ok_uz,
                "ok_ru": ok_ru,
                "total": len(recipients),
                "audience_label": t(lang, f"bcast.audience.{audience}"),
                "with_photo": has_any_photo,
            },
        ),
    )


# ---------------------------------------------------------------------------
# Ticket / sponsor management
# ---------------------------------------------------------------------------

def _direction_slugs() -> list[str]:
    return [d["slug"] for d in DIRECTIONS]


async def _ticket_assets(request: web.Request) -> web.Response:
    scope = _asset_scope(request)
    inv = assets.inventory(scope)
    sponsor_paths = assets.sponsor_files(scope)

    # Build detailed lists for the template
    sponsors_info = []
    for p in sponsor_paths:
        try:
            fname = os.path.basename(p)
            name_no_ext = os.path.splitext(fname)[0]
            stat = os.stat(p)
            sponsors_info.append({
                "path": p,
                "filename": fname,
                "name": name_no_ext,
                "size": stat.st_size,
                "is_runtime": bool(assets._runtime_dir("sponsors", scope) and p.startswith(assets._runtime_dir("sponsors", scope))),
            })
        except Exception:
            continue

    brand_info = {}
    for key in assets.BRAND_LOGOS:
        bpath = assets.brand_logo(key, scope)
        is_runtime = False
        if bpath:
            runtime_dir = assets._runtime_dir("brand", scope)
            is_runtime = bool(runtime_dir and bpath.startswith(runtime_dir))
        brand_info[key] = {
            "path": bpath,
            "exists": bool(bpath and os.path.exists(bpath)),
            "is_runtime": is_runtime,
        }

    direction_info = []
    for d in DIRECTIONS:
        slug = d["slug"]
        bpath = assets.direction_banner(slug, scope)
        is_runtime = False
        if bpath:
            runtime_dir = assets._runtime_dir("directions", scope)
            is_runtime = bool(runtime_dir and bpath.startswith(runtime_dir))
        direction_info.append({
            "slug": slug,
            "canonical": d["canonical"],
            "path": bpath,
            "exists": bool(bpath and os.path.exists(bpath)),
            "is_runtime": is_runtime,
        })

    msg = request.query.get("msg", "")
    err = request.query.get("error", "")
    cfg = _config(request)
    tenant_slug = getattr(cfg, "tenant_slug", None) or getattr(cfg, "asset_scope", None) or "promotors"
    return _html(
        request,
        views.ticket_assets_page(
            _lang(request),
            inventory=inv,
            sponsors=sponsors_info,
            brand=brand_info,
            directions=direction_info,
            message=msg,
            error=err,
            tenant_slug=tenant_slug,
        ),
    )


async def _ticket_preview(request: web.Request) -> web.Response:
    """Render a sample ticket with current assets (real logic = preview logic)."""
    try:
        from ..services.ticket import generate_ticket
        cfg = _config(request)
        png = await run_heavy(
            generate_ticket,
            _asset_scope(request),
            number=1,
            plate="01A777AA",
            direction="Adrenaline Drift",
            name="Test User",
            tenant_name=getattr(cfg, "tenant_name", ""),
            lang="ru",
            hero_image_path=None,
            tenant_config=cfg,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("ticket preview failed: %s", exc)
        raise web.HTTPInternalServerError(text=f"Preview failed: {exc}")
    return web.Response(
        body=png,
        headers={
            "Content-Type": "image/png",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


async def _asset_file(request: web.Request) -> web.StreamResponse:
    scope = _asset_scope(request)
    kind = request.match_info["kind"]
    filename = request.match_info["filename"]
    # Basic sanitization: no path traversal
    if ".." in filename or "/" in filename or "\\" in filename:
        raise web.HTTPNotFound()
    if kind not in ("brand", "sponsors", "directions"):
        raise web.HTTPNotFound()

    path = None
    if kind == "brand":
        # filename can be "logo", "adrenaline" or "logo.png"
        name = os.path.splitext(filename)[0]
        if name not in assets.BRAND_LOGOS:
            # also allow direct filename match
            if filename not in (assets.BRAND_LOGOS.get("logo"), assets.BRAND_LOGOS.get("adrenaline")):
                raise web.HTTPNotFound()
            # resolve via bundled root fallback
            from ..services.assets import _BUNDLED_ROOT
            candidate = os.path.join(_BUNDLED_ROOT, filename)
            if os.path.exists(candidate):
                path = candidate
        if path is None:
            path = assets.brand_logo(name, scope)
    elif kind == "sponsors":
        # Look for exact filename in sponsors dirs
        for d in assets.sponsors_dirs(scope):
            cand = os.path.join(d, filename)
            if os.path.exists(cand):
                path = cand
                break
        # Also allow name without extension
        if path is None:
            name_no_ext = os.path.splitext(filename)[0]
            if assets.is_safe_name(name_no_ext):
                for d in assets.sponsors_dirs(scope):
                    for ext in assets._IMAGE_EXTS:
                        cand = os.path.join(d, name_no_ext + ext)
                        if os.path.exists(cand):
                            path = cand
                            break
                    if path:
                        break
    else:  # directions
        slug = os.path.splitext(filename)[0]
        path = assets.direction_banner(slug, scope)

    if not path or not os.path.exists(path):
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


def _read_upload_file(field) -> bytes | None:
    if not isinstance(field, web.FileField):
        return None
    if not field.filename:
        return None
    try:
        return field.file.read()
    except Exception:
        return None


async def _brand_upload(request: web.Request) -> web.Response:
    data = await request.post()
    brand_name = str(data.get("brand_name", "")).strip()
    file_field = data.get("file")
    file_bytes = _read_upload_file(file_field)

    if brand_name not in assets.BRAND_LOGOS:
        raise web.HTTPFound(_url(request, "/ticket-assets?error=unknown_brand"))
    if not file_bytes:
        raise web.HTTPFound(_url(request, "/ticket-assets?error=no_file"))

    try:
        assets.save_brand(brand_name, file_bytes, _asset_scope(request))
    except Exception as exc:
        logger.exception("brand upload failed")
        raise web.HTTPFound(_url(request, f"/ticket-assets?error={exc}"))
    raise web.HTTPFound(_url(request, "/ticket-assets?msg=brand_uploaded"))


async def _brand_delete(request: web.Request) -> web.Response:
    data = await request.post()
    brand_name = str(data.get("brand_name", "")).strip()
    if brand_name in assets.BRAND_LOGOS:
        assets.delete_asset("brand", brand_name, _asset_scope(request))
    raise web.HTTPFound(_url(request, "/ticket-assets?msg=brand_deleted"))


async def _sponsor_upload(request: web.Request) -> web.Response:
    data = await request.post()
    name = str(data.get("name", "")).strip()
    file_field = data.get("file")
    file_bytes = _read_upload_file(file_field)

    if not name:
        raise web.HTTPFound(_url(request, "/ticket-assets?error=name_required"))
    if not assets.is_safe_name(name):
        raise web.HTTPFound(_url(request, "/ticket-assets?error=invalid_name"))
    if not file_bytes:
        raise web.HTTPFound(_url(request, "/ticket-assets?error=no_file"))

    try:
        assets.save_sponsor(name, file_bytes, _asset_scope(request))
    except Exception as exc:
        logger.exception("sponsor upload failed")
        raise web.HTTPFound(_url(request, f"/ticket-assets?error={exc}"))
    raise web.HTTPFound(_url(request, "/ticket-assets?msg=sponsor_uploaded"))


async def _sponsor_delete(request: web.Request) -> web.Response:
    data = await request.post()
    name = str(data.get("name", "")).strip()
    # Allow passing filename with extension: strip it
    name = os.path.splitext(name)[0]
    if assets.is_safe_name(name):
        assets.delete_asset("sponsors", name, _asset_scope(request))
    raise web.HTTPFound(_url(request, "/ticket-assets?msg=sponsor_deleted"))


async def _direction_upload(request: web.Request) -> web.Response:
    data = await request.post()
    slug = str(data.get("slug", "")).strip()
    file_field = data.get("file")
    file_bytes = _read_upload_file(file_field)

    if slug not in _direction_slugs():
        raise web.HTTPFound(_url(request, "/ticket-assets?error=unknown_direction"))
    if not file_bytes:
        raise web.HTTPFound(_url(request, "/ticket-assets?error=no_file"))

    try:
        assets.save_direction(slug, file_bytes, _asset_scope(request))
    except Exception as exc:
        logger.exception("direction upload failed")
        raise web.HTTPFound(_url(request, f"/ticket-assets?error={exc}"))
    raise web.HTTPFound(_url(request, "/ticket-assets?msg=direction_uploaded"))


async def _direction_delete(request: web.Request) -> web.Response:
    data = await request.post()
    slug = str(data.get("slug", "")).strip()
    if slug in _direction_slugs():
        assets.delete_asset("directions", slug, _asset_scope(request))
    raise web.HTTPFound(_url(request, "/ticket-assets?msg=direction_deleted"))


# ---------------------------------------------------------------------------
# Super-admin control plane and tenant settings
# ---------------------------------------------------------------------------


def _tenant_form_values(data, *, editing: bool = False) -> dict[str, Any]:
    """Convert multipart/form values into the database's explicit field set."""
    raw_chat = str(data.get("admin_chat_id", "")).strip()
    try:
        admin_chat_id = int(raw_chat or 0)
    except ValueError as exc:
        raise ValueError("Admin chat ID must be an integer") from exc
    values: dict[str, Any] = {
        "name": str(data.get("name", "")).strip(),
        "admin_chat_id": admin_chat_id,
        "required_channel": str(data.get("required_channel", "")).strip(),
        "channel_url": str(data.get("channel_url", "")).strip(),
        "instagram_handle": str(data.get("instagram_handle", "")).strip(),
        "instagram_url": str(data.get("instagram_url", "")).strip(),
        "spreadsheet_id": str(data.get("spreadsheet_id", "")).strip(),
        "drive_folder_id": str(data.get("drive_folder_id", "")).strip(),
        "is_active": str(data.get("is_active", "")) in {"1", "true", "on"},
        "event_date_text_ru": str(data.get("event_date_text_ru", "")).strip(),
        "event_date_text_uz": str(data.get("event_date_text_uz", "")).strip(),
        "event_venue_text_ru": str(data.get("event_venue_text_ru", "")).strip(),
        "event_venue_text_uz": str(data.get("event_venue_text_uz", "")).strip(),
        "event_guest_date_text_ru": str(data.get("event_guest_date_text_ru", "")).strip(),
        "event_guest_date_text_uz": str(data.get("event_guest_date_text_uz", "")).strip(),
        "event_note_text_ru": str(data.get("event_note_text_ru", "")).strip(),
        "event_note_text_uz": str(data.get("event_note_text_uz", "")).strip(),
        # Unchecked checkbox is omitted from the POST, which means registration is open.
        "registration_closed": str(data.get("registration_closed", "")) in {"1", "true", "on"},
    }
    token = str(data.get("bot_token", "")).strip()
    password = str(data.get("admin_password", "")).strip()
    if editing:
        # Blank fields on edit intentionally retain secrets; tokens must never
        # be echoed back into a form and passwords need not be re-entered.
        values["bot_token"] = token if token else None
        values["admin_password"] = password if password else None
    else:
        values["bot_token"] = token
        values["admin_password"] = password
    return values


async def _maybe_restart_tenant(request: web.Request, tenant: Tenant) -> None:
    manager = request.app.get("bot_manager")
    if manager is not None:
        await manager.restart_tenant(tenant.id)


async def _super_dashboard(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    tenants = await db.list_tenants()
    counts = await db.tenant_application_counts()
    return _html(request, views.super_dashboard_page(_lang(request), tenants, counts))


async def _super_tenant_new_get(request: web.Request) -> web.Response:
    return _html(request, views.super_tenant_form_page(_lang(request)))


async def _super_tenant_new_post(request: web.Request) -> web.Response:
    data = await request.post()
    try:
        values = _tenant_form_values(data)
        tenant = await request.app["db"].create_tenant(
            slug=str(data.get("slug", "")).strip(), **values
        )
        await _maybe_restart_tenant(request, tenant)
    except (ValueError, EncryptionError) as exc:
        return _html(
            request,
            views.super_tenant_form_page(
                _lang(request), values=dict(data), error=_form_error_text(_lang(request), exc)
            ),
            status=400,
        )
    raise web.HTTPFound("/super-admin/")


async def _super_tenant_edit_get(request: web.Request) -> web.Response:
    tenant = await request.app["db"].get_tenant(request.match_info["slug"])
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    return _html(request, views.super_tenant_form_page(_lang(request), tenant=tenant))


async def _super_tenant_edit_post(request: web.Request) -> web.Response:
    slug = request.match_info["slug"]
    lang = _lang(request)
    data = await request.post()
    try:
        values = _tenant_form_values(data, editing=True)
        tenant = await request.app["db"].update_tenant(slug, **values)
        if tenant is None:
            raise web.HTTPNotFound(text=t(lang, "error.tenant_not_found"))
        await _maybe_restart_tenant(request, tenant)
    except (ValueError, EncryptionError) as exc:
        existing = await request.app["db"].get_tenant(slug)
        return _html(
            request,
            views.super_tenant_form_page(
                lang, tenant=existing, values=dict(data), error=_form_error_text(lang, exc)
            ),
            status=400,
        )
    raise web.HTTPFound("/super-admin/")


async def _super_tenant_toggle(request: web.Request) -> web.Response:
    slug = request.match_info["slug"]
    tenant = await request.app["db"].get_tenant(slug)
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    updated = await request.app["db"].update_tenant(tenant.id, is_active=not tenant.is_active)
    if updated:
        await _maybe_restart_tenant(request, updated)
    raise web.HTTPFound("/super-admin/")


async def _super_tenant_archive(request: web.Request) -> web.Response:
    slug = request.match_info["slug"]
    archived = await request.app["db"].archive_tenant(slug)
    if archived:
        tenant = await request.app["db"].get_tenant(slug)
        if tenant:
            await _maybe_restart_tenant(request, tenant)
    raise web.HTTPFound("/super-admin/")


async def _super_tenant_restart(request: web.Request) -> web.Response:
    slug = request.match_info["slug"]
    tenant = await request.app["db"].get_tenant(slug)
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    await _maybe_restart_tenant(request, tenant)
    raise web.HTTPFound(f"/super-admin/tenants/{tenant.slug}/edit")


async def _super_tenant_diag(request: web.Request) -> web.Response:
    """Run non-destructive Telegram diagnostics for one tenant configuration."""
    tenant = await request.app["db"].get_tenant(request.match_info["slug"])
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    lang = _lang(request)
    checks: list[tuple[str, bool, str]] = []
    bot = None
    me = None
    try:
        token = await request.app["db"].get_tenant_token(tenant.id)
        if not token:
            checks.append((t(lang, "diag.check.bot_token"), False, t(lang, "diag.detail.token_missing")))
        else:
            bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            try:
                me = await bot.get_me()
                checks.append(
                    (t(lang, "diag.check.bot_token"), True, f"@{me.username or me.id}")
                )
            except Exception as exc:  # noqa: BLE001
                checks.append(
                    (t(lang, "diag.check.bot_token"), False, f"{type(exc).__name__}: {exc}")
                )

            if tenant.required_channel and me is not None:
                try:
                    channel = subscription.normalize_channel(tenant.required_channel)
                    chat = await bot.get_chat(channel)
                    checks.append(
                        (t(lang, "diag.check.required_channel"), True, f"{chat.title or chat.id}")
                    )
                    member = await bot.get_chat_member(channel, me.id)
                    status = getattr(member.status, "value", member.status)
                    is_admin = str(status) in {"administrator", "creator", "owner"}
                    checks.append(
                        (t(lang, "diag.check.channel_admin"), is_admin,
                         t(lang, "diag.detail.status", status=status))
                    )
                except Exception as exc:  # noqa: BLE001
                    checks.append(
                        (t(lang, "diag.check.required_channel"), False,
                         f"{type(exc).__name__}: {exc}")
                    )
            elif not tenant.required_channel:
                checks.append(
                    (t(lang, "diag.check.required_channel"), False,
                     t(lang, "diag.detail.not_configured"))
                )

            if tenant.admin_chat_id and bot is not None:
                try:
                    chat = await bot.get_chat(tenant.admin_chat_id)
                    checks.append(
                        (t(lang, "diag.check.moderation_chat"), True, f"{chat.title or chat.id}")
                    )
                except Exception as exc:  # noqa: BLE001
                    checks.append(
                        (t(lang, "diag.check.moderation_chat"), False,
                         f"{type(exc).__name__}: {exc}")
                    )
            elif not tenant.admin_chat_id:
                checks.append(
                    (t(lang, "diag.check.moderation_chat"), False,
                     t(lang, "diag.detail.not_configured"))
                )
    except Exception as exc:  # noqa: BLE001 - encrypted-token failures are reportable too
        checks.append(
            (t(lang, "diag.check.diagnostics"), False, f"{type(exc).__name__}: {exc}")
        )
    finally:
        session = getattr(bot, "session", None)
        if session is not None:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass
    return _html(request, views.tenant_diag_page(lang, tenant, checks))


async def _tenant_settings_get(request: web.Request) -> web.Response:
    tenant = request.get("tenant") or await request.app["db"].get_tenant("promotors")
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    return _html(
        request,
        views.tenant_settings_page(
            _lang(request), tenant, message=request.query.get("msg", "")
        ),
    )


async def _tenant_settings_post(request: web.Request) -> web.Response:
    tenant = request.get("tenant") or await request.app["db"].get_tenant("promotors")
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    data = await request.post()
    try:
        values = _tenant_form_values(data, editing=True)
        # A tenant admin can manage presentation/integration settings, but not
        # its Telegram token or activation state. Those are super-admin only.
        values.pop("bot_token", None)
        values.pop("is_active", None)
        updated = await request.app["db"].update_tenant(tenant.id, **values)
        if updated is not None:
            await _maybe_restart_tenant(request, updated)
    except ValueError as exc:
        return _html(
            request,
            views.tenant_settings_page(
                _lang(request), tenant, error=_form_error_text(_lang(request), exc)
            ),
            status=400,
        )
    raise web.HTTPFound(_url(request, "/settings?msg=saved"))


# ---------------------------------------------------------------------------
# Tenant directions CRUD (super-admin)
# ---------------------------------------------------------------------------

def _direction_form_values(data, existing=None) -> dict:
    """Parse direction form."""
    canonical = str(data.get("canonical", "")).strip()
    label_ru = str(data.get("label_ru", "")).strip()
    label_uz = str(data.get("label_uz", "")).strip()
    slug = str(data.get("slug", "")).strip()
    raw_parent = str(data.get("parent_id", "")).strip()
    parent_id = None
    if raw_parent:
        try:
            parent_id = int(raw_parent)
        except ValueError:
            raise ValueError("parent_id must be integer")
    raw_sort = str(data.get("sort_order", "0")).strip()
    try:
        sort_order = int(raw_sort or 0)
    except ValueError:
        sort_order = 0
    is_active = str(data.get("is_active", "")) in {"1", "true", "on"}
    if not canonical:
        raise ValueError("canonical required")
    if not slug:
        # auto from canonical
        slug = canonical.lower().replace(" ", "_")[:40]
    return {
        "canonical": canonical,
        "label_ru": label_ru or canonical,
        "label_uz": label_uz or canonical,
        "slug": slug,
        "parent_id": parent_id,
        "sort_order": sort_order,
        "is_active": is_active,
    }

async def _super_tenant_directions(request: web.Request) -> web.Response:
    db = request.app["db"]
    tenant = await db.get_tenant(request.match_info["slug"])
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    directions = await db.list_directions(tenant_id=tenant.id, active_only=False)
    return _html(request, views.super_tenant_directions_page(_lang(request), tenant, directions))

async def _super_direction_new_get(request: web.Request) -> web.Response:
    db = request.app["db"]
    tenant = await db.get_tenant(request.match_info["slug"])
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    all_dirs = await db.list_directions(tenant_id=tenant.id, active_only=False)
    # Only roots can be parents (enforce 2-level)
    parents = [d for d in all_dirs if d.parent_id is None]
    return _html(request, views.super_direction_form_page(_lang(request), tenant, parents=parents))

async def _super_direction_new_post(request: web.Request) -> web.Response:
    db = request.app["db"]
    tenant = await db.get_tenant(request.match_info["slug"])
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    data = await request.post()
    lang = _lang(request)
    try:
        vals = _direction_form_values(data)
        await db.create_direction(tenant_id=tenant.id, **vals)
    except ValueError as exc:
        all_dirs = await db.list_directions(tenant_id=tenant.id, active_only=False)
        parents = [d for d in all_dirs if d.parent_id is None]
        return _html(
            request,
            views.super_direction_form_page(
                lang, tenant, values=dict(data), parents=parents, error=t(lang, "tenant.direction.form.err_generic", detail=str(exc))
            ),
            status=400,
        )
    raise web.HTTPFound(f"/super-admin/tenants/{tenant.slug}/directions")

async def _super_direction_edit_get(request: web.Request) -> web.Response:
    db = request.app["db"]
    tenant = await db.get_tenant(request.match_info["slug"])
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    dir_id = int(request.match_info["direction_id"])
    direction = await db.get_direction(dir_id, tenant_id=tenant.id)
    if direction is None:
        raise web.HTTPNotFound(text="Direction not found")
    all_dirs = await db.list_directions(tenant_id=tenant.id, active_only=False)
    parents = [d for d in all_dirs if d.parent_id is None and d.id != direction.id]
    return _html(request, views.super_direction_form_page(_lang(request), tenant, direction=direction, parents=parents))

async def _super_direction_edit_post(request: web.Request) -> web.Response:
    db = request.app["db"]
    tenant = await db.get_tenant(request.match_info["slug"])
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    dir_id = int(request.match_info["direction_id"])
    data = await request.post()
    lang = _lang(request)
    try:
        vals = _direction_form_values(data)
        await db.update_direction(dir_id, tenant_id=tenant.id, **vals)
    except ValueError as exc:
        all_dirs = await db.list_directions(tenant_id=tenant.id, active_only=False)
        parents = [d for d in all_dirs if d.parent_id is None and d.id != dir_id]
        direction = await db.get_direction(dir_id, tenant_id=tenant.id)
        return _html(
            request,
            views.super_direction_form_page(
                lang, tenant, direction=direction, values=dict(data), parents=parents, error=t(lang, "tenant.direction.form.err_generic", detail=str(exc))
            ),
            status=400,
        )
    raise web.HTTPFound(f"/super-admin/tenants/{tenant.slug}/directions")

async def _super_direction_delete(request: web.Request) -> web.Response:
    db = request.app["db"]
    tenant = await db.get_tenant(request.match_info["slug"])
    if tenant is None:
        raise web.HTTPNotFound(text=t(_lang(request), "error.tenant_not_found"))
    dir_id = int(request.match_info["direction_id"])
    await db.delete_direction(dir_id, tenant_id=tenant.id)
    raise web.HTTPFound(f"/super-admin/tenants/{tenant.slug}/directions")


def _int_or_404(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise web.HTTPNotFound()
