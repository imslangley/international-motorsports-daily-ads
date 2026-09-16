"""Intake for packages that arrive from ChatGPT via GitHub.

Codex commits the day's creative into `inbox/` and pushes. What lands is a folder
with `graphic.png` and `description.txt` - but no `listing.json`, because that file
is this system's own record of what the live site said.

So intake does the thing the spec actually asks for: work out which unit the creative
claims, then go and read that unit's live listing and write `listing.json` from it.
Every later check compares the creative against the site, not against the claim.

Resolution order, most trustworthy first:
  1. brief.json, if Codex wrote one (inventory_url or stock_number)
  2. the inventory URL inside description.txt - the spec requires it to be there
  3. the stock number in the package folder name, matched against live inventory
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .core import REPO_ROOT, Unit, get_logger
from .inventory import InventoryError, fetch_unit_by_url, fetch_units
from .packaging import (DESCRIPTION_FILE, GRAPHIC_FILE, LISTING_FILE, list_packages,
                        write_listing)

INVENTORY_URL_RE = re.compile(
    r"https?://(?:www\.)?internationalmotorsports\.com/inventory/[A-Za-z0-9\-]+i/?",
    re.I)

BRIEF_FILE = "brief.json"


class IntakeError(RuntimeError):
    """Raised when a supplied package cannot be tied to a real listing."""


# ---------------------------------------------------------------------- git

def git_sync(*, dry_run: bool = False) -> str:
    """Pulls whatever Codex pushed. Read-only against the remote."""
    log = get_logger()
    if dry_run:
        log.info("DRY RUN: would run git pull --ff-only")
        return "skipped"
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=120)
    except FileNotFoundError as exc:
        raise IntakeError("git is not on PATH, so the repo cannot be synced.") from exc
    except subprocess.TimeoutExpired as exc:
        raise IntakeError("git pull timed out after 120s.") from exc

    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise IntakeError(
            f"git pull failed:\n{output}\n\n"
            f"If this is a diverged history, resolve it by hand - this tool will not "
            f"rewrite or discard commits."
        )
    log.info("git pull: %s", output.splitlines()[-1] if output else "already up to date")
    return output


# ------------------------------------------------------------------ resolve

def find_supplied_packages() -> list[Path]:
    """Inbox packages that have creative but no listing.json yet."""
    out = []
    for pkg in list_packages("inbox"):
        if (pkg / LISTING_FILE).exists():
            continue
        if (pkg / GRAPHIC_FILE).exists() or (pkg / DESCRIPTION_FILE).exists():
            out.append(pkg)
    return out


def url_from_brief(pkg: Path) -> str:
    brief = pkg / BRIEF_FILE
    if not brief.exists():
        return ""
    try:
        data = json.loads(brief.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        get_logger().warning("%s has an unreadable %s: %s", pkg.name, BRIEF_FILE, exc)
        return ""
    for key in ("inventory_url", "url", "listing_url"):
        if data.get(key):
            return str(data[key]).strip()
    return ""


def url_from_description(pkg: Path) -> str:
    path = pkg / DESCRIPTION_FILE
    if not path.exists():
        return ""
    match = INVENTORY_URL_RE.search(path.read_text(encoding="utf-8"))
    return match.group(0).rstrip("/") if match else ""


def stock_from_package_name(pkg: Path) -> str:
    """YYYY-MM-DD_stock-number_year-make-model-trim -> the stock-number part."""
    parts = pkg.name.split("_")
    return parts[1] if len(parts) >= 3 else ""


def resolve_unit(pkg: Path, config: dict) -> tuple[Unit, str]:
    """Finds the live listing this package is about. Returns (unit, how)."""
    log = get_logger()

    url = url_from_brief(pkg)
    if url:
        log.info("%s: unit identified from %s", pkg.name, BRIEF_FILE)
        return fetch_unit_by_url(config, url), f"{BRIEF_FILE} -> {url}"

    url = url_from_description(pkg)
    if url:
        log.info("%s: unit identified from the inventory URL in the description",
                 pkg.name)
        return fetch_unit_by_url(config, url), f"description.txt -> {url}"

    stock = stock_from_package_name(pkg)
    if stock:
        log.info("%s: no URL supplied - matching stock '%s' against live inventory",
                 pkg.name, stock)
        wanted = re.sub(r"[^a-z0-9]", "", stock.lower())
        units = fetch_units(config, limit=config["inventory"].get("max_units", 60))
        for unit in units:
            if re.sub(r"[^a-z0-9]", "", unit.stock_number.lower()) == wanted:
                return unit, f"stock number '{stock}' matched in live inventory"
        raise IntakeError(
            f"{pkg.name}: stock number '{stock}' is not in the current in-stock "
            f"inventory. The unit may have sold, or the package name is wrong. "
            f"Nothing was assumed."
        )

    raise IntakeError(
        f"{pkg.name}: cannot tell which unit this is.\n"
        f"Add the inventory URL to {DESCRIPTION_FILE} (the spec requires it), or "
        f"include a {BRIEF_FILE} with an 'inventory_url' field, or name the package "
        f"YYYY-MM-DD_stock-number_year-make-model-trim."
    )


def intake_package(pkg: Path, config: dict, *, dry_run: bool = False) -> Unit:
    """Ties a supplied package to its live listing and writes listing.json."""
    log = get_logger()
    try:
        unit, how = resolve_unit(pkg, config)
    except InventoryError as exc:
        raise IntakeError(f"{pkg.name}: {exc}") from exc

    log.info("%s: verified against the live listing (%s)", pkg.name, how)
    if not dry_run:
        write_listing(pkg, unit)
        from .packaging import append_approval
        append_approval(pkg, f"Intake: unit resolved via {how}")
    return unit
