"""Tests for the per-direction promo banner lookup."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bot.constants as constants  # noqa: E402
from bot.texts import DIRECTIONS_CANON  # noqa: E402


def test_every_direction_has_a_slug():
    # Each canonical direction must map to a filename slug, else its banner
    # could never be found.
    for direction in DIRECTIONS_CANON:
        assert direction in constants.DIRECTION_SLUGS


def test_missing_banner_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        original = constants._DIRECTIONS_DIR
        constants._DIRECTIONS_DIR = tmp
        try:
            for direction in DIRECTIONS_CANON:
                assert constants.direction_image_path(direction) is None
        finally:
            constants._DIRECTIONS_DIR = original


def test_finds_banner_by_any_supported_extension():
    with tempfile.TemporaryDirectory() as tmp:
        original = constants._DIRECTIONS_DIR
        constants._DIRECTIONS_DIR = tmp
        try:
            # "Дрифт" -> drift.jpg
            path = os.path.join(tmp, "drift.jpg")
            with open(path, "wb") as fh:
                fh.write(b"stub")
            assert constants.direction_image_path("Дрифт") == path
            # Other directions still have no banner.
            assert constants.direction_image_path("Ретро") is None
        finally:
            constants._DIRECTIONS_DIR = original


def test_unknown_direction_is_none():
    assert constants.direction_image_path("Формула-1") is None
    assert constants.direction_image_path("") is None


if __name__ == "__main__":
    test_every_direction_has_a_slug()
    test_missing_banner_returns_none()
    test_finds_banner_by_any_supported_extension()
    test_unknown_direction_is_none()
    print("All direction tests passed.")
