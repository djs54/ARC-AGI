# A-008 - Stabilize `current_truth` Over the MCP Seam

## Card metadata

- Card: A008
- Priority: P0
- Depends on: A003, A004, A006, A007, B219

## Summary

Make `current_truth` reliable in real MCP-backed ARC runs. The immediate symptom is a 5-second tool-call timeout, but the fix should be based on measured runtime behavior rather than a blind timeout increase.

## Implementation approach

- measure where ARC invokes `current_truth` and which call path stalls first in a real smoke
- confirm whether the dominant issue is:
  - too-low ARC-side MCP timeout
  - too-frequent or too-eager ARC retrieval calls
  - a SideQuests-side `current_truth` latency issue that needs its own repo card
- implement the smallest correct fix in ARC first:
  - configurable per-tool timeout for expensive retrieval calls
  - or tighter ARC retrieval gating if calls are happening too often
- if SideQuests latency is the actual blocker, create a SideQuests dependency card rather than bypassing the MCP seam

## Concrete file additions/edits

- edit `sidequest_mcp_client/mcp_brain_client.py`
  - allow explicit timeout control for `current_truth`
- edit `sidequest_mcp_client/mcp_session.py`
  - preserve useful timeout diagnostics
- edit `agents/arc3/orchestrator.py`
  - audit and potentially reduce eager retrieval sites
- edit `agents/arc3/runner.py`
  - keep smoke/debug evidence clear
- add or update focused tests for timeout budgeting and retrieval-call behavior

## API/interface changes

- if introduced, per-tool MCP timeout control must stay centralized in the ARC MCP client seam
- retrieval gating changes must preserve the MCP boundary and must not add prompt bloat

## Tests to add or run

- focused client tests for timeout override behavior
- focused orchestrator tests if retrieval gating changes
- one real smoke validation through `run_single_puzzle.py --num-puzzles 1`

## Validation commands

- `pytest -q tests/test_mcp_session_manager.py tests/test_mcp_brain_client.py tests/test_arc3_harness.py`
- one-puzzle MCP smoke using `run_single_puzzle.py`

## Assumptions/defaults

- `current_truth` is an expensive retrieval call relative to lighter MCP tool calls and may need a different timeout budget
- if the fix changes retrieval frequency, record token/cost impact explicitly
- if SideQuests is the actual bottleneck, create the follow-on card there rather than embedding a workaround in ARC
