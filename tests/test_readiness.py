import os
import sys
import textwrap
import shutil
import json
import asyncio
from pathlib import Path
import pytest

# Ensure repo root on sys.path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sidequest_mcp_client.readiness import _cmd_from_env, check_mcp_readiness, ReadinessError


SERVER_SCRIPT = textwrap.dedent(r"""
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        print('BAD', flush=True)
        continue
    method = msg.get('method')
    id = msg.get('id')
    if method == 'initialize':
        print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fake', 'version': '0.1.0'}}}), flush=True)
    elif method == 'tools/list':
        print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'tools': [{'name': 'notify_turn'}, {'name': 'current_truth'}, {'name': 'register_plan'}, {'name': 'report_outcome'}, {'name': 'recall_plans'}]}}), flush=True)
    else:
        print(json.dumps({'jsonrpc': '2.0', 'id': id, 'error': {'code': -32601, 'message': 'unknown'}}), flush=True)
""")


def python_cmd_for(script: str):
    return [sys.executable, "-u", "-c", script]


def test_cmd_from_env_prefers_campy(monkeypatch):
    monkeypatch.setenv("CAMPY_MCP_CMD", "python -m campy.adapters.mcp_server")
    monkeypatch.setenv("SIDEQUESTS_MCP_CMD", "python legacy.py")

    assert _cmd_from_env() == ["python", "-m", "campy.adapters.mcp_server"]


def test_cmd_from_env_supports_legacy_sidequests_fallback(monkeypatch):
    monkeypatch.delenv("CAMPY_MCP_CMD", raising=False)
    monkeypatch.setenv("SIDEQUESTS_MCP_CMD", "python legacy.py")

    assert _cmd_from_env() == ["python", "legacy.py"]


def test_readiness_success():
    cmd = python_cmd_for(SERVER_SCRIPT)
    # Should not raise
    assert check_mcp_readiness(cmd=cmd, required_tools=["notify_turn", "current_truth", "recall_plans"]) is True


def test_readiness_failure():
    false_path = shutil.which("false")
    if not false_path:
        pytest.skip("'false' binary not found; skipping failure test")

    with pytest.raises(ReadinessError):
        check_mcp_readiness(cmd=[false_path])


def test_readiness_fails_when_required_brain_socket_missing(tmp_path: Path, monkeypatch):
    cmd = python_cmd_for(SERVER_SCRIPT)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CAMPY_BRAIN_SOCKET", raising=False)
    monkeypatch.delenv("CAMPY_SOCKET_PATH", raising=False)
    monkeypatch.delenv("SIDEQUESTS_BRAIN_SOCKET", raising=False)
    monkeypatch.delenv("SIDEQUESTS_SOCKET_PATH", raising=False)

    with pytest.raises(ReadinessError, match="HippoCampy brain socket is missing"):
        check_mcp_readiness(
            cmd=cmd,
            required_tools=["notify_turn"],
            require_brain_socket=True,
            brain_socket_path=str(tmp_path / "missing.sock"),
        )


def test_readiness_brain_socket_prefers_campy_over_legacy(monkeypatch):
    cmd = python_cmd_for(SERVER_SCRIPT)
    campy_sock = "/tmp/campy.sock"
    legacy_sock = "/tmp/legacy-sidequests.sock"
    checked_paths = []

    import stat

    def mock_exists(path):
        expanded = os.path.expanduser(path)
        checked_paths.append(expanded)
        return expanded in {campy_sock, legacy_sock}

    class MockStat:
        st_mode = stat.S_IFSOCK

    def mock_stat(path):
        assert os.path.expanduser(path) == campy_sock
        return MockStat()

    monkeypatch.setattr(os.path, "exists", mock_exists)
    monkeypatch.setattr(os, "stat", mock_stat)
    monkeypatch.setenv("CAMPY_BRAIN_SOCKET", campy_sock)
    monkeypatch.setenv("SIDEQUESTS_BRAIN_SOCKET", legacy_sock)

    assert check_mcp_readiness(
        cmd=cmd,
        required_tools=["notify_turn"],
        require_brain_socket=True,
    ) is True
    assert checked_paths[0] == campy_sock
