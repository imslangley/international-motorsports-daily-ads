"""Duplicate protection.

The spec requires four independent identifiers to be checked, because any one of
them can be missing or entered inconsistently on the listing:

    1. stock number
    2. VIN
    3. inventory URL
    4. normalised year/make/model/trim

and requires the live folders (inbox/, ready/, published/) to be searched as well as
ad-history.csv - a unit already in production is a duplicate even though it has never
been published.
"""
from __future__ import annotations

import re

from .core import STATE_DIRS, Unit, get_logger
from .packaging import read_history, read_listing

# States that make a unit "spoken for". archive/ is excluded: archived material is
# retired, so the same unit may legitimately be advertised again.
ACTIVE_STATES = ("inbox", "ready", "published")


def _norm(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").lower())


class DuplicateMatch:
    def __init__(self, field: str, value: str, where: str, detail: str = ""):
        self.field = field
        self.value = value
        self.where = where
        self.detail = detail

    def __str__(self) -> str:
        base = f"{self.field} '{self.value}' already used in {self.where}"
        return f"{base} ({self.detail})" if self.detail else base

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DuplicateMatch {self}>"


def _identifiers(unit: Unit) -> list[tuple[str, str]]:
    """The identifier set, skipping any that the listing did not provide."""
    out: list[tuple[str, str]] = []
    if unit.stock_number:
        out.append(("stock_number", unit.stock_number))
    if unit.vin:
        out.append(("vin", unit.vin))
    if unit.inventory_url:
        out.append(("inventory_url", unit.inventory_url))
    if unit.unit_key:
        out.append(("year/make/model/trim", unit.identity))
    return out


def check_history(unit: Unit) -> list[DuplicateMatch]:
    """Searches ad-history.csv on all four identifiers."""
    matches: list[DuplicateMatch] = []
    rows = read_history()
    for row in rows:
        if (row.get("status") or "").upper() in {"ARCHIVED", "RETIRED", "FAILED"}:
            continue
        row_key = _norm(" ".join(str(row.get(f, "")) for f in
                                ("year", "make", "model", "trim")))
        for field, value in _identifiers(unit):
            if field == "year/make/model/trim":
                if row_key and row_key == unit.unit_key:
                    matches.append(DuplicateMatch(
                        field, value, "ad-history.csv",
                        f"published_at {row.get('published_at', '?')}, "
                        f"status {row.get('status', '?')}"))
                continue
            if _norm(row.get(field.replace("/", "_"), "")) and \
               _norm(row.get(field, "")) == _norm(value):
                matches.append(DuplicateMatch(
                    field, value, "ad-history.csv",
                    f"published_at {row.get('published_at', '?')}, "
                    f"status {row.get('status', '?')}"))
    return matches


def check_folders(unit: Unit, states: tuple[str, ...] = ACTIVE_STATES) -> list[DuplicateMatch]:
    """Searches live package folders for the same unit."""
    matches: list[DuplicateMatch] = []
    log = get_logger()

    for state in states:
        root = STATE_DIRS[state]
        if not root.exists():
            continue
        for pkg in root.iterdir():
            if not pkg.is_dir():
                continue
            try:
                other = read_listing(pkg)
            except Exception as exc:
                log.debug("Skipping unreadable package %s: %s", pkg.name, exc)
                continue

            for field, value in _identifiers(unit):
                if field == "year/make/model/trim":
                    if other.unit_key and other.unit_key == unit.unit_key:
                        matches.append(DuplicateMatch(field, value, f"{state}/{pkg.name}"))
                    continue
                attr = field if hasattr(other, field) else None
                if attr and _norm(getattr(other, attr)) and \
                   _norm(getattr(other, attr)) == _norm(value):
                    matches.append(DuplicateMatch(field, value, f"{state}/{pkg.name}"))
    return matches


def find_duplicates(unit: Unit) -> list[DuplicateMatch]:
    """Every duplicate signal for this unit, de-duplicated by (field, where)."""
    all_matches = check_history(unit) + check_folders(unit)
    seen: set[tuple[str, str]] = set()
    unique: list[DuplicateMatch] = []
    for match in all_matches:
        key = (match.field, match.where)
        if key in seen:
            continue
        seen.add(key)
        unique.append(match)
    return unique


def is_duplicate(unit: Unit) -> bool:
    return bool(find_duplicates(unit))


def first_eligible(units: list[Unit], *, allow_rerun: bool = False) -> tuple[Unit | None, list[str]]:
    """Walks units in site order and returns the first one not already spoken for.

    Returns (unit, skip_notes) so the caller can log exactly why each earlier unit
    was passed over - silence here is what makes a duplicate-protection bug invisible.
    """
    notes: list[str] = []
    for unit in units:
        matches = find_duplicates(unit)
        if matches and not allow_rerun:
            notes.append(f"{unit.identity or unit.inventory_url}: "
                         + "; ".join(str(m) for m in matches[:2]))
            continue
        return unit, notes
    return None, notes
