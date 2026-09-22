"""Compatibility service export for the tenant polling manager.

The manager lives at :mod:`bot.bot_manager` because it wires handlers and
process-level configuration, while this module gives service-oriented callers a
stable ``bot.services.bot_manager`` import path.
"""
from __future__ import annotations

from ..bot_manager import BotManager, BotRuntime, publish_commands

__all__ = ["BotManager", "BotRuntime", "publish_commands"]
