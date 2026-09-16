"""Duplicate protection.

The spec requires four independent identifiers and a search of the live folders as
well as ad-history.csv. A miss on any one of them means the same unit gets advertised
twice, so each is tested on its own.
"""
from __future__ import annotations

import pytest

from ims_ads import packaging
from ims_ads.dedupe import find_duplicates, first_eligible, is_duplicate

from .conftest import make_unit


def test_fresh_unit_is_not_a_duplicate(workspace, unit):
    assert find_duplicates(unit) == []
    assert not is_duplicate(unit)


def test_duplicate_detected_by_stock_number(workspace, unit):
    packaging.append_history(unit, status="PUBLISHED")
    # Same stock number, everything else different.
    other = make_unit(vin="", inventory_url="https://example.com/inventory/9i",
                      year="2019", make="Honda", model="Rebel", trim="")
    other.stock_number = unit.stock_number
    matches = find_duplicates(other)
    assert any(m.field == "stock_number" for m in matches), matches


def test_duplicate_detected_by_vin(workspace, unit):
    packaging.append_history(unit, status="PUBLISHED")
    other = make_unit(stock_number="", inventory_url="https://example.com/inventory/9i",
                      year="2019", make="Honda", model="Rebel", trim="")
    other.vin = unit.vin
    assert any(m.field == "vin" for m in find_duplicates(other))


def test_duplicate_detected_by_inventory_url(workspace, unit):
    packaging.append_history(unit, status="PUBLISHED")
    other = make_unit(stock_number="", vin="", year="2019", make="Honda",
                      model="Rebel", trim="")
    other.inventory_url = unit.inventory_url
    assert any(m.field == "inventory_url" for m in find_duplicates(other))


def test_duplicate_detected_by_year_make_model_trim(workspace, unit):
    packaging.append_history(unit, status="PUBLISHED")
    # No shared identifiers except the normalised unit description.
    other = make_unit(stock_number="DIFFERENT-STOCK", vin="XXXXXXXXXXXXXXXXX",
                      inventory_url="https://example.com/inventory/999i")
    assert any(m.field == "year/make/model/trim" for m in find_duplicates(other))


def test_year_make_model_matching_ignores_spacing_and_case(workspace, unit):
    packaging.append_history(unit, status="PUBLISHED")
    other = make_unit(stock_number="", vin="", inventory_url="https://x/inventory/1i",
                      make="mv  agusta", model="lxp", trim="ORIOLI")
    assert find_duplicates(other), "normalisation should make these the same unit"


@pytest.mark.parametrize("state", ["inbox", "ready", "published"])
def test_unit_already_in_a_live_folder_is_a_duplicate(workspace, unit, state):
    """A unit in production has never been published, but is still spoken for."""
    packaging.create_package(unit, state=state)
    matches = find_duplicates(make_unit())
    assert matches, f"a package sitting in {state}/ should block a re-run"
    assert any(state in m.where for m in matches)


def test_archived_unit_may_be_advertised_again(workspace, unit):
    """archive/ is retired material, so it must not block a fresh campaign."""
    packaging.create_package(unit, state="archive")
    assert find_duplicates(make_unit()) == []


def test_history_row_marked_archived_does_not_block(workspace, unit):
    packaging.append_history(unit, status="ARCHIVED")
    assert find_duplicates(make_unit()) == []


def test_first_eligible_skips_duplicates_and_reports_why(workspace, unit):
    packaging.append_history(unit, status="PUBLISHED")
    fresh = make_unit(stock_number="NEW-1", vin="VIN00000000000001",
                      inventory_url="https://x/inventory/2i", model="Brutale",
                      trim="1000 RS")
    chosen, notes = first_eligible([make_unit(), fresh])
    assert chosen is fresh
    assert len(notes) == 1 and "already used" in notes[0]


def test_first_eligible_returns_none_when_all_taken(workspace, unit):
    packaging.append_history(unit, status="PUBLISHED")
    chosen, notes = first_eligible([make_unit()])
    assert chosen is None
    assert notes


def test_allow_rerun_overrides_duplicate_block(workspace, unit):
    packaging.append_history(unit, status="PUBLISHED")
    chosen, _ = first_eligible([make_unit()], allow_rerun=True)
    assert chosen is not None, "--allow-rerun must permit a documented repeat"


def test_missing_identifiers_are_skipped_not_matched_as_blank(workspace):
    """Two units both lacking a VIN must not match each other on the empty VIN."""
    a = make_unit(vin="", stock_number="A-1", inventory_url="https://x/inventory/1i",
                  model="Alpha", trim="")
    b = make_unit(vin="", stock_number="B-2", inventory_url="https://x/inventory/2i",
                  model="Beta", trim="")
    packaging.append_history(a, status="PUBLISHED")
    assert find_duplicates(b) == []
