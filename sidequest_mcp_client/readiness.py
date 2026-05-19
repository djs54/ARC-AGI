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
import time
import uuid
from pathlib import Path
from typing import List, Optional

from .mcp_session import MCPStdIOSession


class ReadinessError(RuntimeError):
    pass


def _cmd_from_env(env_var: str = "CAMPY_MCP_CMD"):
    # Prefer the Campy command while preserving SideQuests-era installs.
    cmd = os.environ.get(env_var)
    if not cmd and env_var == "CAMPY_MCP_CMD":
        cmd = os.environ.get("SIDEQUESTS_MCP_CMD")

    if not cmd:
        return None

    parts = shlex.split(cmd)
    if not parts:
        return None

    if any("sidequests-brain" in part for part in parts):
        python_exe = parts[0]
        if "sidequests-brain" in python_exe:
            python_exe = python_exe.replace("sidequests-brain", "hippocampy")
        return [python_exe, "-m", "campy.adapters.mcp_server"]

    return parts


def _check_brain_socket(socket_path: Optional[str] = None) -> None:
    # Discovery order:
    # 1. CAMPY_BRAIN_SOCKET or CAMPY_SOCKET_PATH env vars
    # 2. explicit socket_path argument
    # 3. active Campy default (~/.campy/brain.sock)
    # 4. legacy SIDEQUESTS_BRAIN_SOCKET or SIDEQUESTS_SOCKET_PATH env vars
    # 5. legacy ~/.sidequests/brain.sock
    
    candidates = []
    if os.environ.get("CAMPY_BRAIN_SOCKET"):
        candidates.append(os.environ.get("CAMPY_BRAIN_SOCKET"))
    if os.environ.get("CAMPY_SOCKET_PATH"):
        candidates.append(os.environ.get("CAMPY_SOCKET_PATH"))
    
    if socket_path:
        candidates.append(socket_path)
    
    candidates.append("~/.campy/brain.sock")
    
    if os.environ.get("SIDEQUESTS_BRAIN_SOCKET"):
        candidates.append(os.environ.get("SIDEQUESTS_BRAIN_SOCKET"))
    if os.environ.get("SIDEQUESTS_SOCKET_PATH"):
        candidates.append(os.environ.get("SIDEQUESTS_SOCKET_PATH"))
        
    candidates.append("~/.sidequests/brain.sock")
    
    found_path = None
    for cand in candidates:
        expanded = os.path.expanduser(cand)
        if os.path.exists(expanded):
            found_path = expanded
            break
            
    if not found_path:
        # Report the first non-legacy default as the missing one for the error
        primary_default = os.path.expanduser("~/.campy/brain.sock")
        raise ReadinessError(
            f"HippoCampy brain socket is missing at {primary_default}. "
            "Start the brain daemon with `campy start` or run `campy setup`."
        )
        
    mode = os.stat(found_path).st_mode
    if not stat.S_ISSOCK(mode):
        raise ReadinessError(
            f"HippoCampy brain socket path exists but is not a UNIX socket: {found_path}"
        )


def _is_daemon_offline_response(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    if str(payload.get("status") or "").lower() == "queued_offline":
        return True
    err = payload.get("error")
    if isinstance(err, dict):
        err_text = " ".join(str(v) for v in err.values())
    else:
        err_text = str(err or "")
    return "daemon_offline" in err_text.lower()


def _extract_text_candidates(payload: object) -> List[str]:
    if not isinstance(payload, dict):
        return []
    out: List[str] = []
    for key in ("text", "content", "summary", "description"):
        v = payload.get(key)
        if isinstance(v, str):
            out.append(v)
    return out


def _response_contains_probe(resp: object, probe_token: str) -> bool:
    if not isinstance(resp, dict):
        return False
    lessons = resp.get("lessons")
    if isinstance(lessons, list):
        for item in lessons:
            if isinstance(item, str) and probe_token in item:
                return True
            if isinstance(item, dict):
                for txt in _extract_text_candidates(item):
                    if probe_token in txt:
                        return True
    return False


def check_mcp_readiness(
    cmd: Optional[List[str]] = None,
    required_tools: Optional[List[str]] = None,
    startup_timeout: float = 3.0,
    call_timeout: float = 3.0,
    require_brain_socket: bool = False,
    brain_socket_path: Optional[str] = None,
    probe_memory_backend: bool = False,
    require_roundtrip_persistence: bool = False,
) -> bool:
    """Verify HippoCampy (Campy) MCP readiness.

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
            "HippoCampy MCP command not configured. Set CAMPY_MCP_CMD to the MCP stdio server command "
            "(legacy fallback: SIDEQUESTS_MCP_CMD)."
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
        if probe_memory_backend and "current_truth" in tool_names:
            probe = session.call_tool(
                "current_truth",
                {
                    "query": "readiness_probe",
                    "session_id": "readiness_probe",
                    "scope": "branch",
                    "limit": 1,
                },
                timeout=call_timeout,
            )
            if _is_daemon_offline_response(probe):
                raise ReadinessError(
                    "HippoCampy MCP is reachable but memory backend is offline "
                    f"(current_truth={probe})."
                )
        if require_roundtrip_persistence:
            required_roundtrip_tools = {"upsert_lesson", "recall_relevant_lessons"}
            if not required_roundtrip_tools.issubset(tool_names):
                missing_roundtrip = sorted(required_roundtrip_tools.difference(tool_names))
                raise ReadinessError(
                    f"HippoCampy MCP missing roundtrip probe tools: {missing_roundtrip}"
                )
            probe_token = f"arc_readiness_probe_{uuid.uuid4().hex[:12]}"
            write_payload = session.call_tool(
                "upsert_lesson",
                {
                    "domain": "readiness_probe",
                    "text": probe_token,
                    "valence": 0.5,
                    "confidence": 0.9,
                    "tags": ["readiness_probe", "arc"],
                },
                timeout=call_timeout,
            )
            if _is_daemon_offline_response(write_payload):
                raise ReadinessError(
                    "HippoCampy MCP accepted upsert but backend is offline "
                    f"(upsert_lesson={write_payload})."
                )
            lesson_id = None
            if isinstance(write_payload, dict):
                lesson_id = write_payload.get("lesson_id") or write_payload.get("id")
            if lesson_id in (None, "", "None"):
                raise ReadinessError(
                    "HippoCampy MCP upsert_lesson returned no lesson id during readiness probe "
                    f"(payload={write_payload})."
                )
            readback_ok = False
            last_readback = None
            for _ in range(3):
                readback = session.call_tool(
                    "recall_relevant_lessons",
                    {"query": probe_token, "limit": 3},
                    timeout=call_timeout,
                )
                last_readback = readback
                if _is_daemon_offline_response(readback):
                    raise ReadinessError(
                        "HippoCampy MCP recall returned backend-offline during readiness probe "
                        f"(recall_relevant_lessons={readback})."
                    )
                if _response_contains_probe(readback, probe_token):
                    readback_ok = True
                    break
                time.sleep(0.1)
            if not readback_ok:
                raise ReadinessError(
                    "HippoCampy MCP write-read roundtrip failed: probe lesson not recallable "
                    f"(last_readback={last_readback})."
                )
        return True
    except Exception as exc:
        raise ReadinessError(f"HippoCampy MCP not available: {exc}") from exc
    finally:
        try:
            session.close()
        except Exception:
            pass
