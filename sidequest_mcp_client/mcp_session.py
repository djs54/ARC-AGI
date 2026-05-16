"""Simple MCP-over-stdio session manager for ARC_AGI.

This module implements a minimal, testable stdio-backed MCP client used by
the ARC runtime to talk to a HippoCampy/Campy MCP stdio server. It intentionally
keeps transport-level normalization and error handling in one place.
"""

from __future__ import annotations

import json
import select
import subprocess
import threading
import time
import uuid
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MCPError(Exception):
    pass


class MCPStartupError(MCPError):
    pass


class MCPMalformedResponse(MCPError):
    pass


class MCPToolNotFound(MCPError):
    pass


class MCPTimeoutError(MCPError):
    pass


class MCPStdIOSession:
    """Manage a stdio-backed MCP process and provide simple request/response
    helpers.

    Methods follow the A-003 plan contract: `start`, `initialize`,
    `list_tools`, `call_tool`, and `close`.
    """

    def __init__(self, cmd: Optional[List[str]] = None):
        self.cmd = cmd
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def start(self, cmd: Optional[List[str]] = None, startup_timeout: float = 3.0) -> "MCPStdIOSession":
        """Launch the stdio MCP server command and verify it did not exit immediately.

        The command should be an array invocation (no shell).
        """
        if cmd is not None:
            self.cmd = cmd
        if not self.cmd:
            raise ValueError("no command provided to start()")

        self.proc = subprocess.Popen(self.cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)

        # Small sleep to allow short-lived commands to exit and be detected.
        time.sleep(0.05)
        if self.proc.poll() is not None:
            stderr = self.proc.stderr.read() if self.proc.stderr is not None else ""
            raise MCPStartupError(f"process exited during startup (code={self.proc.returncode}): {stderr!r}")

        return self

    def initialize(self, timeout: float = 5.0) -> Dict[str, Any]:
        resp = self._request(
            {
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {},
            },
            timeout=timeout,
        )
        if "result" in resp:
            return resp.get("result", {})
        raise MCPError(resp)

    def list_tools(self, timeout: float = 5.0) -> List[Dict[str, Any]]:
        resp = self._request(
            {
                "jsonrpc": "2.0",
                "method": "tools/list",
                "params": {},
            },
            timeout=timeout,
        )
        if "result" in resp:
            return resp.get("result", {}).get("tools", [])
        raise MCPError(resp)

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
        resp = self._request(
            {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments or {},
                },
            },
            timeout=timeout,
        )
        if "result" in resp:
            return self._normalize_tool_result(resp.get("result", {}))
        err = resp.get("error", {})
        if err.get("code") == -32601:
            raise MCPToolNotFound(name)
        raise MCPError(resp)

    def _normalize_tool_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict):
                text = first.get("text")
                if isinstance(text, str):
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        return {"text": text}
                    if isinstance(parsed, dict):
                        return parsed
                    return {"value": parsed}
        return result

    def _request(self, body: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
        if not self.proc or not self.proc.stdin or not self.proc.stdout:
            raise MCPError("session not started")

        req_id = str(uuid.uuid4())
        body = dict(body)
        body["id"] = req_id
        raw = json.dumps(body)
        op_name = self._describe_operation(body)

        with self._lock:
            try:
                self.proc.stdin.write(raw + "\n")
                self.proc.stdin.flush()
            except Exception as e:
                raise MCPError("failed to write to process stdin") from e

            end = time.time() + timeout
            fd = self.proc.stdout
            while time.time() < end:
                # Use select so we can honor the timeout.
                try:
                    ready, _, _ = select.select([fd], [], [], max(0, end - time.time()))
                except Exception:
                    # Fall back to a blocking readline attempt if select is not available.
                    line = fd.readline()
                    if not line:
                        if self.proc.poll() is not None:
                            raise MCPStartupError("process terminated")
                        continue
                    try:
                        resp = json.loads(line.strip())
                    except Exception:
                        raise MCPMalformedResponse(line)
                    if resp.get("id") == req_id or "id" not in resp:
                        return resp
                    continue

                if not ready:
                    continue

                line = fd.readline()
                if not line:
                    if self.proc.poll() is not None:
                        raise MCPStartupError("process terminated")
                    continue
                try:
                    resp = json.loads(line.strip())
                except Exception:
                    raise MCPMalformedResponse(line)

                if resp.get("id") == req_id or "id" not in resp:
                    return resp

            raise MCPTimeoutError(f"timeout waiting for response to {req_id} during {op_name}")

    def _describe_operation(self, body: Dict[str, Any]) -> str:
        method = body.get("method")
        if method == "tools/call":
            params = body.get("params")
            if isinstance(params, dict):
                tool_name = params.get("name")
                if isinstance(tool_name, str) and tool_name:
                    return f"tools/call:{tool_name}"
        if isinstance(method, str) and method:
            return method
        return "unknown_operation"

    def close(self, timeout: float = 2.0) -> None:
        if not self.proc:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=timeout)
        except Exception:
            try:
                self.proc.kill()
                self.proc.wait(timeout=timeout)
            except Exception:
                pass
        finally:
            self.proc = None


__all__ = ["MCPStdIOSession", "MCPError", "MCPStartupError", "MCPMalformedResponse", "MCPToolNotFound", "MCPTimeoutError"]
