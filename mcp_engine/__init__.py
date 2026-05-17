"""Test-only namespace bridge to the external HippoCampy mcp_engine package.

ARC_AGI should not own memory internals. This package exists only so legacy
submission-package tests can import the sibling HippoCampy implementation when
ARC_AGI is checked out as a separate repo.
"""

from __future__ import annotations

from pathlib import Path

_CANDIDATE_ROOTS = [
    Path(__file__).resolve().parents[1].parent / "sidequests-brain" / "mcp_engine",
    Path(__file__).resolve().parents[1].parent / "hippocampy" / "mcp_engine",
    Path("/Users/djshelton/Desktop/GitProjects/sidequests-brain/mcp_engine"),
    Path("/Users/djshelton/Desktop/GitProjects/hippocampy/mcp_engine"),
]

for _path in _CANDIDATE_ROOTS:
    if _path.exists():
        __path__.append(str(_path))
