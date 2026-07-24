"""Smoke tests for the shareable participant ticket image."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.services.ticket import generate_ticket  # noqa: E402


def test_generates_png():
    png = generate_ticket(number=1, plate="AB789GG", direction="Тюнинг", name="Иван Иванов", lang="ru")
    assert isinstance(png, bytes) and len(png) > 5000
    assert png[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic header


def test_handles_long_number_and_uz():
    png = generate_ticket(
        number=1234, plate="01A123BC VERY LONG", direction="Drift", name="Nazir Elmurodov", lang="uz"
    )
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


if __name__ == "__main__":
    test_generates_png()
    test_handles_long_number_and_uz()
    print("All ticket tests passed.")
