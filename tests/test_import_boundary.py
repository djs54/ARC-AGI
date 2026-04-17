import sys
from pathlib import Path

# Ensure repo root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import re

ROOT = Path(__file__).resolve().parents[1]
PROD_PATHS = [ROOT / "benchmarks", ROOT / "run_single_puzzle.py"]

BAD_PATTERNS = [
    "mcp_engine.config",
    "mcp_engine.schema",
    "mcp_engine.graph.kuzu_client",
    "mcp_engine.loop.",
]


def _gather_files(paths):
    files = []
    for p in paths:
        if p.is_file():
            files.append(p)
        else:
            for f in sorted(p.rglob("*.py")):
                files.append(f)
    return files


def test_no_direct_bootstrap_imports_in_production_paths():
    files = _gather_files(PROD_PATHS)
    offending = []
    for f in files:
        try:
            text = f.read_text()
        except Exception:
            continue
        for pat in BAD_PATTERNS:
            if pat in text:
                offending.append((str(f.relative_to(ROOT)), pat))

    assert not offending, f"Found direct SideQuests bootstrap imports in production files: {offending}"
