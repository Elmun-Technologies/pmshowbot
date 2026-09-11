"""Tests for the RegistrationClosedMiddleware.

When registration is closed:
- A user mid-form (any step after language) must NOT be able to continue —
  they get the "registration finished" notice and their FSM state is cleared.
- People who already have an application still pass through to /start's
  status view.
- The /start command and the language step are left to their own handlers.
- When registration is open, everything passes through untouched.
"""
import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aiogram.types import Message, User, Chat, CallbackQuery  # noqa: E402
from bot.middlewares import RegistrationClosedMiddleware  # noqa: E402
from bot.states import Registration  # noqa: E402


def _config(closed: bool) -> SimpleNamespace:
    return SimpleNamespace(registration_closed=closed)


class _FakeState:
    def __init__(self, current=None, data=None):
        self._current = current
        self._data = data or {}
        self.cleared = False

    async def get_state(self):
        return self._current

    async def get_data(self):
        return self._data

    async def clear(self):
        self.cleared = True


class _FakeDb:
    def __init__(self, active=False):
        self._active = active

    async def has_active_application(self, user_id):
        return "app" if self._active else None


def _msg(state) -> Message:
    msg = Message(
        message_id=1,
        date=__import__("datetime").datetime.now(),
        chat=Chat(id=1, type="private"),
        from_user=User(id=1, is_bot=False, first_name="A"),
    )
    # Stub the answer coroutine — the real one needs a mounted bot, which we
    # don't spin up in this unit test. We only assert it gets called.
    sent = []

    async def answer(text, **kwargs):
        sent.append(text)

    object.__setattr__(msg, "answer", answer)
    msg._sent = sent
    return msg


def _query(state) -> CallbackQuery:
    msg = _msg(state)
    query = CallbackQuery(id="q", from_user=User(id=1, is_bot=False, first_name="A"),
                          chat_instance="ci", message=msg, data="x")

    async def answer(*args, **kwargs):
        return None

    object.__setattr__(query, "answer", answer)
    return query


def _run(mw, event, data):
    async def run():
        called = []
        async def handler(e, d):
            called.append(True)
            return "handled"
        return await mw(handler, event, data), called
    return asyncio.run(run())


def _user_data():
    return {"event_from_user": SimpleNamespace(id=1)}


def test_open_registration_passes_through():
    mw = RegistrationClosedMiddleware()
    for current in (None, Registration.language, Registration.country, Registration.phone):
        state = _FakeState(current=current, data={"lang": "ru"})
        result, called = _run(mw, _msg(state), {**_user_data(), "config": _config(False), "state": state})
        assert result == "handled"
        assert called == [True]


def test_mid_form_is_blocked_when_closed():
    mw = RegistrationClosedMiddleware()
    for current in Registration.__all_states__:
        if current == Registration.language:
            continue
        state = _FakeState(current=current, data={"lang": "uz"})
        result, called = _run(mw, _msg(state), {**_user_data(), "config": _config(True), "state": state, "db": _FakeDb()})
        assert called == []
        assert state.cleared is True
        assert result is None


def test_start_and_language_are_not_blocked():
    mw = RegistrationClosedMiddleware()
    for current in (None, Registration.language):
        state = _FakeState(current=current, data={"lang": "ru"})
        result, called = _run(mw, _msg(state), {**_user_data(), "config": _config(True), "state": state, "db": _FakeDb()})
        assert result == "handled"
        assert called == [True]
        assert state.cleared is False


def test_existing_applicant_still_passes_through():
    mw = RegistrationClosedMiddleware()
    state = _FakeState(current=Registration.country, data={"lang": "ru"})
    result, called = _run(mw, _msg(state), {**_user_data(), "config": _config(True), "state": state, "db": _FakeDb(active=True)})
    assert result == "handled"
    assert called == [True]


def test_callback_query_mid_form_is_blocked():
    mw = RegistrationClosedMiddleware()
    state = _FakeState(current=Registration.country, data={"lang": "ru"})
    result, called = _run(mw, _query(state), {**_user_data(), "config": _config(True), "state": state, "db": _FakeDb()})
    assert called == []
    assert state.cleared is True


if __name__ == "__main__":
    test_open_registration_passes_through()
    test_mid_form_is_blocked_when_closed()
    test_start_and_language_are_not_blocked()
    test_existing_applicant_still_passes_through()
    test_callback_query_mid_form_is_blocked()
    print("All registration-closed middleware tests passed.")
