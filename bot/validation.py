"""Input checks for the free-text steps of the registration form.

Participants type these by hand, and whatever they type ends up in the
moderation card, the Excel export and the Google Sheet. The rules below are
deliberately permissive — the event draws cars from several countries, so the
goal is to catch obvious junk ("salom", a stray "/mynumber"), not to enforce
one country's format.
"""
from __future__ import annotations

import re
from typing import Optional

# Digits, plus latin/cyrillic letters, spaces and dashes — enough for plates
# from every country in the COUNTRIES list.
_PLATE_ALLOWED = re.compile(r"^[A-Za-z0-9А-Яа-яЎўҚқҒғҲҳЁё \-]+$")
_ALNUM = re.compile(r"[A-Za-z0-9А-Яа-яЎўҚқҒғҲҳЁё]")

_PHONE_ALLOWED = re.compile(r"^\+?[\d\s\-()]+$")

# Punctuation that legitimately shows up inside a country name, e.g. the
# apostrophe in "O‘zbekiston" or the hyphen in "Guinea-Bissau".
_COUNTRY_PUNCT = " '‘’-."


def clean_plate(raw: str) -> Optional[str]:
    """Normalised licence plate, or None if it can't be one."""
    value = " ".join((raw or "").split())
    if not 3 <= len(value) <= 20:
        return None
    if not _PLATE_ALLOWED.match(value):
        return None
    if len(_ALNUM.findall(value)) < 3:
        return None
    return value.upper()


def clean_phone(raw: str) -> Optional[str]:
    """Normalised phone number, or None if it can't be one.

    Returns digits only, keeping a leading "+" when the participant typed one,
    so the export doesn't mix "+998 90 123-45-67" with "998901234567".
    """
    value = (raw or "").strip()
    if not value or not _PHONE_ALLOWED.match(value):
        return None
    digits = re.sub(r"\D", "", value)
    # 9 digits covers a local Uzbek number without the country code; 15 is the
    # international maximum (E.164).
    if not 9 <= len(digits) <= 15:
        return None
    return f"+{digits}" if value.startswith("+") else digits


def clean_country(raw: str) -> Optional[str]:
    """Country name typed under "Other", or None if it can't be one."""
    value = " ".join((raw or "").split())
    if not 2 <= len(value) <= 40:
        return None
    # str.isalpha() is unicode-aware, so this accepts Cyrillic and Latin alike
    # while rejecting digits, commands ("/start") and markup.
    if not value[0].isalpha():
        return None
    if not all(ch.isalpha() or ch in _COUNTRY_PUNCT for ch in value):
        return None
    return value
