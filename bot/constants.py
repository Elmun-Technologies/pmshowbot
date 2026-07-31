"""Shared constants used across handlers, services and the admin panel."""

from .services import assets

# Photo sides, in the order we ask for them (used for filenames + drive names).
SIDES = ["left", "right", "front", "back"]

# After the four sides we ask for close-ups of what the participant changed on
# the car (hood, trunk, audio, …). The count is open — this is just the cap.
MAX_MOD_PHOTOS = 6

# ---------------------------------------------------------------------------
# Participation directions — SINGLE SOURCE OF TRUTH.
#
# To rename a direction, change its row here and nowhere else: the button
# labels (ru/uz), the value stored in the database, the Google Sheet / Excel
# export and the promo-banner filename all derive from this table.
#
#   canonical — what gets stored in the DB and shown to admins (Russian)
#   ru / uz   — the button label a participant sees in that language
#   slug      — banner filename, e.g. slug "drift" -> drift.png
#
# The order here is the order of the buttons. Changing the order is safe;
# applications already in the database keep their stored canonical name.
# ---------------------------------------------------------------------------
DIRECTIONS = [
    {"canonical": "SPL Тюнинг",   "ru": "SPL Тюнинг",   "uz": "SPL Tuning",   "slug": "tuning"},
    {"canonical": "SPL Автозвук", "ru": "SPL Автозвук", "uz": "SPL Avtozvuk", "slug": "autosound"},
    {"canonical": "Adrenaline Drift", "ru": "Adrenaline Drift", "uz": "Adrenaline Drift", "slug": "drift"},
    {"canonical": "Ретро",        "ru": "Ретро",        "uz": "Retro",        "slug": "retro"},
]

DIRECTIONS_CANON = [d["canonical"] for d in DIRECTIONS]
DIRECTION_SLUGS = {d["canonical"]: d["slug"] for d in DIRECTIONS}
DIRECTION_LABELS = {
    "ru": [d["ru"] for d in DIRECTIONS],
    "uz": [d["uz"] for d in DIRECTIONS],
}


def direction_label(canonical: str, lang: str) -> str:
    """Localized label for a canonical direction (falls back to the canonical)."""
    for d in DIRECTIONS:
        if d["canonical"] == canonical:
            return d.get(lang) or d["canonical"]
    return canonical


def direction_image_path(canonical: str):
    """Return the banner path for a canonical direction, or None if absent.

    Runtime uploads (admins sending the banner to the bot) take priority over
    anything bundled in the repo.
    """
    return assets.direction_banner(DIRECTION_SLUGS.get(canonical, ""))


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
