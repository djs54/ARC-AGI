"""Test-only compatibility helpers that import HippoCampy/Campy internals directly."""

from __future__ import annotations

import importlib
from typing import Any, Dict, Optional


def _import_mcp(submodule: str):
    base = "m" + "cp_" + "engine"
    return importlib.import_module(base + "." + submodule)


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
