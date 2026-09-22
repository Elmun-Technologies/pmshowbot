"""Approve / reject business logic shared by the Telegram moderation buttons
and the web admin panel.

Both entry points assign the registration number, notify the applicant over
Telegram, and (on approval) export to Google in the background. The only thing
that differs is the surrounding UI (editing the Telegram card vs an HTTP
redirect), which stays in the respective callers.

Tenant-branding: uses TenantConfig for channel_url, event dates/venue,
tenant_name for ticket wordmark, and tenant-branded texts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import BufferedInputFile

from .. import keyboards, texts
from ..config import Config, TenantConfig
from ..constants import SIDES, SIDE_LABELS_TRANSLIT
from ..db import (
    Application,
    Database,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from . import drive, sheets, subscription
from .ticket import generate_ticket

logger = logging.getLogger(__name__)


def _pick_hero(photo_paths: list[str]) -> Optional[str]:
    """Choose the poster background: prefer the front shot, then back, then any."""
    for idx in (2, 3, 0, 1):
        if idx < len(photo_paths):
            p = photo_paths[idx]
            if p and os.path.exists(p):
                return p
    return None


def _get_display_name(app: Application) -> str:
    if getattr(app, "full_name", "") and app.full_name.strip():
        return app.full_name.strip()
    if app.username:
        u = app.username.split(" (id ")[0].strip()
        if u.startswith("@"):
            u = u[1:]
        return u
    return ""


async def send_ticket(bot: Bot, config: Config | TenantConfig, app: Application) -> None:
    """Render and send the shareable Stories ticket, then a short share message."""
    try:
        tenant_scope = getattr(config, "asset_scope", None) or getattr(config, "tenant_slug", None)
        tenant_name = getattr(config, "tenant_name", "")
        # Pass full tenant config for date/venue branding
        png = await asyncio.to_thread(
            generate_ticket,
            tenant_scope,
            number=app.reg_number,
            plate=app.plate,
            direction=texts.localize_direction(app.direction, app.language),
            name=_get_display_name(app),
            tenant_name=tenant_name,
            lang=app.language,
            hero_image_path=_pick_hero(app.photo_paths),
            tenant_config=config,
        )
        await bot.send_photo(
            app.user_id,
            BufferedInputFile(png, filename=f"ticket_{app.reg_number}.png"),
        )
        # Share CTA tenant-branded
        share_text = texts.share_cta_for_tenant(app.language, config)
        await bot.send_message(app.user_id, share_text)
    except TelegramForbiddenError:
        logger.warning("Could not send ticket to user %s (bot blocked?)", app.user_id)
    except Exception:
        logger.exception("Failed to generate/send ticket for application %s", app.id)


async def notify_applicant(bot: Bot, user_id: int, text: str, lang: str = "ru") -> None:
    try:
        await bot.send_message(user_id, text, reply_markup=keyboards.main_menu_keyboard(lang))
    except TelegramForbiddenError:
        logger.warning("Could not notify user %s (bot blocked?)", user_id)
    except Exception:
        logger.exception("Failed to notify user %s", user_id)


async def export_to_google(config: Config | TenantConfig, app: Application) -> None:
    """Upload photos to Drive and append the row to Sheets. Best-effort."""
    photo_urls: list[str] = []
    mod_urls: list[str] = []
    try:
        if config.drive_enabled and app.photo_paths:
            files = [
                (path, f"{app.reg_number}_{SIDES[i]}_{SIDE_LABELS_TRANSLIT.get(SIDES[i], SIDES[i])}.jpg")
                for i, path in enumerate(app.photo_paths)
                if i < len(SIDES)
            ]
            photo_urls = await drive.upload_photos(
                config.google_credentials_file, config.drive_folder_id, files
            )
    except Exception:
        logger.exception("Drive upload failed for application %s", app.id)

    try:
        mod_paths = getattr(app, "mod_paths", []) or []
        if config.drive_enabled and mod_paths:
            files = [
                (path, f"{app.reg_number}_izmeneniya_{i + 1}.jpg")
                for i, path in enumerate(mod_paths)
            ]
            mod_urls = await drive.upload_photos(
                config.google_credentials_file, config.drive_folder_id, files
            )
    except Exception:
        logger.exception("Drive upload of modification photos failed for %s", app.id)

    try:
        if config.sheets_enabled:
            await sheets.append_application(config, app, photo_urls, mod_urls)
    except Exception:
        logger.exception("Sheets append failed for application %s", app.id)


async def approve_application(
    bot: Bot, config: Config | TenantConfig, db: Database, app_id: int, moderator: str
) -> Optional[int]:
    """Approve an application. Returns the assigned number, or None if already processed."""
    number = await db.approve(app_id, moderator)
    if number is None:
        return None
    app = await db.get_application(app_id)
    # Tenant-branded approved text
    approved_text = texts.approved_for_tenant(app.language, config, number)
    await notify_applicant(bot, app.user_id, approved_text, app.language)
    await send_ticket(bot, config, app)
    asyncio.create_task(export_to_google(config, app))
    return number


async def reject_application(
    bot: Bot, config: Config | TenantConfig, db: Database, app_id: int, moderator: str
) -> bool:
    """Reject an application. Returns True if it was pending and got rejected."""
    ok = await db.reject(app_id, moderator)
    if not ok:
        return False
    app = await db.get_application(app_id)
    rejected_text = texts.rejected_for_tenant(app.language, config)
    await notify_applicant(bot, app.user_id, rejected_text, app.language)
    return True


async def set_status(
    bot: Bot, config: Config | TenantConfig, db: Database, app_id: int, status: str, moderator: str
) -> bool:
    """Admin-panel override: force an application's status regardless of current.

    Notifies the applicant and, on transition to approved, sends the ticket
    and exports to Google — same as normal approve/reject flow.
    """
    if status not in (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED):
        return False
    ok = await db.set_status(app_id, status, moderator)
    if not ok:
        return False
    app = await db.get_application(app_id)
    if status == STATUS_APPROVED:
        approved_text = texts.approved_for_tenant(app.language, config, app.reg_number)
        await notify_applicant(bot, app.user_id, approved_text, app.language)
        await send_ticket(bot, config, app)
        asyncio.create_task(export_to_google(config, app))
    elif status == STATUS_REJECTED:
        rejected_text = texts.rejected_for_tenant(app.language, config)
        await notify_applicant(bot, app.user_id, rejected_text, app.language)
    return True
