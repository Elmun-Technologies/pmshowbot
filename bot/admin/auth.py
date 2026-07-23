"""Minimal single-password session auth for the admin panel.

A signed cookie (HMAC-SHA256 over an expiry timestamp, keyed by the admin
password) proves the visitor logged in. No external dependencies, no server-side
session store. Served only over HTTPS on Fly (force_https), so the cookie and
the login POST are encrypted in transit.
"""
from __future__ import annotations

import hashlib
import hmac
import time

COOKIE_NAME = "pm_admin"
_MAX_AGE = 7 * 24 * 3600  # 7 days


def _sign(secret: str, exp: int) -> str:
    return hmac.new(secret.encode(), str(exp).encode(), hashlib.sha256).hexdigest()


def make_cookie(secret: str) -> str:
    exp = int(time.time()) + _MAX_AGE
    return f"{exp}.{_sign(secret, exp)}"


def valid_cookie(secret: str, value: str | None) -> bool:
    if not value:
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
    return hmac.compare_digest(secret.encode(), (submitted or "").encode())


MAX_AGE = _MAX_AGE
