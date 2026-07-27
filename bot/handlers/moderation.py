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
from ..constants import DIRECTIONS
from ..db import Database
from ..services import assets, decisions, subscription
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


# --- Brand assets uploaded straight from Telegram -------------------------
#
# Sponsor logos and direction banners would otherwise have to be committed to
# the repo, which is impractical mid-event. An admin can instead send the image
# to the bot with a caption, and it is stored on the volume (surviving
# redeploys) and used immediately.

_ASSETS_HELP = (
    "🖼 <b>Логотипы и баннеры</b>\n\n"
    "Отправьте картинку <b>с подписью</b>:\n\n"
    "• <code>/logo 1_pride</code> — логотип спонсора (наверху билета).\n"
    "  Порядок задаётся цифрой в начале: 1_, 2_, 3_…\n"
    "• <code>/banner drift</code> — баннер направления.\n"
    "  Доступные: {slugs}\n\n"
    "Лучше отправлять <b>файлом</b> (без сжатия) — качество выше.\n\n"
    "<code>/assets</code> — что уже загружено\n"
    "<code>/delasset logo 1_pride</code> — удалить"
)


def _direction_slugs() -> list[str]:
    return [d["slug"] for d in DIRECTIONS]


async def _download_incoming_image(message: Message, bot: Bot) -> bytes | None:
    """Bytes of an image sent as a photo or as an uncompressed document."""
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.document and (message.document.mime_type or "").startswith("image/"):
        file_id = message.document.file_id
    else:
        return None
    buf = await bot.download(file_id)
    return buf.read() if buf else None


@router.message(Command("assets"))
async def cmd_assets(message: Message, config: Config) -> None:
    """Show which brand assets are currently in use."""
    if not _is_admin(message, config):
        return

    inv = assets.inventory()

    def _fmt(runtime: list[str], bundled: list[str]) -> str:
        if runtime:
            return "\n".join(f"  • {n} (загружен)" for n in runtime)
        if bundled:
            return "\n".join(f"  • {n} (из репозитория)" for n in bundled)
        return "  — пусто —"

    lines = [
        "🖼 <b>Текущие ассеты</b>\n",
        "<b>Логотипы спонсоров</b> (верх билета):",
        _fmt(inv["sponsors_runtime"], inv["sponsors_bundled"]),
        "",
        "<b>Баннеры направлений</b>:",
        _fmt(inv["directions_runtime"], inv["directions_bundled"]),
    ]
    if not inv["storage_configured"]:
        lines.append("\n⚠️ Хранилище не настроено — загрузка недоступна.")
    else:
        lines.append("\nЗагрузить: /help_assets")
    await message.answer("\n".join(lines))


@router.message(Command("help_assets"))
async def cmd_help_assets(message: Message, config: Config) -> None:
    if not _is_admin(message, config):
        return
    await message.answer(_ASSETS_HELP.format(slugs=", ".join(_direction_slugs())))


@router.message(Command("delasset"))
async def cmd_delasset(message: Message, config: Config) -> None:
    """/delasset logo 1_pride  |  /delasset banner drift"""
    if not _is_admin(message, config):
        return
    parts = (message.text or "").split()
    if len(parts) != 3 or parts[1] not in ("logo", "banner"):
        await message.answer(
            "Использование: <code>/delasset logo 1_pride</code> или "
            "<code>/delasset banner drift</code>"
        )
        return
    kind = "sponsors" if parts[1] == "logo" else "directions"
    if assets.delete_asset(kind, parts[2]):
        await message.answer(f"🗑 Удалено: <code>{parts[2]}</code>")
    else:
        await message.answer(f"Не найдено: <code>{parts[2]}</code>")


@router.message(F.photo | F.document)
async def receive_brand_asset(message: Message, bot: Bot, config: Config) -> None:
    """Store an image sent with a /logo or /banner caption."""
    if not _is_admin(message, config):
        return

    caption = (message.caption or "").strip()
    if not caption.startswith(("/logo", "/banner")):
        return

    parts = caption.split()
    command = parts[0].split("@")[0]  # tolerate /logo@BotName in groups
    name = parts[1] if len(parts) > 1 else ""

    if not name:
        await message.answer(
            "Укажите имя в подписи, например: <code>/logo 1_pride</code>"
            if command == "/logo"
            else f"Укажите направление: <code>/banner drift</code>\n"
                 f"Доступные: {', '.join(_direction_slugs())}"
        )
        return

    if not assets.is_safe_name(name):
        await message.answer(
            "Имя может содержать только латинские буквы, цифры, «_» и «-». "
            "Например: <code>1_pride</code>"
        )
        return

    if command == "/banner" and name not in _direction_slugs():
        await message.answer(
            f"Неизвестное направление: <code>{name}</code>\n"
            f"Доступные: {', '.join(_direction_slugs())}"
        )
        return

    try:
        data = await _download_incoming_image(message, bot)
        if data is None:
            await message.answer("Пришлите именно картинку (фото или файл-изображение).")
            return
        if command == "/logo":
            assets.save_sponsor(name, data)
            await message.answer(
                f"✅ Логотип <code>{name}</code> сохранён — появится на билетах сразу.\n"
                f"Проверить: /assets, затем /diag"
            )
        else:
            assets.save_direction(name, data)
            await message.answer(
                f"✅ Баннер направления <code>{name}</code> сохранён — "
                f"будет показан при выборе этого направления."
            )
    except Exception as exc:  # noqa: BLE001 - report the reason to the admin
        logger.exception("Failed to store brand asset %s", name)
        await message.answer(f"❌ Не удалось сохранить: <code>{exc}</code>")


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


@router.message(Command("stats"))
async def cmd_stats(message: Message, config: Config, db: Database) -> None:
    """Show detailed bot analytics in Telegram."""
    if not _is_admin(message, config):
        return

    st = await db.stats()
    lines = [
        "📊 <b>Promotors Show — Statistika & Analitika</b>\n",
        f"📋 <b>Jami arizalar:</b> <code>{st['total']}</code> ta",
        f"⏳ <b>Kutilmoqda (Pending):</b> <code>{st['pending']}</code> ta",
        f"✅ <b>Tasdiqlangan (Approved):</b> <code>{st['approved']}</code> ta",
        f"❌ <b>Rad etilgan (Rejected):</b> <code>{st['rejected']}</code> ta",
        f"🔢 <b>Oxirgi berilgan raqam:</b> <code>№{st['max_number']}</code>\n",
    ]

    if st.get("by_direction"):
        lines.append("🏎 <b>Yo'nalishlar bo'yicha:</b>")
        for k, v in st["by_direction"].items():
            lines.append(f"  • {k}: <b>{v}</b>")
        lines.append("")

    if st.get("by_country"):
        lines.append("🌍 <b>Mamlakatlar bo'yicha:</b>")
        for k, v in st["by_country"].items():
            lines.append(f"  • {k}: <b>{v}</b>")
        lines.append("")

    if st.get("by_language"):
        lines.append("🌐 <b>Tillar bo'yicha:</b>")
        for k, v in st["by_language"].items():
            lines.append(f"  • {k.upper()}: <b>{v}</b>")

    await message.answer("\n".join(lines))


@router.message(Command("export"))
async def cmd_export(message: Message, config: Config, db: Database) -> None:
    """Send formatted Excel (.xlsx) file in Telegram."""
    if not _is_admin(message, config):
        return

    msg = await message.answer("⏳ Excel fayli tayyorlanmoqda...")
    apps = await db.list_applications(limit=100000)

    from ..services.excel import generate_excel

    xlsx_bytes = await asyncio.to_thread(generate_excel, apps)
    await message.answer_document(
        BufferedInputFile(xlsx_bytes, filename="promotors_applications.xlsx"),
        caption=f"📊 Jami {len(apps)} ta ariza bo'yicha Excel fayli tayyor.",
    )
    try:
        await msg.delete()
    except Exception:  # noqa: BLE001
        pass


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
