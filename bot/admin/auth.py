"""Cookie and password helpers for super-admin and tenant-admin sessions.

Sessions remain stateless HMAC cookies, but super-admin and tenant-admin roles
use different cookie names.  Tenant passwords are PBKDF2 hashes in SQLite; the
hash itself safely serves as the per-tenant cookie-signing secret, so changing a
password invalidates all prior sessions for that tenant.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time

from ..security import is_password_hash, verify_password

# Historic root-panel cookie; retained only for compatibility routes.
COOKIE_NAME = "pm_admin"
SUPER_COOKIE_NAME = "pm_super_admin"
_MAX_AGE = 7 * 24 * 3600


def tenant_cookie_name(slug: str) -> str:
    """Return a safe cookie name isolated from every other tenant."""
    safe = re.sub(r"[^a-z0-9_-]", "_", (slug or "").lower())[:80]
    return f"pm_tenant_{safe or 'unknown'}"


def _sign(secret: str, exp: int) -> str:
    return hmac.new(secret.encode(), str(exp).encode(), hashlib.sha256).hexdigest()


def make_cookie(secret: str) -> str:
    exp = int(time.time()) + _MAX_AGE
    return f"{exp}.{_sign(secret, exp)}"


def valid_cookie(secret: str, value: str | None) -> bool:
    if not secret or not value:
        return False
    try:
        exp_str, sig = value.split(".", 1)
        exp = int(exp_str)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(sig, _sign(secret, exp))


def password_matches(secret: str, submitted: str) -> bool:
    """Verify either an env password or a stored tenant password hash."""
    if is_password_hash(secret):
        return verify_password(secret, submitted or "")
    return hmac.compare_digest((secret or "").encode(), (submitted or "").encode())


MAX_AGE = _MAX_AGE
