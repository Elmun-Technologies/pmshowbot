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
from typing import Any, Awaitable, Optional

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
from ..executors import run_render
from . import drive, sheets, subscription
from .ticket import generate_ticket, ticket_as_jpeg

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Background work
# ---------------------------------------------------------------------------
#
# Approving from the Telegram group used to be one long await: number, notify,
# render, upload, share hint — and only then the answer to the moderator's tap.
# Telegram keeps a callback "spinning" for those seconds, so the moderator
# tapped Accept again and the second tap was answered with «Эта заявка уже
# обработана» while the first one was still delivering.  The decision (a
# millisecond SQLite write) is now answered immediately and the delivery runs
# here, in a task that is kept referenced until it finishes.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


def spawn(coro: Awaitable[Any]) -> asyncio.Task:
    """Run ``coro`` in the background, keeping a strong reference to the task.

    A bare ``asyncio.create_task`` may be garbage-collected mid-flight, which
    would lose a ticket with no trace in the log.
    """
    task = asyncio.ensure_future(coro)
    _BACKGROUND_TASKS.add(task)

    def _done(finished: asyncio.Task) -> None:
        _BACKGROUND_TASKS.discard(finished)
        if finished.cancelled():
            return
        error = finished.exception()
        if error is not None:  # a delivery must never die silently
            logger.error(
                "Background delivery failed: %s: %s",
                type(error).__name__,
                error,
                exc_info=error,
            )

    task.add_done_callback(_done)
    return task


def background_tasks() -> list[asyncio.Task]:
    """Currently running background deliveries (tests and diagnostics)."""
    return [task for task in _BACKGROUND_TASKS if not task.done()]


async def wait_background(timeout: float = 30.0) -> None:
    """Wait for every spawned delivery to finish. Never raises."""
    while True:
        pending = background_tasks()
        if not pending:
            return
        done, still = await asyncio.wait(pending, timeout=timeout)
        if not done:
            logger.warning("%d background task(s) did not finish in %.0fs", len(still), timeout)
            return


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

# Above this size the PNG poster is not worth uploading: Telegram re-encodes
# every photo it accepts, so a quality-88 JPEG copy is visually identical and
# several times smaller — the difference between a participant waiting 20+
# seconds on a phone uplink and getting the ticket in a couple of seconds.
# (Measured on a realistic 3024x4032 hero photo: 2.77 MB PNG vs 0.5 MB JPEG.)
_JPEG_FIRST_BYTES = 1_200_000


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
    """Render the ticket in a worker thread, with a timeout.

    Runs on the *render* pool, not the general slow-work pool: a queue of Drive
    uploads must not be able to make an approval look like "the ticket was never
    delivered" (the render would time out while it only ever waited in line).
    """
    return await asyncio.wait_for(
        run_render(
            generate_ticket,
            _tenant_scope(config),
            number=app.reg_number,
            plate=app.plate,
            direction=texts.ticket_direction(app.direction, app.language),
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
    # A poster over the photo limit would be rejected by Telegram anyway, and a
    # photographic poster is several megabytes of PNG: upload the compact JPEG
    # copy first (Telegram stores photos as JPEG regardless), keeping the PNG as
    # the fallback transport.
    if len(png) > _MAX_PHOTO_BYTES:
        transports = ("photo_jpeg", "document")
    elif len(png) > _JPEG_FIRST_BYTES:
        transports = ("photo_jpeg", "photo", "document")
    else:
        transports = ("photo", "photo_jpeg", "document")

    for kind in transports:
        for attempt in (1, 2):
            try:
                if kind == "photo":
                    await bot.send_photo(
                        app.user_id, BufferedInputFile(png, filename=png_name)
                    )
                elif kind == "photo_jpeg":
                    if jpeg is None:
                        # Pillow is CPU work: never re-encode on the event loop.
                        jpeg = await run_render(ticket_as_jpeg, png)
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


async def notify_applicant(
    bot: Optional[Bot], user_id: int, text: str, lang: str = "ru"
) -> bool:
    """Send the participant their decision text; ``False`` when it cannot arrive.

    The return value matters: a participant whose chat we cannot reach (bot
    blocked, account deleted) still appears as "processed" in the panel, so the
    team has to be told in the moderation chat instead of finding it only in a
    log line.
    """
    if bot is None:
        return False
    try:
        await bot.send_message(user_id, text, reply_markup=keyboards.main_menu_keyboard(lang))
        return True
    except TelegramForbiddenError:
        logger.warning("Could not notify user %s (bot blocked?)", user_id)
        return False
    except Exception:
        logger.exception("Failed to notify user %s", user_id)
        return False


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


async def claim_approval(
    db: Database, app_id: int, moderator: str
) -> Optional[Application]:
    """Assign the registration number and return the application.

    This is the whole *decision*: one ``BEGIN IMMEDIATE`` write, a few
    milliseconds.  Everything the participant sees (notification, ticket,
    Google export) is :func:`deliver_approval` and can run behind an already
    answered button.  ``None`` means the application was not pending — the
    caller should say which decision it already has, not just "already
    processed".
    """
    number = await db.approve(app_id, moderator)
    if number is None:
        return None
    return await db.get_application(app_id)


async def claim_rejection(
    db: Database, app_id: int, moderator: str
) -> Optional[Application]:
    """Reject an application (the decision only — see :func:`claim_approval`)."""
    ok = await db.reject(app_id, moderator)
    if not ok:
        return None
    return await db.get_application(app_id)


async def deliver_approval(
    bot: Optional[Bot],
    config: Config | TenantConfig,
    app: Application,
    *,
    moderator: str = "",
    announce_in_chat: bool = False,
) -> TicketResult:
    """Notify the approved participant, send the ticket, export to Google.

    Never raises: the decision is already stored, so a failure here is a
    delivery problem that is reported (moderation chat / log) rather than a lost
    approval.  Returns the ticket result so the caller can tell the group
    whether the participant really received their ticket.
    """
    # Tenant-branded approved text
    approved_text = texts.approved_for_tenant(
        app.language,
        config,
        app.reg_number,
        name=_get_display_name(app),
        plate=app.plate,
        direction=app.direction,
    )
    await notify_applicant(bot, app.user_id, approved_text, app.language)
    result = await send_ticket(bot, config, app)
    if announce_in_chat:
        await announce_decision_to_chat(
            bot,
            config,
            app,
            status=STATUS_APPROVED,
            number=app.reg_number,
            moderator=moderator,
        )
    spawn(export_to_google(config, app))
    return result


async def deliver_rejection(
    bot: Optional[Bot],
    config: Config | TenantConfig,
    app: Application,
    *,
    moderator: str = "",
    announce_in_chat: bool = False,
) -> bool:
    """Notify a rejected participant (and the panel chat when asked).

    ``False`` when the participant could not be reached — the moderation chat is
    told, because "the bot never answered me" reports usually start there.
    """
    rejected_text = texts.rejected_for_tenant(
        app.language, config, name=_get_display_name(app), plate=app.plate, direction=app.direction
    )
    delivered = await notify_applicant(bot, app.user_id, rejected_text, app.language)
    if not delivered:
        chat_id = getattr(config, "admin_chat_id", 0)
        if chat_id and bot is not None:
            try:
                await bot.send_message(
                    chat_id,
                    texts.MODERATION_UNREACHABLE.format(
                        number=app.reg_number if app.reg_number is not None else "—",
                        user=_applicant_label(app),
                    ),
                )
            except Exception:  # noqa: BLE001 - reporting must never break the decision
                logger.debug("Could not report the unreachable participant", exc_info=True)
    if announce_in_chat:
        await announce_decision_to_chat(
            bot, config, app, status=STATUS_REJECTED, moderator=moderator
        )
    return delivered


async def approve_application(
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
    app_id: int,
    moderator: str,
    *,
    announce_in_chat: bool = False,
) -> Optional[int]:
    """Approve an application and deliver it. Returns the number, or None.

    Kept as the single call for callers that want the old synchronous
    behaviour (the web panel's own tests, the diagnostics).  Interactive callers
    answer the moderator first and use :func:`claim_approval` plus
    :func:`deliver_approval` instead.
    """
    app = await claim_approval(db, app_id, moderator)
    if app is None:
        return None
    await deliver_approval(
        bot, config, app, moderator=moderator, announce_in_chat=announce_in_chat
    )
    return app.reg_number


async def reject_application(
    bot: Bot,
    config: Config | TenantConfig,
    db: Database,
    app_id: int,
    moderator: str,
    *,
    announce_in_chat: bool = False,
) -> bool:
    """Reject an application and notify it. True if it was pending."""
    app = await claim_rejection(db, app_id, moderator)
    if app is None:
        return False
    await deliver_rejection(
        bot, config, app, moderator=moderator, announce_in_chat=announce_in_chat
    )
    return True


def already_processed_text(app: Optional[Application], app_id: int = 0) -> str:
    """Say *what* an application's decision was, not just that there is one.

    «Эта заявка уже обработана» is what made a slow approval look like a lost
    one: the moderator tapped Accept, the answer took seconds, they tapped again
    and only saw that bare line.  With the number and the moderator in it, the
    second tap confirms the first one worked.
    """
    if app is None:
        return texts.MODERATION_ALREADY
    moderator = (app.processed_by or "").strip()
    if app.status == STATUS_APPROVED:
        return texts.MODERATION_ALREADY_APPROVED.format(
            number=app.reg_number if app.reg_number is not None else "—",
            moderator=moderator or "—",
            app_id=app.id or app_id,
        )
    if app.status == STATUS_REJECTED:
        return texts.MODERATION_ALREADY_REJECTED.format(moderator=moderator or "—")
    return texts.MODERATION_ALREADY


async def claim_status(
    db: Database, app_id: int, status: str, moderator: str
) -> Optional[Application]:
    """Force a status (the decision only). ``None`` when it could not be stored."""
    if status not in (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED):
        return None
    ok = await db.set_status(app_id, status, moderator)
    if not ok:
        return None
    return await db.get_application(app_id)


async def deliver_status(
    bot: Optional[Bot],
    config: Config | TenantConfig,
    app: Application,
    status: str,
    *,
    moderator: str = "",
    announce_in_chat: bool = False,
) -> None:
    """Deliver an overridden status: notify the participant, ticket when approved."""
    if status == STATUS_APPROVED:
        await deliver_approval(
            bot, config, app, moderator=moderator, announce_in_chat=announce_in_chat
        )
    elif status == STATUS_REJECTED:
        await deliver_rejection(
            bot, config, app, moderator=moderator, announce_in_chat=announce_in_chat
        )
    elif announce_in_chat:
        await announce_decision_to_chat(
            bot,
            config,
            app,
            status=status,
            number=app.reg_number,
            moderator=moderator,
        )


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
    and exports to Google — same as normal approve/reject flow.  Interactive
    callers use :func:`claim_status` + :func:`deliver_status` instead, so the
    panel does not wait for the upload.
    """
    app = await claim_status(db, app_id, status, moderator)
    if app is None:
        return False
    await deliver_status(
        bot, config, app, status, moderator=moderator, announce_in_chat=announce_in_chat
    )
    return True
