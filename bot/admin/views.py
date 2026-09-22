"""Server-rendered HTML for the admin panel (no external template engine).

Every view receives the panel locale (``"ru"`` or ``"uz"``) and pulls its
strings from :mod:`.i18n` via :func:`~.i18n.t`, so views never branch on the
language themselves.  The RU | O‘Z switcher is rendered by the layout helpers
(``_page`` / ``_super_page``) and on the login pages; it uses safe relative
``?lang=`` links produced by :func:`~.i18n.lang_switcher`.
"""
from __future__ import annotations

from html import escape
from typing import Iterable, Optional
import time

from ..constants import DIRECTIONS_CANON, SIDES
from ..db import Application, STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED
from . import i18n
from .i18n import t

_STATUS_CLASS = {
    STATUS_PENDING: "badge-pending",
    STATUS_APPROVED: "badge-approved",
    STATUS_REJECTED: "badge-rejected",
}

_CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
       background: #f4f5f7; color: #1a1a2e; }
a { color: #5b21b6; text-decoration: none; }
a:hover { text-decoration: underline; }
header { background: #4c1d95; color: #fff; padding: 14px 20px; display: flex; align-items: center;
         gap: 22px; flex-wrap: wrap; }
header .brand { font-weight: 700; font-size: 18px; }
header nav a { color: #ddd6fe; font-weight: 500; margin-right: 14px; }
header nav a.active { color: #fff; border-bottom: 2px solid #fff; padding-bottom: 3px; }
header .spacer { flex: 1; }
main { max-width: 1100px; margin: 24px auto; padding: 0 16px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; }
.card { background: #fff; border-radius: 12px; padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card .n { font-size: 30px; font-weight: 700; }
.card .l { color: #6b7280; font-size: 13px; margin-top: 4px; }
.section { background: #fff; border-radius: 12px; padding: 18px; box-shadow: 0 1px 3px rgba(0,0,0,.08);
           margin-top: 20px; }
.section h2 { margin: 0 0 12px; font-size: 16px; }
.section h3 { margin: 16px 0 8px; font-size: 14px; color: #374151; }
table { width: 100%; border-collapse: collapse; font-size: 14px; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid #eee; vertical-align: middle; }
th { color: #6b7280; font-weight: 600; font-size: 12px; text-transform: uppercase; }
.badge { display: inline-block; padding: 3px 9px; border-radius: 20px; font-size: 12px; font-weight: 600; }
.badge-pending { background: #fef3c7; color: #92400e; }
.badge-approved { background: #d1fae5; color: #065f46; }
.badge-rejected { background: #fee2e2; color: #991b1b; }
.thumb { width: 54px; height: 40px; object-fit: cover; border-radius: 6px; border: 1px solid #ddd; }
.filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 14px; }
.filters a { padding: 6px 12px; border-radius: 8px; background: #ede9fe; color: #5b21b6; font-size: 13px; }
.filters a.active { background: #5b21b6; color: #fff; }
.filters form { margin-left: auto; display: flex; gap: 6px; }
input[type=text], input[type=password], input[type=file] { padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px;
                                          font-size: 14px; }
.btn { display: inline-block; padding: 9px 16px; border-radius: 8px; border: none; cursor: pointer;
       font-size: 14px; font-weight: 600; }
.btn-primary { background: #5b21b6; color: #fff; }
.btn-approve { background: #059669; color: #fff; }
.btn-reject { background: #dc2626; color: #fff; }
.btn-ghost { background: #ede9fe; color: #5b21b6; }
.btn-small { padding: 6px 10px; font-size: 12px; }
.photos { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.photos figure { margin: 0; }
.photos img { width: 100%; border-radius: 10px; border: 1px solid #ddd; }
.photos figcaption { color: #6b7280; font-size: 13px; margin-top: 4px; }
.kv { display: grid; grid-template-columns: 160px 1fr; gap: 8px 16px; font-size: 15px; }
.kv .k { color: #6b7280; }
.actions { margin-top: 18px; display: flex; gap: 10px; }
.login-wrap { max-width: 340px; margin: 40px auto; }
.login-lang { display: flex; justify-content: center; margin: 26px 0 10px; }
.err { background: #fee2e2; color: #991b1b; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; }
.ok { background: #d1fae5; color: #065f46; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; }
.muted { color: #6b7280; }
.bar { display:flex; align-items:center; gap:8px; margin:6px 0; }
.bar .track { flex:1; height:10px; background:#ede9fe; border-radius:6px; overflow:hidden; }
.bar .fill { height:100%; background:#7c3aed; }
.lang-switch { font-size: 13px; font-weight: 700; white-space: nowrap; }
.lang-switch a { color: #5b21b6; padding-bottom: 2px; }
.lang-switch a.cur { color: #4c1d95; border-bottom: 2px solid #4c1d95; }
.lang-switch .sep { color: #c4b5fd; margin: 0 6px; }
header .lang-switch a { color: #c4b5fd; }
header .lang-switch a.cur { color: #fff; border-bottom-color: #fff; }
header .lang-switch .sep { color: #6d5aa8; }

/* Ticket assets */
.asset-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 14px; margin-top: 12px; }
.asset-card { background: #fafafa; border: 1px solid #e5e7eb; border-radius: 12px; padding: 12px; display: flex; flex-direction: column; }
.asset-card .preview { background: #fff; border-radius: 8px; padding: 8px; display: flex; align-items: center; justify-content: center; min-height: 110px; border: 1px solid #eee; }
.asset-card .preview img { max-width: 100%; max-height: 100px; object-fit: contain; }
.asset-card .meta { margin-top: 8px; font-size: 13px; }
.asset-card .meta .name { font-weight: 600; word-break: break-all; }
.asset-card .actions { margin-top: 10px; display: flex; gap: 6px; flex-wrap: wrap; }
.ticket-preview-wrap { display: flex; gap: 20px; flex-wrap: wrap; align-items: flex-start; }
.ticket-preview-img { max-width: 340px; border-radius: 16px; border: 2px solid #ddd; box-shadow: 0 4px 12px rgba(0,0,0,.12); }
.upload-box { background: #f9fafb; border: 1px dashed #c4b5fd; border-radius: 10px; padding: 14px; margin-top: 10px; }
"""


def _html_lang_attribute(lang: str) -> str:
    return i18n.normalize_lang(lang)


def _page(
    title: str,
    body: str,
    lang: str,
    active: str = "",
    nav: bool = True,
    refresh: Optional[int] = None,
) -> str:
    """Layout for the tenant-admin panel with the language switcher.

    ``refresh`` re-loads the page every N seconds; it is used while a ticket
    resend is in flight so the participant-facing outcome appears without a
    second click.
    """
    lang = i18n.normalize_lang(lang)
    switcher = i18n.lang_switcher(lang)
    if nav:
        def link(href: str, label: str, key: str) -> str:
            cls = ' class="active"' if active == key else ""
            return f'<a{cls} href="{href}">{label}</a>'

        nav_html = (
            '<header>'
            f'<span class="brand">🚗 Promotors Show — {t(lang, "nav.brand_suffix")}</span>'
            f'<nav>{link("/", t(lang, "nav.dashboard"), "home")} '
            f'{link("/applications", t(lang, "nav.applications"), "apps")} '
            f'{link("/ticket-assets", t(lang, "nav.tickets"), "ticket")} '
            f'{link("/broadcast", t(lang, "nav.broadcast"), "broadcast")} '
            f'{link("/settings", t(lang, "nav.settings"), "settings")} '
            f'{link("/export.xlsx", t(lang, "nav.export_excel"), "export")} '
            f'{link("/export.csv", t(lang, "nav.export_csv"), "export_csv")}</nav>'
            '<span class="spacer"></span>'
            f'{switcher}'
            f'<a href="/logout" style="color:#ddd6fe">{t(lang, "common.logout")}</a>'
            '</header>'
        )
        switcher_html = ""
    else:
        nav_html = ""
        switcher_html = f'<div class="login-lang">{switcher}</div>'
    refresh_tag = (
        f"<meta http-equiv='refresh' content='{int(refresh)}'>" if refresh else ""
    )
    return (
        f"<!doctype html><html lang='{_html_lang_attribute(lang)}'>"
        "<head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"{refresh_tag}"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body>{nav_html}<main>{switcher_html}{body}</main></body></html>"
    )


def login_page(lang: str, error: bool = False) -> str:
    lang = i18n.normalize_lang(lang)
    err = f'<div class="err">{t(lang, "login.wrong_password")}</div>' if error else ""
    body = (
        '<div class="login-wrap"><div class="section">'
        f'<h2>{t(lang, "login.title")}</h2>'
        f'{err}'
        '<form method="post" action="/login">'
        '<div style="margin-bottom:12px"><input type="password" name="password" '
        f'placeholder="{t(lang, "login.password_placeholder")}" style="width:100%" autofocus></div>'
        f'<button class="btn btn-primary" type="submit" style="width:100%">{t(lang, "common.login")}</button>'
        '</form></div></div>'
    )
    return _page(t(lang, "login.page_title"), body, lang, nav=False)


def panel_disabled_page(lang: str) -> str:
    lang = i18n.normalize_lang(lang)
    body = (
        '<div class="login-wrap"><div class="section">'
        f'<h2>{t(lang, "disabled.panel.title")}</h2>'
        f'<p class="muted">{t(lang, "disabled.panel.body")}</p></div></div>'
    )
    return _page(t(lang, "disabled.panel.title"), body, lang, nav=False)


def _stat_card(n, label: str) -> str:
    return f'<div class="card"><div class="n">{n}</div><div class="l">{escape(label)}</div></div>'


def _distribution(lang: str, title: str, data: dict) -> str:
    if not data:
        return ""
    total = sum(data.values()) or 1
    rows = ""
    for name, n in data.items():
        pct = round(n * 100 / total)
        rows += (
            f'<div class="bar"><div style="width:150px; font-weight:500">{escape(str(name))}</div>'
            f'<div class="track"><div class="fill" style="width:{pct}%"></div></div>'
            f'<div class="muted" style="width:70px;text-align:right">{n} ({pct}%)</div></div>'
        )
    return f'<div class="section"><h2>{escape(title)}</h2>{rows}</div>'


def dashboard_page(lang: str, stats: dict) -> str:
    lang = i18n.normalize_lang(lang)
    cards = (
        '<div class="cards">'
        + _stat_card(stats["total"], t(lang, "dash.total_apps"))
        + _stat_card(stats["pending"], t(lang, "dash.pending"))
        + _stat_card(stats["approved"], t(lang, "dash.approved"))
        + _stat_card(stats.get("approved_users", stats["approved"]), t(lang, "dash.approved_users"))
        + _stat_card(stats["rejected"], t(lang, "dash.rejected"))
        + _stat_card(f'№{stats["max_number"]}', t(lang, "dash.last_number"))
        + '</div>'
    )

    excel_btn = (
        '<div style="margin:20px 0; text-align:right">'
        '<a class="btn btn-primary" href="/export.xlsx" style="padding:10px 20px; font-size:15px">'
        f'{t(lang, "dash.download_excel")}'
        '</a> '
        '<a class="btn btn-ghost" href="/ticket-assets" style="padding:10px 20px; font-size:15px">'
        f'{t(lang, "dash.manage_tickets")}'
        '</a>'
        '</div>'
    )

    body = (
        cards
        + excel_btn
        + _distribution(lang, t(lang, "dash.by_direction"), stats.get("by_direction", {}))
        + _distribution(lang, t(lang, "dash.by_country"), stats.get("by_country", {}))
        + _distribution(lang, t(lang, "dash.by_language"), stats.get("by_language", {}))
        + _distribution(lang, t(lang, "dash.by_date"), stats.get("by_date", {}))
    )
    return _page(t(lang, "dash.page_title"), body, lang, active="home")


def _js_confirm(text: str) -> str:
    """Escape a localized string for use inside a JS confirm('...')."""
    return escape(text, quote=True).replace("'", "\\'")


def _status_badge(lang: str, status: str) -> str:
    cls = _STATUS_CLASS.get(status, "")
    if status in (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED):
        label = t(lang, f"status.{status}")
    else:
        label = status
    return f'<span class="badge {cls}">{escape(label)}</span>'


def applications_page(
    lang: str,
    apps: Iterable[Application],
    status_filter: Optional[str],
    search: str,
    notice: str = "",
) -> str:
    lang = i18n.normalize_lang(lang)
    # Keep list filters when switching the interface language.
    extra = i18n.extra_query({"status": status_filter or "", "search": search})
    switcher = i18n.lang_switcher(lang, extra)

    filters = (
        '<div class="filters">'
        + f'<a class="{ "active" if (status_filter or "all")=="all" else ""}" href="/applications">{t(lang, "apps.filter_all")}</a>'
        + f'<a class="{ "active" if status_filter==STATUS_PENDING else ""}" href="/applications?status={STATUS_PENDING}">{t(lang, "status.pending")}</a>'
        + f'<a class="{ "active" if status_filter==STATUS_APPROVED else ""}" href="/applications?status={STATUS_APPROVED}">{t(lang, "status.approved")}</a>'
        + f'<a class="{ "active" if status_filter==STATUS_REJECTED else ""}" href="/applications?status={STATUS_REJECTED}">{t(lang, "status.rejected")}</a>'
        + '<form method="get" action="/applications">'
        + f'<input type="text" name="search" placeholder="{t(lang, "apps.search_placeholder")}" value="{escape(search)}">'
        + f'<button class="btn btn-ghost" type="submit">{t(lang, "common.search")}</button>'
        + '</form></div>'
    )

    rows = ""
    apps = list(apps)
    for app in apps:
        thumb = ""
        if app.photo_paths:
            thumb = f'<img class="thumb" src="/photo/{app.id}/0" alt="">'
        number = f'№{app.reg_number}' if app.reg_number is not None else "—"
        rows += (
            "<tr>"
            f"<td>{number}</td>"
            f"<td>{thumb}</td>"
            f"<td>{escape(app.country)}</td>"
            f"<td><b>{escape(app.plate)}</b></td>"
            f"<td>{escape(app.direction)}</td>"
            f"<td>{escape(app.phone)}</td>"
            f"<td>{escape(app.username)}</td>"
            f"<td>{_status_badge(lang, app.status)}</td>"
            f'<td><a class="btn btn-ghost" href="/application/{app.id}">{t(lang, "common.open")}</a></td>'
            "</tr>"
        )
    if not rows:
        rows = (
            f'<tr><td colspan="9" class="muted" style="padding:24px;text-align:center">'
            f'{t(lang, "apps.empty")}</td></tr>'
        )

    notice_html = f'<div class="ok">{escape(notice)}</div>' if notice else ""
    table = (
        notice_html
        + '<div class="section">'
        + '<div style="display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap">'
        + f'<h2 style="margin:0">{t(lang, "apps.heading", n=len(apps))}</h2>{switcher}</div>'
        + filters
        + '<div style="overflow-x:auto"><table><thead><tr>'
        + f'<th>{t(lang, "apps.col_number")}</th>'
        + f'<th>{t(lang, "apps.col_photo")}</th>'
        + f'<th>{t(lang, "apps.col_country")}</th>'
        + f'<th>{t(lang, "apps.col_plate")}</th>'
        + f'<th>{t(lang, "apps.col_direction")}</th>'
        + f'<th>{t(lang, "apps.col_phone")}</th>'
        + f'<th>{t(lang, "apps.col_user")}</th>'
        + f'<th>{t(lang, "apps.col_status")}</th><th></th>'
        + '</tr></thead><tbody>'
        + rows
        + '</tbody></table></div></div>'
    )
    return _page(t(lang, "apps.page_title"), table, lang, active="apps")


def _individual_message_form(
    lang: str, app_id: int, sent: bool = False, error: str = ""
) -> str:
    lang = i18n.normalize_lang(lang)
    notice = ""
    if sent:
        notice = (
            '<div class="section" style="background:#ecfdf5;margin:0 0 12px">'
            f'<p>{t(lang, "msg.sent")}</p></div>'
        )
    elif error:
        notice = f'<div class="err">{escape(error)}</div>'
    return (
        f'<div class="section"><h2>{t(lang, "msg.title")}</h2>'
        + notice
        + f'<p class="muted">{t(lang, "msg.hint")}</p>'
        f'<form method="post" action="/application/{app_id}/message">'
        '<textarea name="text" maxlength="3500" rows="5" '
        'style="width:100%;padding:10px;border:1px solid #ccc;border-radius:8px;'
        'font-size:15px;font-family:inherit" '
        f'placeholder="{t(lang, "msg.placeholder")}"></textarea>'
        '<div style="margin-top:10px">'
        f'<button class="btn btn-primary" type="submit">{t(lang, "msg.send")}</button>'
        '</div></form></div>'
    )


def _status_control(
    lang: str, app_id: int, current_status: str, changed: bool = False, error: str = ""
) -> str:
    lang = i18n.normalize_lang(lang)
    notice = ""
    if changed:
        notice = (
            '<div class="section" style="background:#ecfdf5;margin:0 0 12px">'
            f'<p>{t(lang, "statusctl.changed")}</p></div>'
        )
    elif error:
        notice = f'<div class="err">{escape(error)}</div>'

    options = [
        (STATUS_APPROVED, t(lang, "statusctl.approve_btn"), "btn-approve"),
        (STATUS_REJECTED, t(lang, "statusctl.reject_btn"), "btn-reject"),
        (STATUS_PENDING, t(lang, "statusctl.pending_btn"), "btn-ghost"),
    ]
    buttons = ""
    for status, label, cls in options:
        if status == current_status:
            continue
        buttons += (
            f'<form method="post" action="/application/{app_id}/status" style="display:inline">'
            f'<input type="hidden" name="status" value="{status}">'
            f'<button class="btn {cls}" type="submit" '
            f'onclick="return confirm(\'{_js_confirm(t(lang, "statusctl.confirm"))}\')">'
            f'{label}</button></form>'
        )
    return (
        f'<div class="section"><h2>{t(lang, "statusctl.title")}</h2>'
        + notice
        + f'<p class="muted">{t(lang, "statusctl.current")}'
        + _status_badge(lang, current_status)
        + '<br>'
        + t(lang, "statusctl.notice")
        + '</p>'
        + f'<div style="display:flex;gap:10px;flex-wrap:wrap">{buttons}</div>'
        + '</div>'
    )


def _delete_application_form(lang: str, app: Application) -> str:
    """Danger zone: remove the application so the person can register again."""
    number = f'№{app.reg_number}' if app.reg_number is not None else "—"
    return (
        '<div class="section" style="border:1px solid #fecaca">'
        f'<h2>{t(lang, "delete.title")}</h2>'
        f'<p class="muted">{t(lang, "delete.hint")}</p>'
        f'<p class="muted">{t(lang, "delete.details", plate=escape(app.plate), number=number, user=escape(app.username))}</p>'
        f'<form method="post" action="/application/{app.id}/delete">'
        f'<button class="btn btn-reject" type="submit" '
        f'onclick="return confirm(\'{_js_confirm(t(lang, "delete.confirm"))}\')">'
        f'{t(lang, "delete.button")}</button></form>'
        '</div>'
    )


def _ticket_section(lang: str, app: Application, job: Optional[dict], ts: int) -> str:
    """The application's ticket block: live preview + resend.

    The preview is the participant's *actual* ticket (the same render path as
    delivery), so the team can see exactly what the person received and
    forward it by hand when the Telegram send fails.  The resend click answers
    immediately and delivers in the background; ``job`` is the server's record
    of that delivery (``sending`` → ``sent``/``failed``) and is shown here.
    Only approved applications have a ticket.
    """
    if app.status != STATUS_APPROVED or app.reg_number is None:
        return ""
    notice = ""
    if job:
        status = job.get("status")
        if status == "sending":
            notice = f'<div class="ok">{t(lang, "ticket.sending_notice")}</div>'
        elif status == "sent":
            notice = f'<div class="ok">{t(lang, "ticket.sent_notice")}</div>'
        elif status == "failed":
            # Escape before ``t()`` formats the template: the escaped error
            # cannot contain braces, so ``str.format`` cannot choke on it.
            safe_error = escape(str(job.get("error") or ""))
            notice = f'<div class="err">{t(lang, "ticket.failed_notice", error=safe_error)}</div>'
    preview_src = f"/application/{app.id}/ticket.png?ts={ts}"
    return (
        '<div class="section">'
        f'<h2>{t(lang, "ticket.resend_title")}</h2>'
        f'<p class="muted">{t(lang, "ticket.resend_hint", number=app.reg_number)}</p>'
        + notice
        + '<div class="ticket-preview-wrap">'
        f'<img class="ticket-preview-img" src="{preview_src}" alt="Ticket preview" loading="lazy">'
        '<div style="flex:1;min-width:240px">'
        f'<p class="muted" style="font-size:13px">{t(lang, "ticket.preview_hint")}</p>'
        f'<a class="btn btn-ghost btn-small" href="{preview_src}" target="_blank">'
        f'{t(lang, "ticket.preview_full_size")}</a> '
        f'<a class="btn btn-ghost btn-small" href="{preview_src}">'
        f'{t(lang, "ticket.preview_download")}</a>'
        '</div></div>'
        + f'<div style="margin-top:14px"><form method="post" action="/application/{app.id}/ticket" style="display:inline">'
        f'<button class="btn btn-primary" type="submit">{t(lang, "ticket.resend_button")}</button>'
        '</form></div></div>'
    )


def application_detail_page(
    lang: str,
    app: Application,
    msg_sent: bool = False,
    msg_error: str = "",
    status_changed: bool = False,
    status_error: str = "",
    ticket_job: Optional[dict] = None,
) -> str:
    lang = i18n.normalize_lang(lang)
    photos = ""
    for i, _ in enumerate(app.photo_paths):
        side = SIDES[i] if i < len(SIDES) else str(i + 1)
        side_label = t(lang, f"side.{side}") if side in SIDES else side
        caption = t(lang, "detail.photo_side", side=side_label)
        photos += (
            f'<figure><img src="/photo/{app.id}/{i}" alt="{escape(caption)}">'
            f'<figcaption>{escape(caption)}</figcaption></figure>'
        )
    photos_html = (
        f'<div class="photos">{photos}</div>' if photos
        else f'<p class="muted">{t(lang, "detail.photos_empty")}</p>'
    )

    mods = ""
    mod_paths = getattr(app, "mod_paths", []) or []
    for i, _ in enumerate(mod_paths):
        caption = t(lang, "detail.mod_number", n=i + 1)
        mods += (
            f'<figure><img src="/modphoto/{app.id}/{i}" alt="{escape(caption)}">'
            f'<figcaption>{escape(caption)}</figcaption></figure>'
        )
    mods_html = (
        f'<div class="photos">{mods}</div>'
        if mods
        else f'<p class="muted">{t(lang, "detail.mods_empty")}</p>'
    )

    badge_path = getattr(app, "badge_photo_path", "") or ""
    badge_html = (
        f'<div class="photos"><figure><img src="/badgephoto/{app.id}" '
        f'alt="{t(lang, "detail.badge_photo")}">'
        f'<figcaption>{t(lang, "detail.badge_photo")}</figcaption></figure></div>'
        if badge_path
        else f'<p class="muted">{t(lang, "detail.badge_empty")}</p>'
    )

    number = f'№{app.reg_number}' if app.reg_number is not None else "—"
    processed_by = escape(app.processed_by or "")
    kv = (
        '<div class="kv">'
        f'<div class="k">{t(lang, "detail.col_status")}</div><div>{_status_badge(lang, app.status)}</div>'
        f'<div class="k">{t(lang, "detail.col_reg_number")}</div><div>{number}</div>'
        f'<div class="k">{t(lang, "detail.col_country")}</div><div>{escape(app.country)}</div>'
        f'<div class="k">{t(lang, "detail.col_plate")}</div><div><b>{escape(app.plate)}</b></div>'
        f'<div class="k">{t(lang, "detail.col_direction")}</div><div>{escape(app.direction)}</div>'
        f'<div class="k">{t(lang, "detail.col_car_mods")}</div>'
        f'<div>{len(getattr(app, "mod_paths", []) or []) or "—"}</div>'
        f'<div class="k">{t(lang, "detail.col_phone")}</div><div>{escape(app.phone)}</div>'
        f'<div class="k">{t(lang, "detail.col_user")}</div><div>{escape(app.username)}</div>'
        f'<div class="k">{t(lang, "detail.col_language")}</div><div>{escape(app.language)}</div>'
        f'<div class="k">{t(lang, "detail.col_submitted")}</div><div>{escape(app.created_at)}</div>'
        + (f'<div class="k">{t(lang, "detail.col_processed")}</div>'
           f'<div>{escape(app.processed_at or "")} {processed_by}</div>' if app.processed_at else "")
        + '</div>'
    )

    actions = ""
    if app.status == STATUS_PENDING:
        actions = (
            '<div class="actions">'
            f'<form method="post" action="/application/{app.id}/approve">'
            f'<button class="btn btn-approve" type="submit">{t(lang, "detail.accept")}</button></form>'
            f'<form method="post" action="/application/{app.id}/reject">'
            f'<button class="btn btn-reject" type="submit">{t(lang, "detail.reject")}</button></form>'
            '</div>'
        )

    body = (
        f'<p><a href="/applications">{t(lang, "detail.back_to_apps")}</a></p>'
        f'<div class="section"><h2>{t(lang, "detail.heading", id=app.id)}</h2>{kv}{actions}</div>'
        f'<div class="section"><h2>{t(lang, "detail.photos")}</h2>{photos_html}</div>'
        f'<div class="section"><h2>{t(lang, "detail.mods")}</h2>{mods_html}</div>'
        f'<div class="section"><h2>{t(lang, "detail.badge_photo")}</h2>{badge_html}</div>'
        + _status_control(lang, app.id, app.status, changed=status_changed, error=status_error)
        + _individual_message_form(lang, app.id, sent=msg_sent, error=msg_error)
        + _ticket_section(lang, app, ticket_job, ts=int(time.time()))
        + _delete_application_form(lang, app)
    )
    # While the resend delivery is in flight, re-load the page until the
    # outcome (sent / failed with a readable reason) is known.
    refresh = 2 if ticket_job and ticket_job.get("status") == "sending" else None
    return _page(
        t(lang, "detail.page_title", id=app.id), body, lang, active="apps", refresh=refresh
    )


def _broadcast_textarea(
    name: str, label: str, hint: str, placeholder: str, value: str
) -> str:
    return (
        '<div style="margin:12px 0">'
        f'<label style="display:block;font-weight:600;margin-bottom:6px">{label}'
        f'<span class="muted" style="font-weight:400"> — {escape(hint)}</span></label>'
        f'<textarea name="{name}" id="{name}" maxlength="3500" rows="8" '
        'style="width:100%;padding:10px;border:1px solid #ccc;border-radius:8px;'
        'font-size:15px;font-family:inherit" '
        f'placeholder="{escape(placeholder)}">{escape(value)}</textarea>'
        "</div>"
    )


def broadcast_page(
    lang: str,
    counts: dict,
    audience: str = "approved",
    result: Optional[dict] = None,
    error: str = "",
    last_text_uz: str = "",
    last_text_ru: str = "",
    langs: Optional[list] = None,
    directions: Optional[list] = None,
    preview_count: Optional[int] = None,
) -> str:
    lang = i18n.normalize_lang(lang)
    # Nothing selected yet (first load) → don't pre-narrow the audience.
    langs = list(langs) if langs is not None else ["uz", "ru"]
    directions = list(directions) if directions is not None else list(DIRECTIONS_CANON)

    result_html = ""
    if result:
        photo_note = t(lang, "bcast.with_photo") if result.get("with_photo") else ""
        result_html = (
            '<div class="section" style="background:#ecfdf5;margin-top:0;margin-bottom:16px">'
            f"<h2>{t(lang, 'bcast.done')}</h2>"
            f'<p>{t(lang, "bcast.audience_label")}'
            f'<b>{escape(str(result.get("audience_label", "")))}</b>{escape(photo_note)}<br>'
            f'{t(lang, "bcast.sent")}<b>{result.get("ok", 0)}</b> · '
            f'{t(lang, "bcast.failures")}<b>{result.get("fail", 0)}</b> · '
            f'{t(lang, "bcast.total")}<b>{result.get("total", 0)}</b><br>'
            f'{t(lang, "bcast.sent_uz")}<b>{result.get("ok_uz", 0)}</b> · '
            f'{t(lang, "bcast.sent_ru")}<b>{result.get("ok_ru", 0)}</b></p>'
            "</div>"
        )
    elif preview_count is not None:
        result_html = (
            '<div class="section" style="background:#eff6ff;margin-top:0;margin-bottom:16px">'
            f'<p class="muted">{t(lang, "bcast.preview_count", n=preview_count)}</p>'
            "</div>"
        )
    err_html = f'<div class="err">{escape(error)}</div>' if error else ""

    options = [
        ("approved", t(lang, "bcast.audience.approved"), counts.get("approved", 0)),
        ("pending", t(lang, "bcast.audience.pending"), counts.get("pending", 0)),
        ("rejected", t(lang, "bcast.audience.rejected"), counts.get("rejected", 0)),
        ("incomplete", t(lang, "bcast.audience.incomplete"), counts.get("incomplete", 0)),
        ("all_apps", t(lang, "bcast.audience.all_apps"), counts.get("all_apps", 0)),
        ("starters", t(lang, "bcast.audience.starters"), counts.get("starters", 0)),
    ]
    radios = ""
    for key, label, n in options:
        checked = " checked" if audience == key else ""
        radios += (
            '<label style="display:flex;gap:10px;align-items:flex-start;margin:8px 0;'
            'padding:10px 12px;border:1px solid #eee;border-radius:10px;cursor:pointer">'
            f'<input type="radio" name="audience" value="{key}"{checked} style="margin-top:4px"> '
            f'<span><b>{escape(label)}</b>'
            f'<span class="muted"> — {t(lang, "bcast.people", n=n)}</span></span></label>'
        )

    lang_options = [("uz", t(lang, "bcast.only_uz")), ("ru", t(lang, "bcast.only_ru"))]
    lang_checks = ""
    for key, label in lang_options:
        checked = " checked" if key in langs else ""
        lang_checks += (
            '<label style="display:inline-flex;gap:6px;align-items:center;margin:4px 16px 4px 0">'
            f'<input type="checkbox" name="langs" value="{key}"{checked}> {escape(label)}</label>'
        )

    dir_checks = ""
    for canonical in DIRECTIONS_CANON:
        checked = " checked" if canonical in directions else ""
        dir_checks += (
            '<label style="display:inline-flex;gap:6px;align-items:center;margin:4px 16px 4px 0">'
            f'<input type="checkbox" name="directions" value="{escape(canonical)}"{checked}> '
            f'{escape(canonical)}</label>'
        )

    body = (
        result_html
        + err_html
        + '<div class="section">'
        + f"<h2>{t(lang, 'bcast.title')}</h2>"
        + f'<p class="muted">{t(lang, "bcast.intro")}</p>'
        + '<form method="post" action="/broadcast" enctype="multipart/form-data">'
        + f'<h3 style="margin:12px 0 0">{t(lang, "bcast.audience_heading")}</h3>'
        + f'<div style="margin:12px 0">{radios}</div>'
        + '<div style="margin:16px 0"><label style="display:block;font-weight:600;margin-bottom:6px">'
        + t(lang, "bcast.by_language") + "</label>" + lang_checks + "</div>"
        + '<div style="margin:16px 0"><label style="display:block;font-weight:600;margin-bottom:6px">'
        + t(lang, "bcast.by_direction") + "</label>" + dir_checks + "</div>"
        + _broadcast_textarea(
            "text_uz",
            "🇺🇿 O‘zbekcha",
            t(lang, "bcast.text_uz_hint"),
            "Xabar matni…",
            last_text_uz,
        )
        + _broadcast_textarea(
            "text_ru",
            "🇷🇺 Русский",
            t(lang, "bcast.text_ru_hint"),
            t(lang, "bcast.text_ru_placeholder"),
            last_text_ru,
        )
        + '<p class="muted" style="margin:0 0 12px;font-size:13px">'
        + t(lang, "bcast.one_field_hint")
        + "</p>"
        + _broadcast_photo_input(
            "photo_uz", "bcast-photo-uz", t(lang, "bcast.photo_uz"),
            t(lang, "bcast.photo_uz_hint"),
        )
        + _broadcast_photo_input(
            "photo_ru", "bcast-photo-ru", t(lang, "bcast.photo_ru"),
            t(lang, "bcast.photo_ru_hint"),
        )
        + '<p class="muted" style="margin:0 0 16px;font-size:13px">'
        + t(lang, "bcast.caption_limit")
        + "</p>"
        + _broadcast_preview_block(lang, last_text_uz, last_text_ru)
        + '<label class="muted" style="display:flex;gap:8px;align-items:center;margin-bottom:14px">'
        + '<input type="checkbox" name="confirm" value="1" required> '
        + t(lang, "bcast.confirm")
        + "</label>"
        + '<div style="display:flex;gap:10px">'
        + '<button class="btn btn-ghost" type="submit" name="action" value="preview">'
        + t(lang, "bcast.count_btn") + "</button>"
        + '<button class="btn btn-primary" type="submit" name="action" value="send">'
        + t(lang, "bcast.send_btn") + "</button>"
        + "</div>"
        + "</form>"
        + '<p class="muted" style="margin-top:16px;font-size:13px">'
        + t(lang, "bcast.note_incomplete") + "<br>"
        + t(lang, "bcast.note_filters")
        + "</p>"
        + "</div>"
    )
    return _page(t(lang, "bcast.page_title"), body, lang, active="broadcast")


def _broadcast_photo_input(name: str, input_id: str, label: str, hint: str) -> str:
    """File input for one broadcast language; ``label``/``hint`` pre-localized."""
    return (
        '<div style="margin:0 0 12px">'
        f'<label style="display:block;font-weight:600;margin-bottom:6px">📎 {escape(label)} '
        f'<span class="muted" style="font-weight:400"> — {escape(hint)}</span></label>'
        f'<input type="file" name="{name}" id="{input_id}" accept="image/*">'
        "</div>"
    )


def _broadcast_preview_block(lang: str, last_text_uz: str, last_text_ru: str) -> str:
    """A live, client-side preview of what each language's recipient will see.

    Pure JS/no round trip: each language's photo is previewed straight from
    its own file picker (FileReader) and mirrors the *other* language's photo
    when its own is empty — matching the fallback the server applies when
    actually sending — and the text mirrors the textareas on every keystroke.
    Nothing here re-uploads anything or hits the request-size limit that
    sending the form does.
    """
    lang = i18n.normalize_lang(lang)
    empty_hint_js = escape(t(lang, "bcast.preview_empty"), quote=True)

    def card(lang_label: str, preview_photo_id: str, preview_text_id: str, initial: str) -> str:
        content = escape(initial) if initial else f'<span class="muted">{empty_hint_js}</span>'
        return (
            '<div style="flex:1;min-width:220px;border:1px solid #eee;border-radius:10px;'
            'padding:12px;background:#fafafa">'
            f'<div class="muted" style="font-size:12px;margin-bottom:6px">{lang_label}</div>'
            f'<img id="{preview_photo_id}" style="display:none;max-width:100%;'
            'border-radius:8px;margin-bottom:8px">'
            f'<div id="{preview_text_id}" style="white-space:pre-wrap;font-size:14px">'
            f"{content}</div>"
            "</div>"
        )

    return (
        '<div class="section" style="margin:0 0 16px;background:#fff">'
        f'<h3 style="margin:0 0 12px;font-size:14px;color:#6b7280">{t(lang, "bcast.preview_title")}</h3>'
        '<div style="display:flex;gap:16px;flex-wrap:wrap">'
        + card("🇺🇿 O‘zbekcha", "preview-uz-photo", "preview-uz-text", last_text_uz)
        + card("🇷🇺 Русский", "preview-ru-photo", "preview-ru-text", last_text_ru)
        + "</div></div>"
        "<script>"
        "(function(){"
        "var uzInput=document.getElementById('bcast-photo-uz');"
        "var ruInput=document.getElementById('bcast-photo-ru');"
        "var uzImg=document.getElementById('preview-uz-photo');"
        "var ruImg=document.getElementById('preview-ru-photo');"
        "function setPreview(img,file){"
        "if(!img)return;"
        "if(!file){img.style.display='none';img.src='';return;}"
        "var reader=new FileReader();"
        "reader.onload=function(e){img.src=e.target.result;img.style.display='block';};"
        "reader.readAsDataURL(file);"
        "}"
        "function refreshPhotos(){"
        "var uzFile=uzInput&&uzInput.files&&uzInput.files[0];"
        "var ruFile=ruInput&&ruInput.files&&ruInput.files[0];"
        "setPreview(uzImg,uzFile||ruFile);"
        "setPreview(ruImg,ruFile||uzFile);"
        "}"
        "if(uzInput)uzInput.addEventListener('change',refreshPhotos);"
        "if(ruInput)ruInput.addEventListener('change',refreshPhotos);"
        "function bindText(taId,boxId){"
        "var ta=document.getElementById(taId),box=document.getElementById(boxId);"
        "if(!ta||!box)return;"
        "ta.addEventListener('input',function(){"
        "box.textContent=ta.value||'';"
        f"if(!ta.value){{box.innerHTML='<span class=\\\"muted\\\">{empty_hint_js}</span>';}}"
        "});"
        "}"
        "bindText('text_uz','preview-uz-text');"
        "bindText('text_ru','preview-ru-text');"
        "})();"
        "</script>"
    )


# ---------------------------------------------------------------------------
# Ticket assets management page
# ---------------------------------------------------------------------------

def ticket_assets_page(
    lang: str,
    inventory: dict,
    sponsors: list[dict],
    brand: dict,
    directions: list[dict],
    message: str = "",
    error: str = "",
    tenant_slug: str = "promotors",
) -> str:
    lang = i18n.normalize_lang(lang)
    # Flash codes arrive via query params; unknown codes fall back to a
    # localized generic message instead of raw internal text.
    msg_map = {
        "brand_uploaded": "assets.msg.brand_uploaded",
        "brand_deleted": "assets.msg.brand_deleted",
        "sponsor_uploaded": "assets.msg.sponsor_uploaded",
        "sponsor_deleted": "assets.msg.sponsor_deleted",
        "direction_uploaded": "assets.msg.direction_uploaded",
        "direction_deleted": "assets.msg.direction_deleted",
    }
    err_map = {
        "no_file": "assets.err.no_file",
        "name_required": "assets.err.name_required",
        "invalid_name": "assets.err.invalid_name",
        "unknown_brand": "assets.err.unknown_brand",
        "unknown_direction": "assets.err.unknown_direction",
    }

    notice = ""
    if message and message in msg_map:
        notice = f'<div class="ok">{t(lang, msg_map[message])}</div>'
    elif message:
        notice = (
            f'<div class="ok">{t(lang, "assets.err_generic")}: '
            f'{escape(message)}</div>'
        )
    if error:
        err_text = t(lang, err_map[error]) if error in err_map else t(lang, "assets.err_generic")
        notice += f'<div class="err">❌ {escape(err_text)}</div>'

    # Ticket preview
    ts = int(time.time())
    preview_html = (
        '<div class="section">'
        f'<h2>{t(lang, "assets.preview.title")}</h2>'
        f'<p class="muted">{t(lang, "assets.preview.hint")}</p>'
        '<div class="ticket-preview-wrap">'
        f'<img class="ticket-preview-img" src="/ticket-assets/preview.png?ts={ts}" alt="Ticket preview">'
        '<div style="flex:1;min-width:280px">'
        f'<p class="muted" style="font-size:13px">{t(lang, "assets.preview.reload_hint")}</p>'
        f'<a class="btn btn-ghost btn-small" href="/ticket-assets/preview.png" target="_blank">{t(lang, "assets.preview.full_size")}</a> '
        f'<a class="btn btn-ghost btn-small" href="/ticket-assets?ts={ts}">{t(lang, "assets.preview.refresh")}</a>'
        '</div>'
        '</div>'
        '</div>'
    )

    # Brand logos — tenant-branded titles
    is_default_tenant = tenant_slug in (None, "", "promotors")
    brand_title_key = "assets.brand.title" if is_default_tenant else "assets.brand.generic_title"
    brand_hint_key = "assets.brand.hint" if is_default_tenant else "assets.brand.generic_hint"

    brand_cards = ""
    for key in ("logo", "adrenaline"):
        info = brand.get(key, {})
        exists = info.get("exists")
        is_runtime = info.get("is_runtime")
        src = f"/assets/file/brand/{key}?ts={ts}" if exists else ""
        img_tag = (
            f'<img src="{src}" alt="{escape(key)}">' if exists
            else f'<span class="muted">{t(lang, "assets.brand.no_logo")}</span>'
        )
        status = (
            t(lang, "common.uploaded") if is_runtime
            else (t(lang, "common.from_repo") if exists else t(lang, "common.missing"))
        )
        badge = (
            f'<span class="badge badge-{"approved" if exists else "rejected"}">'
            f'{escape(status)}</span>'
        )
        if is_default_tenant:
            title_map = {"logo": "PROMOTORS SHOW (logo.png)", "adrenaline": "Adrenaline Rush (adrenaline.png)"}
            title = title_map.get(key, key)
        else:
            # Generic slot titles i18n
            title_map = {"logo": f"{tenant_slug} logo", "adrenaline": f"{tenant_slug} secondary logo"}
            title = title_map.get(key, key)

        brand_cards += (
            '<div class="asset-card">'
            f'<div class="preview">{img_tag}</div>'
            f'<div class="meta"><div class="name">{escape(title)}</div>'
            f'<div class="muted" style="font-size:12px;margin-top:4px">{t(lang, "assets.brand.key")}'
            f'<code>{escape(key)}</code> {badge}</div></div>'
            '<div class="actions">'
            '<form method="post" action="/ticket-assets/brand/upload" '
            'enctype="multipart/form-data" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;width:100%">'
            f'<input type="hidden" name="brand_name" value="{escape(key)}">'
            '<input type="file" name="file" accept="image/*" required style="flex:1;min-width:120px">'
            f'<button class="btn btn-primary btn-small" type="submit">{t(lang, "common.upload")}</button>'
            '</form>'
        )
        if is_runtime:
            brand_cards += (
                '<form method="post" action="/ticket-assets/brand/delete" style="margin-top:6px">'
                f'<input type="hidden" name="brand_name" value="{escape(key)}">'
                f'<button class="btn btn-reject btn-small" type="submit" '
                f'onclick="return confirm(\'{_js_confirm(t(lang, "assets.brand.confirm_delete"))}\')">'
                f'{t(lang, "common.delete")}</button>'
                '</form>'
            )
        brand_cards += '</div></div>'

    brand_section = (
        '<div class="section">'
        f'<h2>{t(lang, brand_title_key)}</h2>'
        f'<p class="muted">{t(lang, brand_hint_key)}</p>'
        f'<div class="asset-grid">{brand_cards}</div></div>'
    )

    # Partner checklist
    partners = inventory.get("partners", [])
    _SOURCE_KEYS = {
        "runtime": "common.uploaded",
        "bundled": "common.from_repo",
        None: "common.missing",
    }
    partner_rows = ""
    for p in partners:
        source_key = p.get("source")
        src = t(lang, _SOURCE_KEYS.get(source_key, "common.not_set"))
        icon = "✅" if source_key else "❌"
        partner_rows += (
            f'<tr><td>{icon} <code>{escape(p["name"])}</code></td>'
            f'<td>{escape(p["title"])}</td>'
            f'<td>{escape(src)}</td></tr>'
        )
    # For non-promotors tenants, partners from repo are hidden (empty) — show tenant-specific empty state
    if not partner_rows and not is_default_tenant:
        partner_table = (
            '<div class="section">'
            f'<h2>{t(lang, "assets.partners.title")}</h2>'
            f'<p class="muted">{t(lang, "assets.partners.empty_tenant")}</p>'
            '</div>'
        )
    else:
        partner_table = (
            '<div class="section">'
            f'<h2>{t(lang, "assets.partners.title")}</h2>'
            f'<p class="muted">{t(lang, "assets.partners.hint")}</p>'
            '<table><thead><tr>'
            f'<th>{t(lang, "assets.partners.col_name")}</th>'
            f'<th>{t(lang, "assets.partners.col_description")}</th>'
            f'<th>{t(lang, "assets.partners.col_status")}</th>'
            f'</tr></thead><tbody>{partner_rows}</tbody></table></div>'
        )

    # Sponsor logos grid
    sponsor_cards = ""
    for s in sponsors:
        fname = s["filename"]
        name = s["name"]
        size_kb = (
            f"{s['size']//1024} {t(lang, 'common.kb')}" if s["size"] > 1024
            else f"{s['size']} {t(lang, 'common.bytes')}"
        )
        source_note = (
            t(lang, "common.uploaded") if s["is_runtime"] else t(lang, "common.from_repo")
        )
        src = f"/assets/file/sponsors/{escape(fname)}?ts={ts}"
        sponsor_cards += (
            '<div class="asset-card">'
            f'<div class="preview"><img src="{src}" alt="{escape(name)}"></div>'
            f'<div class="meta"><div class="name">{escape(fname)}</div>'
            f'<div class="muted" style="font-size:12px">{t(lang, "assets.sponsors.name")}'
            f'<code>{escape(name)}</code><br>{size_kb} · {escape(source_note)}</div></div>'
            '<div class="actions">'
            '<form method="post" action="/ticket-assets/sponsor/delete" style="display:inline">'
            f'<input type="hidden" name="name" value="{escape(name)}">'
            '<button class="btn btn-reject btn-small" type="submit" '
            f'onclick="return confirm(\'{_js_confirm(t(lang, "assets.sponsors.confirm_delete"))}\')">'
            f'{t(lang, "common.delete")}</button>'
            '</form></div></div>'
        )
    if not sponsor_cards:
        empty_key = "assets.sponsors.empty" if is_default_tenant else "assets.sponsors.empty_tenant"
        sponsor_cards = f'<p class="muted">{t(lang, empty_key)}</p>'

    upload_sponsor_form = (
        '<div class="upload-box">'
        f'<h3>{t(lang, "assets.sponsors.add")}</h3>'
        f'<p class="muted" style="font-size:13px">{t(lang, "assets.sponsors.add_hint")}<br>'
        f'{t(lang, "assets.sponsors.add_example")}</p>'
        '<form method="post" action="/ticket-assets/sponsor/upload" enctype="multipart/form-data" '
        'style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">'
        f'<div><label style="display:block;font-weight:600;margin-bottom:4px">{t(lang, "assets.sponsors.label_name")}</label>'
        '<input type="text" name="name" placeholder="1_mcs_sherdor" required pattern="[A-Za-z0-9_-]{1,40}" '
        'style="min-width:200px"></div>'
        f'<div><label style="display:block;font-weight:600;margin-bottom:4px">{t(lang, "assets.sponsors.label_file")}</label>'
        '<input type="file" name="file" accept="image/*" required></div>'
        f'<button class="btn btn-primary" type="submit">{t(lang, "common.upload")}</button>'
        '</form></div>'
    )

    sponsors_section = (
        '<div class="section">'
        f'<h2>{t(lang, "assets.sponsors.title")}</h2>'
        f'<p class="muted">{t(lang, "assets.sponsors.hint")}</p>'
        f'<div class="asset-grid">{sponsor_cards}</div>'
        + upload_sponsor_form +
        '</div>'
    )

    # Direction banners
    dir_cards = ""
    for d in directions:
        slug = d["slug"]
        canon = d["canonical"]
        exists = d["exists"]
        is_runtime = d["is_runtime"]
        src = f"/assets/file/directions/{escape(slug)}?ts={ts}" if exists else ""
        img_tag = (
            f'<img src="{src}" alt="{escape(slug)}">' if exists
            else f'<span class="muted">{t(lang, "assets.directions.no_banner")}</span>'
        )
        status = (
            t(lang, "common.uploaded") if is_runtime
            else (t(lang, "common.from_repo") if exists else t(lang, "common.missing"))
        )
        dir_cards += (
            '<div class="asset-card">'
            f'<div class="preview">{img_tag}</div>'
            f'<div class="meta"><div class="name">{escape(canon)}</div>'
            f'<div class="muted" style="font-size:12px">slug: <code>{escape(slug)}</code> · '
            f'{escape(status)}</div></div>'
            '<div class="actions">'
            '<form method="post" action="/ticket-assets/direction/upload" '
            'enctype="multipart/form-data" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;width:100%">'
            f'<input type="hidden" name="slug" value="{escape(slug)}">'
            '<input type="file" name="file" accept="image/*" required style="flex:1;min-width:100px">'
            f'<button class="btn btn-primary btn-small" type="submit">{t(lang, "common.upload")}</button>'
            '</form>'
        )
        if is_runtime:
            dir_cards += (
                '<form method="post" action="/ticket-assets/direction/delete" style="margin-top:6px">'
                f'<input type="hidden" name="slug" value="{escape(slug)}">'
                f'<button class="btn btn-reject btn-small" type="submit" '
                f'onclick="return confirm(\'{_js_confirm(t(lang, "assets.directions.confirm_delete"))}\')">'
                f'{t(lang, "common.delete")}</button>'
                '</form>'
            )
        dir_cards += '</div></div>'

    dir_section = (
        '<div class="section">'
        f'<h2>{t(lang, "assets.directions.title")}</h2>'
        f'<p class="muted">{t(lang, "assets.directions.hint")}</p>'
        f'<div class="asset-grid">{dir_cards}</div></div>'
    )

    body = (
        notice
        + preview_html
        + brand_section
        + partner_table
        + sponsors_section
        + dir_section
        + f'<div class="section"><h2>{t(lang, "assets.how.title")}</h2>'
        '<ul style="font-size:14px;line-height:1.6">'
        + "".join(
            f"<li>{t(lang, f'assets.how.{i}')}</li>" for i in range(1, 6)
        )
        + '</ul></div>'
    )
    return _page(t(lang, "assets.page_title"), body, lang, active="ticket")


# ---------------------------------------------------------------------------
# Multi-tenant control-plane pages
# ---------------------------------------------------------------------------

def tenant_selector_login_page(lang: str, tenants, error: bool = False) -> str:
    """Render the public tenant chooser followed by that tenant's password."""
    lang = i18n.normalize_lang(lang)
    err = f'<div class="err">{t(lang, "selector.wrong_credentials")}</div>' if error else ""
    options = ''.join(
        f'<option value="{escape(t_.slug)}">{escape(t_.name)} ({escape(t_.slug)})</option>'
        for t_ in tenants
    )
    disabled = " disabled" if not options else ""
    no_tenants = (
        f'<p class="muted">{t(lang, "selector.no_tenants")}</p>' if not options else ""
    )
    body = (
        '<div class="login-wrap"><div class="section">'
        f'<h2>{t(lang, "selector.title")}</h2>' + err + no_tenants +
        '<form method="post" action="/login">'
        f'<div style="margin-bottom:12px"><label class="muted">{t(lang, "selector.project")}</label>'
        f'<select name="slug" required style="width:100%;padding:8px 10px;border:1px solid #ccc;border-radius:8px">{options}</select></div>'
        '<div style="margin-bottom:12px"><input type="password" name="password" '
        f'placeholder="{t(lang, "selector.password_placeholder")}" style="width:100%" autofocus></div>'
        f'<button class="btn btn-primary" type="submit" style="width:100%"{disabled}>{t(lang, "common.login")}</button>'
        '</form><p class="muted" style="font-size:13px;margin-top:16px">'
        '<a href="/super-admin/login">Super admin</a></p></div></div>'
    )
    return _page(t(lang, "selector.page_title"), body, lang, nav=False)


def tenant_login_page(lang: str, tenant, error: bool = False) -> str:
    """Render a password login constrained to one tenant slug."""
    lang = i18n.normalize_lang(lang)
    err = f'<div class="err">{t(lang, "tlogin.wrong_password")}</div>' if error else ""
    slug = escape(tenant.slug)
    body = (
        '<div class="login-wrap"><div class="section">'
        f'<h2>{escape(tenant.name)} — {t(lang, "tlogin.admin_panel")}</h2>'
        f'<p class="muted">{t(lang, "tlogin.tenant")}<code>{slug}</code></p>'
        + err + f'<form method="post" action="/t/{slug}/login">'
        '<div style="margin-bottom:12px"><input type="password" name="password" '
        f'placeholder="{t(lang, "tlogin.password_placeholder")}" style="width:100%" autofocus></div>'
        f'<button class="btn btn-primary" type="submit" style="width:100%">{t(lang, "common.login")}</button>'
        '</form><p class="muted" style="font-size:13px;margin-top:16px">'
        f'<a href="/login">{t(lang, "tlogin.other_tenant")}</a></p>'
        '</div></div>'
    )
    return _page(t(lang, "tlogin.page_title"), body, lang, nav=False)


def tenant_inactive_page(lang: str) -> str:
    """Explain why an archived tenant cannot issue a tenant-admin session."""
    lang = i18n.normalize_lang(lang)
    body = (
        '<div class="login-wrap"><div class="section">'
        f'<h2>{t(lang, "tenant_inactive.title")}</h2>'
        f'<p class="muted">{t(lang, "tenant_inactive.body")}</p>'
        '</div></div>'
    )
    return _page(t(lang, "tenant_inactive.title"), body, lang, nav=False)


def super_panel_disabled_page(lang: str) -> str:
    """Page shown when no process-level super-admin secret is configured."""
    lang = i18n.normalize_lang(lang)
    body = (
        '<div class="login-wrap"><div class="section">'
        f'<h2>{t(lang, "disabled.super.title")}</h2>'
        f'<p class="muted">{t(lang, "disabled.super.body")}</p>'
        '</div></div>'
    )
    return _page(t(lang, "disabled.super.title"), body, lang, nav=False)


def super_login_page(lang: str, error: bool = False) -> str:
    """Render the process-level super-admin login form."""
    lang = i18n.normalize_lang(lang)
    err = f'<div class="err">{t(lang, "superlogin.wrong_password")}</div>' if error else ""
    body = (
        '<div class="login-wrap"><div class="section">'
        f'<h2>{t(lang, "superlogin.title")}</h2>' + err +
        '<form method="post" action="/super-admin/login">'
        '<div style="margin-bottom:12px"><input type="password" name="password" '
        f'placeholder="{t(lang, "superlogin.password_placeholder")}" style="width:100%" autofocus></div>'
        f'<button class="btn btn-primary" type="submit" style="width:100%">{t(lang, "common.login")}</button>'
        '</form><p class="muted" style="font-size:13px;margin-top:16px">'
        f'<a href="/login">{t(lang, "superlogin.tenant_login")}</a></p>'
        '</div></div>'
    )
    return _page(t(lang, "superlogin.page_title"), body, lang, nav=False)


def _super_page(title: str, body: str, lang: str) -> str:
    lang = i18n.normalize_lang(lang)
    header = (
        '<header>'
        f'<span class="brand">{t(lang, "super.brand")}</span>'
        f'<nav><a href="/super-admin/">{t(lang, "super.nav.tenants")}</a> '
        f'<a href="/super-admin/tenants/new">{t(lang, "super.nav.new")}</a></nav>'
        '<span class="spacer"></span>'
        f'{i18n.lang_switcher(lang)}'
        f'<a href="/super-admin/logout" style="color:#ddd6fe">{t(lang, "common.logout")}</a></header>'
    )
    return (
        f"<!doctype html><html lang='{_html_lang_attribute(lang)}'>"
        "<head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body>{header}<main>{body}</main></body></html>"
    )


def super_dashboard_page(lang: str, tenants, application_counts: dict[int, int]) -> str:
    """Render all tenants with aggregate application counts and safe token status."""
    lang = i18n.normalize_lang(lang)
    rows = ""
    for tenant in tenants:
        active = (
            t(lang, "superdash.active") if tenant.is_active
            else t(lang, "superdash.inactive")
        )
        token = (
            t(lang, "superdash.token_configured") if tenant.token_configured
            else t(lang, "superdash.token_missing")
        )
        password = (
            t(lang, "superdash.password_configured") if tenant.password_configured
            else t(lang, "superdash.password_missing")
        )
        slug = escape(tenant.slug)
        pause_label = (
            t(lang, "superdash.pause") if tenant.is_active
            else t(lang, "superdash.enable")
        )
        confirm_archive = _js_confirm(t(lang, "superdash.confirm_archive"))
        rows += (
            '<tr>'
            f'<td><b>{escape(tenant.name)}</b><br><code>{slug}</code></td>'
            f'<td>{escape(active)}</td><td>{escape(token)}</td><td>{escape(password)}</td>'
            f'<td>{application_counts.get(tenant.id, 0)}</td>'
            '<td style="white-space:nowrap">'
            f'<a class="btn btn-ghost btn-small" href="/super-admin/tenants/{slug}/edit">{t(lang, "superdash.edit")}</a> '
            f'<a class="btn btn-ghost btn-small" href="/super-admin/tenants/{slug}/directions">{t(lang, "tenant.directions.manage")}</a> '
            f'<a class="btn btn-ghost btn-small" href="/super-admin/tenants/{slug}/diag">{t(lang, "superdash.diagnostics")}</a> '
            f'<a class="btn btn-ghost btn-small" href="/t/{slug}/">{t(lang, "common.open")}</a>'
            '<form method="post" action="/super-admin/tenants/' + slug + '/toggle" style="display:inline">'
            f'<button class="btn btn-small" type="submit">{escape(pause_label)}</button></form> '
            '<form method="post" action="/super-admin/tenants/' + slug + '/archive" style="display:inline">'
            '<button class="btn btn-reject btn-small" type="submit" '
            f'onclick="return confirm(\'{confirm_archive}\')">{t(lang, "superdash.archive")}</button></form>'
            '</td></tr>'
        )
    if not rows:
        rows = f'<tr><td colspan="6" class="muted">{t(lang, "superdash.empty")}</td></tr>'
    body = (
        '<div class="cards">'
        + _stat_card(len(tenants), t(lang, "superdash.total_tenants"))
        + _stat_card(sum(application_counts.values()), t(lang, "superdash.total_apps"))
        + '</div>'
        '<div class="section"><div style="display:flex;justify-content:space-between;gap:12px;'
        'align-items:center;flex-wrap:wrap">'
        f'<h2 style="margin:0">{t(lang, "superdash.heading")}</h2>'
        f'<a class="btn btn-primary" href="/super-admin/tenants/new">{t(lang, "superdash.create")}</a></div>'
        f'<p class="muted">{t(lang, "superdash.tokens_note")}</p>'
        '<div style="overflow-x:auto"><table><thead><tr>'
        f'<th>{t(lang, "superdash.col_tenant")}</th>'
        f'<th>{t(lang, "superdash.col_status")}</th>'
        f'<th>{t(lang, "superdash.col_token")}</th>'
        f'<th>{t(lang, "superdash.col_password")}</th>'
        f'<th>{t(lang, "superdash.col_apps")}</th><th></th>'
        '</tr></thead><tbody>' + rows +
        '</tbody></table></div></div>'
    )
    return _super_page(t(lang, "superdash.page_title"), body, lang)


def _flag_checked(values: dict | None, tenant, field: str, *, default: bool = False) -> str:
    """Return the HTML checked attribute. A missing POST key means off."""
    if values is not None:
        raw = values.get(field, "")
        on = raw is True or str(raw).lower() in {"1", "true", "on", "yes"}
    elif tenant is not None:
        on = bool(getattr(tenant, field, default))
    else:
        on = default
    return " checked" if on else ""


def _form_value(values: dict | None, tenant, field: str, default: str = "") -> str:
    if values is not None and field in values:
        value = values[field]
    elif tenant is not None:
        value = getattr(tenant, field, default)
    else:
        value = default
    return escape(str(value if value is not None else ""))


def super_tenant_form_page(
    lang: str,
    tenant=None,
    values: dict | None = None,
    error: str = "",
) -> str:
    """Render create/edit form without ever including an actual bot token."""
    lang = i18n.normalize_lang(lang)
    editing = tenant is not None
    slug = _form_value(values, tenant, "slug")
    action = (
        f'/super-admin/tenants/{escape(tenant.slug)}/edit' if editing
        else '/super-admin/tenants/new'
    )
    error_html = f'<div class="err">{escape(error)}</div>' if error else ""
    if values is not None:
        checked = str(values.get("is_active", "")) in {"1", "true", "on", "True"}
    elif tenant is not None:
        checked = bool(tenant.is_active)
    else:
        checked = True
    active = " checked" if checked else ""
    closed = _flag_checked(values, tenant, "registration_closed", default=False)
    slug_field = (
        f'<input type="text" name="slug" value="{slug}" required pattern="[a-z0-9-]{{2,64}}" '
        'placeholder="adrenaline" style="width:100%">'
        if not editing else
        f'<input type="text" value="{slug}" disabled style="width:100%">'
        f'<input type="hidden" name="slug" value="{slug}">'
    )
    token_hint = (
        t(lang, "tenant.form.token_hint_edit") if editing
        else t(lang, "tenant.form.token_hint_new")
    )
    password_hint = (
        t(lang, "tenant.form.password_hint_edit") if editing
        else t(lang, "tenant.form.password_hint_new")
    )
    restart_form = (
        f'<form method="post" action="/super-admin/tenants/{escape(tenant.slug)}/restart" '
        f'style="display:inline"><button class="btn btn-ghost" type="submit">'
        f'{t(lang, "tenant.form.restart")}</button></form>'
        if editing else ""
    )
    form_title = (
        t(lang, "tenant.edit.title") if editing else t(lang, "tenant.create.title")
    )
    token_status = (
        f'<small class="muted">{t(lang, "tenant.form.token_configured")}</small>'
        if editing and tenant.token_configured
        else f'<small class="muted">{escape(token_hint)}</small>'
    )
    body = (
        f'<p><a href="/super-admin/">{t(lang, "tenant.form.back")}</a></p>'
        f'<div class="section"><h2>{form_title}</h2>'
        + error_html + f'<form method="post" action="{action}">'
        '<div class="kv" style="grid-template-columns:190px minmax(0,1fr)">'
        f'<div class="k">{t(lang, "tenant.form.slug")}</div><div>{slug_field}'
        f'<small class="muted">{t(lang, "tenant.form.slug_hint")}</small></div>'
        f'<div class="k">{t(lang, "tenant.form.name")}</div><div>'
        f'<input type="text" name="name" required value="{_form_value(values, tenant, "name")}" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.bot_token")}</div><div>'
        '<input type="password" name="bot_token" placeholder="***" style="width:100%">'
        + token_status + '</div>'
        f'<div class="k">{t(lang, "tenant.form.admin_chat_id")}</div><div>'
        f'<input type="text" name="admin_chat_id" value="{_form_value(values, tenant, "admin_chat_id", "0")}" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.required_channel")}</div><div>'
        f'<input type="text" name="required_channel" value="{_form_value(values, tenant, "required_channel")}" placeholder="@channel" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.channel_url")}</div><div>'
        f'<input type="text" name="channel_url" value="{_form_value(values, tenant, "channel_url")}" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.instagram_handle")}</div><div>'
        f'<input type="text" name="instagram_handle" value="{_form_value(values, tenant, "instagram_handle")}" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.instagram_url")}</div><div>'
        f'<input type="text" name="instagram_url" value="{_form_value(values, tenant, "instagram_url")}" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.spreadsheet_id")}</div><div>'
        f'<input type="text" name="spreadsheet_id" value="{_form_value(values, tenant, "spreadsheet_id")}" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.drive_folder_id")}</div><div>'
        f'<input type="text" name="drive_folder_id" value="{_form_value(values, tenant, "drive_folder_id")}" style="width:100%"></div>'
        f'<div class="k" style="grid-column:1 / -1; margin-top:12px; font-weight:700">{t(lang, "tenant.form.event_section")}</div>'
        f'<div class="k">{t(lang, "tenant.form.event_date_text_ru")}</div><div>'
        f'<input type="text" name="event_date_text_ru" value="{_form_value(values, tenant, "event_date_text_ru")}" placeholder="например: 2 октября 2026 с 17:00 до 22:00 — время заезда участников" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.event_date_text_uz")}</div><div>'
        f'<input type="text" name="event_date_text_uz" value="{_form_value(values, tenant, "event_date_text_uz")}" placeholder="masalan: 2-oktyabr 2026, soat 17:00 dan 22:00 gacha — ishtirokchilar kirishi" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.event_venue_text_ru")}</div><div>'
        f'<input type="text" name="event_venue_text_ru" value="{_form_value(values, tenant, "event_venue_text_ru")}" placeholder="например: Tashkent INDEX" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.event_venue_text_uz")}</div><div>'
        f'<input type="text" name="event_venue_text_uz" value="{_form_value(values, tenant, "event_venue_text_uz")}" placeholder="например: Tashkent INDEX" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.event_guest_date_text_ru")}</div><div>'
        f'<input type="text" name="event_guest_date_text_ru" value="{_form_value(values, tenant, "event_guest_date_text_ru")}" placeholder="например: 3 октября с 10:00 — если гостей нет, оставьте пустым" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.event_guest_date_text_uz")}</div><div>'
        f'<input type="text" name="event_guest_date_text_uz" value="{_form_value(values, tenant, "event_guest_date_text_uz")}" placeholder="masalan: 3-oktyabr, soat 10:00 dan — mehmonlar bo‘lmasa, bo‘sh qoldiring" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.form.event_note_text_ru")}</div><div>'
        f'<textarea name="event_note_text_ru" rows="3" style="width:100%">{_form_value(values, tenant, "event_note_text_ru")}</textarea></div>'
        f'<div class="k">{t(lang, "tenant.form.event_note_text_uz")}</div><div>'
        f'<textarea name="event_note_text_uz" rows="3" style="width:100%">{_form_value(values, tenant, "event_note_text_uz")}</textarea></div>'
        f'<div class="k"></div><div><small class="muted">{t(lang, "tenant.form.event_hint")}</small></div>'
        f'<div class="k">{t(lang, "tenant.form.registration_closed")}</div><div><label>'
        f'<input type="checkbox" name="registration_closed" value="1"{closed}> '
        f'{t(lang, "tenant.form.registration_closed_hint")}</label></div>'
        f'<div class="k">{t(lang, "tenant.form.admin_password")}</div><div>'
        '<input type="password" name="admin_password" placeholder="***" style="width:100%">'
        f'<small class="muted">{t(lang, "tenant.form.password_stored")}</small></div>'
        f'<div class="k">{t(lang, "tenant.form.is_active")}</div><div><label>'
        f'<input type="checkbox" name="is_active" value="1"{active}> '
        f'{t(lang, "tenant.form.is_active_hint")}</label></div>'
        '</div><div class="actions">'
        f'<button class="btn btn-primary" type="submit">{t(lang, "common.save")}</button></div></form>'
        + (f'<div class="actions">{restart_form}</div>' if restart_form else "")
        + '</div>'
    )
    return _super_page(t(lang, "tenant.form.page_title"), body, lang)


def tenant_settings_page(lang: str, tenant, message: str = "", error: str = "") -> str:
    """Render settings tenant admins may change without seeing their bot token."""
    lang = i18n.normalize_lang(lang)
    notice = (
        f'<div class="ok">{t(lang, "settings.saved")}</div>' if message == "saved" else ""
    )
    notice += f'<div class="err">{escape(error)}</div>' if error else ""

    def field(name: str, label: str, value: str, placeholder: str = "") -> str:
        return (
            f'<div class="k">{escape(label)}</div><div><input type="text" name="{name}" '
            f'value="{escape(str(value or ""))}" placeholder="{escape(placeholder)}" '
            'style="width:100%"></div>'
        )

    body = (
        '<div class="section">'
        f'<h2>{t(lang, "settings.title")}</h2>' + notice +
        f'<p class="muted">{t(lang, "settings.token_note")}</p>'
        '<form method="post" action="/settings">'
        '<div class="kv" style="grid-template-columns:190px minmax(0,1fr)">'
        + field("name", t(lang, "settings.name"), tenant.name)
        + field("admin_chat_id", t(lang, "settings.admin_chat_id"), tenant.admin_chat_id)
        + field("required_channel", t(lang, "settings.required_channel"),
                tenant.required_channel, "@channel")
        + field("channel_url", t(lang, "settings.channel_url"), tenant.channel_url)
        + field("instagram_handle", t(lang, "settings.instagram_handle"), tenant.instagram_handle)
        + field("instagram_url", t(lang, "settings.instagram_url"), tenant.instagram_url)
        + field("spreadsheet_id", t(lang, "settings.spreadsheet_id"), tenant.spreadsheet_id)
        + field("drive_folder_id", t(lang, "settings.drive_folder_id"), tenant.drive_folder_id)
        + f'<div class="k" style="grid-column:1 / -1; margin-top:12px; font-weight:700">{t(lang, "settings.event_section")}</div>'
        + field("event_date_text_ru", t(lang, "settings.event_date_text_ru"), getattr(tenant, "event_date_text_ru", ""))
        + field("event_date_text_uz", t(lang, "settings.event_date_text_uz"), getattr(tenant, "event_date_text_uz", ""))
        + field("event_venue_text_ru", t(lang, "settings.event_venue_text_ru"), getattr(tenant, "event_venue_text_ru", ""))
        + field("event_venue_text_uz", t(lang, "settings.event_venue_text_uz"), getattr(tenant, "event_venue_text_uz", ""))
        + field("event_guest_date_text_ru", t(lang, "settings.event_guest_date_text_ru"), getattr(tenant, "event_guest_date_text_ru", ""))
        + field("event_guest_date_text_uz", t(lang, "settings.event_guest_date_text_uz"), getattr(tenant, "event_guest_date_text_uz", ""))
        + (
            f'<div class="k">{t(lang, "settings.event_note_text_ru")}</div><div>'
            f'<textarea name="event_note_text_ru" rows="3" style="width:100%">'
            f'{escape(str(getattr(tenant, "event_note_text_ru", "") or ""))}</textarea></div>'
            f'<div class="k">{t(lang, "settings.event_note_text_uz")}</div><div>'
            f'<textarea name="event_note_text_uz" rows="3" style="width:100%">'
            f'{escape(str(getattr(tenant, "event_note_text_uz", "") or ""))}</textarea></div>'
        )
        + f'<div class="k"></div><div><small class="muted">{t(lang, "settings.event_hint")}</small></div>'
        + (
            f'<div class="k">{t(lang, "settings.registration_closed")}</div><div><label>'
            f'<input type="checkbox" name="registration_closed" value="1"'
            f'{" checked" if getattr(tenant, "registration_closed", False) else ""}> '
            f'{t(lang, "settings.registration_closed_hint")}</label></div>'
        )
        + f'<div class="k">{t(lang, "settings.new_password")}</div>'
        '<div><input type="password" name="admin_password" '
        f'placeholder="{t(lang, "settings.new_password_hint")}" style="width:100%"></div>'
        + '</div><div class="actions">'
        f'<button class="btn btn-primary" type="submit">{t(lang, "common.save")}</button>'
        '</div></form></div>'
    )
    return _page(t(lang, "settings.page_title"), body, lang, active="settings")


def tenant_diag_page(lang: str, tenant, checks: list[tuple[str, bool, str]]) -> str:
    """Render a per-tenant Telegram configuration diagnostic result.

    ``checks`` rows arrive fully localized from the server layer; the token
    itself never appears in ``detail``.
    """
    lang = i18n.normalize_lang(lang)
    rows = ''.join(
        f'<tr><td>{"✅" if ok else "❌"} {escape(label)}</td><td>{escape(detail)}</td></tr>'
        for label, ok, detail in checks
    ) or f'<tr><td colspan="2" class="muted">{t(lang, "diag.no_results")}</td></tr>'
    body = (
        f'<p><a href="/super-admin/tenants/{escape(tenant.slug)}/edit">'
        f'← {escape(tenant.name)}</a></p>'
        f'<div class="section"><h2>{t(lang, "diag.title", name=tenant.name)}</h2>'
        f'<p class="muted">{t(lang, "diag.token_note")}</p>'
        '<table><thead><tr>'
        f'<th>{t(lang, "diag.col_check")}</th>'
        f'<th>{t(lang, "diag.col_result")}</th>'
        f'</tr></thead><tbody>{rows}</tbody></table></div>'
    )
    return _super_page(t(lang, "diag.page_title"), body, lang)


def super_tenant_directions_page(lang: str, tenant, directions) -> str:
    """List tenant directions with hierarchy, localized."""
    lang = i18n.normalize_lang(lang)
    # Build parent lookup
    id_to_dir = {d.id: d for d in directions}
    # Sort: parents first then children
    roots = [d for d in directions if d.parent_id is None]
    roots_sorted = sorted(roots, key=lambda d: (d.sort_order, d.id))
    rows = ""
    for root in roots_sorted:
        status = t(lang, "tenant.directions.active") if root.is_active else t(lang, "tenant.directions.inactive")
        parent_label = "—"
        rows += (
            f'<tr><td><b>{escape(root.label_ru if lang=="ru" else root.label_uz)}</b><br>'
            f'<small class="muted">{escape(root.canonical)}</small></td>'
            f'<td><code>{escape(root.slug)}</code></td>'
            f'<td>{escape(parent_label)}</td>'
            f'<td>{root.sort_order}</td>'
            f'<td>{escape(status)}</td>'
            f'<td style="white-space:nowrap">'
            f'<a class="btn btn-ghost btn-small" href="/super-admin/tenants/{escape(tenant.slug)}/directions/{root.id}/edit">{t(lang, "tenant.directions.edit")}</a> '
            f'<form method="post" action="/super-admin/tenants/{escape(tenant.slug)}/directions/{root.id}/delete" style="display:inline">'
            f'<button class="btn btn-reject btn-small" type="submit" onclick="return confirm(\'{_js_confirm(t(lang, "tenant.directions.confirm_delete"))}\')">{t(lang, "tenant.directions.delete")}</button></form>'
            f'</td></tr>'
        )
        children = [d for d in directions if d.parent_id == root.id]
        for child in sorted(children, key=lambda d: (d.sort_order, d.id)):
            status_c = t(lang, "tenant.directions.active") if child.is_active else t(lang, "tenant.directions.inactive")
            rows += (
                f'<tr><td style="padding-left:24px">↳ {escape(child.label_ru if lang=="ru" else child.label_uz)}<br>'
                f'<small class="muted">{escape(child.canonical)}</small></td>'
                f'<td><code>{escape(child.slug)}</code></td>'
                f'<td>{escape(root.label_ru if lang=="ru" else root.label_uz)}</td>'
                f'<td>{child.sort_order}</td>'
                f'<td>{escape(status_c)}</td>'
                f'<td style="white-space:nowrap">'
                f'<a class="btn btn-ghost btn-small" href="/super-admin/tenants/{escape(tenant.slug)}/directions/{child.id}/edit">{t(lang, "tenant.directions.edit")}</a> '
                f'<form method="post" action="/super-admin/tenants/{escape(tenant.slug)}/directions/{child.id}/delete" style="display:inline">'
                f'<button class="btn btn-reject btn-small" type="submit" onclick="return confirm(\'{_js_confirm(t(lang, "tenant.directions.confirm_delete"))}\')">{t(lang, "tenant.directions.delete")}</button></form>'
                f'</td></tr>'
            )
    # Orphans (parent not found) as roots
    orphans = [d for d in directions if d.parent_id is not None and d.parent_id not in id_to_dir]
    for o in sorted(orphans, key=lambda d: (d.sort_order, d.id)):
        status_o = t(lang, "tenant.directions.active") if o.is_active else t(lang, "tenant.directions.inactive")
        rows += (
            f'<tr><td>{escape(o.label_ru if lang=="ru" else o.label_uz)}<br><small class="muted">{escape(o.canonical)}</small></td>'
            f'<td><code>{escape(o.slug)}</code></td>'
            f'<td class="muted">orphan {o.parent_id}</td>'
            f'<td>{o.sort_order}</td>'
            f'<td>{escape(status_o)}</td>'
            f'<td><a class="btn btn-ghost btn-small" href="/super-admin/tenants/{escape(tenant.slug)}/directions/{o.id}/edit">{t(lang, "tenant.directions.edit")}</a></td></tr>'
        )
    if not rows:
        rows = f'<tr><td colspan="6" class="muted">{t(lang, "tenant.directions.empty")}</td></tr>'

    body = (
        f'<p><a href="/super-admin/tenants/{escape(tenant.slug)}/edit">{t(lang, "tenant.directions.back")}</a></p>'
        f'<div class="section"><div style="display:flex;justify-content:space-between;align-items:center">'
        f'<h2>{t(lang, "tenant.directions.heading", name=escape(tenant.name))}</h2>'
        f'<a class="btn btn-primary" href="/super-admin/tenants/{escape(tenant.slug)}/directions/new">{t(lang, "tenant.directions.create")}</a>'
        f'</div>'
        '<div style="overflow-x:auto"><table><thead><tr>'
        f'<th>{t(lang, "tenant.directions.col_name")}</th>'
        f'<th>{t(lang, "tenant.directions.col_canonical")}</th>'
        f'<th>{t(lang, "tenant.directions.col_parent")}</th>'
        f'<th>{t(lang, "tenant.directions.col_sort")}</th>'
        f'<th>{t(lang, "tenant.directions.col_status")}</th>'
        f'<th>{t(lang, "tenant.directions.col_actions")}</th>'
        '</tr></thead><tbody>' + rows + '</tbody></table></div></div>'
    )
    return _super_page(t(lang, "tenant.directions.page_title"), body, lang)


def super_direction_form_page(lang: str, tenant, direction=None, parents=None, values=None, error="") -> str:
    lang = i18n.normalize_lang(lang)
    editing = direction is not None
    title = t(lang, "tenant.direction.edit.title", name=tenant.name) if editing else t(lang, "tenant.direction.create.title", name=tenant.name)
    action = (
        f"/super-admin/tenants/{escape(tenant.slug)}/directions/{direction.id}/edit"
        if editing else f"/super-admin/tenants/{escape(tenant.slug)}/directions/new"
    )
    err_html = f'<div class="err">{escape(error)}</div>' if error else ""

    def fv(field, default=""):
        if values is not None and field in values:
            return escape(str(values[field] or ""))
        if direction is not None:
            return escape(str(getattr(direction, field, default) or ""))
        return escape(str(default))

    # Parent select
    parents = parents or []
    parent_options = '<option value="">— (root)</option>'
    current_parent = fv("parent_id")
    # Also consider values dict for parent_id
    try:
        cur_pid = int(current_parent) if current_parent else None
    except:
        cur_pid = None
    for p in parents:
        sel = " selected" if cur_pid is not None and p.id == cur_pid else ""
        # For edit, also check direction.parent_id
        if not sel and direction and direction.parent_id == p.id:
            sel = " selected"
        parent_options += f'<option value="{p.id}"{sel}>{escape(p.label_ru)} ({escape(p.canonical)})</option>'

    # is_active checkbox
    if values is not None:
        is_active_checked = str(values.get("is_active", "")) in {"1", "true", "on", "True"}
    elif direction is not None:
        is_active_checked = bool(direction.is_active)
    else:
        is_active_checked = True
    active_attr = " checked" if is_active_checked else ""

    body = (
        f'<p><a href="/super-admin/tenants/{escape(tenant.slug)}/directions">{t(lang, "common.back")}</a></p>'
        f'<div class="section"><h2>{title}</h2>' + err_html +
        f'<form method="post" action="{action}">'
        '<div class="kv" style="grid-template-columns:190px minmax(0,1fr)">'
        f'<div class="k">{t(lang, "tenant.direction.form.canonical")}</div><div><input type="text" name="canonical" required value="{fv("canonical")}" style="width:100%"><small class="muted">{t(lang, "tenant.direction.form.hint_canonical")}</small></div>'
        f'<div class="k">{t(lang, "tenant.direction.form.label_ru")}</div><div><input type="text" name="label_ru" value="{fv("label_ru")}" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.direction.form.label_uz")}</div><div><input type="text" name="label_uz" value="{fv("label_uz")}" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.direction.form.slug")}</div><div><input type="text" name="slug" value="{fv("slug")}" pattern="[A-Za-z0-9_-]{1,64}" style="width:100%"><small class="muted">{t(lang, "tenant.direction.form.hint_slug")}</small></div>'
        f'<div class="k">{t(lang, "tenant.direction.form.parent")}</div><div><select name="parent_id" style="width:100%">{parent_options}</select></div>'
        f'<div class="k">{t(lang, "tenant.direction.form.sort_order")}</div><div><input type="number" name="sort_order" value="{fv("sort_order", "0")}" style="width:100%"></div>'
        f'<div class="k">{t(lang, "tenant.direction.form.is_active")}</div><div><label><input type="checkbox" name="is_active" value="1"{active_attr}> {t(lang, "common.active")}</label></div>'
        '</div><div class="actions">'
        f'<button class="btn btn-primary" type="submit">{t(lang, "common.save")}</button>'
        '</div></form></div>'
    )
    return _super_page(title, body, lang)
