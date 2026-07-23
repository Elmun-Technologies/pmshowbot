"""'Узнать свой номер' / 'Raqamimni bilish' button and /mynumber command."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from .. import keyboards, texts
from ..db import Application, Database, STATUS_APPROVED, STATUS_PENDING

router = Router(name="mynumber")


async def show_status(message: Message, app: Application) -> None:
    """Render the applicant's current status in their chosen language."""
    lang = app.language or "ru"
    t = texts.T(lang)
    if app.status == STATUS_APPROVED and app.reg_number is not None:
        text = t.APPROVED.format(number=app.reg_number)
    elif app.status == STATUS_PENDING:
        text = t.STATUS_PENDING
    else:  # rejected
        text = t.REJECTED
    await message.answer(text, reply_markup=keyboards.main_menu_keyboard(lang))


@router.message(F.text.in_(texts.MY_NUMBER_LABELS))
@router.message(Command("mynumber"))
async def my_number(message: Message, db: Database) -> None:
    app = await db.get_latest_for_user(message.from_user.id)
    if app is None:
        await message.answer(texts.STATUS_NONE)
        return
    await show_status(message, app)
