"""Test fixtures.

Every test runs against a temporary tree. The real inbox/, ready/, published/,
issues/, archive/ and ad-history.csv are never touched, so the suite is safe to run
on the live machine at any time.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ims_ads import core, dedupe, packaging
from ims_ads.core import Unit


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Redirects every state folder and the history file into tmp_path."""
    dirs = {name: tmp_path / name
            for name in ("inbox", "ready", "published", "issues", "archive")}
    for path in dirs.values():
        path.mkdir(parents=True)
    logs = tmp_path / "logs"
    logs.mkdir()
    history = tmp_path / "ad-history.csv"
    history.write_text(",".join(core.HISTORY_COLUMNS) + "\n", encoding="utf-8")

    # The modules import these names directly, so each binding is patched.
    for module in (core, packaging, dedupe):
        if hasattr(module, "STATE_DIRS"):
            monkeypatch.setattr(module, "STATE_DIRS", dirs, raising=False)
    for name, path in dirs.items():
        monkeypatch.setattr(core, name.upper(), path, raising=False)
        monkeypatch.setattr(packaging, name.upper(), path, raising=False)
    monkeypatch.setattr(core, "HISTORY_PATH", history, raising=False)
    monkeypatch.setattr(packaging, "HISTORY_PATH", history, raising=False)
    monkeypatch.setattr(core, "LOGS", logs, raising=False)

    core.setup_logging()
    return tmp_path


@pytest.fixture
def config():
    return json.loads((core.REPO_ROOT / "config" / "config.json").read_text(encoding="utf-8"))


def make_unit(**overrides) -> Unit:
    """A complete, valid unit. Tests override single fields to break one thing."""
    data = dict(
        inventory_url="https://www.internationalmotorsports.com/inventory/"
                      "2024-mv-agusta-lxp-langley-bc-v1m-4c2-12266444i",
        year="2024", make="MV Agusta", model="LXP", trim="Orioli", color="WHITE",
        stock_number="2024 LXP ORIOLI", vin="ZDM1YBJS6GB012116",
        sale_price=19998.0, regular_price=34998.0, savings=15000.0,
        image_urls=["https://cdnmedia.endeavorsuite.com/images/organizations/"
                    "00c07879/inventory/12266444/2024-mv-agusta-lxp-orioli-first.jpg"],
        title_raw="2024 LXP MV Agusta",
        scraped_at=core.utcnow_iso(),
    )
    data.update(overrides)
    return Unit(**data)


@pytest.fixture
def unit():
    return make_unit()


@pytest.fixture
def square_png(tmp_path):
    """A real 1080x1080 PNG."""
    from PIL import Image
    path = tmp_path / "graphic-1080.png"
    Image.new("RGB", (1080, 1080), "white").save(path)
    return path


@pytest.fixture
def wrong_size_png(tmp_path):
    from PIL import Image
    path = tmp_path / "graphic-1200.png"
    Image.new("RGB", (1200, 1200), "white").save(path)
    return path
