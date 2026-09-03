"""Tests for the admin panel: auth cookie logic and HTTP routes."""
import asyncio
import io
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
                # Language/direction filters, photo upload and the live preview.
                assert 'name="langs"' in body
                assert 'name="directions"' in body
                assert 'id="bcast-photo-uz"' in body
                assert 'id="bcast-photo-ru"' in body
                assert "Предпросмотр поста" in body

                # Out-of-range photo index → 404
                assert (await client.get(f"/photo/{app_id}/9", headers=hdr)).status == 404
                assert (await client.get(f"/modphoto/{app_id}/9", headers=hdr)).status == 404

        asyncio.run(run())


class _FakePhoto:
    def __init__(self, file_id: str):
        self.file_id = file_id


class _FakeSentPhoto:
    def __init__(self, file_id: str):
        self.photo = [_FakePhoto(file_id)]


class _FakeBot:
    """Records outgoing broadcast messages instead of calling Telegram."""

    def __init__(self):
        self.sent: list[tuple[int, str]] = []
        self.sent_photos: list[tuple[int, str, str]] = []
        self._next_file_id = 0

    async def send_message(self, chat_id: int, text: str):
        self.sent.append((chat_id, text))

    async def send_photo(self, chat_id: int, photo, caption: str = ""):
        # A real Bot re-uploads the first BufferedInputFile and returns a
        # fresh file_id; every later call in the broadcast passes that
        # file_id straight through, exactly like Telegram would accept it.
        if isinstance(photo, str):
            file_id = photo
        else:
            self._next_file_id += 1
            file_id = f"uploaded-{self._next_file_id}"
        self.sent_photos.append((chat_id, file_id, caption))
        return _FakeSentPhoto(file_id)


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


def _post_broadcast_multi(client, hdr, fields: list[tuple[str, str]]):
    import aiohttp

    form = aiohttp.FormData()
    for key, value in fields:
        form.add_field(key, value)
    return client.post("/broadcast", data=form, headers=hdr)


def test_broadcast_language_and_direction_filters():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "b2.db"))
        asyncio.run(db.init())
        seed = [(1, "uz", "Adrenaline Drift"), (2, "ru", "SPL Тюнинг"), (3, "ru", "Adrenaline Drift")]
        for user_id, lang, direction in seed:
            app_id = asyncio.run(
                db.create_application(
                    user_id=user_id,
                    username=f"@u{user_id}",
                    country="Узбекистан",
                    plate=f"01A00{user_id}AA",
                    direction=direction,
                    phone="+998901112233",
                    photo_file_ids=[],
                    photo_paths=[],
                    language=lang,
                )
            )
            asyncio.run(db.approve(app_id, "@mod"))

        bot = _FakeBot()
        config = SimpleNamespace(admin_password=PW, panel_port=8080)
        admin_app = create_admin_app(bot=bot, config=config, db=db)
        hdr = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(PW)}"}

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                # Only Uzbek-speaking recipients.
                r = await _post_broadcast_multi(
                    client, hdr,
                    [
                        ("audience", "approved"), ("confirm", "1"), ("action", "send"),
                        ("text_ru", "Привет"), ("langs", "uz"),
                    ],
                )
                assert dict(bot.sent) == {1: "Привет"}
                bot.sent.clear()

                # Only the Adrenaline Drift direction.
                r = await _post_broadcast_multi(
                    client, hdr,
                    [
                        ("audience", "approved"), ("confirm", "1"), ("action", "send"),
                        ("text_ru", "Привет"), ("directions", "Adrenaline Drift"),
                    ],
                )
                assert set(dict(bot.sent)) == {1, 3}
                bot.sent.clear()

                # Combined language + direction filter.
                r = await _post_broadcast_multi(
                    client, hdr,
                    [
                        ("audience", "approved"), ("confirm", "1"), ("action", "send"),
                        ("text_ru", "Привет"),
                        ("langs", "ru"), ("directions", "Adrenaline Drift"),
                    ],
                )
                assert set(dict(bot.sent)) == {3}
                bot.sent.clear()

                # Preview action: reports a count, sends nothing.
                r = await _post_broadcast_multi(
                    client, hdr,
                    [
                        ("audience", "approved"), ("confirm", "1"), ("action", "preview"),
                        ("text_ru", "Привет"), ("langs", "uz"),
                    ],
                )
                body = await r.text()
                assert bot.sent == []
                assert "получателей: <b>1</b>" in body

        asyncio.run(run())


def test_broadcast_with_photo():
    with tempfile.TemporaryDirectory() as tmp:
        db = _broadcast_db(tmp)
        bot = _FakeBot()
        config = SimpleNamespace(admin_password=PW, panel_port=8080)
        admin_app = create_admin_app(bot=bot, config=config, db=db)
        hdr = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(PW)}"}

        async def run():
            import aiohttp

            async with TestClient(TestServer(admin_app)) as client:
                form = aiohttp.FormData()
                form.add_field("audience", "approved")
                form.add_field("confirm", "1")
                form.add_field("action", "send")
                form.add_field("text_ru", "Привет с фото")
                form.add_field(
                    "photo_ru", b"\xff\xd8\xff\xe0fakejpeg",
                    filename="badge.jpg", content_type="image/jpeg",
                )
                r = await client.post("/broadcast", data=form, headers=hdr)
                assert r.status == 200
                body = await r.text()
                assert "с фото" in body

                # Only photo_ru was uploaded, so the Uzbek recipient falls back
                # to it too — same rule as the text fields. One raw upload,
                # every other recipient (Uzbek included) reuses its file_id.
                assert len(bot.sent_photos) == 3
                assert bot.sent == []
                file_ids = {fid for _, fid, _ in bot.sent_photos}
                assert file_ids == {"uploaded-1"}

        asyncio.run(run())


def test_broadcast_with_distinct_photos_per_language():
    with tempfile.TemporaryDirectory() as tmp:
        db = _broadcast_db(tmp)
        bot = _FakeBot()
        config = SimpleNamespace(admin_password=PW, panel_port=8080)
        admin_app = create_admin_app(bot=bot, config=config, db=db)
        hdr = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(PW)}"}

        async def run():
            import aiohttp

            async with TestClient(TestServer(admin_app)) as client:
                form = aiohttp.FormData()
                form.add_field("audience", "approved")
                form.add_field("confirm", "1")
                form.add_field("action", "send")
                form.add_field("text_uz", "Salom")
                form.add_field("text_ru", "Привет")
                form.add_field(
                    "photo_uz", b"\xff\xd8\xff\xe0uzbanner",
                    filename="uz.jpg", content_type="image/jpeg",
                )
                form.add_field(
                    "photo_ru", b"\xff\xd8\xff\xe0rubanner",
                    filename="ru.jpg", content_type="image/jpeg",
                )
                r = await client.post("/broadcast", data=form, headers=hdr)
                assert r.status == 200

                # user 1 is uz, users 2 and 3 are ru (see _broadcast_db) — each
                # language gets its own upload, reused across its own recipients.
                assert len(bot.sent_photos) == 3
                by_chat = {chat_id: (fid, caption) for chat_id, fid, caption in bot.sent_photos}
                assert by_chat[1][1] == "Salom"
                assert by_chat[2][1] == by_chat[3][1] == "Привет"
                uz_file_ids = {fid for chat_id, (fid, _) in by_chat.items() if chat_id == 1}
                ru_file_ids = {fid for chat_id, (fid, _) in by_chat.items() if chat_id in (2, 3)}
                assert uz_file_ids != ru_file_ids
                assert len(ru_file_ids) == 1

        asyncio.run(run())


def test_broadcast_accepts_photo_over_one_megabyte():
    # Regression: aiohttp's default client_max_size is exactly 1 MiB, so a
    # real phone photo (routinely 1-5 MB) used to blow up with "Maximum
    # request body size exceeded" before create_admin_app raised the cap.
    with tempfile.TemporaryDirectory() as tmp:
        db = _broadcast_db(tmp)
        bot = _FakeBot()
        config = SimpleNamespace(admin_password=PW, panel_port=8080)
        admin_app = create_admin_app(bot=bot, config=config, db=db)
        hdr = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(PW)}"}

        async def run():
            import aiohttp

            async with TestClient(TestServer(admin_app)) as client:
                big_photo = b"\xff\xd8\xff\xe0" + os.urandom(2 * 1024 * 1024)
                form = aiohttp.FormData()
                form.add_field("audience", "approved")
                form.add_field("confirm", "1")
                form.add_field("action", "send")
                form.add_field("text_ru", "Большое фото")
                form.add_field(
                    "photo_ru", big_photo, filename="badge.jpg", content_type="image/jpeg"
                )
                r = await client.post("/broadcast", data=form, headers=hdr)
                assert r.status == 200
                assert len(bot.sent_photos) == 3

        asyncio.run(run())


def test_badge_photo_route():
    with tempfile.TemporaryDirectory() as tmp:
        badge = os.path.join(tmp, "badge.jpg")
        with open(badge, "wb") as fh:
            fh.write(b"\xff\xd8\xff\xe0badge")
        db, app_id = _seed_db(
            os.path.join(tmp, "t.db"),
            os.path.join(tmp, "left.jpg"),
            os.path.join(tmp, "mod.jpg"),
        )
        for path in (os.path.join(tmp, "left.jpg"), os.path.join(tmp, "mod.jpg")):
            with open(path, "wb") as fh:
                fh.write(b"\xff\xd8\xff\xe0dummy")
        asyncio.run(db.set_badge_photo(7, "file0", badge))

        config = SimpleNamespace(admin_password=PW, panel_port=8080)
        admin_app = create_admin_app(bot=None, config=config, db=db)
        hdr = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(PW)}"}

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                r = await client.get(f"/badgephoto/{app_id}", headers=hdr)
                assert r.status == 200 and (await r.read()).startswith(b"\xff\xd8")

                detail = await client.get(f"/application/{app_id}", headers=hdr)
                body = await detail.text()
                assert f"/badgephoto/{app_id}" in body

                # No badge photo on this one → 404, no crash.
                other_id = await db.create_application(
                    user_id=8, username="@u8", country="Узбекистан", plate="01A008AA",
                    direction="Adrenaline Drift", phone="+998", photo_file_ids=[], photo_paths=[],
                )
                r = await client.get(f"/badgephoto/{other_id}", headers=hdr)
                assert r.status == 404

        asyncio.run(run())


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


def test_individual_message():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(os.path.join(tmp, "m.db"))
        asyncio.run(db.init())
        app_id = asyncio.run(
            db.create_application(
                user_id=7,
                username="@tester",
                country="Узбекистан",
                plate="01A777AA",
                direction="Adrenaline Drift",
                phone="+998901112233",
                photo_file_ids=[],
                photo_paths=[],
            )
        )
        asyncio.run(db.approve(app_id, "@mod"))
        bot = _FakeBot()
        config = SimpleNamespace(admin_password=PW, panel_port=8080)
        admin_app = create_admin_app(bot=bot, config=config, db=db)
        hdr = {"Cookie": f"{auth.COOKIE_NAME}={auth.make_cookie(PW)}"}

        async def run():
            async with TestClient(TestServer(admin_app)) as client:
                # The page lists known users with their plate / reg number.
                r = await client.get("/message", headers=hdr)
                body = await r.text()
                assert r.status == 200 and "Индивидуальное сообщение" in body
                assert "@tester" in body and "01A777AA" in body

                # Text-only message to the picked user.
                r = await client.post(
                    "/message",
                    data={"pick_user": "7", "text": "Salom, №1!"},
                    headers=hdr,
                )
                body = await r.text()
                assert bot.sent == [(7, "Salom, №1!")]
                assert "Сообщение отправлено" in body

                # A manually typed user id wins over the picker.
                bot.sent.clear()
                await client.post(
                    "/message",
                    data={"pick_user": "7", "user_id": "8", "text": "Priory"},
                    headers=hdr,
                )
                assert bot.sent == [(8, "Priory")]

                # Missing text and photo → error, nothing sent.
                bot.sent.clear()
                r = await client.post("/message", data={"pick_user": "7"}, headers=hdr)
                assert "Введите текст" in await r.text()
                assert bot.sent == []

                # Missing recipient → error, nothing sent.
                r = await client.post("/message", data={"text": "Kimsiz"}, headers=hdr)
                assert "Выберите получателя" in await r.text()

                # Message with a photo → send_photo with the text as caption.
                import aiohttp

                form = aiohttp.FormData()
                form.add_field("pick_user", "7")
                form.add_field("text", "Rasm bilan")
                form.add_field(
                    "photo", io.BytesIO(b"jpeg-data"), filename="p.jpg",
                    content_type="image/jpeg",
                )
                await client.post("/message", data=form, headers=hdr)
                assert bot.sent_photos and bot.sent_photos[0][0] == 7
                assert bot.sent_photos[0][2] == "Rasm bilan"

                # Quick form on the application page: sends + redirects + banner.
                bot.sent.clear()
                r = await client.post(
                    f"/application/{app_id}/message",
                    data={"text": "Xabar from app"},
                    headers=hdr,
                    allow_redirects=False,
                )
                assert r.status == 302 and "sent=1" in r.headers["Location"]
                assert bot.sent == [(7, "Xabar from app")]
                r = await client.get(f"/application/{app_id}?sent=1", headers=hdr)
                body = await r.text()
                assert "Xabar yuborildi" in body
                assert f'action="/application/{app_id}/message"' in body

        asyncio.run(run())


if __name__ == "__main__":
    test_cookie_signing()
    test_routes()
    test_broadcast_two_languages()
    test_broadcast_language_and_direction_filters()
    test_broadcast_with_photo()
    test_broadcast_with_distinct_photos_per_language()
    test_broadcast_accepts_photo_over_one_megabyte()
    test_badge_photo_route()
    test_individual_message()
    print("All admin tests passed.")
