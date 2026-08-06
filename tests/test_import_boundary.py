import sys
from pathlib import Path

# Ensure repo root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import re

ROOT = Path(__file__).resolve().parents[1]

# A030: the MCP stdio seam policy applies to the interactive runtime only.
# `benchmarks/arc3/` is offline scoring / submission packaging that embeds
# the brain directly (submission-pack deployment model) and is intentionally
# exempt from this guard. Do not re-add `benchmarks` here without a card.
PROD_PATHS = [
    ROOT / "agents",
    ROOT / "run_single_puzzle.py",
    ROOT / "sidequest_mcp_client",
]

# Use regex patterns so we don't false-positive on the local MCP client package name.
BAD_REGEXES = [
    r"\bmcp_engine\.config\b",
    r"\bmcp_engine\.schema\b",
    r"\bmcp_engine\.graph\.kuzu_client\b",
    r"\bmcp_engine\.loop\.",
    r"\bfrom\s+campy\b",
    r"\bimport\s+campy\b",
    r"\bfrom\s+sidequests\b",
    r"\bimport\s+sidequests\b",
    r"\bfrom\s+mcp_engine\b",
    r"\bimport\s+mcp_engine\b",
    r'importlib\.import_module\([^)]*"m"\s*\+\s*"cp_"',
    r'importlib\.import_module\([^)]*cp_.*engine',
]

ARC4_BAD_IMPORTS = [
    r"\bfrom\s+agents\.arc3(?:\.|\s)",
    r"\bimport\s+agents\.arc3(?:\.|\s)",
]

# A148: agents/arc3 (v1) was retired and moved to archive/agents-arc3/. Runtime
# code must never import from the archive path (or the old agents.arc3 path,
# which no longer exists but is kept as a regression guard) — v1 is dead code
# kept only for git history / manual inspection, not a live dependency.
ARCHIVE_BAD_IMPORTS = [
    r"\bfrom\s+agents\.arc3(?:\.|\s)",
    r"\bimport\s+agents\.arc3(?:\.|\s)",
    r"\bfrom\s+archive(?:\.|\s)",
    r"\bimport\s+archive(?:\.|\s)",
]
ARCHIVE_GUARDED_PATHS = [
    ROOT / "agents" / "arc4",
    ROOT / "arc_runtime",
    ROOT / "run_single_puzzle.py",
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
    import re

    compiled = [re.compile(p) for p in BAD_REGEXES]

    for f in files:
        # allow test-only shims under sidequest_mcp_client/test_compat if present
        if "sidequest_mcp_client/test_compat" in str(f):
            continue
        try:
            text = f.read_text()
        except Exception:
            continue
        for pat in compiled:
            if pat.search(text):
                offending.append((str(f.relative_to(ROOT)), pat.pattern))

    assert not offending, f"Found direct HippoCampy/Campy bootstrap imports in production files: {offending}"


def test_arc4_has_no_arc3_imports():
    files = _gather_files([ROOT / "agents" / "arc4"])
    offending = []
    compiled = [re.compile(p) for p in ARC4_BAD_IMPORTS]

    for f in files:
        try:
            text = f.read_text()
        except Exception:
            continue
        for pat in compiled:
            if pat.search(text):
                offending.append((str(f.relative_to(ROOT)), pat.pattern))

    assert not offending, f"Found forbidden agents.arc3 imports in arc4 modules: {offending}"


def test_runtime_has_no_archived_arc3_imports():
    files = _gather_files(ARCHIVE_GUARDED_PATHS)
    offending = []
    compiled = [re.compile(p) for p in ARCHIVE_BAD_IMPORTS]

    for f in files:
        try:
            text = f.read_text()
        except Exception:
            continue
        for pat in compiled:
            if pat.search(text):
                offending.append((str(f.relative_to(ROOT)), pat.pattern))

    assert not offending, f"Found forbidden agents.arc3/archive imports in runtime modules: {offending}"
