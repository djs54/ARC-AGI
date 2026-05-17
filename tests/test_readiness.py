import os
import sys
import textwrap
import shutil
import json
import asyncio
import socket
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

ROUNDTRIP_SERVER_SCRIPT = textwrap.dedent(r"""
import sys, json
STORE = []
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get('method')
    id = msg.get('id')
    if method == 'initialize':
        print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fake', 'version': '0.1.0'}}}), flush=True)
    elif method == 'tools/list':
        print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'tools': [
            {'name': 'notify_turn'},
            {'name': 'current_truth'},
            {'name': 'register_plan'},
            {'name': 'report_outcome'},
            {'name': 'recall_plans'},
            {'name': 'upsert_lesson'},
            {'name': 'recall_relevant_lessons'},
        ]}}), flush=True)
    elif method == 'tools/call':
        params = msg.get('params') or {}
        name = params.get('name')
        args = params.get('arguments') or {}
        if name == 'upsert_lesson':
            token = args.get('text')
            STORE.append(token)
            payload = {'lesson_id': 'probe-1'}
            print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'content': [{'type': 'text', 'text': json.dumps(payload)}]}}), flush=True)
        elif name == 'recall_relevant_lessons':
            q = args.get('query', '')
            lessons = [{'text': s} for s in STORE if q in s]
            payload = {'lessons': lessons}
            print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'content': [{'type': 'text', 'text': json.dumps(payload)}]}}), flush=True)
        else:
            payload = {'results': []}
            print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'content': [{'type': 'text', 'text': json.dumps(payload)}]}}), flush=True)
""")

ROUNDTRIP_FAIL_SERVER_SCRIPT = textwrap.dedent(r"""
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get('method')
    id = msg.get('id')
    if method == 'initialize':
        print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fake', 'version': '0.1.0'}}}), flush=True)
    elif method == 'tools/list':
        print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'tools': [
            {'name': 'current_truth'},
            {'name': 'upsert_lesson'},
            {'name': 'recall_relevant_lessons'},
        ]}}), flush=True)
    elif method == 'tools/call':
        params = msg.get('params') or {}
        name = params.get('name')
        if name == 'upsert_lesson':
            payload = {'lesson_id': 'probe-1'}
        elif name == 'recall_relevant_lessons':
            payload = {'lessons': []}
        else:
            payload = {'results': []}
        print(json.dumps({'jsonrpc': '2.0', 'id': id, 'result': {'content': [{'type': 'text', 'text': json.dumps(payload)}]}}), flush=True)
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
    # Redirect HOME to an empty temp dir to avoid finding real sockets
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CAMPY_BRAIN_SOCKET", raising=False)
    monkeypatch.delenv("CAMPY_SOCKET_PATH", raising=False)
    monkeypatch.delenv("SIDEQUESTS_BRAIN_SOCKET", raising=False)
    monkeypatch.delenv("SIDEQUESTS_SOCKET_PATH", raising=False)
    
    missing_sock = tmp_path / "nonexistent.sock"
    with pytest.raises(ReadinessError, match="HippoCampy brain socket is missing"):
        check_mcp_readiness(
            cmd=cmd,
            required_tools=["notify_turn"],
            require_brain_socket=True,
            brain_socket_path=str(missing_sock),
        )

def test_readiness_brain_socket_fallback_env(tmp_path, monkeypatch):
    cmd = python_cmd_for(SERVER_SCRIPT)
    # Mock os.path.exists and stat.S_ISSOCK to avoid path-too-long or real file issues
    sock_file = "/tmp/fake.sock"
    
    import stat
    def mock_exists(path):
        return os.path.expanduser(path) == sock_file
        
    class MockStat:
        def __init__(self):
            self.st_mode = stat.S_IFSOCK
            
    def mock_stat(path):
        if os.path.expanduser(path) == sock_file:
            return MockStat()
        raise OSError(f"No such file: {path}")

    monkeypatch.setattr(os.path, "exists", mock_exists)
    monkeypatch.setattr(os, "stat", mock_stat)
    
    # Test CAMPY_BRAIN_SOCKET wins
    monkeypatch.setenv("CAMPY_BRAIN_SOCKET", sock_file)
    assert check_mcp_readiness(
        cmd=cmd,
        required_tools=["notify_turn"],
        require_brain_socket=True,
    ) is True
    
    # Test CAMPY_SOCKET_PATH fallback
    monkeypatch.delenv("CAMPY_BRAIN_SOCKET")
    monkeypatch.setenv("CAMPY_SOCKET_PATH", sock_file)
    assert check_mcp_readiness(
        cmd=cmd,
        required_tools=["notify_turn"],
        require_brain_socket=True,
    ) is True
    
    # Test SIDEQUESTS_BRAIN_SOCKET fallback
    monkeypatch.delenv("CAMPY_SOCKET_PATH")
    monkeypatch.setenv("SIDEQUESTS_BRAIN_SOCKET", sock_file)
    assert check_mcp_readiness(
        cmd=cmd,
        required_tools=["notify_turn"],
        require_brain_socket=True,
    ) is True


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
        def __init__(self):
            self.st_mode = stat.S_IFSOCK

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


def test_readiness_memory_probe_detects_daemon_offline():
    offline_script = textwrap.dedent(
        r"""
import sys, json
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    id = msg.get("id")
    if method == "initialize":
        print(json.dumps({"jsonrpc":"2.0","id":id,"result":{"protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"fake","version":"0.1"}}}), flush=True)
    elif method == "tools/list":
        print(json.dumps({"jsonrpc":"2.0","id":id,"result":{"tools":[{"name":"current_truth"}]}}), flush=True)
    elif method == "tools/call":
        print(json.dumps({"jsonrpc":"2.0","id":id,"result":{"content":[{"type":"text","text":"{\"error\": \"daemon_offline\"}"}]}}), flush=True)
"""
    )
    cmd = python_cmd_for(offline_script)
    with pytest.raises(ReadinessError):
        check_mcp_readiness(
            cmd=cmd,
            required_tools=["current_truth"],
            probe_memory_backend=True,
        )


def test_readiness_roundtrip_success():
    cmd = python_cmd_for(ROUNDTRIP_SERVER_SCRIPT)
    assert check_mcp_readiness(
        cmd=cmd,
        required_tools=["current_truth", "upsert_lesson", "recall_relevant_lessons"],
        require_roundtrip_persistence=True,
    ) is True


def test_readiness_roundtrip_fails_when_not_persisted():
    cmd = python_cmd_for(ROUNDTRIP_FAIL_SERVER_SCRIPT)
    with pytest.raises(ReadinessError):
        check_mcp_readiness(
            cmd=cmd,
            required_tools=["current_truth", "upsert_lesson", "recall_relevant_lessons"],
            require_roundtrip_persistence=True,
        )
