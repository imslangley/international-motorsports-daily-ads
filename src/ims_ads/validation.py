"""Whole-package validation.

Nothing may move to ready/ unless every blocking check passes, and nothing may be
published unless it is in ready/. This module is the single place those rules live.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from .copywriting import check_description
from .core import Unit, ValidationReport, get_logger, money_str
from .dedupe import find_duplicates
from .media import image_belongs_to_unit, image_size, listing_id
from .packaging import package_files, read_listing


def validate_unit_fields(unit: Unit, config: dict, report: ValidationReport) -> None:
    """The listing facts the spec requires us to have verified."""
    sel = config["selection"]

    report.add("unit.inventory_url", bool(unit.inventory_url),
               unit.inventory_url or "no listing URL")
    report.add("unit.year", bool(unit.year), unit.year or "missing")
    report.add("unit.make", bool(unit.make), unit.make or "missing")
    report.add("unit.model", bool(unit.model), unit.model or "missing")

    # Trim and colour are commonly blank on powersports listings, so they are
    # recorded but not blocking.
    report.add("unit.trim", bool(unit.trim), unit.trim or "not shown on listing",
               blocking=False)
    report.add("unit.color", bool(unit.color), unit.color or "not shown on listing",
               blocking=False)

    if sel.get("require_stock_or_vin", True):
        report.add("unit.stock_or_vin", bool(unit.stock_number or unit.vin),
                   f"stock='{unit.stock_number}' vin='{unit.vin}'")

    if sel.get("require_sale_price", True):
        report.add("unit.sale_price", unit.sale_price is not None,
                   money_str(unit.sale_price) or "no sale price on listing")

    if sel.get("require_savings", True):
        report.add("unit.savings", unit.savings is not None,
                   money_str(unit.savings) or "no savings shown on listing")

    floor = sel.get("min_savings_dollars", 0)
    if unit.savings is not None and floor:
        report.add("unit.savings_threshold", unit.savings >= floor,
                   f"{money_str(unit.savings)} vs minimum {money_str(float(floor))}")

    # Internal arithmetic: regular - sale must equal savings.
    if None not in (unit.sale_price, unit.regular_price, unit.savings):
        delta = abs((unit.regular_price - unit.sale_price) - unit.savings)
        report.add("unit.price_arithmetic", delta < 1.0,
                   f"{money_str(unit.regular_price)} - {money_str(unit.sale_price)} "
                   f"= {money_str(unit.regular_price - unit.sale_price)}, "
                   f"listing says {money_str(unit.savings)}")

    blocked = [k for k in sel.get("exclude_title_keywords", [])
               if k.lower() in (unit.title_raw or "").lower()]
    report.add("unit.not_excluded", not blocked,
               f"title contains {blocked}" if blocked else "no excluded keywords")

    # The spec requires a currently in-stock unit. The listing's own JSON-LD says so
    # explicitly; when it publishes nothing, that is recorded but not treated as a
    # failure, because the search URL already filters to In Stock.
    if unit.availability:
        in_stock = "instock" in unit.availability.lower().replace("/", "")
        report.add("unit.in_stock", in_stock, unit.availability)
    else:
        report.add("unit.in_stock", True,
                   "listing published no availability field; search URL filters to "
                   "In Stock", blocking=False)


def validate_freshness(unit: Unit, config: dict, report: ValidationReport) -> None:
    """A stale listing must not be advertised - the price may have moved."""
    max_age = config["validation"].get("listing_max_age_hours", 24)
    if not unit.scraped_at:
        report.add("listing.freshness", False, "no scraped_at timestamp on the package")
        return
    try:
        seen = dt.datetime.fromisoformat(unit.scraped_at)
    except ValueError:
        report.add("listing.freshness", False, f"unreadable timestamp '{unit.scraped_at}'")
        return
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=dt.timezone.utc)
    age_hours = (dt.datetime.now(dt.timezone.utc) - seen).total_seconds() / 3600
    report.add("listing.freshness", age_hours <= max_age,
               f"listing read {age_hours:.1f}h ago, limit {max_age}h")


def validate_duplicates(unit: Unit, report: ValidationReport,
                        allow_rerun: bool = False,
                        exclude: Path | None = None) -> None:
    matches = find_duplicates(unit, exclude=exclude)
    if allow_rerun:
        report.add("duplicate.check", True,
                   f"rerun approved; {len(matches)} prior record(s) ignored",
                   blocking=False)
        return
    report.add("duplicate.check", not matches,
               "; ".join(str(m) for m in matches[:3]) if matches
               else "no match in history, inbox, ready or published")


def validate_media(pkg: Path, unit: Unit, config: dict,
                   report: ValidationReport) -> None:
    files = package_files(pkg)
    want = (config["image"]["output_width"], config["image"]["output_height"])

    source = files["source_image"]
    report.add("image.source_present", source is not None,
               source.name if source else "no source-image.* in the package")

    if source is not None:
        try:
            size = image_size(source)
            report.add("image.source_readable", True, f"{size[0]}x{size[1]}")
        except Exception as exc:
            report.add("image.source_readable", False, str(exc))

    # The strongest available evidence that the photo is of the exact unit.
    chosen_url = getattr(unit, "_chosen_image_url", "")
    if chosen_url:
        report.add("image.belongs_to_unit", image_belongs_to_unit(chosen_url, unit),
                   f"CDN path vs listing id {listing_id(unit) or 'unknown'}")

    graphic = files["graphic"]
    report.add("graphic.present", graphic is not None,
               graphic.name if graphic else "no graphic.png in the package")

    if graphic is not None:
        try:
            size = image_size(graphic)
            report.add("graphic.is_1080x1080", size == want,
                       f"{size[0]}x{size[1]}, required {want[0]}x{want[1]}")
        except Exception as exc:
            report.add("graphic.is_1080x1080", False, f"unreadable: {exc}")


def validate_copy(pkg: Path, unit: Unit, config: dict,
                  report: ValidationReport) -> None:
    files = package_files(pkg)
    path = files["description"]
    if path is None:
        report.add("description.present", False, "no description.txt in the package")
        return
    report.add("description.present", True, path.name)

    text = path.read_text(encoding="utf-8")
    failures, warnings = check_description(text, unit, config)
    for failure in failures:
        report.add("description.content", False, failure)
    for warning in warnings:
        report.add("description.content", False, warning, blocking=False)
    if not failures and not warnings:
        report.add("description.content", True, "matches the unit and the listing")


def validate_package(pkg: Path, config: dict, *,
                     allow_rerun: bool = False,
                     check_duplicates: bool = True) -> tuple[ValidationReport, Unit]:
    """Runs every check. The report decides ready/ versus issues/."""
    log = get_logger()
    report = ValidationReport()
    unit = read_listing(pkg)

    validate_unit_fields(unit, config, report)
    validate_freshness(unit, config, report)
    if check_duplicates:
        # `pkg` is excluded so a package in inbox/ does not match itself.
        validate_duplicates(unit, report, allow_rerun=allow_rerun, exclude=pkg)
    validate_media(pkg, unit, config, report)
    validate_copy(pkg, unit, config, report)

    log.debug("Validation of %s: %d checks, %d blocking failure(s)",
              pkg.name, len(report.checks), len(report.failures))
    return report, unit
