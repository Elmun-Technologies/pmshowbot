"""Approve / reject business logic shared by the Telegram moderation buttons
and the web admin panel.

Both entry points assign the registration number, notify the applicant over
Telegram, and (on approval) export to Google in the background. The only thing
that differs is the surrounding UI (editing the Telegram card vs an HTTP
redirect), which stays in the respective callers.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError

from .. import keyboards, texts
from ..config import Config
from ..constants import SIDES, SIDE_LABELS_TRANSLIT
from ..db import Application, Database
from . import drive, sheets

logger = logging.getLogger(__name__)


async def notify_applicant(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text, reply_markup=keyboards.main_menu_keyboard())
    except TelegramForbiddenError:
        # User blocked the bot; nothing we can do.
        logger.warning("Could not notify user %s (bot blocked?)", user_id)
    except Exception:  # noqa: BLE001 - a delivery error must not break the decision
        logger.exception("Failed to notify user %s", user_id)


async def export_to_google(config: Config, app: Application) -> None:
    """Upload photos to Drive and append the row to Sheets. Best-effort."""
    photo_urls: list[str] = []
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
    except Exception:  # noqa: BLE001
        logger.exception("Drive upload failed for application %s", app.id)

    try:
        if config.sheets_enabled:
            await sheets.append_application(config, app, photo_urls)
    except Exception:  # noqa: BLE001
        logger.exception("Sheets append failed for application %s", app.id)


async def approve_application(
    bot: Bot, config: Config, db: Database, app_id: int, moderator: str
) -> Optional[int]:
    """Approve an application. Returns the assigned number, or None if it was
    already processed / not found."""
    number = await db.approve(app_id, moderator)
    if number is None:
        return None
    app = await db.get_application(app_id)
    await notify_applicant(bot, app.user_id, texts.APPROVED.format(number=number))
    # Export in the background so the caller's UI stays responsive.
    asyncio.create_task(export_to_google(config, app))
    return number


async def reject_application(
    bot: Bot, config: Config, db: Database, app_id: int, moderator: str
) -> bool:
    """Reject an application. Returns True if it was pending and got rejected."""
    ok = await db.reject(app_id, moderator)
    if not ok:
        return False
    app = await db.get_application(app_id)
    await notify_applicant(bot, app.user_id, texts.REJECTED)
    return True
