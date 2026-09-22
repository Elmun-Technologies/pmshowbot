#!/usr/bin/env python3
"""How long does Accept take — for the moderator, and for the participant?

The complaint was «prinyat bosilsa judayam sekin ishlayapti, keyin zayafka uje
obrabotana deyapti»: the button stayed spinning, the moderator tapped again and
was told the application was already processed — while the participant got no
ticket at all.

This script drives a full registration through the real dispatcher and then taps
Accept from the moderation chat, with a session that emulates a phone-grade
uplink (a photo upload takes as long as its bytes divided by ``--bps``; the real
value of that is the whole point, so it is a parameter).  It reports two
numbers:

* **answer latency** — when ``AnswerCallbackQuery`` goes out, which is what the
  moderator's spinner waits for.  It used to be the *last* thing the handler did
  (after the render and the multi-megabyte upload); it is now the second.
* **ticket latency** — when the ticket photo reaches the participant, and
  whether the card then says «🎫 Билет отправлен участнику».

Usage:
    .venv/bin/python diagnostics/perf_accept.py [repo_path] [--bps 500000]
"""
from __future__ import annotations

import argparse
import asyncio
import io
import os
import random
import sys
import time

REPO = os.path.abspath(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from PIL import Image  # noqa: E402

from harness import BotHarness, FakeSession, FAKE_TOKEN  # noqa: E402

USER = 90210
MODERATOR = 90211


def _phone_photo(width: int = 3024, height: int = 4032) -> bytes:
    """A photo like the ones participants upload: big and noisy (not compressible)."""
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    random.seed(7)
    for y in range(0, height, 4):
        for x in range(0, width, 4):
            colour = (
                90 + int(50 * x / width) + random.randint(-30, 30),
                60 + int(60 * y / height) + random.randint(-30, 30),
                random.randint(40, 90),
            )
            for dy in range(4):
                for dx in range(4):
                    if x + dx < width and y + dy < height:
                        pixels[x + dx, y + dy] = colour
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


class UplinkSession(FakeSession):
    """A Telegram session that spends time uploading, like a real one."""

    def __init__(self, photo: bytes, bytes_per_second: float) -> None:
        super().__init__()
        self.photo = photo
        self.bytes_per_second = bytes_per_second
        self.t0 = time.perf_counter()
        self.timeline: list[tuple[float, str, int]] = []

    async def stream_content(self, *args, **kwargs):  # photo download
        yield self.photo

    def _payload_size(self, method) -> int:
        for attribute in ("photo", "document"):
            payload = getattr(method, attribute, None)
            if payload is None:
                continue
            data = getattr(payload, "data", None)
            if data is not None:
                return len(data) if hasattr(data, "__len__") else len(data.getvalue())
            if getattr(payload, "path", None):
                return os.path.getsize(payload.path)
        return 0

    async def make_request(self, bot, method, timeout=None):  # type: ignore[override]
        name = type(method).__name__
        size = self._payload_size(method) if name in {"SendPhoto", "SendDocument"} else 0
        self.timeline.append((time.perf_counter() - self.t0, name, size))
        if size:
            await asyncio.sleep(size / self.bytes_per_second)
        return await super().make_request(bot, method, timeout)


async def _register(harness: BotHarness) -> int:
    await harness.send_command(USER, "/start")
    await harness.tap(USER, "lang:ru")
    await harness.tap(USER, "country:1")
    await harness.send_text(USER, "01A123BC")
    for method in reversed(harness.session.methods):
        markup = getattr(method, "reply_markup", None)
        rows = getattr(markup, "inline_keyboard", None)
        if not rows:
            continue
        for row in rows:
            for button in row:
                if "SQ" in button.text:
                    await harness.tap(USER, button.callback_data, text="directions")
                    break
            else:
                continue
            break
        break
    for index in range(4):
        await harness.send_photo(USER, f"side-{index}")
    await harness.tap(USER, "modsdone", text="mods")
    await harness.send_contact(USER)
    await asyncio.sleep(0.2)
    apps = await harness.db.for_tenant(harness.tenant.id).list_applications()
    return apps[0].id


async def main(bytes_per_second: float) -> None:
    session = UplinkSession(_phone_photo(), bytes_per_second)
    harness = BotHarness()
    from aiogram import Bot

    harness.session = session
    harness.bot = Bot(token=FAKE_TOKEN, session=session)
    await harness.start()
    try:
        app_id = await _register(harness)
        session.timeline.clear()
        session.t0 = time.perf_counter()

        await harness.tap(
            MODERATOR, f"approve:{app_id}", chat_id=harness.admin_chat_id, settle=False
        )
        # Let the handler + the background delivery run to the held upload.
        for _ in range(20):
            await asyncio.sleep(0.01)
        answered = next(
            (moment for moment, name, _ in session.timeline if name == "AnswerCallbackQuery"),
            None,
        )
        print(f"upload emulation: {bytes_per_second/1000:.0f} KB/s")
        print(f"answer latency (spinner): {answered:.2f} s" if answered else "answer: MISSING")

        await harness.settle(timeout=120)
        photos = [
            (moment, size)
            for moment, name, size in session.timeline
            if name == "SendPhoto" and size
        ]
        if photos:
            moment, size = photos[-1]
            print(f"ticket latency: {moment:.2f} s (uploaded {size/1024/1024:.2f} MB)")
        else:
            print("ticket: NOT DELIVERED")
        print("card:", [
            m.text for m in harness.session.methods_named("EditMessageText")
        ][-1].replace("\n", " ")[:160])
    finally:
        await harness.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="?", default=REPO)
    parser.add_argument("--bps", type=float, default=500_000.0)
    args = parser.parse_args()
    asyncio.run(main(args.bps))
