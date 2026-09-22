"""Server-rendered HTML for the admin panel (no external template engine)."""
from __future__ import annotations

from html import escape
from typing import Iterable, Optional
import os
import time

from ..constants import DIRECTIONS_CANON, SIDES, SIDE_LABELS_RU
from ..db import Application, STATUS_APPROVED, STATUS_PENDING, STATUS_REJECTED

_STATUS_RU = {
    STATUS_PENDING: "На рассмотрении",
    STATUS_APPROVED: "Одобрено",
    STATUS_REJECTED: "Отклонено",
}
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
.login-wrap { max-width: 340px; margin: 80px auto; }
.err { background: #fee2e2; color: #991b1b; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; }
.ok { background: #d1fae5; color: #065f46; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; }
.muted { color: #6b7280; }
.bar { display:flex; align-items:center; gap:8px; margin:6px 0; }
.bar .track { flex:1; height:10px; background:#ede9fe; border-radius:6px; overflow:hidden; }
.bar .fill { height:100%; background:#7c3aed; }

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


def _page(title: str, body: str, active: str = "", nav: bool = True) -> str:
    nav_html = ""
    if nav:
        def link(href: str, label: str, key: str) -> str:
            cls = ' class="active"' if active == key else ""
            return f'<a{cls} href="{href}">{label}</a>'

        nav_html = (
            '<header>'
            '<span class="brand">🚗 Promotors Show — Admin</span>'
            f'<nav>{link("/", "Дашборд", "home")} '
            f'{link("/applications", "Заявки", "apps")} '
            f'{link("/ticket-assets", "🎫 Билеты", "ticket")} '
            f'{link("/broadcast", "📢 Рассылка", "broadcast")} '
            f'{link("/settings", "⚙️ Настройки", "settings")} '
            f'{link("/export.xlsx", "📊 Excel", "export")} '
            f'{link("/export.csv", "CSV", "export_csv")}</nav>'
            '<span class="spacer"></span>'
            '<a href="/logout" style="color:#ddd6fe">Выйти</a>'
            '</header>'
        )
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body>{nav_html}<main>{body}</main></body></html>"
    )


def login_page(error: bool = False) -> str:
    err = '<div class="err">Неверный пароль</div>' if error else ""
    body = (
        '<div class="login-wrap"><div class="section">'
        '<h2>Вход в админ-панель</h2>'
        f'{err}'
        '<form method="post" action="/login">'
        '<div style="margin-bottom:12px"><input type="password" name="password" '
        'placeholder="Пароль" style="width:100%" autofocus></div>'
        '<button class="btn btn-primary" type="submit" style="width:100%">Войти</button>'
        '</form></div></div>'
    )
    return _page("Вход", body, nav=False)


def panel_disabled_page() -> str:
    body = (
        '<div class="login-wrap"><div class="section">'
        '<h2>Панель отключена</h2>'
        '<p class="muted">Задайте секрет <code>ADMIN_PASSWORD</code>, чтобы включить '
        'админ-панель.</p></div></div>'
    )
    return _page("Панель отключена", body, nav=False)


def _stat_card(n, label: str) -> str:
    return f'<div class="card"><div class="n">{n}</div><div class="l">{escape(label)}</div></div>'


def _distribution(title: str, data: dict) -> str:
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


def dashboard_page(stats: dict) -> str:
    cards = (
        '<div class="cards">'
        + _stat_card(stats["total"], "Всего заявок")
        + _stat_card(stats["pending"], "На рассмотрении")
        + _stat_card(stats["approved"], "Одобрено")
        + _stat_card(stats.get("approved_users", stats["approved"]), "Одобр. участники")
        + _stat_card(stats["rejected"], "Отклонено")
        + _stat_card(f'№{stats["max_number"]}', "Последний номер")
        + '</div>'
    )
    
    excel_btn = (
        '<div style="margin:20px 0; text-align:right">'
        '<a class="btn btn-primary" href="/export.xlsx" style="padding:10px 20px; font-size:15px">'
        '📥 Скачать Excel (.xlsx)'
        '</a> '
        '<a class="btn btn-ghost" href="/ticket-assets" style="padding:10px 20px; font-size:15px">'
        '🎫 Управление билетами'
        '</a>'
        '</div>'
    )

    body = (
        cards
        + excel_btn
        + _distribution("📊 По направлениям", stats.get("by_direction", {}))
        + _distribution("🌍 По странам", stats.get("by_country", {}))
        + _distribution("🌐 По языкам", stats.get("by_language", {}))
        + _distribution("📅 Заявки по дням (сост. 14 дней)", stats.get("by_date", {}))
    )
    return _page("Дашборд", body, active="home")


def _status_badge(status: str) -> str:
    cls = _STATUS_CLASS.get(status, "")
    label = _STATUS_RU.get(status, status)
    return f'<span class="badge {cls}">{escape(label)}</span>'


def applications_page(
    apps: Iterable[Application], status_filter: Optional[str], search: str
) -> str:
    filters = (
        '<div class="filters">'
        + f'<a class="{ "active" if (status_filter or "all")=="all" else ""}" href="/applications">Все</a>'
        + f'<a class="{ "active" if status_filter==STATUS_PENDING else ""}" href="/applications?status={STATUS_PENDING}">На рассмотрении</a>'
        + f'<a class="{ "active" if status_filter==STATUS_APPROVED else ""}" href="/applications?status={STATUS_APPROVED}">Одобрено</a>'
        + f'<a class="{ "active" if status_filter==STATUS_REJECTED else ""}" href="/applications?status={STATUS_REJECTED}">Отклонено</a>'
        + '<form method="get" action="/applications">'
        + f'<input type="text" name="search" placeholder="Поиск: номер, телефон…" value="{escape(search)}">'
        + '<button class="btn btn-ghost" type="submit">Найти</button>'
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
            f"<td>{_status_badge(app.status)}</td>"
            f'<td><a class="btn btn-ghost" href="/application/{app.id}">Открыть</a></td>'
            "</tr>"
        )
    if not rows:
        rows = '<tr><td colspan="9" class="muted" style="padding:24px;text-align:center">Заявок нет</td></tr>'

    table = (
        '<div class="section">'
        f'<h2>Заявки ({len(apps)})</h2>'
        + filters
        + '<div style="overflow-x:auto"><table><thead><tr>'
        '<th>Номер</th><th>Фото</th><th>Страна</th><th>Гос. номер</th><th>Направление</th>'
        '<th>Телефон</th><th>Пользователь</th><th>Статус</th><th></th>'
        '</tr></thead><tbody>'
        + rows
        + '</tbody></table></div></div>'
    )
    return _page("Заявки", table, active="apps")


def _individual_message_form(
    app_id: int, sent: bool = False, error: str = ""
) -> str:
    notice = ""
    if sent:
        notice = '<div class="section" style="background:#ecfdf5;margin:0 0 12px"><p>✅ Сообщение отправлено</p></div>'
    elif error:
        notice = f'<div class="err">{escape(error)}</div>'
    return (
        '<div class="section"><h2>✉️ Личное сообщение</h2>'
        + notice
        + '<p class="muted">Сообщение уйдёт только этому пользователю, тем же ботом.</p>'
        f'<form method="post" action="/application/{app_id}/message">'
        '<textarea name="text" maxlength="3500" rows="5" '
        'style="width:100%;padding:10px;border:1px solid #ccc;border-radius:8px;'
        'font-size:15px;font-family:inherit" placeholder="Текст сообщения…"></textarea>'
        '<div style="margin-top:10px">'
        '<button class="btn btn-primary" type="submit">📩 Отправить</button>'
        '</div></form></div>'
    )


def _status_control(
    app_id: int, current_status: str, changed: bool = False, error: str = ""
) -> str:
    notice = ""
    if changed:
        notice = '<div class="section" style="background:#ecfdf5;margin:0 0 12px"><p>✅ Статус изменён</p></div>'
    elif error:
        notice = f'<div class="err">{escape(error)}</div>'

    options = [
        (STATUS_APPROVED, "✅ Одобрено", "btn-approve"),
        (STATUS_REJECTED, "❌ Отклонено", "btn-reject"),
        (STATUS_PENDING, "⏳ На рассмотрении", "btn-ghost"),
    ]
    buttons = ""
    for status, label, cls in options:
        if status == current_status:
            continue
        buttons += (
            f'<form method="post" action="/application/{app_id}/status" style="display:inline">'
            f'<input type="hidden" name="status" value="{status}">'
            f'<button class="btn {cls}" type="submit" '
            'onclick="return confirm(\'Изменить статус и уведомить участника в Telegram?\')">'
            f'{label}</button></form>'
        )
    return (
        '<div class="section"><h2>🔀 Управление статусом</h2>'
        + notice
        + '<p class="muted">Текущий статус: '
        + _status_badge(current_status)
        + '<br>Изменение статуса отправит участнику уведомление в Telegram '
        "(при переводе в «Одобрено» — также билет и номер).</p>"
        + f'<div style="display:flex;gap:10px;flex-wrap:wrap">{buttons}</div>'
        + '</div>'
    )


def application_detail_page(
    app: Application,
    msg_sent: bool = False,
    msg_error: str = "",
    status_changed: bool = False,
    status_error: str = "",
) -> str:
    photos = ""
    for i, _ in enumerate(app.photo_paths):
        side = SIDES[i] if i < len(SIDES) else str(i + 1)
        caption = SIDE_LABELS_RU.get(side, side)
        photos += (
            f'<figure><img src="/photo/{app.id}/{i}" alt="{escape(caption)}">'
            f'<figcaption>{escape(caption)} сторона</figcaption></figure>'
        )
    photos_html = f'<div class="photos">{photos}</div>' if photos else '<p class="muted">Нет фотографий</p>'

    mods = ""
    mod_paths = getattr(app, "mod_paths", []) or []
    for i, _ in enumerate(mod_paths):
        mods += (
            f'<figure><img src="/modphoto/{app.id}/{i}" alt="Изменение {i + 1}">'
            f'<figcaption>Изменение {i + 1}</figcaption></figure>'
        )
    mods_html = (
        f'<div class="photos">{mods}</div>'
        if mods
        else '<p class="muted">Участник не отметил изменений</p>'
    )

    badge_path = getattr(app, "badge_photo_path", "") or ""
    badge_html = (
        f'<div class="photos"><figure><img src="/badgephoto/{app.id}" alt="Фото на бейдж">'
        '<figcaption>Фото на бейдж</figcaption></figure></div>'
        if badge_path
        else '<p class="muted">Участник ещё не прислал фото для бейджа</p>'
    )

    number = f'№{app.reg_number}' if app.reg_number is not None else "—"
    kv = (
        '<div class="kv">'
        f'<div class="k">Статус</div><div>{_status_badge(app.status)}</div>'
        f'<div class="k">Рег. номер</div><div>{number}</div>'
        f'<div class="k">Страна</div><div>{escape(app.country)}</div>'
        f'<div class="k">Гос. номер</div><div><b>{escape(app.plate)}</b></div>'
        f'<div class="k">Направление</div><div>{escape(app.direction)}</div>'
        f'<div class="k">Изменения в авто</div>'
        f'<div>{len(getattr(app, "mod_paths", []) or []) or "—"}</div>'
        f'<div class="k">Телефон</div><div>{escape(app.phone)}</div>'
        f'<div class="k">Пользователь</div><div>{escape(app.username)}</div>'
        f'<div class="k">Язык</div><div>{escape(app.language)}</div>'
        f'<div class="k">Подана</div><div>{escape(app.created_at)}</div>'
        + (f'<div class="k">Обработана</div><div>{escape(app.processed_at or "")} '
           f'{escape(app.processed_by or "")}</div>' if app.processed_at else "")
        + '</div>'
    )

    actions = ""
    if app.status == STATUS_PENDING:
        actions = (
            '<div class="actions">'
            f'<form method="post" action="/application/{app.id}/approve">'
            '<button class="btn btn-approve" type="submit">✅ Принять</button></form>'
            f'<form method="post" action="/application/{app.id}/reject">'
            '<button class="btn btn-reject" type="submit">❌ Отклонить</button></form>'
            '</div>'
        )

    body = (
        '<p><a href="/applications">← Назад к заявкам</a></p>'
        f'<div class="section"><h2>Заявка #{app.id}</h2>{kv}{actions}</div>'
        f'<div class="section"><h2>Фотографии</h2>{photos_html}</div>'
        f'<div class="section"><h2>Изменения в автомобиле</h2>{mods_html}</div>'
        f'<div class="section"><h2>Фото на бейдж</h2>{badge_html}</div>'
        + _status_control(app.id, app.status, changed=status_changed, error=status_error)
        + _individual_message_form(app.id, sent=msg_sent, error=msg_error)
    )
    return _page(f"Заявка #{app.id}", body, active="apps")


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
    # Nothing selected yet (first load) → don't pre-narrow the audience.
    langs = list(langs) if langs is not None else ["uz", "ru"]
    directions = list(directions) if directions is not None else list(DIRECTIONS_CANON)

    result_html = ""
    if result:
        photo_note = " · с фото" if result.get("with_photo") else ""
        result_html = (
            '<div class="section" style="background:#ecfdf5;margin-top:0;margin-bottom:16px">'
            "<h2>Рассылка завершена</h2>"
            f'<p>Аудитория: <b>{escape(str(result.get("audience_label", "")))}</b>{photo_note}<br>'
            f'Отправлено: <b>{result.get("ok", 0)}</b> · '
            f'ошибки / блок бота: <b>{result.get("fail", 0)}</b> · '
            f'всего адресатов: <b>{result.get("total", 0)}</b><br>'
            f'🇺🇿 на узбекском: <b>{result.get("ok_uz", 0)}</b> · '
            f'🇷🇺 на русском: <b>{result.get("ok_ru", 0)}</b></p>'
            "</div>"
        )
    elif preview_count is not None:
        result_html = (
            '<div class="section" style="background:#eff6ff;margin-top:0;margin-bottom:16px">'
            f'<p class="muted">По выбранным фильтрам получателей: <b>{preview_count}</b></p>'
            "</div>"
        )
    err_html = f'<div class="err">{escape(error)}</div>' if error else ""

    options = [
        ("approved", "Одобрено — успешная регистрация", counts.get("approved", 0)),
        ("pending", "На рассмотрении — заявка ещё не решена", counts.get("pending", 0)),
        ("rejected", "Отклонено — регистрация не принята", counts.get("rejected", 0)),
        ("incomplete", "Не завершили регистрацию (открыли бота, заявки нет)", counts.get("incomplete", 0)),
        ("all_apps", "Все, кто подал заявку", counts.get("all_apps", 0)),
        ("starters", "Все, кого бот уже знает", counts.get("starters", 0)),
    ]
    radios = ""
    for key, label, n in options:
        checked = " checked" if audience == key else ""
        radios += (
            f'<label style="display:flex;gap:10px;align-items:flex-start;margin:8px 0;'
            f'padding:10px 12px;border:1px solid #eee;border-radius:10px;cursor:pointer">'
            f'<input type="radio" name="audience" value="{key}"{checked} style="margin-top:4px"> '
            f'<span><b>{escape(label)}</b>'
            f'<span class="muted"> — {n} чел.</span></span></label>'
        )

    lang_options = [("uz", "🇺🇿 Только узбекский"), ("ru", "🇷🇺 Только русский")]
    lang_checks = ""
    for key, label in lang_options:
        checked = " checked" if key in langs else ""
        lang_checks += (
            f'<label style="display:inline-flex;gap:6px;align-items:center;margin:4px 16px 4px 0">'
            f'<input type="checkbox" name="langs" value="{key}"{checked}> {escape(label)}</label>'
        )

    dir_checks = ""
    for canonical in DIRECTIONS_CANON:
        checked = " checked" if canonical in directions else ""
        dir_checks += (
            f'<label style="display:inline-flex;gap:6px;align-items:center;margin:4px 16px 4px 0">'
            f'<input type="checkbox" name="directions" value="{escape(canonical)}"{checked}> '
            f'{escape(canonical)}</label>'
        )

    body = (
        result_html
        + err_html
        + '<div class="section">'
        "<h2>Рассылка в Telegram</h2>"
        '<p class="muted">Выберите аудиторию и напишите текст. Сообщение уйдёт тем же ботом, '
        "которым они писали. Один пользователь — одно сообщение.</p>"
        '<form method="post" action="/broadcast" enctype="multipart/form-data">'
        f'<div style="margin:12px 0">{radios}</div>'
        '<div style="margin:16px 0"><label style="display:block;font-weight:600;margin-bottom:6px">'
        "Уточнить по языку</label>" + lang_checks + "</div>"
        '<div style="margin:16px 0"><label style="display:block;font-weight:600;margin-bottom:6px">'
        "Уточнить по направлению</label>" + dir_checks + "</div>"
        + _broadcast_textarea(
            "text_uz",
            "🇺🇿 O‘zbekcha",
            "Тем, кто выбрал узбекский язык",
            "Xabar matni…",
            last_text_uz,
        )
        + _broadcast_textarea(
            "text_ru",
            "🇷🇺 Русский",
            "Всем остальным получателям",
            "Текст сообщения…",
            last_text_ru,
        )
        + '<p class="muted" style="margin:0 0 12px;font-size:13px">'
        "Если заполнено только одно поле — этот текст уйдёт всем получателям."
        "</p>"
        + _broadcast_photo_input(
            "photo_uz", "bcast-photo-uz", "🇺🇿 Фото к посту — O‘zbekcha",
            "необязательно — если не загружено, возьмётся фото из русской версии",
        )
        + _broadcast_photo_input(
            "photo_ru", "bcast-photo-ru", "🇷🇺 Фото к посту — Русский",
            "необязательно — если не загружено, возьмётся фото из узбекской версии",
        )
        + '<p class="muted" style="margin:0 0 16px;font-size:13px">'
        "Максимум 1024 символа в тексте того языка, для которого прикреплено фото "
        "(ограничение Telegram на подпись)."
        "</p>"
        + _broadcast_preview_block(last_text_uz, last_text_ru)
        + '<label class="muted" style="display:flex;gap:8px;align-items:center;margin-bottom:14px">'
        '<input type="checkbox" name="confirm" value="1" required> '
        "Да, отправить выбранной аудитории"
        "</label>"
        '<div style="display:flex;gap:10px">'
        '<button class="btn btn-ghost" type="submit" name="action" value="preview">'
        "🔍 Показать количество</button>"
        '<button class="btn btn-primary" type="submit" name="action" value="send">'
        "📢 Отправить</button>"
        "</div>"
        "</form>"
        '<p class="muted" style="margin-top:16px;font-size:13px">'
        "«Не завершили регистрацию» — те, кто нажал /start после обновления бота, "
        "но заявку так и не отправил. Старых брошенных анкет в базе нет.<br>"
        "Если снять все языки или все направления — фильтр по этому признаку не применяется."
        "</p>"
        "</div>"
    )
    return _page("Рассылка", body, active="broadcast")


def _broadcast_photo_input(name: str, input_id: str, label: str, hint: str) -> str:
    return (
        '<div style="margin:0 0 12px">'
        f'<label style="display:block;font-weight:600;margin-bottom:6px">📎 {escape(label)} '
        f'<span class="muted" style="font-weight:400"> — {escape(hint)}</span></label>'
        f'<input type="file" name="{name}" id="{input_id}" accept="image/*">'
        "</div>"
    )


def _broadcast_preview_block(last_text_uz: str, last_text_ru: str) -> str:
    """A live, client-side preview of what each language's recipient will see.

    Pure JS/no round trip: each language's photo is previewed straight from
    its own file picker (FileReader) and mirrors the *other* language's photo
    when its own is empty — matching the fallback the server applies when
    actually sending — and the text mirrors the textareas on every keystroke.
    Nothing here re-uploads anything or hits the request-size limit that
    sending the form does.
    """

    def card(lang_label: str, preview_photo_id: str, preview_text_id: str, initial: str) -> str:
        empty_hint = '<span class="muted">Пусто</span>'
        content = escape(initial) if initial else empty_hint
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
        '<h3 style="margin:0 0 12px;font-size:14px;color:#6b7280">👁 Предпросмотр поста</h3>'
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
        "if(!ta.value){box.innerHTML='<span class=\\\"muted\\\">Пусто</span>';}"
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
    inventory: dict,
    sponsors: list[dict],
    brand: dict,
    directions: list[dict],
    message: str = "",
    error: str = "",
) -> str:
    # Messages
    msg_map = {
        "brand_uploaded": "✅ Логотип бренда обновлён",
        "brand_deleted": "🗑 Логотип бренда удалён (теперь используется версия из репозитория, если есть)",
        "sponsor_uploaded": "✅ Логотип спонсора сохранён",
        "sponsor_deleted": "🗑 Логотип спонсора удалён",
        "direction_uploaded": "✅ Баннер направления сохранён",
        "direction_deleted": "🗑 Баннер направления удалён",
    }
    err_map = {
        "no_file": "Файл не выбран",
        "name_required": "Введите имя файла (только латиница, цифры, _ и -)",
        "invalid_name": "Имя может содержать только латиницу, цифры, _ и - (до 40 символов)",
        "unknown_brand": "Неизвестный бренд",
        "unknown_direction": "Неизвестное направление",
    }

    notice = ""
    if message and message in msg_map:
        notice = f'<div class="ok">{msg_map[message]}</div>'
    elif message:
        notice = f'<div class="ok">{escape(message)}</div>'
    if error:
        err_text = err_map.get(error, error)
        notice += f'<div class="err">❌ {escape(err_text)}</div>'

    # Ticket preview
    ts = int(time.time())
    preview_html = (
        '<div class="section">'
        '<h2>🎫 Предпросмотр билета</h2>'
        '<p class="muted">Так будет выглядеть билет с текущими логотипами. Фон — заглушка (градиент), '
        'в реальности за ним фото авто участника.</p>'
        '<div class="ticket-preview-wrap">'
        f'<img class="ticket-preview-img" src="/ticket-assets/preview.png?ts={ts}" alt="Ticket preview">'
        '<div style="flex:1;min-width:280px">'
        '<p class="muted" style="font-size:13px">После загрузки/удаления логотипа обновите страницу — '
        'предпросмотр перегенерируется автоматически. '
        'Если загружен хотя бы один спонсорский логотип из админки, используются только загруженные (из репозитория скрываются).</p>'
        '<a class="btn btn-ghost btn-small" href="/ticket-assets/preview.png" target="_blank">Открыть в полном размере</a> '
        f'<a class="btn btn-ghost btn-small" href="/ticket-assets?ts={ts}">🔄 Обновить</a>'
        '</div>'
        '</div>'
        '</div>'
    )

    # Brand logos
    brand_cards = ""
    for key in ("logo", "adrenaline"):
        info = brand.get(key, {})
        exists = info.get("exists")
        is_runtime = info.get("is_runtime")
        src = f"/assets/file/brand/{key}?ts={ts}" if exists else ""
        img_tag = f'<img src="{src}" alt="{escape(key)}">' if exists else '<span class="muted">Нет логотипа</span>'
        status = "загружен" if is_runtime else ("из репозитория" if exists else "❌ нет")
        badge = f'<span class="badge badge-{"approved" if exists else "rejected"}">{escape(status)}</span>'
        title_map = {"logo": "PROMOTORS SHOW (logo.png)", "adrenaline": "Adrenaline Rush (adrenaline.png)"}
        title = title_map.get(key, key)

        brand_cards += (
            '<div class="asset-card">'
            f'<div class="preview">{img_tag}</div>'
            f'<div class="meta"><div class="name">{escape(title)}</div>'
            f'<div class="muted" style="font-size:12px;margin-top:4px">Ключ: <code>{escape(key)}</code> {badge}</div></div>'
            '<div class="actions">'
            f'<form method="post" action="/ticket-assets/brand/upload" enctype="multipart/form-data" style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;width:100%">'
            f'<input type="hidden" name="brand_name" value="{escape(key)}">'
            '<input type="file" name="file" accept="image/*" required style="flex:1;min-width:120px">'
            '<button class="btn btn-primary btn-small" type="submit">Загрузить</button>'
            '</form>'
        )
        if is_runtime:
            brand_cards += (
                f'<form method="post" action="/ticket-assets/brand/delete" style="margin-top:6px">'
                f'<input type="hidden" name="brand_name" value="{escape(key)}">'
                '<button class="btn btn-reject btn-small" type="submit" onclick="return confirm(\'Удалить логотип?\')">🗑 Удалить</button>'
                '</form>'
            )
        brand_cards += '</div></div>'

    brand_section = (
        '<div class="section"><h2>🏷 Главные логотипы билета</h2>'
        '<p class="muted">Эти два логотипа показываются вверху постера. Загрузите прозрачный PNG ~1200px шириной.</p>'
        f'<div class="asset-grid">{brand_cards}</div></div>'
    )

    # Partner checklist
    partners = inventory.get("partners", [])
    _SOURCE_RU = {"runtime": "загружен", "bundled": "из репозитория", None: "❌ НЕ ЗАГРУЖЕН"}
    partner_rows = ""
    for p in partners:
        src = _SOURCE_RU.get(p.get("source"), "—")
        icon = "✅" if p.get("source") else "❌"
        partner_rows += (
            f'<tr><td>{icon} <code>{escape(p["name"])}</code></td>'
            f'<td>{escape(p["title"])}</td>'
            f'<td>{escape(src)}</td></tr>'
        )
    partner_table = (
        '<div class="section"><h2>🤝 Ожидаемые партнёрские логотипы</h2>'
        '<p class="muted">Рекомендуемый набор — эти 4 логотипа показывают в полосе наверху билета. '
        'Порядок задаётся цифрой в начале имени: 1_, 2_, 3_, 4_…</p>'
        '<table><thead><tr><th>Имя</th><th>Описание</th><th>Статус</th></tr></thead>'
        f'<tbody>{partner_rows}</tbody></table></div>'
    )

    # Sponsor logos grid
    sponsor_cards = ""
    for s in sponsors:
        fname = s["filename"]
        name = s["name"]
        size_kb = f"{s['size']//1024} KB" if s["size"] > 1024 else f"{s['size']} B"
        src = f"/assets/file/sponsors/{escape(fname)}?ts={ts}"
        sponsor_cards += (
            '<div class="asset-card">'
            f'<div class="preview"><img src="{src}" alt="{escape(name)}"></div>'
            f'<div class="meta"><div class="name">{escape(fname)}</div>'
            f'<div class="muted" style="font-size:12px">Имя: <code>{escape(name)}</code><br>{size_kb}'
            f' {"· загружен" if s["is_runtime"] else "· из репозитория"}</div></div>'
            '<div class="actions">'
            f'<form method="post" action="/ticket-assets/sponsor/delete" style="display:inline">'
            f'<input type="hidden" name="name" value="{escape(name)}">'
            '<button class="btn btn-reject btn-small" type="submit" '
            'onclick="return confirm(\'Удалить логотип спонсора?\')">🗑 Удалить</button>'
            '</form></div></div>'
        )
    if not sponsor_cards:
        sponsor_cards = '<p class="muted">Пока нет логотипов спонсоров. Загрузите хотя бы один — он сразу появится на билете.</p>'

    upload_sponsor_form = (
        '<div class="upload-box">'
        '<h3>➕ Добавить логотип спонсора / хомий логосини қўшиш</h3>'
        '<p class="muted" style="font-size:13px">Имя задаёт порядок на билете. Используйте префикс: '
        '<code>1_</code>, <code>2_</code> и т.д. Только латиница, цифры, _ и -.<br>'
        'Например: <code>1_mcs_sherdor</code>, <code>2_retro_tashkent</code>, <code>5_my_sponsor</code></p>'
        '<form method="post" action="/ticket-assets/sponsor/upload" enctype="multipart/form-data" '
        'style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">'
        '<div><label style="display:block;font-weight:600;margin-bottom:4px">Имя логотипа</label>'
        '<input type="text" name="name" placeholder="1_mcs_sherdor" required pattern="[A-Za-z0-9_-]{1,40}" '
        'style="min-width:200px"></div>'
        '<div><label style="display:block;font-weight:600;margin-bottom:4px">Файл (PNG/JPG/WEBP)</label>'
        '<input type="file" name="file" accept="image/*" required></div>'
        '<button class="btn btn-primary" type="submit">Загрузить</button>'
        '</form></div>'
    )

    sponsors_section = (
        '<div class="section"><h2>🏢 Логотипы спонсоров / ҳомийлар (полоса наверху билета)</h2>'
        '<p class="muted">Эти логотипы показываются в чёрной полосе наверху билета, как на промо-баннерах мероприятия. '
        'До 10 логотипов — если их много, полоса автоматически разбивается на 2 ряда.</p>'
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
        img_tag = f'<img src="{src}" alt="{escape(slug)}">' if exists else '<span class="muted">Нет баннера</span>'
        status = "загружен" if is_runtime else ("из репозитория" if exists else "❌ нет")
        dir_cards += (
            '<div class="asset-card">'
            f'<div class="preview">{img_tag}</div>'
            f'<div class="meta"><div class="name">{escape(canon)}</div>'
            f'<div class="muted" style="font-size:12px">slug: <code>{escape(slug)}</code> · {escape(status)}</div></div>'
            '<div class="actions">'
            f'<form method="post" action="/ticket-assets/direction/upload" enctype="multipart/form-data" '
            'style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;width:100%">'
            f'<input type="hidden" name="slug" value="{escape(slug)}">'
            '<input type="file" name="file" accept="image/*" required style="flex:1;min-width:100px">'
            '<button class="btn btn-primary btn-small" type="submit">Загрузить</button>'
            '</form>'
        )
        if is_runtime:
            dir_cards += (
                f'<form method="post" action="/ticket-assets/direction/delete" style="margin-top:6px">'
                f'<input type="hidden" name="slug" value="{escape(slug)}">'
                '<button class="btn btn-reject btn-small" type="submit" onclick="return confirm(\'Удалить баннер?\')">🗑 Удалить</button>'
                '</form>'
            )
        dir_cards += '</div></div>'

    dir_section = (
        '<div class="section"><h2>🎨 Баннеры направлений</h2>'
        '<p class="muted">Показываются участнику при выборе направления. Не обязательны, но делают бот красивее.</p>'
        f'<div class="asset-grid">{dir_cards}</div></div>'
    )

    body = (
        notice
        + preview_html
        + brand_section
        + partner_table
        + sponsors_section
        + dir_section
        + '<div class="section"><h2>ℹ️ Как это работает</h2>'
        '<ul style="font-size:14px;line-height:1.6">'
        '<li><b>Загруженные файлы живут на volume</b> — переживают рестарты и деплои, без коммита в git.</li>'
        '<li>Если загружен хотя бы один спонсорский логотип через админку, <b>используются только загруженные</b> — из репозитория скрываются.</li>'
        '<li>Порядок логотипов — по имени файла (алфавит). Используйте префиксы <code>1_</code>, <code>2_</code> для сортировки.</li>'
        '<li>Рекомендуется <b>прозрачный PNG</b> — логотип ляжет на чёрный фон полосы без белого квадрата.</li>'
        '<li>Можно по-прежнему загружать через Telegram: отправьте файл с подписью <code>/logo 1_mcs_sherdor</code> в модерационный чат.</li>'
        '</ul></div>'
    )
    return _page("Билеты и логотипы", body, active="ticket")


# ---------------------------------------------------------------------------
# Multi-tenant control-plane pages
# ---------------------------------------------------------------------------

def tenant_selector_login_page(tenants, error: bool = False) -> str:
    """Render the public tenant chooser followed by that tenant's password."""
    err = '<div class="err">Неверный tenant или пароль</div>' if error else ""
    options = ''.join(
        f'<option value="{escape(t.slug)}">{escape(t.name)} ({escape(t.slug)})</option>'
        for t in tenants
    )
    disabled = " disabled" if not options else ""
    no_tenants = (
        '<p class="muted">Активных tenants пока нет. Войдите как super admin и создайте первый.</p>'
        if not options else ""
    )
    body = (
        '<div class="login-wrap"><div class="section">'
        '<h2>Вход в tenant админ-панель</h2>' + err + no_tenants +
        '<form method="post" action="/login">'
        '<div style="margin-bottom:12px"><label class="muted">Проект</label>'
        f'<select name="slug" required style="width:100%;padding:8px 10px;border:1px solid #ccc;border-radius:8px">{options}</select></div>'
        '<div style="margin-bottom:12px"><input type="password" name="password" '
        'placeholder="Пароль tenant" style="width:100%" autofocus></div>'
        f'<button class="btn btn-primary" type="submit" style="width:100%"{disabled}>Войти</button>'
        '</form><p class="muted" style="font-size:13px;margin-top:16px">'
        '<a href="/super-admin/login">Super admin</a></p></div></div>'
    )
    return _page("Вход в tenant", body, nav=False)


def tenant_login_page(tenant, error: bool = False) -> str:
    """Render a password login constrained to one tenant slug."""
    err = '<div class="err">Неверный пароль</div>' if error else ""
    slug = escape(tenant.slug)
    body = (
        '<div class="login-wrap"><div class="section">'
        f'<h2>{escape(tenant.name)} — админ-панель</h2><p class="muted">Tenant: <code>{slug}</code></p>'
        + err + f'<form method="post" action="/t/{slug}/login">'
        '<div style="margin-bottom:12px"><input type="password" name="password" '
        'placeholder="Пароль" style="width:100%" autofocus></div>'
        '<button class="btn btn-primary" type="submit" style="width:100%">Войти</button>'
        '</form><p class="muted" style="font-size:13px;margin-top:16px"><a href="/login">← Другой tenant</a></p>'
        '</div></div>'
    )
    return _page("Вход", body, nav=False)


def tenant_inactive_page() -> str:
    """Explain why an archived tenant cannot issue a tenant-admin session."""
    body = (
        '<div class="login-wrap"><div class="section"><h2>Tenant неактивен</h2>'
        '<p class="muted">Доступ приостановлен super admin. Обратитесь к владельцу платформы.</p>'
        '</div></div>'
    )
    return _page("Tenant неактивен", body, nav=False)


def super_panel_disabled_page() -> str:
    """Page shown when no process-level super-admin secret is configured."""
    body = (
        '<div class="login-wrap"><div class="section"><h2>Super admin отключён</h2>'
        '<p class="muted">Задайте секрет <code>SUPER_ADMIN_PASSWORD</code> для доступа к управлению tenants.</p>'
        '</div></div>'
    )
    return _page("Super admin", body, nav=False)


def super_login_page(error: bool = False) -> str:
    """Render the process-level super-admin login form."""
    err = '<div class="err">Неверный пароль</div>' if error else ""
    body = (
        '<div class="login-wrap"><div class="section"><h2>Super admin</h2>' + err +
        '<form method="post" action="/super-admin/login">'
        '<div style="margin-bottom:12px"><input type="password" name="password" '
        'placeholder="SUPER_ADMIN_PASSWORD" style="width:100%" autofocus></div>'
        '<button class="btn btn-primary" type="submit" style="width:100%">Войти</button>'
        '</form><p class="muted" style="font-size:13px;margin-top:16px"><a href="/login">Tenant login</a></p>'
        '</div></div>'
    )
    return _page("Super admin", body, nav=False)


def _super_page(title: str, body: str) -> str:
    header = (
        '<header><span class="brand">🛡 Multi-tenant Control Plane</span>'
        '<nav><a href="/super-admin/">Tenants</a> '
        '<a href="/super-admin/tenants/new">➕ Новый tenant</a></nav>'
        '<span class="spacer"></span><a href="/super-admin/logout" style="color:#ddd6fe">Выйти</a></header>'
    )
    return (
        "<!doctype html><html lang='ru'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head>"
        f"<body>{header}<main>{body}</main></body></html>"
    )


def super_dashboard_page(tenants, application_counts: dict[int, int]) -> str:
    """Render all tenants with aggregate application counts and safe token status."""
    rows = ""
    for tenant in tenants:
        active = "✅ active" if tenant.is_active else "⏸ inactive"
        token = "*** configured" if tenant.token_configured else "— missing"
        password = "configured" if tenant.password_configured else "— missing"
        slug = escape(tenant.slug)
        rows += (
            '<tr>'
            f'<td><b>{escape(tenant.name)}</b><br><code>{slug}</code></td>'
            f'<td>{active}</td><td>{token}</td><td>{password}</td>'
            f'<td>{application_counts.get(tenant.id, 0)}</td>'
            '<td style="white-space:nowrap">'
            f'<a class="btn btn-ghost btn-small" href="/super-admin/tenants/{slug}/edit">Изменить</a> '
            f'<a class="btn btn-ghost btn-small" href="/super-admin/tenants/{slug}/diag">Диагностика</a> '
            f'<a class="btn btn-ghost btn-small" href="/t/{slug}/">Открыть</a>'
            f'<form method="post" action="/super-admin/tenants/{slug}/toggle" style="display:inline">'
            f'<button class="btn btn-small" type="submit">{"Пауза" if tenant.is_active else "Включить"}</button></form> '
            f'<form method="post" action="/super-admin/tenants/{slug}/archive" style="display:inline">'
            '<button class="btn btn-reject btn-small" type="submit" '
            'onclick="return confirm(\'Архивировать tenant? Данные сохранятся, polling остановится.\')">Архив</button></form>'
            '</td></tr>'
        )
    if not rows:
        rows = '<tr><td colspan="6" class="muted">Tenants не найдены</td></tr>'
    body = (
        '<div class="cards">' + _stat_card(len(tenants), "Всего tenants") +
        _stat_card(sum(application_counts.values()), "Всего заявок") + '</div>'
        '<div class="section"><div style="display:flex;justify-content:space-between;gap:12px;align-items:center">'
        '<h2>Tenants</h2><a class="btn btn-primary" href="/super-admin/tenants/new">➕ Создать tenant</a></div>'
        '<p class="muted">Токены никогда не выводятся в браузер: только статус <code>***</code>. '
        'Изменение tenant автоматически перезапускает только его polling worker.</p>'
        '<div style="overflow-x:auto"><table><thead><tr><th>Tenant</th><th>Статус</th><th>Bot token</th>'
        '<th>Admin пароль</th><th>Заявки</th><th></th></tr></thead><tbody>' + rows +
        '</tbody></table></div></div>'
    )
    return _super_page("Tenants", body)


def _form_value(values: dict | None, tenant, field: str, default: str = "") -> str:
    if values is not None and field in values:
        value = values[field]
    elif tenant is not None:
        value = getattr(tenant, field, default)
    else:
        value = default
    return escape(str(value if value is not None else ""))


def super_tenant_form_page(tenant=None, values: dict | None = None, error: str = "") -> str:
    """Render create/edit form without ever including an actual bot token."""
    editing = tenant is not None
    slug = _form_value(values, tenant, "slug")
    action = f'/super-admin/tenants/{escape(tenant.slug)}/edit' if editing else '/super-admin/tenants/new'
    error_html = f'<div class="err">{escape(error)}</div>' if error else ""
    checked = False
    if values is not None:
        checked = str(values.get("is_active", "")) in {"1", "true", "on", "True"}
    elif tenant is not None:
        checked = bool(tenant.is_active)
    else:
        checked = True
    active = " checked" if checked else ""
    slug_field = (
        f'<input type="text" name="slug" value="{slug}" required pattern="[a-z0-9-]{{2,64}}" '
        'placeholder="adrenaline" style="width:100%">'
        if not editing else
        f'<input type="text" value="{slug}" disabled style="width:100%"><input type="hidden" name="slug" value="{slug}">'
    )
    token_hint = "Новый token оставьте пустым, чтобы сохранить текущий ***" if editing else "Token @BotFather; хранится encrypted"
    password_hint = "Новый пароль оставьте пустым, чтобы сохранить текущий" if editing else "Пароль tenant admin"
    restart_form = (
        f'<form method="post" action="/super-admin/tenants/{escape(tenant.slug)}/restart" style="display:inline">'
        '<button class="btn btn-ghost" type="submit">Перезапустить tenant</button></form>'
        if editing else ""
    )
    body = (
        f'<p><a href="/super-admin/">← К tenants</a></p><div class="section"><h2>{"Изменить" if editing else "Создать"} tenant</h2>'
        + error_html + f'<form method="post" action="{action}">'
        '<div class="kv" style="grid-template-columns:190px minmax(0,1fr)">'
        f'<div class="k">Slug</div><div>{slug_field}<small class="muted">Slug нельзя менять после создания: он является ключом media-изоляции.</small></div>'
        f'<div class="k">Название</div><div><input type="text" name="name" required value="{_form_value(values, tenant, "name")}" style="width:100%"></div>'
        f'<div class="k">Bot token</div><div><input type="password" name="bot_token" placeholder="{escape(token_hint)}" style="width:100%"><small class="muted">{"*** configured" if editing and tenant.token_configured else token_hint}</small></div>'
        f'<div class="k">Admin chat ID</div><div><input type="text" name="admin_chat_id" value="{_form_value(values, tenant, "admin_chat_id", "0")}" style="width:100%"></div>'
        f'<div class="k">Required channel</div><div><input type="text" name="required_channel" value="{_form_value(values, tenant, "required_channel")}" placeholder="@channel" style="width:100%"></div>'
        f'<div class="k">Channel URL</div><div><input type="text" name="channel_url" value="{_form_value(values, tenant, "channel_url")}" style="width:100%"></div>'
        f'<div class="k">Instagram handle</div><div><input type="text" name="instagram_handle" value="{_form_value(values, tenant, "instagram_handle")}" style="width:100%"></div>'
        f'<div class="k">Instagram URL</div><div><input type="text" name="instagram_url" value="{_form_value(values, tenant, "instagram_url")}" style="width:100%"></div>'
        f'<div class="k">Spreadsheet ID</div><div><input type="text" name="spreadsheet_id" value="{_form_value(values, tenant, "spreadsheet_id")}" style="width:100%"></div>'
        f'<div class="k">Drive folder ID</div><div><input type="text" name="drive_folder_id" value="{_form_value(values, tenant, "drive_folder_id")}" style="width:100%"></div>'
        f'<div class="k">Tenant admin password</div><div><input type="password" name="admin_password" placeholder="{escape(password_hint)}" style="width:100%"><small class="muted">Хранится только PBKDF2 hash.</small></div>'
        f'<div class="k">Активен</div><div><label><input type="checkbox" name="is_active" value="1"{active}> Запускать polling этого tenant</label></div>'
        '</div><div class="actions"><button class="btn btn-primary" type="submit">Сохранить</button></div></form>'
        + (f'<div class="actions">{restart_form}</div>' if restart_form else "")
        + '</div>'
    )
    return _super_page("Tenant form", body)


def tenant_settings_page(tenant, message: str = "", error: str = "") -> str:
    """Render settings tenant admins may change without seeing their bot token."""
    notice = '<div class="ok">Настройки сохранены</div>' if message == "saved" else ""
    notice += f'<div class="err">{escape(error)}</div>' if error else ""
    def field(name: str, label: str, value: str, placeholder: str = "") -> str:
        return (
            f'<div class="k">{escape(label)}</div><div><input type="text" name="{name}" '
            f'value="{escape(str(value or ""))}" placeholder="{escape(placeholder)}" style="width:100%"></div>'
        )
    body = (
        '<div class="section"><h2>⚙️ Настройки tenant</h2>' + notice +
        '<p class="muted">Bot token управляется только super admin и здесь не отображается.</p>'
        '<form method="post" action="/settings"><div class="kv" style="grid-template-columns:190px minmax(0,1fr)">'
        + field("name", "Название", tenant.name)
        + field("admin_chat_id", "Admin chat ID", tenant.admin_chat_id)
        + field("required_channel", "Required channel", tenant.required_channel, "@channel")
        + field("channel_url", "Channel URL", tenant.channel_url)
        + field("instagram_handle", "Instagram handle", tenant.instagram_handle)
        + field("instagram_url", "Instagram URL", tenant.instagram_url)
        + field("spreadsheet_id", "Spreadsheet ID", tenant.spreadsheet_id)
        + field("drive_folder_id", "Drive folder ID", tenant.drive_folder_id)
        + '<div class="k">Новый пароль</div><div><input type="password" name="admin_password" '
        'placeholder="Оставьте пустым, чтобы не менять" style="width:100%"></div>'
        + '</div><div class="actions"><button class="btn btn-primary" type="submit">Сохранить</button></div></form></div>'
    )
    return _page("Настройки", body, active="settings")


def tenant_diag_page(tenant, checks: list[tuple[str, bool, str]]) -> str:
    """Render a per-tenant Telegram configuration diagnostic result."""
    rows = ''.join(
        f'<tr><td>{"✅" if ok else "❌"} {escape(label)}</td><td>{escape(detail)}</td></tr>'
        for label, ok, detail in checks
    ) or '<tr><td colspan="2" class="muted">Нет результатов</td></tr>'
    body = (
        f'<p><a href="/super-admin/tenants/{escape(tenant.slug)}/edit">← {escape(tenant.name)}</a></p>'
        f'<div class="section"><h2>Диагностика: {escape(tenant.name)}</h2>'
        '<p class="muted">Проверка выполняется с token tenant в памяти; token не выводится.</p>'
        '<table><thead><tr><th>Проверка</th><th>Результат</th></tr></thead><tbody>' + rows +
        '</tbody></table></div>'
    )
    return _super_page("Tenant diagnostics", body)
