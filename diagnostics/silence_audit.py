#!/usr/bin/env python3
"""Silence audit: does *every* possible update get an answer?

Reported symptom: "the bot freezes".  The log line the user has is

    Update id=322515500 is handled. Duration 1 ms by bot id=8933846661

A 1 ms "is handled" with no error means the update matched either nothing at
all or a handler that returned without sending anything.  The participant sees
a dead bot, the log shows no failure.

This script drives the **real production dispatcher** (built by
``bot.bot_manager.BotManager`` through ``tests/harness.py``) over

    8 form steps x 8 input kinds = 64 cases
    + the same 8 input kinds with no active form state

and counts the answers that come back.  Anything with zero answers is a place
where a participant gets silence.

Usage:  .venv/bin/python silence_audit.py [repo_path]
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys

REPO = os.path.abspath(
    sys.argv[1]
    if len(sys.argv) > 1
    else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tests"))

from aiogram.fsm.storage.base import StorageKey  # noqa: E402
from aiogram.types import (  # noqa: E402
    CallbackQuery,
    Chat,
    Contact,
    Document,
    Message,
    PhotoSize,
    Sticker,
    Update,
    User,
)

from harness import BotHarness  # noqa: E402
from bot.states import Registration  # noqa: E402

USER_ID = 555000111
CHAT_ID = USER_ID

STEPS = [
    ("language", Registration.language),
    ("country", Registration.country),
    ("country_other", Registration.country_other),
    ("plate", Registration.plate),
    ("direction", Registration.direction),
    ("sub_direction", Registration.sub_direction),
    ("photos", Registration.photos),
    ("mods", Registration.mods),
    ("phone", Registration.phone),
]

INPUTS = [
    "text",
    "start",
    "unknown_command",
    "photo",
    "contact",
    "stale_callback",
    "document",
    "sticker",
    "mynumber_button",
]


def _msg(message_id: int, *, text=None, photo=None, contact=None, document=None, sticker=None) -> Message:
    return Message(
        message_id=message_id,
        date=dt.datetime.now(dt.timezone.utc),
        chat=Chat(id=CHAT_ID, type="private"),
        from_user=User(id=USER_ID, is_bot=False, first_name="Tester", username="tester"),
        text=text,
        photo=[
            PhotoSize(file_id=photo, file_unique_id=f"{photo}-u", width=1080, height=720)
        ]
        if photo
        else None,
        contact=contact,
        document=document,
        sticker=sticker,
    )


def _build_payload(kind: str):
    if kind == "text":
        return {"message": _msg(1, text="salom, nima gap?")}
    if kind == "start":
        return {"message": _msg(2, text="/start")}
    if kind == "unknown_command":
        return {"message": _msg(3, text="/help")}
    if kind == "photo":
        return {"message": _msg(4, photo=f"file-{kind}")}
    if kind == "contact":
        return {
            "message": _msg(
                5, contact=Contact(phone_number="+998901234567", first_name="Tester")
            )
        }
    if kind == "document":
        return {
            "message": _msg(
                6,
                document=Document(
                    file_id="doc-1", file_unique_id="doc-1u", file_name="photo.jpg"
                ),
            )
        }
    if kind == "sticker":
        return {
            "message": _msg(
                7,
                sticker=Sticker(
                    file_id="st-1",
                    file_unique_id="st-1u",
                    type="regular",
                    width=512,
                    height=512,
                    is_animated=False,
                    is_video=False,
                ),
            )
        }
    if kind == "mynumber_button":
        # The reply-keyboard "Узнать свой номер" button; text-based, no state.
        from bot import texts

        return {"message": _msg(8, text=texts.RU.BTN_MY_NUMBER)}
    if kind == "stale_callback":
        return {"callback_query": _callback("direction:999999")}
    raise AssertionError(kind)


def _callback(data: str) -> CallbackQuery:
    return CallbackQuery(
        id=f"cq-{data}",
        from_user=User(id=USER_ID, is_bot=False, first_name="Tester"),
        chat_instance="ci",
        message=_msg(9, text="card"),
        data=data,
    )


def _answers(session, before: int) -> int:
    """How many Telegram calls this update produced for our participant."""
    count = 0
    for method in session.methods[before:]:
        name = type(method).__name__
        if name == "AnswerCallbackQuery":
            count += 1
            continue
        if getattr(method, "chat_id", None) in (CHAT_ID, USER_ID):
            count += 1
    return count


async def main() -> int:
    harness = BotHarness()
    await harness.start()
    storage = harness.dispatcher.storage
    key = StorageKey(bot_id=harness.bot.id, chat_id=CHAT_ID, user_id=USER_ID)

    silent: list[tuple[str, str]] = []
    cases = 0
    try:
        for step_name, step_state in STEPS:
            for kind in INPUTS:
                await storage.set_state(key, step_state.state)
                await storage.set_data(key, {"lang": "ru", "country": "Узбекистан"})
                payload = _build_payload(kind)
                update = Update(update_id=1000 + cases, **payload)
                cases += 1
                before = len(harness.session.methods)
                try:
                    await harness.feed(update)
                except Exception as exc:  # a crash is *not* silence, report it
                    print(f"RAISED  step={step_name:<14} input={kind:<16} {type(exc).__name__}: {exc}")
                    continue
                answers = _answers(harness.session, before)
                if answers == 0:
                    silent.append((step_name, kind))
                    print(f"SILENT  step={step_name:<14} input={kind}")
                else:
                    print(f"ok      step={step_name:<14} input={kind:<16} answers={answers}")

        # No active state at all — what a returning participant looks like.
        for kind in INPUTS:
            await storage.set_state(key, None)
            await storage.set_data(key, {})
            payload = _build_payload(kind)
            update = Update(update_id=9000 + cases, **payload)
            cases += 1
            before = len(harness.session.methods)
            try:
                await harness.feed(update)
            except Exception as exc:
                print(f"RAISED  step=<none>        input={kind:<16} {type(exc).__name__}: {exc}")
                continue
            answers = _answers(harness.session, before)
            if answers == 0:
                silent.append(("<none>", kind))
                print(f"SILENT  step=<none>        input={kind}")
            else:
                print(f"ok      step=<none>        input={kind:<16} answers={answers}")
    finally:
        await harness.stop()

    print()
    print(f"cases={cases}  silent={len(silent)}")
    for step, kind in silent:
        print(f"  SILENT: step={step} input={kind}")
    return 1 if silent else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
