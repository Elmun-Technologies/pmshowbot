"""Registration flow: /start → language → subscription gate → form → moderation.

Tenant-branded:
- greetings, subscribe, closed messages from TenantConfig
- directions loaded from DB (tenant-specific, 2-level)
- stores direction as "Parent — Child" string + direction_id for export
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.media_group import MediaGroupBuilder

from .. import keyboards, texts
from ..config import Config, TenantConfig
from ..constants import MAX_MOD_PHOTOS, SIDES, direction_image_path
from ..db import Database
from ..services import subscription
from ..services.directions import (
    build_hierarchy,
    format_final_choice,
    localized_final_choice,
)
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


def _config_tenant_id(config) -> int | str | None:
    return getattr(config, "tenant_id", None) or getattr(config, "tenant_slug", None)


async def _load_tenant_directions(db: Database, config) -> list:
    """Load active directions for this tenant, fallback to empty."""
    try:
        tid = _config_tenant_id(config)
        if tid is None:
            return []
        return await db.list_directions(tenant_id=tid, active_only=True)
    except Exception:
        logger.exception("Failed to load directions for tenant %s", getattr(config, "tenant_slug", "unknown"))
        return []


async def _gate_or_start(
    message: Message, state: FSMContext, bot: Bot, config: Config | TenantConfig, user_id: int, lang: str, db: Database | None = None
) -> None:
    """After the language is known: subscription gate, then the form."""
    if getattr(config, "registration_closed", False):
        await message.answer(texts.registration_closed_for_tenant(lang, config))
        return
    if getattr(config, "require_subscription", True) and not await subscription.is_subscribed(
        bot, config.required_channel, user_id
    ):
        await message.answer(
            texts.subscribe_required_for_tenant(lang, config),
            reply_markup=keyboards.subscription_keyboard(
                subscription.channel_url(config.required_channel, getattr(config, "channel_url", "")),
                lang,
            ),
        )
        return
    await _start_form(message, state, lang, config)


async def _start_form(message: Message, state: FSMContext, lang: str, config: Config | TenantConfig | None = None) -> None:
    """Send greeting and move to the first form step (country)."""
    if config is not None:
        greet = texts.greeting_for_tenant(lang, config)
    else:
        greet = texts.T(lang).GREETING
    await state.set_state(Registration.country)
    await message.answer(greet)
    await message.answer(texts.T(lang).ASK_COUNTRY, reply_markup=keyboards.country_keyboard(lang))


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext, bot: Bot, config: Config | TenantConfig, db: Database) -> None:
    await db.touch_user(
        message.from_user.id,
        username=_user_label(message),
        language="ru",
    )
    await state.clear()

    active = await db.has_active_application(message.from_user.id)
    if active is not None:
        from .mynumber import show_status

        await show_status(message, active)
        return

    if getattr(config, "registration_closed", False):
        await message.answer(texts.registration_closed_bilingual_for_tenant(config))
        return

    await state.set_state(Registration.language)
    await message.answer(texts.ASK_LANGUAGE, reply_markup=keyboards.language_keyboard())


@router.callback_query(Registration.language, F.data.startswith(f"{keyboards.CB_LANG}:"))
async def choose_language(
    query: CallbackQuery, state: FSMContext, bot: Bot, config: Config | TenantConfig, db: Database
) -> None:
    lang = query.data.split(":", 1)[1]
    if lang not in ("uz", "ru"):
        lang = "ru"
    await state.update_data(lang=lang)
    await db.touch_user(query.from_user.id, language=lang)
    await query.answer()
    await _gate_or_start(query.message, state, bot, config, query.from_user.id, lang, db)


@router.callback_query(F.data == keyboards.CB_CHECK_SUB)
async def check_subscription(
    query: CallbackQuery, state: FSMContext, bot: Bot, config: Config | TenantConfig, db: Database
) -> None:
    active = await db.has_active_application(query.from_user.id)
    if active is not None:
        from .mynumber import show_status

        await show_status(query.message, active)
        await query.answer()
        return

    lang = await _lang(state)
    if getattr(config, "registration_closed", False):
        await query.answer()
        await query.message.answer(texts.registration_closed_for_tenant(lang, config))
        return

    if await subscription.is_subscribed(bot, config.required_channel, query.from_user.id):
        await query.answer()
        await _start_form(query.message, state, lang, config)
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
async def set_plate(message: Message, state: FSMContext, config: Config | TenantConfig, db: Database) -> None:
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

    # Load tenant-specific directions from DB; fallback to legacy static list
    db_directions = await _load_tenant_directions(db, config)
    if db_directions:
        await message.answer(
            texts.T(lang).ASK_DIRECTION,
            reply_markup=keyboards.direction_keyboard_from_db(db_directions, lang=lang),
        )
    else:
        await message.answer(
            texts.T(lang).ASK_DIRECTION, reply_markup=keyboards.direction_keyboard(lang)
        )


# --- Direction (right after the plate, before photos) — now DB-aware with podnapravleniya ---
@router.callback_query(Registration.direction, F.data.startswith(f"{keyboards.CB_DIRECTION}:"))
async def choose_direction(query: CallbackQuery, state: FSMContext, config: Config | TenantConfig, db: Database) -> None:
    lang = await _lang(state)
    raw = query.data.split(":", 1)[1]

    # Try DB path first: callback is direction:<id>
    db_directions = await _load_tenant_directions(db, config)
    if db_directions:
        try:
            dir_id = int(raw)
            # Find selected
            selected = next((d for d in db_directions if d.id == dir_id), None)
            if selected is None:
                await query.answer(texts.T(lang).DIRECTION_PICKED.format(direction=""), show_alert=True)
                return
            # Check if has children
            children = [d for d in db_directions if d.parent_id == selected.id and d.is_active]
            if children:
                # Show sub-direction selection
                await state.update_data(direction_parent_id=selected.id, direction_parent_canonical=selected.canonical)
                await state.set_state(Registration.sub_direction)
                await query.message.answer(
                    texts.T(lang).ASK_DIRECTION,  # Could add ASK_SUB_DIRECTION key later
                    reply_markup=keyboards.direction_keyboard_from_db(
                        db_directions, lang=lang, parent_id=selected.id
                    ),
                )
                await query.answer()
                return
            # Leaf direction — store final
            final_str = format_final_choice(db_directions, leaf_id=selected.id)
            await state.update_data(direction=final_str, direction_id=selected.id)
            await state.set_state(Registration.photos)
            banner = direction_image_path(selected.canonical, getattr(config, "asset_scope", None))
            if banner:
                try:
                    await query.message.answer_photo(
                        FSInputFile(banner),
                        caption=texts.T(lang).DIRECTION_PICKED.format(
                            direction=localized_final_choice(db_directions, leaf_id=selected.id, lang=lang)
                        ),
                    )
                except Exception:
                    logger.exception("Could not send direction banner for %s", selected.canonical)
            await query.message.answer(texts.T(lang).PHOTO_PROMPTS[0])
            await query.answer()
            return
        except ValueError:
            # Not an int — fall through to legacy
            pass

    # Legacy fallback: index into static DIRECTIONS_CANON
    try:
        idx = int(raw)
        canonical = texts.DIRECTIONS_CANON[idx]
    except Exception:
        canonical = raw
    await state.update_data(direction=canonical, direction_id=None)
    await state.set_state(Registration.photos)

    banner = direction_image_path(canonical, getattr(config, "asset_scope", None))
    if banner:
        try:
            await query.message.answer_photo(
                FSInputFile(banner),
                caption=texts.T(lang).DIRECTION_PICKED.format(
                    direction=texts.localize_direction(canonical, lang)
                ),
            )
        except Exception:
            logger.exception("Could not send direction banner for %s", canonical)

    await query.message.answer(texts.T(lang).PHOTO_PROMPTS[0])
    await query.answer()


@router.callback_query(Registration.sub_direction, F.data.startswith(f"{keyboards.CB_SUB_DIRECTION}:"))
async def choose_sub_direction(query: CallbackQuery, state: FSMContext, config: Config | TenantConfig, db: Database) -> None:
    """Second level: podnapravleniya. Callback format subdirection:parent_id:child_id."""
    lang = await _lang(state)
    try:
        _, parent_s, child_s = query.data.split(":")
        parent_id = int(parent_s)
        child_id = int(child_s)
    except Exception:
        await query.answer()
        return

    db_directions = await _load_tenant_directions(db, config)
    if not db_directions:
        await query.answer()
        return

    final_str = format_final_choice(db_directions, leaf_id=child_id)
    if not final_str:
        # Child not found, fallback
        child = next((d for d in db_directions if d.id == child_id), None)
        final_str = child.canonical if child else ""

    await state.update_data(direction=final_str, direction_id=child_id, direction_parent_id=None)
    await state.set_state(Registration.photos)

    # Banner for final choice
    leaf = next((d for d in db_directions if d.id == child_id), None)
    canonical = leaf.canonical if leaf else final_str
    banner = direction_image_path(canonical, getattr(config, "asset_scope", None))
    if banner:
        try:
            await query.message.answer_photo(
                FSInputFile(banner),
                caption=texts.T(lang).DIRECTION_PICKED.format(
                    direction=localized_final_choice(db_directions, leaf_id=child_id, lang=lang)
                ),
            )
        except Exception:
            logger.exception("Could not send direction banner for %s", canonical)

    await query.message.answer(texts.T(lang).PHOTO_PROMPTS[0])
    await query.answer()


# --- Photos (4, one by one) ---
@router.message(Registration.photos, F.photo)
async def collect_photo(message: Message, state: FSMContext, bot: Bot, config: Config | TenantConfig) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    file_ids: list[str] = data.get("photo_file_ids", [])
    paths: list[str] = data.get("photo_paths", [])

    index = len(file_ids)
    side = SIDES[index]
    user_dir = os.path.join(config.media_dir, str(message.from_user.id))
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, f"{side}.jpg")

    photo = message.photo[-1]
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


# --- Modifications ---
async def _ask_mods(message: Message, state: FSMContext, lang: str) -> None:
    await state.set_state(Registration.mods)
    await message.answer(
        texts.T(lang).ASK_MODS.format(max=MAX_MOD_PHOTOS),
        reply_markup=keyboards.mods_keyboard(lang, has_photos=False),
    )


@router.message(Registration.mods, F.photo)
async def collect_mod_photo(message: Message, state: FSMContext, bot: Bot, config: Config | TenantConfig) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    file_ids: list[str] = data.get("mod_file_ids", [])
    paths: list[str] = data.get("mod_paths", [])
    t = texts.T(lang)

    user_dir = os.path.join(config.media_dir, str(message.from_user.id))
    os.makedirs(user_dir, exist_ok=True)
    path = os.path.join(user_dir, f"mod_{len(file_ids) + 1}.jpg")

    photo = message.photo[-1]
    await bot.download(photo, destination=path)

    file_ids.append(photo.file_id)
    paths.append(path)
    await state.update_data(mod_file_ids=file_ids, mod_paths=paths)

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
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
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
    message: Message, state: FSMContext, bot: Bot, config: Config | TenantConfig, db: Database
) -> None:
    phone = clean_phone(message.contact.phone_number) or message.contact.phone_number
    await _finalize(message, state, bot, config, db, phone=phone)


@router.message(Registration.phone, F.text)
async def set_phone_text(
    message: Message, state: FSMContext, bot: Bot, config: Config | TenantConfig, db: Database
) -> None:
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
    config: Config | TenantConfig,
    db: Database,
    *,
    phone: str,
) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    direction_id = data.get("direction_id")
    app_id = await db.create_application(
        user_id=message.from_user.id,
        username=_user_label(message),
        full_name=message.from_user.full_name or "",
        country=data.get("country", ""),
        plate=data.get("plate", ""),
        direction=data.get("direction", ""),
        direction_id=direction_id,
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
    config: Config | TenantConfig,
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
    except Exception:
        logger.exception("Failed to send moderation card for application %s", app_id)


def create_router() -> Router:
    fresh = Router(name="registration")
    fresh.message.register(cmd_start, CommandStart())
    fresh.callback_query.register(
        choose_language,
        Registration.language,
        F.data.startswith(f"{keyboards.CB_LANG}:"),
    )
    fresh.callback_query.register(check_subscription, F.data == keyboards.CB_CHECK_SUB)
    fresh.callback_query.register(
        choose_country,
        Registration.country,
        F.data.startswith(f"{keyboards.CB_COUNTRY}:"),
    )
    fresh.message.register(country_other, Registration.country_other, F.text)
    fresh.message.register(set_plate, Registration.plate, F.text)
    fresh.callback_query.register(
        choose_direction,
        Registration.direction,
        F.data.startswith(f"{keyboards.CB_DIRECTION}:"),
    )
    fresh.callback_query.register(
        choose_sub_direction,
        Registration.sub_direction,
        F.data.startswith(f"{keyboards.CB_SUB_DIRECTION}:"),
    )
    fresh.message.register(collect_photo, Registration.photos, F.photo)
    fresh.message.register(photos_not_a_photo, Registration.photos)
    fresh.message.register(collect_mod_photo, Registration.mods, F.photo)
    fresh.callback_query.register(mods_done, Registration.mods, F.data == keyboards.CB_MODS_DONE)
    fresh.message.register(mods_not_a_photo, Registration.mods)
    fresh.message.register(set_phone_contact, Registration.phone, F.contact)
    fresh.message.register(set_phone_text, Registration.phone, F.text)
    return fresh
