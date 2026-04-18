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
    typ = msg.get('type')
    id = msg.get('id')
    if typ == 'initialize':
        print(json.dumps({'id': id, 'status': 'ok', 'payload': {'ready': True}}), flush=True)
    elif typ == 'list_tools':
        print(json.dumps({'id': id, 'status': 'ok', 'payload': [{'name': 'notify_turn'}, {'name': 'current_truth'}, {'name': 'register_plan'}, {'name': 'report_outcome'}, {'name': 'recall_plans'}]}), flush=True)
    else:
        print(json.dumps({'id': id, 'status': 'error', 'error': 'unknown'}), flush=True)
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
