"""aiohttp web app for the admin panel.

Runs in the same process/event loop as the bot, so it shares the SQLite
Database instance and the Bot instance (to notify applicants on decisions).
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
from typing import Optional

from aiohttp import web

from aiogram.types import BufferedInputFile

from ..config import Config
from ..constants import DIRECTIONS_CANON
from ..db import Database, STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED
from ..services import decisions
from . import auth, views

logger = logging.getLogger(__name__)

_VALID_STATUSES = {STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED}
_PUBLIC_PATHS = {"/login", "/health"}
_AUDIENCES = {
    "approved",
    "pending",
    "rejected",
    "incomplete",
    "all_apps",
    "starters",
}
_AUDIENCE_LABELS = {
    "approved": "Одобрено",
    "pending": "На рассмотрении",
    "rejected": "Отклонено",
    "incomplete": "Не завершили регистрацию",
    "all_apps": "Все заявки",
    "starters": "Все, кого бот знает",
}


_MAX_UPLOAD_SIZE = 20 * 1024 * 1024  # Telegram photos can run a few MB.


def create_admin_app(bot, config: Config, db: Database) -> web.Application:
    app = web.Application(
        middlewares=[_auth_middleware], client_max_size=_MAX_UPLOAD_SIZE
    )
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
    app.router.add_post("/application/{id}/message", _send_individual_message)
    app.router.add_get("/photo/{id}/{idx}", _photo)
    app.router.add_get("/modphoto/{id}/{idx}", _mod_photo)
    app.router.add_get("/badgephoto/{id}", _badge_photo)
    app.router.add_get("/export.csv", _export_csv)
    app.router.add_get("/export.xlsx", _export_excel)
    app.router.add_get("/broadcast", _broadcast_get)
    app.router.add_post("/broadcast", _broadcast_post)
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
    msg = request.query.get("msg")
    return web.Response(
        text=views.application_detail_page(
            app,
            msg_sent=msg == "sent",
            msg_error="Не удалось отправить — пользователь заблокировал бота" if msg == "blocked" else (
                "Введите текст сообщения" if msg == "empty" else ""
            ),
        ),
        content_type="text/html",
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


async def _send_individual_message(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    bot = request.app["bot"]
    app_id = _int_or_404(request.match_info["id"])
    app = await db.get_application(app_id)
    if app is None:
        raise web.HTTPNotFound(text="Заявка не найдена")
    data = await request.post()
    text = str(data.get("text", "")).strip()
    if not text:
        raise web.HTTPFound(f"/application/{app_id}?msg=empty")
    if bot is None:
        raise web.HTTPFound(f"/application/{app_id}?msg=blocked")
    try:
        await bot.send_message(chat_id=app.user_id, text=text)
    except Exception as exc:  # noqa: BLE001 — Telegram blocks, deleted chats, etc.
        logger.warning("individual message to %s (app %s) failed: %s", app.user_id, app_id, exc)
        raise web.HTTPFound(f"/application/{app_id}?msg=blocked")
    raise web.HTTPFound(f"/application/{app_id}?msg=sent")


async def _photo(request: web.Request) -> web.StreamResponse:
    return await _serve_photo(request, "photo_paths")


async def _mod_photo(request: web.Request) -> web.StreamResponse:
    """Serve one of the "what did you change?" close-ups."""
    return await _serve_photo(request, "mod_paths")


async def _badge_photo(request: web.Request) -> web.StreamResponse:
    db: Database = request.app["db"]
    app_id = _int_or_404(request.match_info["id"])
    app = await db.get_application(app_id)
    path = getattr(app, "badge_photo_path", "") if app is not None else ""
    if app is None or not path:
        raise web.HTTPNotFound()
    if not os.path.exists(path):
        raise web.HTTPNotFound(text="Фото не найдено на диске")
    return web.FileResponse(path, headers={"Cache-Control": "private, max-age=3600"})


async def _serve_photo(request: web.Request, attr: str) -> web.StreamResponse:
    db: Database = request.app["db"]
    app_id = _int_or_404(request.match_info["id"])
    idx = _int_or_404(request.match_info["idx"])
    app = await db.get_application(app_id)
    paths = getattr(app, attr, []) if app is not None else []
    if app is None or idx < 0 or idx >= len(paths):
        raise web.HTTPNotFound()
    path = paths[idx]
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


async def _export_excel(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    apps = await db.list_applications(limit=100000)
    from ..services.excel import generate_excel

    xlsx_bytes = generate_excel(apps)
    return web.Response(
        body=xlsx_bytes,
        headers={
            "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "Content-Disposition": 'attachment; filename="promotors_applications.xlsx"',
        },
    )


async def _broadcast_get(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    counts = await db.audience_counts()
    return web.Response(
        text=views.broadcast_page(counts), content_type="text/html"
    )


async def _broadcast_post(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    bot = request.app["bot"]
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
    directions = [v for v in data.getall("directions", []) if v in DIRECTIONS_CANON] or None

    photo_uz_field = data.get("photo_uz")
    photo_ru_field = data.get("photo_ru")
    has_photo_uz = isinstance(photo_uz_field, web.FileField) and bool(photo_uz_field.filename)
    has_photo_ru = isinstance(photo_ru_field, web.FileField) and bool(photo_ru_field.filename)

    counts = await db.audience_counts()

    def page(
        error: str = "", result: Optional[dict] = None, preview: Optional[int] = None
    ) -> str:
        return views.broadcast_page(
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
        return web.Response(text=page(preview=n), content_type="text/html")

    if not text_uz and not text_ru:
        return web.Response(
            text=page(error="Введите текст хотя бы на одном языке"),
            content_type="text/html",
        )
    if not confirm:
        return web.Response(
            text=page(error="Подтвердите отправку галочкой"),
            content_type="text/html",
        )
    if bot is None:
        return web.Response(
            text=page(error="Бот недоступен — рассылка невозможна"),
            content_type="text/html",
            status=503,
        )
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
        return web.Response(
            text=page(
                error="Текст на узбекском слишком длинный для сообщения с фото — "
                "у Telegram лимит подписи 1024 символа"
            ),
            content_type="text/html",
        )
    if lang_photo_source["ru"] and len(body_ru) > 1024:
        return web.Response(
            text=page(
                error="Текст на русском слишком длинный для сообщения с фото — "
                "у Telegram лимит подписи 1024 символа"
            ),
            content_type="text/html",
        )

    recipients = await db.recipients(audience, languages=langs, directions=directions)
    if not recipients:
        return web.Response(
            text=page(error="По выбранным фильтрам получателей не найдено"),
            content_type="text/html",
        )

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
    return web.Response(
        text=page(
            result={
                "ok": ok,
                "fail": fail,
                "ok_uz": ok_uz,
                "ok_ru": ok_ru,
                "total": len(recipients),
                "audience_label": _AUDIENCE_LABELS.get(audience, audience),
                "with_photo": has_any_photo,
            },
        ),
        content_type="text/html",
    )


def _int_or_404(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise web.HTTPNotFound()
