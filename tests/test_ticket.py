"""Smoke tests for the shareable participant ticket image."""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bot.services.assets as assets  # noqa: E402
import bot.services.ticket as ticket  # noqa: E402
from bot.services.ticket import generate_ticket  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def _use_sponsor_dir(directory):
    """Point the assets module at a temp dir holding sponsor logos.

    assets stores runtime uploads under "<root>/_sponsors", so hand it a root
    whose _sponsors subdirectory is the directory the test populated.
    """
    root = os.path.join(directory, "root")
    os.makedirs(root, exist_ok=True)
    link = os.path.join(root, "_sponsors")
    if not os.path.exists(link):
        os.symlink(directory, link)
    assets.configure(root)


def test_generates_png():
    png = generate_ticket(number=1, plate="AB789GG", direction="Тюнинг", name="Иван Иванов", lang="ru")
    assert isinstance(png, bytes) and len(png) > 5000
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic header


def test_handles_long_number_and_uz():
    png = generate_ticket(
        number=1234, plate="01A123BC VERY LONG", direction="Drift", name="Nazir Elmurodov", lang="uz"
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_no_sponsors_directory_is_fine():
    with tempfile.TemporaryDirectory() as tmp:
        assets.configure(os.path.join(tmp, "does-not-exist"))
        try:
            png = generate_ticket(number=1, plate="AB789GG", direction="Тюнинг", lang="ru")
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
        finally:
            assets.configure(None)


def test_sponsor_strip_renders_and_fits_many_logos():
    with tempfile.TemporaryDirectory() as tmp:
        # 6 logos of varying aspect ratios, including a very wide banner.
        sizes = [(400, 400), (500, 300), (700, 220), (300, 300), (250, 250), (600, 200)]
        for i, (w, h) in enumerate(sizes):
            Image.new("RGBA", (w, h), (200, 20, 30, 255)).save(
                os.path.join(tmp, f"{i}_sponsor.png")
            )

        assets.configure(os.path.join(tmp, "empty"))
        baseline = generate_ticket(number=1, plate="AB789GG", direction="Тюнинг", lang="ru")
        _use_sponsor_dir(tmp)
        try:
            with_sponsors = generate_ticket(
                number=1, plate="AB789GG", direction="Тюнинг", lang="ru"
            )
            assert with_sponsors[:8] == b"\x89PNG\r\n\x1a\n"
            # Sponsor strip changes the rendered image vs. the no-sponsor baseline.
            assert with_sponsors != baseline

            # The strip must fit inside the canvas regardless of aspect ratios.
            img = Image.open(io.BytesIO(with_sponsors))
            assert img.size == (ticket.W, ticket.H)
        finally:
            assets.configure(None)


def test_no_partner_is_dropped_when_the_strip_is_full():
    # Eight partners must all reach the ticket: silently truncating the list
    # would drop whatever sorts last (e.g. 4_sof_expo).
    with tempfile.TemporaryDirectory() as tmp:
        names = [
            "1_mcs_sherdor", "1_pride", "2_retro_tashkent", "2_tuning_ibragimov",
            "3_acv", "3_drift_show", "4_sof_expo", "4_spl_show",
        ]
        for name in names:
            Image.new("RGBA", (600, 600), (200, 20, 30, 255)).save(
                os.path.join(tmp, f"{name}.png")
            )
        _use_sponsor_dir(tmp)
        try:
            assert len(ticket._load_sponsor_logos()) == len(names)
            png = generate_ticket(number=7, plate="01A777AA", direction="Ретро", lang="ru")
            assert Image.open(io.BytesIO(png)).size == (ticket.W, ticket.H)
        finally:
            assets.configure(None)


def test_strip_wraps_instead_of_shrinking_logos_away():
    # Wide wordmarks can't share one row at a readable size, so the strip must
    # wrap onto a second row rather than scale everything into a sliver.
    wide = [Image.new("RGBA", (1100, 300), (255, 255, 255, 255)) for _ in range(8)]
    bar_w = ticket.W - 2 * ticket.MARGIN - 60
    one_row_h, _ = ticket._fit_row(wide, bar_w, 40, 230)
    assert one_row_h < ticket._MIN_LOGO_H, "this fixture should be too wide for one row"

    content = Image.new("RGB", (ticket.W, ticket.H), (0, 0, 0))
    draw = ImageDraw.Draw(content)
    bottom = ticket._draw_sponsor_strip(content, draw, ticket.W // 2, ticket.Y0, wide)

    half = (len(wide) + 1) // 2
    row_h, _ = ticket._fit_row(wide[:half], bar_w, 40, 230)
    assert row_h >= ticket._MIN_LOGO_H, "wrapping must restore a readable height"
    # Two rows make the band taller than a single-row band would have been.
    assert bottom - ticket.Y0 > one_row_h + 52


def test_sponsor_logo_loader_ignores_corrupt_files():
    with tempfile.TemporaryDirectory() as tmp:
        Image.new("RGBA", (200, 200), (10, 10, 10, 255)).save(
            os.path.join(tmp, "1_good.png")
        )
        with open(os.path.join(tmp, "2_bad.png"), "wb") as fh:
            fh.write(b"not a real png")

        _use_sponsor_dir(tmp)
        try:
            logos = ticket._load_sponsor_logos()
            assert len(logos) == 1
        finally:
            assets.configure(None)


def test_flat_background_is_stripped_for_light_marks():
    # A light logo on a grey plate: the plate must go, the mark must stay.
    im = Image.new("RGBA", (200, 200), (128, 128, 133, 255))
    Image.Image.paste(im, Image.new("RGBA", (80, 80), (255, 255, 255, 255)), (60, 60))
    out = ticket._strip_flat_background(im)
    assert out.getpixel((2, 2))[3] == 0, "backdrop should be transparent"
    assert out.getpixel((100, 100))[3] > 200, "the mark itself must survive"


def test_dark_mark_keeps_its_light_plate():
    # Black lettering on white would vanish on the black header band, so the
    # original (with its plate) is kept instead.
    im = Image.new("RGBA", (200, 200), (255, 255, 255, 255))
    Image.Image.paste(im, Image.new("RGBA", (120, 60), (0, 0, 0, 255)), (40, 70))
    out = ticket._strip_flat_background(im)
    assert out.getpixel((2, 2))[3] > 200, "light plate must be preserved"


def test_transparent_logo_is_left_alone():
    im = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    Image.Image.paste(im, Image.new("RGBA", (60, 60), (255, 0, 0, 255)), (70, 70))
    out = ticket._strip_flat_background(im)
    assert out.getpixel((100, 100))[:3] == (255, 0, 0)


if __name__ == "__main__":
    test_generates_png()
    test_handles_long_number_and_uz()
    test_no_sponsors_directory_is_fine()
    test_sponsor_strip_renders_and_fits_many_logos()
    test_no_partner_is_dropped_when_the_strip_is_full()
    test_strip_wraps_instead_of_shrinking_logos_away()
    test_sponsor_logo_loader_ignores_corrupt_files()
    test_flat_background_is_stripped_for_light_marks()
    test_dark_mark_keeps_its_light_plate()
    test_transparent_logo_is_left_alone()
    print("All ticket tests passed.")
