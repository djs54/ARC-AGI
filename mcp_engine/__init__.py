"""
Minimal compatibility shim for tests that expect `mcp_engine` package.
This is a lightweight stub; real deployments should use the official package.
"""

from pathlib import Path

_SIDEQUESTS_MCP_ENGINE = Path(__file__).resolve().parents[2] / "sidequests-brain" / "mcp_engine"
if _SIDEQUESTS_MCP_ENGINE.exists():
    __path__.append(str(_SIDEQUESTS_MCP_ENGINE))

__all__ = ["config", "llm", "loop"]
