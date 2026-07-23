"""Server-rendered HTML for the admin panel (no external template engine)."""
from __future__ import annotations

from html import escape
from typing import Iterable, Optional

from ..constants import SIDES, SIDE_LABELS_RU
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
header nav a { color: #ddd6fe; font-weight: 500; }
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
input[type=text], input[type=password] { padding: 8px 10px; border: 1px solid #ccc; border-radius: 8px;
                                          font-size: 14px; }
.btn { display: inline-block; padding: 9px 16px; border-radius: 8px; border: none; cursor: pointer;
       font-size: 14px; font-weight: 600; }
.btn-primary { background: #5b21b6; color: #fff; }
.btn-approve { background: #059669; color: #fff; }
.btn-reject { background: #dc2626; color: #fff; }
.btn-ghost { background: #ede9fe; color: #5b21b6; }
.photos { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.photos figure { margin: 0; }
.photos img { width: 100%; border-radius: 10px; border: 1px solid #ddd; }
.photos figcaption { color: #6b7280; font-size: 13px; margin-top: 4px; }
.kv { display: grid; grid-template-columns: 160px 1fr; gap: 8px 16px; font-size: 15px; }
.kv .k { color: #6b7280; }
.actions { margin-top: 18px; display: flex; gap: 10px; }
.login-wrap { max-width: 340px; margin: 80px auto; }
.err { background: #fee2e2; color: #991b1b; padding: 10px 12px; border-radius: 8px; margin-bottom: 12px; }
.muted { color: #6b7280; }
.bar { display:flex; align-items:center; gap:8px; margin:6px 0; }
.bar .track { flex:1; height:10px; background:#ede9fe; border-radius:6px; overflow:hidden; }
.bar .fill { height:100%; background:#7c3aed; }
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
            f'{link("/export.csv", "Экспорт CSV", "export")}</nav>'
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
            f'<div class="bar"><div style="width:130px">{escape(str(name))}</div>'
            f'<div class="track"><div class="fill" style="width:{pct}%"></div></div>'
            f'<div class="muted" style="width:60px;text-align:right">{n} ({pct}%)</div></div>'
        )
    return f'<div class="section"><h2>{escape(title)}</h2>{rows}</div>'


def dashboard_page(stats: dict) -> str:
    cards = (
        '<div class="cards">'
        + _stat_card(stats["total"], "Всего заявок")
        + _stat_card(stats["pending"], "На рассмотрении")
        + _stat_card(stats["approved"], "Одобрено")
        + _stat_card(stats["rejected"], "Отклонено")
        + _stat_card(f'№{stats["max_number"]}', "Последний номер")
        + '</div>'
    )
    body = (
        cards
        + _distribution("По направлениям", stats["by_direction"])
        + _distribution("По странам", stats["by_country"])
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


def application_detail_page(app: Application) -> str:
    photos = ""
    for i, _ in enumerate(app.photo_paths):
        side = SIDES[i] if i < len(SIDES) else str(i + 1)
        caption = SIDE_LABELS_RU.get(side, side)
        photos += (
            f'<figure><img src="/photo/{app.id}/{i}" alt="{escape(caption)}">'
            f'<figcaption>{escape(caption)} сторона</figcaption></figure>'
        )
    photos_html = f'<div class="photos">{photos}</div>' if photos else '<p class="muted">Нет фотографий</p>'

    number = f'№{app.reg_number}' if app.reg_number is not None else "—"
    kv = (
        '<div class="kv">'
        f'<div class="k">Статус</div><div>{_status_badge(app.status)}</div>'
        f'<div class="k">Рег. номер</div><div>{number}</div>'
        f'<div class="k">Страна</div><div>{escape(app.country)}</div>'
        f'<div class="k">Гос. номер</div><div><b>{escape(app.plate)}</b></div>'
        f'<div class="k">Направление</div><div>{escape(app.direction)}</div>'
        f'<div class="k">Телефон</div><div>{escape(app.phone)}</div>'
        f'<div class="k">Пользователь</div><div>{escape(app.username)}</div>'
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
    )
    return _page(f"Заявка #{app.id}", body, active="apps")
