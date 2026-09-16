#!/usr/bin/env python3
"""Launcher so the workflow runs without installing the package.

    python ims-ads.py run --dry-run
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "src"))

from ims_ads.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
