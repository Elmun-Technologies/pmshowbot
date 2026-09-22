"""No update may ever be answered with silence.

The reported symptom was "the bot freezes" and the only evidence was

    Update id=322515500 is handled. Duration 1 ms by bot id=8933846661

— 1 ms, no error.  That is exactly what an *unmatched* update looks like: no
handler claimed it, nothing raised, aiogram logged "is handled" anyway and the
participant got nothing.

``diagnostics/silence_audit.py`` walks 9 form steps x 9 input kinds plus the
same inputs with no active state (90 cases) through the real dispatcher.  Before
the safety-net router 10 of them were silent; all of them were one of

* an unknown command sent while the user has a form step, or
* anything sent when the user has no state at all (the two catch-alls were
  registered with ``StateFilter(*Registration.__all_states__)``, which cannot
  match a user without a state).

The cases below are the ones that mattered in production, expressed as tests
against the real dispatcher.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

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

USER = 4242


def _message(
    *,
    text: str | None = None,
    photo: str | None = None,
    contact: Contact | None = None,
    document: Document | None = None,
    sticker: Sticker | None = None,
    chat_type: str = "private",
    chat_id: int = USER,
) -> Message:
    return Message(
        message_id=1,
        date=dt.datetime.now(dt.timezone.utc),
        chat=Chat(id=chat_id, type=chat_type),
        from_user=User(id=USER, is_bot=False, first_name="Tester", username="tester"),
        text=text,
        photo=(
            [PhotoSize(file_id=photo, file_unique_id=f"{photo}-u", width=1080, height=720)]
            if photo
            else None
        ),
        contact=contact,
        document=document,
        sticker=sticker,
    )


def _callback(data: str) -> CallbackQuery:
    return CallbackQuery(
        id=f"cq-{data}",
        from_user=User(id=USER, is_bot=False, first_name="Tester"),
        chat_instance="ci",
        message=_message(text="card"),
        data=data,
    )


async def _answers(harness: BotHarness, payload: dict, *, update_id: int = 7) -> int:
    before = len(harness.session.methods)
    await harness.feed(Update(update_id=update_id, **payload))
    return len(harness.session.methods) - before


async def _set_state(harness: BotHarness, state, data: dict | None = None) -> StorageKey:
    key = StorageKey(bot_id=harness.bot.id, chat_id=USER, user_id=USER)
    await harness.dispatcher.storage.set_state(key, state.state if state else None)
    await harness.dispatcher.storage.set_data(key, data or {"lang": "ru"})
    return key


def test_unknown_command_mid_form_is_answered():
    """`/help`, or any command the browser keyboard sends, used to be swallowed.

    It was worse than it looks: the catch-all handler *skipped* commands (so
    they could reach their own handlers) and no handler owned them.
    """

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _set_state(harness, Registration.photos, {"lang": "ru"})
            answered = await _answers(harness, {"message": _message(text="/help")})
            assert answered, "an unknown command mid-form got no answer"
            assert harness.private_texts(USER), "no message reached the participant"
        finally:
            await harness.stop()

    asyncio.run(run())


def test_plain_text_without_any_state_is_answered():
    """The exact production case: no form, no application, one text message."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _set_state(harness, None, {})
            answered = await _answers(harness, {"message": _message(text="salom")})
            assert answered, "a text message outside the form got no answer"
            text = harness.private_texts(USER)[-1]
            assert "/start" in text, text
        finally:
            await harness.stop()

    asyncio.run(run())


def test_every_input_kind_without_state_is_answered():
    """Text, contact, document, sticker and a stale button, all with no state."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            cases = {
                "text": {"message": _message(text="ассалом")},
                "unknown_command": {"message": _message(text="/whoami")},
                "contact": {
                    "message": _message(
                        contact=Contact(phone_number="+998901234567", first_name="T")
                    )
                },
                "document": {
                    "message": _message(
                        document=Document(
                            file_id="d1", file_unique_id="d1u", file_name="photo.jpg"
                        )
                    )
                },
                "sticker": {
                    "message": _message(
                        sticker=Sticker(
                            file_id="s1",
                            file_unique_id="s1u",
                            type="regular",
                            width=512,
                            height=512,
                            is_animated=False,
                            is_video=False,
                        )
                    )
                },
                "stale_callback": {"callback_query": _callback("direction:999999")},
            }
            for name, payload in cases.items():
                await _set_state(harness, None, {})
                assert await _answers(harness, payload), f"{name} got no answer"
        finally:
            await harness.stop()

    asyncio.run(run())


def test_stale_callback_is_always_acknowledged():
    """An unanswered callback query leaves the button spinning forever."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _set_state(harness, None, {})
            before = len(harness.session.methods)
            await harness.feed(
                Update(update_id=11, callback_query=_callback("mods:done:old"))
            )
            methods = harness.session.methods[before:]
            assert any(type(m).__name__ == "AnswerCallbackQuery" for m in methods), (
                "the callback query was not acknowledged"
            )
        finally:
            await harness.stop()

    asyncio.run(run())


def test_user_with_an_application_gets_their_status():
    """Not just *an* answer: the useful one."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await harness.db.create_application(
                tenant_id=harness.tenant.id,
                user_id=USER,
                username="@tester",
                country="Узбекистан",
                plate="01A123BC",
                direction="Drift",
                phone="+998901234567",
                photo_file_ids=[],
                photo_paths=[],
                language="ru",
            )
            await _set_state(harness, None, {})
            assert await _answers(harness, {"message": _message(text="nima bo'ldi?")})
            assert harness.private_texts(USER), "the applicant got no status"
        finally:
            await harness.stop()

    asyncio.run(run())


def test_safety_net_never_touches_the_admin_group():
    """Moderation traffic must not be swallowed by the last-resort router."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            group = harness.admin_chat_id
            before = len(harness.session.methods)
            await harness.feed(
                Update(
                    update_id=12,
                    message=_message(
                        text="любое сообщение в группе",
                        chat_type="supergroup",
                        chat_id=group,
                    ),
                )
            )
            assert len(harness.session.methods) == before, (
                "the safety net answered inside the moderation group"
            )
        finally:
            await harness.stop()

    asyncio.run(run())


def test_unknown_command_is_answered_in_every_step():
    """Regression sweep: 9 steps x 1 command that no router owns."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            for state in Registration.__all_states__:
                await _set_state(harness, state, {"lang": "uz"})
                assert await _answers(harness, {"message": _message(text="/help")}), (
                    f"no answer in state {state.state}"
                )
        finally:
            await harness.stop()

    asyncio.run(run())


def test_closed_registration_never_leaves_a_mid_form_user_silent():
    """Scenario (a) of the review, end to end.

    ``RegistrationClosedMiddleware`` deliberately clears the state of somebody
    who is halfway through the form and answers once with the "registration
    finished" notice.  Every message after that used to produce *nothing at
    all*: the state was gone, the two catch-alls required a state, and the
    participant concluded the bot was frozen.  The reminder must keep coming.
    """

    async def run():
        from bot.bot_manager import BotManager

        harness = BotHarness()
        await harness.start()
        try:
            await harness.db.update_tenant(harness.tenant.id, registration_closed=True)
            tenant = await harness.db.get_tenant(harness.tenant.id)
            harness.tenant_config = harness.config.tenant_config(
                tenant, bot_token=harness.bot.token
            )
            # The panel save that switches the checkbox also restarts the worker.
            manager = BotManager(harness.db, harness.config, storage=harness._storage)
            dispatcher = manager._new_dispatcher(harness.tenant_config)

            await _set_state(harness, Registration.photos, {"lang": "ru"})
            before = len(harness.session.methods)
            await dispatcher.feed_update(harness.bot, Update(
                update_id=21, message=_message(photo="closed-1")
            ))
            first = len(harness.session.methods) - before
            assert first, "the closed notice was not sent"

            # Now the state is cleared — this is where the silence used to start.
            for payload in (
                {"message": _message(text="neda?")},
                {"message": _message(photo="closed-2")},
                {"callback_query": _callback("mods:done")},
            ):
                before = len(harness.session.methods)
                await dispatcher.feed_update(harness.bot, Update(update_id=22, **payload))
                assert len(harness.session.methods) > before, (
                    f"{list(payload)[0]} got no answer once the form was closed"
                )
        finally:
            await harness.stop()

    asyncio.run(run())


def test_group_callback_is_acknowledged_without_posting_into_the_group():
    """A stale button in the moderation chat must not make the bot talk there.

    The callback is still acknowledged — otherwise the moderator's client keeps
    the spinner — but nothing is sent to the group, which is their workspace.
    """

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            group = harness.admin_chat_id
            query = CallbackQuery(
                id="cq-group",
                from_user=User(id=999, is_bot=False, first_name="Moderator"),
                chat_instance="ci",
                message=_message(text="card", chat_type="supergroup", chat_id=group),
                data="approve:424242",
            )
            before = len(harness.session.methods)
            await harness.feed(Update(update_id=31, callback_query=query))
            methods = harness.session.methods[before:]
            names = [type(m).__name__ for m in methods]
            assert "AnswerCallbackQuery" in names, names
            sent_to_group = [
                m for m in methods if getattr(m, "chat_id", None) == group
            ]
            assert not sent_to_group, [type(m).__name__ for m in sent_to_group]
        finally:
            await harness.stop()

    asyncio.run(run())


if __name__ == "__main__":
    test_unknown_command_mid_form_is_answered()
    test_plain_text_without_any_state_is_answered()
    test_every_input_kind_without_state_is_answered()
    test_stale_callback_is_always_acknowledged()
    test_user_with_an_application_gets_their_status()
    test_safety_net_never_touches_the_admin_group()
    test_unknown_command_is_answered_in_every_step()
    test_closed_registration_never_leaves_a_mid_form_user_silent()
    test_group_callback_is_acknowledged_without_posting_into_the_group()
    print("All no-silence tests passed.")
