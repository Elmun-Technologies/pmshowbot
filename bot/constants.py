"""Shared constants used across handlers, services and the admin panel."""

# Photo sides, in the order we ask for them (used for filenames + drive names).
SIDES = ["left", "right", "front", "back"]

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
