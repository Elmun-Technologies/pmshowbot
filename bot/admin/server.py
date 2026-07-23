"""aiohttp web app for the admin panel.

Runs in the same process/event loop as the bot, so it shares the SQLite
Database instance and the Bot instance (to notify applicants on decisions).
"""
from __future__ import annotations

import csv
import io
import logging
import os

from aiohttp import web

from ..config import Config
from ..db import Database, STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED
from ..services import decisions
from . import auth, views

logger = logging.getLogger(__name__)

_VALID_STATUSES = {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED}
_PUBLIC_PATHS = {"/login", "/health"}


def create_admin_app(bot, config: Config, db: Database) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware])
    app["bot"] = bot
    app["config"] = config
    app["db"] = db

    app.router.add_get("/health", _health)
    app.router.add_get("/login", _login_get)
    app.router.add_post("/login", _login_post)
    app.router.add_get("/logout", _logout)
    app.router.add_get("/", _dashboard)
    app.router.add_get("/applications", _applications)
    app.router.add_get("/application/{id}", _application_detail)
    app.router.add_post("/application/{id}/approve", _approve)
    app.router.add_post("/application/{id}/reject", _reject)
    app.router.add_get("/photo/{id}/{idx}", _photo)
    app.router.add_get("/export.csv", _export_csv)
    return app


@web.middleware
async def _auth_middleware(request: web.Request, handler):
    config: Config = request.app["config"]
    if request.path in _PUBLIC_PATHS:
        return await handler(request)
    if not config.admin_password:
        return web.Response(
            text=views.panel_disabled_page(), content_type="text/html", status=503
        )
    cookie = request.cookies.get(auth.COOKIE_NAME)
    if not auth.valid_cookie(config.admin_password, cookie):
        raise web.HTTPFound("/login")
    return await handler(request)


async def _health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


async def _login_get(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    if not config.admin_password:
        return web.Response(
            text=views.panel_disabled_page(), content_type="text/html", status=503
        )
    error = request.query.get("error") == "1"
    return web.Response(text=views.login_page(error), content_type="text/html")


async def _login_post(request: web.Request) -> web.Response:
    config: Config = request.app["config"]
    data = await request.post()
    submitted = str(data.get("password", ""))
    if config.admin_password and auth.password_matches(config.admin_password, submitted):
        resp = web.HTTPFound("/")
        resp.set_cookie(
            auth.COOKIE_NAME,
            auth.make_cookie(config.admin_password),
            max_age=auth.MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=True,
        )
        raise resp
    raise web.HTTPFound("/login?error=1")


async def _logout(request: web.Request) -> web.Response:
    resp = web.HTTPFound("/login")
    resp.del_cookie(auth.COOKIE_NAME)
    raise resp


async def _dashboard(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    stats = await db.stats()
    return web.Response(text=views.dashboard_page(stats), content_type="text/html")


async def _applications(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    status = request.query.get("status")
    if status not in _VALID_STATUSES:
        status = None
    search = request.query.get("search", "").strip()
    apps = await db.list_applications(status=status, search=search or None)
    return web.Response(
        text=views.applications_page(apps, status, search), content_type="text/html"
    )


async def _application_detail(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    app_id = _int_or_404(request.match_info["id"])
    app = await db.get_application(app_id)
    if app is None:
        raise web.HTTPNotFound(text="Заявка не найдена")
    return web.Response(
        text=views.application_detail_page(app), content_type="text/html"
    )


async def _approve(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    bot = request.app["bot"]
    config: Config = request.app["config"]
    app_id = _int_or_404(request.match_info["id"])
    await decisions.approve_application(bot, config, db, app_id, moderator="админ-панель")
    raise web.HTTPFound(f"/application/{app_id}")


async def _reject(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    bot = request.app["bot"]
    config: Config = request.app["config"]
    app_id = _int_or_404(request.match_info["id"])
    await decisions.reject_application(bot, config, db, app_id, moderator="админ-панель")
    raise web.HTTPFound(f"/application/{app_id}")


async def _photo(request: web.Request) -> web.StreamResponse:
    db: Database = request.app["db"]
    app_id = _int_or_404(request.match_info["id"])
    idx = _int_or_404(request.match_info["idx"])
    app = await db.get_application(app_id)
    if app is None or idx < 0 or idx >= len(app.photo_paths):
        raise web.HTTPNotFound()
    path = app.photo_paths[idx]
    if not os.path.exists(path):
        raise web.HTTPNotFound(text="Фото не найдено на диске")
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


async def _export_csv(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    apps = await db.list_applications(limit=100000)
    buf = io.StringIO()
    buf.write("﻿")  # BOM so Excel opens UTF-8 (Cyrillic) correctly
    writer = csv.writer(buf)
    writer.writerow(
        ["ID", "Рег. номер", "Статус", "Страна", "Гос. номер", "Направление",
         "Телефон", "Пользователь", "Язык", "Подана", "Обработана", "Кто обработал"]
    )
    for a in apps:
        writer.writerow(
            [a.id, a.reg_number or "", a.status, a.country, a.plate, a.direction,
             a.phone, a.username, a.language, a.created_at, a.processed_at or "", a.processed_by or ""]
        )
    return web.Response(
        body=buf.getvalue().encode("utf-8"),
        headers={
            "Content-Type": "text/csv; charset=utf-8",
            "Content-Disposition": 'attachment; filename="applications.csv"',
        },
    )


def _int_or_404(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise web.HTTPNotFound()
