"""Tenant-scoped ticket artwork storage.

Bundled artwork remains a read-only fallback **only for the default
``promotors`` tenant** and legacy single-tenant calls (``tenant_id=None``).
Every other tenant starts with an empty sponsor strip and a typographic
wordmark — no Promotors branding ever leaks into another event.

Runtime uploads live below ``MEDIA_DIR/_tenants/<tenant-scope>/``.  Every
public helper accepts a ``tenant_id``/scope argument so concurrently polling
bots never share mutable asset state.
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Optional

_BUNDLED_ROOT = os.path.join(os.path.dirname(__file__), "..", "assets")
_BUNDLED_SPONSORS = os.path.join(_BUNDLED_ROOT, "sponsors")
_BUNDLED_DIRECTIONS = os.path.join(_BUNDLED_ROOT, "directions")

_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

# Base persistent media directory.  ``_configured_tenant_scope`` is only a
# backwards-compatible default; live multi-tenant call sites always pass scope.
_runtime_root: Optional[str] = None
_configured_tenant_scope: Optional[str] = None

_SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

# The two marks shown at the top of the original ticket poster.  A tenant can
# replace them or leave them empty to use the ticket's typographic fallback.
# For ``promotors`` the bundled files remain a fallback; other tenants never
# fall back to repo logos.
BRAND_LOGOS = {
    "logo": "logo.png",
    "adrenaline": "adrenaline.png",
}

# Promotors-specific partner checklist — intentionally not shown for other
# tenants.  Kept for backward compatibility and for the promotors panel only.
PARTNER_LOGOS = [
    {"name": "1_mcs_sherdor", "title": "Мотоклуб MCS «Sherdor» (Самарканд)"},
    {"name": "2_retro_tashkent", "title": "Авто-Ретро Клуб (Ташкент)"},
    {"name": "3_drift_show", "title": "Uzbekistan Drift Show"},
    {"name": "4_sof_expo", "title": "SOF EXPO Samarkand"},
]

# Default tenant slug that is allowed to use bundled fallback.
_DEFAULT_TENANT_SCOPE = "promotors"


def configure(media_dir: str | os.PathLike[str] | None, tenant_id: object | None = None) -> None:
    """Configure the persistent root and optionally a legacy default tenant.

    ``configure(media_dir)`` deliberately keeps writing to ``media/_sponsors``
    etc. for old command-line tools and existing tests.  New code should pass a
    tenant scope to each read/write function (or configure with ``tenant_id``),
    which writes ``media/_tenants/<scope>/_sponsors`` and siblings.
    """
    global _runtime_root, _configured_tenant_scope
    _runtime_root = os.fspath(media_dir) if media_dir else None
    _configured_tenant_scope = _normalise_scope(tenant_id) if tenant_id is not None else None


def _normalise_scope(tenant_id: object | None) -> Optional[str]:
    if tenant_id is None:
        return None
    scope = str(tenant_id).strip()
    if not _SAFE_SCOPE.fullmatch(scope):
        raise ValueError(f"unsafe tenant asset scope: {scope!r}")
    return scope


def _is_default_scope(tenant_id: object | None) -> bool:
    """True if this scope may use bundled Promotors assets as fallback."""
    scope = _normalise_scope(tenant_id) if tenant_id is not None else _configured_tenant_scope
    # Legacy calls (None) and explicit promotors scope are allowed to fallback.
    return scope is None or scope == _DEFAULT_TENANT_SCOPE


def tenant_root(tenant_id: object) -> Optional[str]:
    """Return the tenant's isolated runtime root, or ``None`` if unconfigured."""
    if not _runtime_root:
        return None
    scope = _normalise_scope(tenant_id)
    if not scope:
        return None
    return os.path.join(_runtime_root, "_tenants", scope)


def tenant_media_dir(media_dir: str, tenant_id: object) -> str:
    """Return the root used for tenant participant photos as well as artwork."""
    root = os.path.join(media_dir, "_tenants", _normalise_scope(tenant_id) or "")
    return root


def is_safe_name(name: str) -> bool:
    return bool(_SAFE_NAME.fullmatch(name or ""))


def _runtime_dir(kind: str, tenant_id: object | None = None) -> Optional[str]:
    """Internal path helper retained for old integrations/tests.

    A passed ``tenant_id`` is always authoritative.  Without it, an explicitly
    configured default scope is used; otherwise this returns the pre-migration
    single-tenant location.
    """
    if not _runtime_root:
        return None
    scope = _normalise_scope(tenant_id) if tenant_id is not None else _configured_tenant_scope
    if scope:
        return os.path.join(_runtime_root, "_tenants", scope, f"_{kind}")
    return os.path.join(_runtime_root, f"_{kind}")


def migrate_legacy_assets(media_dir: str, tenant_id: object = "promotors") -> dict[str, int]:
    """Move old global asset directories into one tenant, without data loss.

    The default target is exactly ``media/_tenants/promotors`` to match the
    documented migration path.  Existing destination files win; conflicting
    old files receive a ``.legacy-N`` suffix rather than being overwritten.
    The function is idempotent and safe to run on every startup.
    """
    moved: dict[str, int] = {"sponsors": 0, "brand": 0, "directions": 0}
    base = Path(media_dir)
    scope = _normalise_scope(tenant_id)
    if not scope:
        return moved
    for kind in moved:
        source = base / f"_{kind}"
        destination = base / "_tenants" / scope / f"_{kind}"
        if not source.is_dir():
            continue
        destination.mkdir(parents=True, exist_ok=True)
        for child in list(source.iterdir()):
            target = destination / child.name
            if target.exists():
                stem, suffix = target.stem, target.suffix
                counter = 1
                while target.exists():
                    target = destination / f"{stem}.legacy-{counter}{suffix}"
                    counter += 1
            shutil.move(str(child), str(target))
            moved[kind] += 1
        try:
            source.rmdir()
        except OSError:
            pass
    return moved


def sponsors_dirs(tenant_id: object | None = None) -> list[str]:
    """Search paths for sponsor logos, tenant upload first then bundled fallback.

    Bundled fallback is only included for the default ``promotors`` tenant
    (or legacy calls without a scope). Other tenants see only their own uploads.
    """
    dirs: list[str] = []
    runtime = _runtime_dir("sponsors", tenant_id)
    if runtime:
        dirs.append(runtime)
    if _is_default_scope(tenant_id):
        dirs.append(_BUNDLED_SPONSORS)
    return dirs


def sponsor_files(tenant_id: object | None = None) -> list[str]:
    """Return sponsor paths in filename order for exactly one tenant.

    For ``promotors`` (and legacy None scope) the bundled repo logos act as
    fallback when nothing was uploaded. For any other tenant the list is
    strictly its own uploads — empty when nothing was uploaded.
    """
    for directory in sponsors_dirs(tenant_id):
        if not os.path.isdir(directory):
            continue
        names = sorted(name for name in os.listdir(directory) if name.lower().endswith(_IMAGE_EXTS))
        if names:
            return [os.path.join(directory, name) for name in names]
    return []


def direction_banner(slug: str, tenant_id: object | None = None) -> Optional[str]:
    """Return a direction banner path; tenant upload takes priority.

    Bundled direction banners are only fallback for promotors/legacy.
    """
    if not slug:
        return None
    candidates: list[str] = []
    runtime = _runtime_dir("directions", tenant_id)
    if runtime:
        candidates.append(runtime)
    if _is_default_scope(tenant_id):
        candidates.append(_BUNDLED_DIRECTIONS)
    for directory in candidates:
        for ext in _IMAGE_EXTS:
            path = os.path.join(directory, slug + ext)
            if os.path.exists(path):
                return path
    return None


def partner_status(tenant_id: object | None = None) -> list[dict]:
    """Report expected partner slots for one tenant's ticket strip.

    Only the default ``promotors`` tenant sees the hard-coded checklist.
    Other tenants return an empty list so their panel never shows another
    client's partners.
    """
    scope = _normalise_scope(tenant_id) if tenant_id is not None else _configured_tenant_scope
    # Only promotors (and legacy None for old scripts) gets the checklist.
    if scope is not None and scope != _DEFAULT_TENANT_SCOPE:
        return []
    loaded = {os.path.splitext(os.path.basename(path))[0]: path for path in sponsor_files(tenant_id)}
    runtime = _runtime_dir("sponsors", tenant_id)
    output = []
    for slot in PARTNER_LOGOS:
        path = loaded.get(slot["name"])
        if not path:
            source = None
        elif runtime and os.path.commonpath([os.path.abspath(path), os.path.abspath(runtime)]) == os.path.abspath(runtime):
            source = "runtime"
        else:
            source = "bundled"
        output.append({**slot, "source": source})
    return output


def brand_logo(name: str, tenant_id: object | None = None) -> Optional[str]:
    """Return a main brand logo path scoped to one tenant.

    For ``promotors`` and legacy None scope the bundled repo file is fallback.
    For other tenants only runtime uploads are considered — otherwise None.
    """
    bundled_name = BRAND_LOGOS.get(name)
    if not bundled_name:
        return None
    runtime = _runtime_dir("brand", tenant_id)
    if runtime:
        for ext in _IMAGE_EXTS:
            path = os.path.join(runtime, name + ext)
            if os.path.exists(path):
                return path
    if _is_default_scope(tenant_id):
        path = os.path.join(_BUNDLED_ROOT, bundled_name)
        return path if os.path.exists(path) else None
    return None


def save_brand(name: str, data: bytes, tenant_id: object | None = None) -> str:
    """Persist a main brand logo beneath the selected tenant root."""
    if name not in BRAND_LOGOS:
        raise ValueError(f"unknown brand logo: {name!r}")
    return _save("brand", name, data, tenant_id)


def save_sponsor(name: str, data: bytes, tenant_id: object | None = None) -> str:
    """Persist a sponsor logo beneath the selected tenant root."""
    return _save("sponsors", name, data, tenant_id)


def save_direction(slug: str, data: bytes, tenant_id: object | None = None) -> str:
    """Persist a direction banner beneath the selected tenant root."""
    return _save("directions", slug, data, tenant_id)


def _save(kind: str, name: str, data: bytes, tenant_id: object | None = None) -> str:
    if not is_safe_name(name):
        raise ValueError(f"unsafe asset name: {name!r}")
    directory = _runtime_dir(kind, tenant_id)
    if not directory:
        raise RuntimeError("runtime asset storage is not configured")
    os.makedirs(directory, exist_ok=True)
    for ext in _IMAGE_EXTS:
        old = os.path.join(directory, name + ext)
        if os.path.exists(old):
            os.remove(old)
    path = os.path.join(directory, name + ".png")
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def delete_asset(kind: str, name: str, tenant_id: object | None = None) -> bool:
    """Delete a runtime upload from only the selected tenant."""
    if kind not in ("sponsors", "directions", "brand") or not is_safe_name(name):
        return False
    directory = _runtime_dir(kind, tenant_id)
    if not directory:
        return False
    removed = False
    for ext in _IMAGE_EXTS:
        path = os.path.join(directory, name + ext)
        if os.path.exists(path):
            os.remove(path)
            removed = True
    return removed


def inventory(tenant_id: object | None = None) -> dict:
    """Return an asset inventory strictly scoped to one tenant."""
    runtime_sponsors = _runtime_dir("sponsors", tenant_id)
    runtime_directions = _runtime_dir("directions", tenant_id)

    def listing(directory: Optional[str]) -> list[str]:
        if not directory or not os.path.isdir(directory):
            return []
        return sorted(name for name in os.listdir(directory) if name.lower().endswith(_IMAGE_EXTS))

    brand: dict[str, str] = {}
    for name in BRAND_LOGOS:
        path = brand_logo(name, tenant_id)
        runtime_brand = _runtime_dir("brand", tenant_id)
        if not path:
            brand[name] = "—"
        elif runtime_brand and os.path.commonpath([os.path.abspath(path), os.path.abspath(runtime_brand)]) == os.path.abspath(runtime_brand):
            brand[name] = "загружен"
        else:
            brand[name] = "из репозитория"

    return {
        "partners": partner_status(tenant_id),
        "sponsors_runtime": listing(runtime_sponsors),
        "sponsors_bundled": listing(_BUNDLED_SPONSORS) if _is_default_scope(tenant_id) else [],
        "directions_runtime": listing(runtime_directions),
        "directions_bundled": listing(_BUNDLED_DIRECTIONS) if _is_default_scope(tenant_id) else [],
        "brand": brand,
        "storage_configured": bool(_runtime_root),
        "tenant_scope": _normalise_scope(tenant_id) if tenant_id is not None else _configured_tenant_scope,
    }
