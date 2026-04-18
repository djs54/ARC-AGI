# A-007 - Distinguish MCP Tool Timeouts from LLM Timeouts

## Card metadata

- Card: A007
- Priority: P0
- Depends on: A003, A004, A006

## Summary

Correct ARC failure reporting so MCP transport/tool timeouts are not mislabeled as `llm_timeout`. Preserve tool identity in the recorded failure so smoke outputs point directly at the failing subsystem.

## Implementation approach

- audit the current timeout error path from `sidequest_mcp_client.mcp_session` through `MCPBrainClient`, runtime exception handling, and final failure classification
- introduce an MCP/tool-timeout-specific classification signal without breaking existing benchmark consumers
- preserve tool name and timeout context in the surfaced error payload
- update result export/tests so a future `current_truth` stall is clearly marked as an MCP/tool failure, not an LLM failure

## Concrete file additions/edits

- edit `sidequest_mcp_client/mcp_session.py`
  - keep timeout messages tool-aware
  - make sure the timeout error shape is consistent enough for classification
- edit `agents/arc3/failure_taxonomy.py`
  - add or route a distinct MCP/tool-timeout class
  - do not collapse all `timeout` text into `llm_timeout`
- edit `agents/arc3/runner.py`
  - preserve the structured timeout/tool context in failure recording
- add or update targeted tests under `tests/`

## API/interface changes

- failure taxonomy gains an MCP/tool-timeout distinction
- ARC result payloads should preserve enough information to identify the stalled tool

## Tests to add or run

- targeted taxonomy tests:
  - MCP tool timeout string classifies to the new/non-LLM bucket
  - true LLM timeout still classifies to `llm_timeout`
- any result-export test that asserts `failure_class`

## Validation commands

- `pytest -q tests/test_b185_failure_taxonomy.py`
- any focused runner/result export tests touched by the implementation

## Assumptions/defaults

- benchmark consumers can tolerate one additional or refined failure class if it is documented
- preserving the exact tool name is more useful than collapsing every timeout into a single bucket
