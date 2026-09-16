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


# Everything the post flow actually needs. success_marker and post_link are
# deliberately excluded: confirming them means publishing a real post, which has not
# been done, so they stay empty rather than being guessed at.
REQUIRED_PATHS = (
    "urls.login",
    "urls.post_composer",
    "selectors.account_picker",
    "selectors.caption_field",
    "selectors.image_input",
    "selectors.submit_button",
)


def unfilled_selectors(cfg: dict) -> list[str]:
    """Required settings that are still FILL-ME or empty, reported all at once."""
    missing = []
    for path in REQUIRED_PATHS:
        node: object = cfg
        for part in path.split("."):
            node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, str) or not node.strip() or node.strip() == PLACEHOLDER:
            missing.append(path)
    return missing


def is_armed(cfg: dict) -> bool:
    """Posting is refused until a supervised test post has been done and this is set."""
    return bool(cfg.get("armed"))


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
    """Posts one package through the DA (GoHighLevel) Social Planner UI.

    The order below is not arbitrary. The caption editor is a Quill instance that
    ships disabled (`ql-disabled`, contenteditable="false") and the Post button ships
    disabled; both unlock only after a social account is chosen. A script that types
    first writes nothing at all, and does it silently.
    """
    from playwright.sync_api import sync_playwright

    log = get_logger()
    cfg = load_da_config()

    missing = unfilled_selectors(cfg)
    if missing:
        raise DealershipAcceleratorError(
            "Dealership Accelerator page details are incomplete, so there is nothing "
            "safe to click.\n"
            + "\n".join(f"  - {m}" for m in missing)
            + "\n\nRun `python ims-ads.py da-login` and capture them from the composer."
        )

    if not is_armed(cfg):
        raise DealershipAcceleratorError(
            "Dealership Accelerator publishing is NOT ARMED, so nothing was posted.\n\n"
            "The selectors were read off the live composer, but a post has never been "
            "put through this path end to end - verifying that means publishing a real "
            "ad to your accounts. So it refuses rather than assuming it works.\n\n"
            "To go live:\n"
            "  1. Post one package from ready/ by hand, watching each step.\n"
            '  2. Set "armed": true in config/dealership-accelerator.json.\n'
            "  3. Fill selectors.success_marker with something that appears only after "
            "a successful post, so a failure is detected rather than assumed.\n\n"
            "Better still: ask Envoke Digital to enable Private Integrations on "
            f"location {cfg.get('location_id', '')} and switch publishing.platform to "
            "'gohighlevel'. Dealership Accelerator IS GoHighLevel, and the API needs "
            "no browser at all."
        )

    urls, sel = cfg["urls"], cfg["selectors"]
    t = cfg.get("timeouts", {})

    if dry_run:
        log.info("DRY RUN: would post %s via the DA composer (%s)",
                 unit.identity, urls["post_composer"])
        return ""

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(profile_dir(cfg)), headless=False, channel="chrome",
            viewport={"width": 1440, "height": 950})
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(urls["post_composer"], wait_until="domcontentloaded",
                      timeout=int(t.get("nav_ms", 90000)))
            page.wait_for_timeout(5000)

            if sel.get("signed_out_marker") and page.query_selector(sel["signed_out_marker"]):
                raise DealershipAcceleratorError(
                    "The saved Dealership Accelerator session has expired.\n"
                    "Run `python ims-ads.py da-login` and sign in again."
                )

            # 1. Choose the social accounts. This is what unlocks everything else.
            targets = cfg.get("accounts", {}).get("targets") or []
            if not targets:
                raise DealershipAcceleratorError(
                    "accounts.targets is empty in config/dealership-accelerator.json, "
                    "so no social account would be selected - and with none selected "
                    "the caption editor stays disabled and the post would be empty.\n\n"
                    "List the accounts to post to, exactly as they appear in the "
                    "composer's 'Select a social account' picker, for example:\n"
                    '  "targets": ["International Motorsports", "@intlmotorsports"]'
                )

            is_group = bool(cfg.get("accounts", {}).get("is_group"))
            log.info("Selecting %s: %s",
                     "group" if is_group else "account(s)", ", ".join(targets))
            page.click(sel["account_picker"], timeout=int(t.get("action_ms", 30000)))
            page.wait_for_timeout(1500)

            for name in targets:
                try:
                    # A group sits under the GROUPS heading, above ALL ACCOUNTS, and
                    # ticking it selects every account inside - so the post follows
                    # whatever the group contains rather than a list that goes stale.
                    page.get_by_text(name, exact=True).first.click(
                        timeout=int(t.get("action_ms", 30000)))
                except Exception as exc:
                    raise DealershipAcceleratorError(
                        f"No {'group' if is_group else 'account'} named '{name}' in "
                        f"the picker. Check accounts.targets against the names shown "
                        f"in the composer's 'Select a social account' list."
                    ) from exc
                page.wait_for_timeout(700)
            page.keyboard.press("Escape")

            # The Claude/Codex group contains two Google Business Profiles, and a GBP
            # post has a REQUIRED 'Type' field. That dropdown has never been opened,
            # so it is not filled here - submitting with it unset will fail, which is
            # one of the things the first supervised post needs to sort out.
            if cfg.get("_google_business_profile_options"):
                log.warning("Google Business Profile options are not automated. If the "
                            "post is rejected for a missing 'Type', capture that "
                            "dropdown and add it to the config.")

            # 2. Wait for Quill to actually enable before typing into it.
            check = sel.get("caption_enabled_check") or sel["caption_field"]
            try:
                page.wait_for_selector(check, state="attached",
                                       timeout=int(t.get("editor_enable_ms", 20000)))
            except Exception as exc:
                raise DealershipAcceleratorError(
                    "The caption editor never enabled, which means no social account "
                    "was actually selected. Check accounts.targets in "
                    "config/dealership-accelerator.json against the names in the picker."
                ) from exc

            # 3. Real keystrokes: Quill keeps its own document model and ignores
            #    direct DOM writes, so fill()/innerText would leave the post empty.
            log.info("Typing the caption (%d chars)", len(description))
            page.click(sel["caption_field"], timeout=int(t.get("action_ms", 30000)))
            page.type(sel["caption_field"], description, delay=1)

            # 4. The graphic. The file input is hidden; set_input_files handles that.
            log.info("Uploading %s", graphic.name)
            page.set_input_files(sel["image_input"], str(graphic))
            page.wait_for_timeout(int(t.get("upload_ms", 30000)))

            # 5. Post, once.
            page.click(sel["submit_button"], timeout=int(t.get("action_ms", 30000)))
            page.wait_for_timeout(int(t.get("confirm_ms", 10000)))

            if sel.get("success_marker"):
                page.wait_for_selector(sel["success_marker"],
                                       timeout=int(t.get("action_ms", 30000)))
            else:
                log.warning("No success_marker configured - the post was submitted but "
                            "success could not be confirmed. Check the Planner.")

            post_url = ""
            if sel.get("post_link"):
                node = page.query_selector(sel["post_link"])
                if node:
                    post_url = node.get_attribute("href") or ""
            log.info("Submitted to Dealership Accelerator%s",
                     f" -> {post_url}" if post_url else "")
            return post_url
        finally:
            ctx.close()
