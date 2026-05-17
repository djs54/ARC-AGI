"""Loop/orchestrator stubs for mcp_engine."""

from pathlib import Path

_SIDEQUESTS_LOOP = Path(__file__).resolve().parents[3] / "sidequests-brain" / "mcp_engine" / "loop"
if _SIDEQUESTS_LOOP.exists():
    __path__.append(str(_SIDEQUESTS_LOOP))
