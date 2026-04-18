import sys
import textwrap
import json
import shutil
import pytest
from pathlib import Path

# Ensure repository root is on sys.path so tests can import the local package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sidequest_mcp_client.mcp_session import (
    MCPStdIOSession,
    MCPStartupError,
    MCPMalformedResponse,
    MCPToolNotFound,
)


SERVER_SCRIPT = textwrap.dedent(r"""
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        sys.stdout.write("NOT_JSON\n")
        sys.stdout.flush()
        continue
    method = msg.get('method')
    id = msg.get('id')
    if method == 'initialize':
        resp = {'jsonrpc': '2.0', 'id': id, 'result': {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fake', 'version': '0.1.0'}}}
        print(json.dumps(resp), flush=True)
    elif method == 'tools/list':
        resp = {'jsonrpc': '2.0', 'id': id, 'result': {'tools': [{'name': 'echo', 'schema': {}}, {'name': 'add', 'schema': {}}]}}
        print(json.dumps(resp), flush=True)
    elif method == 'tools/call':
        params = msg.get('params') or {}
        name = params.get('name')
        args = params.get('arguments')
        if name == 'echo':
            resp = {'jsonrpc': '2.0', 'id': id, 'result': {'content': [{'type': 'text', 'text': json.dumps({'result': args})}]}}
            print(json.dumps(resp), flush=True)
        else:
            resp = {'jsonrpc': '2.0', 'id': id, 'error': {'code': -32601, 'message': 'Unknown method: ' + str(name)}}
            print(json.dumps(resp), flush=True)
    else:
        resp = {'jsonrpc': '2.0', 'id': id, 'error': {'code': -32601, 'message': 'Unknown method: ' + str(method)}}
        print(json.dumps(resp), flush=True)
""")


def python_cmd_for(script: str):
    return [sys.executable, "-u", "-c", script]


def test_initialize_list_and_call_success():
    cmd = python_cmd_for(SERVER_SCRIPT)
    s = MCPStdIOSession()
    s.start(cmd)

    payload = s.initialize(timeout=2.0)
    assert isinstance(payload, dict) and payload.get("serverInfo", {}).get("name") == "fake"

    tools = s.list_tools(timeout=2.0)
    assert any(t.get("name") == "echo" for t in tools)

    result = s.call_tool("echo", {"message": "hello"}, timeout=2.0)
    assert result.get("result") == {"message": "hello"}

    s.close()


def test_startup_failure_raises():
    # if /usr/bin/false isn't available on PATH, skip this test
    false_path = shutil.which("false")
    if not false_path:
        pytest.skip("skipping startup failure test; 'false' not found")

    s = MCPStdIOSession()
    with pytest.raises(MCPStartupError):
        s.start([false_path])


def test_malformed_response_raises():
    MALFORMED = textwrap.dedent(r"""
import sys
for line in sys.stdin:
    sys.stdout.write('not a json\n')
    sys.stdout.flush()
""")
    cmd = python_cmd_for(MALFORMED)
    s = MCPStdIOSession()
    s.start(cmd)

    with pytest.raises(MCPMalformedResponse):
        s.initialize(timeout=1.0)

    s.close()


def test_missing_tool_raises():
    cmd = python_cmd_for(SERVER_SCRIPT)
    s = MCPStdIOSession()
    s.start(cmd)
    s.initialize(timeout=2.0)

    with pytest.raises(MCPToolNotFound):
        s.call_tool("this_tool_does_not_exist", {}, timeout=2.0)

    s.close()
