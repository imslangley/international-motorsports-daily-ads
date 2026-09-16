"""Publishing-state transitions.

The spec's rules, each as a test:
  * only items in ready/ may be published
  * a publish is not complete until the package is in published/ AND the history row
    is written
  * material moves to archive/ without losing its history
  * nothing is overwritten
"""
from __future__ import annotations

import pytest

from ims_ads import packaging
from ims_ads.packaging import (ALLOWED_TRANSITIONS, PackageError, append_history,
                               create_package, find_package, list_packages,
                               move_package, read_history)

from .conftest import make_unit


def test_create_package_lands_in_inbox(workspace, unit):
    pkg = create_package(unit, state="inbox")
    assert pkg.exists()
    assert (pkg / "listing.json").exists()
    assert find_package(unit.package_name)[0] == "inbox"


def test_package_name_follows_the_spec(workspace, unit):
    name = unit.package_name
    date, stock, descriptor = name.split("_", 2)
    assert len(date) == 10 and date[4] == "-"
    assert stock == "2024-lxp-orioli"
    assert descriptor == "2024-mv-agusta-lxp-orioli"


def test_create_package_never_overwrites(workspace, unit):
    create_package(unit, state="inbox")
    with pytest.raises(PackageError, match="already exists"):
        create_package(make_unit(), state="inbox")


def test_inbox_to_ready_to_published(workspace, unit):
    create_package(unit, state="inbox")
    move_package(unit.package_name, "ready")
    assert find_package(unit.package_name)[0] == "ready"
    move_package(unit.package_name, "published")
    assert find_package(unit.package_name)[0] == "published"


def test_inbox_cannot_jump_straight_to_published(workspace, unit):
    """The whole point of ready/ is that nothing skips validation."""
    create_package(unit, state="inbox")
    with pytest.raises(PackageError, match="Illegal transition"):
        move_package(unit.package_name, "published")


def test_published_cannot_go_back_to_ready(workspace, unit):
    create_package(unit, state="published")
    with pytest.raises(PackageError, match="Illegal transition"):
        move_package(unit.package_name, "ready")


def test_published_may_only_be_archived(workspace, unit):
    create_package(unit, state="published")
    move_package(unit.package_name, "archive")
    assert find_package(unit.package_name)[0] == "archive"


def test_issues_can_be_reworked_back_to_inbox(workspace, unit):
    create_package(unit, state="issues")
    move_package(unit.package_name, "inbox")
    assert find_package(unit.package_name)[0] == "inbox"


def test_every_allowed_transition_is_reachable(workspace):
    """Guards against a typo silently removing a legal path."""
    for source, target in sorted(ALLOWED_TRANSITIONS):
        unit = make_unit(stock_number=f"{source}-{target}",
                         inventory_url=f"https://x/inventory/{abs(hash((source,target)))%9999}i")
        create_package(unit, state=source)
        move_package(unit.package_name, target)
        assert find_package(unit.package_name)[0] == target


def test_move_refuses_to_overwrite_an_existing_package(workspace, unit):
    create_package(unit, state="inbox")
    create_package(make_unit(), state="ready")  # same name, already there
    with pytest.raises(PackageError, match="Refusing to overwrite"):
        move_package(unit.package_name, "ready")


def test_move_is_a_no_op_when_already_in_target_state(workspace, unit):
    create_package(unit, state="ready")
    move_package(unit.package_name, "ready")
    assert len(list_packages("ready")) == 1


def test_dry_run_move_changes_nothing(workspace, unit):
    create_package(unit, state="inbox")
    move_package(unit.package_name, "ready", dry_run=True)
    assert find_package(unit.package_name)[0] == "inbox"
    assert not list_packages("ready")


def test_history_row_written_on_publish(workspace, unit):
    append_history(unit, status="PUBLISHED", platform="gohighlevel",
                   post_url="https://facebook.com/post/1")
    rows = read_history()
    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "PUBLISHED"
    assert row["stock_number"] == unit.stock_number
    assert row["vin"] == unit.vin
    assert row["inventory_url"] == unit.inventory_url
    assert row["post_url"] == "https://facebook.com/post/1"


def test_history_dry_run_does_not_write(workspace, unit):
    append_history(unit, status="PUBLISHED", dry_run=True)
    assert read_history() == []


def test_history_is_append_only_and_keeps_prior_rows(workspace):
    for i in range(3):
        append_history(make_unit(stock_number=f"S-{i}",
                                 inventory_url=f"https://x/inventory/{i}i"),
                       status="PUBLISHED")
    rows = read_history()
    assert [r["stock_number"] for r in rows] == ["S-0", "S-1", "S-2"]


def test_approval_trail_records_each_move(workspace, unit):
    pkg = create_package(unit, state="inbox")
    move_package(unit.package_name, "ready")
    moved = find_package(unit.package_name)[1]
    trail = (moved / "approval.md").read_text(encoding="utf-8")
    assert "inbox -> ready" in trail


def test_unknown_state_is_rejected(workspace, unit):
    with pytest.raises(PackageError, match="Unknown state"):
        packaging.package_dir("nowhere", "any-package")


def test_move_to_an_unknown_state_is_rejected(workspace, unit):
    create_package(unit, state="inbox")
    with pytest.raises(PackageError, match="Illegal transition"):
        move_package(unit.package_name, "nowhere")
