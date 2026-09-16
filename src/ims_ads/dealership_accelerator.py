"""Dealership Accelerator publishing.

DA (LeadVenture) exposes no public posting API, so this drives the real UI with
Playwright. Two consequences worth understanding before relying on it:

  * It needs a logged-in browser session. Credentials are never handled here and are
    never stored in this repo. You sign in once, by hand, into a persistent Chrome
    profile; the adapter reuses that profile afterwards.
  * It depends on DA's page structure, which LeadVenture can change without notice.
    Every selector therefore lives in config/dealership-accelerator.json, so a break
    is a config edit rather than a code change - and the adapter refuses to run on
    placeholder selectors rather than clicking something it cannot identify.

Set up the session once:

    python ims-ads.py da-login

That opens the profile browser. Sign in, confirm you can reach the post composer,
then close the window. The session persists for future runs.
"""
from __future__ import annotations

import json
from pathlib import Path

from .core import REPO_ROOT, Unit, get_logger

CONFIG_FILE = REPO_ROOT / "config" / "dealership-accelerator.json"

# Profile lives outside the repo: it holds live session cookies and must never be
# committed. .gitignore covers the repo; this keeps it out of the tree entirely.
DEFAULT_PROFILE_DIR = Path.home() / ".ims-ads" / "da-profile"

PLACEHOLDER = "FILL-ME"


class DealershipAcceleratorError(RuntimeError):
    """Raised when DA cannot be driven safely."""


def load_da_config() -> dict:
    if not CONFIG_FILE.exists():
        raise DealershipAcceleratorError(
            f"Missing {CONFIG_FILE.relative_to(REPO_ROOT)}.\n"
            f"It carries the DA URLs and selectors. Restore it from git."
        )
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DealershipAcceleratorError(f"{CONFIG_FILE} is not valid JSON: {exc}") from exc


def unfilled_selectors(cfg: dict) -> list[str]:
    """Every selector still set to FILL-ME, so all gaps are reported at once."""
    missing = []

    def walk(node, path=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key.startswith("_"):
                    continue
                walk(value, f"{path}.{key}" if path else key)
        elif isinstance(node, str) and node.strip() == PLACEHOLDER:
            missing.append(path)

    walk(cfg)
    return missing


def profile_dir(cfg: dict) -> Path:
    raw = (cfg.get("session") or {}).get("profile_dir") or ""
    path = Path(raw).expanduser() if raw else DEFAULT_PROFILE_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def open_login_session(cfg: dict | None = None) -> None:
    """Opens the persistent-profile browser so a human can sign in once.

    This function never types a credential. It opens the window and waits; the
    person signs in themselves.
    """
    from playwright.sync_api import sync_playwright

    log = get_logger()
    cfg = cfg or load_da_config()
    profile = profile_dir(cfg)
    url = (cfg.get("urls") or {}).get("login") or (cfg.get("urls") or {}).get("post_composer")

    if not url or url == PLACEHOLDER:
        raise DealershipAcceleratorError(
            "urls.login is not set in config/dealership-accelerator.json.\n"
            "Put the Dealership Accelerator sign-in URL there first."
        )

    log.info("Opening Dealership Accelerator in the persistent profile at %s", profile)
    log.info("Sign in yourself, confirm you can reach the post composer, then close "
             "the window. Nothing is typed for you and no credential is stored here.")

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile), headless=False, channel="chrome",
            viewport={"width": 1440, "height": 950})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        log.info("Waiting for you to close the browser window...")
        try:
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        ctx.close()
    log.info("Session saved. Future runs will reuse it.")


def session_is_live(cfg: dict) -> bool:
    """True when the saved profile still reaches the composer without a login wall."""
    from playwright.sync_api import sync_playwright

    log = get_logger()
    urls = cfg.get("urls") or {}
    composer = urls.get("post_composer")
    if not composer or composer == PLACEHOLDER:
        return False

    signed_out = (cfg.get("selectors") or {}).get("signed_out_marker")
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile_dir(cfg)), headless=False, channel="chrome",
            viewport={"width": 1440, "height": 950})
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(composer, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(4000)
            if signed_out and signed_out != PLACEHOLDER:
                if page.query_selector(signed_out):
                    log.warning("Dealership Accelerator is showing a signed-out page.")
                    return False
            return True
        finally:
            ctx.close()


def publish(unit: Unit, graphic: Path, description: str, *,
            dry_run: bool = False) -> str:
    """Posts one package through the DA UI. Returns the post URL when DA gives one.

    Refuses to run while any selector is still FILL-ME. Clicking an unverified
    control in a live marketing tool is exactly the kind of guess that publishes
    the wrong thing.
    """
    from playwright.sync_api import sync_playwright

    log = get_logger()
    cfg = load_da_config()

    missing = unfilled_selectors(cfg)
    if missing:
        raise DealershipAcceleratorError(
            "Dealership Accelerator is selected, but its page details have never "
            "been captured, so there is nothing safe to click.\n\n"
            "Still unset in config/dealership-accelerator.json:\n"
            + "\n".join(f"  - {m}" for m in missing)
            + "\n\nRun `python ims-ads.py da-login`, sign in, and the selectors can "
              "be read off the real page. Until then publishing.platform should stay "
              "'none' and posts go out by hand from ready/."
        )

    urls = cfg["urls"]
    sel = cfg["selectors"]
    timeout = int(cfg.get("timeouts", {}).get("action_ms", 30000))

    if dry_run:
        log.info("DRY RUN: would post %s to Dealership Accelerator (%s)",
                 unit.identity, urls["post_composer"])
        return ""

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile_dir(cfg)), headless=False, channel="chrome",
            viewport={"width": 1440, "height": 950})
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(urls["post_composer"], wait_until="domcontentloaded",
                      timeout=int(cfg.get("timeouts", {}).get("nav_ms", 90000)))
            page.wait_for_timeout(3000)

            if sel.get("signed_out_marker") and page.query_selector(sel["signed_out_marker"]):
                raise DealershipAcceleratorError(
                    "The saved Dealership Accelerator session has expired.\n"
                    "Run `python ims-ads.py da-login` and sign in again."
                )

            log.info("Filling the DA composer for %s", unit.identity)
            page.wait_for_selector(sel["caption_field"], timeout=timeout)
            page.fill(sel["caption_field"], description)

            page.set_input_files(sel["image_input"], str(graphic))
            page.wait_for_timeout(int(cfg.get("timeouts", {}).get("upload_ms", 15000)))

            # The submit control is clicked last and only once.
            page.click(sel["submit_button"], timeout=timeout)
            page.wait_for_timeout(int(cfg.get("timeouts", {}).get("confirm_ms", 10000)))

            if sel.get("success_marker"):
                page.wait_for_selector(sel["success_marker"], timeout=timeout)

            post_url = ""
            if sel.get("post_link"):
                node = page.query_selector(sel["post_link"])
                if node:
                    post_url = node.get_attribute("href") or ""
            log.info("Posted to Dealership Accelerator%s",
                     f" -> {post_url}" if post_url else "")
            return post_url
        finally:
            ctx.close()
