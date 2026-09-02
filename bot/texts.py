"""All user-facing text, in Russian (ru) and Uzbek (uz).

Use ``T(lang)`` to get the namespace for a language, e.g. ``T(lang).GREETING``.
Admin/moderation-facing strings (MODERATION_*) stay Russian-only.

Dates confirmed with the client:
- Participant entry: 11 September, 10:00-19:00.
- Guest event: 12-13 September, from 10:00, at the SOF EXPO parking.
"""
from types import SimpleNamespace

from .constants import DIRECTION_LABELS, DIRECTIONS_CANON, direction_label

# --- Russian ---
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
    SHARE_CTA="📸 Опубликуй свой билет в Stories и отметь нас {handle} — увидимся на Promotors Show!",
    SHARE_CTA_PLAIN="📸 Опубликуй свой билет в Stories — увидимся на Promotors Show!",
    BTN_SUBSCRIBE="Подписаться на канал",
    BTN_CHECK_SUBSCRIPTION="Я подписался ✅",
    BTN_SEND_PHONE="Отправить номер телефона ☎️",
    BTN_MY_NUMBER="Узнать свой номер",
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

# --- Uzbek ---
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
    SHARE_CTA="📸 Biletingizni Storiesda ulashing va bizni belgilang {handle} — Promotors Show’da ko‘rishguncha!",
    SHARE_CTA_PLAIN="📸 Biletingizni Storiesda ulashing — Promotors Show’da ko‘rishguncha!",
    BTN_SUBSCRIBE="Kanalga obuna bo‘lish",
    BTN_CHECK_SUBSCRIPTION="Obuna bo‘ldim ✅",
    BTN_SEND_PHONE="Telefon raqamni yuborish ☎️",
    BTN_MY_NUMBER="Raqamimni bilish",
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
    """Map a canonical direction to its label in ``lang``.

    Delegates to the DIRECTIONS table in constants.py, which is the single
    place a direction's names are defined.
    """
    return direction_label(canonical, lang)


# --- Language-independent data ---
# Canonical values stored in the DB / shown to admins, regardless of UI language.
COUNTRIES_CANON = ["Россия", "Узбекистан", "Таджикистан", "Казахстан", "Киргизия"]
# DIRECTIONS_CANON is imported from constants.py (single source of truth).

# --- Language picker (shown before the language is known) ---
ASK_LANGUAGE = "Tilni tanlang / Выберите язык:"
BTN_LANG_UZ = "🇺🇿 O‘zbekcha"
BTN_LANG_RU = "🇷🇺 Русский"

# Any-language labels used to match the "my number" reply-keyboard button.
MY_NUMBER_LABELS = {RU.BTN_MY_NUMBER, UZ.BTN_MY_NUMBER}

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
