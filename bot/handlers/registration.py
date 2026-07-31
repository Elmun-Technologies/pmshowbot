"""Registration flow: /start → language → subscription gate → form → moderation."""
from __future__ import annotations

import logging
import os

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.media_group import MediaGroupBuilder

from .. import keyboards, texts
from ..config import Config
from ..constants import MAX_MOD_PHOTOS, SIDES, direction_image_path
from ..db import Database
from ..services import subscription
from ..states import Registration
from ..validation import clean_country, clean_phone, clean_plate

logger = logging.getLogger(__name__)
router = Router(name="registration")


def _user_label(message_or_query) -> str:
    user = message_or_query.from_user
    if user.username:
        return f"@{user.username}"
    return f"{user.full_name} (id {user.id})"


async def _lang(state: FSMContext) -> str:
    return (await state.get_data()).get("lang", "ru")


async def _gate_or_start(
    message: Message, state: FSMContext, bot: Bot, config: Config, user_id: int, lang: str
) -> None:
    """After the language is known: subscription gate, then the form."""
    t = texts.T(lang)
    if config.require_subscription and not await subscription.is_subscribed(
        bot, config.required_channel, user_id
    ):
        await message.answer(
            t.SUBSCRIBE_REQUIRED,
            reply_markup=keyboards.subscription_keyboard(
                subscription.channel_url(config.required_channel, config.channel_url), lang
            ),
        )
        return
    await _start_form(message, state, lang)


async def _start_form(message: Message, state: FSMContext, lang: str) -> None:
    """Send greeting and move to the first form step (country)."""
    t = texts.T(lang)
    await state.set_state(Registration.country)
    await message.answer(t.GREETING)
    await message.answer(t.ASK_COUNTRY, reply_markup=keyboards.country_keyboard(lang))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot, config: Config, db: Database) -> None:
    await state.clear()

    # If the user already has a pending/approved application, show status instead.
    active = await db.has_active_application(message.from_user.id)
    if active is not None:
        from .mynumber import show_status  # local import avoids a cycle

        await show_status(message, active)
        return

    # First ask the language (prompt is bilingual).
    await state.set_state(Registration.language)
    await message.answer(texts.ASK_LANGUAGE, reply_markup=keyboards.language_keyboard())


@router.callback_query(Registration.language, F.data.startswith(f"{keyboards.CB_LANG}:"))
async def choose_language(
    query: CallbackQuery, state: FSMContext, bot: Bot, config: Config
) -> None:
    lang = query.data.split(":", 1)[1]
    if lang not in ("uz", "ru"):
        lang = "ru"
    await state.update_data(lang=lang)
    await query.answer()
    await _gate_or_start(query.message, state, bot, config, query.from_user.id, lang)


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

    lang = await _lang(state)
    if await subscription.is_subscribed(bot, config.required_channel, query.from_user.id):
        await query.answer()
        await _start_form(query.message, state, lang)
    else:
        await query.answer(texts.T(lang).SUBSCRIBE_STILL_NOT, show_alert=True)


# --- Country ---
@router.callback_query(Registration.country, F.data.startswith(f"{keyboards.CB_COUNTRY}:"))
async def choose_country(query: CallbackQuery, state: FSMContext) -> None:
    lang = await _lang(state)
    _, value = query.data.split(":", 1)
    if value == "other":
        await state.set_state(Registration.country_other)
        await query.message.answer(texts.T(lang).ASK_COUNTRY_OTHER)
        await query.answer()
        return

    # Store the canonical (Russian) country name regardless of display language.
    await state.update_data(country=texts.COUNTRIES_CANON[int(value)])
    await state.set_state(Registration.plate)
    await query.message.answer(texts.T(lang).ASK_PLATE)
    await query.answer()


@router.message(Registration.country_other, F.text)
async def country_other(message: Message, state: FSMContext) -> None:
    lang = await _lang(state)
    country = clean_country(message.text)
    if country is None:
        await message.answer(texts.T(lang).BAD_COUNTRY)
        return
    await state.update_data(country=country)
    await state.set_state(Registration.plate)
    await message.answer(texts.T(lang).ASK_PLATE)


# --- License plate ---
@router.message(Registration.plate, F.text)
async def set_plate(message: Message, state: FSMContext) -> None:
    lang = await _lang(state)
    plate = clean_plate(message.text)
    if plate is None:
        await message.answer(texts.T(lang).BAD_PLATE)
        return
    await state.update_data(
        plate=plate,
        photo_file_ids=[],
        photo_paths=[],
        mod_file_ids=[],
        mod_paths=[],
    )
    await state.set_state(Registration.direction)
    await message.answer(
        texts.T(lang).ASK_DIRECTION, reply_markup=keyboards.direction_keyboard(lang)
    )


# --- Direction (right after the plate, before photos) ---
@router.callback_query(Registration.direction, F.data.startswith(f"{keyboards.CB_DIRECTION}:"))
async def choose_direction(query: CallbackQuery, state: FSMContext) -> None:
    lang = await _lang(state)
    _, idx = query.data.split(":", 1)
    # Store the canonical (Russian) direction name.
    canonical = texts.DIRECTIONS_CANON[int(idx)]
    await state.update_data(direction=canonical)
    await state.set_state(Registration.photos)

    # Show the direction's promo banner so the participant sees the category
    # they just joined (skipped silently if the asset isn't bundled).
    banner = direction_image_path(canonical)
    if banner:
        try:
            await query.message.answer_photo(
                FSInputFile(banner),
                caption=texts.T(lang).DIRECTION_PICKED.format(
                    direction=texts.localize_direction(canonical, lang)
                ),
            )
        except Exception:  # noqa: BLE001 - a banner must never block registration
            logger.exception("Could not send direction banner for %s", canonical)

    await query.message.answer(texts.T(lang).PHOTO_PROMPTS[0])
    await query.answer()


# --- Photos (4, one by one) ---
@router.message(Registration.photos, F.photo)
async def collect_photo(message: Message, state: FSMContext, bot: Bot, config: Config) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
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
        await message.answer(texts.T(lang).PHOTO_PROMPTS[len(file_ids)])
    else:
        await _ask_mods(message, state, lang)


@router.message(Registration.photos)
async def photos_not_a_photo(message: Message, state: FSMContext) -> None:
    await message.answer(texts.T(await _lang(state)).PHOTO_NOT_A_PHOTO)


# --- Modifications: close-ups of what was changed on the car ---
async def _ask_mods(message: Message, state: FSMContext, lang: str) -> None:
    await state.set_state(Registration.mods)
    await message.answer(
        texts.T(lang).ASK_MODS.format(max=MAX_MOD_PHOTOS),
        reply_markup=keyboards.mods_keyboard(lang, has_photos=False),
    )


@router.message(Registration.mods, F.photo)
async def collect_mod_photo(message: Message, state: FSMContext, bot: Bot, config: Config) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    file_ids: list[str] = data.get("mod_file_ids", [])
    paths: list[str] = data.get("mod_paths", [])
    t = texts.T(lang)

    user_dir = os.path.join(config.media_dir, str(message.from_user.id))
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, f"mod_{len(file_ids) + 1}.jpg")

    photo = message.photo[-1]  # highest resolution
    await bot.download(photo, destination=path)

    file_ids.append(photo.file_id)
    paths.append(path)
    await state.update_data(mod_file_ids=file_ids, mod_paths=paths)

    # The cap keeps the moderation album within Telegram's media-group limit.
    if len(file_ids) >= MAX_MOD_PHOTOS:
        await message.answer(t.MODS_LIMIT.format(max=MAX_MOD_PHOTOS))
        await _ask_phone(message, state, lang)
        return

    await message.answer(
        t.MODS_ADDED.format(n=len(file_ids), max=MAX_MOD_PHOTOS),
        reply_markup=keyboards.mods_keyboard(lang, has_photos=True),
    )


@router.callback_query(Registration.mods, F.data == keyboards.CB_MODS_DONE)
async def mods_done(query: CallbackQuery, state: FSMContext) -> None:
    lang = await _lang(state)
    await query.answer()
    # Drop the button so an old message can't be pressed again mid-form.
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - a stale message must not block the form
        logger.debug("Could not clear the mods keyboard", exc_info=True)
    await _ask_phone(query.message, state, lang)


@router.message(Registration.mods)
async def mods_not_a_photo(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await message.answer(
        texts.T(lang).MODS_NOT_A_PHOTO,
        reply_markup=keyboards.mods_keyboard(
            lang, has_photos=bool(data.get("mod_file_ids"))
        ),
    )


async def _ask_phone(message: Message, state: FSMContext, lang: str) -> None:
    await state.set_state(Registration.phone)
    await message.answer(texts.T(lang).ASK_PHONE, reply_markup=keyboards.phone_keyboard(lang))


# --- Phone ---
@router.message(Registration.phone, F.contact)
async def set_phone_contact(
    message: Message, state: FSMContext, bot: Bot, config: Config, db: Database
) -> None:
    # Telegram's own number, so it needs no validation — only normalising.
    phone = clean_phone(message.contact.phone_number) or message.contact.phone_number
    await _finalize(message, state, bot, config, db, phone=phone)


@router.message(Registration.phone, F.text)
async def set_phone_text(
    message: Message, state: FSMContext, bot: Bot, config: Config, db: Database
) -> None:
    # Accept a typed number too, in case the user doesn't use the button — but
    # only if it can actually be one, so a stray "/mynumber" or "salom" doesn't
    # get filed as somebody's phone number.
    phone = clean_phone(message.text)
    if phone is None:
        lang = await _lang(state)
        await message.answer(
            texts.T(lang).BAD_PHONE, reply_markup=keyboards.phone_keyboard(lang)
        )
        return
    await _finalize(message, state, bot, config, db, phone=phone)


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
    lang = data.get("lang", "ru")
    app_id = await db.create_application(
        user_id=message.from_user.id,
        username=_user_label(message),
        full_name=message.from_user.full_name or "",
        country=data.get("country", ""),
        plate=data.get("plate", ""),
        direction=data.get("direction", ""),
        phone=phone,
        photo_file_ids=data.get("photo_file_ids", []),
        photo_paths=data.get("photo_paths", []),
        mod_file_ids=data.get("mod_file_ids", []),
        mod_paths=data.get("mod_paths", []),
        language=lang,
    )
    await state.clear()
    await message.answer(texts.T(lang).THANKS, reply_markup=keyboards.main_menu_keyboard(lang))

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
        mod_file_ids=data.get("mod_file_ids", []),
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
    mod_file_ids: list[str] | None = None,
) -> None:
    """Send the car photos + a summary card with Accept/Reject to the admin chat.

    The modification close-ups go into the same album, right after the four
    sides, so a moderator sees the whole car in one scroll.
    """
    mod_file_ids = mod_file_ids or []
    try:
        all_file_ids = list(photo_file_ids) + list(mod_file_ids)
        if all_file_ids:
            album = MediaGroupBuilder()
            for file_id in all_file_ids:
                album.add_photo(media=file_id)
            await bot.send_media_group(config.admin_chat_id, media=album.build())

        card = texts.MODERATION_CARD.format(
            country=country,
            plate=plate,
            direction=direction,
            phone=phone,
            mods=(
                texts.MODERATION_MODS_COUNT.format(n=len(mod_file_ids))
                if mod_file_ids
                else texts.MODERATION_MODS_NONE
            ),
            user=user_label,
        )
        await bot.send_message(
            config.admin_chat_id,
            card,
            reply_markup=keyboards.moderation_keyboard(app_id),
        )
    except Exception:  # noqa: BLE001 - never lose the applicant over a delivery error
        logger.exception("Failed to send moderation card for application %s", app_id)
