# Plan: A-123 — Wire ARC v2 Into run_single_puzzle.py and Smoke Validate

## Card metadata

- **Card:** A123
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A118, A119, A120, A121, A122, B278
- **Intended executor:** GPT-5.4-mini subagent

## Summary

Integrate ARC v2 into the real runner, provide the ARC-side MCP adapter over the B278 tool surface, and validate the smoke path without breaking v1.

## Integration contract

- A123 is the only ARC card allowed to know concrete B278 tool names.
- A118-A122 continue to depend only on shared ARC v2 ports and test doubles.
- `run_single_puzzle.py` must keep `v1` as the default path until v2 is explicitly requested.

## Implementation approach

### Step 1: Add the ARC-side MCP adapter

Create `agents/arc4/graph_queries.py` that implements the A118 graph-query port using `MCPBrainClient.call_tool()`.

The adapter must:

- map each port method to the exact B278 tool name
- normalize raw tool responses into the shapes expected by A118-A122
- fail loudly during integration tests when a required B278 tool is missing

### Step 2: Add the v2 entrypoint path

Update `run_single_puzzle.py` to:

- accept `--agent-version` with `v1` default
- build the ARC v2 dependency bundle
- run the A118 workflow when `--agent-version=v2`
- preserve existing v1 behavior unchanged otherwise

### Step 3: Add ARC v2 telemetry glue

Create `agents/arc4/telemetry.py` to adapt workflow step results into the existing smoke artifact streams. Keep compatibility with the current artifact readers unless this card intentionally changes them.

### Step 4: Add focused tests and smoke validation

Create `tests/test_arc4_integration.py` for:

- v1 default path unchanged
- v2 path builds the dependency bundle correctly
- B278 tool-name mapping in `graph_queries.py`
- normalized response handling for a representative subset of tools

Then run the documented smoke command against the real MCP server after B278 is available.

## Concrete file edits

- `run_single_puzzle.py`
- `agents/arc4/graph_queries.py`
- `agents/arc4/telemetry.py`
- `tests/test_arc4_integration.py`

## Validation commands

```bash
pytest -q tests/test_arc4_integration.py
```

```bash
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
PYTHONPATH=. .venv/bin/python run_single_puzzle.py \
  --live-smoke --num-puzzles 1 --max-steps 10 --agent-version=v2
```

## Assumptions and defaults

- B278 lands in the sibling `hippocampy` repo before final smoke validation.
- If artifact field names must change, this card must update the downstream smoke readers in the same slice.