"""Test-only compatibility helpers that import HippoCampy/Campy internals directly."""

from __future__ import annotations

import importlib
from typing import Any, Dict, Optional


# B248: mcp_engine.* was renamed to campy.brain.brainstem.* /
# campy.brain.hippocampus.graph.* by an unrelated hippocampy-side
# codebase-anatomy refactor; mcp_engine no longer exists there. Updated to
# the current module paths. Plain string literals here are fine —
# tests/test_import_boundary.py's `import campy`/`from campy import` ban
# does not match dict values, and this directory
# (sidequest_mcp_client/test_compat/) is an explicit carve-out in that
# test anyway.
_MODULE_MAP = {
    "config": "campy.brain.brainstem.config",
    "graph.kuzu_client": "campy.brain.hippocampus.graph.kuzu_client",
    "schema": "campy.brain.hippocampus.schema",
}


def _import_mcp(submodule: str):
    return importlib.import_module(_MODULE_MAP[submodule])


def load_config(path: Optional[str] = None) -> Dict[str, Any]:
    mod = _import_mcp("config")
    loader = getattr(mod, "load_config")
    return loader(path) if path is not None else loader()


def KuzuClient(path: str):
    mod = _import_mcp("graph.kuzu_client")
    return getattr(mod, "KuzuClient")(path)


def init_schema(db: Any, seed_path: str, embedding_model: str):
    mod = _import_mcp("schema")
    return getattr(mod, "init_schema")(db, seed_path, embedding_model)
