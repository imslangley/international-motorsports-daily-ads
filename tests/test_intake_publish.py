"""Intake of Codex-pushed packages, and refusal-to-publish safety.

These cover the ChatGPT -> Codex -> GitHub -> here -> Dealership Accelerator path.
Nothing here touches the network: the unit-resolution helpers are pure, and the
publishing tests assert on refusals, which is the behaviour that matters most.
"""
from __future__ import annotations

import json

import pytest
from PIL import Image

from ims_ads import intake, packaging, publishing
from ims_ads.copywriting import build_from_template

from .conftest import make_unit

LISTING = ("https://www.internationalmotorsports.com/inventory/"
           "2024-mv-agusta-lxp-langley-bc-v1m-4c2-12266444i")


def supplied_package(workspace, name="2026-09-17_2024-LXP-ORIOLI_2024-mv-agusta-lxp-orioli",
                     *, description=None, brief=None):
    """What Codex pushes: creative only, no listing.json."""
    pkg = workspace / "inbox" / name
    pkg.mkdir(parents=True)
    Image.new("RGB", (1080, 1080), "white").save(pkg / packaging.GRAPHIC_FILE)
    if description is not None:
        (pkg / packaging.DESCRIPTION_FILE).write_text(description, encoding="utf-8")
    if brief is not None:
        (pkg / intake.BRIEF_FILE).write_text(json.dumps(brief), encoding="utf-8")
    return pkg


# ---------------------------------------------------------------- finding work

def test_supplied_package_is_picked_up(workspace, config):
    supplied_package(workspace, description="see " + LISTING)
    found = intake.find_supplied_packages()
    assert len(found) == 1


def test_package_that_already_has_a_listing_is_left_alone(workspace, config, unit):
    """Packages this system built itself must not be re-taken-in."""
    packaging.create_package(unit, state="inbox")
    assert intake.find_supplied_packages() == []


# ------------------------------------------------------------ unit resolution

def test_url_is_read_from_the_description(workspace, config, unit):
    pkg = supplied_package(workspace,
                           description=build_from_template(unit, config))
    assert intake.url_from_description(pkg) == LISTING


def test_url_is_read_from_a_brief(workspace, config):
    pkg = supplied_package(workspace, brief={"inventory_url": LISTING})
    assert intake.url_from_brief(pkg) == LISTING


def test_brief_wins_over_description(workspace, config, unit):
    """The brief is the more deliberate statement of intent."""
    other = "https://www.internationalmotorsports.com/inventory/some-other-unit-999i"
    pkg = supplied_package(workspace, description=build_from_template(unit, config),
                           brief={"inventory_url": other})
    assert intake.url_from_brief(pkg) == other


def test_unreadable_brief_does_not_crash_intake(workspace, config):
    pkg = supplied_package(workspace, description="see " + LISTING)
    (pkg / intake.BRIEF_FILE).write_text("{not json", encoding="utf-8")
    assert intake.url_from_brief(pkg) == ""
    assert intake.url_from_description(pkg) == LISTING


def test_stock_number_is_read_from_the_package_name(workspace, config):
    pkg = supplied_package(workspace)
    assert intake.stock_from_package_name(pkg) == "2024-LXP-ORIOLI"


def test_package_with_no_identifying_information_is_refused(workspace, config):
    """No URL, no brief, unusable name - it must stop, not guess a unit."""
    pkg = supplied_package(workspace, name="todays-ad", description="Great bike!")
    with pytest.raises(intake.IntakeError, match="cannot tell which unit"):
        intake.resolve_unit(pkg, config)


def test_a_foreign_url_in_the_description_is_not_treated_as_the_listing(workspace, config):
    pkg = supplied_package(workspace, name="todays-ad",
                           description="See https://example.com/inventory/12345i")
    assert intake.url_from_description(pkg) == ""


# ------------------------------------------------------- publishing refusals

def test_dealership_accelerator_refuses_while_selectors_are_placeholders(workspace):
    """The rule that matters: never click an unidentified control in a live tool."""
    adapter = publishing.ADAPTERS["dealership_accelerator"]()
    missing = adapter.missing_credentials()
    assert missing, "shipped config must still be placeholders"

    with pytest.raises(publishing.PublishError, match="never been captured"):
        adapter.publish(make_unit(), workspace / "graphic.png", "copy")


def test_dealership_accelerator_refusal_names_every_gap(workspace):
    adapter = publishing.ADAPTERS["dealership_accelerator"]()
    try:
        adapter.publish(make_unit(), workspace / "g.png", "copy")
    except publishing.PublishError as exc:
        message = str(exc)
    assert "urls.post_composer" in message
    assert "selectors.submit_button" in message
    assert "da-login" in message


def test_manual_platform_refuses_and_says_what_to_do(workspace):
    adapter = publishing.ADAPTERS["none"]()
    with pytest.raises(publishing.PublishError, match="does not post"):
        adapter.publish(make_unit(), workspace / "g.png", "copy")


def test_unknown_platform_is_rejected(workspace, config):
    config = dict(config)
    config["publishing"] = dict(config["publishing"], platform="myspace")
    with pytest.raises(publishing.PublishError, match="Unknown publishing.platform"):
        publishing.get_adapter(config)


def test_readiness_report_explains_the_dealership_accelerator_gap(workspace, config):
    config = dict(config)
    config["publishing"] = dict(config["publishing"],
                                platform="dealership_accelerator")
    lines = "\n".join(publishing.readiness_report(config))
    assert "no API" in lines
    assert "da-login" in lines


def test_shipped_da_config_contains_no_credentials(workspace):
    """A standing guard: this file is committed, so it must never hold secrets.

    Checks keys and values, not prose - the documentation comments legitimately talk
    about cookies and sessions while carrying none.
    """
    from ims_ads import dealership_accelerator as da

    forbidden = ("password", "passwd", "token", "secret", "cookie", "apikey",
                 "api_key", "bearer", "authorization")
    offenders: list[str] = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("_"):      # documentation, not data
                    continue
                here = f"{path}.{key}" if path else key
                if any(word in key.lower() for word in forbidden):
                    offenders.append(f"key {here}")
                walk(value, here)
        elif isinstance(node, str):
            lowered = node.lower()
            if any(word in lowered for word in forbidden):
                offenders.append(f"value at {path}")

    walk(json.loads(da.CONFIG_FILE.read_text(encoding="utf-8")))
    assert offenders == [], f"credential-looking entries in a committed file: {offenders}"


def test_da_session_profile_lives_outside_the_repo(workspace):
    """The signed-in profile holds live cookies and must never land in the tree."""
    from ims_ads import core, dealership_accelerator as da

    path = da.profile_dir(da.load_da_config()).resolve()
    assert core.REPO_ROOT.resolve() not in path.parents
    assert path != core.REPO_ROOT.resolve()
