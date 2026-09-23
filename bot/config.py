"""Process and tenant runtime configuration.

Only process-wide settings live in environment variables.  Telegram credentials
and event settings belong to rows in ``tenants`` so one Fly machine can run
multiple independent bots.  The former single-bot environment variables remain
optional *migration fallbacks* for the first ``promotors`` tenant.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

from .db import Tenant
from .security import EncryptionError, TokenCipher

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required process-level configuration is missing or invalid."""


def _get(name: str, *, required: bool = False, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if required and not value:
        raise ConfigError(
            f"Environment variable {name} is required but not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _get_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"false", "0", "no", "off"}


def _parse_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return frozenset(ids)


def _materialize_google_credentials(path: str) -> None:
    """Write inline Google credentials once when a deployment supplies them."""
    inline = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if inline and not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(inline)


def _materialize_logo() -> None:
    """Keep the historic ``LOGO_BASE64`` bootstrap fallback working.

    New per-tenant artwork is uploaded through the tenant asset panel.  This
    fallback only preserves existing deployments that used a bundled logo.
    """
    b64 = os.getenv("LOGO_BASE64", "").strip()
    if not b64:
        return
    path = os.path.join(os.path.dirname(__file__), "assets", "logo.png")
    if os.path.exists(path):
        return
    import base64

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(b64))
    except Exception:  # noqa: BLE001 - a bad deployment secret must not crash startup
        pass


@dataclass(frozen=True)
class TenantConfig:
    """Configuration injected into one tenant's bot dispatcher.

    It intentionally has the familiar attributes used by existing handlers,
    allowing their business logic to stay focused on registration rather than
    configuration lookup.  ``media_dir`` is already tenant-scoped.
    """

    tenant_id: int
    tenant_slug: str
    tenant_name: str
    bot_token: str
    required_channel: str
    admin_chat_id: int
    google_credentials_file: str
    spreadsheet_id: str
    drive_folder_id: str
    media_dir: str
    asset_scope: str
    require_subscription: bool
    registration_closed: bool
    admin_user_ids: frozenset[int]
    channel_url: str
    instagram_handle: str
    instagram_url: str
    # Event branding — optional, empty means sentence omitted.
    event_date_text_ru: str = ""
    event_date_text_uz: str = ""
    event_venue_text_ru: str = ""
    event_venue_text_uz: str = ""
    event_guest_date_text_ru: str = ""
    event_guest_date_text_uz: str = ""
    event_note_text_ru: str = ""
    event_note_text_uz: str = ""
    # Full approval / rejection texts from the panel (empty = assembled text).
    approved_text_ru: str = ""
    approved_text_uz: str = ""
    rejected_text_ru: str = ""
    rejected_text_uz: str = ""

    @property
    def sheets_enabled(self) -> bool:
        return bool(self.spreadsheet_id and self.google_credentials_file)

    @property
    def drive_enabled(self) -> bool:
        return bool(self.drive_folder_id and self.google_credentials_file)


@dataclass(frozen=True)
class Config:
    """Process-wide configuration, with legacy values only for migration."""

    super_admin_password: str
    encryption_key: str
    google_credentials_file: str
    db_path: str
    media_dir: str
    require_subscription: bool
    registration_closed: bool
    panel_port: int
    admin_user_ids: frozenset[int]
    # Old single-bot values; they seed ``promotors`` once and are never used by
    # new tenants or the polling manager afterwards.
    legacy_bot_token: str = ""
    legacy_required_channel: str = ""
    legacy_admin_chat_id: int = 0
    legacy_channel_url: str = ""
    legacy_instagram_handle: str = ""
    legacy_instagram_url: str = ""
    legacy_spreadsheet_id: str = ""
    legacy_drive_folder_id: str = ""
    legacy_admin_password: str = ""

    @property
    def panel_enabled(self) -> bool:
        return bool(self.super_admin_password)

    @property
    def legacy_panel_enabled(self) -> bool:
        """New deployments use tenant routes rather than historic root routes."""
        return False

    # Compatibility aliases for integrations that still inspect a Config while
    # migrating. They are intentionally not used by BotManager.
    @property
    def bot_token(self) -> str:
        return self.legacy_bot_token

    @property
    def required_channel(self) -> str:
        return self.legacy_required_channel

    @property
    def admin_chat_id(self) -> int:
        return self.legacy_admin_chat_id

    @property
    def channel_url(self) -> str:
        return self.legacy_channel_url

    @property
    def instagram_handle(self) -> str:
        return self.legacy_instagram_handle

    @property
    def instagram_url(self) -> str:
        return self.legacy_instagram_url

    @property
    def spreadsheet_id(self) -> str:
        return self.legacy_spreadsheet_id

    @property
    def drive_folder_id(self) -> str:
        return self.legacy_drive_folder_id

    @property
    def admin_password(self) -> str:
        return self.legacy_admin_password

    @property
    def sheets_enabled(self) -> bool:
        return bool(self.legacy_spreadsheet_id and self.google_credentials_file)

    @property
    def drive_enabled(self) -> bool:
        return bool(self.legacy_drive_folder_id and self.google_credentials_file)

    def tenant_config(self, tenant: Tenant, *, bot_token: str = "") -> TenantConfig:
        """Build immutable runtime settings for a tenant worker or web request."""
        scope = tenant.slug
        return TenantConfig(
            tenant_id=tenant.id,
            tenant_slug=tenant.slug,
            tenant_name=tenant.name,
            bot_token=bot_token,
            required_channel=tenant.required_channel,
            admin_chat_id=tenant.admin_chat_id,
            google_credentials_file=self.google_credentials_file,
            spreadsheet_id=tenant.spreadsheet_id,
            drive_folder_id=tenant.drive_folder_id,
            media_dir=os.path.join(self.media_dir, "_tenants", scope),
            asset_scope=scope,
            require_subscription=self.require_subscription,
            # Per tenant. A leftover REGISTRATION_CLOSED=true secret must not
            # make every bot, including SPL Show, answer «регистрация завершена».
            registration_closed=bool(getattr(tenant, "registration_closed", False)),
            admin_user_ids=self.admin_user_ids,
            channel_url=tenant.channel_url,
            instagram_handle=tenant.instagram_handle,
            instagram_url=tenant.instagram_url,
            event_date_text_ru=getattr(tenant, "event_date_text_ru", "") or "",
            event_date_text_uz=getattr(tenant, "event_date_text_uz", "") or "",
            event_venue_text_ru=getattr(tenant, "event_venue_text_ru", "") or "",
            event_venue_text_uz=getattr(tenant, "event_venue_text_uz", "") or "",
            event_guest_date_text_ru=getattr(tenant, "event_guest_date_text_ru", "") or "",
            event_guest_date_text_uz=getattr(tenant, "event_guest_date_text_uz", "") or "",
            event_note_text_ru=getattr(tenant, "event_note_text_ru", "") or "",
            event_note_text_uz=getattr(tenant, "event_note_text_uz", "") or "",
            approved_text_ru=getattr(tenant, "approved_text_ru", "") or "",
            approved_text_uz=getattr(tenant, "approved_text_uz", "") or "",
            rejected_text_ru=getattr(tenant, "rejected_text_ru", "") or "",
            rejected_text_uz=getattr(tenant, "rejected_text_uz", "") or "",
        )


def load_config() -> Config:
    """Read process settings and validate the encryption key before startup."""
    encryption_key = _get("ENCRYPTION_KEY", required=True)
    try:
        TokenCipher(encryption_key)
    except EncryptionError as exc:
        raise ConfigError(str(exc)) from exc

    raw_admin_chat_id = _get("ADMIN_CHAT_ID")
    try:
        legacy_admin_chat_id = int(raw_admin_chat_id) if raw_admin_chat_id else 0
    except ValueError as exc:
        raise ConfigError(
            "ADMIN_CHAT_ID must be an integer chat id (e.g. -1001234567890), "
            f"got: {raw_admin_chat_id!r}"
        ) from exc

    google_credentials_file = _get("GOOGLE_CREDENTIALS_FILE", default="credentials.json")
    _materialize_google_credentials(google_credentials_file)
    _materialize_logo()

    legacy_admin_password = _get("ADMIN_PASSWORD")
    # Existing installations can make the transition without unexpectedly
    # locking themselves out. New deployments should set SUPER_ADMIN_PASSWORD.
    super_admin_password = _get("SUPER_ADMIN_PASSWORD") or legacy_admin_password

    try:
        panel_port = int(_get("PORT", default="8080") or "8080")
    except ValueError as exc:
        raise ConfigError("PORT must be an integer") from exc

    return Config(
        super_admin_password=super_admin_password,
        encryption_key=encryption_key,
        google_credentials_file=google_credentials_file,
        db_path=_get("DB_PATH", default="data/pmshow.db"),
        media_dir=_get("MEDIA_DIR", default="media"),
        require_subscription=_get_bool("REQUIRE_SUBSCRIPTION", default=True),
        # Kept so old deployments still parse. It is NOT applied to tenant bots;
        # close a single event from the admin panel instead.
        registration_closed=_get_bool("REGISTRATION_CLOSED", default=False),
        panel_port=panel_port,
        admin_user_ids=_parse_ids(_get("ADMIN_USER_IDS")),
        legacy_bot_token=_get("BOT_TOKEN"),
        legacy_required_channel=_get("REQUIRED_CHANNEL"),
        legacy_admin_chat_id=legacy_admin_chat_id,
        legacy_channel_url=_get("CHANNEL_URL"),
        legacy_instagram_handle=_get("INSTAGRAM_HANDLE"),
        legacy_instagram_url=_get("INSTAGRAM_URL"),
        legacy_spreadsheet_id=_get("SPREADSHEET_ID"),
        legacy_drive_folder_id=_get("DRIVE_FOLDER_ID"),
        legacy_admin_password=legacy_admin_password,
    )


def _check() -> int:
    """Validate process config and legacy Google access when configured."""
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[config] ERROR: {exc}")
        return 1

    print("[config] OK: process-level multi-tenant settings present.")
    print(
        "[config]   Super admin      = "
        + ("enabled" if config.panel_enabled else "disabled (set SUPER_ADMIN_PASSWORD)")
    )
    print("[config]   Tenant tokens     = encrypted with ENCRYPTION_KEY")
    print(f"[config]   Database          = {config.db_path}")
    print(f"[config]   Media root        = {config.media_dir}")
    if config.legacy_bot_token:
        print("[config]   Legacy BOT_TOKEN  = present (will seed promotors only if needed)")
    else:
        print("[config]   Legacy BOT_TOKEN  = absent (normal after migration)")

    if not (config.sheets_enabled or config.drive_enabled):
        print("[config] Legacy Google integration disabled; skipping smoke test.")
        return 0
    if not os.path.exists(config.google_credentials_file):
        print(
            f"[config] ERROR: GOOGLE_CREDENTIALS_FILE not found at "
            f"{config.google_credentials_file!r}"
        )
        return 1
    try:
        from bot.services import sheets as sheets_service

        title = sheets_service.smoke_test(config)
        print(f"[config] OK: opened legacy promotors spreadsheet {title!r}.")
    except Exception as exc:  # noqa: BLE001 - surface any auth/access error clearly
        print(f"[config] ERROR: Google access failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(_check())
    load_config()
    print("[config] loaded successfully.")
