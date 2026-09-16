"""Shared foundations: paths, configuration, logging, and the data model.

Nothing in this package ever invents inventory data. Every field on a Unit is
either read from the live listing or left empty, and an empty required field is a
validation failure rather than something to fill in.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any

# repo_root/src/ims_ads/core.py -> repo_root
REPO_ROOT = Path(__file__).resolve().parents[2]

INBOX = REPO_ROOT / "inbox"
READY = REPO_ROOT / "ready"
PUBLISHED = REPO_ROOT / "published"
ISSUES = REPO_ROOT / "issues"
ARCHIVE = REPO_ROOT / "archive"
LOGS = REPO_ROOT / "logs"
ASSETS = REPO_ROOT / "assets"

CONFIG_PATH = REPO_ROOT / "config" / "config.json"
HISTORY_PATH = REPO_ROOT / "ad-history.csv"

STATE_DIRS = {
    "inbox": INBOX,
    "ready": READY,
    "published": PUBLISHED,
    "issues": ISSUES,
    "archive": ARCHIVE,
}

# The header that already exists in ad-history.csv. Order is preserved on write.
HISTORY_COLUMNS = [
    "published_at", "status", "stock_number", "vin", "year", "make", "model",
    "trim", "color", "inventory_url", "graphic_path", "description_path",
    "platform", "post_url", "notes",
]


# --------------------------------------------------------------------- config

class ConfigError(RuntimeError):
    """Raised when configuration is missing or unusable."""


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_PATH
    if not path.exists():
        raise ConfigError(
            f"Missing configuration file: {path}\n"
            f"Copy config/config.json from the repository, or run: git checkout {path.name}"
        )
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc


def load_dotenv(path: Path | None = None) -> dict[str, str]:
    """Reads .env into the process environment without overwriting real env vars.

    Deliberately minimal - no dependency, no export syntax, no interpolation.
    """
    path = path or (REPO_ROOT / ".env")
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
        if key:
            loaded[key] = value
    return loaded


def require_env(*names: str) -> list[str]:
    """Returns the names that are missing, so the caller can report all at once."""
    return [n for n in names if not os.environ.get(n)]


# -------------------------------------------------------------------- logging

def setup_logging(verbose: bool = False, run_id: str | None = None) -> logging.Logger:
    """Console plus a dated file under logs/. The file is the audit trail."""
    LOGS.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("ims_ads")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(console)

    log_file = LOGS / f"{dt.date.today():%Y-%m-%d}.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-7s " + (f"[{run_id}] " if run_id else "") + "%(message)s"
    ))
    logger.addHandler(handler)
    logger.debug("logging to %s", log_file)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("ims_ads")


# ----------------------------------------------------------------- data model

def slugify(value: str, keep_case: bool = False) -> str:
    """'MV Agusta LXP Orioli' -> 'mv-agusta-lxp-orioli'."""
    text = re.sub(r"[^\w\s-]", "", (value or "").strip())
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text if keep_case else text.lower()


def parse_money(value: str | None) -> float | None:
    """'On Sale $13,995.00' -> 13995.0. Returns None when there is no figure,
    which is treated as missing data, never as zero."""
    if not value:
        return None
    match = re.search(r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?|\d+(?:\.\d{1,2})?)", value)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def money_str(value: float | None) -> str:
    return "" if value is None else f"${value:,.2f}".replace(".00", "")


@dataclasses.dataclass
class Unit:
    """One inventory unit, exactly as read from the live listing."""
    inventory_url: str = ""
    year: str = ""
    make: str = ""
    model: str = ""
    trim: str = ""
    color: str = ""
    stock_number: str = ""
    vin: str = ""
    sale_price: float | None = None
    regular_price: float | None = None
    savings: float | None = None
    # schema.org availability from the listing's JSON-LD, e.g.
    # "https://schema.org/InStock". Empty when the listing published none.
    availability: str = ""
    image_urls: list[str] = dataclasses.field(default_factory=list)
    title_raw: str = ""
    scraped_at: str = ""

    @property
    def identity(self) -> str:
        """Human label: '2024 MV Agusta LXP Orioli'."""
        return " ".join(p for p in (self.year, self.make, self.model, self.trim) if p)

    @property
    def slug(self) -> str:
        return slugify(self.identity)

    @property
    def unit_key(self) -> str:
        """Normalised year/make/model/trim key for duplicate detection."""
        return re.sub(r"[^a-z0-9]", "", self.identity.lower())

    @property
    def package_name(self) -> str:
        """YYYY-MM-DD_stock-number_year-make-model-trim, per the spec."""
        date = (self.scraped_at[:10] or f"{dt.date.today():%Y-%m-%d}")
        stock = slugify(self.stock_number or self.vin or "no-stock")
        return f"{date}_{stock}_{self.slug}"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Unit":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in fields})


@dataclasses.dataclass
class CheckResult:
    """One validation outcome. `blocking` decides issues/ versus a note."""
    name: str
    passed: bool
    detail: str = ""
    blocking: bool = True

    def __str__(self) -> str:
        mark = "PASS" if self.passed else ("FAIL" if self.blocking else "WARN")
        return f"[{mark}] {self.name}" + (f" - {self.detail}" if self.detail else "")


@dataclasses.dataclass
class ValidationReport:
    checks: list[CheckResult] = dataclasses.field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str = "", blocking: bool = True) -> None:
        self.checks.append(CheckResult(name, passed, detail, blocking))

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and c.blocking]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed and not c.blocking]

    @property
    def ok(self) -> bool:
        return not self.failures

    def render(self) -> str:
        lines = [str(c) for c in self.checks]
        lines.append("")
        lines.append("RESULT: " + ("PASSED" if self.ok else "FAILED"))
        if self.failures:
            lines.append(f"Blocking failures: {len(self.failures)}")
        if self.warnings:
            lines.append(f"Advisory warnings: {len(self.warnings)}")
        return "\n".join(lines)


def utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def ensure_dirs() -> None:
    for path in (*STATE_DIRS.values(), LOGS, ASSETS / "brand"):
        path.mkdir(parents=True, exist_ok=True)
