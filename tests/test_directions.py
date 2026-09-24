"""Tests for the per-direction promo banner lookup and the directions table."""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image  # noqa: E402

import bot.constants as constants  # noqa: E402
from bot import texts  # noqa: E402
from bot.services import assets  # noqa: E402


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (100, 100), (10, 10, 10, 255)).save(buf, "PNG")
    return buf.getvalue()


def test_directions_table_is_self_consistent():
    # One row per direction, each with every field the rest of the code reads.
    for d in constants.DIRECTIONS:
        assert d["canonical"] and d["slug"] and d["ru"] and d["uz"], d
    assert constants.DIRECTIONS_CANON == [d["canonical"] for d in constants.DIRECTIONS]
    assert set(constants.DIRECTION_SLUGS) == set(constants.DIRECTIONS_CANON)
    # No duplicate canonical names or slugs (both are used as keys).
    assert len(set(constants.DIRECTIONS_CANON)) == len(constants.DIRECTIONS_CANON)
    slugs = [d["slug"] for d in constants.DIRECTIONS]
    assert len(set(slugs)) == len(slugs)


def test_labels_follow_the_table():
    # texts derives its per-language button labels from the same table, so a
    # rename in constants.py propagates everywhere.
    assert texts.T("ru").DIRECTIONS == [d["ru"] for d in constants.DIRECTIONS]
    assert texts.T("uz").DIRECTIONS == [d["uz"] for d in constants.DIRECTIONS]
    for d in constants.DIRECTIONS:
        assert texts.localize_direction(d["canonical"], "ru") == d["ru"]
        assert texts.localize_direction(d["canonical"], "uz") == d["uz"]


def test_unknown_direction_falls_back_to_itself():
    # A direction stored before a rename still renders instead of blowing up.
    assert texts.localize_direction("Формула-1", "uz") == "Формула-1"
    assert constants.direction_image_path("Формула-1") is None


def test_apply_exclusive_selection_replaces_only_its_own_group():
    """Single-choice groups: a new pick drops earlier picks of the same group."""
    from bot.db import Direction
    from bot.services.directions import apply_exclusive_selection

    def _dir(i, group=""):
        return Direction(
            id=i, tenant_id=1, parent_id=None, canonical=f"d{i}",
            label_ru=f"d{i}", label_uz=f"d{i}", slug=f"d{i}", sort_order=0,
            is_active=True, created_at="", updated_at="", exclusive_group=group,
        )

    dirs = [_dir(1, "front"), _dir(2, "front"), _dir(3, "rear"), _dir(4), _dir(5, "front")]
    # A grouped pick drops only the earlier picks of its own group.
    assert apply_exclusive_selection(dirs, [1, 3, 4], 2) == [3, 4]
    assert apply_exclusive_selection(dirs, [2, 3, 4], 5) == [3, 4]
    # An ungrouped pick changes nothing, whatever is selected.
    assert apply_exclusive_selection(dirs, [1, 3], 4) == [1, 3]
    # A pick that is already selected keeps its place (no duplicate on append).
    assert apply_exclusive_selection(dirs, [1, 3], 1) == [1, 3]
    # Unknown leaf id: the selection is returned untouched.
    assert apply_exclusive_selection(dirs, [1, 3], 99) == [1, 3]
    # An empty selection stays empty.
    assert apply_exclusive_selection(dirs, [], 2) == []


def test_missing_banner_returns_none():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            for direction in constants.DIRECTIONS_CANON:
                assert constants.direction_image_path(direction) is None
        finally:
            assets.configure(None)


def test_uploaded_banner_is_found_per_direction():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            target = constants.DIRECTIONS[0]
            assets.save_direction(target["slug"], _png())
            found = constants.direction_image_path(target["canonical"])
            assert found and os.path.basename(found) == target["slug"] + ".png"
            # Every other direction is still without a banner.
            for d in constants.DIRECTIONS[1:]:
                assert constants.direction_image_path(d["canonical"]) is None
        finally:
            assets.configure(None)


if __name__ == "__main__":
    test_directions_table_is_self_consistent()
    test_labels_follow_the_table()
    test_unknown_direction_falls_back_to_itself()
    test_apply_exclusive_selection_replaces_only_its_own_group()
    test_missing_banner_returns_none()
    test_uploaded_banner_is_found_per_direction()
    print("All direction tests passed.")
