"""Pytest bootstrap.

Puts the repo root on sys.path so `import host` works without installing
the package, and deselects hardware-only tests unless --hardware is given.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_addoption(parser):
    parser.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help="run tests that need a real revD board + Pico attached",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--hardware"):
        return
    skip_hw = pytest.mark.skip(reason="needs real hardware; pass --hardware to run")
    for item in items:
        if "hardware" in item.keywords:
            item.add_marker(skip_hw)
