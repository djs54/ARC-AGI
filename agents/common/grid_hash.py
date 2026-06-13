"""Deterministic grid hashing shared across ARC runtimes."""

from __future__ import annotations

import hashlib
from typing import Any


def hash_grid(grid: Any) -> str:
    if not grid:
        return "empty"
    flat = str(grid)
    return hashlib.sha256(flat.encode()).hexdigest()[:16]
