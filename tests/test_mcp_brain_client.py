import sys
import textwrap
import json
import asyncio
import shutil
import pytest
from pathlib import Path

# Ensure repository root is on sys.path so tests can import the local package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sidequest_mcp_client.mcp_brain_client import MCPBrainClient
from sidequest_mcp_client.mcp_session import MCPToolNotFound


SERVER_SCRIPT = textwrap.dedent(r"""
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        msg = json.loads(line)
    except Exception:
        sys.stdout.write('NOT_JSON\n')
        sys.stdout.flush()
        continue
    typ = msg.get('type')
    id = msg.get('id')
    if typ == 'initialize':
        resp = {'id': id, 'type': 'initialize_response', 'status': 'ok', 'payload': {'ready': True}}
        print(json.dumps(resp), flush=True)
    elif typ == 'list_tools':
        resp = {'id': id, 'type': 'list_tools_response', 'status': 'ok', 'payload': [
            {'name': 'notify_turn', 'schema': {}},
            {'name': 'current_truth', 'schema': {}},
            {'name': 'register_plan', 'schema': {}},
            {'name': 'report_outcome', 'schema': {}},
            {'name': 'recall_plans', 'schema': {}},
            {'name': 'analogical_search', 'schema': {}},
        ]}
        print(json.dumps(resp), flush=True)
    elif typ == 'call_tool':
        name = msg.get('name')
        args = msg.get('arguments') or {}
        if name == 'notify_turn':
            print(json.dumps({'id': id, 'status': 'ok', 'payload': {'status': 'accepted'}}), flush=True)
        elif name == 'current_truth':
            print(json.dumps({'id': id, 'status': 'ok', 'payload': {'results': []}}), flush=True)
        elif name == 'register_plan':
            print(json.dumps({'id': id, 'status': 'ok', 'payload': {'plan_id': 'plan-1'}}), flush=True)
        elif name == 'report_outcome':
            print(json.dumps({'id': id, 'status': 'ok', 'payload': {'updated': True}}), flush=True)
        elif name == 'recall_plans':
            print(json.dumps({'id': id, 'status': 'ok', 'payload': {'plans': []}}), flush=True)
        elif name == 'analogical_search':
            print(json.dumps({'id': id, 'status': 'ok', 'payload': {'results': []}}), flush=True)
        else:
            print(json.dumps({'id': id, 'status': 'error', 'error': 'tool_not_found'}), flush=True)
    else:
        print(json.dumps({'id': id, 'status': 'error', 'error': 'unknown'}), flush=True)
""")


def python_cmd_for(script: str):
    return [sys.executable, "-u", "-c", script]


def test_wrapped_methods_success():
    cmd = python_cmd_for(SERVER_SCRIPT)
    client = MCPBrainClient(db=None, config=None, cmd=cmd)

    async def scenario():
        await client.start()
        await client.initialize_session()

        r1 = await client.notify_turn(role="agent", content="hello", session_id="s1")
        assert r1.get("status") == "accepted"

        r2 = await client.current_truth(query="q", session_id="s1", scope="global", limit=5)
        assert isinstance(r2.get("results"), list)

        r3 = await client.register_plan(goal="g", steps=["a","b"], session_id="s1")
        assert r3.get("plan_id") == "plan-1"

        r4 = await client.report_outcome(valence=0.5, session_id="s1")
        assert r4.get("updated") is True

        r5 = await client.recall_plans(goal_query="g", session_id="s1", min_valence=0.0, limit=3)
        assert isinstance(r5.get("plans"), list)

        r6 = await client.analogical_search(query="q", current_quest_id="x", limit=1, min_similarity=0.1)
        assert isinstance(r6.get("results"), list)

        await client.close()

    asyncio.run(scenario())


def test_missing_tool_raises():
    cmd = python_cmd_for(SERVER_SCRIPT)
    client = MCPBrainClient(db=None, config=None, cmd=cmd)

    async def scenario():
        await client.start()
        await client.initialize_session()
        with pytest.raises(MCPToolNotFound):
            await client.call_tool("this_tool_does_not_exist", {})
        await client.close()

    asyncio.run(scenario())
