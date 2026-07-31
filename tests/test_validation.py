"""Tests for the free-text form steps: plate, phone and typed-in country."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from bot.validation import clean_country, clean_phone, clean_plate  # noqa: E402


def test_plate_accepts_real_plates_and_normalises_them():
    assert clean_plate("01A123BC") == "01A123BC"
    assert clean_plate(" 30x577xx ") == "30X577XX"
    assert clean_plate("01 A 123 BC") == "01 A 123 BC"       # inner spacing kept
    assert clean_plate("30  X  577") == "30 X 577"           # runs collapsed
    assert clean_plate("А123ВС77") == "А123ВС77"             # cyrillic (RU)
    assert clean_plate("KZ-123-ABC") == "KZ-123-ABC"


def test_plate_rejects_junk():
    for bad in ("", "   ", "??", "ab", "/mynumber", "salom!", "<b>x</b>", "x" * 21):
        assert clean_plate(bad) is None, bad


def test_phone_accepts_the_formats_people_actually_type():
    assert clean_phone("+998 90 123 45 67") == "+998901234567"
    assert clean_phone("998901234567") == "998901234567"
    assert clean_phone("+998(90)123-45-67") == "+998901234567"
    assert clean_phone("901234567") == "901234567"           # local, no country code


def test_phone_rejects_junk():
    # The whole point: a stray command or a greeting must not be filed as a
    # participant's phone number.
    for bad in ("", "salom", "/mynumber", "Узнать свой номер", "+", "12345", "9" * 16):
        assert clean_phone(bad) is None, bad


def test_country_accepts_names_and_rejects_junk():
    assert clean_country("  Туркмения ") == "Туркмения"
    assert clean_country("O‘zbekiston") == "O‘zbekiston"
    for bad in ("", "X", "/start", "12345", "<script>", "x" * 41):
        assert clean_country(bad) is None, bad


if __name__ == "__main__":
    test_plate_accepts_real_plates_and_normalises_them()
    test_plate_rejects_junk()
    test_phone_accepts_the_formats_people_actually_type()
    test_phone_rejects_junk()
    test_country_accepts_names_and_rejects_junk()
    print("All validation tests passed.")
