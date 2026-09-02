"""Accepts a badge photo sent outside the registration flow.

A broadcast (see admin/broadcast) can ask participants to send a 3x4 photo
for their personal event badge. This handles that photo whenever it arrives
with no active FSM state — i.e. not mid-registration — attaching it to the
sender's latest application.
"""
from __future__ import annotations

import logging
import os

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.types import Message

from .. import texts
from ..config import Config
from ..db import Database

logger = logging.getLogger(__name__)
router = Router(name="badge_photo")


@router.message(StateFilter(None), F.photo, F.chat.type == "private")
async def receive_badge_photo(message: Message, bot: Bot, config: Config, db: Database) -> None:
    lang = await db.get_user_language(message.from_user.id)
    t = texts.T(lang)

    user_dir = os.path.join(config.media_dir, str(message.from_user.id))
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, "badge.jpg")

    photo = message.photo[-1]  # highest resolution
    await bot.download(photo, destination=path)

    app_id = await db.set_badge_photo(message.from_user.id, photo.file_id, path)
    if app_id is None:
        await message.answer(t.BADGE_PHOTO_NO_APP)
        return
    await message.answer(t.BADGE_PHOTO_SAVED)
