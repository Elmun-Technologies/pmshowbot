"""Centralized RU/UZ localization for the admin panel.

Every user-visible admin-panel string lives in :data:`_STRINGS` under a stable
dot-separated key (``"tenant.create.title"``).  Views and handlers call
:func:`t` with the request locale and never branch on ``if lang == ...``
themselves.

Locale rules:
- only ``"ru"`` and ``"uz"`` are supported; anything else — including values
  taken from a query string or cookie — safely falls back to ``"ru"``
  (:func:`normalize_lang`), which is also the dictionary fallback language;
- the locale is carried by the ``pm_lang`` cookie plus an optional validated
  ``?lang=`` query parameter handled by the admin server middleware.

The switcher markup produced by :func:`lang_switcher` uses only whitelisted
locale codes in its ``href`` values, so neither XSS nor open redirects are
possible through the language parameter.  Bot tokens and other secrets are
never passed through this module.
"""
from __future__ import annotations

from html import escape
from urllib.parse import urlencode

# ---------------------------------------------------------------------------
# Locale constants
# ---------------------------------------------------------------------------

DEFAULT_LANG = "ru"
SUPPORTED_LANGS = ("ru", "uz")
LANG_COOKIE = "pm_lang"

_UZ_SWITCH_LABEL = "O‘Z"
_RU_SWITCH_LABEL = "RU"


def is_supported(value: object) -> bool:
    """True only for the exact supported locale codes."""
    return value in SUPPORTED_LANGS


def normalize_lang(value: object) -> str:
    """Map any raw locale value onto a safe supported language.

    Unknown, empty or hostile values (query params, cookies) all resolve to
    the Russian default rather than ever reaching a template.
    """
    return value if is_supported(value) else DEFAULT_LANG


# ---------------------------------------------------------------------------
# Dictionaries.  ``ru`` is the fallback: a key missing from ``uz`` resolves
# through it, so ``t()`` never returns an empty string for a known key.
# ---------------------------------------------------------------------------

_STRINGS: dict[str, dict[str, str]] = {
    # ------------------------------------------------------------------
    # Русский
    # ------------------------------------------------------------------
    "ru": {
        # --- Generic / shared ---
        "common.save": "Сохранить",
        "common.login": "Войти",
        "common.logout": "Выйти",
        "common.open": "Открыть",
        "common.delete": "🗑 Удалить",
        "common.upload": "Загрузить",
        "common.back": "Назад",
        "common.yes": "Да",
        "common.active": "Активен",
        "common.search": "Найти",
        "common.kb": "КБ",
        "common.bytes": "Б",
        "common.not_set": "— не задано",
        "common.configured": "задан",
        "common.from_repo": "из репозитория",
        "common.uploaded": "загружен",
        "common.missing": "❌ нет",

        # --- Navigation (tenant panel) ---
        "nav.brand_suffix": "Админ",
        "nav.dashboard": "Дашборд",
        "nav.applications": "Заявки",
        "nav.tickets": "🎫 Билеты",
        "nav.broadcast": "📢 Рассылка",
        "nav.settings": "⚙️ Настройки",
        "nav.export_excel": "📊 Excel",
        "nav.export_csv": "CSV",

        # --- Language switcher ---
        "lang.title": "Интерфейс языка",

        # --- Legacy root login ---
        "login.title": "Вход в админ-панель",
        "login.page_title": "Вход",
        "login.wrong_password": "Неверный пароль",
        "login.password_placeholder": "Пароль",

        # --- Panel disabled / inactive states ---
        "disabled.panel.title": "Панель отключена",
        "disabled.panel.body": (
            "Задайте секрет <code>ADMIN_PASSWORD</code>, чтобы включить админ-панель."
        ),
        "disabled.super.title": "Super admin отключён",
        "disabled.super.body": (
            "Задайте секрет <code>SUPER_ADMIN_PASSWORD</code> для доступа "
            "к управлению tenants."
        ),
        "tenant_inactive.title": "Tenant неактивен",
        "tenant_inactive.body": (
            "Доступ приостановлен super admin. Обратитесь к владельцу платформы."
        ),

        # --- Tenant dashboard ---
        "dash.page_title": "Дашборд",
        "dash.total_apps": "Всего заявок",
        "dash.pending": "На рассмотрении",
        "dash.approved": "Одобрено",
        "dash.approved_users": "Одобр. участники",
        "dash.rejected": "Отклонено",
        "dash.last_number": "Последний номер",
        "dash.download_excel": "📥 Скачать Excel (.xlsx)",
        "dash.manage_tickets": "🎫 Управление билетами",
        "dash.by_direction": "📊 По направлениям",
        "dash.by_country": "🌍 По странам",
        "dash.by_language": "🌐 По языкам",
        "dash.by_date": "📅 Заявки по дням (последние 14 дней)",

        # --- Statuses ---
        "status.pending": "На рассмотрении",
        "status.approved": "Одобрено",
        "status.rejected": "Отклонено",

        # --- Applications list ---
        "apps.page_title": "Заявки",
        "apps.heading": "Заявки ({n})",
        "apps.filter_all": "Все",
        "apps.search_placeholder": "Поиск: номер, телефон…",
        "apps.col_number": "Номер",
        "apps.col_photo": "Фото",
        "apps.col_country": "Страна",
        "apps.col_plate": "Гос. номер",
        "apps.col_direction": "Направление",
        "apps.col_phone": "Телефон",
        "apps.col_user": "Пользователь",
        "apps.col_status": "Статус",
        "apps.empty": "Заявок нет",

        # --- Application detail ---
        "detail.page_title": "Заявка #{id}",
        "detail.back_to_apps": "← Назад к заявкам",
        "detail.heading": "Заявка #{id}",
        "detail.col_status": "Статус",
        "detail.col_reg_number": "Рег. номер",
        "detail.col_country": "Страна",
        "detail.col_plate": "Гос. номер",
        "detail.col_direction": "Направление",
        "detail.col_car_mods": "Изменения в авто",
        "detail.col_phone": "Телефон",
        "detail.col_user": "Пользователь",
        "detail.col_language": "Язык",
        "detail.col_submitted": "Подана",
        "detail.col_processed": "Обработана",
        "detail.photos": "Фотографии",
        "detail.photos_empty": "Нет фотографий",
        "detail.photo_side": "{side} сторона",
        "detail.mod_number": "Изменение {n}",
        "detail.mods": "Изменения в автомобиле",
        "detail.mods_empty": "Участник не отметил изменений",
        "detail.badge_photo": "Фото на бейдж",
        "detail.badge_empty": "Участник ещё не прислал фото для бейджа",
        "detail.accept": "✅ Принять",
        "detail.reject": "❌ Отклонить",

        # --- Status control (detail page) ---
        "statusctl.title": "🔀 Управление статусом",
        "statusctl.current": "Текущий статус: ",
        "statusctl.notice": (
            "Изменение статуса отправит участнику уведомление в Telegram "
            "(при переводе в «Одобрено» — также билет и номер)."
        ),
        "statusctl.confirm": "Изменить статус и уведомить участника в Telegram?",
        "statusctl.changed": "✅ Статус изменён",
        "statusctl.approve_btn": "✅ Одобрено",
        "statusctl.reject_btn": "❌ Отклонено",
        "statusctl.pending_btn": "⏳ На рассмотрении",

        # --- Individual message (detail page) ---
        "msg.title": "✉️ Личное сообщение",
        "msg.hint": "Сообщение уйдёт только этому пользователю, тем же ботом.",
        "msg.placeholder": "Текст сообщения…",
        "msg.send": "📩 Отправить",
        "msg.sent": "✅ Сообщение отправлено",

        # --- Errors / flashes (server-side) ---
        "error.app_not_found": "Заявка не найдена",
        "error.tenant_not_found": "Tenant не найден",
        "error.photo_not_on_disk": "Фото не найдено на диске",
        "error.msg_blocked": "Не удалось отправить — пользователь заблокировал бота",
        "error.msg_empty": "Введите текст сообщения",
        "error.status_change": "Не удалось изменить статус",

        # --- Broadcast ---
        "bcast.page_title": "Рассылка",
        "bcast.title": "Рассылка в Telegram",
        "bcast.intro": (
            "Выберите аудиторию и напишите текст. Сообщение уйдёт тем же ботом, "
            "которым они писали. Один пользователь — одно сообщение."
        ),
        "bcast.done": "Рассылка завершена",
        "bcast.audience_label": "Аудитория: ",
        "bcast.sent": "Отправлено: ",
        "bcast.failures": "ошибки / блок бота: ",
        "bcast.total": "всего адресатов: ",
        "bcast.sent_uz": "🇺🇿 на узбекском: ",
        "bcast.sent_ru": "🇷🇺 на русском: ",
        "bcast.with_photo": " · с фото",
        "bcast.preview_count": "По выбранным фильтрам получателей: <b>{n}</b>",
        "bcast.audience_heading": "Аудитория",
        "bcast.audience.approved": "Одобрено — успешная регистрация",
        "bcast.audience.pending": "На рассмотрении — заявка ещё не решена",
        "bcast.audience.rejected": "Отклонено — регистрация не принята",
        "bcast.audience.incomplete": "Не завершили регистрацию (открыли бота, заявки нет)",
        "bcast.audience.all_apps": "Все, кто подал заявку",
        "bcast.audience.starters": "Все, кого бот уже знает",
        "bcast.people": "{n} чел.",
        "bcast.by_language": "Уточнить по языку",
        "bcast.only_uz": "🇺🇿 Только узбекский",
        "bcast.only_ru": "🇷🇺 Только русский",
        "bcast.by_direction": "Уточнить по направлению",
        "bcast.text_uz_hint": "Тем, кто выбрал узбекский язык",
        "bcast.text_ru_hint": "Всем остальным получателям",
        "bcast.text_ru_placeholder": "Текст сообщения…",
        "bcast.one_field_hint": (
            "Если заполнено только одно поле — этот текст уйдёт всем получателям."
        ),
        "bcast.photo_uz": "🇺🇿 Фото к посту — O‘zbekcha",
        "bcast.photo_ru": "🇷🇺 Фото к посту — Русский",
        "bcast.photo_uz_hint": (
            "необязательно — если не загружено, возьмётся фото из русской версии"
        ),
        "bcast.photo_ru_hint": (
            "необязательно — если не загружено, возьмётся фото из узбекской версии"
        ),
        "bcast.caption_limit": (
            "Максимум 1024 символа в тексте того языка, для которого прикреплено фото "
            "(ограничение Telegram на подпись)."
        ),
        "bcast.preview_title": "👁 Предпросмотр поста",
        "bcast.preview_empty": "Пусто",
        "bcast.confirm": "Да, отправить выбранной аудитории",
        "bcast.count_btn": "🔍 Показать количество",
        "bcast.send_btn": "📢 Отправить",
        "bcast.note_incomplete": (
            "«Не завершили регистрацию» — те, кто нажал /start после обновления бота, "
            "но заявку так и не отправил. Старых брошенных анкет в базе нет."
        ),
        "bcast.note_filters": (
            "Если снять все языки или все направления — фильтр по этому признаку "
            "не применяется."
        ),
        "bcast.err_no_text": "Введите текст хотя бы на одном языке",
        "bcast.err_no_confirm": "Подтвердите отправку галочкой",
        "bcast.err_no_bot": "Бот недоступен — рассылка невозможна",
        "bcast.err_uz_too_long": (
            "Текст на узбекском слишком длинный для сообщения с фото — "
            "у Telegram лимит подписи 1024 символа"
        ),
        "bcast.err_ru_too_long": (
            "Текст на русском слишком длинный для сообщения с фото — "
            "у Telegram лимит подписи 1024 символа"
        ),
        "bcast.err_no_recipients": "По выбранным фильтрам получателей не найдено",

        # --- Ticket assets page ---
        "assets.page_title": "Билеты и логотипы",
        "assets.msg.brand_uploaded": "✅ Логотип бренда обновлён",
        "assets.msg.brand_deleted": (
            "🗑 Логотип бренда удалён (теперь используется версия из репозитория, "
            "если есть)"
        ),
        "assets.msg.sponsor_uploaded": "✅ Логотип спонсора сохранён",
        "assets.msg.sponsor_deleted": "🗑 Логотип спонсора удалён",
        "assets.msg.direction_uploaded": "✅ Баннер направления сохранён",
        "assets.msg.direction_deleted": "🗑 Баннер направления удалён",
        "assets.err.no_file": "Файл не выбран",
        "assets.err.name_required": "Введите имя файла (только латиница, цифры, _ и -)",
        "assets.err.invalid_name": (
            "Имя может содержать только латиницу, цифры, _ и - (до 40 символов)"
        ),
        "assets.err.unknown_brand": "Неизвестный бренд",
        "assets.err.unknown_direction": "Неизвестное направление",
        "assets.err_generic": "Не удалось сохранить файл",
        "assets.preview.title": "🎫 Предпросмотр билета",
        "assets.preview.hint": (
            "Так будет выглядеть билет с текущими логотипами. Фон — заглушка "
            "(градиент), в реальности за ним фото авто участника."
        ),
        "assets.preview.reload_hint": (
            "После загрузки/удаления логотипа обновите страницу — предпросмотр "
            "перегенерируется автоматически. Если загружен хотя бы один спонсорский "
            "логотип из админки, используются только загруженные "
            "(из репозитория скрываются)."
        ),
        "assets.preview.full_size": "Открыть в полном размере",
        "assets.preview.refresh": "🔄 Обновить",
        "assets.brand.title": "🏷 Главные логотипы билета",
        "assets.brand.hint": (
            "Эти два логотипа показываются вверху постера. Загрузите прозрачный "
            "PNG ~1200px шириной."
        ),
        "assets.brand.no_logo": "Нет логотипа",
        "assets.brand.key": "Ключ: ",
        "assets.brand.confirm_delete": "Удалить логотип?",
        "assets.partners.title": "🤝 Ожидаемые партнёрские логотипы",
        "assets.partners.hint": (
            "Рекомендуемый набор — эти 4 логотипа показывают в полосе наверху "
            "билета. Порядок задаётся цифрой в начале имени: 1_, 2_, 3_, 4_…"
        ),
        "assets.partners.col_name": "Имя",
        "assets.partners.col_description": "Описание",
        "assets.partners.col_status": "Статус",
        "assets.sponsors.title": "🏢 Логотипы спонсоров (полоса наверху билета)",
        "assets.sponsors.hint": (
            "Эти логотипы показываются в чёрной полосе наверху билета, как на "
            "промо-баннерах мероприятия. До 10 логотипов — если их много, полоса "
            "автоматически разбивается на 2 ряда."
        ),
        "assets.sponsors.name": "Имя: ",
        "assets.sponsors.empty": (
            "Пока нет логотипов спонсоров. Загрузите хотя бы один — он сразу "
            "появится на билете."
        ),
        "assets.sponsors.add": "➕ Добавить логотип спонсора",
        "assets.sponsors.add_hint": (
            "Имя задаёт порядок на билете. Используйте префикс: <code>1_</code>, "
            "<code>2_</code> и т.д. Только латиница, цифры, _ и -."
        ),
        "assets.sponsors.add_example": (
            "Например: <code>1_mcs_sherdor</code>, <code>2_retro_tashkent</code>, "
            "<code>5_my_sponsor</code>"
        ),
        "assets.sponsors.label_name": "Имя логотипа",
        "assets.sponsors.label_file": "Файл (PNG/JPG/WEBP)",
        "assets.sponsors.confirm_delete": "Удалить логотип спонсора?",
        "assets.directions.title": "🎨 Баннеры направлений",
        "assets.directions.hint": (
            "Показываются участнику при выборе направления. Не обязательны, "
            "но делают бот красивее."
        ),
        "assets.directions.no_banner": "Нет баннера",
        "assets.directions.confirm_delete": "Удалить баннер?",
        "assets.how.title": "ℹ️ Как это работает",
        "assets.how.1": (
            "<b>Загруженные файлы живут на volume</b> — переживают рестарты "
            "и деплои, без коммита в git."
        ),
        "assets.how.2": (
            "Если загружен хотя бы один спонсорский логотип через админку, "
            "<b>используются только загруженные</b> — из репозитория скрываются."
        ),
        "assets.how.3": (
            "Порядок логотипов — по имени файла (алфавит). Используйте префиксы "
            "<code>1_</code>, <code>2_</code> для сортировки."
        ),
        "assets.how.4": (
            "Рекомендуется <b>прозрачный PNG</b> — логотип ляжет на чёрный фон "
            "полосы без белого квадрата."
        ),
        "assets.how.5": (
            "Можно по-прежнему загружать через Telegram: отправьте файл "
            "с подписью <code>/logo 1_mcs_sherdor</code> в модерационный чат."
        ),

        # --- Photo side labels ---
        "side.left": "Левая",
        "side.right": "Правая",
        "side.front": "Передняя",
        "side.back": "Задняя",

        # --- Tenant selector login (/login) ---
        "selector.page_title": "Вход в tenant",
        "selector.title": "Вход в tenant админ-панель",
        "selector.wrong_credentials": "Неверный tenant или пароль",
        "selector.no_tenants": (
            "Активных tenants пока нет. Войдите как super admin и создайте первый."
        ),
        "selector.project": "Проект",
        "selector.password_placeholder": "Пароль tenant",

        # --- Tenant login (/t/{slug}/login) ---
        "tlogin.page_title": "Вход",
        "tlogin.admin_panel": "админ-панель",
        "tlogin.tenant": "Tenant: ",
        "tlogin.wrong_password": "Неверный пароль",
        "tlogin.password_placeholder": "Пароль",
        "tlogin.other_tenant": "← Другой tenant",

        # --- Super-admin login ---
        "superlogin.page_title": "Super admin",
        "superlogin.title": "Super admin",
        "superlogin.wrong_password": "Неверный пароль",
        "superlogin.password_placeholder": "SUPER_ADMIN_PASSWORD",
        "superlogin.tenant_login": "Tenant login",

        # --- Super-admin layout ---
        "super.brand": "🛡 Multi-tenant Control Plane",
        "super.nav.tenants": "Tenants",
        "super.nav.new": "➕ Новый tenant",

        # --- Super-admin dashboard ---
        "superdash.page_title": "Tenants",
        "superdash.total_tenants": "Всего tenants",
        "superdash.total_apps": "Всего заявок",
        "superdash.heading": "Tenants",
        "superdash.create": "➕ Создать tenant",
        "superdash.tokens_note": (
            "Токены никогда не выводятся в браузер: только статус <code>***</code>. "
            "Изменение tenant автоматически перезапускает только его polling worker."
        ),
        "superdash.col_tenant": "Tenant",
        "superdash.col_status": "Статус",
        "superdash.col_token": "Bot token",
        "superdash.col_password": "Admin пароль",
        "superdash.col_apps": "Заявки",
        "superdash.active": "✅ активен",
        "superdash.inactive": "⏸ неактивен",
        "superdash.token_configured": "*** задан",
        "superdash.token_missing": "— не задан",
        "superdash.password_configured": "задан",
        "superdash.password_missing": "— не задан",
        "superdash.edit": "Изменить",
        "superdash.diagnostics": "Диагностика",
        "superdash.pause": "Пауза",
        "superdash.enable": "Включить",
        "superdash.archive": "Архив",
        "superdash.confirm_archive": (
            "Архивировать tenant? Данные сохранятся, polling остановится."
        ),
        "superdash.empty": "Tenants не найдены",

        # --- Tenant create/edit form ---
        "tenant.create.title": "Создать tenant",
        "tenant.edit.title": "Изменить tenant",
        "tenant.form.back": "← К tenants",
        "tenant.form.page_title": "Tenant form",
        "tenant.form.slug": "Slug",
        "tenant.form.slug_hint": (
            "Slug нельзя менять после создания: он является ключом media-изоляции."
        ),
        "tenant.form.name": "Название",
        "tenant.form.bot_token": "Токен бота (Bot token)",
        "tenant.form.token_hint_new": "Token от @BotFather; хранится в зашифрованном виде",
        "tenant.form.token_hint_edit": (
            "Оставьте пустым, чтобы сохранить текущий ***"
        ),
        "tenant.form.token_configured": "*** задан",
        "tenant.form.admin_chat_id": "ID чата модерации (Admin chat ID)",
        "tenant.form.required_channel": "Обязательный канал (Required channel)",
        "tenant.form.channel_url": "Ссылка на канал (Channel URL)",
        "tenant.form.instagram_handle": "Instagram аккаунт (handle)",
        "tenant.form.instagram_url": "Ссылка на Instagram (Instagram URL)",
        "tenant.form.spreadsheet_id": "Google Sheets ID (Spreadsheet ID)",
        "tenant.form.drive_folder_id": "Google Drive папка (Drive folder ID)",
        "tenant.form.admin_password": "Пароль tenant-админа",
        "tenant.form.password_hint_new": "Пароль администратора tenant-панели",
        "tenant.form.password_hint_edit": (
            "Оставьте пустым, чтобы сохранить текущий пароль"
        ),
        "tenant.form.password_stored": "Хранится только PBKDF2-хеш пароля.",
        "tenant.form.is_active": "Активен",
        "tenant.form.is_active_hint": "Запускать polling этого tenant",
        "tenant.form.restart": "Перезапустить tenant",

        # --- Tenant form errors (mapped from ValueError/EncryptionError) ---
        "tenant.form.err_slug": (
            "Slug может содержать только строчные латинские буквы, цифры и дефисы"
        ),
        "tenant.form.err_name": "Укажите название tenant",
        "tenant.form.err_chat_id": "Admin chat ID должен быть целым числом",
        "tenant.form.err_generic": "Не удалось сохранить tenant: {detail}",

        # --- Tenant settings ---
        "settings.page_title": "Настройки",
        "settings.title": "⚙️ Настройки tenant",
        "settings.saved": "Настройки сохранены",
        "settings.token_note": (
            "Bot token управляется только super admin и здесь не отображается."
        ),
        "settings.name": "Название",
        "settings.admin_chat_id": "ID чата модерации (Admin chat ID)",
        "settings.required_channel": "Обязательный канал (Required channel)",
        "settings.channel_url": "Ссылка на канал (Channel URL)",
        "settings.instagram_handle": "Instagram аккаунт (handle)",
        "settings.instagram_url": "Ссылка на Instagram (Instagram URL)",
        "settings.spreadsheet_id": "Google Sheets ID (Spreadsheet ID)",
        "settings.drive_folder_id": "Google Drive папка (Drive folder ID)",
        "settings.new_password": "Новый пароль",
        "settings.new_password_hint": "Оставьте пустым, чтобы не менять",

        # --- Diagnostics ---
        "diag.page_title": "Tenant diagnostics",
        "diag.title": "Диагностика: {name}",
        "diag.token_note": (
            "Проверка выполняется с token tenant в памяти; token не выводится."
        ),
        "diag.col_check": "Проверка",
        "diag.col_result": "Результат",
        "diag.no_results": "Нет результатов",
        "diag.check.bot_token": "Токен бота",
        "diag.check.required_channel": "Обязательный канал",
        "diag.check.channel_admin": "Права администратора канала",
        "diag.check.moderation_chat": "Чат модерации",
        "diag.check.diagnostics": "Диагностика",
        "diag.detail.token_missing": "Токен не настроен",
        "diag.detail.not_configured": "Не настроено",
        "diag.detail.status": "Telegram-статус: {status}",

        # --- CSV export headers ---
        "csv.id": "ID",
        "csv.reg_number": "Рег. номер",
        "csv.status": "Статус",
        "csv.country": "Страна",
        "csv.plate": "Гос. номер",
        "csv.direction": "Направление",
        "csv.phone": "Телефон",
        "csv.username": "Пользователь",
        "csv.language": "Язык",
        "csv.created_at": "Подана",
        "csv.processed_at": "Обработана",
        "csv.processed_by": "Кто обработал",

        # --- Moderator identity shown in Telegram cards / exports ---
        "moderation.via_panel": "админ-панель",
    },

    # ------------------------------------------------------------------
    # O‘zbekcha
    # ------------------------------------------------------------------
    "uz": {
        # --- Generic / shared ---
        "common.save": "Saqlash",
        "common.login": "Kirish",
        "common.logout": "Chiqish",
        "common.open": "Ochish",
        "common.delete": "🗑 O‘chirish",
        "common.upload": "Yuklash",
        "common.back": "Orqaga",
        "common.yes": "Ha",
        "common.active": "Faol",
        "common.search": "Qidirish",
        "common.kb": "KB",
        "common.bytes": "B",
        "common.not_set": "— kiritilmagan",
        "common.configured": "kiritilgan",
        "common.from_repo": "repozitoriydan",
        "common.uploaded": "yuklangan",
        "common.missing": "❌ yo‘q",

        # --- Navigation (tenant panel) ---
        "nav.brand_suffix": "Admin",
        "nav.dashboard": "Boshqaruv paneli",
        "nav.applications": "Arizalar",
        "nav.tickets": "🎫 Biletlar",
        "nav.broadcast": "📢 Ommaviy xabar",
        "nav.settings": "⚙️ Sozlamalar",
        "nav.export_excel": "📊 Excel",
        "nav.export_csv": "CSV",

        # --- Language switcher ---
        "lang.title": "Interfeys tili",

        # --- Legacy root login ---
        "login.title": "Admin panelga kirish",
        "login.page_title": "Kirish",
        "login.wrong_password": "Parol xato",
        "login.password_placeholder": "Parol",

        # --- Panel disabled / inactive states ---
        "disabled.panel.title": "Panel o‘chirilgan",
        "disabled.panel.body": (
            "Admin panelni yoqish uchun <code>ADMIN_PASSWORD</code> maxfiy so‘zini "
            "belgilang."
        ),
        "disabled.super.title": "Super admin o‘chirilgan",
        "disabled.super.body": (
            "Tenantlarni boshqarish uchun <code>SUPER_ADMIN_PASSWORD</code> maxfiy "
            "so‘zini belgilang."
        ),
        "tenant_inactive.title": "Tenant faol emas",
        "tenant_inactive.body": (
            "Ruxsat super admin tomonidan to‘xtatilgan. Platforma egasiga murojaat qiling."
        ),

        # --- Tenant dashboard ---
        "dash.page_title": "Boshqaruv paneli",
        "dash.total_apps": "Jami arizalar",
        "dash.pending": "Ko‘rib chiqilmoqda",
        "dash.approved": "Tasdiqlangan",
        "dash.approved_users": "Tasdiqlangan ishtirokchilar",
        "dash.rejected": "Rad etilgan",
        "dash.last_number": "Oxirgi raqam",
        "dash.download_excel": "📥 Excel (.xlsx) yuklab olish",
        "dash.manage_tickets": "🎫 Biletlarni boshqarish",
        "dash.by_direction": "📊 Yo‘nalishlar bo‘yicha",
        "dash.by_country": "🌍 Davlatlar bo‘yicha",
        "dash.by_language": "🌐 Tillar bo‘yicha",
        "dash.by_date": "📅 Kunlar bo‘yicha arizalar (oxirgi 14 kun)",

        # --- Statuses ---
        "status.pending": "Ko‘rib chiqilmoqda",
        "status.approved": "Tasdiqlangan",
        "status.rejected": "Rad etilgan",

        # --- Applications list ---
        "apps.page_title": "Arizalar",
        "apps.heading": "Arizalar ({n})",
        "apps.filter_all": "Hammasi",
        "apps.search_placeholder": "Qidiruv: raqam, telefon…",
        "apps.col_number": "Raqam",
        "apps.col_photo": "Surat",
        "apps.col_country": "Davlat",
        "apps.col_plate": "Davlat raqami",
        "apps.col_direction": "Yo‘nalish",
        "apps.col_phone": "Telefon",
        "apps.col_user": "Foydalanuvchi",
        "apps.col_status": "Holat",
        "apps.empty": "Arizalar yo‘q",

        # --- Application detail ---
        "detail.page_title": "Ariza #{id}",
        "detail.back_to_apps": "← Arizalarga qaytish",
        "detail.heading": "Ariza #{id}",
        "detail.col_status": "Holat",
        "detail.col_reg_number": "Reg. raqam",
        "detail.col_country": "Davlat",
        "detail.col_plate": "Davlat raqami",
        "detail.col_direction": "Yo‘nalish",
        "detail.col_car_mods": "Avtodagi o‘zgarishlar",
        "detail.col_phone": "Telefon",
        "detail.col_user": "Foydalanuvchi",
        "detail.col_language": "Til",
        "detail.col_submitted": "Topshirilgan",
        "detail.col_processed": "Ko‘rib chiqilgan",
        "detail.photos": "Suratlar",
        "detail.photos_empty": "Suratlar yo‘q",
        "detail.photo_side": "{side} tomon",
        "detail.mod_number": "O‘zgarish {n}",
        "detail.mods": "Avtomobildagi o‘zgarishlar",
        "detail.mods_empty": "Ishtirokchi o‘zgarish belgilamagan",
        "detail.badge_photo": "Badge (bedj) uchun surat",
        "detail.badge_empty": "Ishtirokchi hali badge uchun surat yubormagan",
        "detail.accept": "✅ Qabul qilish",
        "detail.reject": "❌ Rad etish",

        # --- Status control (detail page) ---
        "statusctl.title": "🔀 Holatni boshqarish",
        "statusctl.current": "Joriy holat: ",
        "statusctl.notice": (
            "Holatni o‘zgartirish ishtirokchiga Telegram orqali xabar yuboradi "
            "(«Tasdiqlangan» holatga o‘tkazilsa — bilet va raqam ham yuboriladi)."
        ),
        "statusctl.confirm": "Holat o‘zgartirilib, ishtirokchiga Telegram orqali xabar yuborilsinmi?",
        "statusctl.changed": "✅ Holat o‘zgartirildi",
        "statusctl.approve_btn": "✅ Tasdiqlangan",
        "statusctl.reject_btn": "❌ Rad etilgan",
        "statusctl.pending_btn": "⏳ Ko‘rib chiqilmoqda",

        # --- Individual message (detail page) ---
        "msg.title": "✉️ Shaxsiy xabar",
        "msg.hint": "Xabar faqat shu foydalanuvchiga, aynan shu bot orqali yuboriladi.",
        "msg.placeholder": "Xabar matni…",
        "msg.send": "📩 Yuborish",
        "msg.sent": "✅ Xabar yuborildi",

        # --- Errors / flashes (server-side) ---
        "error.app_not_found": "Ariza topilmadi",
        "error.tenant_not_found": "Tenant topilmadi",
        "error.photo_not_on_disk": "Surat diskda topilmadi",
        "error.msg_blocked": "Yuborib bo‘lmadi — foydalanuvchi botni bloklagan",
        "error.msg_empty": "Xabar matnini kiriting",
        "error.status_change": "Holatni o‘zgartirib bo‘lmadi",

        # --- Broadcast ---
        "bcast.page_title": "Ommaviy xabar",
        "bcast.title": "Telegramga ommaviy xabar",
        "bcast.intro": (
            "Auditoriyani tanlang va matn yozing. Xabar foydalanuvchilar yozgan "
            "shu bot orqali yuboriladi. Bir foydalanuvchi — bitta xabar."
        ),
        "bcast.done": "Ommaviy xabar yakunlandi",
        "bcast.audience_label": "Auditoriya: ",
        "bcast.sent": "Yuborildi: ",
        "bcast.failures": "xatolar / bot bloklangan: ",
        "bcast.total": "jami qabul qiluvchilar: ",
        "bcast.sent_uz": "🇺🇿 o‘zbek tilida: ",
        "bcast.sent_ru": "🇷🇺 rus tilida: ",
        "bcast.with_photo": " · surat bilan",
        "bcast.preview_count": "Tanlangan filtrlar bo‘yicha qabul qiluvchilar: <b>{n}</b>",
        "bcast.audience_heading": "Auditoriya",
        "bcast.audience.approved": "Tasdiqlangan — muvaffaqiyatli ro‘yxatdan o‘tgan",
        "bcast.audience.pending": "Ko‘rib chiqilmoqda — ariza hali hal qilinmagan",
        "bcast.audience.rejected": "Rad etilgan — ro‘yxatdan o‘tish qabul qilinmagan",
        "bcast.audience.incomplete": (
            "Ro‘yxatdan o‘tmagan (botni ochgan, lekin ariza yubormagan)"
        ),
        "bcast.audience.all_apps": "Ariza topshirgan barchalar",
        "bcast.audience.starters": "Bot biladigan barcha foydalanuvchilar",
        "bcast.people": "{n} kishi",
        "bcast.by_language": "Til bo‘yicha aniqlash",
        "bcast.only_uz": "🇺🇿 Faqat o‘zbek tili",
        "bcast.only_ru": "🇷🇺 Faqat rus tili",
        "bcast.by_direction": "Yo‘nalish bo‘yicha aniqlash",
        "bcast.text_uz_hint": "O‘zbek tilini tanlaganlar uchun",
        "bcast.text_ru_hint": "Qolgan barcha qabul qiluvchilarga",
        "bcast.text_ru_placeholder": "Xabar matni (ruscha)…",
        "bcast.one_field_hint": (
            "Faqat bitta maydon to‘ldirilsa — bu matn hamma qabul qiluvchilarga yuboriladi."
        ),
        "bcast.photo_uz": "🇺🇿 Post surati — O‘zbekcha",
        "bcast.photo_ru": "🇷🇺 Post surati — Ruscha",
        "bcast.photo_uz_hint": (
            "majburiy emas — yuklanmasa, ruscha versiyadagi surat ishlatiladi"
        ),
        "bcast.photo_ru_hint": (
            "majburiy emas — yuklanmasa, o‘zbekcha versiyadagi surat ishlatiladi"
        ),
        "bcast.caption_limit": (
            "Surat biriktirilgan til matni ko‘pi bilan 1024 belgi "
            "(Telegramning izoh cheklovi)."
        ),
        "bcast.preview_title": "👁 Postni oldindan ko‘rish",
        "bcast.preview_empty": "Bo‘sh",
        "bcast.confirm": "Ha, tanlangan auditoriyaga yuborish",
        "bcast.count_btn": "🔍 Sonini ko‘rsatish",
        "bcast.send_btn": "📢 Yuborish",
        "bcast.note_incomplete": (
            "«Ro‘yxatdan o‘tmagan» — bot yangilanganidan keyin /start bosgan, "
            "lekin ariza yubormagan foydalanuvchilar. Eski tashlab ketilgan "
            "anketalar bazada yo‘q."
        ),
        "bcast.note_filters": (
            "Barcha tillar yoki barcha yo‘nalishlar belgisi olib tashlansa — "
            "shu belgi bo‘yicha filtr qo‘llanilmaydi."
        ),
        "bcast.err_no_text": "Kamida bitta tilda matn kiriting",
        "bcast.err_no_confirm": "Yuborishni tasdiqlang (belgi qo‘ying)",
        "bcast.err_no_bot": "Bot mavjud emas — ommaviy xabar yuborib bo‘lmaydi",
        "bcast.err_uz_too_long": (
            "O‘zbekcha matn suratli xabar uchun juda uzun — Telegramning izoh "
            "chegarasi 1024 belgi"
        ),
        "bcast.err_ru_too_long": (
            "Ruscha matn suratli xabar uchun juda uzun — Telegramning izoh "
            "chegarasi 1024 belgi"
        ),
        "bcast.err_no_recipients": "Tanlangan filtrlar bo‘yicha qabul qiluvchi topilmadi",

        # --- Ticket assets page ---
        "assets.page_title": "Biletlar va logotiplar",
        "assets.msg.brand_uploaded": "✅ Brend logotipi yangilandi",
        "assets.msg.brand_deleted": (
            "🗑 Brend logotipi o‘chirildi (endi, bo‘lsa, repozitoriydagi versiya "
            "ishlatiladi)"
        ),
        "assets.msg.sponsor_uploaded": "✅ Homiy logotipi saqlandi",
        "assets.msg.sponsor_deleted": "🗑 Homiy logotipi o‘chirildi",
        "assets.msg.direction_uploaded": "✅ Yo‘nalish banneri saqlandi",
        "assets.msg.direction_deleted": "🗑 Yo‘nalish banneri o‘chirildi",
        "assets.err.no_file": "Fayl tanlanmagan",
        "assets.err.name_required": (
            "Fayl nomini kiriting (faqat lotin harflari, raqamlar, _ va -)"
        ),
        "assets.err.invalid_name": (
            "Nom faqat lotin harflari, raqamlar, _ va - dan iborat bo‘lishi mumkin "
            "(40 belgigacha)"
        ),
        "assets.err.unknown_brand": "Noma’lum brend",
        "assets.err.unknown_direction": "Noma’lum yo‘nalish",
        "assets.err_generic": "Faylni saqlab bo‘lmadi",
        "assets.preview.title": "🎫 Biletni oldindan ko‘rish",
        "assets.preview.hint": (
            "Bilet joriy logotiplar bilan mana bunday ko‘rinadi. Fon — vaqtinchalik "
            "zaglushka (gradient), aslida uning orqasida ishtirokchi avtomobili surati "
            "bo‘ladi."
        ),
        "assets.preview.reload_hint": (
            "Logotip yuklangan/o‘chirilgandan keyin sahifani yangilang — oldindan "
            "ko‘rish avtomatik qayta tayyorlanadi. Agar kamida bitta homiy logotipi "
            "paneldan yuklangan bo‘lsa, faqat yuklanganlari ishlatiladi "
            "(repozitoriydagi lari yashiriladi)."
        ),
        "assets.preview.full_size": "To‘liq o‘lchamda ochish",
        "assets.preview.refresh": "🔄 Yangilash",
        "assets.brand.title": "🏷 Biletning asosiy logotiplari",
        "assets.brand.hint": (
            "Bu ikki logotip posterning yuqori qismida ko‘rinadi. Kengligi ~1200px "
            "bo‘lgan shaffof PNG yuklang."
        ),
        "assets.brand.no_logo": "Logotip yo‘q",
        "assets.brand.key": "Kalit: ",
        "assets.brand.confirm_delete": "Logotip o‘chirilsinmi?",
        "assets.partners.title": "🤝 Kutilayotgan hamkor logotiplari",
        "assets.partners.hint": (
            "Tavsiya etilgan to‘plam — bu 4 logotip biletning yuqori lentasida "
            "ko‘rinadi. Tartib nom boshidagi raqam bilan belgilanadi: 1_, 2_, 3_, 4_…"
        ),
        "assets.partners.col_name": "Nomi",
        "assets.partners.col_description": "Tavsif",
        "assets.partners.col_status": "Holat",
        "assets.sponsors.title": "🏢 Homiy logotiplari (biletning yuqori lentasi)",
        "assets.sponsors.hint": (
            "Bu logotiplar biletning yuqori qismidagi qora lentada, tadbir "
            "promo-bannerlaridagi kabi ko‘rinadi. 10 tagacha logotip — ko‘p bo‘lsa, "
            "lenta avtomatik 2 qatorga bo‘linadi."
        ),
        "assets.sponsors.name": "Nomi: ",
        "assets.sponsors.empty": (
            "Hozircha homiy logotiplari yo‘q. Kamida bittasini yuklang — u darhol "
            "biletda paydo bo‘ladi."
        ),
        "assets.sponsors.add": "➕ Homiy logotipini qo‘shish",
        "assets.sponsors.add_hint": (
            "Nom biletdagi tartibni belgilaydi. Prefiksdan foydalaning: <code>1_</code>, "
            "<code>2_</code> va h.k. Faqat lotin harflari, raqamlar, _ va -."
        ),
        "assets.sponsors.add_example": (
            "Masalan: <code>1_mcs_sherdor</code>, <code>2_retro_tashkent</code>, "
            "<code>5_my_sponsor</code>"
        ),
        "assets.sponsors.label_name": "Logotip nomi",
        "assets.sponsors.label_file": "Fayl (PNG/JPG/WEBP)",
        "assets.sponsors.confirm_delete": "Homiy logotipi o‘chirilsinmi?",
        "assets.directions.title": "🎨 Yo‘nalish bannerlari",
        "assets.directions.hint": (
            "Ishtirokchiga yo‘nalishni tanlash paytida ko‘rinadi. Majburiy emas, "
            "lekin botni chiroyli qiladi."
        ),
        "assets.directions.no_banner": "Banner yo‘q",
        "assets.directions.confirm_delete": "Banner o‘chirilsinmi?",
        "assets.how.title": "ℹ️ Bu qanday ishlaydi",
        "assets.how.1": (
            "<b>Yuklangan fayllar volume’da saqlanadi</b> — qayta ishga tushirish va "
            "deploy’lardan omon qoladi, git’ga kiritilmaydi."
        ),
        "assets.how.2": (
            "Agar kamida bitta homiy logotipi paneldan yuklangan bo‘lsa, "
            "<b>faqat yuklanganlari ishlatiladi</b> — repozitoriydagi lari yashiriladi."
        ),
        "assets.how.3": (
            "Logotiplar tartibi fayl nomi bo‘yicha (alifbo). Saralash uchun "
            "<code>1_</code>, <code>2_</code> prefikslaridan foydalaning."
        ),
        "assets.how.4": (
            "<b>Shaffof PNG</b> tavsiya etiladi — logotip lentaning qora fonida "
            "oq kvadratsiz yotadi."
        ),
        "assets.how.5": (
            "Telegram orqali ham yuklash mumkin: moderatsiya chatiga "
            "<code>/logo 1_mcs_sherdor</code> izohi bilan fayl yuboring."
        ),

        # --- Photo side labels ---
        "side.left": "Chap",
        "side.right": "O‘ng",
        "side.front": "Old",
        "side.back": "Orqa",

        # --- Tenant selector login (/login) ---
        "selector.page_title": "Tenant paneliga kirish",
        "selector.title": "Tenant admin paneliga kirish",
        "selector.wrong_credentials": "Tenant yoki parol xato",
        "selector.no_tenants": (
            "Hozircha faol tenantlar yo‘q. Super admin sifatida kirib, birinchisini "
            "yarating."
        ),
        "selector.project": "Loyiha",
        "selector.password_placeholder": "Tenant paroli",

        # --- Tenant login (/t/{slug}/login) ---
        "tlogin.page_title": "Kirish",
        "tlogin.admin_panel": "admin panel",
        "tlogin.tenant": "Tenant: ",
        "tlogin.wrong_password": "Parol xato",
        "tlogin.password_placeholder": "Parol",
        "tlogin.other_tenant": "← Boshqa tenant",

        # --- Super-admin login ---
        "superlogin.page_title": "Super admin",
        "superlogin.title": "Super admin",
        "superlogin.wrong_password": "Parol xato",
        "superlogin.password_placeholder": "SUPER_ADMIN_PASSWORD",
        "superlogin.tenant_login": "Tenant paneliga kirish",

        # --- Super-admin layout ---
        "super.brand": "🛡 Multi-tenant Control Plane",
        "super.nav.tenants": "Tenantlar",
        "super.nav.new": "➕ Yangi tenant",

        # --- Super-admin dashboard ---
        "superdash.page_title": "Tenantlar",
        "superdash.total_tenants": "Jami tenantlar",
        "superdash.total_apps": "Jami arizalar",
        "superdash.heading": "Tenantlar",
        "superdash.create": "➕ Tenant yaratish",
        "superdash.tokens_note": (
            "Tokenlar hech qachon brauzerga chiqarilmaydi: faqat <code>***</code> "
            "holati ko‘rsatiladi. Tenantni o‘zgartirish faqat shu tenantning "
            "polling worker’ini avtomatik qayta ishga tushiradi."
        ),
        "superdash.col_tenant": "Tenant",
        "superdash.col_status": "Holat",
        "superdash.col_token": "Bot tokeni",
        "superdash.col_password": "Admin paroli",
        "superdash.col_apps": "Arizalar",
        "superdash.active": "✅ faol",
        "superdash.inactive": "⏸ faol emas",
        "superdash.token_configured": "*** kiritilgan",
        "superdash.token_missing": "— kiritilmagan",
        "superdash.password_configured": "kiritilgan",
        "superdash.password_missing": "— kiritilmagan",
        "superdash.edit": "Tahrirlash",
        "superdash.diagnostics": "Diagnostika",
        "superdash.pause": "Pauza",
        "superdash.enable": "Yoqish",
        "superdash.archive": "Arxiv",
        "superdash.confirm_archive": (
            "Tenant arxivlansinmi? Ma’lumotlar saqlanadi, polling to‘xtatiladi."
        ),
        "superdash.empty": "Tenantlar topilmadi",

        # --- Tenant create/edit form ---
        "tenant.create.title": "Tenant yaratish",
        "tenant.edit.title": "Tenantni tahrirlash",
        "tenant.form.back": "← Tenantlarga",
        "tenant.form.page_title": "Tenant formasi",
        "tenant.form.slug": "Slug (texnik nom)",
        "tenant.form.slug_hint": (
            "Slug yaratilgandan keyin o‘zgartirib bo‘lmaydi: u media-fayllar "
            "izolyatsiyasi kaliti hisoblanadi."
        ),
        "tenant.form.name": "Nomi",
        "tenant.form.bot_token": "Bot tokeni (Bot token)",
        "tenant.form.token_hint_new": "@BotFather’dan olingan token; shifrlangan holda saqlanadi",
        "tenant.form.token_hint_edit": "Joriy *** saqlanishi uchun bo‘sh qoldiring",
        "tenant.form.token_configured": "*** kiritilgan",
        "tenant.form.admin_chat_id": "Moderatsiya chat IDsi (Admin chat ID)",
        "tenant.form.required_channel": "Majburiy kanal (Required channel)",
        "tenant.form.channel_url": "Kanal havolasi (Channel URL)",
        "tenant.form.instagram_handle": "Instagram akkaunt (handle)",
        "tenant.form.instagram_url": "Instagram havolasi (Instagram URL)",
        "tenant.form.spreadsheet_id": "Google Sheets ID (Spreadsheet ID)",
        "tenant.form.drive_folder_id": "Google Drive papka IDsi (Drive folder ID)",
        "tenant.form.admin_password": "Tenant admin paroli",
        "tenant.form.password_hint_new": "Tenant paneli administratori paroli",
        "tenant.form.password_hint_edit": "Joriy parol saqlanishi uchun bo‘sh qoldiring",
        "tenant.form.password_stored": "Faqat PBKDF2 hash sifatida saqlanadi.",
        "tenant.form.is_active": "Faol",
        "tenant.form.is_active_hint": "Bu tenantning polling’ini ishga tushirish",
        "tenant.form.restart": "Tenantni qayta ishga tushirish",

        # --- Tenant form errors (mapped from ValueError/EncryptionError) ---
        "tenant.form.err_slug": (
            "Slug faqat kichik lotin harflari, raqamlar va defisdan iborat bo‘lishi kerak"
        ),
        "tenant.form.err_name": "Tenant nomini kiriting",
        "tenant.form.err_chat_id": "Admin chat ID butun son bo‘lishi kerak",
        "tenant.form.err_generic": "Tenantni saqlab bo‘lmadi: {detail}",

        # --- Tenant settings ---
        "settings.page_title": "Sozlamalar",
        "settings.title": "⚙️ Tenant sozlamalari",
        "settings.saved": "Sozlamalar saqlandi",
        "settings.token_note": (
            "Bot tokenni faqat super admin boshqaradi va bu yerda ko‘rsatilmaydi."
        ),
        "settings.name": "Nomi",
        "settings.admin_chat_id": "Moderatsiya chat IDsi (Admin chat ID)",
        "settings.required_channel": "Majburiy kanal (Required channel)",
        "settings.channel_url": "Kanal havolasi (Channel URL)",
        "settings.instagram_handle": "Instagram akkaunt (handle)",
        "settings.instagram_url": "Instagram havolasi (Instagram URL)",
        "settings.spreadsheet_id": "Google Sheets ID (Spreadsheet ID)",
        "settings.drive_folder_id": "Google Drive papka IDsi (Drive folder ID)",
        "settings.new_password": "Yangi parol",
        "settings.new_password_hint": "O‘zgartirmaslik uchun bo‘sh qoldiring",

        # --- Diagnostics ---
        "diag.page_title": "Tenant diagnostikasi",
        "diag.title": "Diagnostika: {name}",
        "diag.token_note": (
            "Tekshiruv tenant tokeni xotirada bajariladi; token hech qanday joyda "
            "ko‘rsatilmaydi."
        ),
        "diag.col_check": "Tekshiruv",
        "diag.col_result": "Natija",
        "diag.no_results": "Natijalar yo‘q",
        "diag.check.bot_token": "Bot tokeni",
        "diag.check.required_channel": "Majburiy kanal",
        "diag.check.channel_admin": "Kanal administrator huquqlari",
        "diag.check.moderation_chat": "Moderatsiya chati",
        "diag.check.diagnostics": "Diagnostika",
        "diag.detail.token_missing": "Token sozlanmagan",
        "diag.detail.not_configured": "Sozlanmagan",
        "diag.detail.status": "Telegram holati: {status}",

        # --- CSV export headers ---
        "csv.id": "ID",
        "csv.reg_number": "Reg. raqam",
        "csv.status": "Holat",
        "csv.country": "Davlat",
        "csv.plate": "Davlat raqami",
        "csv.direction": "Yo‘nalish",
        "csv.phone": "Telefon",
        "csv.username": "Foydalanuvchi",
        "csv.language": "Til",
        "csv.created_at": "Topshirilgan",
        "csv.processed_at": "Ko‘rib chiqilgan",
        "csv.processed_by": "Kim ko‘rib chiqqan",

        # --- Moderator identity shown in Telegram cards / exports ---
        "moderation.via_panel": "admin panel",
    },
}


def t(lang: str, key: str, **kwargs) -> str:
    """Translate ``key`` for ``lang`` with a safe Russian fallback.

    Unknown locales resolve to ``ru``; an unknown key resolves to ``ru``'s
    entry and ultimately to the key itself, so templates never break.  Only
    developer-supplied dictionary values go through ``str.format`` — never
    user input.
    """
    table = _STRINGS.get(normalize_lang(lang), _STRINGS[DEFAULT_LANG])
    text = table.get(key)
    if text is None:
        text = _STRINGS[DEFAULT_LANG].get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def lang_switcher(lang: str, extra_query: str = "") -> str:
    """Render the ``RU | O‘Z`` switcher as safe relative links.

    ``extra_query`` (already urlencoded by the caller) is appended after the
    locale parameter so pages like the filtered applications list keep their
    state.  Only the whitelisted locale codes ever appear in an ``href`` —
    no user value is ever reflected — so the links cannot carry XSS payloads
    or act as an open redirect.
    """
    current = normalize_lang(lang)
    suffix = f"&{extra_query}" if extra_query else ""
    links = []
    for code, label in (("ru", _RU_SWITCH_LABEL), ("uz", _UZ_SWITCH_LABEL)):
        cls = f'{code}{" cur" if code == current else ""}'
        links.append(f'<a class="{cls}" href="?lang={code}{suffix}">{label}</a>')
    return (
        '<span class="lang-switch" title="' + escape(t(current, "lang.title")) + '">'
        + '<span class="sep">|</span>'.join(links)
        + "</span>"
    )


def extra_query(params: dict) -> str:
    """Build a urlencoded query-string fragment for :func:`lang_switcher`.

    Empty values are skipped; keys are fixed by the callers, values are
    escaped by ``urlencode``.
    """
    clean = [
        (str(key), str(value))
        for key, value in params.items()
        if value not in (None, "")
    ]
    return urlencode(clean)
