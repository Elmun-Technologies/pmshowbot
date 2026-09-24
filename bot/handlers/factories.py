"""Fresh aiogram router factories for each tenant dispatcher.

Aiogram routers may be attached to one parent dispatcher only.  Importing the
historic module-level routers twice would therefore make the second tenant fail.
The individual handler modules expose ``create_router``; this helper assembles
an isolated set for every bot worker.

The routers are returned in matching order and the safety net
(:mod:`bot.handlers.stay_alive`) is always *last*: it answers whatever no other
router claimed, so an unknown command or a message sent outside the form can
never leave a participant without a reply.
"""
from __future__ import annotations

from aiogram import Router

from . import moderation, mynumber, registration, stay_alive


def create_tenant_routers() -> tuple[Router, Router, Router, Router]:
    """Return new registration, moderation, number and safety routers."""
    return (
        registration.create_router(),
        moderation.create_router(),
        mynumber.create_router(),
        stay_alive.create_router(),
    )
