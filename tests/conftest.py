from __future__ import annotations

import os

import pytest


def pytest_collection_modifyitems(config, items):
    del config
    skip_mcp = pytest.mark.skip(reason="requires CAMPY_MCP_CMD (live MCP daemon)")
    skip_api = pytest.mark.skip(reason="requires ARC_API_KEY (live ARC API)")

    for item in items:
        if "requires_mcp" in item.keywords and not os.environ.get("CAMPY_MCP_CMD"):
            item.add_marker(skip_mcp)
        if "requires_arc_api" in item.keywords and not os.environ.get("ARC_API_KEY"):
            item.add_marker(skip_api)
