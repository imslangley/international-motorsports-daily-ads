"""Publishing adapters.

No platform is wired yet, deliberately: the platform has not been chosen and no
credentials have been supplied. Rather than guess, each adapter reports exactly what
it needs. `platform = "none"` is fully supported - the workflow stops at ready/ and
you record the publish yourself with `ims-ads publish <package> --post-url ...`.

To add a platform: implement `publish()` on a new Adapter, register it in ADAPTERS,
and list its environment variable names in .env.example. Nothing else changes.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .core import Unit, get_logger, require_env


class PublishError(RuntimeError):
    """Raised when publishing cannot proceed."""


@dataclass
class PublishResult:
    platform: str
    post_url: str = ""
    detail: str = ""


class Adapter:
    name = "base"
    required_env: tuple[str, ...] = ()

    def missing_credentials(self) -> list[str]:
        return require_env(*self.required_env)

    def publish(self, unit: Unit, graphic: Path, description: str,
                *, dry_run: bool = False) -> PublishResult:
        raise NotImplementedError


class ManualAdapter(Adapter):
    """The default. Nothing is posted automatically; you post and then record it."""
    name = "none"

    def publish(self, unit, graphic, description, *, dry_run=False) -> PublishResult:
        raise PublishError(
            "publishing.platform is 'none', so this system does not post.\n"
            "Post the package from ready/ yourself, then record it with:\n"
            "  python -m ims_ads publish <package-name> --post-url <url>\n"
            "To automate publishing, choose a platform in config/config.json and "
            "supply its credentials in .env (see .env.example)."
        )


class GoHighLevelAdapter(Adapter):
    """GHL / LeadConnector Social Planner - posts to FB, IG, LinkedIn and GBP."""
    name = "gohighlevel"
    required_env = ("GHL_PRIVATE_INTEGRATION_TOKEN", "GHL_LOCATION_ID")

    def publish(self, unit, graphic, description, *, dry_run=False) -> PublishResult:
        missing = self.missing_credentials()
        if missing:
            raise PublishError(
                f"GoHighLevel publishing needs: {', '.join(missing)}.\n"
                f"Create a Private Integration token with the scopes "
                f"social-media-posting.write and medias.write, then put it in .env."
            )
        raise PublishError(
            "The GoHighLevel adapter is not implemented yet.\n"
            "Credentials are present but the endpoint shapes have never been "
            "exercised against your sub-account, so writing untested publishing code "
            "would risk posting something wrong. Say the word and it is a short job."
        )


class MetaAdapter(Adapter):
    """Facebook Page + Instagram Business via the Graph API."""
    name = "meta"
    required_env = ("META_PAGE_ID", "META_IG_USER_ID", "META_ACCESS_TOKEN")

    def publish(self, unit, graphic, description, *, dry_run=False) -> PublishResult:
        missing = self.missing_credentials()
        if missing:
            raise PublishError(f"Meta publishing needs: {', '.join(missing)}.")
        raise PublishError("The Meta adapter is not implemented yet.")


ADAPTERS: dict[str, type[Adapter]] = {
    "none": ManualAdapter,
    "gohighlevel": GoHighLevelAdapter,
    "meta": MetaAdapter,
}


def get_adapter(config: dict) -> Adapter:
    name = config["publishing"].get("platform", "none")
    if name not in ADAPTERS:
        raise PublishError(
            f"Unknown publishing.platform '{name}'. "
            f"Available: {', '.join(ADAPTERS)}"
        )
    return ADAPTERS[name]()


def readiness_report(config: dict) -> list[str]:
    """What is still missing before automatic publishing can work."""
    lines: list[str] = []
    platform = config["publishing"].get("platform", "none")
    if platform == "none":
        lines.append("No publishing platform chosen (publishing.platform = 'none'). "
                     "Publishing is manual; the workflow stops at ready/.")
        return lines
    adapter = get_adapter(config)
    missing = adapter.missing_credentials()
    if missing:
        lines.append(f"Platform '{platform}' is selected but these environment "
                     f"variables are unset: {', '.join(missing)}")
    else:
        lines.append(f"Platform '{platform}' has its credentials, but the adapter "
                     f"is not implemented yet.")
    return lines
