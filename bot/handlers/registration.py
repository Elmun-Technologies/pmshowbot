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
* photo updates are answered *before* the bytes are fetched (the download runs
  as its own task, see ``bot.services.media.PhotoIngest``), so a slow or stalled
  photo delays nothing else — not the next prompt, not the next message, and not
  the other three photos of an album, which are now fetched at the same time;
* photo updates are de-duplicated and download failures are reported, so a
  single bad upload can neither desynchronise the four sides nor drop a photo
  without a word, and the side a participant is asked to send again lands back
  in its own slot instead of shifting the car's sides;
* ``/start`` in the middle of the form asks whether to continue or restart
  instead of silently deleting the collected answers.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import os
from typing import Optional

from aiogram import Bot, F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
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
        direction_ids=[],
        direction_parent_id=None,
    )
    await _ask_direction(message, state, lang, config, db)


MAX_DIRECTIONS = texts.MAX_DIRECTIONS


def _children_map(directions: list) -> dict[int, list[int]]:
    """Parent id → its active child ids (used to hide fully-picked parents)."""
    out: dict[int, list[int]] = {}
    for d in directions:
        if d.parent_id is not None and d.is_active:
            out.setdefault(d.parent_id, []).append(d.id)
    return out


def _selected_ids(data: dict, directions: list) -> list[int]:
    """Leaf ids picked so far that still exist (a deleted one is dropped)."""
    known = {d.id for d in directions}
    return [i for i in (data.get("direction_ids") or []) if i in known]


def _selected_lines(directions: list, ids: list[int], lang: str) -> str:
    return "\n".join(f"• {localized_final_choice(directions, i, lang)}" for i in ids)


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
    """Open the direction menu (or a parent's categories when ``parent_id`` is set).

    The participant may pick up to :data:`MAX_DIRECTIONS` categories: the menu
    reopens after every pick, without what is already chosen, and «Готово»
    finishes the choice.
    """
    t = texts.T(lang)
    if directions is None:
        directions = await _load_tenant_directions(db, config)
    if not directions:
        # No DB directions for this tenant: keep the historic static keyboard
        # (old single-tenant installs, single choice).  For any other tenant this
        # is a setup mistake worth seeing in the log — the list below is Promotors'.
        if getattr(config, "tenant_slug", "promotors") != "promotors":
            logger.warning(
                "Tenant %s has no directions in the database — falling back to the "
                "legacy global list. Add its directions in the admin panel.",
                getattr(config, "tenant_slug", "?"),
            )
        await state.set_state(Registration.direction)
        await message.answer(
            t.ASK_DIRECTION.format(max=MAX_DIRECTIONS),
            reply_markup=keyboards.direction_keyboard(lang),
        )
        return

    data = await state.get_data()
    selected = _selected_ids(data, directions)
    if parent_id is not None:
        parent = find_direction_by_id(directions, parent_id)
        prompt = (
            t.ASK_SUB_DIRECTION.format(parent=label_for(parent, lang), max=MAX_DIRECTIONS)
            if parent is not None
            else t.ASK_SUB_DIRECTION_PLAIN
        )
        await state.update_data(direction_parent_id=parent_id)
        await state.set_state(Registration.sub_direction)
    else:
        prompt = t.ASK_DIRECTION.format(max=MAX_DIRECTIONS)
        await state.update_data(direction_parent_id=None)
        await state.set_state(Registration.direction)
    if selected:
        prompt = (
            t.DIRECTIONS_SELECTED.format(
                n=len(selected), max=MAX_DIRECTIONS,
                items=_selected_lines(directions, selected, lang),
            )
            + "\n\n" + t.DIRECTIONS_MORE_HINT.format(done=t.BTN_DIRECTIONS_DONE)
            + "\n\n" + prompt
        )

    await message.answer(
        prompt,
        reply_markup=keyboards.direction_keyboard_from_db(
            directions,
            lang=lang,
            parent_id=parent_id,
            selected=selected,
            children_by_parent=_children_map(directions),
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


async def _add_direction(
    message: Message,
    state: FSMContext,
    bot: Bot,
    lang: str,
    config: Config | TenantConfig,
    db: Database,
    directions: list,
    leaf_id: int,
) -> bool:
    """Add one picked leaf; reopen the menu, or finish at the limit."""
    leaf = find_direction_by_id(directions, leaf_id)
    if leaf is None or not format_final_choice(directions, leaf_id):
        return False
    data = await state.get_data()
    selected = _selected_ids(data, directions)
    if leaf_id not in selected:
        if len(selected) >= MAX_DIRECTIONS:
            await message.answer(texts.T(lang).DIRECTION_LIMIT.format(max=MAX_DIRECTIONS))
            await _finish_directions(message, state, bot, lang, config, directions)
            return True
        selected.append(leaf_id)
    await state.update_data(direction_ids=selected)
    if len(selected) >= MAX_DIRECTIONS:
        await _finish_directions(message, state, bot, lang, config, directions)
        return True
    # Reopen the menu.  Inside a parent that still has categories left, stay
    # there — SPL Avtozvuk has 16 of them and most people pick several.
    parent_id = leaf.parent_id
    if parent_id is not None:
        remaining = [c for c in children_of(directions, parent_id) if c.id not in selected]
        if not remaining:
            parent_id = None
    await _ask_direction(
        message, state, lang, config, db, parent_id=parent_id, directions=directions
    )
    return True


async def _finish_directions(
    message: Message,
    state: FSMContext,
    bot: Bot,
    lang: str,
    config: Config | TenantConfig,
    directions: list,
) -> bool:
    """Store the chosen categories and move on to the photos."""
    data = await state.get_data()
    selected = _selected_ids(data, directions)
    if not selected:
        return False
    stored = texts.join_directions([format_final_choice(directions, i) for i in selected])
    await state.update_data(
        direction=stored,
        direction_id=selected[0],
        direction_ids=selected,
        direction_parent_id=None,
    )
    await state.set_state(Registration.photos)

    t = texts.T(lang)
    if len(selected) == 1:
        picked = t.DIRECTION_PICKED.format(
            direction=localized_final_choice(directions, selected[0], lang)
        )
    else:
        picked = t.DIRECTIONS_PICKED.format(items=_selected_lines(directions, selected, lang))
    first = find_direction_by_id(directions, selected[0])
    banner = _direction_banner(first, config) if first is not None else None
    if banner:
        # Sent through the file_id cache: the banner is uploaded once per bot,
        # not once per registration.
        if not await media.send_cached_photo(bot, message.chat.id, banner, picked):
            await message.answer(picked)
    else:
        await message.answer(picked)
    await message.answer(t.PHOTO_PROMPTS[0])
    return True


async def _accept_direction(
    message: Message,
    state: FSMContext,
    bot: Bot,
    lang: str,
    config: Config | TenantConfig,
    directions: list,
    leaf_id: int,
    db: Database | None = None,
) -> bool:
    """Pick one leaf (kept under its historic name for callers and tests)."""
    return await _add_direction(message, state, bot, lang, config, db, directions, leaf_id)


# --- Direction (right after the plate, before photos) — DB-aware, multi-select ---
@router.callback_query(Registration.direction, F.data.startswith(f"{keyboards.CB_DIRECTION}:"))
async def choose_direction(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
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
        if selected is not None and selected.parent_id is None:
            # Parents with children open the second level.
            if children_of(db_directions, selected.id):
                await _ask_direction(
                    query.message, state, lang, config, db,
                    parent_id=selected.id, directions=db_directions,
                )
                return
            if await _accept_direction(
                query.message, state, bot, lang, config, db_directions,
                leaf_id=selected.id, db=db,
            ):
                return
        # The direction was deleted (or the keyboard is stale) — ask again
        # instead of leaving the participant without an answer.
        logger.info("Unknown direction id %r for tenant %s", raw, getattr(config, "tenant_slug", ""))
        await query.message.answer(texts.T(lang).STEP_STALE)
        await _ask_direction(query.message, state, lang, config, db, directions=db_directions)
        return

    # Legacy fallback (no DB directions): index into the static list, single choice.
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
    if not banner or not await media.send_cached_photo(
        bot, query.message.chat.id, banner, picked
    ):
        await query.message.answer(picked)

    await query.message.answer(texts.T(lang).PHOTO_PROMPTS[0])


@router.callback_query(Registration.sub_direction, F.data.startswith(f"{keyboards.CB_SUB_DIRECTION}:"))
async def choose_sub_direction(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
) -> None:
    """Second level: categories. Callback format ``subdirection:parent:child``."""
    lang = await _lang(state)
    try:
        _, parent_s, child_s = query.data.split(":")
        parent_id = int(parent_s)
        child_id = int(child_s)
    except Exception:
        await query.answer()
        await _repeat_step(query.message, state, lang, config, db)
        return
    await query.answer()

    db_directions = await _load_tenant_directions(db, config)
    if not db_directions:
        await _ask_direction(query.message, state, lang, config, db, directions=[])
        return

    child = find_direction_by_id(db_directions, child_id)
    if child is None or child.parent_id != parent_id or not await _accept_direction(
        query.message, state, bot, lang, config, db_directions, leaf_id=child_id, db=db
    ):
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


@router.callback_query(
    StateFilter(Registration.direction, Registration.sub_direction),
    F.data == keyboards.CB_DIRECTIONS_BACK,
)
async def directions_back(
    query: CallbackQuery, state: FSMContext, config: Config | TenantConfig, db: Database
) -> None:
    """«Назад» inside a parent: back to the root menu, choices kept."""
    lang = await _lang(state)
    await query.answer()
    await _ask_direction(query.message, state, lang, config, db)


@router.callback_query(
    StateFilter(Registration.direction, Registration.sub_direction),
    F.data == keyboards.CB_DIRECTIONS_DONE,
)
async def directions_done(
    query: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
) -> None:
    """«Готово»: finish with the 1–4 categories picked so far."""
    lang = await _lang(state)
    await query.answer()
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:  # noqa: BLE001 - the message may be too old to edit
        logger.debug("Could not clear the directions keyboard", exc_info=True)
    directions = await _load_tenant_directions(db, config)
    if not await _finish_directions(query.message, state, bot, lang, config, directions):
        # Nothing picked (or everything picked was deleted meanwhile).
        await _ask_direction(query.message, state, lang, config, db, directions=directions)


# --- Photos (the four required sides) ---------------------------------------
#
# The update records the photo and answers the participant **first**; the bytes
# travel to the volume in the background (``bot.services.media.PhotoIngest``).
# That is what stops "send the second side" from waiting for the first one's
# download, and it is why four photos posted as an album are fetched at the same
# time instead of one after another — the reported "the bot hangs after the
# first photo" was exactly this wait, made of one download per photo plus every
# later message queued behind it.
#
# Everything below exists to keep that record honest:
#
# * a slot belongs to the *side* it was sent for, so a photo that failed to save
#   leaves its slot reserved: the side the participant is asked to send again
#   lands where it belongs instead of on the next free side, which would swap
#   left and right without anybody noticing;
# * a photo recorded by a process that died mid-download is fetched again from
#   the id still in the state, because Telegram keeps a photo downloadable;
# * the end of the form waits for whatever is still in flight and refuses to
#   create the application while a required side is missing from the volume.

# How long the end of the form waits for photos that are still being fetched.
# Each one is bounded by the download ceiling (15 s) plus the write (10 s), and
# they were submitted when the participant was still answering questions about
# modifications and the phone number — so this is a ceiling, not a delay.
PENDING_WAIT_SECONDS = 20.0

# How long a participant is left in silence before being told that their photos
# are still being saved ("⏳ …"), when the wait turns out to be visible.
SAVING_NOTICE_AFTER_SECONDS = 1.5

# At the end of the form, a side whose file the volume lost is fetched once more
# from the id in the state.  Short on purpose: it is a repair, not the main path.
REFETCH_WAIT_SECONDS = 8.0


def _photo_path(config: Config | TenantConfig, user_id: int, name: str) -> Optional[str]:
    """Destination for one upload, or ``None`` when there is no media volume.

    The directory itself is created by the writer (:func:`media.write_bytes`,
    which runs on the slow-work pool).  ``os.makedirs`` used to run here, on the
    event loop, for every photo — a blocking syscall against the network volume
    that holds the photos, i.e. a stall for *every* participant, not just this
    one.
    """
    media_dir = getattr(config, "media_dir", "")
    if not media_dir:
        logger.error(
            "Tenant %s has no media directory — photos cannot be stored",
            getattr(config, "tenant_slug", "?"),
        )
        return None
    return os.path.join(str(media_dir), str(user_id), name)


def _records(data: dict, id_key: str, path_key: str) -> tuple[list[str], list[str]]:
    """(file_ids, paths) recorded so far, kept side by side.

    A pair that is out of step can only come from an older or damaged state;
    trimming to the shorter one keeps every index meaningful.
    """
    ids = list(data.get(id_key) or [])
    paths = list(data.get(path_key) or [])
    length = min(len(ids), len(paths))
    return ids[:length], paths[:length]


def _failure_notice(bot: Bot, chat_id: int, lang: str):
    """Ask for one photo again, by name, when its download failed.

    The download runs on its own task, so this is the only place that can tell
    the participant *which* photo did not make it — and it must say so, or their
    next photo would quietly fill the wrong slot.
    """

    async def notify(upload: media.Upload) -> None:
        await bot.send_message(
            chat_id,
            texts.T(lang).PHOTO_SAVE_FAILED.format(
                what=upload.label or texts.side_name(lang, SIDES[min(upload.index, len(SIDES) - 1)])
            ),
        )

    return notify


def _start_download(
    bot: Bot,
    chat_id: int,
    lang: str,
    *,
    user_id: int,
    file_id: str,
    path: str,
    kind: str,
    index: int,
    label: str,
) -> None:
    """Hand one photo to the ingest; the caller answers the participant now."""
    media.ingest.submit(
        bot,
        user_id=user_id,
        chat_id=chat_id,
        file_id=file_id,
        path=path,
        kind=kind,
        index=index,
        label=label,
        notify=_failure_notice(bot, chat_id, lang),
    )


async def _photo_states(bot: Bot, user_id: int, paths: list[str]) -> list[str]:
    """Where each recorded photo is: ``pending``, ``done`` or ``missing``.

    The ledger knows everything that happened in this process; for anything it
    has never seen (a restart in the middle of a download, an entry pruned after
    an hour) the volume itself is asked.
    """
    statuses = media.ingest.statuses(bot, user_id)
    unknown = [i for i, path in enumerate(paths) if path and path not in statuses]
    present = await media.files_exist([paths[i] for i in unknown]) if unknown else []
    states: list[str] = []
    for index, path in enumerate(paths):
        if not path:
            states.append("missing")
        elif path in statuses:
            states.append({"pending": "pending", "done": "done", "failed": "missing"}[statuses[path]])
        else:
            position = unknown.index(index)
            states.append("done" if present[position] else "missing")
    return states


async def _unresolved_photos(
    bot: Bot,
    user_id: int,
    chat_id: int,
    lang: str,
    ids: list[str],
    paths: list[str],
    *,
    kind: str,
    label_of,
) -> list[int]:
    """Indexes of photos the participant still owes the bot, oldest first.

    Two situations end up here, and confusing them would put the wrong photo in
    the wrong slot:

    * **the download failed and the participant was told to send that photo
      again** — the notice names the side, so their next photo belongs in *this*
      slot; the index is returned and the slot is not re-fetched behind their
      back;
    * **the file is missing and nobody was ever asked** — the state says "sent"
      while the volume disagrees, which is what a redeploy in the middle of a
      download leaves behind.  Telegram still serves the photo by id, so it is
      fetched again here and the participant never learns about it.  Only if
      *that* download fails do they get the "send it again" notice — from the
      download itself, which is also what then puts the side on this list.

    A photo that is still being fetched is not owed either: its file is about to
    appear, and asking for it again would put two photos in one slot.
    """
    statuses = media.ingest.statuses(bot, user_id)
    owed = [index for index, path in enumerate(paths) if statuses.get(path) == "failed"]
    unknown = [index for index, path in enumerate(paths) if path and path not in statuses]
    if not unknown:
        return owed
    present = await media.files_exist([paths[index] for index in unknown])
    for index, exists in zip(unknown, present):
        if exists or index >= len(ids) or not ids[index]:
            continue
        logger.info(
            "Re-fetching photo %s for user %s — the state has it, the volume does not",
            index,
            user_id,
        )
        _start_download(
            bot,
            chat_id,
            lang,
            user_id=user_id,
            file_id=ids[index],
            path=paths[index],
            kind=kind,
            index=index,
            label=label_of(index),
        )
    return owed


def _side_label_of(lang: str):
    return lambda index: texts.side_name(lang, SIDES[index])


def _mod_label_of(lang: str):
    return lambda index: texts.mod_photo_name(lang, index + 1)


def _labels(lang: str, indexes: list[int]) -> str:
    return ", ".join(texts.side_name(lang, SIDES[i]) for i in indexes)


async def _save_side_photo(
    message: Message,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    lang: str,
    *,
    index: int,
    file_id: str,
    ids: list[str],
    paths: list[str],
) -> bool:
    """Reserve side ``index`` for ``file_id`` and start fetching it.

    The slot is written to the state *before* the download, so it is reserved:
    the next photo appends after it, and a failure can ask for exactly this side
    again without shifting the other three.
    """
    path = _photo_path(config, message.from_user.id, f"{SIDES[index]}.jpg")
    if path is None:
        await message.answer(texts.T(lang).PHOTO_DOWNLOAD_FAILED)
        return False
    if index < len(ids):
        ids[index] = file_id
        paths[index] = path
    else:
        ids.append(file_id)
        paths.append(path)
    await state.update_data(photo_file_ids=ids, photo_paths=paths)
    _start_download(
        bot,
        message.chat.id,
        lang,
        user_id=message.from_user.id,
        file_id=file_id,
        path=path,
        kind="side",
        index=index,
        label=texts.side_name(lang, SIDES[index]),
    )
    return True


async def _after_side_photo(
    message: Message,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
    lang: str,
) -> None:
    """Answer a saved side: the next question, or the sides still missing."""
    # Read the state again: the slot was written a moment ago, and the prompt
    # has to be based on the photos that are really recorded.
    current = await state.get_state()
    data = await state.get_data()
    ids, paths = _records(data, "photo_file_ids", "photo_paths")
    again = await _unresolved_photos(
        bot, message.from_user.id, message.chat.id, lang, ids, paths,
        kind="side", label_of=_side_label_of(lang),
    )
    t = texts.T(lang)
    if again:
        # A different side is still missing: ask for that one by name instead of
        # moving on, or the participant would walk into a form with a hole in it.
        await message.answer(t.PHOTO_RESEND_ASK.format(what=_labels(lang, again)))
        return
    if len(ids) < len(SIDES):
        await message.answer(t.PHOTO_PROMPTS[len(ids)])
        return
    phone = data.get("phone")
    if phone:
        # The form was already finished once and was sent back for one missing
        # photo; now that it is here, finish the registration without asking the
        # participant to repeat the questions they already answered.
        await _finalize(message, state, bot, config, db, phone=phone)
        return
    if len(ids) < len(SIDES):
        return  # the next side was asked for above
    if current == Registration.phone.state:
        # The photo was sent while the form was already asking for the phone
        # number (the side's download had failed on the way): ask that again.
        await _ask_phone(message, state, lang)
        return
    await _ask_mods(message, state, lang)


@router.message(Registration.photos, F.photo)
async def collect_photo(
    message: Message,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = texts.T(lang)
    ids, paths = _records(data, "photo_file_ids", "photo_paths")

    again = await _unresolved_photos(
        bot, message.from_user.id, message.chat.id, lang, ids, paths,
        kind="side", label_of=_side_label_of(lang),
    )
    file_id = message.photo[-1].file_id

    if file_id in ids:
        # Telegram re-delivers an update whose handler died before answering,
        # and participants do tap "send" twice.  Both used to be answered with
        # silence — indistinguishable from a frozen bot; now the current
        # question is repeated and no fifth side is ever created.
        logger.debug("Duplicate photo %s — repeating the current question", file_id)
        await _after_side_photo(message, state, bot, config, db, lang)
        return

    if again:
        index = again[0]
    elif len(ids) < len(SIDES):
        index = len(ids)
    else:
        # Four sides recorded and none of them missing: a redelivery, or a
        # photo the participant sent twice.  Repeat the question rather than
        # swallowing the update.
        await _after_side_photo(message, state, bot, config, db, lang)
        return

    if not await _save_side_photo(
        message, state, bot, config, lang, index=index, file_id=file_id, ids=ids, paths=paths
    ):
        return
    await _after_side_photo(message, state, bot, config, db, lang)


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


async def _save_mod_photo(
    message: Message,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    lang: str,
    *,
    index: int,
    file_id: str,
    ids: list[str],
    paths: list[str],
) -> bool:
    """Reserve close-up slot ``index`` for ``file_id`` and start fetching it."""
    path = _photo_path(config, message.from_user.id, f"mod_{index + 1}.jpg")
    if path is None:
        await message.answer(texts.T(lang).PHOTO_DOWNLOAD_FAILED)
        return False
    if index < len(ids):
        ids[index] = file_id
        paths[index] = path
    else:
        ids.append(file_id)
        paths.append(path)
    await state.update_data(mod_file_ids=ids, mod_paths=paths)
    _start_download(
        bot,
        message.chat.id,
        lang,
        user_id=message.from_user.id,
        file_id=file_id,
        path=path,
        kind="mod",
        index=index,
        label=texts.mod_photo_name(lang, index + 1),
    )
    return True


async def _fill_failed_side(
    message: Message,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    lang: str,
) -> bool:
    """Use this photo for a required side whose download failed earlier.

    The participant was told "send the left side again", and their next photo can
    arrive while the form has already moved on (the modifications step, or the
    phone question).  The reserved slot is where it belongs; without this it
    would be filed as a modification photo and the side would stay missing.
    """
    user_id = message.from_user.id
    failures = [
        upload
        for upload in media.ingest.failed(bot, user_id, kind="side")
        if upload.index < len(SIDES)
    ]
    if not failures:
        return False
    data = await state.get_data()
    ids, paths = _records(data, "photo_file_ids", "photo_paths")
    for upload in failures:
        if upload.index > len(ids):
            # The form was restarted since that failure — the slot does not
            # exist any more, so this photo is not the answer to it.
            logger.info("Dropping a stale side retry for user %s (%s)", user_id, upload.path)
            media.ingest.forget(bot, user_id, upload.path)
            continue
        if not await _save_side_photo(
            message,
            state,
            bot,
            config,
            lang,
            index=upload.index,
            file_id=message.photo[-1].file_id,
            ids=ids,
            paths=paths,
        ):
            return True  # answered with "could not save, send it again"
        logger.info(
            "Photo from user %s filled side %s, as asked after the failed download",
            user_id,
            SIDES[upload.index],
        )
        return True
    return False


@router.message(Registration.mods, F.photo)
async def collect_mod_photo(
    message: Message,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
) -> None:
    data = await state.get_data()
    lang = data.get("lang", "ru")
    t = texts.T(lang)
    user_id = message.from_user.id
    file_id = message.photo[-1].file_id

    # A required side may still be owed (see _fill_failed_side): put it there
    # first, then continue the flow — which repeats this step's question, or
    # names the next side that still has to be sent again.
    if await _fill_failed_side(message, state, bot, config, lang):
        await _after_side_photo(message, state, bot, config, db, lang)
        return

    ids, paths = _records(data, "mod_file_ids", "mod_paths")
    again = await _unresolved_photos(
        bot, user_id, message.chat.id, lang, ids, paths,
        kind="mod", label_of=_mod_label_of(lang),
    )

    if file_id in ids:
        logger.debug("Duplicate modification photo %s — repeating the question", file_id)
        await _ask_mods(message, state, lang)
        return

    if again:
        index = again[0]
    elif len(ids) < MAX_MOD_PHOTOS:
        index = len(ids)
    else:
        await message.answer(t.MODS_LIMIT.format(max=MAX_MOD_PHOTOS))
        await _ask_phone(message, state, lang)
        return

    if not await _save_mod_photo(
        message, state, bot, config, lang, index=index, file_id=file_id, ids=ids, paths=paths
    ):
        return

    data = await state.get_data()
    ids, _ = _records(data, "mod_file_ids", "mod_paths")
    if len(ids) >= MAX_MOD_PHOTOS:
        await message.answer(t.MODS_LIMIT.format(max=MAX_MOD_PHOTOS))
        await _ask_phone(message, state, lang)
        return

    await message.answer(
        t.MODS_ADDED.format(n=len(ids), max=MAX_MOD_PHOTOS),
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
    # Remember it in the state: if a photo turns out to be missing, the form
    # finishes by itself once it arrives instead of asking for the phone again.
    await state.update_data(phone=phone)
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
    await state.update_data(phone=phone)
    await _finalize(message, state, bot, config, db, phone=phone)


async def _confirmed_photos(
    bot: Bot, user_id: int, ids: list[str], paths: list[str]
) -> tuple[list[str], list[str]]:
    """Recorded photos minus the ones the volume never accepted.

    Used for the optional close-ups: a photo that is still being fetched is kept
    (it was submitted long before the phone question), but one whose file is not
    there is dropped rather than left in the record as a path that 404s in the
    admin panel and in the Drive export.
    """
    states = await _photo_states(bot, user_id, paths)
    keep = [index for index, state in enumerate(states) if state != "missing"]
    return [ids[index] for index in keep], [paths[index] for index in keep]


async def _sides_missing_at_finish(
    bot: Bot, user_id: int, chat_id: int, lang: str, ids: list[str], paths: list[str]
) -> list[int]:
    """Required sides that are not on the volume when the form is finished.

    Stricter than the mid-form check: here a photo that is *still* in flight
    after the wait counts as missing, because the application row must not point
    at a file that does not exist.  Anything Telegram can still serve is fetched
    once more (a network blip must not send the participant back for a photo
    they already sent), and only what is left is asked for by name.
    """
    again = await _unresolved_photos(
        bot, user_id, chat_id, lang, ids, paths, kind="side", label_of=_side_label_of(lang)
    )
    if again:
        return again
    if media.ingest.is_pending(bot, user_id):
        # A download is still running: give it a moment before deciding.
        await media.ingest.wait(bot, user_id, timeout=REFETCH_WAIT_SECONDS)

    missing = await _missing_side_indexes(paths)
    if not missing:
        return []
    # The id of every side is still in the state and Telegram serves a photo by
    # id, so fetch what is gone before asking the participant to look for those
    # photos in their gallery again.
    for index in missing:
        if index >= len(ids) or not ids[index]:
            continue
        logger.warning(
            "Side %s of user %s is not on the volume — fetching it again from Telegram",
            SIDES[index],
            user_id,
        )
        _start_download(
            bot,
            chat_id,
            lang,
            user_id=user_id,
            file_id=ids[index],
            path=paths[index],
            kind="side",
            index=index,
            label=_side_label_of(lang)(index),
        )
    if media.ingest.is_pending(bot, user_id):
        await media.ingest.wait(bot, user_id, timeout=REFETCH_WAIT_SECONDS)
    return await _missing_side_indexes(paths)


async def _missing_side_indexes(paths: list[str]) -> list[int]:
    """Sides of the four that are not on the volume right now.

    The volume has the last word here: an application row must never claim four
    sides while the disk holds three (the moderator's export and the ticket
    would both quietly come out without the car on them).
    """
    present = await media.files_exist(paths)
    return [
        index for index in range(len(SIDES)) if index >= len(paths) or not present[index]
    ]


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
    t = texts.T(lang)
    user_id = message.from_user.id
    direction_id = data.get("direction_id")
    ids, paths = _records(data, "photo_file_ids", "photo_paths")
    mod_ids, mod_paths = _records(data, "mod_file_ids", "mod_paths")

    # The photos are fetched in the background, so one of them can still be on
    # its way right now — the participant answered the last questions while
    # their photos were being saved, which is exactly the point.  Give what is
    # in flight a bounded moment to land, and say so if that takes a visible
    # moment, instead of going quiet right before the confirmation.
    if media.ingest.is_pending(bot, user_id):
        waiter = asyncio.ensure_future(
            media.ingest.wait(bot, user_id, timeout=PENDING_WAIT_SECONDS)
        )
        done, _ = await asyncio.wait({waiter}, timeout=SAVING_NOTICE_AFTER_SECONDS)
        if not done:
            await message.answer(t.PHOTOS_SAVING)
            await waiter

    again = await _sides_missing_at_finish(bot, user_id, message.chat.id, lang, ids, paths)
    if again:
        # Never say "thanks, your application is accepted" and then ask for a
        # photo: keep the form open, remember the phone number, and come back
        # here by itself as soon as the missing side arrives.
        await state.update_data(phone=phone)
        await state.set_state(Registration.photos)
        await message.answer(
            t.PHOTO_RESEND_BEFORE_FINISH.format(what=_labels(lang, again))
        )
        return

    mod_ids, mod_paths = await _confirmed_photos(bot, user_id, mod_ids, mod_paths)

    app_id = await db.create_application(
        user_id=user_id,
        username=_user_label(message),
        full_name=message.from_user.full_name or "",
        country=data.get("country", ""),
        plate=data.get("plate", ""),
        direction=data.get("direction", ""),
        direction_id=direction_id,
        phone=phone,
        photo_file_ids=ids,
        photo_paths=paths,
        mod_file_ids=mod_ids,
        mod_paths=mod_paths,
        language=lang,
    )
    await state.clear()
    await message.answer(t.THANKS, reply_markup=keyboards.main_menu_keyboard(lang))

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
        photo_file_ids=ids,
        mod_file_ids=mod_ids,
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

        direction_text = texts.direction_lines(direction)
        card = texts.MODERATION_CARD.format(
            country=country,
            plate=plate,
            direction=("\n" + direction_text) if "\n" in direction_text else direction_text,
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
    message: Message,
    state: FSMContext,
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
) -> None:
    """Input that no step expected — repeat the step rather than stay silent.

    Global actions (``/start``, ``/mynumber``, the "Узнать свой номер" button)
    are passed on with :class:`SkipHandler` so they keep reaching their own
    handlers instead of being swallowed by this safety net.
    """
    if not _is_form_message(message):
        raise SkipHandler
    if message.photo:
        # A photo can arrive while the form is on another question: the bot asked
        # for a side whose download failed after the participant had already
        # moved on to the phone number.  That photo is the answer to that
        # request — repeating "send your phone number" would lose it, and the
        # end-of-form check would send them back for it a second time.
        lang = (await state.get_data()).get("lang", "ru")
        if await _fill_failed_side(message, state, bot, config, lang):
            await _after_side_photo(message, state, bot, config, db, lang)
            return
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
    fresh.callback_query.register(
        directions_back,
        StateFilter(Registration.direction, Registration.sub_direction),
        F.data == keyboards.CB_DIRECTIONS_BACK,
    )
    fresh.callback_query.register(
        directions_done,
        StateFilter(Registration.direction, Registration.sub_direction),
        F.data == keyboards.CB_DIRECTIONS_DONE,
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
