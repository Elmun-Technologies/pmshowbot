"""End-to-end registration flow on a real dispatcher (see ``tests/harness.py``).

Every test here reproduces a bug the client reported in production:

* the SPL bot showed another event's (Promotors) direction list and only three
  SPL Avtozvuk categories — the direction query used the wrong database facade
  and raised a ``TypeError`` swallowed by a broad ``except``;
* choosing a category froze the bot — ``format_final_choice`` was called with a
  keyword it does not accept;
* after ``/start`` an approved participant got Promotors' September dates;
* a second photo could stop the flow (duplicate updates / failed downloads);
* a decision taken in the web panel never showed up in the moderation chat.
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from aiogram.exceptions import TelegramBadRequest  # noqa: E402

from bot.db import (  # noqa: E402
    SPL_AUTOSOUND_CHILDREN,
    SPL_AUTOSOUND_STALE,
    STATUS_APPROVED,
    STATUS_PENDING,
)
from bot.services import decisions  # noqa: E402
from harness import BotHarness  # noqa: E402

USER = 4242
MODERATOR = 777


async def _register_until_directions(harness: BotHarness) -> None:
    await harness.send_command(USER, "/start")
    await harness.tap(USER, "lang:ru")
    await harness.tap(USER, "country:1")
    await harness.send_text(USER, "01A123BC")


async def _direction_keyboard(harness: BotHarness):
    for method in reversed(harness.session.methods):
        if type(method).__name__ == "SendMessage" and getattr(method, "reply_markup", None):
            if getattr(method.reply_markup, "inline_keyboard", None):
                return method.reply_markup.inline_keyboard
    return []


async def _tap_root(harness: BotHarness, label: str) -> str:
    """Tap a direction button whose label matches (``"SQ"`` → ``"SQ - …"``)."""
    for row in await _direction_keyboard(harness):
        for button in row:
            if button.text == label or label in button.text:
                await harness.tap(USER, button.callback_data, text="directions")
                return button.callback_data
    raise AssertionError(f"direction {label!r} not found in the current keyboard")


async def _pick_only(harness: BotHarness, label: str) -> None:
    """Pick one direction and finish the choice with «Готово»."""
    await _tap_root(harness, label)
    await harness.tap(USER, "dirdone", text="directions")


async def _tap_child(harness: BotHarness, label: str) -> str:
    """Tap a sub-direction button by its label."""
    for row in await _direction_keyboard(harness):
        for button in row:
            if button.text == label:
                await harness.tap(USER, button.callback_data, text="sub-direction")
                return button.callback_data
    raise AssertionError(f"sub-direction {label!r} not found")


async def _complete_form(harness: BotHarness, *, photos: int = 4, mods: int = 1) -> None:
    for index in range(photos):
        await harness.send_photo(USER, f"photo-{index}")
    if mods:
        await harness.send_photo(USER, "mod-1")
    await harness.tap(USER, "modsdone", text="mods card")
    await harness.send_contact(USER)


def test_direction_list_comes_from_the_tenant_database():
    """The SPL bot must show its own directions, not the legacy Promotors list."""

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            labels = [b.text for row in await _direction_keyboard(harness) for b in row]
            assert labels == ["SQ - Качество звучания", "Выставка", "Тюнинг", "SPL Автозвук"], labels
            # The global list that used to leak through:
            assert "Adrenaline Drift" not in labels
            assert "SPL Tuning" not in labels
        finally:
            await harness.stop()

    asyncio.run(run())


AUTOSOUND_RU = [
    "SPL Sport Багажник 2К", "SPL Sport Багажник 4К", "SPL Sport Максимум", "SPL Sport Салон",
    "SPL Show Лайт", "SPL Show Стандарт", "SPL Show Профи", "SPL Show Полубронь",
    "SPL Front Лайт", "SPL Front Стандарт", "SPL Front Максимум",
    "SPL Тыл Стандарт", "SPL Тыл Максимум",
    "SPL Game 129.99", "SPL Game 139.99", "SPL Game 149.99",
]


def _category_labels(rows) -> list[str]:
    """Category buttons only (without «Назад» / «Готово»)."""
    return [
        b.text for row in rows for b in row
        if b.callback_data.startswith("subdirection:")
    ]


def test_autosound_shows_the_sixteen_confirmed_categories_in_both_languages():
    async def run(lang: str):
        harness = BotHarness()
        await harness.start()
        try:
            await harness.send_command(USER, "/start")
            await harness.tap(USER, f"lang:{lang}")
            await harness.tap(USER, "country:1")
            await harness.send_text(USER, "01A123BC")
            prompt = harness.private_texts(USER)[-1]
            assert ("до 4 категорий" in prompt) if lang == "ru" else ("4 tagacha" in prompt), prompt
            await _tap_root(harness, "SPL Автозвук" if lang == "ru" else "SPL Avtozvuk")
            labels = _category_labels(await _direction_keyboard(harness))
            assert len(labels) == 16, labels
            if lang == "ru":
                assert labels == AUTOSOUND_RU, labels
            # The old four are gone.
            assert "SPL Sport / SPL Show" not in labels
            assert "SPL Game (129/139/149)" not in labels
            # Nobody sees «Spl Show».
            assert not any("Spl " in label for label in labels)
        finally:
            await harness.stop()

    asyncio.run(run("ru"))
    asyncio.run(run("uz"))


def test_up_to_four_categories_with_the_menu_reopening_after_each_pick():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _tap_root(harness, "SPL Автозвук")
            await _tap_child(harness, "SPL Sport Салон")
            # The menu reopens (same parent), without the picked one, with «Готово».
            rows = await _direction_keyboard(harness)
            labels = _category_labels(rows)
            assert "SPL Sport Салон" not in labels and len(labels) == 15, labels
            assert any(b.callback_data == "dirdone" for row in rows for b in row)
            assert "Выбрано (1 из 4)" in harness.private_texts(USER)[-1]

            await _tap_child(harness, "SPL Show Профи")
            # Back to the root menu and a root direction as the third pick.
            await harness.tap(USER, "dirback", text="directions")
            await _tap_root(harness, "SQ")
            assert "Выбрано (3 из 4)" in harness.private_texts(USER)[-1]
            await _tap_root(harness, "SPL Автозвук")
            await _tap_child(harness, "SPL Game 139.99")
            # The fourth pick finishes the choice by itself.
            texts_ = harness.private_texts(USER)
            assert any("Ваши категории" in t for t in texts_[-3:]), texts_[-3:]
            assert "1 из 4" in texts_[-1]  # first photo prompt

            await _complete_form(harness, mods=0)
            app = (await harness.db.for_tenant(harness.tenant.id).list_applications())[0]
            assert app.direction == (
                "SPL Автозвук — SPL Sport Салон; SPL Автозвук — SPL Show Профи; "
                "SQ; SPL Автозвук — SPL Game 139.99"
            ), app.direction
            card = [
                m.text for m in harness.session.methods_named("SendMessage")
                if m.chat_id == harness.admin_chat_id and m.reply_markup
            ][-1]
            assert "• SQ" in card and "• SPL Автозвук — SPL Game 139.99" in card, card
        finally:
            await harness.stop()

    asyncio.run(run())


def test_done_after_one_category_and_stale_done_is_answered():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            # «Готово» before anything is picked just repeats the menu.
            await harness.tap(USER, "dirdone", text="directions")
            assert "до 4 категорий" in harness.private_texts(USER)[-1]
            await _tap_root(harness, "SPL Автозвук")
            await _tap_child(harness, "SPL Front Лайт")
            await harness.tap(USER, "dirdone", text="directions")
            assert "1 из 4" in harness.private_texts(USER)[-1]
            await _complete_form(harness, mods=0)
            app = (await harness.db.for_tenant(harness.tenant.id).list_applications())[0]
            assert app.direction == "SPL Автозвук — SPL Front Лайт"
        finally:
            await harness.stop()

    asyncio.run(run())


def test_full_form_stores_parent_child_direction_and_all_photos():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _tap_root(harness, "SPL Автозвук")
            await _tap_child(harness, "SPL Show Полубронь")
            await harness.tap(USER, "dirdone", text="directions")

            await _complete_form(harness)

            apps = await harness.db.for_tenant(harness.tenant.id).list_applications()
            assert len(apps) == 1
            app = apps[0]
            assert app.direction == "SPL Автозвук — SPL Show Полубронь"
            assert app.direction_id is not None
            assert app.plate == "01A123BC"
            assert app.phone.startswith("+998")
            assert app.status == STATUS_PENDING
            assert len(app.photo_paths) == 4
            assert len(app.mod_paths) == 1
            for path in app.photo_paths + app.mod_paths:
                assert os.path.getsize(path) > 0
            assert [os.path.basename(p) for p in app.photo_paths] == [
                "left.jpg", "right.jpg", "front.jpg", "back.jpg"
            ]
            cards = [
                m for m in harness.session.methods_named("SendMessage")
                if m.chat_id == harness.admin_chat_id and m.reply_markup
            ]
            assert cards, "moderation card was not sent"
            assert cards[-1].reply_markup.inline_keyboard[0][0].callback_data == f"approve:{app.id}"
            assert app.card_message_id is not None
        finally:
            await harness.stop()

    asyncio.run(run())


def test_start_after_registration_is_tenant_branded_not_promotors():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")
            await _complete_form(harness, mods=0)
            app = (await harness.db.for_tenant(harness.tenant.id).list_applications())[0]
            await harness.tap(MODERATOR, f"approve:{app.id}", chat_id=harness.admin_chat_id)

            await harness.send_command(USER, "/start")
            answer = harness.private_texts(USER)[-1]
            assert "11 сентября" not in answer
            assert "promotorsshow" not in answer
            assert "SPL Show" in answer or "t.me/splshow" in answer
            # No date is invented: the SPL schedule comes only from the panel.
            assert "октября" not in answer
            assert "№1" in answer
        finally:
            await harness.stop()

    asyncio.run(run())


def test_approve_from_telegram_updates_the_card_without_freeze():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")
            await _complete_form(harness, mods=0)
            app = (await harness.db.for_tenant(harness.tenant.id).list_applications())[0]

            await harness.tap(MODERATOR, f"approve:{app.id}", chat_id=harness.admin_chat_id)

            stored = await harness.db.for_tenant(harness.tenant.id).get_application(app.id)
            assert stored.status == STATUS_APPROVED
            assert stored.reg_number == 1
            # The card was edited (no more buttons) and carries the decision.
            edits = harness.session.methods_named("EditMessageText")
            assert edits, "moderation card was not updated"
            assert "Принято" in edits[-1].text
            assert "№1" in edits[-1].text
            # The participant got the tenant-branded approval + a ticket.
            texts = harness.private_texts(USER)
            assert any("№1" in t and "Поздравляем" in t for t in texts), texts[-3:]
            assert not any("3 октября" in t for t in texts)
            assert not any("11 сентября" in t for t in texts)
            assert harness.session.methods_named("SendPhoto"), "ticket photo was not sent"
            # The moderator's callback was always answered.
            assert harness.session.methods_named("AnswerCallbackQuery")
        finally:
            await harness.stop()

    asyncio.run(run())


def test_panel_decision_clears_buttons_and_posts_to_the_group():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")
            await _complete_form(harness, mods=0)
            scoped = harness.db.for_tenant(harness.tenant.id)
            app = (await scoped.list_applications())[0]
            bot, config = harness.bot, harness.tenant_config
            await decisions.approve_application(
                bot, config, scoped, app.id, "панель", announce_in_chat=True
            )
            assert harness.session.methods_named("EditMessageReplyMarkup"), "buttons were not cleared"
            posted = [
                m for m in harness.session.methods_named("SendMessage")
                if m.chat_id == harness.admin_chat_id and "админ-панель" in (m.text or "")
            ]
            assert posted, [m.text for m in harness.session.methods_named("SendMessage")]
            assert "№1" in posted[-1].text
        finally:
            await harness.stop()

    asyncio.run(run())


def test_duplicate_photo_update_cannot_break_the_sequence():
    """The same photo twice: no fifth side, and an answer either way.

    Telegram re-delivers an update whose handler died, and participants do send
    the same photo twice.  The duplicate is still filed once — but it is no
    longer answered with silence, which was indistinguishable from a frozen bot.
    """

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")
            # Telegram re-delivers the same update: the same file_id twice.
            await harness.send_photo(USER, "left-photo")
            assert "2 из 4" in harness.private_texts(USER)[-1]
            answered = len(harness.private_texts(USER))
            await harness.send_photo(USER, "left-photo")
            # Answered (never silent), the same question repeated, no fifth side.
            assert len(harness.private_texts(USER)) == answered + 1
            assert "2 из 4" in harness.private_texts(USER)[-1]

            for index in range(1, 4):
                await harness.send_photo(USER, f"side-{index}")
            apps = await harness.db.for_tenant(harness.tenant.id).list_applications()
            # Not finished yet — but the flow is alive and already asks about mods.
            assert apps == []
            assert any("изменили" in t for t in harness.private_texts(USER))
        finally:
            await harness.stop()

    asyncio.run(run())


def test_failed_photo_download_asks_to_resend():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")
            harness.session.fail_next(
                "GetFile", TelegramBadRequest(method=None, message="file is too big")
            )
            await harness.send_photo(USER, "broken-photo")
            assert "ещё раз" in harness.private_texts(USER)[-1]
            # The step did not advance: resending works and asks for side 2.
            await harness.send_photo(USER, "good-photo")
            assert "2 из 4" in harness.private_texts(USER)[-1]
        finally:
            await harness.stop()

    asyncio.run(run())


def test_stale_button_repeats_the_step_instead_of_freezing():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            before = len(harness.private_texts(USER))
            await harness.tap(USER, "direction:999999", text="old keyboard")
            answers = harness.session.methods_named("AnswerCallbackQuery")
            assert answers, "the stale button was not answered"
            assert len(harness.private_texts(USER)) > before, "the step was not repeated"
        finally:
            await harness.stop()

    asyncio.run(run())


def test_unexpected_message_is_always_answered():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")
            before = len(harness.private_texts(USER))
            await harness.send_text(USER, "а это что за шаг?")
            after = harness.private_texts(USER)
            assert len(after) > before, "the bot stayed silent"
            assert "фотографи" in after[-1]
        finally:
            await harness.stop()

    asyncio.run(run())


def test_deleting_an_application_lets_the_person_register_again():
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")
            await _complete_form(harness, mods=0)
            scoped = harness.db.for_tenant(harness.tenant.id)
            app = (await scoped.list_applications())[0]
            assert (await scoped.has_active_application(USER)) is not None

            assert await scoped.delete_application(app.id) is not None
            assert not os.path.exists(app.photo_paths[0])
            assert (await scoped.has_active_application(USER)) is None

            # /start now starts a brand new form instead of showing the old status.
            await harness.send_command(USER, "/start")
            assert harness.private_texts(USER)[-1] == "Tilni tanlang / Выберите язык:"
        finally:
            await harness.stop()

    asyncio.run(run())


def test_state_survives_a_worker_restart():
    """A hot reload (admin saves a setting) must not drop people mid-form."""
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")
            await harness.send_photo(USER, "photo-1")

            # Simulate the worker restart: same database, fresh dispatcher.
            from bot.bot_manager import BotManager

            manager = BotManager(harness.db, harness.config)
            manager._storage = None  # rebuild from the database file
            harness.dispatcher = manager._new_dispatcher(harness.tenant_config)
            manager._storage = harness.dispatcher.storage

            await harness.send_photo(USER, "photo-2")
            assert "3 из 4" in harness.private_texts(USER)[-1]
        finally:
            await harness.stop()

    asyncio.run(run())


def test_legacy_database_with_old_spl_seed_is_migrated():
    """A deployment seeded with the placeholder list gets the confirmed four."""
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            import sqlite3

            scoped = harness.db.for_tenant(harness.tenant.id)
            directions = await scoped.list_all_directions()
            root = next(d for d in directions if d.canonical == "SPL Автозвук")
            conn = sqlite3.connect(harness.db_path)
            conn.execute("DELETE FROM directions WHERE parent_id = ?", (root.id,))
            for stale in SPL_AUTOSOUND_STALE:
                conn.execute(
                    "INSERT INTO directions (tenant_id, parent_id, canonical, label_ru,"
                    " label_uz, slug, sort_order, is_active, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, 0, 1, 'now', 'now')",
                    (harness.tenant.id, root.id, stale["canonical"], stale["canonical"],
                     stale["canonical"], stale["slug"]),
                )
            conn.commit()
            conn.close()

            await harness.db.init()  # the next boot syncs the list

            children = [
                d for d in await scoped.list_all_directions() if d.parent_id == root.id
            ]
            active = [d for d in children if d.is_active]
            assert sorted(d.canonical for d in active) == sorted(
                c["canonical"] for c in SPL_AUTOSOUND_CHILDREN
            )
        finally:
            await harness.stop()

    asyncio.run(run())


def test_a_crashing_handler_still_answers_and_the_bot_keeps_working(monkeypatch=None):
    """A handler exception must never look like a frozen bot.

    aiogram only logs a raised exception in polling mode — the participant sees
    nothing at all.  The dispatcher error handler answers the callback with an
    alert and, for private chats, sends a short technical-error note; the next
    update is processed normally.
    """
    from bot.handlers import registration

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)

            async def boom(*args, **kwargs):
                raise RuntimeError("simulated handler crash")

            original = registration._accept_direction
            registration._accept_direction = boom
            try:
                # Tap a real direction so the crashed helper is reached.
                await _pick_only(harness, "SQ")
            finally:
                registration._accept_direction = original

            alerts = [
                m for m in harness.session.methods_named("AnswerCallbackQuery")
                if m.text and "ошибк" in m.text
            ]
            assert alerts, "the callback query was left unanswered"

            # The dispatcher survived: a new /start is handled normally.
            before = len(harness.private_texts(USER))
            await harness.send_command(USER, "/start")
            assert len(harness.private_texts(USER)) > before
        finally:
            await harness.stop()

    asyncio.run(run())


def test_a_crashing_message_handler_answers_the_participant():
    """The same guarantee for plain messages: never leave the chat silent."""
    from bot.handlers import registration

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")

            async def boom(*args, **kwargs):
                raise RuntimeError("simulated photo failure")

            original = registration._after_side_photo
            registration._after_side_photo = boom
            try:
                await harness.send_photo(USER, "photo-1")
            finally:
                registration._after_side_photo = original

            assert any("ошибк" in t for t in harness.private_texts(USER)), "no answer at all"

            # The flow is still alive after the failure.
            await harness.send_photo(USER, "photo-2")
            assert "3 из 4" in harness.private_texts(USER)[-1]
        finally:
            await harness.stop()

    asyncio.run(run())


def test_global_buttons_still_work_in_the_middle_of_the_form():
    """The safety net must not swallow /mynumber or the status keyboard button.

    The reply keyboard of an earlier session stays on screen while the form is
    open, so participants do tap "Узнать свой номер" mid-registration.  The
    answer must continue the form — answering "у вас нет заявки, нажмите /start"
    made testers fill everything in again and looked like a lost registration.
    """
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await harness.send_text(USER, "Узнать свой номер")
            answers = harness.private_texts(USER)[-2:]
            assert "процессе регистрации" in answers[0], answers
            # …and the current step (choosing a direction) is re-asked.
            assert any("направление" in a for a in answers), answers
            assert not any("нет заявки" in a for a in answers)
        finally:
            await harness.stop()

    asyncio.run(run())


def test_status_button_without_any_application_still_answers_the_status_text():
    """Outside the form the plain "no application" answer is kept."""
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            # No form is open: the plain status answer is still the right one.
            await harness.send_text(USER, "Узнать свой номер")
            last = harness.private_texts(USER)[-1]
            assert "нет заявки" in last, last
        finally:
            await harness.stop()

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover - manual run helper
    for name, value in sorted(list(globals().items())):
        if name.startswith("test_") and callable(value):
            value()
            print(f"ok  {name}")

def test_start_in_the_middle_of_the_form_offers_continue_or_restart():
    """``/start`` mid-form must not silently wipe the collected answers.

    The client tapped /start (an old answer had told him to) after all four
    photos and had to fill the whole form in again — "заново опять всё делает".
    """
    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            await _register_until_directions(harness)
            await _pick_only(harness, "SQ")
            await harness.send_photo(USER, "left")
            await harness.send_photo(USER, "right")

            await harness.send_command(USER, "/start")
            assert "уже начали регистрацию" in harness.private_texts(USER)[-1]
            keys = [
                button.callback_data
                for row in await _direction_keyboard(harness)
                for button in row
            ]
            assert "flow:continue" in keys and "flow:restart" in keys, keys

            # Continuing keeps the two photos and repeats the current step.
            await harness.tap(USER, "flow:continue")
            assert "3 из 4" in harness.private_texts(USER)[-1]

            # Restarting really starts over.
            await harness.send_command(USER, "/start")
            await harness.tap(USER, "flow:restart")
            assert "Выберите страну" in harness.private_texts(USER)[-1]
        finally:
            await harness.stop()

    asyncio.run(run())


def test_missing_application_is_logged_with_the_other_tenants(caplog):
    """A "нет заявки" answer must leave a diagnosable trace in the log.

    The client finished the form and was then told he had no application.  The
    handler cannot know which bot the record went to, but the log now names the
    tenants that do hold rows for that person, so the next such report can be
    resolved from the log instead of guessed at.
    """
    import logging

    async def run():
        harness = BotHarness()
        await harness.start()
        try:
            default_tenant = await harness.db.get_tenant("promotors")
            assert default_tenant is not None, "the bootstrap tenant is missing"
            await harness.db.create_application(
                tenant_id=default_tenant.id,
                user_id=USER,
                username="@tester",
                country="Узбекистан",
                plate="01A000AA",
                direction="SQ",
                phone="+998900000000",
                photo_file_ids=[],
                photo_paths=[],
            )
            with caplog.at_level(logging.WARNING):
                await harness.send_text(USER, "Узнать свой номер")

            assert "нет заявки" in harness.private_texts(USER)[-1]
            messages = [record.getMessage() for record in caplog.records]
            assert any("promotors" in m for m in messages), messages
        finally:
            await harness.stop()

    asyncio.run(run())
