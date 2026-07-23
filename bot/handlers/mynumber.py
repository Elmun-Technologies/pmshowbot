"""'Узнать свой номер' button and /mynumber command."""
from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from .. import keyboards, texts
from ..db import Application, Database, STATUS_APPROVED, STATUS_PENDING

router = Router(name="mynumber")


async def show_status(message: Message, app: Application) -> None:
    """Render the applicant's current status."""
    if app.status == STATUS_APPROVED and app.reg_number is not None:
        text = texts.APPROVED.format(number=app.reg_number)
    elif app.status == STATUS_PENDING:
        text = texts.STATUS_PENDING
    else:  # rejected
        text = texts.REJECTED
    await message.answer(text, reply_markup=keyboards.main_menu_keyboard())


@router.message(F.text == texts.BTN_MY_NUMBER)
@router.message(Command("mynumber"))
async def my_number(message: Message, db: Database) -> None:
    app = await db.get_latest_for_user(message.from_user.id)
    if app is None:
        await message.answer(texts.STATUS_NONE)
        return
    await show_status(message, app)
