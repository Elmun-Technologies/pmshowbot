"""Accept / Reject handling for the Telegram moderation-chat buttons.

The actual decision logic (assign number, notify applicant, Google export)
lives in ``bot.services.decisions`` and is shared with the web admin panel.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery

from .. import keyboards, texts
from ..config import Config
from ..db import Database
from ..services import decisions

logger = logging.getLogger(__name__)
router = Router(name="moderation")


def _moderator_label(query: CallbackQuery) -> str:
    user = query.from_user
    return f"@{user.username}" if user.username else user.full_name


@router.callback_query(F.data.startswith(f"{keyboards.CB_APPROVE}:"))
async def approve(query: CallbackQuery, bot: Bot, config: Config, db: Database) -> None:
    app_id = int(query.data.split(":", 1)[1])
    moderator = _moderator_label(query)

    number = await decisions.approve_application(bot, config, db, app_id, moderator)
    if number is None:
        await query.answer(texts.MODERATION_ALREADY, show_alert=True)
        return

    await query.message.edit_text(
        texts.MODERATION_APPROVED.format(number=number, moderator=moderator)
    )
    await query.answer(f"Одобрено, №{number}")


@router.callback_query(F.data.startswith(f"{keyboards.CB_REJECT}:"))
async def reject(query: CallbackQuery, bot: Bot, config: Config, db: Database) -> None:
    app_id = int(query.data.split(":", 1)[1])
    moderator = _moderator_label(query)

    ok = await decisions.reject_application(bot, config, db, app_id, moderator)
    if not ok:
        await query.answer(texts.MODERATION_ALREADY, show_alert=True)
        return

    await query.message.edit_text(texts.MODERATION_REJECTED.format(moderator=moderator))
    await query.answer("Отклонено")
