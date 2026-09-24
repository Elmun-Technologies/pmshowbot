"""SQLite persistence for tenant-scoped Promotors Show data.

The project originally had one bot and one global SQLite namespace.  This
module now owns the migration to a tenant-aware schema and exposes both the
low-level :class:`Database` API and :meth:`Database.for_tenant` scoped views.
The scoped view is what bot dispatchers and tenant-admin pages use, making it
impossible for a normal query to accidentally omit its ``tenant_id`` filter.

SQLite calls stay in the stdlib and run via ``asyncio.to_thread`` so the bot's
single event loop is never blocked by disk I/O.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .security import EncryptionError, TokenCipher, hash_password, is_password_hash
from .sqlite_pool import connect_sqlite

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
DEFAULT_TENANT_SLUG = "promotors"
DEFAULT_TENANT_NAME = "Promotors Show"


@dataclass(frozen=True)
class Tenant:
    """Public tenant metadata.

    ``bot_token_encrypted`` is intentionally the ciphertext from SQLite.  Bot
    workers obtain the decrypted token through ``Database.get_tenant_token``;
    templates only receive ``token_configured`` / a masked representation.
    """

    id: int
    slug: str
    name: str
    is_active: bool
    bot_token_encrypted: str
    admin_chat_id: int
    required_channel: str
    channel_url: str
    instagram_handle: str
    instagram_url: str
    spreadsheet_id: str
    drive_folder_id: str
    admin_password: str
    created_at: str
    updated_at: str
    # Event branding — optional, empty means that sentence is omitted.
    event_date_text_ru: str = ""
    event_date_text_uz: str = ""
    event_venue_text_ru: str = ""
    event_venue_text_uz: str = ""
    event_guest_date_text_ru: str = ""
    event_guest_date_text_uz: str = ""
    # Extra participant instructions (arrival rules, show start). Empty = omitted.
    event_note_text_ru: str = ""
    event_note_text_uz: str = ""
    # Per-tenant switch. A process-wide REGISTRATION_CLOSED secret must not
    # close every bot — that is what made SPL Show answer «завершена».
    registration_closed: bool = False
    # Full participant-facing texts, editable in the panel.  Empty = the text is
    # assembled from the event fields above (historic behaviour).
    approved_text_ru: str = ""
    approved_text_uz: str = ""
    rejected_text_ru: str = ""
    rejected_text_uz: str = ""

    @property
    def bot_token(self) -> str:
        """Encrypted database value, retained under the schema's field name.

        Use :meth:`Database.get_tenant_token` for the short-lived decrypted
        value required by a polling worker; never render this property.
        """
        return self.bot_token_encrypted

    @property
    def token_configured(self) -> bool:
        return bool(self.bot_token_encrypted)

    @property
    def token_mask(self) -> str:
        """Never expose the Telegram token in a normal data object."""
        return "***" if self.bot_token_encrypted else ""

    @property
    def password_configured(self) -> bool:
        return bool(self.admin_password)


@dataclass
class Direction:
    """One participation direction, tenant-scoped, optionally hierarchical.

    ``exclusive_group`` puts this category into a single-choice ("mutually
    exclusive") group: categories of one tenant sharing the same non-empty
    value form a group in which a participant may pick only ONE option — a new
    pick replaces the previous one.  An empty value means the category combines
    freely with any other.
    """

    id: int
    tenant_id: int
    parent_id: Optional[int]
    canonical: str
    label_ru: str
    label_uz: str
    slug: str
    sort_order: int
    is_active: bool
    created_at: str
    updated_at: str
    exclusive_group: str = ""


@dataclass
class Application:
    """One registration application, always belonging to one tenant."""

    id: int
    user_id: int
    username: str
    country: str
    plate: str
    direction: str
    phone: str
    photo_file_ids: list[str]
    photo_paths: list[str]
    status: str
    reg_number: Optional[int]
    created_at: str
    processed_at: Optional[str]
    processed_by: Optional[str]
    language: str = "ru"
    full_name: str = ""
    # Close-ups of what the participant changed on the car (hood, trunk, audio…).
    mod_file_ids: list[str] = field(default_factory=list)
    mod_paths: list[str] = field(default_factory=list)
    # Photo submitted for the personal event badge.
    badge_photo_file_id: str = ""
    badge_photo_path: str = ""
    tenant_id: int = 0
    # Optional FK to the new directions table; ``direction`` string stays canonical
    # for backward compatibility (e.g. "SPL Тюнинг — Show").
    direction_id: Optional[int] = None
    # Telegram message id of this application's moderation card (admin chat).
    card_message_id: Optional[int] = None


_TENANTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    slug             TEXT NOT NULL UNIQUE,
    name             TEXT NOT NULL,
    is_active        INTEGER NOT NULL DEFAULT 1,
    bot_token        TEXT NOT NULL DEFAULT '',
    admin_chat_id    INTEGER NOT NULL DEFAULT 0,
    required_channel TEXT NOT NULL DEFAULT '',
    channel_url      TEXT NOT NULL DEFAULT '',
    instagram_handle TEXT NOT NULL DEFAULT '',
    instagram_url    TEXT NOT NULL DEFAULT '',
    spreadsheet_id   TEXT NOT NULL DEFAULT '',
    drive_folder_id  TEXT NOT NULL DEFAULT '',
    admin_password   TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""

_TENANTS_EVENT_MIGRATIONS = [
    ("event_date_text_ru", "ALTER TABLE tenants ADD COLUMN event_date_text_ru TEXT NOT NULL DEFAULT ''"),
    ("event_date_text_uz", "ALTER TABLE tenants ADD COLUMN event_date_text_uz TEXT NOT NULL DEFAULT ''"),
    ("event_venue_text_ru", "ALTER TABLE tenants ADD COLUMN event_venue_text_ru TEXT NOT NULL DEFAULT ''"),
    ("event_venue_text_uz", "ALTER TABLE tenants ADD COLUMN event_venue_text_uz TEXT NOT NULL DEFAULT ''"),
    ("event_guest_date_text_ru", "ALTER TABLE tenants ADD COLUMN event_guest_date_text_ru TEXT NOT NULL DEFAULT ''"),
    ("event_guest_date_text_uz", "ALTER TABLE tenants ADD COLUMN event_guest_date_text_uz TEXT NOT NULL DEFAULT ''"),
    ("event_note_text_ru", "ALTER TABLE tenants ADD COLUMN event_note_text_ru TEXT NOT NULL DEFAULT ''"),
    ("event_note_text_uz", "ALTER TABLE tenants ADD COLUMN event_note_text_uz TEXT NOT NULL DEFAULT ''"),
    ("registration_closed", "ALTER TABLE tenants ADD COLUMN registration_closed INTEGER NOT NULL DEFAULT 0"),
    ("approved_text_ru", "ALTER TABLE tenants ADD COLUMN approved_text_ru TEXT NOT NULL DEFAULT ''"),
    ("approved_text_uz", "ALTER TABLE tenants ADD COLUMN approved_text_uz TEXT NOT NULL DEFAULT ''"),
    ("rejected_text_ru", "ALTER TABLE tenants ADD COLUMN rejected_text_ru TEXT NOT NULL DEFAULT ''"),
    ("rejected_text_uz", "ALTER TABLE tenants ADD COLUMN rejected_text_uz TEXT NOT NULL DEFAULT ''"),
]

# Editable message templates (see bot.texts.render_template for placeholders).
MESSAGE_TEMPLATE_FIELDS = (
    "approved_text_ru", "approved_text_uz", "rejected_text_ru", "rejected_text_uz",
)

# SPL Show, Tashkent INDEX.
#
# Only the venue is seeded.  The arrival date/time and the participant note used
# to be seeded too — and re-seeded on *every* startup: an empty field was filled
# again, and any arrival date naming 3 October was "corrected" back to the 2nd.
# The client reported «после одобрения приходит сообщение с неправильными датами,
# временем» and could not fix it in the panel, because the next restart put the
# old values back.  Dates are now whatever the panel says, and nothing else.
SPL_EVENT_COPY = {
    "event_venue_text_ru": "Tashkent INDEX",
    "event_venue_text_uz": "Tashkent INDEX",
}

# Every schedule value an earlier build of this bot seeded (or migrated to) for
# SPL.  The one-time migration below clears these — and only these — from the
# SPL tenant, so the wrong date/time disappears from the approval message and
# the ticket while anything typed by hand in the panel is kept.
_SPL_SEEDED_SCHEDULE_VALUES = frozenset({
    "02 октября 2026 с 17:00 до 22:00",
    "02-oktyabr 2026, soat 17:00 dan 22:00 gacha",
    "2 октября до 22:00",
    "2-oktyabr soat 22:00 gacha",
    "11 сентября 2026 с 10:00 до 19:00",
    "11-sentyabr 2026, 10:00 dan 19:00 gacha",
    "12 и 13 сентября с 10:00",
    "12 va 13-sentyabr, 10:00 dan",
    "03 октября 2026 с 12:00",
    "03-oktyabr 2026, soat 12:00 dan",
    "3 октября с 12:00",
    "3-oktyabr, soat 12:00 dan",
    (
        "Площадка — Tashkent INDEX. "
        "03 октября 2026 с 09:00 участники должны находиться рядом со своими автомобилями."
    ),
    (
        "Maydon — Tashkent INDEX. "
        "Tadbir ishtirokchilari 03-oktyabr 2026 kuni soat 09:00 dan boshlab "
        "avtomobillari yonida bo‘lishlari shart."
    ),
    (
        "Начало шоу — 3 октября в 12:00, Tashkent INDEX. "
        "3 октября с 09:00 участники должны находиться рядом со своими автомобилями."
    ),
    (
        "Shou boshlanishi — 3-oktyabr soat 12:00, Tashkent INDEX. "
        "Tadbir ishtirokchilari 3-oktyabr kuni soat 09:00 dan boshlab "
        "avtomobillari yonida bo‘lishlari shart."
    ),
})
SPL_SCHEDULE_FIELDS = (
    "event_date_text_ru", "event_date_text_uz", "event_note_text_ru", "event_note_text_uz",
)
# SPL is "only registered participants": a guest date is what put an event time
# into the rejection message («при отклонении — неправильное время»).
SPL_CLEARED_EVENT_FIELDS = ("event_guest_date_text_ru", "event_guest_date_text_uz")
# Bumped when the SPL one-time migration changes; stored per tenant in app_meta.
SPL_MIGRATION_KEY = "spl_messages_2026_09"
_SPL_SLUGS = frozenset({"splshow", "spl", "spl-show"})
_SPL_NAMES = frozenset({"spl show", "spl"})

_THIRD_OF_OCTOBER = re.compile(
    r"(?<![\d])0?3\s*[.\-/]?\s*(?:0?10(?:[.\-/]\s*\d{2,4})?|октябр\w*|окт\w*|oktyabr\w*|okt\w*)",
    re.IGNORECASE,
)

# «Везде нужно изменить Spl Show на SPL Show» — the brand is written in capitals.
_SPL_WORD = re.compile(r"\bspl\b", re.IGNORECASE)


def normalize_spl_brand(text: str) -> str:
    """``Spl Show`` / ``spl show`` → ``SPL Show`` (only the word "SPL" changes)."""
    if not text:
        return text
    return _SPL_WORD.sub("SPL", text)


def mentions_third_of_october(text: str) -> bool:
    """True when an event date names 3 October (in any of the usual spellings)."""
    return bool(_THIRD_OF_OCTOBER.search(text or ""))


def is_spl_tenant(slug: str, name: str = "") -> bool:
    """True for the SPL Show tenant, whatever slug the panel used.

    The exact slugs and names are the documented ones; the extra "spl" + "show"
    check covers a tenant created by hand as «SPL Show 2026» or
    ``spl-show-tashkent`` — for those the seed (and the 3-October arrival-date
    correction above) has to work too, or the wrong date stays forever.
    """
    slug = (slug or "").strip().lower()
    name = (name or "").strip().lower()
    if slug in _SPL_SLUGS or name in _SPL_NAMES:
        return True
    return any("spl" in value and "show" in value for value in (slug, name))


# ---------------------------------------------------------------------------
# SPL Show directions, confirmed with the client for the 2–3 October event.
#
# ``canonical`` is what lands in ``applications.direction`` (and in every
# export); ``label_ru`` / ``label_uz`` are the buttons a participant sees.
# Children store their canonical pre-joined as "Parent — Child" so the old
# single-level rows and the new ones read the same in the admin panel.
# ---------------------------------------------------------------------------
SPL_ROOT_DIRECTIONS = [
    {"canonical": "SQ", "label_ru": "SQ - Качество звучания", "label_uz": "SQ - Ovoz sifati", "slug": "sq", "sort": 0},
    {"canonical": "Выставка", "label_ru": "Выставка", "label_uz": "Ko'rgazma", "slug": "vistavka", "sort": 1},
    {"canonical": "Тюнинг", "label_ru": "Тюнинг", "label_uz": "Tuning", "slug": "tuning", "sort": 2},
    {"canonical": "SPL Автозвук", "label_ru": "SPL Автозвук", "label_uz": "SPL Avtozvuk", "slug": "spl_avtozvuk", "sort": 3},
]

SPL_TUNING_CHILDREN = [
    {"canonical": "Тюнинг — Т1 Новичок", "label_ru": "Т1 Новичок", "label_uz": "T1 Yangi", "slug": "tuning_t1_novichok", "sort": 0},
    {"canonical": "Тюнинг — Т2 Профессионал", "label_ru": "Т2 Профессионал", "label_uz": "T2 Professional", "slug": "tuning_t2_pro", "sort": 1},
]

# The SPL Avtozvuk categories, as confirmed by the client (September 2026).
#
# ``group`` puts a category into a single-choice ("mutually exclusive") group:
# categories sharing one value can only be picked ONE at a time in the bot —
# a new pick automatically replaces the previous one from the same group
# (client requirement, September 2026):
#
#   spl        — EITHER a Show OR a Sport category (one of the eight total);
#   bass_race  — SPL Game 129.99 / 139.99 / 149.99 — only one;
#   front      — SPL Front Лайт / Стандарт / Максимум — only one;
#   rear       — SPL Тыл Стандарт / Максимум — only one.
SPL_AUTOSOUND_CHILDREN = [
    {"canonical": "SPL Автозвук — SPL Sport Багажник 2К", "label_ru": "SPL Sport Багажник 2К", "label_uz": "SPL Sport Bagajnik 2K", "slug": "spl_sport_trunk_2k", "sort": 0, "group": "spl"},
    {"canonical": "SPL Автозвук — SPL Sport Багажник 4К", "label_ru": "SPL Sport Багажник 4К", "label_uz": "SPL Sport Bagajnik 4K", "slug": "spl_sport_trunk_4k", "sort": 1, "group": "spl"},
    {"canonical": "SPL Автозвук — SPL Sport Максимум", "label_ru": "SPL Sport Максимум", "label_uz": "SPL Sport Maksimum", "slug": "spl_sport_max", "sort": 2, "group": "spl"},
    {"canonical": "SPL Автозвук — SPL Sport Салон", "label_ru": "SPL Sport Салон", "label_uz": "SPL Sport Salon", "slug": "spl_sport_salon", "sort": 3, "group": "spl"},
    {"canonical": "SPL Автозвук — SPL Show Лайт", "label_ru": "SPL Show Лайт", "label_uz": "SPL Show Layt", "slug": "spl_show_light", "sort": 4, "group": "spl"},
    {"canonical": "SPL Автозвук — SPL Show Стандарт", "label_ru": "SPL Show Стандарт", "label_uz": "SPL Show Standart", "slug": "spl_show_standard", "sort": 5, "group": "spl"},
    {"canonical": "SPL Автозвук — SPL Show Профи", "label_ru": "SPL Show Профи", "label_uz": "SPL Show Profi", "slug": "spl_show_pro", "sort": 6, "group": "spl"},
    {"canonical": "SPL Автозвук — SPL Show Полубронь", "label_ru": "SPL Show Полубронь", "label_uz": "SPL Show Polubron", "slug": "spl_show_halfarmor", "sort": 7, "group": "spl"},
    {"canonical": "SPL Автозвук — SPL Front Лайт", "label_ru": "SPL Front Лайт", "label_uz": "SPL Front Layt", "slug": "spl_front_light", "sort": 8, "group": "front"},
    {"canonical": "SPL Автозвук — SPL Front Стандарт", "label_ru": "SPL Front Стандарт", "label_uz": "SPL Front Standart", "slug": "spl_front_standard", "sort": 9, "group": "front"},
    {"canonical": "SPL Автозвук — SPL Front Максимум", "label_ru": "SPL Front Максимум", "label_uz": "SPL Front Maksimum", "slug": "spl_front_max", "sort": 10, "group": "front"},
    {"canonical": "SPL Автозвук — SPL Тыл Стандарт", "label_ru": "SPL Тыл Стандарт", "label_uz": "SPL Orqa Standart", "slug": "spl_rear_standard", "sort": 11, "group": "rear"},
    {"canonical": "SPL Автозвук — SPL Тыл Максимум", "label_ru": "SPL Тыл Максимум", "label_uz": "SPL Orqa Maksimum", "slug": "spl_rear_max", "sort": 12, "group": "rear"},
    {"canonical": "SPL Автозвук — SPL Game 129.99", "label_ru": "SPL Game 129.99", "label_uz": "SPL Game 129.99", "slug": "spl_game_129", "sort": 13, "group": "bass_race"},
    {"canonical": "SPL Автозвук — SPL Game 139.99", "label_ru": "SPL Game 139.99", "label_uz": "SPL Game 139.99", "slug": "spl_game_139", "sort": 14, "group": "bass_race"},
    {"canonical": "SPL Автозвук — SPL Game 149.99", "label_ru": "SPL Game 149.99", "label_uz": "SPL Game 149.99", "slug": "spl_game_149", "sort": 15, "group": "bass_race"},
]

# Seeded single-choice groups: slug → group.  Used by the one-time migration
# that brings an existing deployment's rows to the grouped structure.
SPL_EXCLUSIVE_GROUPS = {
    spec["slug"]: spec["group"] for spec in SPL_AUTOSOUND_CHILDREN if spec.get("group")
}

# Lists shipped by earlier builds.  A deployment whose SPL Avtozvuk children
# still match one of them exactly is migrated to the list above; a list edited
# by hand in the panel is left alone.
SPL_AUTOSOUND_PREVIOUS = [
    {"slug": "spl_front", "canonical": "SPL Автозвук — SPL Front"},
    {"slug": "spl_rear", "canonical": "SPL Автозвук — SPL Тыл"},
    {"slug": "spl_game", "canonical": "SPL Автозвук — SPL Game (129/139/149)"},
    {"slug": "spl_sport_show", "canonical": "SPL Автозвук — SPL Sport / SPL Show"},
]
SPL_AUTOSOUND_STALE = [
    {"slug": "spl", "canonical": "SPL Автозвук — SPL"},
    {"slug": "spl_t1", "canonical": "SPL Автозвук — SPL Т1"},
    {"slug": "spl_t2", "canonical": "SPL Автозвук — SPL Т2"},
]

_APPLICATIONS_CREATE = """
CREATE TABLE applications (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    user_id             INTEGER NOT NULL,
    username            TEXT NOT NULL DEFAULT '',
    country             TEXT NOT NULL DEFAULT '',
    plate               TEXT NOT NULL DEFAULT '',
    direction           TEXT NOT NULL DEFAULT '',
    phone               TEXT NOT NULL DEFAULT '',
    photo_file_ids      TEXT NOT NULL DEFAULT '[]',
    photo_paths         TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'pending',
    reg_number          INTEGER,
    created_at          TEXT NOT NULL,
    processed_at        TEXT,
    processed_by        TEXT,
    language            TEXT NOT NULL DEFAULT 'ru',
    full_name           TEXT NOT NULL DEFAULT '',
    mod_file_ids        TEXT NOT NULL DEFAULT '[]',
    mod_paths           TEXT NOT NULL DEFAULT '[]',
    badge_photo_file_id TEXT NOT NULL DEFAULT '',
    badge_photo_path    TEXT NOT NULL DEFAULT '',
    direction_id        INTEGER REFERENCES directions(id) ON DELETE SET NULL,
    -- Message id of the moderation card in the admin chat, so a decision made
    -- in the web panel can update that very card (buttons removed + status).
    card_message_id     INTEGER
);
"""

_DIRECTIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS directions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    parent_id   INTEGER REFERENCES directions(id) ON DELETE SET NULL,
    canonical   TEXT NOT NULL,
    label_ru    TEXT NOT NULL,
    label_uz    TEXT NOT NULL,
    slug        TEXT NOT NULL,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    is_active   INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    exclusive_group TEXT NOT NULL DEFAULT '',
    UNIQUE(tenant_id, canonical),
    UNIQUE(tenant_id, slug)
);
"""

# Lightweight migrations for ``directions`` tables created before the
# single-choice group column existed.
_DIRECTION_MIGRATIONS = [
    (
        "exclusive_group",
        "ALTER TABLE directions ADD COLUMN exclusive_group TEXT NOT NULL DEFAULT ''",
    ),
]

_BOT_USERS_CREATE = """
CREATE TABLE bot_users (
    tenant_id INTEGER NOT NULL REFERENCES tenants(id) ON DELETE RESTRICT,
    user_id    INTEGER NOT NULL,
    username   TEXT NOT NULL DEFAULT '',
    language   TEXT NOT NULL DEFAULT 'ru',
    first_seen TEXT NOT NULL,
    last_seen  TEXT NOT NULL,
    PRIMARY KEY (tenant_id, user_id)
);
"""

# Lightweight migrations for old ``applications`` tables before they are
# rebuilt with a real tenant FK below.
_APPLICATION_MIGRATIONS = [
    ("language", "ALTER TABLE applications ADD COLUMN language TEXT NOT NULL DEFAULT 'ru'"),
    ("full_name", "ALTER TABLE applications ADD COLUMN full_name TEXT NOT NULL DEFAULT ''"),
    ("mod_file_ids", "ALTER TABLE applications ADD COLUMN mod_file_ids TEXT NOT NULL DEFAULT '[]'"),
    ("mod_paths", "ALTER TABLE applications ADD COLUMN mod_paths TEXT NOT NULL DEFAULT '[]'"),
    ("badge_photo_file_id", "ALTER TABLE applications ADD COLUMN badge_photo_file_id TEXT NOT NULL DEFAULT ''"),
    ("badge_photo_path", "ALTER TABLE applications ADD COLUMN badge_photo_path TEXT NOT NULL DEFAULT ''"),
    ("tenant_id", "ALTER TABLE applications ADD COLUMN tenant_id INTEGER"),
    ("direction_id", "ALTER TABLE applications ADD COLUMN direction_id INTEGER REFERENCES directions(id) ON DELETE SET NULL"),
    ("card_message_id", "ALTER TABLE applications ADD COLUMN card_message_id INTEGER"),
]

_DIRECTION_RENAMES = [("Дрифт", "Adrenaline Drift")]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_list(value: Any) -> list[str]:
    """Read old/corrupt JSON defensively rather than breaking the whole panel."""
    try:
        decoded = json.loads(value or "[]")
        return decoded if isinstance(decoded, list) else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []


def _row_to_direction(row: sqlite3.Row) -> Direction:
    keys = row.keys()
    return Direction(
        id=int(row["id"]),
        tenant_id=int(row["tenant_id"]),
        parent_id=int(row["parent_id"]) if row["parent_id"] is not None else None,
        canonical=str(row["canonical"]),
        label_ru=str(row["label_ru"]),
        label_uz=str(row["label_uz"]),
        slug=str(row["slug"]),
        sort_order=int(row["sort_order"] or 0),
        is_active=bool(row["is_active"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        exclusive_group=(
            str(row["exclusive_group"] or "") if "exclusive_group" in keys else ""
        ),
    )


def _row_to_application(row: sqlite3.Row) -> Application:
    keys = row.keys()
    return Application(
        id=row["id"],
        tenant_id=int(row["tenant_id"]) if "tenant_id" in keys and row["tenant_id"] is not None else 0,
        user_id=row["user_id"],
        username=row["username"],
        country=row["country"],
        plate=row["plate"],
        direction=row["direction"],
        phone=row["phone"],
        photo_file_ids=_json_list(row["photo_file_ids"]),
        photo_paths=_json_list(row["photo_paths"]),
        status=row["status"],
        reg_number=row["reg_number"],
        created_at=row["created_at"],
        processed_at=row["processed_at"],
        processed_by=row["processed_by"],
        language=row["language"] if "language" in keys else "ru",
        full_name=row["full_name"] if "full_name" in keys else "",
        mod_file_ids=_json_list(row["mod_file_ids"]) if "mod_file_ids" in keys else [],
        mod_paths=_json_list(row["mod_paths"]) if "mod_paths" in keys else [],
        badge_photo_file_id=row["badge_photo_file_id"] if "badge_photo_file_id" in keys else "",
        badge_photo_path=row["badge_photo_path"] if "badge_photo_path" in keys else "",
        direction_id=int(row["direction_id"]) if "direction_id" in keys and row["direction_id"] is not None else None,
        card_message_id=(
            int(row["card_message_id"])
            if "card_message_id" in keys and row["card_message_id"] is not None
            else None
        ),
    )


def _row_to_tenant(row: sqlite3.Row) -> Tenant:
    keys = row.keys()
    return Tenant(
        id=int(row["id"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        is_active=bool(row["is_active"]),
        bot_token_encrypted=str(row["bot_token"] or ""),
        admin_chat_id=int(row["admin_chat_id"] or 0),
        required_channel=str(row["required_channel"] or ""),
        channel_url=str(row["channel_url"] or ""),
        instagram_handle=str(row["instagram_handle"] or ""),
        instagram_url=str(row["instagram_url"] or ""),
        spreadsheet_id=str(row["spreadsheet_id"] or ""),
        drive_folder_id=str(row["drive_folder_id"] or ""),
        admin_password=str(row["admin_password"] or ""),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        event_date_text_ru=str(row["event_date_text_ru"] or "") if "event_date_text_ru" in keys else "",
        event_date_text_uz=str(row["event_date_text_uz"] or "") if "event_date_text_uz" in keys else "",
        event_venue_text_ru=str(row["event_venue_text_ru"] or "") if "event_venue_text_ru" in keys else "",
        event_venue_text_uz=str(row["event_venue_text_uz"] or "") if "event_venue_text_uz" in keys else "",
        event_guest_date_text_ru=str(row["event_guest_date_text_ru"] or "") if "event_guest_date_text_ru" in keys else "",
        event_guest_date_text_uz=str(row["event_guest_date_text_uz"] or "") if "event_guest_date_text_uz" in keys else "",
        event_note_text_ru=str(row["event_note_text_ru"] or "") if "event_note_text_ru" in keys else "",
        event_note_text_uz=str(row["event_note_text_uz"] or "") if "event_note_text_uz" in keys else "",
        registration_closed=bool(row["registration_closed"]) if "registration_closed" in keys else False,
        **{
            key: (str(row[key] or "") if key in keys else "")
            for key in MESSAGE_TEMPLATE_FIELDS
        },
    )


class TenantDatabase:
    """A tenant-bound facade over :class:`Database`.

    Handlers receive this instead of the global database.  Every application,
    user and broadcast operation therefore carries the tenant id automatically.
    """

    def __init__(self, database: "Database", tenant_id: int):
        self._database = database
        self.tenant_id = int(tenant_id)

    async def list_applications(
        self, status: Optional[str] = None, search: Optional[str] = None, limit: int = 500
    ) -> list[Application]:
        return await self._database.list_applications(status, search, limit, tenant_id=self.tenant_id)

    async def stats(self) -> dict:
        return await self._database.stats(tenant_id=self.tenant_id)

    async def create_application(self, **kwargs) -> int:
        return await self._database.create_application(tenant_id=self.tenant_id, **kwargs)

    async def get_application(self, app_id: int) -> Optional[Application]:
        return await self._database.get_application(app_id, tenant_id=self.tenant_id)

    async def get_latest_for_user(self, user_id: int) -> Optional[Application]:
        return await self._database.get_latest_for_user(user_id, tenant_id=self.tenant_id)

    async def has_active_application(self, user_id: int) -> Optional[Application]:
        return await self._database.has_active_application(user_id, tenant_id=self.tenant_id)

    async def set_badge_photo(self, user_id: int, file_id: str, path: str) -> Optional[int]:
        return await self._database.set_badge_photo(
            user_id, file_id, path, tenant_id=self.tenant_id
        )

    async def other_tenants_for_user(self, user_id: int) -> dict[str, int]:
        """Diagnostics only: other tenants holding applications of this user."""
        counter = getattr(self._database, "user_tenant_counts", None)
        if counter is None:  # pragma: no cover - very old facades
            return {}
        return await counter(user_id, exclude_tenant_id=int(self.tenant_id))

    async def delete_application(self, app_id: int, *, remove_files: bool = True) -> Optional[Application]:
        """Remove one of this tenant's applications permanently."""
        return await self._database.delete_application(
            app_id, tenant_id=self.tenant_id, remove_files=remove_files
        )

    async def set_card_message_id(self, app_id: int, message_id: Optional[int]) -> bool:
        return await self._database.set_card_message_id(
            app_id, message_id, tenant_id=self.tenant_id
        )

    async def get_user_language(self, user_id: int) -> str:
        return await self._database.get_user_language(user_id, tenant_id=self.tenant_id)

    async def approve(self, app_id: int, moderator: str) -> Optional[int]:
        return await self._database.approve(app_id, moderator, tenant_id=self.tenant_id)

    async def reject(self, app_id: int, moderator: str) -> bool:
        return await self._database.reject(app_id, moderator, tenant_id=self.tenant_id)

    async def set_status(self, app_id: int, status: str, moderator: str) -> bool:
        return await self._database.set_status(
            app_id, status, moderator, tenant_id=self.tenant_id
        )

    async def touch_user(self, user_id: int, username: str = "", language: str = "ru") -> None:
        await self._database.touch_user(
            user_id, username, language, tenant_id=self.tenant_id
        )

    async def recipients(
        self,
        audience: str,
        languages: Optional[list[str]] = None,
        directions: Optional[list[str]] = None,
    ) -> list[tuple[int, str]]:
        return await self._database.recipients(
            audience, languages, directions, tenant_id=self.tenant_id
        )

    async def audience_counts(self) -> dict[str, int]:
        return await self._database.audience_counts(tenant_id=self.tenant_id)

    # Directions (tenant-scoped)
    async def list_directions(self, active_only: bool = True) -> list[Direction]:
        return await self._database.list_directions(tenant_id=self.tenant_id, active_only=active_only)

    async def get_direction(self, direction_id: int) -> Optional[Direction]:
        return await self._database.get_direction(direction_id, tenant_id=self.tenant_id)

    async def create_direction(self, **kwargs) -> Direction:
        return await self._database.create_direction(tenant_id=self.tenant_id, **kwargs)

    async def update_direction(self, direction_id: int, **kwargs) -> Optional[Direction]:
        return await self._database.update_direction(direction_id, tenant_id=self.tenant_id, **kwargs)

    async def delete_direction(self, direction_id: int) -> bool:
        return await self._database.delete_direction(direction_id, tenant_id=self.tenant_id)

    async def list_all_directions(self) -> list[Direction]:
        return await self._database.list_directions(tenant_id=self.tenant_id, active_only=False)


class Database:
    """SQLite database plus idempotent legacy-to-tenant migration.

    ``bootstrap`` is normally the process-level :class:`bot.config.Config`.
    Its legacy environment values are used exactly once to seed the mandatory
    ``promotors`` tenant.  Future tenants are entirely database-driven.
    """

    def __init__(
        self,
        path: str,
        *,
        encryption_key: str | bytes | None = None,
        bootstrap: Any | Mapping[str, Any] | None = None,
    ):
        self.path = path
        self._bootstrap = bootstrap
        self._cipher = TokenCipher(encryption_key) if encryption_key else None
        # One SQLite connection per worker thread, reused for the process
        # lifetime.  ``asyncio.to_thread`` runs every query on a small, stable
        # pool of threads, so this keeps the connection count bounded while
        # removing per-query connection setup.
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    def _new_connection(self) -> sqlite3.Connection:
        """Open and tune one SQLite connection.

        Opening a connection is not free: it is a file open plus, for the
        pragmas, real disk I/O.  ``journal_mode=WAL`` is persisted in the
        database file itself, yet it was re-issued on every single query — and
        on a network volume (Fly.io) that write-and-fsync dominated the bot's
        response time.  The pragmas therefore run once per connection, and the
        connection is then reused.  See :mod:`bot.sqlite_pool`.
        """
        return connect_sqlite(self.path)

    def _connect(self) -> sqlite3.Connection:
        """Return this thread's cached connection, opening it on first use.

        The returned object is deliberately *not* closed by callers: every call
        site uses ``with self._connect() as conn``, and for ``sqlite3`` that
        context manager commits (or rolls back) the transaction and leaves the
        connection open — exactly the semantics needed for pooling.
        """
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = self._new_connection()
        self._local.conn = conn
        with self._connections_lock:
            self._connections.append(conn)
        return conn

    def close(self) -> None:
        """Close every pooled connection (tests and shutdown)."""
        with self._connections_lock:
            connections, self._connections = self._connections, []
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover - already unusable
                pass
        self._local = threading.local()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
        return bool(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
            ).fetchone()
        )

    @staticmethod
    def _bootstrap_value(bootstrap: Any | Mapping[str, Any] | None, *names: str, default=""):
        if bootstrap is None:
            return default
        for name in names:
            if isinstance(bootstrap, Mapping):
                value = bootstrap.get(name)
            else:
                value = getattr(bootstrap, name, None)
            if value is not None and value != "":
                return value
        return default

    def _encrypt_token(self, token: str) -> str:
        token = (token or "").strip()
        if not token:
            return ""
        if self._cipher is None:
            raise EncryptionError(
                "Cannot store a tenant bot token without ENCRYPTION_KEY. "
                "Set a Fernet key before running the migration."
            )
        return self._cipher.encrypt(token)

    def _decrypt_token(self, ciphertext: str) -> str:
        if not ciphertext:
            return ""
        if self._cipher is None:
            raise EncryptionError(
                "ENCRYPTION_KEY is required to start a tenant with an encrypted bot token"
            )
        return self._cipher.decrypt(ciphertext)

    def _default_tenant_values(self) -> dict[str, Any]:
        b = self._bootstrap
        raw_admin_id = self._bootstrap_value(b, "legacy_admin_chat_id", "admin_chat_id", default=0)
        try:
            admin_chat_id = int(raw_admin_id or 0)
        except (TypeError, ValueError):
            admin_chat_id = 0
        password = str(
            self._bootstrap_value(b, "legacy_admin_password", "admin_password", default="") or ""
        )
        return {
            "slug": DEFAULT_TENANT_SLUG,
            "name": str(self._bootstrap_value(b, "legacy_tenant_name", default=DEFAULT_TENANT_NAME)),
            "is_active": 1,
            "bot_token": str(self._bootstrap_value(b, "legacy_bot_token", "bot_token", default="") or ""),
            "admin_chat_id": admin_chat_id,
            "required_channel": str(
                self._bootstrap_value(b, "legacy_required_channel", "required_channel", default="") or ""
            ),
            "channel_url": str(self._bootstrap_value(b, "legacy_channel_url", "channel_url", default="") or ""),
            "instagram_handle": str(
                self._bootstrap_value(b, "legacy_instagram_handle", "instagram_handle", default="") or ""
            ),
            "instagram_url": str(
                self._bootstrap_value(b, "legacy_instagram_url", "instagram_url", default="") or ""
            ),
            "spreadsheet_id": str(
                self._bootstrap_value(b, "legacy_spreadsheet_id", "spreadsheet_id", default="") or ""
            ),
            "drive_folder_id": str(
                self._bootstrap_value(b, "legacy_drive_folder_id", "drive_folder_id", default="") or ""
            ),
            "admin_password": password,
            # Promotors defaults — client-confirmed dates/venue, kept for regression.
            "event_date_text_ru": "11 сентября 2026 с 10:00 до 19:00",
            "event_date_text_uz": "11-sentyabr 2026, 10:00 dan 19:00 gacha",
            "event_venue_text_ru": "SOF EXPO",
            "event_venue_text_uz": "SOF EXPO",
            "event_guest_date_text_ru": "12 и 13 сентября с 10:00",
            "event_guest_date_text_uz": "12 va 13-sentyabr, 10:00 dan",
        }

    def _ensure_default_tenant(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT id FROM tenants WHERE slug = ?", (DEFAULT_TENANT_SLUG,)
        ).fetchone()
        if row:
            return int(row["id"])
        values = self._default_tenant_values()
        now = _now()
        encrypted = self._encrypt_token(values["bot_token"])
        password = values["admin_password"]
        if password and not is_password_hash(password):
            password = hash_password(password)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tenants)").fetchall()} if self._table_exists(conn, "tenants") else set()
        # Base insert - include event columns if they exist
        base_cols = [
            "slug", "name", "is_active", "bot_token", "admin_chat_id", "required_channel",
            "channel_url", "instagram_handle", "instagram_url", "spreadsheet_id",
            "drive_folder_id", "admin_password", "created_at", "updated_at",
        ]
        base_vals = [
            values["slug"], values["name"], values["is_active"], encrypted,
            values["admin_chat_id"], values["required_channel"], values["channel_url"],
            values["instagram_handle"], values["instagram_url"], values["spreadsheet_id"],
            values["drive_folder_id"], password, now, now,
        ]
        extra_cols = []
        extra_vals = []
        for k in (
            "event_date_text_ru", "event_date_text_uz",
            "event_venue_text_ru", "event_venue_text_uz",
            "event_guest_date_text_ru", "event_guest_date_text_uz",
        ):
            if k in cols:
                extra_cols.append(k)
                extra_vals.append(values.get(k, ""))
        all_cols = base_cols + extra_cols
        all_vals = base_vals + extra_vals
        placeholders = ", ".join("?" for _ in all_cols)
        conn.execute(
            f"INSERT INTO tenants ({', '.join(all_cols)}) VALUES ({placeholders})",
            tuple(all_vals),
        )
        row = conn.execute("SELECT id FROM tenants WHERE slug = ?", (DEFAULT_TENANT_SLUG,)).fetchone()
        return int(row["id"]) if row else 0

    def _create_applications_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(_APPLICATIONS_CREATE)

    def _create_bot_users_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(_BOT_USERS_CREATE)

    def _create_directions_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(_DIRECTIONS_SCHEMA)

    def _migrate_directions(self, conn: sqlite3.Connection) -> None:
        """Add the single-choice group column to old ``directions`` tables.

        When the column is created, the seeded SPL Avtozvuk categories get
        their confirmed groups backfilled **exactly once** — after that the
        panel is the only source of truth, so a group an admin changed (or
        cleared) is never overwritten by a restart.  Fresh databases get the
        groups straight from the seeds.
        """
        if not self._table_exists(conn, "directions"):
            return
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(directions)").fetchall()}
        added = False
        for col, ddl in _DIRECTION_MIGRATIONS:
            if col not in cols:
                try:
                    conn.execute(ddl)
                    added = True
                except sqlite3.OperationalError:
                    pass
        if added:
            for slug, group in SPL_EXCLUSIVE_GROUPS.items():
                conn.execute(
                    "UPDATE directions SET exclusive_group = ? "
                    "WHERE slug = ? AND exclusive_group = ''",
                    (group, slug),
                )

    def _migrate_tenants(self, conn: sqlite3.Connection) -> None:
        if not self._table_exists(conn, "tenants"):
            return
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tenants)").fetchall()}
        for col, ddl in _TENANTS_EVENT_MIGRATIONS:
            if col not in cols:
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
        # Seed promotors event fields if empty
        row = conn.execute("SELECT * FROM tenants WHERE slug = ?", (DEFAULT_TENANT_SLUG,)).fetchone()
        if row:
            defaults = self._default_tenant_values()
            updates: dict[str, str] = {}
            for k in (
                "event_date_text_ru", "event_date_text_uz",
                "event_venue_text_ru", "event_venue_text_uz",
                "event_guest_date_text_ru", "event_guest_date_text_uz",
            ):
                if k in cols:
                    try:
                        cur_val = row[k]
                    except Exception:
                        cur_val = ""
                    if not (cur_val or "").strip():
                        updates[k] = defaults.get(k, "")
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                params = list(updates.values()) + [_now(), row["id"]]
                conn.execute(f"UPDATE tenants SET {set_clause}, updated_at = ? WHERE id = ?", params)
        self._seed_spl_event(conn)

    @staticmethod
    def _meta_done(conn: sqlite3.Connection, key: str) -> bool:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        return conn.execute("SELECT 1 FROM app_meta WHERE key = ?", (key,)).fetchone() is not None

    @staticmethod
    def _meta_mark(conn: sqlite3.Connection, key: str) -> None:
        conn.execute(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES (?, ?)", (key, _now())
        )

    def _seed_spl_event(self, conn: sqlite3.Connection) -> None:
        """One-time clean-up of the SPL Show schedule and branding.

        Earlier builds re-seeded the SPL arrival date and note on **every**
        startup, so a correction typed in the panel was silently overwritten by
        the next deploy — the reported «неправильные даты, время» after approval.
        Now, once per tenant (tracked in ``app_meta``):

        * schedule values that an earlier build wrote are cleared — the approval
          then carries no date/time until the team types the real one in the
          panel; values typed by hand are kept;
        * the guest date is cleared (SPL is participants only), which keeps any
          event time out of the rejection message;
        * an empty venue gets «Tashkent INDEX»;
        * «Spl Show» in the tenant name is written «SPL Show».

        After that the panel is the only source of truth for these fields.
        """
        if not self._table_exists(conn, "tenants"):
            return
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(tenants)").fetchall()}
        for row in conn.execute("SELECT * FROM tenants").fetchall():
            if not is_spl_tenant(str(row["slug"] or ""), str(row["name"] or "")):
                continue
            key = f"{SPL_MIGRATION_KEY}:{int(row['id'])}"
            if self._meta_done(conn, key):
                continue
            updates: dict[str, str] = {}

            def current(name: str) -> str:
                try:
                    return str(row[name] or "").strip()
                except (KeyError, IndexError):
                    return ""

            for name in SPL_SCHEDULE_FIELDS + SPL_CLEARED_EVENT_FIELDS:
                if name not in cols:
                    continue
                value = current(name)
                seeded = value in _SPL_SEEDED_SCHEDULE_VALUES
                if name in SPL_CLEARED_EVENT_FIELDS and value:
                    seeded = True
                if seeded:
                    updates[name] = ""
            for name, value in SPL_EVENT_COPY.items():
                if name in cols and not current(name):
                    updates[name] = value
            name_fixed = normalize_spl_brand(str(row["name"] or ""))
            if name_fixed != row["name"]:
                updates["name"] = name_fixed
            if updates:
                logger.warning(
                    "[%s] SPL one-time migration: %s",
                    row["slug"],
                    ", ".join(f"{k}: {current(k)!r} -> {v!r}" for k, v in updates.items()),
                )
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                params = list(updates.values()) + [_now(), int(row["id"])]
                conn.execute(
                    f"UPDATE tenants SET {set_clause}, updated_at = ? WHERE id = ?",
                    params,
                )
            self._meta_mark(conn, key)

    def _migrate_applications(self, conn: sqlite3.Connection, default_tenant_id: int) -> None:
        if not self._table_exists(conn, "applications"):
            self._create_applications_table(conn)
            return

        columns = {r["name"] for r in conn.execute("PRAGMA table_info(applications)")}
        for column, ddl in _APPLICATION_MIGRATIONS:
            if column not in columns:
                try:
                    conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
                columns.add(column)
        conn.execute(
            "UPDATE applications SET tenant_id = ? WHERE tenant_id IS NULL", (default_tenant_id,)
        )
        for old, new in _DIRECTION_RENAMES:
            conn.execute(
                "UPDATE applications SET direction = ? WHERE direction = ?", (new, old)
            )

        foreign_keys = conn.execute("PRAGMA foreign_key_list(applications)").fetchall()
        has_tenant_fk = any(
            row["from"] == "tenant_id" and row["table"] == "tenants" for row in foreign_keys
        )
        if has_tenant_fk:
            return

        conn.execute("ALTER TABLE applications RENAME TO applications_legacy_tenant_migration")
        self._create_applications_table(conn)
        conn.execute(
            """
            INSERT INTO applications (
                id, tenant_id, user_id, username, country, plate, direction, phone,
                photo_file_ids, photo_paths, status, reg_number, created_at,
                processed_at, processed_by, language, full_name, mod_file_ids,
                mod_paths, badge_photo_file_id, badge_photo_path, direction_id
            )
            SELECT id, COALESCE(tenant_id, ?), user_id,
                   COALESCE(username, ''), COALESCE(country, ''), COALESCE(plate, ''),
                   COALESCE(direction, ''), COALESCE(phone, ''),
                   COALESCE(photo_file_ids, '[]'), COALESCE(photo_paths, '[]'),
                   COALESCE(status, 'pending'), reg_number, created_at, processed_at,
                   processed_by, COALESCE(language, 'ru'), COALESCE(full_name, ''),
                   COALESCE(mod_file_ids, '[]'), COALESCE(mod_paths, '[]'),
                   COALESCE(badge_photo_file_id, ''), COALESCE(badge_photo_path, ''),
                   direction_id
            FROM applications_legacy_tenant_migration
            """,
            (default_tenant_id,),
        )
        conn.execute("DROP TABLE applications_legacy_tenant_migration")

    def _migrate_bot_users(self, conn: sqlite3.Connection, default_tenant_id: int) -> None:
        if not self._table_exists(conn, "bot_users"):
            self._create_bot_users_table(conn)
            return
        info = conn.execute("PRAGMA table_info(bot_users)").fetchall()
        cols = {row["name"] for row in info}
        pk_cols = [row["name"] for row in sorted(info, key=lambda row: row["pk"]) if row["pk"]]
        has_composite_pk = pk_cols == ["tenant_id", "user_id"]
        if "tenant_id" in cols and has_composite_pk:
            conn.execute(
                "UPDATE bot_users SET tenant_id = ? WHERE tenant_id IS NULL", (default_tenant_id,)
            )
            return

        conn.execute("ALTER TABLE bot_users RENAME TO bot_users_legacy_tenant_migration")
        self._create_bot_users_table(conn)
        old_cols = {r["name"] for r in conn.execute("PRAGMA table_info(bot_users_legacy_tenant_migration)")}
        tenant_expr = "COALESCE(tenant_id, ?)" if "tenant_id" in old_cols else "?"
        username_expr = "COALESCE(username, '')" if "username" in old_cols else "''"
        language_expr = "COALESCE(language, 'ru')" if "language" in old_cols else "'ru'"
        first_expr = "COALESCE(first_seen, ?)" if "first_seen" in old_cols else "?"
        last_expr = "COALESCE(last_seen, ?)" if "last_seen" in old_cols else "?"
        now = _now()
        conn.execute(
            f"""
            INSERT OR IGNORE INTO bot_users
                (tenant_id, user_id, username, language, first_seen, last_seen)
            SELECT {tenant_expr}, user_id, {username_expr}, {language_expr},
                   {first_expr}, {last_expr}
            FROM bot_users_legacy_tenant_migration
            """,
            tuple(
                [default_tenant_id]
                + ([now] if "first_seen" in old_cols else [now])
                + ([now] if "last_seen" in old_cols else [now])
            ),
        )
        conn.execute("DROP TABLE bot_users_legacy_tenant_migration")

    def _create_indexes(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_tenant_user "
            "ON applications(tenant_id, user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_applications_tenant_status "
            "ON applications(tenant_id, status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bot_users_tenant ON bot_users(tenant_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_directions_tenant_parent "
            "ON directions(tenant_id, parent_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_directions_tenant_active "
            "ON directions(tenant_id, is_active)"
        )

    def _seed_known_users(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT OR IGNORE INTO bot_users
                (tenant_id, user_id, username, language, first_seen, last_seen)
            SELECT tenant_id,
                   user_id,
                   MAX(username),
                   MAX(language),
                   MIN(created_at),
                   MAX(created_at)
            FROM applications
            GROUP BY tenant_id, user_id
            """
        )

    def _seed_directions(self, conn: sqlite3.Connection, default_tenant_id: int) -> None:
        """Give every known tenant its direction list (idempotent, boot-safe)."""
        self._seed_promotors_directions(conn, default_tenant_id)
        # Any SPL tenant — whatever slug/name the panel used — gets the
        # confirmed event structure, including the already-existing rows that
        # still hold the first placeholder seed.
        for row in conn.execute("SELECT id, slug, name FROM tenants").fetchall():
            if not is_spl_tenant(str(row["slug"] or ""), str(row["name"] or "")):
                continue
            try:
                self._seed_spl_directions(conn, int(row["id"]))
            except Exception:  # noqa: BLE001 - seeding must never break init
                continue

    def _seed_promotors_directions(self, conn: sqlite3.Connection, tenant_id: int) -> None:
        """Seed the default tenant from the global DIRECTIONS table if empty."""
        cnt = conn.execute(
            "SELECT COUNT(*) FROM directions WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()[0]
        if cnt:
            return
        try:
            from .constants import DIRECTIONS as GLOBAL_DIRECTIONS
        except Exception:  # noqa: BLE001
            GLOBAL_DIRECTIONS = []
        self._insert_directions(conn, tenant_id, None, list(GLOBAL_DIRECTIONS))

    def _seed_spl_directions(self, conn: sqlite3.Connection, tenant_id: int) -> None:
        """Seed/refresh the SPL Show structure for one tenant.

        Roots are only created when missing, so an admin's rename or reorder is
        kept.  Children are synced by :meth:`_sync_direction_children` directly
        below; the SPL Avtozvuk categories by :meth:`_sync_autosound_children`.
        """
        root_ids: dict[str, int] = {}
        for root in SPL_ROOT_DIRECTIONS:
            root_ids[root["canonical"]] = self._ensure_direction(
                conn, tenant_id, parent_id=None, spec=root
            )
        self._sync_direction_children(
            conn, tenant_id, root_ids.get("Тюнинг"), SPL_TUNING_CHILDREN
        )
        self._sync_autosound_children(conn, tenant_id, root_ids.get("SPL Автозвук"))

    @staticmethod
    def _sync_autosound_children(
        conn: sqlite3.Connection, tenant_id: int, parent_id: Optional[int]
    ) -> None:
        """Bring the SPL Avtozvuk categories to the confirmed list (idempotent).

        The previous rule only replaced the list when it matched an old seed
        *exactly*, so one renamed or switched-off category kept the whole stale
        list forever.  Now:

        * every category an earlier build shipped is switched off (the row is
          kept, so old applications still show where they came from);
        * every confirmed category that does not exist yet is inserted — one an
          admin switched off in the panel exists, and stays off;
        * categories added by hand in the panel are not touched.
        """
        if not parent_id:
            return
        old_slugs = [s["slug"] for s in (*SPL_AUTOSOUND_PREVIOUS, *SPL_AUTOSOUND_STALE)]
        placeholders = ",".join("?" for _ in old_slugs)
        conn.execute(
            f"UPDATE directions SET is_active = 0, updated_at = ? "
            f"WHERE tenant_id = ? AND parent_id = ? AND is_active = 1 AND slug IN ({placeholders})",
            (_now(), tenant_id, parent_id, *old_slugs),
        )
        Database._insert_directions(conn, tenant_id, parent_id, SPL_AUTOSOUND_CHILDREN)

    def _ensure_direction(
        self,
        conn: sqlite3.Connection,
        tenant_id: int,
        *,
        parent_id: Optional[int],
        spec: Mapping[str, Any],
    ) -> int:
        """Return the id of a direction, inserting it only when it is missing."""
        row = conn.execute(
            "SELECT id FROM directions WHERE tenant_id = ? AND slug = ?",
            (tenant_id, spec["slug"]),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT id FROM directions WHERE tenant_id = ? AND canonical = ?",
                (tenant_id, spec["canonical"]),
            ).fetchone()
        if row is not None:
            return int(row["id"])
        now = _now()
        try:
            cur = conn.execute(
                """
                INSERT INTO directions
                    (tenant_id, parent_id, canonical, label_ru, label_uz, slug,
                     sort_order, is_active, exclusive_group, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    tenant_id,
                    parent_id,
                    spec["canonical"],
                    spec.get("label_ru") or spec["canonical"],
                    spec.get("label_uz") or spec["canonical"],
                    spec["slug"],
                    int(spec.get("sort", 0)),
                    str(spec.get("group") or ""),
                    now,
                    now,
                ),
            )
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            row = conn.execute(
                "SELECT id FROM directions WHERE tenant_id = ? AND slug = ?",
                (tenant_id, spec["slug"]),
            ).fetchone()
            return int(row["id"]) if row is not None else 0

    @staticmethod
    def _insert_directions(
        conn: sqlite3.Connection,
        tenant_id: int,
        parent_id: Optional[int],
        specs: list[Mapping[str, Any]],
    ) -> None:
        """Insert a batch of direction specs, skipping anything already present."""
        now = _now()
        for spec in specs:
            spec = dict(spec)
            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO directions
                        (tenant_id, parent_id, canonical, label_ru, label_uz, slug,
                         sort_order, is_active, exclusive_group, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        parent_id,
                        spec["canonical"],
                        spec.get("label_ru") or spec.get("ru") or spec["canonical"],
                        spec.get("label_uz") or spec.get("uz") or spec["canonical"],
                        spec["slug"],
                        int(spec.get("sort", spec.get("sort_order", 0))),
                        str(spec.get("group") or ""),
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError:
                continue

    @staticmethod
    def _sync_direction_children(
        conn: sqlite3.Connection,
        tenant_id: int,
        parent_id: Optional[int],
        wanted: list[Mapping[str, Any]],
        *,
        stale: list[Mapping[str, Any]] | tuple = (),
    ) -> None:
        """Keep the confirmed child list of one parent in place.

        Rules — all idempotent, so this is safe to run on every boot:

        * no parent (or no ``wanted`` list) -> nothing to do;
        * parent has no children -> insert ``wanted``;
        * every existing child still matches a previously shipped seed
          (``stale``) -> delete them and insert ``wanted``.  This is how the
          confirmed four SPL Avtozvuk categories reach a database that was
          seeded with the earlier placeholder list;
        * anything else -> an admin edited the list by hand, leave it alone.
        """
        if not parent_id or not wanted:
            return
        rows = conn.execute(
            "SELECT id, slug, canonical FROM directions WHERE tenant_id = ? AND parent_id = ?",
            (tenant_id, parent_id),
        ).fetchall()
        if rows:
            existing = {(str(r["slug"]), str(r["canonical"])) for r in rows}
            seeded = {(str(s["slug"]), str(s["canonical"])) for s in stale}
            if not seeded or existing != seeded:
                return
            for row in rows:
                conn.execute("DELETE FROM directions WHERE id = ?", (int(row["id"]),))
        Database._insert_directions(conn, tenant_id, parent_id, wanted)

    def _init(self) -> None:
        with self._connect() as conn:
            conn.executescript(_TENANTS_SCHEMA)
            conn.executescript(_DIRECTIONS_SCHEMA)
            default_tenant_id = self._ensure_default_tenant(conn)
            self._migrate_tenants(conn)
            self._migrate_directions(conn)
            self._migrate_applications(conn, default_tenant_id)
            self._migrate_bot_users(conn, default_tenant_id)
            self._create_indexes(conn)
            self._seed_known_users(conn)
            self._seed_directions(conn, default_tenant_id)

    # ------------------------------------------------------------------
    # Tenant administration
    # ------------------------------------------------------------------

    @staticmethod
    def _clean_slug(slug: str) -> str:
        value = (slug or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9-]{0,62}[a-z0-9])?", value):
            raise ValueError(
                "Tenant slug must contain lowercase letters, digits and hyphens only"
            )
        return value

    @staticmethod
    def _to_bool(value: Any) -> int:
        return 1 if bool(value) else 0

    def _resolve_tenant_id(self, conn: sqlite3.Connection, tenant_id: int | str | None) -> int:
        if tenant_id is None:
            row = conn.execute(
                "SELECT id FROM tenants WHERE slug = ?", (DEFAULT_TENANT_SLUG,)
            ).fetchone()
        elif isinstance(tenant_id, int):
            row = conn.execute("SELECT id FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        else:
            row = conn.execute("SELECT id FROM tenants WHERE slug = ?", (str(tenant_id),)).fetchone()
        if row is None:
            raise ValueError(f"Unknown tenant: {tenant_id!r}")
        return int(row["id"])

    def _get_tenant(self, identifier: int | str) -> Optional[Tenant]:
        with self._connect() as conn:
            if isinstance(identifier, int):
                row = conn.execute("SELECT * FROM tenants WHERE id = ?", (identifier,)).fetchone()
            else:
                row = conn.execute("SELECT * FROM tenants WHERE slug = ?", (identifier,)).fetchone()
            return _row_to_tenant(row) if row else None

    def _list_tenants(self, active_only: bool = False) -> list[Tenant]:
        with self._connect() as conn:
            sql = "SELECT * FROM tenants"
            if active_only:
                sql += " WHERE is_active = 1"
            sql += " ORDER BY name COLLATE NOCASE, id"
            return [_row_to_tenant(row) for row in conn.execute(sql).fetchall()]

    def _create_tenant(
        self,
        *,
        slug: str,
        name: str,
        bot_token: str = "",
        admin_chat_id: int = 0,
        required_channel: str = "",
        channel_url: str = "",
        instagram_handle: str = "",
        instagram_url: str = "",
        spreadsheet_id: str = "",
        drive_folder_id: str = "",
        admin_password: str = "",
        is_active: bool = True,
        event_date_text_ru: str = "",
        event_date_text_uz: str = "",
        event_venue_text_ru: str = "",
        event_venue_text_uz: str = "",
        event_guest_date_text_ru: str = "",
        event_guest_date_text_uz: str = "",
        event_note_text_ru: str = "",
        event_note_text_uz: str = "",
        registration_closed: bool = False,
        approved_text_ru: str = "",
        approved_text_uz: str = "",
        rejected_text_ru: str = "",
        rejected_text_uz: str = "",
    ) -> Tenant:
        slug = self._clean_slug(slug)
        name = (name or "").strip()
        if not name:
            raise ValueError("Tenant name is required")
        try:
            admin_chat_id = int(admin_chat_id or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("admin_chat_id must be an integer") from exc
        if is_spl_tenant(slug, name):
            name = normalize_spl_brand(name)
            if not (event_venue_text_ru or "").strip():
                event_venue_text_ru = SPL_EVENT_COPY["event_venue_text_ru"]
            if not (event_venue_text_uz or "").strip():
                event_venue_text_uz = SPL_EVENT_COPY["event_venue_text_uz"]
        now = _now()
        password = (admin_password or "").strip()
        if password and not is_password_hash(password):
            password = hash_password(password)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO tenants (
                    slug, name, is_active, bot_token, admin_chat_id, required_channel,
                    channel_url, instagram_handle, instagram_url, spreadsheet_id,
                    drive_folder_id, admin_password, created_at, updated_at,
                    event_date_text_ru, event_date_text_uz,
                    event_venue_text_ru, event_venue_text_uz,
                    event_guest_date_text_ru, event_guest_date_text_uz,
                    event_note_text_ru, event_note_text_uz, registration_closed
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    slug, name, self._to_bool(is_active), self._encrypt_token(bot_token),
                    admin_chat_id, (required_channel or "").strip(), (channel_url or "").strip(),
                    (instagram_handle or "").strip(), (instagram_url or "").strip(),
                    (spreadsheet_id or "").strip(), (drive_folder_id or "").strip(),
                    password, now, now,
                    (event_date_text_ru or "").strip(),
                    (event_date_text_uz or "").strip(),
                    (event_venue_text_ru or "").strip(),
                    (event_venue_text_uz or "").strip(),
                    (event_guest_date_text_ru or "").strip(),
                    (event_guest_date_text_uz or "").strip(),
                    (event_note_text_ru or "").strip(),
                    (event_note_text_uz or "").strip(),
                    self._to_bool(registration_closed),
                ),
            )
            templates = {
                "approved_text_ru": approved_text_ru,
                "approved_text_uz": approved_text_uz,
                "rejected_text_ru": rejected_text_ru,
                "rejected_text_uz": rejected_text_uz,
            }
            templates = {k: (v or "").strip() for k, v in templates.items() if (v or "").strip()}
            if templates:
                conn.execute(
                    f"UPDATE tenants SET {', '.join(f'{k} = ?' for k in templates)} WHERE id = ?",
                    [*templates.values(), cur.lastrowid],
                )
            row = conn.execute("SELECT * FROM tenants WHERE id = ?", (cur.lastrowid,)).fetchone()
            return _row_to_tenant(row)

    def _update_tenant(self, identifier: int | str, **changes: Any) -> Optional[Tenant]:
        allowed = {
            "name", "is_active", "registration_closed", "admin_chat_id", "required_channel", "channel_url",
            "instagram_handle", "instagram_url", "spreadsheet_id", "drive_folder_id",
            "event_date_text_ru", "event_date_text_uz",
            "event_venue_text_ru", "event_venue_text_uz",
            "event_guest_date_text_ru", "event_guest_date_text_uz",
            "event_note_text_ru", "event_note_text_uz",
            *MESSAGE_TEMPLATE_FIELDS,
        }
        with self._connect() as conn:
            tenant_id = self._resolve_tenant_id(conn, identifier)
            fields: list[str] = []
            params: list[Any] = []
            for key in allowed:
                if key not in changes:
                    continue
                value = changes[key]
                if key == "name":
                    # «Spl Show» typed in the panel is still written «SPL Show».
                    value = normalize_spl_brand(str(value or "").strip())
                    if not value:
                        raise ValueError("Tenant name is required")
                elif key in {"is_active", "registration_closed"}:
                    value = self._to_bool(value)
                elif key == "admin_chat_id":
                    try:
                        value = int(value or 0)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("admin_chat_id must be an integer") from exc
                else:
                    value = str(value or "").strip()
                fields.append(f"{key} = ?")
                params.append(value)
            if "bot_token" in changes and changes["bot_token"] is not None:
                fields.append("bot_token = ?")
                params.append(self._encrypt_token(str(changes["bot_token"])))
            if "admin_password" in changes and changes["admin_password"] is not None:
                password = str(changes["admin_password"] or "").strip()
                fields.append("admin_password = ?")
                params.append(hash_password(password) if password and not is_password_hash(password) else password)
            if not fields:
                row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
                return _row_to_tenant(row) if row else None
            fields.append("updated_at = ?")
            params.extend([_now(), tenant_id])
            conn.execute(f"UPDATE tenants SET {', '.join(fields)} WHERE id = ?", params)
            row = conn.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
            return _row_to_tenant(row) if row else None

    def _archive_tenant(self, identifier: int | str) -> bool:
        """Archive instead of deleting rows, preserving tenant data/audit trail."""
        with self._connect() as conn:
            tenant_id = self._resolve_tenant_id(conn, identifier)
            if tenant_id == self._resolve_tenant_id(conn, DEFAULT_TENANT_SLUG):
                return False
            cur = conn.execute(
                "UPDATE tenants SET is_active = 0, updated_at = ? WHERE id = ?",
                (_now(), tenant_id),
            )
            return bool(cur.rowcount)

    def _tenant_application_counts(self) -> dict[int, int]:
        with self._connect() as conn:
            return {
                int(row["id"]): int(row["n"])
                for row in conn.execute(
                    """
                    SELECT t.id, COUNT(a.id) AS n
                    FROM tenants t
                    LEFT JOIN applications a ON a.tenant_id = t.id
                    GROUP BY t.id
                    """
                ).fetchall()
            }

    def _get_tenant_token(self, identifier: int | str) -> str:
        with self._connect() as conn:
            tenant_id = self._resolve_tenant_id(conn, identifier)
            row = conn.execute("SELECT bot_token FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
            return self._decrypt_token(str(row["bot_token"] or "")) if row else ""

    # ------------------------------------------------------------------
    # Directions (tenant-scoped)
    # ------------------------------------------------------------------

    def _clean_direction_slug(self, slug: str) -> str:
        value = (slug or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9]+(?:[a-z0-9_-]{0,62}[a-z0-9])?", value):
            raise ValueError("Direction slug must be ascii letters/digits/_/-")
        return value

    @staticmethod
    def _clean_exclusive_group(group: Any) -> str:
        """Normalize a single-choice group value ('' = no group).

        Only lowered/trimmed, not validated: the value is a free-form key that
        links categories together, never a filename or an HTML attribute, and
        making it strict would stop admins from naming groups in their own
        language.
        """
        value = str(group or "").strip().lower()
        return value or ""

    def _list_directions(self, tenant_id: int | str | None = None, active_only: bool = True) -> list[Direction]:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            sql = "SELECT * FROM directions WHERE tenant_id = ?"
            params: list[Any] = [tid]
            if active_only:
                sql += " AND is_active = 1"
            sql += " ORDER BY sort_order ASC, id ASC"
            return [_row_to_direction(r) for r in conn.execute(sql, params).fetchall()]

    def _get_direction(self, direction_id: int, tenant_id: int | str | None = None) -> Optional[Direction]:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            row = conn.execute(
                "SELECT * FROM directions WHERE id = ? AND tenant_id = ?", (direction_id, tid)
            ).fetchone()
            return _row_to_direction(row) if row else None

    def _create_direction(
        self,
        *,
        tenant_id: int | str | None = None,
        parent_id: int | None = None,
        canonical: str = "",
        label_ru: str = "",
        label_uz: str = "",
        slug: str = "",
        sort_order: int = 0,
        is_active: bool = True,
        exclusive_group: str = "",
    ) -> Direction:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            canonical = (canonical or "").strip()
            label_ru = (label_ru or "").strip()
            label_uz = (label_uz or "").strip()
            slug = self._clean_direction_slug(slug or canonical.lower().replace(" ", "_")[:40] or "dir")
            exclusive_group = self._clean_exclusive_group(exclusive_group)
            if not canonical:
                raise ValueError("canonical is required")
            if not label_ru:
                label_ru = canonical
            if not label_uz:
                label_uz = canonical
            # Validate parent belongs to same tenant
            if parent_id is not None:
                parent_row = conn.execute(
                    "SELECT id FROM directions WHERE id = ? AND tenant_id = ?", (parent_id, tid)
                ).fetchone()
                if not parent_row:
                    raise ValueError("parent_id does not belong to this tenant")
                # Prevent self-parent
                if int(parent_id) == 0:
                    parent_id = None
            now = _now()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO directions
                        (tenant_id, parent_id, canonical, label_ru, label_uz, slug, sort_order, is_active, exclusive_group, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (tid, parent_id, canonical, label_ru, label_uz, slug, int(sort_order or 0), self._to_bool(is_active), exclusive_group, now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Direction already exists: {exc}") from exc
            row = conn.execute("SELECT * FROM directions WHERE id = ?", (cur.lastrowid,)).fetchone()
            return _row_to_direction(row)

    def _update_direction(self, direction_id: int, tenant_id: int | str | None = None, **changes: Any) -> Optional[Direction]:
        allowed = {"canonical", "label_ru", "label_uz", "slug", "sort_order", "is_active", "parent_id", "exclusive_group"}
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            existing = conn.execute(
                "SELECT * FROM directions WHERE id = ? AND tenant_id = ?", (direction_id, tid)
            ).fetchone()
            if not existing:
                return None
            fields: list[str] = []
            params: list[Any] = []
            for key in allowed:
                if key not in changes:
                    continue
                val = changes[key]
                if key == "canonical":
                    val = str(val or "").strip()
                    if not val:
                        raise ValueError("canonical required")
                elif key in ("label_ru", "label_uz"):
                    val = str(val or "").strip() or existing["canonical"]
                elif key == "slug":
                    val = self._clean_direction_slug(str(val or ""))
                elif key == "exclusive_group":
                    val = self._clean_exclusive_group(val)
                elif key == "sort_order":
                    try:
                        val = int(val or 0)
                    except (TypeError, ValueError):
                        val = 0
                elif key == "is_active":
                    val = self._to_bool(val)
                elif key == "parent_id":
                    if val in (None, "", 0, "0"):
                        val = None
                    else:
                        try:
                            pid = int(val)
                        except (TypeError, ValueError) as exc:
                            raise ValueError("parent_id must be integer") from exc
                        if pid == direction_id:
                            raise ValueError("direction cannot be its own parent")
                        # Ensure parent belongs to same tenant and is not a child (prevent deeper than 2 levels if desired)
                        parent_row = conn.execute(
                            "SELECT id, parent_id FROM directions WHERE id = ? AND tenant_id = ?", (pid, tid)
                        ).fetchone()
                        if not parent_row:
                            raise ValueError("parent_id not found in tenant")
                        # Optional: prevent grandchild (enforce max 2 levels)
                        if parent_row["parent_id"] is not None:
                            raise ValueError("Only 2 levels allowed: child cannot have its own children")
                        val = pid
                fields.append(f"{key} = ?")
                params.append(val)
            if not fields:
                return _row_to_direction(existing)
            fields.append("updated_at = ?")
            params.extend([_now(), direction_id, tid])
            try:
                conn.execute(f"UPDATE directions SET {', '.join(fields)} WHERE id = ? AND tenant_id = ?", params)
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Direction conflict: {exc}") from exc
            row = conn.execute("SELECT * FROM directions WHERE id = ? AND tenant_id = ?", (direction_id, tid)).fetchone()
            return _row_to_direction(row) if row else None

    def _delete_direction(self, direction_id: int, tenant_id: int | str | None = None) -> bool:
        """Soft-delete: set is_active=0, preserving historical applications."""
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            cur = conn.execute(
                "UPDATE directions SET is_active = 0, updated_at = ? WHERE id = ? AND tenant_id = ?",
                (_now(), direction_id, tid),
            )
            return bool(cur.rowcount)

    # ------------------------------------------------------------------
    # Tenant-scoped application operations (sync core)
    # ------------------------------------------------------------------

    def _create_application(
        self,
        *,
        user_id: int,
        username: str,
        country: str,
        plate: str,
        direction: str,
        phone: str,
        photo_file_ids: list[str],
        photo_paths: list[str],
        language: str = "ru",
        full_name: str = "",
        mod_file_ids: list[str] | None = None,
        mod_paths: list[str] | None = None,
        direction_id: int | None = None,
        tenant_id: int | str | None = None,
    ) -> int:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            now = _now()
            cur = conn.execute(
                """
                INSERT INTO applications
                    (tenant_id, user_id, username, country, plate, direction, phone,
                     photo_file_ids, photo_paths, status, created_at, language, full_name,
                     mod_file_ids, mod_paths, direction_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tid, user_id, username or "", country or "", plate or "", direction or "",
                    phone or "", json.dumps(photo_file_ids or [], ensure_ascii=False),
                    json.dumps(photo_paths or [], ensure_ascii=False), STATUS_PENDING, now,
                    language or "ru", full_name or "",
                    json.dumps(mod_file_ids or [], ensure_ascii=False),
                    json.dumps(mod_paths or [], ensure_ascii=False),
                    direction_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO bot_users (tenant_id, user_id, username, language, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, user_id) DO UPDATE SET
                    username = CASE WHEN excluded.username != '' THEN excluded.username ELSE bot_users.username END,
                    language = CASE WHEN excluded.language != '' THEN excluded.language ELSE bot_users.language END,
                    last_seen = excluded.last_seen
                """,
                (tid, user_id, username or "", language or "ru", now, now),
            )
            return int(cur.lastrowid)

    def _get_application(
        self, app_id: int, tenant_id: int | str | None = None
    ) -> Optional[Application]:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            row = conn.execute(
                "SELECT * FROM applications WHERE id = ? AND tenant_id = ?", (app_id, tid)
            ).fetchone()
            return _row_to_application(row) if row else None

    def _get_latest_for_user(
        self, user_id: int, tenant_id: int | str | None = None
    ) -> Optional[Application]:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            row = conn.execute(
                """
                SELECT * FROM applications
                WHERE user_id = ? AND tenant_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, tid),
            ).fetchone()
            return _row_to_application(row) if row else None

    def _has_active_application(
        self, user_id: int, tenant_id: int | str | None = None
    ) -> Optional[Application]:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            row = conn.execute(
                """
                SELECT * FROM applications
                WHERE user_id = ? AND tenant_id = ? AND status IN (?, ?)
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, tid, STATUS_PENDING, STATUS_APPROVED),
            ).fetchone()
            return _row_to_application(row) if row else None

    def _user_tenant_counts(
        self, user_id: int, exclude_tenant_id: int | None = None
    ) -> dict[str, int]:
        """How many applications a Telegram user has per tenant (diagnostics).

        Used when someone is answered "you have no application" although they
        just finished the form: if rows exist under another tenant, the log
        says so instead of leaving the team guessing why the record vanished.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT t.slug AS slug, t.id AS tid, COUNT(a.id) AS n
                FROM applications a
                JOIN tenants t ON t.id = a.tenant_id
                WHERE a.user_id = ?
                GROUP BY t.id
                ORDER BY t.slug
                """,
                (user_id,),
            ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            if exclude_tenant_id is not None and int(row["tid"]) == int(exclude_tenant_id):
                continue
            counts[str(row["slug"])] = int(row["n"])
        return counts

    def _set_badge_photo(
        self, user_id: int, file_id: str, path: str, tenant_id: int | str | None = None
    ) -> Optional[int]:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            row = conn.execute(
                """
                SELECT id FROM applications
                WHERE user_id = ? AND tenant_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, tid),
            ).fetchone()
            if row is None:
                return None
            app_id = int(row["id"])
            conn.execute(
                """
                UPDATE applications
                SET badge_photo_file_id = ?, badge_photo_path = ?
                WHERE id = ? AND tenant_id = ?
                """,
                (file_id, path, app_id, tid),
            )
            return app_id

    def _get_user_language(
        self, user_id: int, tenant_id: int | str | None = None
    ) -> str:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            row = conn.execute(
                "SELECT language FROM bot_users WHERE tenant_id = ? AND user_id = ?",
                (tid, user_id),
            ).fetchone()
            return (row["language"] if row else None) or "ru"

    def _set_card_message_id(
        self, app_id: int, message_id: Optional[int], tenant_id: int | str | None = None
    ) -> bool:
        """Remember which admin-chat message is this application's card."""
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            cur = conn.execute(
                "UPDATE applications SET card_message_id = ? WHERE id = ? AND tenant_id = ?",
                (message_id, app_id, tid),
            )
            return bool(cur.rowcount)

    def _delete_application(
        self,
        app_id: int,
        tenant_id: int | str | None = None,
        *,
        remove_files: bool = True,
    ) -> Optional[Application]:
        """Permanently remove one application, freeing the person to register again.

        Testers and participants who must be able to run the form from scratch
        need a real delete: an archived row would still answer ``/start`` with
        its old status.  Deleted rows also stop reserving a registration number,
        because numbers are assigned as ``MAX(reg_number) + 1``.

        Returns the removed row (so a caller can report or log it) or ``None``
        when the id does not belong to this tenant.
        """
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            row = conn.execute(
                "SELECT * FROM applications WHERE id = ? AND tenant_id = ?", (app_id, tid)
            ).fetchone()
            if row is None:
                return None
            application = _row_to_application(row)
            conn.execute(
                "DELETE FROM applications WHERE id = ? AND tenant_id = ?", (app_id, tid)
            )
        if remove_files:
            self.remove_application_files(application)
        return application

    @staticmethod
    def remove_application_files(application: Application) -> list[str]:
        """Delete the photos belonging to a removed application (best effort).

        Telegram keeps the ``file_id`` copies, but the runtime volume copies are
        participant data we no longer need — and the participant may register
        again, which would otherwise leave orphaned files behind.
        """
        removed: list[str] = []
        paths = [
            *(application.photo_paths or []),
            *(application.mod_paths or []),
            application.badge_photo_path or "",
        ]
        for path in paths:
            if not path:
                continue
            try:
                if os.path.isfile(path):
                    os.remove(path)
                    removed.append(path)
            except OSError:
                continue
        return removed

    def _approve(
        self, app_id: int, moderator: str, tenant_id: int | str | None = None
    ) -> Optional[int]:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status, reg_number FROM applications WHERE id = ? AND tenant_id = ?",
                (app_id, tid),
            ).fetchone()
            if row is None or row["status"] != STATUS_PENDING:
                conn.rollback()
                return None
            next_number = conn.execute(
                "SELECT COALESCE(MAX(reg_number), 0) + 1 FROM applications WHERE tenant_id = ?",
                (tid,),
            ).fetchone()[0]
            conn.execute(
                """
                UPDATE applications
                SET status = ?, reg_number = ?, processed_at = ?, processed_by = ?
                WHERE id = ? AND tenant_id = ?
                """,
                (STATUS_APPROVED, next_number, _now(), moderator, app_id, tid),
            )
            conn.commit()
            return int(next_number)

    def _reject(
        self, app_id: int, moderator: str, tenant_id: int | str | None = None
    ) -> bool:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT status FROM applications WHERE id = ? AND tenant_id = ?", (app_id, tid)
            ).fetchone()
            if row is None or row["status"] != STATUS_PENDING:
                conn.rollback()
                return False
            conn.execute(
                """
                UPDATE applications
                SET status = ?, processed_at = ?, processed_by = ?
                WHERE id = ? AND tenant_id = ?
                """,
                (STATUS_REJECTED, _now(), moderator, app_id, tid),
            )
            conn.commit()
            return True

    def _set_status(
        self, app_id: int, status: str, moderator: str, tenant_id: int | str | None = None
    ) -> bool:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT reg_number FROM applications WHERE id = ? AND tenant_id = ?", (app_id, tid)
            ).fetchone()
            if row is None:
                conn.rollback()
                return False
            reg_number = row["reg_number"]
            if status == STATUS_APPROVED and reg_number is None:
                reg_number = conn.execute(
                    "SELECT COALESCE(MAX(reg_number), 0) + 1 FROM applications WHERE tenant_id = ?",
                    (tid,),
                ).fetchone()[0]
            conn.execute(
                """
                UPDATE applications
                SET status = ?, reg_number = ?, processed_at = ?, processed_by = ?
                WHERE id = ? AND tenant_id = ?
                """,
                (status, reg_number, _now(), moderator, app_id, tid),
            )
            conn.commit()
            return True

    def _list_applications(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 500,
        tenant_id: int | str | None = None,
    ) -> list[Application]:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            query = "SELECT * FROM applications WHERE tenant_id = ?"
            params: list[Any] = [tid]
            if status:
                query += " AND status = ?"
                params.append(status)
            if search:
                query += (
                    " AND (plate LIKE ? OR phone LIKE ? OR username LIKE ? OR country LIKE ? OR full_name LIKE ?)"
                )
                like = f"%{search}%"
                params.extend([like, like, like, like, like])
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)
            return [_row_to_application(row) for row in conn.execute(query, params).fetchall()]

    def _stats(self, tenant_id: int | str | None = None) -> dict:
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            by_status = {
                row["status"]: row["n"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS n FROM applications WHERE tenant_id = ? GROUP BY status",
                    (tid,),
                ).fetchall()
            }
            by_direction = {
                row["direction"] or "—": row["n"]
                for row in conn.execute(
                    """
                    SELECT direction, COUNT(*) AS n FROM applications
                    WHERE tenant_id = ? GROUP BY direction ORDER BY n DESC
                    """,
                    (tid,),
                ).fetchall()
            }
            by_country = {
                row["country"] or "—": row["n"]
                for row in conn.execute(
                    """
                    SELECT country, COUNT(*) AS n FROM applications
                    WHERE tenant_id = ? GROUP BY country ORDER BY n DESC
                    """,
                    (tid,),
                ).fetchall()
            }
            by_language = {
                row["language"] or "ru": row["n"]
                for row in conn.execute(
                    """
                    SELECT language, COUNT(*) AS n FROM applications
                    WHERE tenant_id = ? GROUP BY language ORDER BY n DESC
                    """,
                    (tid,),
                ).fetchall()
            }
            by_date = {
                row["dt"]: row["n"]
                for row in conn.execute(
                    """
                    SELECT SUBSTR(created_at, 1, 10) AS dt, COUNT(*) AS n
                    FROM applications WHERE tenant_id = ?
                    GROUP BY dt ORDER BY dt DESC LIMIT 14
                    """,
                    (tid,),
                ).fetchall()
            }
            max_number = conn.execute(
                "SELECT COALESCE(MAX(reg_number), 0) FROM applications WHERE tenant_id = ?", (tid,)
            ).fetchone()[0]
            approved_users = conn.execute(
                """
                SELECT COUNT(DISTINCT user_id) AS n FROM applications
                WHERE tenant_id = ? AND status = ?
                """,
                (tid, STATUS_APPROVED),
            ).fetchone()["n"]
        total = sum(by_status.values())
        return {
            "total": total,
            "pending": by_status.get(STATUS_PENDING, 0),
            "approved": by_status.get(STATUS_APPROVED, 0),
            "rejected": by_status.get(STATUS_REJECTED, 0),
            "by_direction": by_direction,
            "by_country": by_country,
            "by_language": by_language,
            "by_date": by_date,
            "max_number": int(max_number),
            "approved_users": int(approved_users),
        }

    def _touch_user(
        self,
        user_id: int,
        username: str = "",
        language: str = "ru",
        tenant_id: int | str | None = None,
    ) -> None:
        now = _now()
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            conn.execute(
                """
                INSERT INTO bot_users (tenant_id, user_id, username, language, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, user_id) DO UPDATE SET
                    username = CASE WHEN excluded.username != '' THEN excluded.username ELSE bot_users.username END,
                    language = CASE WHEN excluded.language != '' THEN excluded.language ELSE bot_users.language END,
                    last_seen = excluded.last_seen
                """,
                (tid, user_id, username or "", language or "ru", now, now),
            )

    def _recipients(
        self,
        audience: str,
        languages: Optional[list[str]] = None,
        directions: Optional[list[str]] = None,
        tenant_id: int | str | None = None,
    ) -> list[tuple[int, str]]:
        """Unique ``(user_id, language)`` recipients within one tenant."""
        lang_filter = [lang for lang in (languages or []) if lang]
        dir_filter = [direction for direction in (directions or []) if direction]
        with self._connect() as conn:
            tid = self._resolve_tenant_id(conn, tenant_id)
            if audience == "starters":
                query = "SELECT u.user_id, u.language FROM bot_users u WHERE u.tenant_id = ?"
                params: list[Any] = [tid]
                if dir_filter:
                    placeholders = ",".join("?" for _ in dir_filter)
                    query += (
                        " AND EXISTS (SELECT 1 FROM applications a "
                        "WHERE a.tenant_id = u.tenant_id AND a.user_id = u.user_id "
                        f"AND a.direction IN ({placeholders}))"
                    )
                    params.extend(dir_filter)
                if lang_filter:
                    placeholders = ",".join("?" for _ in lang_filter)
                    query += f" AND u.language IN ({placeholders})"
                    params.extend(lang_filter)
                rows = conn.execute(query, params).fetchall()
            elif audience == "incomplete":
                if dir_filter:
                    rows = []
                else:
                    query = (
                        "SELECT u.user_id, u.language FROM bot_users u "
                        "WHERE u.tenant_id = ? AND NOT EXISTS ("
                        "SELECT 1 FROM applications a WHERE a.tenant_id = u.tenant_id "
                        "AND a.user_id = u.user_id)"
                    )
                    params = [tid]
                    if lang_filter:
                        placeholders = ",".join("?" for _ in lang_filter)
                        query += f" AND u.language IN ({placeholders})"
                        params.extend(lang_filter)
                    rows = conn.execute(query, params).fetchall()
            elif audience == "all_apps":
                query = (
                    "SELECT user_id, language FROM applications WHERE tenant_id = ? "
                    "AND id IN (SELECT MAX(id) FROM applications WHERE tenant_id = ? GROUP BY user_id)"
                )
                params = [tid, tid]
                if lang_filter:
                    placeholders = ",".join("?" for _ in lang_filter)
                    query += f" AND language IN ({placeholders})"
                    params.extend(lang_filter)
                if dir_filter:
                    placeholders = ",".join("?" for _ in dir_filter)
                    query += f" AND direction IN ({placeholders})"
                    params.extend(dir_filter)
                rows = conn.execute(query, params).fetchall()
            else:
                status = {
                    "approved": STATUS_APPROVED,
                    "pending": STATUS_PENDING,
                    "rejected": STATUS_REJECTED,
                }.get(audience, STATUS_APPROVED)
                query = (
                    "SELECT user_id, language FROM applications WHERE tenant_id = ? AND id IN ("
                    "SELECT MAX(id) FROM applications WHERE tenant_id = ? AND status = ? GROUP BY user_id)"
                )
                params = [tid, tid, status]
                if lang_filter:
                    placeholders = ",".join("?" for _ in lang_filter)
                    query += f" AND language IN ({placeholders})"
                    params.extend(lang_filter)
                if dir_filter:
                    placeholders = ",".join("?" for _ in dir_filter)
                    query += f" AND direction IN ({placeholders})"
                    params.extend(dir_filter)
                rows = conn.execute(query, params).fetchall()
            return [(int(row["user_id"]), row["language"] or "ru") for row in rows]

    def _audience_counts(self, tenant_id: int | str | None = None) -> dict[str, int]:
        return {
            key: len(self._recipients(key, tenant_id=tenant_id))
            for key in ("approved", "pending", "rejected", "all_apps", "incomplete", "starters")
        }

    # ------------------------------------------------------------------
    # Async public API
    # ------------------------------------------------------------------

    async def init(self) -> None:
        await asyncio.to_thread(self._init)

    def for_tenant(self, tenant_id: int) -> TenantDatabase:
        """Return a facade whose application/user queries cannot cross tenants."""
        return TenantDatabase(self, tenant_id)

    async def get_tenant(self, identifier: int | str) -> Optional[Tenant]:
        return await asyncio.to_thread(self._get_tenant, identifier)

    async def list_tenants(self, active_only: bool = False) -> list[Tenant]:
        return await asyncio.to_thread(self._list_tenants, active_only)

    async def get_tenant_by_slug(self, slug: str) -> Optional[Tenant]:
        """Explicit readability alias for callers that resolve URL slugs."""
        return await self.get_tenant(slug)

    async def get_active_tenants(self) -> list[Tenant]:
        """Return only polling-eligible tenant records."""
        return await self.list_tenants(active_only=True)

    async def create_tenant(self, **kwargs: Any) -> Tenant:
        return await asyncio.to_thread(lambda: self._create_tenant(**kwargs))

    async def update_tenant(self, identifier: int | str, **changes: Any) -> Optional[Tenant]:
        return await asyncio.to_thread(lambda: self._update_tenant(identifier, **changes))

    async def archive_tenant(self, identifier: int | str) -> bool:
        return await asyncio.to_thread(self._archive_tenant, identifier)

    async def delete_tenant(self, identifier: int | str) -> bool:
        """Data-safe delete operation: archive the tenant and retain its audit data."""
        return await self.archive_tenant(identifier)

    async def set_tenant_active(self, identifier: int | str, is_active: bool) -> Optional[Tenant]:
        """Convenience API for explicit activation/deactivation controls."""
        return await self.update_tenant(identifier, is_active=is_active)

    async def tenant_application_counts(self) -> dict[int, int]:
        return await asyncio.to_thread(self._tenant_application_counts)

    async def get_tenant_token(self, identifier: int | str) -> str:
        return await asyncio.to_thread(self._get_tenant_token, identifier)

    async def list_applications(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 500,
        *,
        tenant_id: int | str | None = None,
    ) -> list[Application]:
        return await asyncio.to_thread(self._list_applications, status, search, limit, tenant_id)

    async def stats(self, *, tenant_id: int | str | None = None) -> dict:
        return await asyncio.to_thread(self._stats, tenant_id)

    async def create_application(self, *, tenant_id: int | str | None = None, **kwargs: Any) -> int:
        return await asyncio.to_thread(
            lambda: self._create_application(tenant_id=tenant_id, **kwargs)
        )

    async def get_application(
        self, app_id: int, *, tenant_id: int | str | None = None
    ) -> Optional[Application]:
        return await asyncio.to_thread(self._get_application, app_id, tenant_id)

    async def get_latest_for_user(
        self, user_id: int, *, tenant_id: int | str | None = None
    ) -> Optional[Application]:
        return await asyncio.to_thread(self._get_latest_for_user, user_id, tenant_id)

    async def has_active_application(
        self, user_id: int, *, tenant_id: int | str | None = None
    ) -> Optional[Application]:
        return await asyncio.to_thread(self._has_active_application, user_id, tenant_id)

    async def user_tenant_counts(
        self, user_id: int, *, exclude_tenant_id: int | None = None
    ) -> dict[str, int]:
        return await asyncio.to_thread(self._user_tenant_counts, user_id, exclude_tenant_id)

    async def set_badge_photo(
        self, user_id: int, file_id: str, path: str, *, tenant_id: int | str | None = None
    ) -> Optional[int]:
        return await asyncio.to_thread(self._set_badge_photo, user_id, file_id, path, tenant_id)

    async def get_user_language(
        self, user_id: int, *, tenant_id: int | str | None = None
    ) -> str:
        return await asyncio.to_thread(self._get_user_language, user_id, tenant_id)

    async def delete_application(
        self,
        app_id: int,
        *,
        tenant_id: int | str | None = None,
        remove_files: bool = True,
    ) -> Optional[Application]:
        return await asyncio.to_thread(
            lambda: self._delete_application(
                app_id, tenant_id, remove_files=remove_files
            )
        )

    async def set_card_message_id(
        self,
        app_id: int,
        message_id: Optional[int],
        *,
        tenant_id: int | str | None = None,
    ) -> bool:
        return await asyncio.to_thread(self._set_card_message_id, app_id, message_id, tenant_id)

    async def approve(
        self, app_id: int, moderator: str, *, tenant_id: int | str | None = None
    ) -> Optional[int]:
        return await asyncio.to_thread(self._approve, app_id, moderator, tenant_id)

    async def reject(
        self, app_id: int, moderator: str, *, tenant_id: int | str | None = None
    ) -> bool:
        return await asyncio.to_thread(self._reject, app_id, moderator, tenant_id)

    async def set_status(
        self, app_id: int, status: str, moderator: str, *, tenant_id: int | str | None = None
    ) -> bool:
        return await asyncio.to_thread(self._set_status, app_id, status, moderator, tenant_id)

    async def touch_user(
        self,
        user_id: int,
        username: str = "",
        language: str = "ru",
        *,
        tenant_id: int | str | None = None,
    ) -> None:
        await asyncio.to_thread(self._touch_user, user_id, username, language, tenant_id)

    async def recipients(
        self,
        audience: str,
        languages: Optional[list[str]] = None,
        directions: Optional[list[str]] = None,
        *,
        tenant_id: int | str | None = None,
    ) -> list[tuple[int, str]]:
        return await asyncio.to_thread(
            self._recipients, audience, languages, directions, tenant_id
        )

    async def audience_counts(self, *, tenant_id: int | str | None = None) -> dict[str, int]:
        return await asyncio.to_thread(self._audience_counts, tenant_id)

    async def list_directions(
        self, *, tenant_id: int | str | None = None, active_only: bool = True
    ) -> list[Direction]:
        return await asyncio.to_thread(self._list_directions, tenant_id, active_only)

    async def get_direction(
        self, direction_id: int, *, tenant_id: int | str | None = None
    ) -> Optional[Direction]:
        return await asyncio.to_thread(self._get_direction, direction_id, tenant_id)

    async def create_direction(self, *, tenant_id: int | str | None = None, **kwargs: Any) -> Direction:
        return await asyncio.to_thread(lambda: self._create_direction(tenant_id=tenant_id, **kwargs))

    async def update_direction(
        self, direction_id: int, *, tenant_id: int | str | None = None, **kwargs: Any
    ) -> Optional[Direction]:
        return await asyncio.to_thread(lambda: self._update_direction(direction_id, tenant_id=tenant_id, **kwargs))

    async def delete_direction(self, direction_id: int, *, tenant_id: int | str | None = None) -> bool:
        return await asyncio.to_thread(self._delete_direction, direction_id, tenant_id)
