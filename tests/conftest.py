import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def pytest_collection_modifyitems(config, items):
    """Integration tests need a live warehouse; they run only when RUN_INTEGRATION=1."""
    if os.getenv("RUN_INTEGRATION") == "1":
        return
    skip = pytest.mark.skip(reason="set RUN_INTEGRATION=1 to run against a live database")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)
