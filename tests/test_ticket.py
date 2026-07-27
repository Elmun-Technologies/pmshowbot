"""Smoke tests for the shareable participant ticket image."""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import bot.services.ticket as ticket  # noqa: E402
from bot.services.ticket import generate_ticket  # noqa: E402
from PIL import Image  # noqa: E402


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
        original = ticket._SPONSORS_DIR
        ticket._SPONSORS_DIR = os.path.join(tmp, "does-not-exist")
        try:
            png = generate_ticket(number=1, plate="AB789GG", direction="Тюнинг", lang="ru")
            assert png[:8] == b"\x89PNG\r\n\x1a\n"
        finally:
            ticket._SPONSORS_DIR = original


def test_sponsor_strip_renders_and_fits_many_logos():
    with tempfile.TemporaryDirectory() as tmp:
        # 6 logos of varying aspect ratios, including a very wide banner.
        sizes = [(400, 400), (500, 300), (700, 220), (300, 300), (250, 250), (600, 200)]
        for i, (w, h) in enumerate(sizes):
            Image.new("RGBA", (w, h), (200, 20, 30, 255)).save(
                os.path.join(tmp, f"{i}_sponsor.png")
            )

        original = ticket._SPONSORS_DIR
        baseline = generate_ticket(number=1, plate="AB789GG", direction="Тюнинг", lang="ru")
        ticket._SPONSORS_DIR = tmp
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
            ticket._SPONSORS_DIR = original


def test_sponsor_logo_loader_ignores_corrupt_files():
    with tempfile.TemporaryDirectory() as tmp:
        Image.new("RGBA", (200, 200), (10, 10, 10, 255)).save(
            os.path.join(tmp, "1_good.png")
        )
        with open(os.path.join(tmp, "2_bad.png"), "wb") as fh:
            fh.write(b"not a real png")

        original = ticket._SPONSORS_DIR
        ticket._SPONSORS_DIR = tmp
        try:
            logos = ticket._load_sponsor_logos()
            assert len(logos) == 1
        finally:
            ticket._SPONSORS_DIR = original


if __name__ == "__main__":
    test_generates_png()
    test_handles_long_number_and_uz()
    test_no_sponsors_directory_is_fine()
    test_sponsor_strip_renders_and_fits_many_logos()
    test_sponsor_logo_loader_ignores_corrupt_files()
    print("All ticket tests passed.")
