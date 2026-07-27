"""Shared constants used across handlers, services and the admin panel."""

import os

# Photo sides, in the order we ask for them (used for filenames + drive names).
SIDES = ["left", "right", "front", "back"]

# Promo banner shown when a participant picks a direction, so they see what the
# category looks like. Files live in bot/assets/directions/ (any of these
# extensions); a missing file just means no banner is sent.
DIRECTION_SLUGS = {
    "Тюнинг": "tuning",
    "Автозвук": "autosound",
    "Дрифт": "drift",
    "Ретро": "retro",
}

_DIRECTIONS_DIR = os.path.join(os.path.dirname(__file__), "assets", "directions")


def direction_image_path(canonical: str):
    """Return the banner path for a canonical direction, or None if absent."""
    slug = DIRECTION_SLUGS.get(canonical)
    if not slug:
        return None
    for ext in (".png", ".jpg", ".jpeg", ".webp"):
        path = os.path.join(_DIRECTIONS_DIR, slug + ext)
        if os.path.exists(path):
            return path
    return None

# Transliterated labels for Drive filenames (ASCII-safe).
SIDE_LABELS_TRANSLIT = {
    "left": "levaya",
    "right": "pravaya",
    "front": "perednyaya",
    "back": "zadnyaya",
}

# Human-readable Russian labels for the admin panel.
SIDE_LABELS_RU = {
    "left": "Левая",
    "right": "Правая",
    "front": "Передняя",
    "back": "Задняя",
}
