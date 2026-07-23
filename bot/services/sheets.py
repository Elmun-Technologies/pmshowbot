"""Append approved applications to a Google Sheet.

Photos are written as ``=IMAGE("url")`` formulas so they render inline in the
cell. Sync gspread calls are wrapped with ``asyncio.to_thread``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import gspread

from .google_auth import get_credentials

if TYPE_CHECKING:
    from ..config import Config
    from ..db import Application

logger = logging.getLogger(__name__)

HEADER = [
    "Дата/время",
    "№",
    "Страна",
    "Гос. номер",
    "Направление",
    "Телефон",
    "Пользователь",
    "Фото (левая)",
    "Фото (правая)",
    "Фото (передняя)",
    "Фото (задняя)",
]


def _open_worksheet(credentials_file: str, spreadsheet_id: str):
    client = gspread.authorize(get_credentials(credentials_file))
    spreadsheet = client.open_by_key(spreadsheet_id)
    worksheet = spreadsheet.sheet1
    # Ensure a header row exists (only on an empty sheet).
    first_cell = worksheet.acell("A1").value
    if not first_cell:
        # gspread 6.x signature is update(values, range_name).
        worksheet.update([HEADER], "A1")
    return worksheet


def _image_cell(url: str) -> str:
    if not url:
        return ""
    return f'=IMAGE("{url}")'


def _append_application(
    credentials_file: str,
    spreadsheet_id: str,
    app: "Application",
    photo_urls: list[str],
) -> None:
    worksheet = _open_worksheet(credentials_file, spreadsheet_id)
    # Pad/trim to exactly 4 photo cells.
    urls = (list(photo_urls) + ["", "", "", ""])[:4]
    row = [
        app.processed_at or app.created_at,
        app.reg_number if app.reg_number is not None else "",
        app.country,
        app.plate,
        app.direction,
        app.phone,
        app.username,
        *[_image_cell(u) for u in urls],
    ]
    worksheet.append_row(row, value_input_option="USER_ENTERED")


async def append_application(
    config: "Config",
    app: "Application",
    photo_urls: list[str],
) -> None:
    """Append one approved application row. Raises on failure (caller logs)."""
    await asyncio.to_thread(
        _append_application,
        config.google_credentials_file,
        config.spreadsheet_id,
        app,
        photo_urls,
    )


def smoke_test(config: "Config") -> str:
    """Open the spreadsheet with the service account and return its title.

    Used by ``python -m bot.config --check``. Runs synchronously.
    """
    client = gspread.authorize(get_credentials(config.google_credentials_file))
    spreadsheet = client.open_by_key(config.spreadsheet_id)
    return spreadsheet.title
