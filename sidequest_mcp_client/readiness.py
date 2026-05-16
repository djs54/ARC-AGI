"""MCP readiness helpers used by production ARC startup paths.

Provides a simple `check_mcp_readiness` function that starts or attaches to a
stdio MCP session, runs `initialize` and `tools/list`, and verifies the
presence of required tools. Raises `ReadinessError` with a clear message on
failure.
"""

from __future__ import annotations

import os
import shlex
import stat
from typing import List, Optional

from .mcp_session import MCPStdIOSession


class ReadinessError(RuntimeError):
    pass


def _cmd_from_env(env_var: str = "CAMPY_MCP_CMD"):
    cmd = os.environ.get(env_var)
    if not cmd and env_var == "CAMPY_MCP_CMD":
        cmd = os.environ.get("SIDEQUESTS_MCP_CMD")
    if not cmd:
        return None
    return shlex.split(cmd)


def _check_brain_socket(socket_path: Optional[str] = None) -> None:
    """Verify the Campy brain socket with legacy SideQuests fallback."""
    candidates: list[str] = []
    for env_name in ("CAMPY_BRAIN_SOCKET", "CAMPY_SOCKET_PATH"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(value)
    if socket_path:
        candidates.append(socket_path)
    candidates.append("~/.campy/brain.sock")
    for env_name in ("SIDEQUESTS_BRAIN_SOCKET", "SIDEQUESTS_SOCKET_PATH"):
        value = os.environ.get(env_name)
        if value:
            candidates.append(value)
    candidates.append("~/.sidequests/brain.sock")

    for candidate in candidates:
        expanded = os.path.expanduser(candidate)
        if os.path.exists(expanded):
            mode = os.stat(expanded).st_mode
            if not stat.S_ISSOCK(mode):
                raise ReadinessError(
                    f"HippoCampy brain socket path exists but is not a UNIX socket: {expanded}"
                )
            return

    primary_default = os.path.expanduser("~/.campy/brain.sock")
    raise ReadinessError(
        f"HippoCampy brain socket is missing at {primary_default}. "
        "Start the brain daemon with `campy start` or run `campy setup`."
    )


def check_mcp_readiness(
    cmd: Optional[List[str]] = None,
    required_tools: Optional[List[str]] = None,
    startup_timeout: float = 3.0,
    call_timeout: float = 3.0,
    require_brain_socket: bool = False,
    brain_socket_path: Optional[str] = None,
) -> bool:
    """Verify HippoCampy/Campy MCP readiness.

    - `cmd`: optional command list to start the MCP stdio server. If `None`, the
      environment variable `CAMPY_MCP_CMD` will be used.
    - `required_tools`: optional list of tool names that must be present in
      `tools/list`.

    Raises `ReadinessError` with a clear message on failure. Returns True on
    success.
    """
    if require_brain_socket:
        _check_brain_socket(brain_socket_path)

    if cmd is None:
        cmd = _cmd_from_env()
    if cmd is None:
        raise ReadinessError(
            "HippoCampy MCP command not configured. Set CAMPY_MCP_CMD to the MCP stdio server command (legacy fallback: SIDEQUESTS_MCP_CMD)."
        )

    session = MCPStdIOSession(cmd=cmd)
    try:
        session.start(cmd, startup_timeout)
        session.initialize(timeout=call_timeout)
        tools = session.list_tools(timeout=call_timeout)
        tool_names = {t.get("name") for t in (tools or [])}
        missing = [t for t in (required_tools or []) if t not in tool_names]
        if missing:
            raise ReadinessError(f"HippoCampy MCP missing required tools: {missing}")
        return True
    except Exception as exc:
        raise ReadinessError(f"HippoCampy MCP not available: {exc}") from exc
    finally:
        try:
            session.close()
        except Exception:
            pass
