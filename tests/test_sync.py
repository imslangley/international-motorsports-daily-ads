"""The daily sync, exercised without the network.

Two behaviours matter enough to pin down:

  * A dry run must still validate what Codex pushed - an earlier version skipped the
    pull and the validation, so a "dry-run week" would have tested nothing - while
    leaving the real package exactly where it was.
  * A pull that changes code or configuration must stop the run. Codex has write
    access to main and this job executes whatever is on main every morning.
"""
from __future__ import annotations

import argparse

from PIL import Image

from ims_ads import cli, core, intake, packaging
from ims_ads.copywriting import build_from_template

from .conftest import make_unit


def _args(**overrides):
    base = dict(dry_run=False, allow_rerun=False, accept_code_changes=False,
                verbose=False)
    base.update(overrides)
    return argparse.Namespace(**base)


def _codex_package(workspace, config, unit):
    """What Codex pushes: the creative, no listing.json."""
    pkg = workspace / "inbox" / unit.package_name
    pkg.mkdir(parents=True)
    Image.new("RGB", (1080, 1080), "white").save(pkg / packaging.GRAPHIC_FILE)
    (pkg / packaging.DESCRIPTION_FILE).write_text(build_from_template(unit, config),
                                                  encoding="utf-8")
    return pkg


def _offline(monkeypatch, unit):
    """Replaces the two network steps: the git pull and reading the live listing."""
    monkeypatch.setattr(intake, "git_sync", lambda **kw: [])

    def fake_intake(pkg, config, *, dry_run=False):
        packaging.write_listing(pkg, unit)
        Image.new("RGB", (1620, 1080), "grey").save(pkg / "source-image.jpg")
        return unit

    monkeypatch.setattr(intake, "intake_package", fake_intake)


def _report_text():
    return cli._daily_report_path().read_text(encoding="utf-8")


# ------------------------------------------------------------------ dry run

def test_dry_run_validates_but_leaves_the_package_untouched(workspace, config,
                                                            monkeypatch):
    unit = make_unit()
    pkg = _codex_package(workspace, config, unit)
    before = sorted(p.name for p in pkg.iterdir())
    _offline(monkeypatch, unit)

    assert cli.cmd_sync(_args(dry_run=True), config) == 0

    assert pkg.exists(), "the real package must stay in inbox/"
    assert sorted(p.name for p in pkg.iterdir()) == before, \
        "nothing may be written into the real package on a dry run"
    assert not packaging.list_packages("ready")
    assert "WOULD BE READY TO POST" in _report_text()


def test_dry_run_reports_a_failing_package_without_filing_it(workspace, config,
                                                             monkeypatch):
    unit = make_unit()
    pkg = _codex_package(workspace, config, unit)
    Image.new("RGB", (1200, 1200), "white").save(pkg / packaging.GRAPHIC_FILE)
    _offline(monkeypatch, unit)

    assert cli.cmd_sync(_args(dry_run=True), config) == 1
    assert pkg.exists()
    assert not packaging.list_packages("issues")
    text = _report_text()
    assert "WOULD BE BLOCKED" in text
    assert "graphic.is_1080x1080" in text


def test_dry_run_does_not_revalidate_the_same_package_all_morning(workspace, config,
                                                                  monkeypatch):
    """The task repeats every 30 minutes; each repeat would re-read the live site
    for the same package and invite a rate-limit 403."""
    unit = make_unit()
    _codex_package(workspace, config, unit)
    calls = []
    _offline(monkeypatch, unit)
    real = intake.intake_package
    monkeypatch.setattr(intake, "intake_package",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])

    cli.cmd_sync(_args(dry_run=True), config)
    cli.cmd_sync(_args(dry_run=True), config)
    assert len(calls) == 1


def test_nothing_arriving_is_reported_not_silent(workspace, config, monkeypatch):
    """The single most useful line of the test week."""
    monkeypatch.setattr(intake, "git_sync", lambda **kw: [])
    assert cli.cmd_sync(_args(dry_run=True), config) == 0
    assert "Nothing from Codex yet today" in _report_text()


def test_live_sync_files_to_ready(workspace, config, monkeypatch):
    unit = make_unit()
    _codex_package(workspace, config, unit)
    _offline(monkeypatch, unit)

    assert cli.cmd_sync(_args(), config) == 0
    assert [p.name for p in packaging.list_packages("ready")] == [unit.package_name]
    assert "RESULT: READY TO POST" in _report_text()


# --------------------------------------------------------- code-change guard

def test_protected_paths_are_recognised():
    changed = ["inbox/2026-09-18_X_y/graphic.png", "config/dealership-accelerator.json",
               "src/ims_ads/cli.py", "README.md", "ims-ads.py", "docs/example-package/x"]
    assert intake.protected_changes(changed) == [
        "config/dealership-accelerator.json", "src/ims_ads/cli.py", "ims-ads.py"]


def test_a_normal_codex_push_is_not_protected():
    assert intake.protected_changes([
        "inbox/2026-09-18_2024-LXP-ORIOLI_2024-mv-agusta-lxp-orioli/graphic.png",
        "inbox/2026-09-18_2024-LXP-ORIOLI_2024-mv-agusta-lxp-orioli/description.txt",
        "inbox/2026-09-18_2024-LXP-ORIOLI_2024-mv-agusta-lxp-orioli/brief.json",
    ]) == []


def test_arming_via_a_pushed_config_change_stops_the_run(workspace, config,
                                                        monkeypatch):
    """The concrete risk: a push that flips armed to true must not simply run."""
    def blocked(**kw):
        raise intake.IntakeError("The pull changed code or configuration:\n"
                                 "  - config/dealership-accelerator.json")
    monkeypatch.setattr(intake, "git_sync", blocked)

    assert cli.cmd_sync(_args(), config) == 2
    assert "STOPPED" in _report_text()
