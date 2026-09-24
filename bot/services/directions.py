"""Tenant-specific directions with optional 2-level hierarchy.

This module provides helpers that work with the ``directions`` table.
The old global ``bot.constants.DIRECTIONS`` remains as fallback for legacy
code and for seeding the default ``promotors`` tenant.

Storage decision (documented):
- Final choice is stored in ``applications.direction`` as a human-readable
  string.  When a child (podnapravleniye) is selected the format is
  ``"Parent — Child"`` (em dash separated).  This keeps existing rows
  untouched and remains searchable.
- ``directions.exclusive_group`` (non-empty) marks a single-choice group:
  categories sharing one value are mutually exclusive — the participant may
  pick only ONE of them, and a later pick replaces the earlier one
  (:func:`apply_exclusive_selection`).
- Additionally ``applications.direction_id`` stores the leaf direction's DB id
  when the choice came from the DB table.  Old rows have NULL there.
- Export (CSV/Excel/Sheets) shows the final string (Parent — Child) so admins
  see exactly what participant picked.

Every helper here takes the *whole* direction list of one tenant plus the id of
the leaf the participant tapped.  Passing a list (instead of pre-resolved
objects) keeps the handlers free of parent lookups — and an id that is not in
the list yields ``""`` rather than an exception, so a stale inline button can
never freeze the registration form.

Isolation:
- Every query is filtered by ``tenant_id``; ``splshow`` never sees
  ``promotors`` directions.

Admin CRUD is exposed via ``Database`` methods; this module only formats
labels and builds the hierarchy for the bot keyboard.
"""
from __future__ import annotations

from typing import Iterable, Optional

from ..db import Direction


def label_for(direction: Direction, lang: str) -> str:
    """Button-style label for one direction in ``lang`` (canonical fallback)."""
    if lang == "uz":
        return direction.label_uz or direction.canonical
    return direction.label_ru or direction.canonical


# Historic private name kept for any out-of-tree caller.
_label_for = label_for


def exclusive_group_of(direction: Optional[Direction]) -> str:
    """Single-choice group of a direction (``""`` = combines freely)."""
    return str(getattr(direction, "exclusive_group", "") or "").strip()


def apply_exclusive_selection(
    directions: Iterable[Direction], selected: list[int], leaf_id: int
) -> list[int]:
    """Single-choice groups: return ``selected`` ready for adding ``leaf_id``.

    Categories of one tenant that share a non-empty ``exclusive_group`` form a
    mutually exclusive group — the participant may keep only ONE of them.  When
    ``leaf_id`` belongs to such a group, every earlier pick from the *same*
    group is dropped, so the new pick silently replaces it.  Picks from other
    groups (and ungrouped categories) are kept; an unknown ``leaf_id`` or a
    groupless one changes nothing.
    """
    directions = list(directions)
    leaf = find_direction_by_id(directions, leaf_id)
    group = exclusive_group_of(leaf)
    if not group:
        return selected
    by_id = {d.id: d for d in directions}
    return [
        i for i in selected
        if i == leaf_id or exclusive_group_of(by_id.get(i)) != group
    ]


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


def find_direction_by_canonical(directions: Iterable[Direction], canonical: str) -> Optional[Direction]:
    """Find a direction by its canonical (exact match)."""
    for d in directions:
        if d.canonical == canonical:
            return d
    return None


def find_direction_by_id(directions: Iterable[Direction], dir_id: Optional[int]) -> Optional[Direction]:
    if dir_id is None:
        return None
    for d in directions:
        if d.id == dir_id:
            return d
    return None


def children_of(directions: Iterable[Direction], parent_id: Optional[int]) -> list[Direction]:
    """Active children of one parent, in keyboard order."""
    if parent_id is None:
        return []
    kids = [d for d in directions if d.parent_id == parent_id and d.is_active]
    return sorted(kids, key=lambda d: (d.sort_order, d.id))


def _join(parent_text: str, child_text: str) -> str:
    """``Parent — Child`` unless the child already carries the parent's name.

    Seeded children store their canonical pre-joined (``"SPL Автозвук — SPL
    Front"``) while their short label is just ``"SPL Front"``; admins can also
    type the full name into a label.  Joining blindly would produce
    ``"SPL Автозвук — SPL Автозвук — SPL Front"``.
    """
    child_text = (child_text or "").strip()
    parent_text = (parent_text or "").strip()
    if not parent_text:
        return child_text
    if not child_text:
        return parent_text
    if child_text == parent_text or child_text.startswith(f"{parent_text} —"):
        return child_text
    return f"{parent_text} — {child_text}"


def format_final_choice(directions: Iterable[Direction], leaf_id: Optional[int]) -> str:
    """Return the string stored in ``applications.direction`` for a leaf id.

    Uses canonical (Russian) values so the database and the exports stay
    searchable, matching the pre-hierarchy rows.  ``""`` when the id is
    unknown/deleted — callers then fall back to their own recovery path.
    """
    directions = list(directions)
    leaf = find_direction_by_id(directions, leaf_id)
    if leaf is None:
        return ""
    parent = find_direction_by_id(directions, leaf.parent_id)
    if parent is None:
        return leaf.canonical
    return _join(parent.canonical, leaf.canonical)


def localized_final_choice(
    directions: Iterable[Direction], leaf_id: Optional[int], lang: str = "ru"
) -> str:
    """Human-readable localized final choice for the participant's UI."""
    directions = list(directions)
    leaf = find_direction_by_id(directions, leaf_id)
    if leaf is None:
        return ""
    parent = find_direction_by_id(directions, leaf.parent_id)
    label = _label_for(leaf, lang)
    if parent is None:
        return label
    return _join(_label_for(parent, lang), label)
