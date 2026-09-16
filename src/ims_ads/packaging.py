"""Ad package layout, state transitions, and the ad-history.csv record.

Package layout (spec: YYYY-MM-DD_stock-number_year-make-model-trim):

    <state>/<package-name>/
        listing.json          source inventory information, as read from the site
        source-image.<ext>    the exact-unit photo, downloaded from its own listing
        graphic.png           the final 1080x1080 social graphic
        description.txt       the matching social description
        validation.txt        validation results
        approval.md           approval notes and the audit trail

A package only ever moves between state folders. Files are never deleted and a move
never overwrites an existing package.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import shutil
from pathlib import Path

from .core import (ARCHIVE, HISTORY_COLUMNS, HISTORY_PATH, INBOX, ISSUES,
                   PUBLISHED, READY, STATE_DIRS, Unit, get_logger, money_str)

LISTING_FILE = "listing.json"
GRAPHIC_FILE = "graphic.png"
DESCRIPTION_FILE = "description.txt"
VALIDATION_FILE = "validation.txt"
APPROVAL_FILE = "approval.md"
SOURCE_IMAGE_STEM = "source-image"


class PackageError(RuntimeError):
    """Raised for an impossible or unsafe package operation."""


# ------------------------------------------------------------------- packages

def package_dir(state: str, name: str) -> Path:
    if state not in STATE_DIRS:
        raise PackageError(f"Unknown state '{state}'. Valid: {', '.join(STATE_DIRS)}")
    return STATE_DIRS[state] / name


def find_package(name: str) -> tuple[str, Path] | None:
    """Locates a package by name across every state folder."""
    for state, root in STATE_DIRS.items():
        candidate = root / name
        if candidate.is_dir():
            return state, candidate
    return None


def list_packages(state: str) -> list[Path]:
    root = STATE_DIRS[state]
    if not root.exists():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def create_package(unit: Unit, state: str = "inbox") -> Path:
    """Creates the package folder and writes listing.json. Never overwrites."""
    target = package_dir(state, unit.package_name)
    if target.exists():
        raise PackageError(
            f"A package named {unit.package_name} already exists in {state}/. "
            f"Refusing to overwrite it."
        )
    target.mkdir(parents=True)
    write_listing(target, unit)
    get_logger().info("Created package %s/%s", state, unit.package_name)
    return target


def write_listing(pkg: Path, unit: Unit) -> Path:
    path = pkg / LISTING_FILE
    payload = unit.to_dict()
    payload["_note"] = ("Read from the live listing. Never edit by hand - re-run the "
                        "workflow so the values stay traceable to the site.")
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def read_listing(pkg: Path) -> Unit:
    path = pkg / LISTING_FILE
    if not path.exists():
        raise PackageError(f"{pkg.name} has no {LISTING_FILE} - it is not a valid package.")
    return Unit.from_dict(json.loads(path.read_text(encoding="utf-8")))


def package_files(pkg: Path) -> dict[str, Path | None]:
    """Resolves the well-known files, tolerating any source-image extension."""
    source = next((p for p in sorted(pkg.glob(f"{SOURCE_IMAGE_STEM}.*"))), None)
    return {
        "listing": (pkg / LISTING_FILE) if (pkg / LISTING_FILE).exists() else None,
        "source_image": source,
        "graphic": (pkg / GRAPHIC_FILE) if (pkg / GRAPHIC_FILE).exists() else None,
        "description": (pkg / DESCRIPTION_FILE) if (pkg / DESCRIPTION_FILE).exists() else None,
        "validation": (pkg / VALIDATION_FILE) if (pkg / VALIDATION_FILE).exists() else None,
        "approval": (pkg / APPROVAL_FILE) if (pkg / APPROVAL_FILE).exists() else None,
    }


# ------------------------------------------------------------ state transitions

# Only these moves are legal. Anything else is a bug or a manual mistake.
ALLOWED_TRANSITIONS = {
    ("inbox", "ready"),
    ("inbox", "issues"),
    ("ready", "published"),
    ("ready", "issues"),
    ("ready", "inbox"),
    ("issues", "inbox"),
    ("issues", "archive"),
    ("published", "archive"),
    ("archive", "inbox"),
}


def move_package(name: str, to_state: str, *, dry_run: bool = False,
                 reason: str = "") -> Path:
    """Moves a package between states, enforcing the legal transitions."""
    log = get_logger()
    found = find_package(name)
    if not found:
        raise PackageError(f"No package named {name} in any state folder.")
    from_state, current = found

    if from_state == to_state:
        log.info("%s is already in %s/", name, to_state)
        return current

    if (from_state, to_state) not in ALLOWED_TRANSITIONS:
        raise PackageError(
            f"Illegal transition {from_state} -> {to_state} for {name}. "
            f"Allowed from {from_state}: "
            + ", ".join(t for f, t in ALLOWED_TRANSITIONS if f == from_state)
        )

    target = package_dir(to_state, name)
    if target.exists():
        raise PackageError(
            f"{name} already exists in {to_state}/. Refusing to overwrite. "
            f"Move or rename the existing package first."
        )

    log.info("%s: %s -> %s%s", name, from_state, to_state,
             f" ({reason})" if reason else "")
    if dry_run:
        return target

    shutil.move(str(current), str(target))
    append_approval(target, f"Moved {from_state} -> {to_state}"
                            + (f": {reason}" if reason else ""))
    return target


def append_approval(pkg: Path, entry: str) -> None:
    """Appends a timestamped line to the package's audit trail."""
    path = pkg / APPROVAL_FILE
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = "" if path.exists() else f"# Approval notes - {pkg.name}\n\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"{header}- {stamp}  {entry}\n")


# --------------------------------------------------------------------- history

def read_history() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open(newline="", encoding="utf-8-sig") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def write_history(rows: list[dict]) -> None:
    """Full rewrite through a temp file, so an interrupted write cannot truncate
    the permanent record."""
    tmp = HISTORY_PATH.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=HISTORY_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in HISTORY_COLUMNS})
    tmp.replace(HISTORY_PATH)


def append_history(unit: Unit, *, status: str, platform: str = "",
                   post_url: str = "", notes: str = "",
                   graphic_path: str = "", description_path: str = "",
                   dry_run: bool = False) -> dict:
    """Adds one row. The spec requires this immediately after publishing."""
    row = {
        "published_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": status,
        "stock_number": unit.stock_number,
        "vin": unit.vin,
        "year": unit.year,
        "make": unit.make,
        "model": unit.model,
        "trim": unit.trim,
        "color": unit.color,
        "inventory_url": unit.inventory_url,
        "graphic_path": graphic_path,
        "description_path": description_path,
        "platform": platform,
        "post_url": post_url,
        "notes": " | ".join(n for n in [notes, f"sale {money_str(unit.sale_price)}",
                                        f"save {money_str(unit.savings)}"] if n.strip()),
    }
    if not dry_run:
        rows = read_history()
        rows.append(row)
        write_history(rows)
        get_logger().info("ad-history.csv += %s (%s)", unit.identity, status)
    return row


def write_validation(pkg: Path, report_text: str, dry_run: bool = False) -> Path:
    path = pkg / VALIDATION_FILE
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not dry_run:
        path.write_text(f"Validated: {stamp}\n\n{report_text}\n", encoding="utf-8")
    return path


def write_issue_note(name: str, reasons: list[str], *, dry_run: bool = False) -> Path:
    """A dated note in issues/, per the spec's 'record blockers in issues/'."""
    ISSUES.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now()
    path = ISSUES / f"{stamp:%Y-%m-%d}_{name}_ISSUE.md"
    body = [
        f"# Blocked: {name}",
        "",
        f"- Logged: {stamp:%Y-%m-%d %H:%M:%S}",
        "",
        "## Why production stopped",
        "",
    ]
    body += [f"- {r}" for r in reasons]
    body += [
        "",
        "## What to do",
        "",
        "Nothing was fabricated or substituted. Correct the source listing or the",
        "supplied package, then re-run the workflow. To re-run a unit deliberately,",
        "document the approval here and use `--allow-rerun`.",
        "",
    ]
    if not dry_run:
        path.write_text("\n".join(body), encoding="utf-8")
    return path
