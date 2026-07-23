"""Accept / Reject handling for the Telegram moderation-chat buttons.

The actual decision logic (assign number, notify applicant, Google export)
lives in ``bot.services.decisions`` and is shared with the web admin panel.
"""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from .. import keyboards, texts
from ..config import Config
from ..db import Database
from ..services import decisions, subscription

logger = logging.getLogger(__name__)
router = Router(name="moderation")


def _moderator_label(query: CallbackQuery) -> str:
    user = query.from_user
    return f"@{user.username}" if user.username else user.full_name


def _is_admin(message: Message, config: Config) -> bool:
    return message.chat.id == config.admin_chat_id or (
        message.from_user is not None and message.from_user.id in config.admin_user_ids
    )


@router.message(Command("diag"))
async def diag(message: Message, bot: Bot, config: Config) -> None:
    """Self-diagnostics for the subscription gate. Run it in the moderation
    chat (or as an ADMIN_USER_IDS user in private): reports the channel it sees
    and whether the bot is an admin there — the usual cause of the "you're not
    subscribed" loop."""
    if not _is_admin(message, config):
        return

    lines = [f"<b>Диагностика</b>", f"REQUIRED_CHANNEL = <code>{config.required_channel}</code>"]
    channel = subscription.normalize_channel(config.required_channel)

    try:
        chat = await bot.get_chat(channel)
        uname = f"@{chat.username}" if chat.username else "—"
        lines.append(f"Канал найден: <b>{chat.title}</b> (id <code>{chat.id}</code>, {uname})")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"❌ Канал недоступен: <code>{exc}</code>")
        lines.append("➡️ Проверьте REQUIRED_CHANNEL (id/username).")
        await message.answer("\n".join(lines))
        return

    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(channel, me.id)
        lines.append(f"Статус бота в канале: <b>{member.status}</b>")
        if member.status in {"administrator", "creator"}:
            lines.append("✅ Бот админ — проверка подписки будет работать.")
        else:
            lines.append("⚠️ Бот НЕ админ канала — добавьте его администратором, иначе проверка подписки всегда будет считать пользователя неподписанным.")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"❌ Бот не может проверить участников: <code>{exc}</code>")
        lines.append("➡️ Добавьте бота <b>администратором</b> канала.")

    await message.answer("\n".join(lines))


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
