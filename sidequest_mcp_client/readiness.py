"""MCP readiness helpers used by production ARC startup paths.

Provides a simple `check_mcp_readiness` function that starts or attaches to a
stdio MCP session, runs `initialize` and `tools/list`, and verifies the
presence of required tools. Raises `ReadinessError` with a clear message on
failure.
"""

from __future__ import annotations

import os
import shlex
from typing import List, Optional

from .mcp_session import MCPStdIOSession


class ReadinessError(RuntimeError):
    pass


def _cmd_from_env(env_var: str = "SIDEQUESTS_MCP_CMD"):
    cmd = os.environ.get(env_var)
    if not cmd:
        return None
    return shlex.split(cmd)


def check_mcp_readiness(cmd: Optional[List[str]] = None, required_tools: Optional[List[str]] = None, startup_timeout: float = 3.0, call_timeout: float = 3.0) -> bool:
    """Verify SideQuests MCP readiness.

    - `cmd`: optional command list to start the MCP stdio server. If `None`, the
      environment variable `SIDEQUESTS_MCP_CMD` will be used.
    - `required_tools`: optional list of tool names that must be present in
      `tools/list`.

    Raises `ReadinessError` with a clear message on failure. Returns True on
    success.
    """
    if cmd is None:
        cmd = _cmd_from_env()
    if cmd is None:
        raise ReadinessError(
            "SideQuests MCP command not configured. Set SIDEQUESTS_MCP_CMD to the MCP stdio server command."
        )

    session = MCPStdIOSession(cmd=cmd)
    try:
        session.start(cmd, startup_timeout)
        session.initialize(timeout=call_timeout)
        tools = session.list_tools(timeout=call_timeout)
        tool_names = {t.get("name") for t in (tools or [])}
        missing = [t for t in (required_tools or []) if t not in tool_names]
        if missing:
            raise ReadinessError(f"SideQuests MCP missing required tools: {missing}")
        return True
    except Exception as exc:
        raise ReadinessError(f"SideQuests MCP not available: {exc}") from exc
    finally:
        try:
            session.close()
        except Exception:
            pass
