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


def _seed_db(path: str, photo_path: str) -> int:
    db = Database(path)
    asyncio.run(db.init())
    return db, asyncio.run(
        db.create_application(
            user_id=7,
            username="@tester",
            country="Узбекистан",
            plate="01A777AA",
            direction="Дрифт",
            phone="+998901112233",
            photo_file_ids=["f0"],
            photo_paths=[photo_path],
        )
    )


def test_routes():
    with tempfile.TemporaryDirectory() as tmp:
        photo = os.path.join(tmp, "left.jpg")
        with open(photo, "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0JFIFdummy")  # minimal jpeg-ish bytes
        db, app_id = _seed_db(os.path.join(tmp, "t.db"), photo)
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
                assert r.status == 200 and "Дрифт" in await r.text()

                r = await client.get(f"/photo/{app_id}/0", headers=hdr)
                assert r.status == 200 and (await r.read()).startswith(b"\xff\xd8")

                r = await client.get("/export.csv", headers=hdr)
                assert r.status == 200 and "01A777AA" in await r.text()

                # Out-of-range photo index → 404
                assert (await client.get(f"/photo/{app_id}/9", headers=hdr)).status == 404

        asyncio.run(run())


if __name__ == "__main__":
    test_cookie_signing()
    test_routes()
    print("All admin tests passed.")
