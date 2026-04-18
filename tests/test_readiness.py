import sys
import textwrap
import shutil
import json
import asyncio
from pathlib import Path

# Ensure repo root on sys.path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sidequest_mcp_client.readiness import check_mcp_readiness, ReadinessError


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


def test_readiness_success():
    cmd = python_cmd_for(SERVER_SCRIPT)
    # Should not raise
    assert check_mcp_readiness(cmd=cmd, required_tools=["notify_turn", "current_truth", "recall_plans"]) is True


def test_readiness_failure():
    false_path = shutil.which("false")
    if not false_path:
        import pytest

        pytest.skip("'false' binary not found; skipping failure test")

    with __import__("pytest").raises(ReadinessError):
        check_mcp_readiness(cmd=[false_path])
