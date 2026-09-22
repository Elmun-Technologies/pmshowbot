"""Tenant-specific directions with optional 2-level hierarchy.

This module provides helpers that work with the new ``directions`` table.
The old global ``bot.constants.DIRECTIONS`` remains as fallback for legacy
code and for seeding the default ``promotors`` tenant.

Storage decision (documented):
- Final choice is stored in ``applications.direction`` as a human-readable
  string.  When a child (podnapravleniye) is selected the format is
  ``"Parent — Child"`` (em dash separated).  This keeps existing 130+ rows
  untouched and remains searchable.
- Additionally ``applications.direction_id`` stores the leaf direction's DB id
  when the choice came from the DB table.  Old rows have NULL there.
- Export (CSV/Excel/Sheets) shows the final string (Parent — Child) so admins
  see exactly what participant picked.

Isolation:
- Every query is filtered by ``tenant_id`` via ``Database.for_tenant`` style.
- ``splshow`` never sees ``promotors`` directions.

Admin CRUD is exposed via ``Database`` methods; this module only formats
labels and builds the hierarchy for the bot keyboard.
"""
from __future__ import annotations

from typing import Optional

from ..db import Direction


def _label_for(direction: Direction, lang: str) -> str:
    if lang == "uz":
        return direction.label_uz or direction.canonical
    return direction.label_ru or direction.canonical


def build_hierarchy(directions: list[Direction]) -> tuple[list[Direction], dict[int, list[Direction]]]:
    """Return (roots, children_by_parent_id) sorted by sort_order.

    Roots are directions with ``parent_id is None`` and active.
    Children are grouped by parent.
    """
    active = [d for d in directions if d.is_active]
    # Sort globally by sort_order then id for determinism
    active_sorted = sorted(active, key=lambda d: (d.sort_order, d.id))
    roots: list[Direction] = []
    children: dict[int, list[Direction]] = {}
    by_id = {d.id: d for d in active_sorted}
    for d in active_sorted:
        if d.parent_id is None:
            roots.append(d)
        else:
            # Only include if parent exists and is active (or at least present)
            if d.parent_id in by_id:
                children.setdefault(d.parent_id, []).append(d)
            else:
                # Orphaned child treated as root to avoid losing it
                roots.append(d)
    # Ensure children sorted too
    for pid in children:
        children[pid] = sorted(children[pid], key=lambda d: (d.sort_order, d.id))
    roots = sorted(roots, key=lambda d: (d.sort_order, d.id))
    return roots, children


def format_final_choice(parent: Direction, child: Optional[Direction], lang: str = "ru") -> str:
    """Return the string stored in ``applications.direction``.

    For a child selection: ``Parent — Child`` using the canonical values
    (canonical is the DB canonical, which stays Russian as per legacy).
    The label for display can be localized separately.
    """
    if child is None:
        return parent.canonical
    # Use canonical for storage (keeps DB searchable and matches old style)
    # But if canonicals equal labels, this still works.
    return f"{parent.canonical} — {child.canonical}"


def localized_final_choice(parent: Direction, child: Optional[Direction], lang: str) -> str:
    """Human-readable localized final choice for UI."""
    if child is None:
        return _label_for(parent, lang)
    return f"{_label_for(parent, lang)} — {_label_for(child, lang)}"


def find_direction_by_canonical(directions: list[Direction], canonical: str) -> Optional[Direction]:
    """Find a direction by its canonical (exact match)."""
    for d in directions:
        if d.canonical == canonical:
            return d
    return None


def find_direction_by_id(directions: list[Direction], dir_id: int) -> Optional[Direction]:
    for d in directions:
        if d.id == dir_id:
            return d
    return None
