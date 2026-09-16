"""Command line entry point for the daily ad workflow.

    python -m ims_ads run            build today's package from live inventory
    python -m ims_ads run --dry-run  the whole workflow, writing nothing permanent
    python -m ims_ads validate       re-check everything in inbox/
    python -m ims_ads promote NAME   inbox -> ready (only if validation passes)
    python -m ims_ads publish NAME   ready -> published, append history, write log
    python -m ims_ads archive NAME   published/issues -> archive
    python -m ims_ads status         what is in each state
    python -m ims_ads doctor         what is still missing to run end to end
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
import tempfile
from pathlib import Path

from . import copywriting, intake, media, packaging, publishing
from .core import (ConfigError, STATE_DIRS, Unit, ValidationReport, ensure_dirs,
                   get_logger, load_config, load_dotenv, money_str, setup_logging)
from .dedupe import find_duplicates, first_eligible
from .inventory import InventoryError, fetch_units
from .validation import validate_package


# ------------------------------------------------------------------------ run

def cmd_run(args, config) -> int:
    log = get_logger()
    ensure_dirs()
    dry = args.dry_run
    if dry:
        log.info("DRY RUN - live inventory is read, but nothing is published and "
                 "no package is promoted.")

    # 1. Read live inventory (site order = highest discount first).
    try:
        units = fetch_units(config, limit=config["selection"]["max_candidates_to_inspect"])
    except InventoryError as exc:
        log.error("Could not read the inventory.\n%s", exc)
        return 2
    if not units:
        log.error("The inventory page returned no priced units. Nothing to do.")
        return 2

    # 2. Pick the first unit that is neither already advertised nor missing a
    #    verifiable photo of itself. A unit whose listing only carries manufacturer
    #    catalog shots is skipped, not substituted - and it is recorded in issues/,
    #    because it needs a real photo uploading before it can ever be advertised.
    unit = None
    skipped: list[str] = []
    photo_blocked: list[tuple[Unit, str]] = []

    for candidate in units:
        label = candidate.identity or candidate.inventory_url
        duplicates = find_duplicates(candidate)
        if duplicates and not args.allow_rerun:
            skipped.append(f"{label}: {duplicates[0]}")
            log.info("  skipped (duplicate): %s", label)
            continue

        if config["selection"].get("require_exact_unit_image", True):
            try:
                image_url, _ = media.choose_exact_unit_image(candidate, config)
                setattr(candidate, "_chosen_image_url", image_url)
            except media.ImageError as exc:
                skipped.append(f"{label}: no exact-unit photo")
                photo_blocked.append((candidate, str(exc)))
                log.info("  skipped (no exact-unit photo): %s", label)
                continue

        unit = candidate
        break

    # Units with no photo of themselves are a dealership problem worth surfacing.
    for blocked_unit, detail in photo_blocked:
        packaging.write_issue_note(
            f"no-exact-photo_{blocked_unit.slug or 'unknown'}",
            [f"Listing: {blocked_unit.inventory_url}", detail,
             "Upload a real photo of this unit to its listing and it becomes "
             "eligible automatically."],
            dry_run=dry)

    if unit is None:
        log.error("None of the %d candidate unit(s) is eligible: %d already "
                  "advertised, %d without a verifiable photo of themselves. "
                  "Raise selection.max_candidates_to_inspect, or use --allow-rerun "
                  "with a documented approval.",
                  len(units), len(skipped) - len(photo_blocked), len(photo_blocked))
        packaging.write_issue_note("no-eligible-unit", skipped, dry_run=dry)
        return 3

    log.info("Selected: %s  |  sale %s  save %s", unit.identity or unit.inventory_url,
             money_str(unit.sale_price), money_str(unit.savings))

    # 3. Build the package. In a dry run this happens in a temp folder that is
    #    deleted afterwards, so the workflow runs fully without leaving state.
    if dry:
        temp_root = Path(tempfile.mkdtemp(prefix="ims-ads-dry-"))
        pkg = temp_root / unit.package_name
        pkg.mkdir(parents=True)
        packaging.write_listing(pkg, unit)
    else:
        try:
            pkg = packaging.create_package(unit, state="inbox")
        except packaging.PackageError as exc:
            log.error("%s", exc)
            return 3

    try:
        return _produce_and_file(args, config, unit, pkg, dry=dry)
    finally:
        if dry:
            shutil.rmtree(pkg.parent, ignore_errors=True)
            log.info("Dry run complete - temporary package discarded.")


def _produce_and_file(args, config, unit: Unit, pkg: Path, *, dry: bool) -> int:
    """Downloads the exact-unit photo, produces or expects the creative, validates,
    then files the package to ready/ or issues/."""
    log = get_logger()
    mode = config["production"]["mode"]
    blockers: list[str] = []

    # 3a. The exact-unit photo. No fallback: a substitute is never acceptable.
    #     Selection already picked and verified the URL, so it is reused here rather
    #     than re-derived - the package must contain the photo that was vetted.
    try:
        image_url = getattr(unit, "_chosen_image_url", "")
        if not image_url:
            image_url, _ = media.choose_exact_unit_image(unit, config)
            setattr(unit, "_chosen_image_url", image_url)
        media.download_image(image_url, pkg / media_stem(), config)
        packaging.write_listing(pkg, unit)
    except media.ImageError as exc:
        blockers.append(str(exc))
        log.error("Exact-unit image unavailable:\n%s", exc)

    # 3b. The creative.
    if mode == "produce" and not blockers:
        try:
            source = packaging.package_files(pkg)["source_image"]
            media.compose_graphic(unit, source, pkg / packaging.GRAPHIC_FILE, config)
        except media.ImageError as exc:
            blockers.append(str(exc))
            log.error("%s", exc)
        try:
            text = copywriting.build_description(unit, config)
            (pkg / packaging.DESCRIPTION_FILE).write_text(text, encoding="utf-8")
        except copywriting.CopyError as exc:
            blockers.append(str(exc))
            log.error("%s", exc)
    elif mode == "validate_only":
        log.info("production.mode is 'validate_only' - expecting graphic.png and "
                 "description.txt to be supplied in the package.")

    # 4. Validate and file.
    report, unit = validate_package(pkg, config, allow_rerun=args.allow_rerun,
                                    check_duplicates=False)  # already checked at selection
    for blocker in blockers:
        report.add("production", False, blocker.splitlines()[0])

    packaging.write_validation(pkg, report.render(), dry_run=dry)
    log.info("\n%s", report.render())

    if dry:
        log.info("DRY RUN: would move %s to %s/", pkg.name,
                 "ready" if report.ok else "issues")
        return 0 if report.ok else 1

    if report.ok:
        packaging.append_approval(pkg, "Validation passed - promoted to ready/")
        packaging.move_package(pkg.name, "ready", reason="validation passed")
        log.info("READY: %s", pkg.name)
        return 0

    reasons = [f"{c.name}: {c.detail}" for c in report.failures]
    packaging.append_approval(pkg, "Validation failed - moved to issues/")
    packaging.move_package(pkg.name, "issues", reason="validation failed")
    packaging.write_issue_note(pkg.name, reasons)
    log.error("BLOCKED: %s - %d failure(s). See issues/.", pkg.name, len(reasons))
    return 1


def media_stem() -> str:
    return packaging.SOURCE_IMAGE_STEM


# ----------------------------------------------------------------------- sync

def cmd_sync(args, config) -> int:
    """The ChatGPT -> Codex -> GitHub -> here path.

    Pulls whatever Codex pushed, ties each supplied package to its own live listing,
    validates it, and files it to ready/ or issues/. Publishes nothing.
    """
    log = get_logger()
    ensure_dirs()

    try:
        intake.git_sync(dry_run=args.dry_run)
    except intake.IntakeError as exc:
        log.error("%s", exc)
        return 2

    supplied = intake.find_supplied_packages()
    if not supplied:
        log.info("No new packages in inbox/ awaiting intake.")
        return 0

    log.info("Taking in %d package(s) from inbox/.", len(supplied))
    worst = 0

    for pkg in supplied:
        log.info("\n=== %s ===", pkg.name)
        try:
            unit = intake.intake_package(pkg, config, dry_run=args.dry_run)
        except intake.IntakeError as exc:
            log.error("%s", exc)
            if not args.dry_run:
                packaging.write_issue_note(pkg.name, [str(exc)])
                packaging.move_package(pkg.name, "issues", reason="intake failed")
            worst = max(worst, 1)
            continue

        if args.dry_run:
            log.info("DRY RUN: resolved %s; skipping validation write.", unit.identity)
            continue

        report, _ = validate_package(pkg, config, allow_rerun=args.allow_rerun)
        packaging.write_validation(pkg, report.render())
        log.info("\n%s", report.render())

        if report.ok:
            packaging.append_approval(pkg, "Validation passed - promoted to ready/")
            packaging.move_package(pkg.name, "ready", reason="validation passed")
            log.info("READY: %s", pkg.name)
        else:
            reasons = [f"{c.name}: {c.detail}" for c in report.failures]
            packaging.append_approval(pkg, "Validation failed - moved to issues/")
            packaging.move_package(pkg.name, "issues", reason="validation failed")
            packaging.write_issue_note(pkg.name, reasons)
            log.error("BLOCKED: %s - %d failure(s). See issues/.", pkg.name, len(reasons))
            worst = max(worst, 1)

    return worst


# ------------------------------------------------------------------- validate

def cmd_validate(args, config) -> int:
    log = get_logger()
    names = [args.name] if args.name else [p.name for p in packaging.list_packages("inbox")]
    if not names:
        log.info("inbox/ is empty.")
        return 0

    worst = 0
    for name in names:
        found = packaging.find_package(name)
        if not found:
            log.error("No package named %s.", name)
            worst = max(worst, 1)
            continue
        _, pkg = found
        report, _ = validate_package(pkg, config, allow_rerun=args.allow_rerun)
        packaging.write_validation(pkg, report.render(), dry_run=args.dry_run)
        log.info("\n=== %s ===\n%s", name, report.render())
        worst = max(worst, 0 if report.ok else 1)
    return worst


# -------------------------------------------------------------------- promote

def cmd_promote(args, config) -> int:
    log = get_logger()
    found = packaging.find_package(args.name)
    if not found:
        log.error("No package named %s.", args.name)
        return 1
    state, pkg = found
    if state != "inbox":
        log.error("%s is in %s/, not inbox/. Only inbox packages are promoted.",
                  args.name, state)
        return 1

    report, _ = validate_package(pkg, config, allow_rerun=args.allow_rerun)
    packaging.write_validation(pkg, report.render(), dry_run=args.dry_run)
    if not report.ok:
        log.error("%s failed validation - not promoted.\n%s", args.name, report.render())
        if not args.dry_run:
            packaging.move_package(args.name, "issues", reason="validation failed")
            packaging.write_issue_note(args.name,
                                       [f"{c.name}: {c.detail}" for c in report.failures])
        return 1

    packaging.move_package(args.name, "ready", dry_run=args.dry_run,
                           reason="validation passed")
    log.info("READY: %s", args.name)
    return 0


# -------------------------------------------------------------------- publish

def cmd_publish(args, config) -> int:
    """Only ready/ may be published, and history is written immediately after."""
    log = get_logger()
    found = packaging.find_package(args.name)
    if not found:
        log.error("No package named %s.", args.name)
        return 1
    state, pkg = found
    if state != "ready":
        log.error("%s is in %s/. Only packages in ready/ may be published.",
                  args.name, state)
        return 1

    # Re-validate at the moment of publishing: the listing may have moved since.
    report, unit = validate_package(pkg, config, check_duplicates=False)
    if not report.ok:
        log.error("%s no longer passes validation - refusing to publish.\n%s",
                  args.name, report.render())
        if not args.dry_run:
            packaging.move_package(args.name, "issues",
                                   reason="failed re-validation at publish time")
        return 1

    files = packaging.package_files(pkg)
    platform = config["publishing"].get("platform", "none")
    post_url = args.post_url or ""

    if platform != "none" and not args.post_url:
        adapter = publishing.get_adapter(config)
        try:
            description = files["description"].read_text(encoding="utf-8")
            result = adapter.publish(unit, files["graphic"], description,
                                     dry_run=args.dry_run)
            post_url = result.post_url
        except publishing.PublishError as exc:
            log.error("Publishing failed:\n%s", exc)
            return 2

    if args.dry_run:
        log.info("DRY RUN: would publish %s via '%s' and record it.", args.name, platform)
        return 0

    packaging.append_approval(pkg, f"Published via '{platform}'"
                                   + (f" -> {post_url}" if post_url else ""))
    moved = packaging.move_package(args.name, "published", reason="published")
    packaging.append_history(
        unit, status="PUBLISHED", platform=platform, post_url=post_url,
        graphic_path=str(moved / packaging.GRAPHIC_FILE),
        description_path=str(moved / packaging.DESCRIPTION_FILE),
        notes=args.note or "")
    log.info("PUBLISHED: %s", args.name)
    return 0


# -------------------------------------------------------------------- archive

def cmd_archive(args, config) -> int:
    log = get_logger()
    found = packaging.find_package(args.name)
    if not found:
        log.error("No package named %s.", args.name)
        return 1
    packaging.move_package(args.name, "archive", dry_run=args.dry_run,
                           reason=args.note or "retired")
    log.info("ARCHIVED: %s", args.name)
    return 0


# --------------------------------------------------------------------- status

def cmd_status(args, config) -> int:
    rows = packaging.read_history()
    print()
    print("STATE".ljust(12) + "COUNT  PACKAGES")
    for state in ("inbox", "ready", "published", "issues", "archive"):
        pkgs = packaging.list_packages(state)
        names = ", ".join(p.name for p in pkgs[:3])
        if len(pkgs) > 3:
            names += f", +{len(pkgs) - 3} more"
        print(f"{state.ljust(12)}{str(len(pkgs)).rjust(5)}  {names}")

    print()
    print(f"ad-history.csv rows: {len(rows)}")
    published = [r for r in rows if (r.get('status') or '').upper() == 'PUBLISHED']
    if published:
        last = published[-1]
        print(f"last published     : {last.get('year','')} {last.get('make','')} "
              f"{last.get('model','')} on {last.get('published_at','')[:10]}")

    ready = packaging.list_packages("ready")
    print()
    if ready:
        print("READY TO POST:")
        for pkg in ready:
            try:
                unit = packaging.read_listing(pkg)
                print(f"  {pkg.name}")
                print(f"    {unit.identity}  |  sale {money_str(unit.sale_price)}  "
                      f"save {money_str(unit.savings)}")
            except Exception:
                print(f"  {pkg.name}  (listing.json unreadable)")
    else:
        print("READY TO POST: nothing")
    print()
    return 0


# ------------------------------------------------------------------- da-login

def cmd_da_login(args, config) -> int:
    """Opens Dealership Accelerator in the persistent profile so you can sign in.

    Nothing is typed for you and no credential is stored in this repo - the session
    lives in a browser profile under your home directory.
    """
    from . import dealership_accelerator as da
    log = get_logger()
    try:
        cfg = da.load_da_config()
        da.open_login_session(cfg)
    except da.DealershipAcceleratorError as exc:
        log.error("%s", exc)
        return 2

    missing = da.unfilled_selectors(da.load_da_config())
    if missing:
        log.warning("Signed in, but these page details are still unset in "
                    "config/dealership-accelerator.json:")
        for item in missing:
            log.warning("  - %s", item)
        log.warning("Publishing stays blocked until they are captured.")
        return 1
    log.info("Dealership Accelerator session saved and fully configured.")
    return 0


# --------------------------------------------------------------------- doctor

def cmd_doctor(args, config) -> int:
    """Everything still needed to run the workflow end to end."""
    print()
    print("=== Environment ===")
    try:
        import playwright  # noqa: F401
        print("  [ok]   playwright installed")
    except ImportError:
        print("  [MISS] playwright - run: python -m pip install -r requirements.txt")
    try:
        import PIL  # noqa: F401
        print("  [ok]   Pillow installed")
    except ImportError:
        print("  [MISS] Pillow - run: python -m pip install -r requirements.txt")
    try:
        import requests  # noqa: F401
        print("  [ok]   requests installed")
    except ImportError:
        print("  [MISS] requests - run: python -m pip install -r requirements.txt")

    print()
    print("=== Branding assets ===")
    from .core import REPO_ROOT
    logo = REPO_ROOT / config["graphic"]["logo_path"]
    mode = config["production"]["mode"]
    if logo.exists():
        print(f"  [ok]   {logo.relative_to(REPO_ROOT)}")
    elif mode == "produce":
        print(f"  [MISS] {logo.relative_to(REPO_ROOT)} - required because "
              f"production.mode is 'produce'.")
        print("         Supply a transparent PNG of the IM logo, about 1000px wide.")
    else:
        print(f"  [n/a]  {logo.relative_to(REPO_ROOT)} not needed while "
              f"production.mode is 'validate_only'.")

    print()
    print("=== Production mode ===")
    print(f"  production.mode = '{mode}'")
    if mode == "validate_only":
        print("    ChatGPT supplies graphic.png and description.txt in the package;")
        print("    this system verifies and promotes them.")
    else:
        print("    This system builds the graphic and description from the listing.")

    print()
    print("=== Publishing ===")
    for line in publishing.readiness_report(config):
        print(f"  {line}")

    print()
    return 0


# ------------------------------------------------------------------------ cli

def build_parser() -> argparse.ArgumentParser:
    # The shared flags live on a parent parser so they work either side of the
    # subcommand: "run --dry-run" and "--dry-run run" both parse.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dry-run", action="store_true",
                        help="complete the workflow without publishing or "
                             "leaving permanent state")
    common.add_argument("--verbose", "-v", action="store_true")
    common.add_argument("--allow-rerun", action="store_true",
                        help="permit a documented duplicate re-run")

    parser = argparse.ArgumentParser(
        prog="ims-ads", parents=[common],
        description="International Motorsports daily social-ad workflow.")

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", parents=[common],
                   help="build today's package from live inventory")
    sub.add_parser("sync", parents=[common],
                   help="pull packages pushed by Codex, verify and file them")

    p_val = sub.add_parser("validate", parents=[common], help="re-check inbox packages")
    p_val.add_argument("name", nargs="?")

    p_pro = sub.add_parser("promote", parents=[common], help="inbox -> ready")
    p_pro.add_argument("name")

    p_pub = sub.add_parser("publish", parents=[common], help="ready -> published")
    p_pub.add_argument("name")
    p_pub.add_argument("--post-url", default="", help="URL of the live post")
    p_pub.add_argument("--note", default="")

    p_arc = sub.add_parser("archive", parents=[common], help="retire a package")
    p_arc.add_argument("name")
    p_arc.add_argument("--note", default="")

    sub.add_parser("status", parents=[common], help="what is in each state")
    sub.add_parser("da-login", parents=[common],
                   help="sign in to Dealership Accelerator once, by hand")
    sub.add_parser("doctor", parents=[common], help="what is still missing")
    return parser


COMMANDS = {
    "run": cmd_run, "sync": cmd_sync, "validate": cmd_validate, "promote": cmd_promote,
    "publish": cmd_publish, "archive": cmd_archive, "status": cmd_status,
    "da-login": cmd_da_login, "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    for attr, default in (("name", None), ("post_url", ""), ("note", "")):
        if not hasattr(args, attr):
            setattr(args, attr, default)

    run_id = dt.datetime.now().strftime("%H%M%S")
    setup_logging(verbose=args.verbose, run_id=run_id)
    load_dotenv()

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        return COMMANDS[args.command](args, config)
    except KeyboardInterrupt:
        get_logger().error("Interrupted.")
        return 130
    except Exception as exc:  # last resort - the log must always show the cause
        get_logger().exception("Unhandled error: %s", exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
