"""Accept / Reject handling for the Telegram moderation-chat buttons.

The actual decision logic (assign number, notify applicant, Google export)
lives in ``bot.services.decisions`` and is shared with the web admin panel.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .. import keyboards, texts
from ..config import Config
from ..db import Database
from ..services import decisions, subscription
from ..services.ticket import generate_ticket

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
async def diag(message: Message, bot: Bot, config: Config, db: Database) -> None:
    """Self-diagnostics. Run it in the moderation chat (or as an ADMIN_USER_IDS
    user in private): reports the subscription channel + the bot's admin status
    there, and runs a ticket self-test (posts a test ticket or the exact error)."""
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

    # --- ticket self-test: reproduce the real generation path and surface errors ---
    try:
        apps = await db.list_applications(limit=1)
        app = apps[0] if apps else None
        if app is not None:
            hero = decisions._pick_hero(app.photo_paths)
            hero_note = "фото участника" if hero else "нет фото → заглушка"
            png = await asyncio.to_thread(
                generate_ticket,
                number=app.reg_number or 1,
                plate=app.plate or "TEST",
                direction=texts.localize_direction(app.direction, app.language),
                name=decisions._get_display_name(app),
                lang=app.language,
                hero_image_path=hero,
            )
        else:
            hero_note = "нет заявок → заглушка"
            png = await asyncio.to_thread(
                generate_ticket, number=1, plate="TEST-777", direction="Тюнинг", name="Иван Иванов", lang="ru"
            )
        await message.answer_photo(
            BufferedInputFile(png, filename="diag_ticket.png"),
            caption=f"🎫 Генерация билета OK ({hero_note}).",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ticket self-test failed")
        await message.answer(f"🎫 <b>Билет: ОШИБКА</b>\n<code>{type(exc).__name__}: {exc}</code>")


@router.callback_query(F.data.startswith(f"{keyboards.CB_APPROVE}:"))
async def approve(query: CallbackQuery, bot: Bot, config: Config, db: Database) -> None:
    app_id = int(query.data.split(":", 1)[1])
    moderator = _moderator_label(query)

    number = await decisions.approve_application(bot, config, db, app_id, moderator)
    if number is None:
        await query.answer(texts.MODERATION_ALREADY, show_alert=True)
        return

    # Keep all the application details visible; append the decision below them.
    await _append_status(
        query, texts.MODERATION_APPROVED.format(number=number, moderator=moderator)
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

    await _append_status(query, texts.MODERATION_REJECTED.format(moderator=moderator))
    await query.answer("Отклонено")


async def _append_status(query: CallbackQuery, status_line: str) -> None:
    """Append a decision line under the existing card, keeping all the details,
    and drop the inline buttons."""
    base = query.message.html_text or query.message.text or ""
    try:
        await query.message.edit_text(f"{base}\n\n{status_line}")
    except Exception:  # noqa: BLE001 - fall back to editing just the markup
        logger.exception("Could not edit moderation card %s", query.message.message_id)
        await query.message.edit_reply_markup(reply_markup=None)
