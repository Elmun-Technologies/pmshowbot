"""Approve / reject business logic shared by the Telegram moderation buttons
and the web admin panel.

Both entry points assign the registration number, notify the applicant over
Telegram, and (on approval) export to Google in the background. The only thing
that differs is the surrounding UI (editing the Telegram card vs an HTTP
redirect), which stays in the respective callers.

Tenant-branding: uses TenantConfig for channel_url, event dates/venue,
tenant_name for ticket wordmark, and tenant-branded texts.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

from aiogram import Bot
from aiogram.exceptions import (
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import BufferedInputFile

from .. import keyboards, texts
from ..config import Config, TenantConfig
from ..constants import SIDES, SIDE_LABELS_TRANSLIT
from ..db import (
    Application,
    Database,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
)
from . import drive, sheets, subscription
from .ticket import generate_ticket, ticket_as_jpeg

logger = logging.getLogger(__name__)


def _pick_hero(photo_paths: list[str]) -> Optional[str]:
    """Choose the poster background: prefer the front shot, then back, then any."""
    for idx in (2, 3, 0, 1):
        if idx < len(photo_paths):
            p = photo_paths[idx]
            if p and os.path.exists(p):
                return p
    return None


def _get_display_name(app: Application) -> str:
    if getattr(app, "full_name", "") and app.full_name.strip():
        return app.full_name.strip()
    if app.username:
        u = app.username.split(" (id ")[0].strip()
        if u.startswith("@"):
            u = u[1:]
        return u
    return ""


_UNSET = object()

# Telegram rejects photos above 10 MB; re-encode well before that.
_MAX_PHOTO_BYTES = 9 * 1024 * 1024
# Rendering a ticket over a participant's 4-5 MB phone photo takes seconds; a
# stuck render must not hold the approval handler (and the group card) forever.
_RENDER_TIMEOUT_SECONDS = 90.0


@dataclass
class TicketResult:
    """Outcome of one ticket delivery — ``bool(result)`` is True when sent."""

    ok: bool
    error: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - trivial
        return self.ok


def _tenant_scope(config: Any) -> Optional[str]:
    return getattr(config, "asset_scope", None) or getattr(config, "tenant_slug", None)


def _applicant_label(app: Application) -> str:
    return _get_display_name(app) or f"id {app.user_id}"


async def _render_ticket(
    config: Config | TenantConfig, app: Application, hero_path: Optional[str]
) -> bytes:
    """Render the ticket in a worker thread, with a timeout."""
    return await asyncio.wait_for(
        asyncio.to_thread(
            generate_ticket,
            _tenant_scope(config),
            number=app.reg_number,
            plate=app.plate,
            direction=texts.localize_direction(app.direction, app.language),
            name=_get_display_name(app),
            tenant_name=getattr(config, "tenant_name", ""),
            lang=app.language,
            hero_image_path=hero_path,
            tenant_config=config,
        ),
        timeout=_RENDER_TIMEOUT_SECONDS,
    )


async def _report_ticket_failure(
    bot: Optional[Bot],
    config: Config | TenantConfig,
    app: Application,
    error: str,
    *,
    image: Optional[bytes] = None,
) -> None:
    """Tell the moderation chat that a ticket could not be delivered.

    The team can forward the very same image to the participant by hand, which
    is what they had to do (blindly) whenever the automatic send failed.
    """
    chat_id = getattr(config, "admin_chat_id", 0)
    logger.error("Ticket for application %s was not delivered: %s", app.id, error)
    if not chat_id or bot is None:
        return
    label = _applicant_label(app)
    number = app.reg_number if app.reg_number is not None else "—"
    try:
        if image:
            await bot.send_photo(
                chat_id,
                BufferedInputFile(image, filename=f"ticket_{number}.png"),
                caption=texts.TICKET_FALLBACK_ADMIN.format(
                    number=number, plate=app.plate or "—", user=label
                ),
            )
        else:
            await bot.send_message(
                chat_id,
                texts.TICKET_FAILED_ADMIN.format(
                    number=number, plate=app.plate or "—", user=label, error=error[:300]
                ),
            )
    except Exception:  # noqa: BLE001 - reporting must never break the decision
        logger.warning("Could not report the ticket failure for %s", app.id, exc_info=True)


async def send_ticket(
    bot: Optional[Bot],
    config: Config | TenantConfig,
    app: Application,
    *,
    hero_path: Any = _UNSET,
    report_failure: bool = True,
) -> TicketResult:
    """Render and send the shareable Stories ticket, then a short share message.

    Never raises. Delivery is layered, because the participant paid for a
    registration and must not silently lose the ticket:

    1. the poster is rendered from their front photo, and without it (gradient
       background) if that photo cannot be decoded;
    2. sent as PNG, retried as JPEG when Telegram refuses the photo (size or
       dimensions), and finally as a document;
    3. on any definitive failure the moderation chat receives the ticket with a
       "forward this to the participant" caption, and the error is logged.
    """
    if bot is None:
        error = "рабочий процесс бота не запущен (bot is None)"
        if report_failure:
            await _report_ticket_failure(bot, config, app, error)
        else:
            logger.error("Ticket for application %s: %s", app.id, error)
        return TicketResult(False, error)

    hero = _pick_hero(app.photo_paths) if hero_path is _UNSET else hero_path
    candidates = [hero, None] if hero else [None]

    png: Optional[bytes] = None
    error = ""
    for candidate in candidates:
        try:
            png = await _render_ticket(config, app, candidate)
            break
        except Exception as exc:  # noqa: BLE001 - retried without the hero photo
            error = f"{type(exc).__name__}: {exc}"
            logger.exception(
                "Ticket rendering for application %s failed (hero photo: %s)",
                app.id,
                bool(candidate),
            )

    if png is None:
        if report_failure:
            await _report_ticket_failure(bot, config, app, error)
        return TicketResult(False, error or "ticket rendering failed")

    png_name = f"ticket_{app.reg_number}.png"
    jpg_name = f"ticket_{app.reg_number}.jpg"
    jpeg: Optional[bytes] = None
    # A PNG over the photo limit would be rejected by Telegram anyway — send the
    # compact JPEG copy first in that case instead of wasting the upload.
    transports = (
        ("photo", "photo_jpeg", "document")
        if len(png) <= _MAX_PHOTO_BYTES
        else ("photo_jpeg", "document")
    )

    for kind in transports:
        for attempt in (1, 2):
            try:
                if kind == "photo":
                    await bot.send_photo(
                        app.user_id, BufferedInputFile(png, filename=png_name)
                    )
                elif kind == "photo_jpeg":
                    if jpeg is None:
                        jpeg = ticket_as_jpeg(png)
                    if not jpeg:
                        break  # nothing to send on this transport
                    await bot.send_photo(
                        app.user_id, BufferedInputFile(jpeg, filename=jpg_name)
                    )
                else:
                    await bot.send_document(
                        app.user_id, BufferedInputFile(png, filename=png_name)
                    )
                share_text = texts.share_cta_for_tenant(app.language, config)
                try:
                    await bot.send_message(app.user_id, share_text)
                except Exception:  # noqa: BLE001 - the ticket itself arrived
                    logger.warning(
                        "Share CTA was not delivered to %s", app.user_id, exc_info=True
                    )
                return TicketResult(True, "")
            except TelegramForbiddenError:
                error = "участник заблокировал бота или не начинал с ним чат"
                logger.warning(
                    "Ticket for application %s: participant %s is unreachable",
                    app.id,
                    app.user_id,
                )
                if report_failure:
                    await _report_ticket_failure(bot, config, app, error, image=png)
                return TicketResult(False, error)
            except TelegramRetryAfter as exc:
                # Telegram rate-limited us: wait exactly as asked, then retry once.
                if attempt == 2:
                    error = f"TelegramRetryAfter: {getattr(exc, 'retry_after', '?')}s"
                    break
                wait = min(float(getattr(exc, "retry_after", 5) or 5), 60.0)
                logger.warning(
                    "Telegram asked to wait %.0fs before the ticket of application %s",
                    wait,
                    app.id,
                )
                await asyncio.sleep(wait)
            except Exception as exc:  # noqa: BLE001 - try the next transport
                error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Ticket of application %s was rejected as %s: %s", app.id, kind, error
                )
                break

    if report_failure:
        await _report_ticket_failure(bot, config, app, error, image=png)
    return TicketResult(False, error or "ticket delivery failed")


async def notify_applicant(bot: Bot, user_id: int, text: str, lang: str = "ru") -> None:
    try:
        await bot.send_message(user_id, text, reply_markup=keyboards.main_menu_keyboard(lang))
    except TelegramForbiddenError:
        logger.warning("Could not notify user %s (bot blocked?)", user_id)
    except Exception:
        logger.exception("Failed to notify user %s", user_id)


async def export_to_google(config: Config | TenantConfig, app: Application) -> None:
    """Upload photos to Drive and append the row to Sheets. Best-effort."""
    photo_urls: list[str] = []
    mod_urls: list[str] = []
    try:
        if config.drive_enabled and app.photo_paths:
            files = [
                (path, f"{app.reg_number}_{SIDES[i]}_{SIDE_LABELS_TRANSLIT.get(SIDES[i], SIDES[i])}.jpg")
                for i, path in enumerate(app.photo_paths)
                if i < len(SIDES)
            ]
            photo_urls = await drive.upload_photos(
                config.google_credentials_file, config.drive_folder_id, files
            )
    except Exception:
        logger.exception("Drive upload failed for application %s", app.id)

    try:
        mod_paths = getattr(app, "mod_paths", []) or []
        if config.drive_enabled and mod_paths:
            files = [
                (path, f"{app.reg_number}_izmeneniya_{i + 1}.jpg")
                for i, path in enumerate(mod_paths)
            ]
            mod_urls = await drive.upload_photos(
                config.google_credentials_file, config.drive_folder_id, files
            )
    except Exception:
        logger.exception("Drive upload of modification photos failed for %s", app.id)

    try:
        if config.sheets_enabled:
            await sheets.append_application(config, app, photo_urls, mod_urls)
    except Exception:
        logger.exception("Sheets append failed for application %s", app.id)


async def announce_decision_to_chat(
    bot: Bot,
    config: Config | TenantConfig,
    app: Application,
    *,
    status: str,
    number: Optional[int] = None,
    moderator: str = "",
) -> None:
    """Mirror a decision into the moderation chat.

    Needed for decisions taken in the **web panel**: before this, the Telegram
    card kept showing "pending" with live Accept/Reject buttons, so the team in
    the group never saw that the application had already been processed (the
    reported "в группе не изменился статус").  The card's buttons are removed
    when we know which message it is, and the decision itself is posted as a
    short message the group cannot miss.
    """
    chat_id = getattr(config, "admin_chat_id", 0)
    # ``bot`` is None when the tenant's worker is not running (panel reachable
    # while polling is stopped) — nothing can be mirrored then.
    if not chat_id or bot is None:
        return

    card_id = getattr(app, "card_message_id", None)
    if card_id:
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=card_id, reply_markup=None
            )
        except Exception:  # noqa: BLE001 - the card may be too old or deleted
            logger.debug("Could not clear buttons of moderation card %s", card_id, exc_info=True)

    decision = (
        texts.MODERATION_APPROVED.format(number=number, moderator=moderator)
        if status == STATUS_APPROVED and number is not None
        else texts.MODERATION_REJECTED.format(moderator=moderator)
    )
    details = " · ".join(part for part in (app.plate, app.direction, app.username) if part)
    line = f"{texts.MODERATION_PANEL_HEADER}\n{decision}"
    if details:
        line += f"\n{details}"
    try:
        await bot.send_message(chat_id, line)
    except Exception:  # noqa: BLE001 - never break the decision itself
        logger.warning("Could not post the decision to the moderation chat", exc_info=True)


async def approve_application(
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
    app_id: int,
    moderator: str,
    *,
    announce_in_chat: bool = False,
) -> Optional[int]:
    """Approve an application. Returns the assigned number, or None if already processed."""
    number = await db.approve(app_id, moderator)
    if number is None:
        return None
    app = await db.get_application(app_id)
    # Tenant-branded approved text
    approved_text = texts.approved_for_tenant(app.language, config, number)
    await notify_applicant(bot, app.user_id, approved_text, app.language)
    await send_ticket(bot, config, app)
    if announce_in_chat:
        await announce_decision_to_chat(
            bot, config, app, status=STATUS_APPROVED, number=number, moderator=moderator
        )
    asyncio.create_task(export_to_google(config, app))
    return number


async def reject_application(
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
    app_id: int,
    moderator: str,
    *,
    announce_in_chat: bool = False,
) -> bool:
    """Reject an application. Returns True if it was pending and got rejected."""
    ok = await db.reject(app_id, moderator)
    if not ok:
        return False
    app = await db.get_application(app_id)
    rejected_text = texts.rejected_for_tenant(app.language, config)
    await notify_applicant(bot, app.user_id, rejected_text, app.language)
    if announce_in_chat:
        await announce_decision_to_chat(
            bot, config, app, status=STATUS_REJECTED, moderator=moderator
        )
    return True


async def set_status(
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
    app_id: int,
    status: str,
    moderator: str,
    *,
    announce_in_chat: bool = False,
) -> bool:
    """Admin-panel override: force an application's status regardless of current.

    Notifies the applicant and, on transition to approved, sends the ticket
    and exports to Google — same as normal approve/reject flow.
    """
    if status not in (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED):
        return False
    ok = await db.set_status(app_id, status, moderator)
    if not ok:
        return False
    app = await db.get_application(app_id)
    if status == STATUS_APPROVED:
        approved_text = texts.approved_for_tenant(app.language, config, app.reg_number)
        await notify_applicant(bot, app.user_id, approved_text, app.language)
        await send_ticket(bot, config, app)
        asyncio.create_task(export_to_google(config, app))
    elif status == STATUS_REJECTED:
        rejected_text = texts.rejected_for_tenant(app.language, config)
        await notify_applicant(bot, app.user_id, rejected_text, app.language)
    if announce_in_chat:
        await announce_decision_to_chat(
            bot,
            config,
            app,
            status=status,
            number=app.reg_number,
            moderator=moderator,
        )
    return True
