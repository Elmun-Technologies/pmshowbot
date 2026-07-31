"""Brand assets that admins can upload straight from Telegram.

Sponsor logos and direction banners can either be bundled in the repo
(``bot/assets/sponsors`` / ``bot/assets/directions``) or uploaded at runtime by
an admin sending the image to the bot. Runtime uploads live on the Fly volume,
so they survive restarts and redeploys without needing a git commit — which is
the practical way to manage these during an event.

Runtime files win over bundled ones, so an upload can also replace a bundled
asset without touching the repo.
"""
from __future__ import annotations

import os
import re
from typing import Optional

_BUNDLED_ROOT = os.path.join(os.path.dirname(__file__), "..", "assets")
_BUNDLED_SPONSORS = os.path.join(_BUNDLED_ROOT, "sponsors")
_BUNDLED_DIRECTIONS = os.path.join(_BUNDLED_ROOT, "directions")

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# Set once at startup from the configured MEDIA_DIR so uploads land on the
# volume. Underscore-prefixed so they can never collide with the numeric
# per-user photo folders that live alongside them.
_runtime_root: Optional[str] = None

# Asset names are used to build file paths, so keep them strictly safe.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def configure(media_dir: str) -> None:
    """Point runtime uploads at the persistent media directory."""
    global _runtime_root
    _runtime_root = media_dir


def is_safe_name(name: str) -> bool:
    return bool(_SAFE_NAME.match(name or ""))


def _runtime_dir(kind: str) -> Optional[str]:
    if not _runtime_root:
        return None
    return os.path.join(_runtime_root, f"_{kind}")


def sponsors_dirs() -> list[str]:
    """Directories to look in for sponsor logos, highest priority first."""
    dirs = []
    runtime = _runtime_dir("sponsors")
    if runtime:
        dirs.append(runtime)
    dirs.append(_BUNDLED_SPONSORS)
    return dirs


def sponsor_files() -> list[str]:
    """Sponsor logo paths in filename order.

    Uses the first directory that actually contains images, so a runtime upload
    set fully replaces the bundled set rather than mixing the two.
    """
    for directory in sponsors_dirs():
        if not os.path.isdir(directory):
            continue
        names = sorted(
            n for n in os.listdir(directory) if n.lower().endswith(_IMAGE_EXTS)
        )
        if names:
            return [os.path.join(directory, n) for n in names]
    return []


def direction_banner(slug: str) -> Optional[str]:
    """Path to a direction's banner (runtime upload wins), or None."""
    if not slug:
        return None
    candidates = []
    runtime = _runtime_dir("directions")
    if runtime:
        candidates.append(runtime)
    candidates.append(_BUNDLED_DIRECTIONS)
    for directory in candidates:
        for ext in _IMAGE_EXTS:
            path = os.path.join(directory, slug + ext)
            if os.path.exists(path):
                return path
    return None


# The two marks shown at the top of the ticket poster, keyed by the bundled
# filename they override.
BRAND_LOGOS = {
    "logo": "logo.png",            # PROMOTORS SHOW · Samarkand
    "adrenaline": "adrenaline.png",  # Adrenaline Rush (main sponsor)
}


# The partner logos the ticket's top strip is designed around, in the order
# they should appear. Naming them here means an admin never has to invent a
# filename: /logo 1_mcs_sherdor puts that club's mark in the first slot.
#
# The strip is not limited to these — any extra sponsor file still shows up —
# but /assets reports these four as a checklist so it's obvious what's missing
# before the tickets go out.
PARTNER_LOGOS = [
    {"name": "1_mcs_sherdor", "title": "Мотоклуб MCS «Sherdor» (Самарканд)"},
    {"name": "2_retro_tashkent", "title": "Авто-Ретро Клуб (Ташкент)"},
    {"name": "3_drift_show", "title": "Uzbekistan Drift Show"},
    {"name": "4_sof_expo", "title": "SOF EXPO Samarkand"},
]


def partner_status() -> list[dict]:
    """Each expected partner logo with where (if anywhere) its file was found."""
    loaded = {os.path.splitext(os.path.basename(p))[0]: p for p in sponsor_files()}
    runtime = _runtime_dir("sponsors")
    out = []
    for slot in PARTNER_LOGOS:
        path = loaded.get(slot["name"])
        if not path:
            source = None
        elif runtime and path.startswith(runtime):
            source = "runtime"
        else:
            source = "bundled"
        out.append({**slot, "source": source})
    return out


def brand_logo(name: str) -> Optional[str]:
    """Path to a main brand logo (runtime upload wins), or None."""
    bundled_name = BRAND_LOGOS.get(name)
    if not bundled_name:
        return None
    runtime = _runtime_dir("brand")
    if runtime:
        for ext in _IMAGE_EXTS:
            path = os.path.join(runtime, name + ext)
            if os.path.exists(path):
                return path
    path = os.path.join(_BUNDLED_ROOT, bundled_name)
    return path if os.path.exists(path) else None


def save_brand(name: str, data: bytes) -> str:
    """Persist a main brand logo under the runtime directory."""
    if name not in BRAND_LOGOS:
        raise ValueError(f"unknown brand logo: {name!r}")
    return _save("brand", name, data)


def save_sponsor(name: str, data: bytes) -> str:
    """Persist a sponsor logo under the runtime directory. Returns its path."""
    return _save("sponsors", name, data)


def save_direction(slug: str, data: bytes) -> str:
    """Persist a direction banner under the runtime directory. Returns its path."""
    return _save("directions", slug, data)


def _save(kind: str, name: str, data: bytes) -> str:
    if not is_safe_name(name):
        raise ValueError(f"unsafe asset name: {name!r}")
    directory = _runtime_dir(kind)
    if not directory:
        raise RuntimeError("runtime asset storage is not configured")
    os.makedirs(directory, exist_ok=True)
    # One file per name: drop any previous upload with a different extension.
    for ext in _IMAGE_EXTS:
        old = os.path.join(directory, name + ext)
        if os.path.exists(old):
            os.remove(old)
    path = os.path.join(directory, name + ".png")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def delete_asset(kind: str, name: str) -> bool:
    """Remove a runtime-uploaded asset. Returns True if something was deleted."""
    if kind not in ("sponsors", "directions", "brand") or not is_safe_name(name):
        return False
    directory = _runtime_dir(kind)
    if not directory:
        return False
    removed = False
    for ext in _IMAGE_EXTS:
        path = os.path.join(directory, name + ext)
        if os.path.exists(path):
            os.remove(path)
            removed = True
    return removed


def inventory() -> dict:
    """What's currently available, for the /assets admin command."""
    runtime_sponsors = _runtime_dir("sponsors")
    runtime_directions = _runtime_dir("directions")

    def _listing(directory: Optional[str]) -> list[str]:
        if not directory or not os.path.isdir(directory):
            return []
        return sorted(
            n for n in os.listdir(directory) if n.lower().endswith(_IMAGE_EXTS)
        )

    brand = {}
    for name in BRAND_LOGOS:
        path = brand_logo(name)
        if not path:
            brand[name] = "—"
        elif _runtime_dir("brand") and path.startswith(_runtime_dir("brand")):
            brand[name] = "загружен"
        else:
            brand[name] = "из репозитория"

    return {
        "partners": partner_status(),
        "sponsors_runtime": _listing(runtime_sponsors),
        "sponsors_bundled": _listing(_BUNDLED_SPONSORS),
        "directions_runtime": _listing(runtime_directions),
        "directions_bundled": _listing(_BUNDLED_DIRECTIONS),
        "brand": brand,
        "storage_configured": bool(_runtime_root),
    }
