"""Registration flow: /start subscription gate through to the moderation card."""
from __future__ import annotations

import logging
import os

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.media_group import MediaGroupBuilder

from .. import keyboards, texts
from ..config import Config
from ..constants import SIDES
from ..db import Database
from ..services import subscription
from ..states import Registration

logger = logging.getLogger(__name__)
router = Router(name="registration")


def _user_label(message_or_query) -> str:
    user = message_or_query.from_user
    if user.username:
        return f"@{user.username}"
    return f"{user.full_name} (id {user.id})"


async def _start_form(message: Message, state: FSMContext) -> None:
    """Send greeting and move to the first form step (country)."""
    await state.set_state(Registration.country)
    await message.answer(texts.GREETING)
    await message.answer(texts.ASK_COUNTRY, reply_markup=keyboards.country_keyboard())


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot, config: Config, db: Database) -> None:
    await state.clear()

    # If the user already has a pending/approved application, show status instead.
    active = await db.has_active_application(message.from_user.id)
    if active is not None:
        from .mynumber import show_status  # local import avoids a cycle

        await show_status(message, active)
        return

    if config.require_subscription and not await subscription.is_subscribed(
        bot, config.required_channel, message.from_user.id
    ):
        await message.answer(
            texts.SUBSCRIBE_REQUIRED,
            reply_markup=keyboards.subscription_keyboard(
                subscription.channel_url(config.required_channel, config.channel_url)
            ),
        )
        return

    await _start_form(message, state)


@router.callback_query(F.data == keyboards.CB_CHECK_SUB)
async def check_subscription(
    query: CallbackQuery, state: FSMContext, bot: Bot, config: Config, db: Database
) -> None:
    active = await db.has_active_application(query.from_user.id)
    if active is not None:
        from .mynumber import show_status

        await show_status(query.message, active)
        await query.answer()
        return

    if await subscription.is_subscribed(bot, config.required_channel, query.from_user.id):
        await query.answer()
        await _start_form(query.message, state)
    else:
        await query.answer(texts.SUBSCRIBE_STILL_NOT, show_alert=True)


# --- Country ---
@router.callback_query(Registration.country, F.data.startswith(f"{keyboards.CB_COUNTRY}:"))
async def choose_country(query: CallbackQuery, state: FSMContext) -> None:
    _, value = query.data.split(":", 1)
    if value == "other":
        await state.set_state(Registration.country_other)
        await query.message.answer(texts.ASK_COUNTRY_OTHER)
        await query.answer()
        return

    country = texts.COUNTRIES[int(value)]
    await state.update_data(country=country)
    await state.set_state(Registration.plate)
    await query.message.answer(texts.ASK_PLATE)
    await query.answer()


@router.message(Registration.country_other, F.text)
async def country_other(message: Message, state: FSMContext) -> None:
    await state.update_data(country=message.text.strip())
    await state.set_state(Registration.plate)
    await message.answer(texts.ASK_PLATE)


# --- License plate ---
@router.message(Registration.plate, F.text)
async def set_plate(message: Message, state: FSMContext) -> None:
    await state.update_data(plate=message.text.strip(), photo_file_ids=[], photo_paths=[])
    await state.set_state(Registration.photos)
    await message.answer(texts.PHOTO_PROMPTS[0])


# --- Photos (4, one by one) ---
@router.message(Registration.photos, F.photo)
async def collect_photo(message: Message, state: FSMContext, bot: Bot, config: Config) -> None:
    data = await state.get_data()
    file_ids: list[str] = data.get("photo_file_ids", [])
    paths: list[str] = data.get("photo_paths", [])

    index = len(file_ids)
    side = SIDES[index]
    user_dir = os.path.join(config.media_dir, str(message.from_user.id))
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, f"{side}.jpg")

    photo = message.photo[-1]  # highest resolution
    await bot.download(photo, destination=path)

    file_ids.append(photo.file_id)
    paths.append(path)
    await state.update_data(photo_file_ids=file_ids, photo_paths=paths)

    if len(file_ids) < len(SIDES):
        await message.answer(texts.PHOTO_PROMPTS[len(file_ids)])
    else:
        await state.set_state(Registration.direction)
        await message.answer(texts.ASK_DIRECTION, reply_markup=keyboards.direction_keyboard())


@router.message(Registration.photos)
async def photos_not_a_photo(message: Message) -> None:
    await message.answer(texts.PHOTO_NOT_A_PHOTO)


# --- Direction ---
@router.callback_query(Registration.direction, F.data.startswith(f"{keyboards.CB_DIRECTION}:"))
async def choose_direction(query: CallbackQuery, state: FSMContext) -> None:
    _, idx = query.data.split(":", 1)
    await state.update_data(direction=texts.DIRECTIONS[int(idx)])
    await state.set_state(Registration.phone)
    await query.message.answer(texts.ASK_PHONE, reply_markup=keyboards.phone_keyboard())
    await query.answer()


# --- Phone ---
@router.message(Registration.phone, F.contact)
async def set_phone_contact(
    message: Message, state: FSMContext, bot: Bot, config: Config, db: Database
) -> None:
    await _finalize(message, state, bot, config, db, phone=message.contact.phone_number)


@router.message(Registration.phone, F.text)
async def set_phone_text(
    message: Message, state: FSMContext, bot: Bot, config: Config, db: Database
) -> None:
    # Accept a typed number too, in case the user doesn't use the button.
    await _finalize(message, state, bot, config, db, phone=message.text.strip())


async def _finalize(
    message: Message,
    state: FSMContext,
    bot: Bot,
    config: Config,
    db: Database,
    *,
    phone: str,
) -> None:
    data = await state.get_data()
    app_id = await db.create_application(
        user_id=message.from_user.id,
        username=_user_label(message),
        country=data.get("country", ""),
        plate=data.get("plate", ""),
        direction=data.get("direction", ""),
        phone=phone,
        photo_file_ids=data.get("photo_file_ids", []),
        photo_paths=data.get("photo_paths", []),
    )
    await state.clear()
    await message.answer(texts.THANKS, reply_markup=keyboards.main_menu_keyboard())

    await _send_moderation_card(
        bot,
        config,
        app_id=app_id,
        country=data.get("country", ""),
        plate=data.get("plate", ""),
        direction=data.get("direction", ""),
        phone=phone,
        user_label=_user_label(message),
        photo_file_ids=data.get("photo_file_ids", []),
    )


async def _send_moderation_card(
    bot: Bot,
    config: Config,
    *,
    app_id: int,
    country: str,
    plate: str,
    direction: str,
    phone: str,
    user_label: str,
    photo_file_ids: list[str],
) -> None:
    """Send the 4 photos + a summary card with Accept/Reject to the admin chat."""
    try:
        if photo_file_ids:
            album = MediaGroupBuilder()
            for file_id in photo_file_ids:
                album.add_photo(media=file_id)
            await bot.send_media_group(config.admin_chat_id, media=album.build())

        card = texts.MODERATION_CARD.format(
            country=country,
            plate=plate,
            direction=direction,
            phone=phone,
            user=user_label,
        )
        await bot.send_message(
            config.admin_chat_id,
            card,
            reply_markup=keyboards.moderation_keyboard(app_id),
        )
    except Exception:  # noqa: BLE001 - never lose the applicant over a delivery error
        logger.exception("Failed to send moderation card for application %s", app_id)
