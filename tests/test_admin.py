"""Tests for the admin panel: auth cookie logic and HTTP routes."""
import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from bot.admin import auth  # noqa: E402
from bot.admin.server import create_admin_app  # noqa: E402
from bot.db import Database  # noqa: E402

PW = "s3cret-pass"


def test_cookie_signing():
    good = auth.make_cookie(PW)
    assert auth.valid_cookie(PW, good)
    assert not auth.valid_cookie("other", good)      # wrong secret
    assert not auth.valid_cookie(PW, None)            # no cookie
    assert not auth.valid_cookie(PW, "garbage")       # malformed
    assert not auth.valid_cookie(PW, "0.deadbeef")    # expired
    assert auth.password_matches(PW, PW)
    assert not auth.password_matches(PW, "nope")


def _seed_db(path: str, photo_path: str, mod_path: str) -> int:
    db = Database(path)
    asyncio.run(db.init())
    return db, asyncio.run(
        db.create_application(
            user_id=7,
            username="@tester",
            country="Узбекистан",
            plate="01A777AA",
            direction="Adrenaline Drift",
            phone="+998901112233",
            photo_file_ids=["f0"],
            photo_paths=[photo_path],
            mod_file_ids=["m0"],
            mod_paths=[mod_path],
        )
    )


def test_routes():
    with tempfile.TemporaryDirectory() as tmp:
        photo = os.path.join(tmp, "left.jpg")
        mod = os.path.join(tmp, "mod_1.jpg")
        for path in (photo, mod):
            with open(path, "wb") as fh:
                fh.write(b"\xff\xd8\xff\xe0JFIFdummy")  # minimal jpeg-ish bytes
        db, app_id = _seed_db(os.path.join(tmp, "t.db"), photo, mod)
        config = SimpleNamespace(admin_password=PW, panel_port=8080)
        admin_app = create_admin_app(bot=None, config=config, db=db)

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                # Unauthenticated → redirect to /login
                r = await client.get("/", allow_redirects=False)
                assert r.status == 302 and r.headers["Location"] == "/login"

                # Login page renders; /health is public
                assert (await client.get("/login")).status == 200
                assert (await client.get("/health")).status == 200

                # Wrong password → back to login with error
                r = await client.post("/login", data={"password": "wrong"}, allow_redirects=False)
                assert r.status == 302 and "error=1" in r.headers["Location"]

                # Correct password → sets the session cookie
                r = await client.post("/login", data={"password": PW}, allow_redirects=False)
                assert r.status == 302
                assert auth.COOKIE_NAME in r.headers.get("Set-Cookie", "")

                # Authenticated requests via an explicit valid cookie
                # (the Secure cookie isn't sent by the test client over http)
                hdr = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(PW)}"}
                r = await client.get("/", headers=hdr)
                assert r.status == 200 and "Всего заявок" in await r.text()

                r = await client.get("/applications", headers=hdr)
                body = await r.text()
                assert r.status == 200 and "01A777AA" in body

                r = await client.get(f"/application/{app_id}", headers=hdr)
                body = await r.text()
                assert r.status == 200 and "Adrenaline Drift" in body
                # The modification close-ups get their own section.
                assert "Изменения в автомобиле" in body
                assert f"/modphoto/{app_id}/0" in body

                r = await client.get(f"/photo/{app_id}/0", headers=hdr)
                assert r.status == 200 and (await r.read()).startswith(b"\xff\xd8")

                r = await client.get("/export.csv", headers=hdr)
                assert r.status == 200 and "01A777AA" in await r.text()

                r = await client.get(f"/modphoto/{app_id}/0", headers=hdr)
                assert r.status == 200 and (await r.read()).startswith(b"\xff\xd8")

                r = await client.get("/broadcast", headers=hdr)
                body = await r.text()
                assert r.status == 200 and "Рассылка" in body
                assert 'name="audience"' in body
                assert "Одобрено" in body
                # Two language fields instead of a single "text" textarea.
                assert 'name="text_uz"' in body
                assert 'name="text_ru"' in body

                # Out-of-range photo index → 404
                assert (await client.get(f"/photo/{app_id}/9", headers=hdr)).status == 404
                assert (await client.get(f"/modphoto/{app_id}/9", headers=hdr)).status == 404

        asyncio.run(run())


class _FakeBot:
    """Records outgoing broadcast messages instead of calling Telegram."""

    def __init__(self):
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str):
        self.sent.append((chat_id, text))


def _broadcast_db(tmp: str):
    db = Database(os.path.join(tmp, "b.db"))
    asyncio.run(db.init())
    for user_id, lang in ((1, "uz"), (2, "ru"), (3, "")):
        app_id = asyncio.run(
            db.create_application(
                user_id=user_id,
                username=f"@u{user_id}",
                country="Узбекистан",
                plate=f"01A00{user_id}AA",
                direction="Adrenaline Drift",
                phone="+998901112233",
                photo_file_ids=["f0"],
                photo_paths=["/nope.jpg"],
                language=lang,
            )
        )
        asyncio.run(db.approve(app_id, "@mod"))
    return db


def _post_broadcast(client, hdr, **fields):
    data = {"audience": "approved", "confirm": "1"}
    data.update(fields)
    return client.post("/broadcast", data=data, headers=hdr)


def test_broadcast_two_languages():
    with tempfile.TemporaryDirectory() as tmp:
        db = _broadcast_db(tmp)
        bot = _FakeBot()
        config = SimpleNamespace(admin_password=PW, panel_port=8080)
        admin_app = create_admin_app(bot=bot, config=config, db=db)
        hdr = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(PW)}"}

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                # Both languages filled → routed by the recipient's language.
                r = await _post_broadcast(
                    client, hdr, text_uz="Salom", text_ru="Привет"
                )
                body = await r.text()
                assert r.status == 200
                assert dict(bot.sent) == {1: "Salom", 2: "Привет", 3: "Привет"}
                assert "на узбекском: <b>1</b>" in body
                assert "на русском: <b>2</b>" in body

                # Only one language filled → everyone gets that text.
                bot.sent.clear()
                await _post_broadcast(client, hdr, text_uz="Faqat uz", text_ru="")
                assert {t for _, t in bot.sent} == {"Faqat uz"}
                assert len(bot.sent) == 3

                bot.sent.clear()
                await _post_broadcast(client, hdr, text_uz="", text_ru="Только ру")
                assert {t for _, t in bot.sent} == {"Только ру"}

                # Both empty → error, nothing sent.
                bot.sent.clear()
                r = await _post_broadcast(client, hdr, text_uz="  ", text_ru="")
                assert r.status == 200
                assert "хотя бы на одном языке" in await r.text()
                assert bot.sent == []

                # No confirmation checkbox → nothing sent.
                r = await client.post(
                    "/broadcast",
                    data={"audience": "approved", "text_ru": "Привет"},
                    headers=hdr,
                )
                assert "Подтвердите" in await r.text()
                assert bot.sent == []

        asyncio.run(run())


if __name__ == "__main__":
    test_cookie_signing()
    test_routes()
    test_broadcast_two_languages()
    print("All admin tests passed.")
