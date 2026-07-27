"""Tests for runtime brand assets uploaded by admins through Telegram."""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from PIL import Image  # noqa: E402

from bot.constants import DIRECTIONS, DIRECTIONS_CANON, direction_image_path  # noqa: E402
from bot.services import assets  # noqa: E402


def _png(size=(200, 100), color=(200, 20, 30, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def test_sponsor_upload_roundtrip_and_order():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            assert assets.sponsor_files() == []
            # Upload out of order; they must come back in filename order.
            for name in ("3_acv", "1_pride", "2_tuning"):
                assets.save_sponsor(name, _png())
            names = [os.path.basename(p) for p in assets.sponsor_files()]
            assert names == ["1_pride.png", "2_tuning.png", "3_acv.png"]
        finally:
            assets.configure(None)


def test_direction_banner_roundtrip():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            assert direction_image_path("Дрифт") is None
            assets.save_direction("drift", _png())
            path = direction_image_path("Дрифт")
            assert path is not None and os.path.basename(path) == "drift.png"
            # An unrelated direction is unaffected.
            assert direction_image_path("Ретро") is None
        finally:
            assets.configure(None)


def test_reupload_replaces_previous_file():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            assets.save_sponsor("1_pride", _png(size=(200, 100)))
            assets.save_sponsor("1_pride", _png(size=(400, 400)))
            files = assets.sponsor_files()
            assert len(files) == 1  # not two entries for the same logo
            assert Image.open(files[0]).size == (400, 400)
        finally:
            assets.configure(None)


def test_unsafe_names_are_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            for bad in ("../evil", "a/b", "", "x" * 41, "лого"):
                assert not assets.is_safe_name(bad)
                try:
                    assets.save_sponsor(bad, _png())
                    raise AssertionError(f"path traversal allowed for {bad!r}")
                except ValueError:
                    pass
        finally:
            assets.configure(None)


def test_delete_asset():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            assets.save_sponsor("1_pride", _png())
            assert assets.delete_asset("sponsors", "1_pride") is True
            assert assets.sponsor_files() == []
            # Deleting again, or an unknown kind, is a safe no-op.
            assert assets.delete_asset("sponsors", "1_pride") is False
            assert assets.delete_asset("bogus", "1_pride") is False
        finally:
            assets.configure(None)


def test_without_storage_configured_nothing_breaks():
    assets.configure(None)
    # Falls back to bundled assets (currently none committed) without raising.
    assert isinstance(assets.sponsor_files(), list)
    assert direction_image_path("Дрифт") is None or isinstance(
        direction_image_path("Дрифт"), str
    )
    inv = assets.inventory()
    assert inv["storage_configured"] is False


def test_every_direction_slug_is_uploadable():
    # The /banner command validates against these slugs, so they must all be
    # safe filenames.
    for d in DIRECTIONS:
        assert assets.is_safe_name(d["slug"]), d
    assert len(DIRECTIONS) == len(DIRECTIONS_CANON)


def test_brand_logo_upload_overrides_bundled():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            # Nothing uploaded yet -> falls back to whatever is bundled.
            before = assets.brand_logo("adrenaline")
            assets.save_brand("adrenaline", _png())
            after = assets.brand_logo("adrenaline")
            assert after is not None
            assert after != before, "an upload must take priority over the bundled file"
            assert after.startswith(tmp)
            # Reported as uploaded in the inventory.
            assert assets.inventory()["brand"]["adrenaline"] == "загружен"
        finally:
            assets.configure(None)


def test_unknown_brand_logo_is_rejected():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            assert assets.brand_logo("nope") is None
            try:
                assets.save_brand("nope", _png())
                raise AssertionError("unknown brand name was accepted")
            except ValueError:
                pass
        finally:
            assets.configure(None)


def test_brand_logo_can_be_deleted_back_to_bundled():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(tmp)
        try:
            assets.save_brand("logo", _png())
            assert assets.brand_logo("logo").startswith(tmp)
            assert assets.delete_asset("brand", "logo") is True
            path = assets.brand_logo("logo")
            assert path is None or not path.startswith(tmp)
        finally:
            assets.configure(None)


if __name__ == "__main__":
    test_sponsor_upload_roundtrip_and_order()
    test_direction_banner_roundtrip()
    test_reupload_replaces_previous_file()
    test_unsafe_names_are_rejected()
    test_delete_asset()
    test_without_storage_configured_nothing_breaks()
    test_every_direction_slug_is_uploadable()
    test_brand_logo_upload_overrides_bundled()
    test_unknown_brand_logo_is_rejected()
    test_brand_logo_can_be_deleted_back_to_bundled()
    print("All asset tests passed.")
