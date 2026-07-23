"""Accept / Reject handling in the moderation chat."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import CallbackQuery

from .. import keyboards, texts
from ..config import Config
from ..db import Application, Database
from ..handlers.registration import SIDES
from ..services import drive, sheets

logger = logging.getLogger(__name__)
router = Router(name="moderation")

_SIDE_LABELS = {"left": "levaya", "right": "pravaya", "front": "perednyaya", "back": "zadnyaya"}


def _moderator_label(query: CallbackQuery) -> str:
    user = query.from_user
    return f"@{user.username}" if user.username else user.full_name


async def _notify_applicant(bot: Bot, user_id: int, text: str) -> None:
    try:
        await bot.send_message(user_id, text, reply_markup=keyboards.main_menu_keyboard())
    except TelegramForbiddenError:
        # User blocked the bot; nothing we can do.
        logger.warning("Could not notify user %s (bot blocked?)", user_id)


@router.callback_query(F.data.startswith(f"{keyboards.CB_APPROVE}:"))
async def approve(query: CallbackQuery, bot: Bot, config: Config, db: Database) -> None:
    app_id = int(query.data.split(":", 1)[1])
    moderator = _moderator_label(query)

    number = await db.approve(app_id, moderator)
    if number is None:
        await query.answer(texts.MODERATION_ALREADY, show_alert=True)
        return

    app = await db.get_application(app_id)
    await _notify_applicant(bot, app.user_id, texts.APPROVED.format(number=number))

    await query.message.edit_text(
        texts.MODERATION_APPROVED.format(number=number, moderator=moderator)
    )
    await query.answer(f"Одобрено, №{number}")

    # Export to Google (Drive upload + Sheets append) in the background so the
    # moderator's UI stays responsive and export failures don't block approval.
    asyncio.create_task(_export_to_google(config, app))


@router.callback_query(F.data.startswith(f"{keyboards.CB_REJECT}:"))
async def reject(query: CallbackQuery, bot: Bot, db: Database) -> None:
    app_id = int(query.data.split(":", 1)[1])
    moderator = _moderator_label(query)

    ok = await db.reject(app_id, moderator)
    if not ok:
        await query.answer(texts.MODERATION_ALREADY, show_alert=True)
        return

    app = await db.get_application(app_id)
    await _notify_applicant(bot, app.user_id, texts.REJECTED)

    await query.message.edit_text(texts.MODERATION_REJECTED.format(moderator=moderator))
    await query.answer("Отклонено")


async def _export_to_google(config: Config, app: Application) -> None:
    """Upload photos to Drive and append the row to Sheets. Best-effort."""
    photo_urls: list[str] = []
    try:
        if config.drive_enabled and app.photo_paths:
            files = [
                (path, f"{app.reg_number}_{SIDES[i]}_{_SIDE_LABELS.get(SIDES[i], SIDES[i])}.jpg")
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
