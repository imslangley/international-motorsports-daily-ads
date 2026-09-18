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

from . import media
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

# Paths that decide what this job DOES. Codex has write access to main, and the
# 09:30 task executes whatever is on main - so a pull that changes any of these is
# not run blindly. Codex's job is to add folders to inbox/, nothing else.
PROTECTED_PATHS = ("src/", "config/", "scripts/", "tests/", "ims-ads.py",
                   "requirements.txt", "pytest.ini", ".gitignore")


def _git(*args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", *args], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise IntakeError("git is not on PATH, so the repo cannot be synced.") from exc
    except subprocess.TimeoutExpired as exc:
        raise IntakeError(f"git {args[0]} timed out after {timeout}s.") from exc


def protected_changes(paths: list[str]) -> list[str]:
    """The subset of changed paths that alter code or configuration."""
    return [p for p in paths
            if any(p == guard or p.startswith(guard) for guard in PROTECTED_PATHS)]


def git_sync(*, accept_code_changes: bool = False) -> list[str]:
    """Fast-forwards to whatever was pushed. Returns the paths that changed.

    Runs on dry runs too: a fast-forward only brings in what is already on GitHub,
    and a dry run that skipped it would never see what Codex pushed.
    """
    log = get_logger()
    before = _git("rev-parse", "HEAD").stdout.strip()

    result = _git("pull", "--ff-only")
    output = (result.stdout + result.stderr).strip()
    if result.returncode != 0:
        raise IntakeError(
            f"git pull failed:\n{output}\n\n"
            f"If this is a diverged history, resolve it by hand - this tool will not "
            f"rewrite or discard commits."
        )

    after = _git("rev-parse", "HEAD").stdout.strip()
    if before == after:
        log.info("git pull: already up to date")
        return []

    changed = [line for line in
               _git("diff", "--name-only", before, after).stdout.splitlines() if line]
    log.info("git pull: %s -> %s, %d path(s) changed", before[:7], after[:7], len(changed))

    touched = protected_changes(changed)
    if touched and not accept_code_changes:
        raise IntakeError(
            "The pull changed code or configuration, so today's run was stopped "
            "rather than executing it unreviewed:\n"
            + "\n".join(f"  - {p}" for p in touched)
            + f"\n\nCodex should only ever add folders under inbox/. Review with:\n"
              f"  git log -p {before[:7]}..{after[:7]} -- "
            + " ".join(touched)
            + "\nIf the change is intended, run once with --accept-code-changes."
        )
    if touched:
        log.warning("Running despite code/config changes (--accept-code-changes): %s",
                    ", ".join(touched))
    return changed


# ------------------------------------------------------------------ resolve

REQUIRED_FILES = (GRAPHIC_FILE, DESCRIPTION_FILE)


def find_supplied_packages() -> list[Path]:
    """Every inbox package that has not been taken in yet.

    This deliberately returns folders that are INCOMPLETE as well as good ones.
    An earlier version only returned folders containing creative, which meant a
    malformed push sat in inbox/ forever while the run reported "no new packages" -
    the morning would quietly produce nothing and nobody would know.
    """
    return [pkg for pkg in list_packages("inbox")
            if not (pkg / LISTING_FILE).exists()]


def missing_required_files(pkg: Path) -> list[str]:
    """Required files this package does not have."""
    return [name for name in REQUIRED_FILES if not (pkg / name).exists()]


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
    """Ties a supplied package to its live listing, downloads the exact-unit photo,
    and writes listing.json."""
    log = get_logger()
    try:
        unit, how = resolve_unit(pkg, config)
    except InventoryError as exc:
        raise IntakeError(f"{pkg.name}: {exc}") from exc

    log.info("%s: verified against the live listing (%s)", pkg.name, how)
    if dry_run:
        return unit

    from .packaging import SOURCE_IMAGE_STEM, append_approval

    write_listing(pkg, unit)
    append_approval(pkg, f"Intake: unit resolved via {how}")

    # The spec requires the package to carry the exact-unit source image, and it is
    # also the evidence that this unit has a real photo of itself rather than only
    # manufacturer catalog shots. ChatGPT's package has the finished graphic but not
    # this, so it is fetched here from the unit's own listing.
    if not any(pkg.glob(f"{SOURCE_IMAGE_STEM}.*")):
        try:
            image_url, _ = media.choose_exact_unit_image(unit, config)
            setattr(unit, "_chosen_image_url", image_url)
            media.download_image(image_url, pkg / SOURCE_IMAGE_STEM, config)
            write_listing(pkg, unit)
            append_approval(pkg, f"Intake: exact-unit photo downloaded from {image_url}")
        except media.ImageError as exc:
            # Not fatal here: validation reports the missing source image, so the
            # package is blocked with a clear reason rather than dying mid-intake.
            log.error("%s: exact-unit photo unavailable\n%s", pkg.name, exc)
            append_approval(pkg, f"Intake: no exact-unit photo - {exc}".replace("\n", " "))

    return unit
