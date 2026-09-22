"""Fresh aiogram router factories for each tenant dispatcher.

Aiogram routers may be attached to one parent dispatcher only.  Importing the
historic module-level routers twice would therefore make the second tenant fail.
The individual handler modules expose ``create_router``; this helper assembles
an isolated set for every bot worker.
"""
from __future__ import annotations

from aiogram import Router

from . import badge_photo, moderation, mynumber, registration


def create_tenant_routers() -> tuple[Router, Router, Router, Router]:
    """Return new registration, moderation, badge and number routers."""
    return (
        registration.create_router(),
        moderation.create_router(),
        badge_photo.create_router(),
        mynumber.create_router(),
    )
