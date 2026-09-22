"""'Узнать свой номер' / 'Raqamimni bilish' button and /mynumber command.

The answer is always built from the tenant's own config.  The legacy templates
(``texts.T(lang).APPROVED``) contain Promotors Show's September dates and its
channel link, so they are never used once a tenant config is available — that
mix-up is what made a SPL Show participant receive "11 сентября" after
``/start``.

Two production lessons are encoded here:

* the reply keyboard from an earlier session stays on screen while the form is
  being filled, so a participant (or a tester) taps "Узнать свой номер"
  *mid-registration*.  The old answer — "у вас нет заявки, нажмите /start" —
  made them start the whole form again, which looked exactly like the bot
  losing the registration.  Mid-form the handler now continues the form;
* when nothing is found for this bot, the fact is logged together with the
  other tenants that *do* hold rows for that person, so such a report can be
  diagnosed from the log instead of guessed at.
"""
from __future__ import annotations

import logging
from typing import Optional

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from .. import keyboards, texts
from ..config import Config, TenantConfig
from ..db import Application, Database, STATUS_APPROVED, STATUS_PENDING

logger = logging.getLogger(__name__)
router = Router(name="mynumber")


async def show_status(
    message: Message,
    app: Application,
    config: Config | TenantConfig | None = None,
) -> None:
    """Render the applicant's current status in their chosen language, tenant-branded."""
    lang = app.language or "ru"
    t = texts.T(lang)
    if config is not None:
        if app.status == STATUS_APPROVED and app.reg_number is not None:
            text = texts.approved_for_tenant(lang, config, app.reg_number)
        elif app.status == STATUS_PENDING:
            text = t.STATUS_PENDING
        else:
            text = texts.rejected_for_tenant(lang, config)
    else:
        # No tenant context (legacy callers only).  Answer with the bare status
        # rather than another event's dates and channel link.
        if app.status == STATUS_APPROVED and app.reg_number is not None:
            text = t.STATUS_APPROVED.format(number=app.reg_number)
        elif app.status == STATUS_PENDING:
            text = t.STATUS_PENDING
        else:
            text = t.STATUS_REJECTED
    await message.answer(text, reply_markup=keyboards.main_menu_keyboard(lang))


async def _log_missing_application(db: Database, user_id: int, config: Config | TenantConfig) -> None:
    """Say in the log whether the person's rows landed under another tenant."""
    slug = getattr(config, "tenant_slug", "?")
    counter = getattr(db, "other_tenants_for_user", None)
    others: dict[str, int] = {}
    if counter is not None:
        try:
            others = await counter(user_id)
        except Exception:  # noqa: BLE001 - diagnostics must not break the answer
            logger.debug("Could not run the cross-tenant diagnostic", exc_info=True)
    if others:
        logger.warning(
            "[%s] user %s has no application for this bot, but rows exist under %s "
            "— check the tenant of those applications",
            slug,
            user_id,
            others,
        )
    else:
        logger.info("[%s] user %s asked for their number: no application yet", slug, user_id)


async def _answer_mid_form(
    message: Message,
    state: FSMContext,
    config: Config | TenantConfig,
    db: Database,
    lang: str,
) -> bool:
    """Continue the form when the button is tapped in the middle of it."""
    if state is None or await state.get_state() is None:
        return False
    # Imported lazily: the registration module imports this one for show_status().
    from .registration import _repeat_step

    await message.answer(texts.T(lang).STILL_IN_FORM)
    if await _repeat_step(message, state, lang, config, db):
        return True
    return False


@router.message(F.text.in_(texts.MY_NUMBER_LABELS))
@router.message(Command("mynumber"))
async def my_number(
    message: Message,
    state: FSMContext,
    db: Database,
    config: Config | TenantConfig,
) -> None:
    await db.touch_user(message.from_user.id)
    app: Optional[Application] = await db.get_latest_for_user(message.from_user.id)
    if app is None:
        data = await state.get_data() if state is not None else {}
        lang = data.get("lang", "ru")
        if await _answer_mid_form(message, state, config, db, lang):
            return
        await _log_missing_application(db, message.from_user.id, config)
        await message.answer(texts.STATUS_NONE)
        return
    await show_status(message, app, config)


def create_router() -> Router:
    fresh = Router(name="mynumber")
    fresh.message.register(my_number, F.text.in_(texts.MY_NUMBER_LABELS))
    fresh.message.register(my_number, Command("mynumber"))
    return fresh
