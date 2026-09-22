"""'Узнать свой номер' / 'Raqamimni bilish' button and /mynumber command.

Tenant-branded: uses approved_for_tenant / rejected_for_tenant when config available.
"""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from .. import keyboards, texts
from ..config import Config, TenantConfig
from ..db import Application, Database, STATUS_APPROVED, STATUS_PENDING

router = Router(name="mynumber")


async def show_status(message: Message, app: Application, config: Config | TenantConfig | None = None) -> None:
    """Render the applicant's current status in their chosen language, tenant-branded."""
    lang = app.language or "ru"
    if config is not None:
        if app.status == STATUS_APPROVED and app.reg_number is not None:
            text = texts.approved_for_tenant(lang, config, app.reg_number)
        elif app.status == STATUS_PENDING:
            text = texts.T(lang).STATUS_PENDING
        else:
            text = texts.rejected_for_tenant(lang, config)
    else:
        t = texts.T(lang)
        if app.status == STATUS_APPROVED and app.reg_number is not None:
            text = t.APPROVED.format(number=app.reg_number)
        elif app.status == STATUS_PENDING:
            text = t.STATUS_PENDING
        else:
            text = t.REJECTED
    await message.answer(text, reply_markup=keyboards.main_menu_keyboard(lang))


@router.message(F.text.in_(texts.MY_NUMBER_LABELS))
@router.message(Command("mynumber"))
async def my_number(message: Message, db: Database, config: Config | TenantConfig) -> None:
    await db.touch_user(message.from_user.id)
    app = await db.get_latest_for_user(message.from_user.id)
    if app is None:
        await message.answer(texts.STATUS_NONE)
        return
    await show_status(message, app, config)


def create_router() -> Router:
    fresh = Router(name="mynumber")
    fresh.message.register(my_number, F.text.in_(texts.MY_NUMBER_LABELS))
    fresh.message.register(my_number, Command("mynumber"))
    return fresh
