"""Registration flow: /start → language → subscription gate → form → moderation.

Tenant-branded:
- greetings, subscribe, closed messages from TenantConfig
- directions loaded from DB (tenant-specific, 2-level)
- stores direction as "Parent — Child" string + direction_id for export

Robustness rules (learned from production incidents):

* the tenant's direction list is loaded through whichever database facade the
  dispatcher injected, so the bot never silently falls back to the legacy
  global list;
* every step answers the participant — a button whose data no longer matches
  anything re-asks the current question instead of doing nothing;
* photo updates are de-duplicated and download failures are reported, so a
  single bad upload can neither desynchronise the four sides nor drop a photo
  without a word;
* ``/start`` in the middle of the form asks whether to continue or restart
  instead of silently deleting the collected answers.
"""
from __future__ import annotations

import inspect
import logging
import os
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message
from aiogram.utils.media_group import MediaGroupBuilder

from .. import keyboards, texts
from ..config import Config, TenantConfig
from ..constants import MAX_MOD_PHOTOS, SIDES, direction_image_path
from ..db import Database, Direction
from ..services import assets, media, subscription
from ..services.directions import (
    children_of,
    find_direction_by_id,
    format_final_choice,
    label_for,
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
    """Load the active directions of the tenant this update belongs to.

    Two facades reach this function in production: the tenant-scoped one the
    dispatcher injects (``TenantDatabase``, already filtered by tenant) and the
    global ``Database`` used by scripts/tests, which needs an explicit
    ``tenant_id``.  Calling the wrong one raised ``TypeError`` inside a broad
    ``except``, so the bot fell back to the *legacy global* direction list and
    participants of one event saw another event's categories.  The signature is
    now inspected instead of guessed.
    """
    try:
        loader = db.list_directions
        kwargs: dict = {"active_only": True}
        if "tenant_id" in inspect.signature(loader).parameters:
            tid = _config_tenant_id(config)
            if tid is None:
                return []
            kwargs["tenant_id"] = tid
        return list(await loader(**kwargs))
    except Exception:
        logger.exception(
            "Failed to load directions for tenant %s",
            getattr(config, "tenant_slug", "unknown"),
        )
        return []


async def _gate_or_start(
    message: Message,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    user_id: int,
    lang: str,
    db: Database | None = None,
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

    # /start used to clear the state unconditionally.  Testers (and real
    # participants) tap it in the middle of the form — often because an old
    # answer told them to — and then had to fill in four photos again
    # ("заново опять всё делает").  Ask instead of wiping.
    data = await state.get_data()
    active_state = await state.get_state()
    if active_state is not None and data.get("lang"):
        lang = data.get("lang", "ru")
        await message.answer(
            texts.T(lang).FORM_IN_PROGRESS,
            reply_markup=keyboards.continue_or_restart_keyboard(lang),
        )
        return

    await state.clear()

    active = await db.has_active_application(message.from_user.id)
    if active is not None:
        from .mynumber import show_status

        # Tenant config is mandatory here: without it the legacy Promotors
        # template answered a SPL participant with September dates.
        await show_status(message, active, config)
        return

    if getattr(config, "registration_closed", False):
        await message.answer(texts.registration_closed_bilingual_for_tenant(config))
        return

    await state.set_state(Registration.language)
    await message.answer(texts.ASK_LANGUAGE, reply_markup=keyboards.language_keyboard())


@router.callback_query(F.data == keyboards.CB_FLOW_CONTINUE)
async def flow_continue(
    query: CallbackQuery, state: FSMContext, config: Config | TenantConfig, db: Database
) -> None:
    """Keep the collected answers and repeat the current step."""
    lang = (await state.get_data()).get("lang", "ru")
    await query.answer()
    if query.message is None:
        return
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - the message may be too old to edit
        logger.debug("Could not clear the resume keyboard", exc_info=True)
    if not await _repeat_step(query.message, state, lang, config, db):
        await state.clear()
        await _start_form(query.message, state, lang, config)


@router.callback_query(F.data == keyboards.CB_FLOW_RESTART)
async def flow_restart(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
) -> None:
    """Drop the collected answers and start the form from the beginning."""
    lang = (await state.get_data()).get("lang", "ru")
    await query.answer()
    await state.clear()
    if query.message is None:
        return
    await _gate_or_start(query.message, state, bot, config, query.from_user.id, lang, db)


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

        await show_status(query.message, active, config)
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

    try:
        country = texts.COUNTRIES_CANON[int(value)]
    except (ValueError, IndexError):
        # Keyboard from an older message (or a broken client) — re-ask instead
        # of failing silently.
        await query.answer(texts.T(lang).STEP_STALE)
        await query.message.answer(
            texts.T(lang).ASK_COUNTRY, reply_markup=keyboards.country_keyboard(lang)
        )
        return

    await state.update_data(country=country)
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
        direction=None,
        direction_id=None,
        direction_parent_id=None,
    )
    await _ask_direction(message, state, lang, config, db)


async def _ask_direction(
    message: Message,
    state: FSMContext,
    lang: str,
    config: Config | TenantConfig,
    db: Database,
    *,
    parent_id: Optional[int] = None,
    directions: Optional[list] = None,
) -> None:
    """Ask for the direction (or its sub-direction when ``parent_id`` is given)."""
    if directions is None:
        directions = await _load_tenant_directions(db, config)
    if not directions:
        # No DB directions for this tenant: keep the historic static keyboard
        # (old single-tenant installs).  For any other tenant this is a setup
        # mistake worth seeing in the log — the list below is Promotors'.
        if getattr(config, "tenant_slug", "promotors") != "promotors":
            logger.warning(
                "Tenant %s has no directions in the database — falling back to the "
                "legacy global list. Add its directions in the admin panel.",
                getattr(config, "tenant_slug", "?"),
            )
        await state.set_state(Registration.direction)
        await message.answer(
            texts.T(lang).ASK_DIRECTION, reply_markup=keyboards.direction_keyboard(lang)
        )
        return

    if parent_id is not None:
        parent = find_direction_by_id(directions, parent_id)
        prompt = (
            texts.T(lang).ASK_SUB_DIRECTION.format(parent=label_for(parent, lang))
            if parent is not None
            else texts.T(lang).ASK_SUB_DIRECTION_PLAIN
        )
        await state.set_state(Registration.sub_direction)
    else:
        prompt = texts.T(lang).ASK_DIRECTION
        await state.set_state(Registration.direction)

    await message.answer(
        prompt,
        reply_markup=keyboards.direction_keyboard_from_db(
            directions, lang=lang, parent_id=parent_id
        ),
    )


def _direction_banner(direction: Direction, config: Config | TenantConfig) -> Optional[str]:
    """Banner for a DB direction — the tenant's own upload always wins."""
    scope = getattr(config, "asset_scope", None) or _config_tenant_id(config)
    slug = getattr(direction, "slug", "") or ""
    if slug:
        found = assets.direction_banner(slug, scope)
        if found:
            return found
    return direction_image_path(direction.canonical, scope)


async def _accept_direction(
    message: Message,
    state: FSMContext,
    lang: str,
    config: Config | TenantConfig,
    directions: list,
    leaf_id: int,
) -> bool:
    """Store a leaf direction and move on to the photos."""
    leaf = find_direction_by_id(directions, leaf_id)
    final_str = format_final_choice(directions, leaf_id)
    if leaf is None or not final_str:
        return False
    await state.update_data(
        direction=final_str, direction_id=leaf.id, direction_parent_id=None
    )
    await state.set_state(Registration.photos)

    picked = texts.T(lang).DIRECTION_PICKED.format(
        direction=localized_final_choice(directions, leaf.id, lang)
    )
    banner = _direction_banner(leaf, config)
    if banner:
        try:
            await message.answer_photo(FSInputFile(banner), caption=picked)
        except Exception:
            logger.exception("Could not send direction banner for %s", leaf.canonical)
            await message.answer(picked)
    else:
        await message.answer(picked)
    await message.answer(texts.T(lang).PHOTO_PROMPTS[0])
    return True


# --- Direction (right after the plate, before photos) — DB-aware, 2 levels ---
@router.callback_query(Registration.direction, F.data.startswith(f"{keyboards.CB_DIRECTION}:"))
async def choose_direction(
    query: CallbackQuery, state: FSMContext, config: Config | TenantConfig, db: Database
) -> None:
    lang = await _lang(state)
    raw = query.data.split(":", 1)[1]
    await query.answer()

    db_directions = await _load_tenant_directions(db, config)
    if db_directions:
        try:
            dir_id = int(raw)
        except (TypeError, ValueError):
            dir_id = None
        selected = find_direction_by_id(db_directions, dir_id)
        if selected is not None:
            # Parents with children open the second level.
            if children_of(db_directions, selected.id):
                await state.update_data(direction_parent_id=selected.id)
                await state.set_state(Registration.sub_direction)
                await query.message.answer(
                    texts.T(lang).ASK_SUB_DIRECTION.format(
                        parent=label_for(selected, lang)
                    ),
                    reply_markup=keyboards.direction_keyboard_from_db(
                        db_directions, lang=lang, parent_id=selected.id
                    ),
                )
                return
            if await _accept_direction(
                query.message, state, lang, config, db_directions, leaf_id=selected.id
            ):
                return
        # The direction was deleted (or the keyboard is stale) — ask again
        # instead of leaving the participant without an answer.
        logger.info("Unknown direction id %r for tenant %s", raw, getattr(config, "tenant_slug", ""))
        await query.message.answer(texts.T(lang).STEP_STALE)
        await _ask_direction(query.message, state, lang, config, db, directions=db_directions)
        return

    # Legacy fallback: index into the static DIRECTIONS_CANON list.
    canonical = raw
    try:
        canonical = texts.DIRECTIONS_CANON[int(raw)]
    except (ValueError, IndexError, KeyError):
        canonical = raw
    await state.update_data(direction=canonical, direction_id=None)
    await state.set_state(Registration.photos)

    banner = direction_image_path(canonical, getattr(config, "asset_scope", None))
    picked = texts.T(lang).DIRECTION_PICKED.format(
        direction=texts.localize_direction(canonical, lang)
    )
    if banner:
        try:
            await query.message.answer_photo(FSInputFile(banner), caption=picked)
        except Exception:
            logger.exception("Could not send direction banner for %s", canonical)
            await query.message.answer(picked)
    else:
        await query.message.answer(picked)

    await query.message.answer(texts.T(lang).PHOTO_PROMPTS[0])


@router.callback_query(Registration.sub_direction, F.data.startswith(f"{keyboards.CB_SUB_DIRECTION}:"))
async def choose_sub_direction(
    query: CallbackQuery, state: FSMContext, config: Config | TenantConfig, db: Database
) -> None:
    """Second level: podnapravleniya. Callback format ``subdirection:parent:child``."""
    lang = await _lang(state)
    try:
        _, parent_s, child_s = query.data.split(":")
        parent_id = int(parent_s)
        child_id = int(child_s)
    except Exception:
        await query.answer()
        return
    await query.answer()

    db_directions = await _load_tenant_directions(db, config)
    if not db_directions:
        await _ask_direction(query.message, state, lang, config, db, directions=[])
        return

    child = find_direction_by_id(db_directions, child_id)
    if child is None or child.parent_id != parent_id:
        logger.info(
            "Stale sub-direction %s/%s for tenant %s",
            parent_id,
            child_id,
            getattr(config, "tenant_slug", ""),
        )
        await query.message.answer(texts.T(lang).STEP_STALE)
        await _ask_direction(
            query.message, state, lang, config, db, parent_id=parent_id, directions=db_directions
        )
        return

    if not await _accept_direction(
        query.message, state, lang, config, db_directions, leaf_id=child_id
    ):
        await query.message.answer(texts.T(lang).STEP_STALE)
        await _ask_direction(
            query.message, state, lang, config, db, parent_id=parent_id, directions=db_directions
        )


# --- Photos (4, one by one) ---
def _photo_path(config: Config | TenantConfig, user_id: int, name: str) -> Optional[str]:
    """Destination for one upload, or ``None`` when the volume is not writable.

    A full/read-only media volume would otherwise raise inside the handler and
    leave the participant without any answer at all.
    """
    try:
        user_dir = os.path.join(config.media_dir, str(user_id))
        os.makedirs(user_dir, exist_ok=True)
        return os.path.join(user_dir, name)
    except OSError:
        logger.exception(
            "Media directory %s is not writable", getattr(config, "media_dir", "?")
        )
        return None


async def _download_photo(bot: Bot, file_id: str, path: str) -> bool:
    """Download one Telegram photo, reporting success instead of raising.

    Bounded (see :mod:`bot.services.media`) because this runs inside the
    per-user lock: an unbounded download is a silent freeze for that
    participant, and the write happens on the slow-work pool so the disk volume
    cannot take the database's threads with it.
    """
    return await media.save_telegram_photo(bot, file_id, path)


async def _next_photo_prompt(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    collected = len(data.get("photo_file_ids", []))
    if collected < len(SIDES):
        await message.answer(texts.T(lang).PHOTO_PROMPTS[collected])
    else:
        await _ask_mods(message, state, lang)


@router.message(Registration.photos, F.photo)
async def collect_photo(message: Message, state: FSMContext, bot: Bot, config: Config | TenantConfig) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    file_ids: list[str] = list(data.get("photo_file_ids", []))
    paths: list[str] = list(data.get("photo_paths", []))

    photo = message.photo[-1]
    # Telegram may deliver the same update twice (retry after a slow handler or
    # a redeploy). Counting it twice would overflow the four sides and crash on
    # SIDES[4], which left the participant without any answer.
    if photo.file_id in file_ids:
        logger.debug("Ignoring duplicate photo %s", photo.file_id)
        return
    if len(file_ids) >= len(SIDES):
        await _ask_mods(message, state, lang)
        return

    index = len(file_ids)
    side = SIDES[index]
    path = _photo_path(config, message.from_user.id, f"{side}.jpg")

    if path is None or not await _download_photo(bot, photo.file_id, path):
        await message.answer(texts.T(lang).PHOTO_DOWNLOAD_FAILED)
        return

    file_ids.append(photo.file_id)
    paths.append(path)
    await state.update_data(photo_file_ids=file_ids, photo_paths=paths)
    await _next_photo_prompt(message, state, lang)


@router.message(Registration.photos)
async def photos_not_a_photo(message: Message, state: FSMContext) -> None:
    await message.answer(texts.T(await _lang(state)).PHOTO_NOT_A_PHOTO)


# --- Modifications ---
async def _ask_mods(message: Message, state: FSMContext, lang: str) -> None:
    data = await state.get_data()
    await state.set_state(Registration.mods)
    await message.answer(
        texts.T(lang).ASK_MODS.format(max=MAX_MOD_PHOTOS),
        reply_markup=keyboards.mods_keyboard(
            lang, has_photos=bool(data.get("mod_file_ids"))
        ),
    )


@router.message(Registration.mods, F.photo)
async def collect_mod_photo(message: Message, state: FSMContext, bot: Bot, config: Config | TenantConfig) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    file_ids: list[str] = list(data.get("mod_file_ids", []))
    paths: list[str] = list(data.get("mod_paths", []))
    t = texts.T(lang)

    photo = message.photo[-1]
    if photo.file_id in file_ids:
        logger.debug("Ignoring duplicate modification photo %s", photo.file_id)
        return

    path = _photo_path(config, message.from_user.id, f"mod_{len(file_ids) + 1}.jpg")

    if path is None or not await _download_photo(bot, photo.file_id, path):
        await message.answer(t.PHOTO_DOWNLOAD_FAILED)
        return

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
        db,
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
    db: Database | None = None,
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
        sent = await bot.send_message(
            config.admin_chat_id,
            card,
            reply_markup=keyboards.moderation_keyboard(app_id),
        )
        # Remember the card so a decision taken in the web panel can update this
        # very message (status line + buttons removed) instead of leaving the
        # chat with an untouched "pending" card.
        if db is not None and getattr(sent, "message_id", None):
            try:
                await db.set_card_message_id(app_id, int(sent.message_id))
            except Exception:
                logger.debug("Could not store moderation card id", exc_info=True)
    except Exception:
        logger.exception("Failed to send moderation card for application %s", app_id)


# ---------------------------------------------------------------------------
# Recovery handlers — the last resort that keeps the bot from going silent.
#
# In polling mode aiogram only logs an exception raised inside a handler: the
# participant gets no answer at all, which looks exactly like "the bot froze".
# These two handlers run only when nothing else matched the update *and* the
# user is inside the form, so they can re-ask the current question (or tell the
# participant to start over) instead of doing nothing.
# ---------------------------------------------------------------------------

_GLOBAL_ACTIONS = texts.MY_NUMBER_LABELS | {"/start", "/mynumber"}


def _is_form_message(message: Message) -> bool:
    """True for ordinary input (not a global command/button) inside the form."""
    if message.chat.type != "private":
        return False
    text = (message.text or "").strip()
    if text.startswith("/"):
        return False
    return text not in _GLOBAL_ACTIONS


async def _repeat_step(
    message: Message,
    state: FSMContext,
    lang: str,
    config: Config | TenantConfig,
    db: Database,
) -> bool:
    """Re-send the question of the step the participant is currently on."""
    current = await state.get_state()
    t = texts.T(lang)
    if current == Registration.language.state:
        await message.answer(texts.ASK_LANGUAGE, reply_markup=keyboards.language_keyboard())
        return True
    if current == Registration.country.state:
        await message.answer(t.ASK_COUNTRY, reply_markup=keyboards.country_keyboard(lang))
        return True
    if current == Registration.country_other.state:
        await message.answer(t.ASK_COUNTRY_OTHER)
        return True
    if current == Registration.plate.state:
        await message.answer(t.ASK_PLATE)
        return True
    if current in (Registration.direction.state, Registration.sub_direction.state):
        data = await state.get_data()
        parent_id = (
            data.get("direction_parent_id")
            if current == Registration.sub_direction.state
            else None
        )
        await _ask_direction(message, state, lang, config, db, parent_id=parent_id)
        return True
    if current == Registration.photos.state:
        data = await state.get_data()
        collected = len(data.get("photo_file_ids", []))
        if collected < len(SIDES):
            await message.answer(t.PHOTO_PROMPTS[collected])
        else:
            await _ask_mods(message, state, lang)
        return True
    if current == Registration.mods.state:
        await _ask_mods(message, state, lang)
        return True
    if current == Registration.phone.state:
        await _ask_phone(message, state, lang)
        return True
    return False


@router.message(StateFilter(*Registration.__all_states__))
async def unexpected_message(
    message: Message, state: FSMContext, config: Config | TenantConfig, db: Database
) -> None:
    """Input that no step expected — repeat the step rather than stay silent.

    Global actions (``/start``, ``/mynumber``, the "Узнать свой номер" button)
    are passed on with :class:`SkipHandler` so they keep reaching their own
    handlers instead of being swallowed by this safety net.
    """
    if not _is_form_message(message):
        raise SkipHandler
    data = await state.get_data()
    lang = data.get("lang", "ru")
    if await _repeat_step(message, state, lang, config, db):
        return
    await state.clear()
    await message.answer(texts.T(lang).RECOVER_RESTART)


@router.callback_query(StateFilter(*Registration.__all_states__))
async def stale_callback(
    query: CallbackQuery, state: FSMContext, config: Config | TenantConfig, db: Database
) -> None:
    """A button from an older message: answer it and repeat the current step."""
    data = await state.get_data()
    lang = data.get("lang", "ru")
    await query.answer(texts.T(lang).STEP_STALE)
    if query.message is None:
        return
    if not await _repeat_step(query.message, state, lang, config, db):
        await state.clear()
        await query.message.answer(texts.T(lang).RECOVER_RESTART)


def create_router() -> Router:
    fresh = Router(name="registration")
    fresh.message.register(cmd_start, CommandStart())
    fresh.callback_query.register(flow_continue, F.data == keyboards.CB_FLOW_CONTINUE)
    fresh.callback_query.register(flow_restart, F.data == keyboards.CB_FLOW_RESTART)
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
    # Keep these two last: they only run when no step above matched.
    fresh.message.register(unexpected_message, StateFilter(*Registration.__all_states__))
    fresh.callback_query.register(stale_callback, StateFilter(*Registration.__all_states__))
    return fresh
