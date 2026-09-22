"""Keyboard builders for the bot (language-aware, tenant-aware)."""
from __future__ import annotations

from typing import Optional

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from . import texts
from .db import Direction

# --- Callback data prefixes ---
CB_LANG = "lang"
CB_COUNTRY = "country"
CB_DIRECTION = "direction"
CB_SUB_DIRECTION = "subdirection"
CB_MODS_DONE = "modsdone"
CB_APPROVE = "approve"
CB_REJECT = "reject"
CB_CHECK_SUB = "checksub"


def language_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_LANG_UZ, callback_data=f"{CB_LANG}:uz")
    builder.button(text=texts.BTN_LANG_RU, callback_data=f"{CB_LANG}:ru")
    builder.adjust(2)
    return builder.as_markup()


def subscription_keyboard(channel_url: str, lang: str) -> InlineKeyboardMarkup:
    t = texts.T(lang)
    builder = InlineKeyboardBuilder()
    if channel_url:
        builder.row(InlineKeyboardButton(text=t.BTN_SUBSCRIBE, url=channel_url))
    builder.row(
        InlineKeyboardButton(text=t.BTN_CHECK_SUBSCRIPTION, callback_data=CB_CHECK_SUB)
    )
    return builder.as_markup()


def country_keyboard(lang: str) -> InlineKeyboardMarkup:
    t = texts.T(lang)
    builder = InlineKeyboardBuilder()
    for idx, name in enumerate(t.COUNTRIES):
        builder.button(text=name, callback_data=f"{CB_COUNTRY}:{idx}")
    builder.button(text=t.COUNTRY_OTHER, callback_data=f"{CB_COUNTRY}:other")
    builder.adjust(2)
    return builder.as_markup()


def direction_keyboard(lang: str) -> InlineKeyboardMarkup:
    """Legacy global keyboard (fallback for promotors when DB empty)."""
    t = texts.T(lang)
    builder = InlineKeyboardBuilder()
    for idx, name in enumerate(t.DIRECTIONS):
        builder.button(text=name, callback_data=f"{CB_DIRECTION}:{idx}")
    builder.adjust(2)
    return builder.as_markup()


def direction_keyboard_from_db(
    directions: list[Direction], lang: str, *, parent_id: Optional[int] = None
) -> InlineKeyboardMarkup:
    """Build inline keyboard from DB directions, filtered by parent_id.

    - When ``parent_id`` is None, shows root directions.
    - When ``parent_id`` is set, shows its children (podnapravleniya).
    - Callback data uses DB id: ``direction:<id>`` for roots,
      ``subdirection:<parent_id>:<child_id>`` for children.
    """
    builder = InlineKeyboardBuilder()
    # Filter
    if parent_id is None:
        filtered = [d for d in directions if d.parent_id is None and d.is_active]
    else:
        filtered = [d for d in directions if d.parent_id == parent_id and d.is_active]
    # Sort by sort_order
    filtered = sorted(filtered, key=lambda d: (d.sort_order, d.id))
    for d in filtered:
        label = d.label_uz if lang == "uz" else d.label_ru
        label = label or d.canonical
        if parent_id is None:
            builder.button(text=label, callback_data=f"{CB_DIRECTION}:{d.id}")
        else:
            builder.button(text=label, callback_data=f"{CB_SUB_DIRECTION}:{parent_id}:{d.id}")
    builder.adjust(2)
    return builder.as_markup()


def mods_keyboard(lang: str, has_photos: bool) -> InlineKeyboardMarkup:
    """Finish the "what did you change?" step."""
    t = texts.T(lang)
    builder = InlineKeyboardBuilder()
    builder.button(
        text=t.BTN_MODS_DONE if has_photos else t.BTN_MODS_NONE,
        callback_data=CB_MODS_DONE,
    )
    return builder.as_markup()


def phone_keyboard(lang: str) -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=texts.T(lang).BTN_SEND_PHONE, request_contact=True))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def main_menu_keyboard(lang: str) -> ReplyKeyboardMarkup:
    """Persistent menu shown after finishing / for status lookups."""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=texts.T(lang).BTN_MY_NUMBER))
    return builder.as_markup(resize_keyboard=True)


def moderation_keyboard(app_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ Принять", callback_data=f"{CB_APPROVE}:{app_id}")
    builder.button(text="❌ Отклонить", callback_data=f"{CB_REJECT}:{app_id}")
    builder.adjust(2)
    return builder.as_markup()
