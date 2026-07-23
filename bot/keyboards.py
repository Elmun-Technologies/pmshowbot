"""Keyboard builders for the bot."""
from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from . import texts

# --- Callback data prefixes ---
CB_COUNTRY = "country"
CB_DIRECTION = "direction"
CB_APPROVE = "approve"
CB_REJECT = "reject"
CB_CHECK_SUB = "checksub"


def subscription_keyboard(channel_url: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    # Only add the link button when we have a usable URL (numeric-id channels
    # without a CHANNEL_URL override have none).
    if channel_url:
        builder.row(InlineKeyboardButton(text=texts.BTN_SUBSCRIBE, url=channel_url))
    builder.row(
        InlineKeyboardButton(text=texts.BTN_CHECK_SUBSCRIPTION, callback_data=CB_CHECK_SUB)
    )
    return builder.as_markup()


def country_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, name in enumerate(texts.COUNTRIES):
        builder.button(text=name, callback_data=f"{CB_COUNTRY}:{idx}")
    # "Другая" uses a sentinel index.
    builder.button(text=texts.COUNTRY_OTHER, callback_data=f"{CB_COUNTRY}:other")
    builder.adjust(2)
    return builder.as_markup()


def direction_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for idx, name in enumerate(texts.DIRECTIONS):
        builder.button(text=name, callback_data=f"{CB_DIRECTION}:{idx}")
    builder.adjust(2)
    return builder.as_markup()


def phone_keyboard() -> ReplyKeyboardMarkup:
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=texts.BTN_SEND_PHONE, request_contact=True))
    return builder.as_markup(resize_keyboard=True, one_time_keyboard=True)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Persistent menu shown after finishing / for status lookups."""
    builder = ReplyKeyboardBuilder()
    builder.add(KeyboardButton(text=texts.BTN_MY_NUMBER))
    return builder.as_markup(resize_keyboard=True)


def moderation_keyboard(app_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=texts.BTN_APPROVE, callback_data=f"{CB_APPROVE}:{app_id}")
    builder.button(text=texts.BTN_REJECT, callback_data=f"{CB_REJECT}:{app_id}")
    builder.adjust(2)
    return builder.as_markup()
