"""Configuration loaded from environment / .env file.

Run ``python -m bot.config --check`` to validate configuration and perform a
Google auth + Sheets/Drive access smoke test without touching Telegram.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _get(name: str, *, required: bool = False, default: str = "") -> str:
    value = os.getenv(name, default).strip()
    if required and not value:
        raise ConfigError(
            f"Environment variable {name} is required but not set. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


@dataclass(frozen=True)
class Config:
    bot_token: str
    required_channel: str
    admin_chat_id: int
    google_credentials_file: str
    spreadsheet_id: str
    drive_folder_id: str
    db_path: str
    media_dir: str
    require_subscription: bool
    admin_password: str
    panel_port: int
    admin_user_ids: frozenset[int]

    @property
    def panel_enabled(self) -> bool:
        return bool(self.admin_password)

    @property
    def sheets_enabled(self) -> bool:
        return bool(self.spreadsheet_id and self.google_credentials_file)

    @property
    def drive_enabled(self) -> bool:
        return bool(self.drive_folder_id and self.google_credentials_file)


def _materialize_google_credentials(path: str) -> None:
    """If GOOGLE_CREDENTIALS_JSON is set, write it to ``path``.

    Lets platforms like Fly.io (which store secrets as env vars, not files)
    provide the service-account key inline. A real file at ``path`` still wins.
    """
    inline = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if inline and not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(inline)


def _get_bool(name: str, *, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw not in {"false", "0", "no", "off"}


def load_config() -> Config:
    admin_chat_raw = _get("ADMIN_CHAT_ID", required=True)
    try:
        admin_chat_id = int(admin_chat_raw)
    except ValueError as exc:
        raise ConfigError(
            f"ADMIN_CHAT_ID must be an integer chat id (e.g. -1001234567890), "
            f"got: {admin_chat_raw!r}"
        ) from exc

    required_channel = _get("REQUIRED_CHANNEL", required=True)

    google_credentials_file = _get("GOOGLE_CREDENTIALS_FILE", default="credentials.json")
    _materialize_google_credentials(google_credentials_file)

    return Config(
        bot_token=_get("BOT_TOKEN", required=True),
        required_channel=required_channel,
        admin_chat_id=admin_chat_id,
        google_credentials_file=google_credentials_file,
        spreadsheet_id=_get("SPREADSHEET_ID"),
        drive_folder_id=_get("DRIVE_FOLDER_ID"),
        db_path=_get("DB_PATH", default="data/pmshow.db"),
        media_dir=_get("MEDIA_DIR", default="media"),
        require_subscription=_get_bool("REQUIRE_SUBSCRIPTION", default=True),
        admin_password=_get("ADMIN_PASSWORD"),
        panel_port=int(_get("PORT", default="8080") or "8080"),
        admin_user_ids=_parse_ids(_get("ADMIN_USER_IDS")),
    )


def _parse_ids(raw: str) -> frozenset[int]:
    ids = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            ids.add(int(part))
    return frozenset(ids)


def _check() -> int:
    """Validate config and Google access. Returns a process exit code."""
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[config] ERROR: {exc}")
        return 1

    print("[config] OK: required Telegram settings present.")
    print(f"[config]   REQUIRED_CHANNEL = {config.required_channel}")
    print(f"[config]   ADMIN_CHAT_ID    = {config.admin_chat_id}")
    print(f"[config]   Subscription     = {'required' if config.require_subscription else 'not required'}")
    print(f"[config]   Admin panel      = {'enabled (port ' + str(config.panel_port) + ')' if config.panel_enabled else 'disabled (set ADMIN_PASSWORD)'}")
    print(f"[config]   Sheets export    = {'enabled' if config.sheets_enabled else 'disabled'}")
    print(f"[config]   Drive photos     = {'enabled' if config.drive_enabled else 'disabled'}")

    if not (config.sheets_enabled or config.drive_enabled):
        print("[config] Google integration disabled; skipping Google smoke test.")
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
        print(f"[config] OK: opened spreadsheet {title!r} with the service account.")
    except Exception as exc:  # noqa: BLE001 - surface any auth/access error clearly
        print(f"[config] ERROR: Google access failed: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(_check())
    # Default: just try to load and report.
    load_config()
    print("[config] loaded successfully.")
