"""All user-facing text, in Russian (ru) and Uzbek (uz).

Use ``T(lang)`` to get the namespace for a language, e.g. ``T(lang).GREETING``.
For tenant-branded messages use the ``*_for_tenant`` helpers that take a
``TenantConfig`` — event name comes from ``tenant_name``, channel from
``channel_url``/``required_channel``, dates/venue from tenant settings.

Dates confirmed with the client (promotors defaults):
- Participant entry: 11 September, 10:00-19:00.
- Guest event: 12-13 September, from 10:00, at the SOF EXPO parking.

Tenant isolation: no hard-coded ``t.me/promotorsshow`` remains in branded
helpers; everything comes from tenant config.  Promotors keeps its old wording
via DB seed/migration.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from .constants import DIRECTION_LABELS, DIRECTIONS_CANON, direction_label

# --- Russian (base templates, promotors fallback) ---
_RU = dict(
    SUBSCRIBE_REQUIRED=(
        "Здравствуйте! Вас приветствует бот для регистрации автомобилей на мероприятии "
        "<b>Promotors Show Samarkand</b>.\n\n"
        "Чтобы продолжить, подпишитесь, пожалуйста, на наш канал, а затем нажмите "
        "«Я подписался»."
    ),
    SUBSCRIBE_STILL_NOT=(
        "Похоже, вы ещё не подписаны на канал. Подпишитесь и нажмите «Я подписался»."
    ),
    GREETING=(
        "Здравствуйте! Вас приветствует бот для регистрации автомобилей на мероприятии "
        "<b>Promotors Show Samarkand</b>.\n\nДавайте начнём регистрацию."
    ),
    ASK_COUNTRY="Выберите страну:",
    ASK_COUNTRY_OTHER="Впишите название вашей страны:",
    ASK_PLATE="Напишите государственный номер автомобиля полностью:",
    BAD_PLATE=(
        "Это не похоже на государственный номер. Напишите его полностью, "
        "например <b>01A123BC</b> или <b>30X577XX</b>:"
    ),
    BAD_COUNTRY="Впишите, пожалуйста, название страны буквами:",
    PHOTO_PROMPTS=[
        "Пришлите фотографию <b>левой</b> стороны автомобиля (1 из 4):",
        "Отлично! Теперь <b>правую</b> сторону (2 из 4):",
        "Теперь <b>переднюю</b> сторону (3 из 4):",
        "И последнее — <b>заднюю</b> сторону (4 из 4):",
    ],
    PHOTO_NOT_A_PHOTO="Пожалуйста, пришлите именно фотографию (как изображение).",
    ASK_MODS=(
        "А что вы изменили в автомобиле после этих фотографий?\n\n"
        "Пришлите фото тех мест, где есть изменения — например <b>капот</b>, "
        "<b>багажник</b>, <b>аудиосистема</b> и так далее.\n\n"
        "Можно прислать несколько фото (до {max}). Если изменений нет — "
        "нажмите кнопку ниже."
    ),
    MODS_ADDED=(
        "Принято ({n} из {max}). Пришлите ещё фото изменений или нажмите «Готово»."
    ),
    MODS_LIMIT="Достаточно фотографий ({max} из {max}) — идём дальше.",
    MODS_NOT_A_PHOTO=(
        "Пожалуйста, пришлите фотографию изменения — или нажмите кнопку ниже."
    ),
    BTN_MODS_DONE="Готово ✅",
    BTN_MODS_NONE="Изменений нет ➡️",
    ASK_DIRECTION="Выберите направление для участия:",
    ASK_SUB_DIRECTION="Выберите поднаправление для <b>{parent}</b>:",
    ASK_SUB_DIRECTION_PLAIN="Выберите поднаправление:",
    PHOTO_DOWNLOAD_FAILED=(
        "Не удалось сохранить эту фотографию. Пришлите, пожалуйста, её ещё раз."
    ),
    # The photo step answers first and saves afterwards (bot/services/media.py),
    # so a failed save is reported by the download itself, naming the side the
    # participant has to send again — the slot it belongs to stays reserved.
    PHOTO_SAVE_FAILED=(
        "⚠️ Не удалось сохранить фото ({what}). Пришлите его, пожалуйста, ещё раз."
    ),
    PHOTO_RESEND_ASK=(
        "⚠️ Пока не сохранилось: {what}. Пришлите, пожалуйста, ещё раз."
    ),
    PHOTO_RESEND_BEFORE_FINISH=(
        "⚠️ Чтобы завершить регистрацию, пришлите, пожалуйста, ещё раз: {what}."
    ),
    PHOTOS_SAVING="⏳ Сохраняю фотографии — секунду…",
    SIDE_NAMES={
        "left": "левая сторона",
        "right": "правая сторона",
        "front": "передняя сторона",
        "back": "задняя сторона",
    },
    MOD_PHOTO_NAME="фото изменений №{n}",
    STEP_STALE="Эта кнопка уже устарела — продолжаем с текущего шага.",
    RECOVER_RESTART=(
        "Что-то пошло не так, и форма сбилась. Нажмите /start, чтобы начать "
        "регистрацию заново — предыдущие ответы не сохранятся."
    ),
    RECOVER_TECHNICAL=(
        "Произошла техническая ошибка. Попробуйте ещё раз или нажмите /start, "
        "чтобы начать регистрацию заново."
    ),
    UNRECOGNIZED=(
        "Я не понял это сообщение. Чтобы начать регистрацию, нажмите /start. "
        "Узнать свой номер — «Узнать свой номер»."
    ),
    FORM_STEP_REPEATED=(
        "Продолжаем с текущего шага — смотрите вопрос выше. "
        "Начать заново: /start."
    ),
    DIRECTION_PICKED="Ваше направление: <b>{direction}</b> 🔥",
    ASK_PHONE="Отправьте, пожалуйста, ваш номер телефона кнопкой ниже.",
    BAD_PHONE=(
        "Это не похоже на номер телефона. Нажмите кнопку ниже или напишите "
        "номер, например <b>+998 90 123 45 67</b>:"
    ),
    THANKS=(
        "Спасибо! Ваша заявка принята. В ближайшее время вы получите ответ.\n\n"
        "Узнать статус можно кнопкой «Узнать свой номер»."
    ),
    APPROVED=(
        "Поздравляем, вы прошли регистрацию! 🎉\n\n"
        "Ваш регистрационный номер — <b>№{number}</b>.\n"
        "Заезд участников начнётся <b>11 сентября 2026 с 10:00 до 19:00</b>.\n\n"
        "Обязательно подпишитесь на канал https://t.me/promotorsshow — там мы публикуем:\n"
        "1) Время заезда\n"
        "2) Правила участия на фестивале\n"
        "3) Расстановку\n"
        "а также все другие новости."
    ),
    REJECTED=(
        "Здравствуйте! К сожалению, вы не прошли регистрацию.\n\n"
        "Но мы приглашаем вас посетить наше мероприятие как гостя (без автомобиля) — "
        "оно пройдёт <b>12 и 13 сентября с 10:00</b> на парковке <b>SOF EXPO</b>."
    ),
    STATUS_PENDING="Ваша заявка на рассмотрении. В ближайшее время вы получите ответ.",
    STATUS_APPROVED="Ваш регистрационный номер — <b>№{number}</b>.",
    STATUS_REJECTED="Ваша заявка отклонена.",
    STILL_IN_FORM=(
        "Вы сейчас в процессе регистрации — продолжим оформление. Вот текущий шаг:"
    ),
    FORM_IN_PROGRESS=(
        "Вы уже начали регистрацию. Продолжим с того же места или начнём заново?"
    ),
    REGISTRATION_CLOSED=(
        "Регистрация на <b>Promotors Show Samarkand</b> завершена. Спасибо за интерес!\n\n"
        "Приходите на мероприятие в качестве гостя — оно пройдёт "
        "<b>12 и 13 сентября с 10:00</b> на парковке <b>SOF EXPO</b>."
    ),
    SHARE_CTA="📸 Опубликуй свой билет в Stories и отметь нас {handle} — увидимся на Promotors Show!",
    SHARE_CTA_PLAIN="📸 Опубликуй свой билет в Stories — увидимся на Promotors Show!",
    BTN_SUBSCRIBE="Подписаться на канал",
    BTN_CHECK_SUBSCRIPTION="Я подписался ✅",
    BTN_SEND_PHONE="Отправить номер телефона ☎️",
    BTN_MY_NUMBER="Узнать свой номер",
    BTN_CONTINUE_FORM="Продолжить",
    BTN_RESTART_FORM="Начать заново",
    COUNTRY_OTHER="Другая",
    COUNTRIES=["Россия", "Узбекистан", "Таджикистан", "Казахстан", "Киргизия"],
    DIRECTIONS=DIRECTION_LABELS["ru"],
    BADGE_PHOTO_SAVED=(
        "Спасибо! Фото для бейджа получено ✅\n\n"
        "Если захотите заменить его — просто пришлите новое фото."
    ),
    BADGE_PHOTO_NO_APP=(
        "Фото получено, но у вас пока нет заявки — сначала зарегистрируйтесь: /start"
    ),
)

# --- Uzbek (base templates) ---
_UZ = dict(
    SUBSCRIBE_REQUIRED=(
        "Assalomu alaykum! <b>Promotors Show Samarkand</b> tadbirida avtomobillarni "
        "ro‘yxatdan o‘tkazish botiga xush kelibsiz.\n\n"
        "Davom etish uchun, iltimos, kanalimizga obuna bo‘ling va so‘ng «Obuna bo‘ldim» "
        "tugmasini bosing."
    ),
    SUBSCRIBE_STILL_NOT=(
        "Siz hali kanalga obuna bo‘lmagansiz. Obuna bo‘ling va «Obuna bo‘ldim» tugmasini bosing."
    ),
    GREETING=(
        "Assalomu alaykum! <b>Promotors Show Samarkand</b> tadbirida avtomobillarni "
        "ro‘yxatdan o‘tkazish botiga xush kelibsiz.\n\nRo‘yxatdan o‘tishni boshlaymiz."
    ),
    ASK_COUNTRY="Davlatni tanlang:",
    ASK_COUNTRY_OTHER="Davlatingiz nomini yozing:",
    ASK_PLATE="Avtomobilingizning davlat raqamini to‘liq yozing:",
    BAD_PLATE=(
        "Bu davlat raqamiga o‘xshamaydi. To‘liq yozing, masalan "
        "<b>01A123BC</b> yoki <b>30X577XX</b>:"
    ),
    BAD_COUNTRY="Iltimos, davlat nomini harflar bilan yozing:",
    PHOTO_PROMPTS=[
        "Avtomobilning <b>chap</b> tomoni suratini yuboring (1 dan 4):",
        "Zo‘r! Endi <b>o‘ng</b> tomonini (2 dan 4):",
        "Endi <b>old</b> tomonini (3 dan 4):",
        "Va oxirgisi — <b>orqa</b> tomonini (4 dan 4):",
    ],
    PHOTO_NOT_A_PHOTO="Iltimos, aynan surat yuboring (rasm sifatida).",
    ASK_MODS=(
        "Bu suratlardan keyin avtomobilda yana nima o‘zgardi?\n\n"
        "O‘zgarish kiritilgan joylarning suratini yuboring — masalan <b>kapot</b>, "
        "<b>bagaj</b>, <b>ovoz tizimi</b> va hokazo.\n\n"
        "Bir nechta surat yuborishingiz mumkin ({max} tagacha). O‘zgarish bo‘lmasa — "
        "pastdagi tugmani bosing."
    ),
    MODS_ADDED=(
        "Qabul qilindi ({n} dan {max}). Yana o‘zgarish suratini yuboring yoki "
        "«Tayyor» tugmasini bosing."
    ),
    MODS_LIMIT="Suratlar yetarli ({max} dan {max}) — davom etamiz.",
    MODS_NOT_A_PHOTO=(
        "Iltimos, o‘zgarish suratini yuboring — yoki pastdagi tugmani bosing."
    ),
    BTN_MODS_DONE="Tayyor ✅",
    BTN_MODS_NONE="O‘zgarish yo‘q ➡️",
    ASK_DIRECTION="Ishtirok yo‘nalishini tanlang:",
    ASK_SUB_DIRECTION="<b>{parent}</b> uchun yo‘nalish osti turini tanlang:",
    ASK_SUB_DIRECTION_PLAIN="Yo‘nalish osti turini tanlang:",
    PHOTO_DOWNLOAD_FAILED=(
        "Bu suratni saqlab bo‘lmadi. Iltimos, uni yana bir marta yuboring."
    ),
    PHOTO_SAVE_FAILED=(
        "⚠️ Suratni ({what}) saqlab bo‘lmadi. Iltimos, uni yana bir marta yuboring."
    ),
    PHOTO_RESEND_ASK=(
        "⚠️ Hali saqlanmadi: {what}. Iltimos, yana bir marta yuboring."
    ),
    PHOTO_RESEND_BEFORE_FINISH=(
        "⚠️ Ro‘yxatdan o‘tishni tugatish uchun yana yuborishingiz kerak: {what}."
    ),
    PHOTOS_SAVING="⏳ Suratlar saqlanmoqda — bir soniya…",
    SIDE_NAMES={
        "left": "chap tomon",
        "right": "o‘ng tomon",
        "front": "old tomon",
        "back": "orqa tomon",
    },
    MOD_PHOTO_NAME="o‘zgarish surati №{n}",
    STEP_STALE="Bu tugma eskirgan — joriy qadamdan davom etamiz.",
    RECOVER_RESTART=(
        "Nimadir xato ketdi va shakl buzildi. Ro‘yxatdan o‘tishni boshidan "
        "boshlash uchun /start tugmasini bosing — oldingi javoblar saqlanmaydi."
    ),
    RECOVER_TECHNICAL=(
        "Texnik xatolik yuz berdi. Yana urinib ko‘ring yoki ro‘yxatdan o‘tishni "
        "boshidan boshlash uchun /start tugmasini bosing."
    ),
    UNRECOGNIZED=(
        "Bu xabarni tushunmadim. Ro‘yxatdan o‘tishni boshlash uchun /start "
        "tugmasini bosing. Raqamingizni bilish — «Raqamimni bilish»."
    ),
    FORM_STEP_REPEATED=(
        "Joriy qadamdan davom etamiz — savol yuqorida. Boshidan boshlash: /start."
    ),
    DIRECTION_PICKED="Sizning yo‘nalishingiz: <b>{direction}</b> 🔥",
    ASK_PHONE="Iltimos, telefon raqamingizni pastdagi tugma orqali yuboring.",
    BAD_PHONE=(
        "Bu telefon raqamiga o‘xshamaydi. Pastdagi tugmani bosing yoki raqamni "
        "yozing, masalan <b>+998 90 123 45 67</b>:"
    ),
    THANKS=(
        "Rahmat! Arizangiz qabul qilindi. Tez orada javob olasiz.\n\n"
        "Holatni «Raqamimni bilish» tugmasi orqali bilib olishingiz mumkin."
    ),
    APPROVED=(
        "Tabriklaymiz, ro‘yxatdan o‘tdingiz! 🎉\n\n"
        "Sizning ro‘yxat raqamingiz — <b>№{number}</b>.\n"
        "Ishtirokchilar kirishi <b>11-sentyabr 2026, 10:00 dan 19:00 gacha</b> boshlanadi.\n\n"
        "Albatta kanalga obuna bo‘ling: https://t.me/promotorsshow — u yerda quyidagilarni e’lon qilamiz:\n"
        "1) Kirish vaqti\n"
        "2) Festivalda ishtirok etish qoidalari\n"
        "3) Joylashuv\n"
        "shuningdek boshqa barcha yangiliklar."
    ),
    REJECTED=(
        "Assalomu alaykum! Afsuski, siz ro‘yxatdan o‘tmadingiz.\n\n"
        "Ammo sizni tadbirimizga mehmon sifatida (avtomobilsiz) taklif qilamiz — u "
        "<b>12 va 13-sentyabr, 10:00 dan</b> <b>SOF EXPO</b> avtoturargohida bo‘lib o‘tadi."
    ),
    STATUS_PENDING="Arizangiz ko‘rib chiqilmoqda. Tez orada javob olasiz.",
    STATUS_APPROVED="Sizning ro‘yxat raqamingiz — <b>№{number}</b>.",
    STATUS_REJECTED="Arizangiz rad etildi.",
    STILL_IN_FORM=(
        "Siz hozir ro‘yxatdan o‘tmoqdasiz — davom etamiz. Mana joriy qadam:"
    ),
    FORM_IN_PROGRESS=(
        "Siz allaqachon ro‘yxatdan o‘tishni boshlagansiz. Davom etamizmi yoki "
        "boshidan boshlaymizmi?"
    ),
    REGISTRATION_CLOSED=(
        "<b>Promotors Show Samarkand</b> uchun ro‘yxatdan o‘tish yakunlandi. "
        "Qiziqish bildirganingiz uchun rahmat!\n\n"
        "Tadbirga mehmon sifatida tashrif buyuring — u <b>12 va 13-sentyabr, 10:00 dan</b> "
        "<b>SOF EXPO</b> avtoturargohida bo‘lib o‘tadi."
    ),
    SHARE_CTA="📸 Biletingizni Storiesda ulashing va bizni belgilang {handle} — Promotors Show’da ko‘rishguncha!",
    SHARE_CTA_PLAIN="📸 Biletingizni Storiesda ulashing — Promotors Show’da ko‘rishguncha!",
    BTN_SUBSCRIBE="Kanalga obuna bo‘lish",
    BTN_CHECK_SUBSCRIPTION="Obuna bo‘ldim ✅",
    BTN_SEND_PHONE="Telefon raqamni yuborish ☎️",
    BTN_MY_NUMBER="Raqamimni bilish",
    BTN_CONTINUE_FORM="Davom etish",
    BTN_RESTART_FORM="Boshidan boshlash",
    COUNTRY_OTHER="Boshqa",
    COUNTRIES=["Rossiya", "O‘zbekiston", "Tojikiston", "Qozog‘iston", "Qirg‘iziston"],
    DIRECTIONS=DIRECTION_LABELS["uz"],
    BADGE_PHOTO_SAVED=(
        "Rahmat! Beyjingiz uchun surat qabul qilindi ✅\n\n"
        "Almashtirmoqchi bo‘lsangiz — shunchaki yangi surat yuboring."
    ),
    BADGE_PHOTO_NO_APP=(
        "Surat qabul qilindi, lekin sizda hali ariza yo‘q — avval ro‘yxatdan o‘ting: /start"
    ),
)

RU = SimpleNamespace(**_RU)
UZ = SimpleNamespace(**_UZ)
_LANGS = {"ru": RU, "uz": UZ}


def T(lang: str) -> SimpleNamespace:
    """Return the text namespace for a language (falls back to Russian)."""
    return _LANGS.get(lang, RU)


def localize_direction(canonical: str, lang: str) -> str:
    """Map a canonical direction to its label in ``lang``."""
    return direction_label(canonical, lang)


def side_name(lang: str, side: str) -> str:
    """Name one of the four required sides ("левая сторона") for messages.

    The registration flow answers before the photo is on the volume, so a failed
    save has to name the side it belongs to: "send the *left* side again" is
    actionable, "something failed" is not.
    """
    return T(lang).SIDE_NAMES.get(side, side)


def mod_photo_name(lang: str, number: int) -> str:
    """Label for one modification close-up, e.g. "фото изменений №2"."""
    return T(lang).MOD_PHOTO_NAME.format(n=number)


# --- Language-independent data ---
COUNTRIES_CANON = ["Россия", "Узбекистан", "Таджикистан", "Казахстан", "Киргизия"]

# --- Language picker (shown before the language is known) ---
ASK_LANGUAGE = "Tilni tanlang / Выберите язык:"
BTN_LANG_UZ = "🇺🇿 O‘zbekcha"
BTN_LANG_RU = "🇷🇺 Русский"

# Any-language labels used to match the "my number" reply-keyboard button.
MY_NUMBER_LABELS = {RU.BTN_MY_NUMBER, UZ.BTN_MY_NUMBER}

# Shown on /start when registration is closed and the user's language is still
# unknown (before the language picker), so it has to be bilingual.
REGISTRATION_CLOSED_BILINGUAL = (
    "Ro‘yxatdan o‘tish yakunlandi! ✅ Qiziqish bildirganingiz uchun rahmat!\n"
    "Tadbirga mehmon sifatida tashrif buyuring — 12 va 13-sentyabr, 10:00 dan, "
    "SOF EXPO avtoturargohida.\n\n"
    "Регистрация завершена! ✅ Спасибо за интерес!\n"
    "Приходите на мероприятие как гость — 12 и 13 сентября с 10:00, "
    "парковка SOF EXPO."
)

# Shown after an unexpected error when the participant's language is unknown.
RECOVER_TECHNICAL_BILINGUAL = (
    "Texnik xatolik yuz berdi. Yana urinib ko‘ring yoki /start tugmasini bosing.\n"
    "Произошла техническая ошибка. Попробуйте ещё раз или нажмите /start."
)

# Shown when someone asks their number but has no application (language unknown).
STATUS_NONE = (
    "Sizda hali ariza yo‘q. Ro‘yxatdan o‘tish uchun /start bosing.\n"
    "У вас пока нет заявки. Нажмите /start, чтобы зарегистрироваться."
)

# --- Moderation card (admin-facing, Russian only) ---
MODERATION_CARD = (
    "🚗 <b>Новая заявка</b>\n\n"
    "Страна: {country}\n"
    "Гос. номер: {plate}\n"
    "Направление: {direction}\n"
    "Телефон: {phone}\n"
    "Изменения в авто: {mods}\n"
    "Пользователь: {user}"
)
MODERATION_MODS_NONE = "не указаны"
MODERATION_MODS_COUNT = "{n} фото (последние в альбоме)"
MODERATION_APPROVED = "✅ Принято — №{number} ({moderator})"
MODERATION_REJECTED = "❌ Отклонено ({moderator})"
MODERATION_ALREADY = "Эта заявка уже обработана."
MODERATION_FAILED = "Не удалось обработать заявку — попробуйте ещё раз."
TICKET_CMD_USAGE = (
    "Использование: <code>/ticket 123</code> — id заявки из карточки или панели."
)
TICKET_CMD_NO_APP = "Заявка <code>{app_id}</code> не найдена."
TICKET_CMD_NOT_APPROVED = (
    "У заявки статус <b>{status}</b> — билет есть только у одобренной заявки."
)
TICKET_CMD_SENT = "🎫 Билет отправлен участнику (№{number})."
TICKET_CMD_FAILED = "⚠️ Билет не ушёл: <code>{error}</code>"
TICKET_FAILED_ADMIN = (
    "⚠️ <b>Билет не ушёл участнику</b>\n"
    "Заявка №{number} · {plate} · {user}\n"
    "<code>{error}</code>\n"
    "Перешлите билет участнику вручную, если он его не получил."
)
TICKET_FALLBACK_ADMIN = (
    "🎫 Заявка №{number} · {plate} · {user}\n"
    "Перешлите это фото участнику — автоматическая отправка не прошла."
)
MODERATION_PANEL_HEADER = "🖥 <b>Решение через админ-панель</b>"

# Sent to the moderation chat when a registered participant sends their badge
# photo (outside the registration flow, so it never touches its texts/state).
BADGE_PHOTO_ADMIN_NOTICE = (
    "🖼 <b>Фото на бейдж</b>\n\n"
    "Заявка: #{app_id}\n"
    "Гос. номер: {plate}\n"
    "Направление: {direction}\n"
    "Пользователь: {user}"
)


# ---------------------------------------------------------------------------
# Tenant-branded helpers — all event-specific strings come from TenantConfig
# ---------------------------------------------------------------------------

def _tenant_name_or_default(tenant: Any) -> str:
    name = getattr(tenant, "tenant_name", "") or getattr(tenant, "name", "") or "Promotors Show"
    return str(name).strip() or "Promotors Show"


def _channel_url_for_tenant(tenant: Any) -> str:
    """Return channel URL for a tenant, never hard-coded promotors link."""
    # Prefer explicit channel_url, then build from required_channel if it's a handle.
    url = (getattr(tenant, "channel_url", "") or "").strip()
    if url:
        return url
    # Try to derive from required_channel
    req = (getattr(tenant, "required_channel", "") or "").strip()
    if req.startswith("@"):
        return f"https://t.me/{req.lstrip('@')}"
    if req.startswith("https://"):
        return req
    # Fallback: no URL, only subscription check via chat id
    return ""


def _event_date(tenant: Any, lang: str) -> str:
    if lang == "uz":
        return (getattr(tenant, "event_date_text_uz", "") or "").strip()
    return (getattr(tenant, "event_date_text_ru", "") or "").strip()


def _guest_date(tenant: Any, lang: str) -> str:
    if lang == "uz":
        return (getattr(tenant, "event_guest_date_text_uz", "") or "").strip()
    return (getattr(tenant, "event_guest_date_text_ru", "") or "").strip()


def _venue(tenant: Any, lang: str) -> str:
    if lang == "uz":
        return (getattr(tenant, "event_venue_text_uz", "") or "").strip()
    return (getattr(tenant, "event_venue_text_ru", "") or "").strip()


def _event_note(tenant: Any, lang: str) -> str:
    if lang == "uz":
        return (getattr(tenant, "event_note_text_uz", "") or "").strip()
    return (getattr(tenant, "event_note_text_ru", "") or "").strip()


def greeting_for_tenant(lang: str, tenant: Any) -> str:
    """Greeting with tenant name, no hard-coded Promotors."""
    name = _tenant_name_or_default(tenant)
    if lang == "uz":
        return (
            f"Assalomu alaykum! <b>{name}</b> tadbirida avtomobillarni "
            f"ro‘yxatdan o‘tkazish botiga xush kelibsiz.\n\nRo‘yxatdan o‘tishni boshlaymiz."
        )
    return (
        f"Здравствуйте! Вас приветствует бот для регистрации автомобилей на мероприятии "
        f"<b>{name}</b>.\n\nДавайте начнём регистрацию."
    )


def subscribe_required_for_tenant(lang: str, tenant: Any) -> str:
    name = _tenant_name_or_default(tenant)
    if lang == "uz":
        return (
            f"Assalomu alaykum! <b>{name}</b> tadbirida avtomobillarni "
            f"ro‘yxatdan o‘tkazish botiga xush kelibsiz.\n\n"
            f"Davom etish uchun, iltimos, kanalimizga obuna bo‘ling va so‘ng «Obuna bo‘ldim» "
            f"tugmasini bosing."
        )
    return (
        f"Здравствуйте! Вас приветствует бот для регистрации автомобилей на мероприятии "
        f"<b>{name}</b>.\n\n"
        f"Чтобы продолжить, подпишитесь, пожалуйста, на наш канал, а затем нажмите "
        f"«Я подписался»."
    )


def approved_for_tenant(lang: str, tenant: Any, number: int) -> str:
    """Approved message with tenant channel and date, no hard-coded link."""
    name = _tenant_name_or_default(tenant)
    channel = _channel_url_for_tenant(tenant)
    ev_date = _event_date(tenant, lang)
    # Build channel line
    if channel:
        if lang == "uz":
            channel_line = f"Albatta kanalga obuna bo‘ling: {channel} — u yerda quyidagilarni e’lon qilamiz:\n"
        else:
            channel_line = f"Обязательно подпишитесь на канал {channel} — там мы публикуем:\n"
    else:
        if lang == "uz":
            channel_line = "Albatta kanalga obuna bo‘ling — u yerda quyidagilarni e’lon qilamiz:\n"
        else:
            channel_line = "Обязательно подпишитесь на канал — там мы публикуем:\n"

    if lang == "uz":
        base = (
            f"Tabriklaymiz, ro‘yxatdan o‘tdingiz! 🎉\n\n"
            f"Sizning ro‘yxat raqamingiz — <b>№{number}</b>.\n"
        )
        if ev_date:
            base += f"Ishtirokchi avtomobillari kirishi: <b>{ev_date}</b>.\n"
        note = _event_note(tenant, lang)
        if note:
            base += f"{note}\n"
        base += "\n"
        base += channel_line
        base += "1) Kirish vaqti\n2) Festivalda ishtirok etish qoidalari\n3) Joylashuv\nshuningdek boshqa barcha yangiliklar."
        return base
    else:
        base = (
            f"Поздравляем, вы прошли регистрацию! 🎉\n\n"
            f"Ваш регистрационный номер — <b>№{number}</b>.\n"
        )
        if ev_date:
            base += f"Заезд авто участников: <b>{ev_date}</b>.\n"
        note = _event_note(tenant, lang)
        if note:
            base += f"{note}\n"
        base += "\n"
        base += channel_line
        base += "1) Время заезда\n2) Правила участия на фестивале\n3) Расстановку\nа также все другие новости."
        return base


def _venue_is_parking(venue: str) -> bool:
    """Promotors called SOF EXPO a parking lot. Named halls (INDEX) are not."""
    low = (venue or "").lower()
    if any(word in low for word in ("парк", "parking", "turargoh", "avtoturargoh")):
        return True
    return "expo" in low


def rejected_for_tenant(lang: str, tenant: Any) -> str:
    """Rejection message.

    Two flavours, chosen by the tenant's own settings:

    * a tenant that advertises a guest date (Promotors: 12-13 September at the
      SOF EXPO parking) keeps the historic "come as a guest" invitation;
    * a tenant with no guest date — SPL Show, where the client's rule is
      «hozircha faqat uchastniklar uchun» — gets an event-neutral rejection with
      **no** date, time or venue.  That is what removed the "неправильное время
      мероприятия" from the rejection: the empty "Дата для гостей" field in the
      panel is the switch.
    """
    gdate = _guest_date(tenant, lang)
    if not gdate:
        if lang == "uz":
            return (
                "Assalomu alaykum! Afsuski, siz ro‘yxatdan o‘tmadingiz.\n\n"
                "Hozircha tadbir faqat ro‘yxatdan o‘tgan ishtirokchilar uchun. "
                "Qiziqish bildirganingiz uchun rahmat!"
            )
        return (
            "Здравствуйте! К сожалению, вы не прошли регистрацию.\n\n"
            "Сейчас мероприятие проходит только для зарегистрированных участников. "
            "Спасибо за интерес!"
        )

    venue = _venue(tenant, lang)
    if lang == "uz":
        base = "Assalomu alaykum! Afsuski, siz ro‘yxatdan o‘tmadingiz.\n\n"
        base += "Ammo sizni tadbirimizga mehmon sifatida (avtomobilsiz) taklif qilamiz"
        base += f" — u <b>{gdate}</b>"
        if venue:
            if _venue_is_parking(venue):
                base += f" <b>{venue}</b> avtoturargohida bo‘lib o‘tadi."
            else:
                base += f" <b>{venue}</b> manzilida bo‘lib o‘tadi."
        else:
            base += " bo‘lib o‘tadi."
        return base

    base = "Здравствуйте! К сожалению, вы не прошли регистрацию.\n\n"
    base += "Но мы приглашаем вас посетить наше мероприятие как гостя (без автомобиля)"
    base += f" — оно пройдёт <b>{gdate}</b>"
    if venue:
        if _venue_is_parking(venue):
            base += f" на парковке <b>{venue}</b>."
        else:
            base += f" на площадке <b>{venue}</b>."
    else:
        base += "."
    return base


def registration_closed_for_tenant(lang: str, tenant: Any) -> str:
    name = _tenant_name_or_default(tenant)
    venue = _venue(tenant, lang)
    gdate = _guest_date(tenant, lang)
    if lang == "uz":
        base = f"<b>{name}</b> uchun ro‘yxatdan o‘tish yakunlandi. Qiziqish bildirganingiz uchun rahmat!\n\n"
        if gdate or venue:
            base += "Tadbirga mehmon sifatida tashrif buyuring"
            if gdate or venue:
                base += " — u "
                if gdate:
                    base += f"<b>{gdate}</b>"
                if venue:
                    if gdate:
                        if _venue_is_parking(venue):
                            base += f" <b>{venue}</b> avtoturargohida bo‘lib o‘tadi."
                        else:
                            base += f" <b>{venue}</b> manzilida bo‘lib o‘tadi."
                    else:
                        base += f" <b>{venue}</b> manzilida bo‘lib o‘tadi."
                else:
                    base += " bo‘lib o‘tadi."
            else:
                base += "."
        return base
    else:
        base = f"Регистрация на <b>{name}</b> завершена. Спасибо за интерес!\n\n"
        if gdate or venue:
            base += "Приходите на мероприятие в качестве гостя"
            if gdate or venue:
                base += " — оно пройдёт "
                if gdate:
                    base += f"<b>{gdate}</b>"
                if venue:
                    if gdate:
                        if _venue_is_parking(venue):
                            base += f" на парковке <b>{venue}</b>."
                        else:
                            base += f" на площадке <b>{venue}</b>."
                    else:
                        base += (
                            f" на парковке <b>{venue}</b>."
                            if _venue_is_parking(venue)
                            else f" на площадке <b>{venue}</b>."
                        )
                else:
                    base += "."
            else:
                base += "."
        return base


def registration_closed_bilingual_for_tenant(tenant: Any) -> str:
    """Bilingual closed message before language is known, tenant-branded."""
    # Use tenant name if available, else generic
    name = _tenant_name_or_default(tenant)
    # Build ru and uz parts using the per-lang helpers but strip HTML for simplicity?
    # Keep similar structure to old bilingual but with tenant name.
    ru = registration_closed_for_tenant("ru", tenant)
    uz = registration_closed_for_tenant("uz", tenant)
    # Remove HTML tags for bilingual plain? Keep as is but combine.
    # The old bilingual had plain text without <b> for some parts; we keep HTML.
    return f"{uz}\n\n{ru}"


def share_cta_for_tenant(lang: str, tenant: Any, handle: str = "") -> str:
    name = _tenant_name_or_default(tenant)
    # handle may be instagram handle
    if lang == "uz":
        if handle:
            return f"📸 Biletingizni Storiesda ulashing va bizni belgilang {handle} — {name}’da ko‘rishguncha!"
        return f"📸 Biletingizni Storiesda ulashing — {name}’da ko‘rishguncha!"
    else:
        if handle:
            return f"📸 Опубликуй свой билет в Stories и отметь нас {handle} — увидимся на {name}!"
        return f"📸 Опубликуй свой билет в Stories — увидимся на {name}!"


def ticket_copy_for_tenant(lang: str, tenant: Any) -> dict:
    """Return ticket date/place copy for a tenant, with fallback."""
    # Use event_date and venue if set, else old defaults
    ev_date = _event_date(tenant, lang)
    venue = _venue(tenant, lang)
    # For ticket, we want short lines: date and place
    # If tenant provides date, use it directly; otherwise fallback to _COPY logic handled in ticket.py
    # This helper returns dict with 'date' and 'place' if available.
    result: dict[str, str] = {}
    if ev_date:
        result["date"] = ev_date
    if venue:
        # Ticket place is usually uppercase venue
        result["place"] = venue.upper() if lang == "ru" else venue.upper()
    return result
