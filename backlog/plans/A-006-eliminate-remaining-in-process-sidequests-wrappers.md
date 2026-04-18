# A-006 - Eliminate Remaining In-Process SideQuests Wrappers

## Card metadata

- Card: A006
- Priority: P0
- Depends on: A001, A002, A003, A004, A005

## Summary

Finish the architecture transition by removing remaining production-side seam modules that still wrap direct SideQuests imports in-process. After this card, production `ARC_AGI` should talk to SideQuests through MCP-facing client code only.

## Implementation approach

- audit the MCP client package and classify each module as:
  - keep as MCP-facing client/transport code
  - move to test-only compatibility support
  - delete and replace with MCP-backed behavior
- remove production use of `runtime.py`, `tool_handlers.py`, and any similar import-wrapper helpers that directly expose SideQuests internals
- route required behavior through the MCP session and `MCPBrainClient`, or create explicit SideQuests-repo follow-up cards if required MCP endpoints do not yet exist
- add a boundary check for production code paths so direct SideQuests imports fail validation

## Concrete file additions/edits

- update `sidequest_mcp_client/__init__.py` exports to reflect the allowed seam
- edit or remove `sidequest_mcp_client/runtime.py`
- edit or remove `sidequest_mcp_client/tool_handlers.py`
- edit `sidequest_mcp_client/observability.py` if it still wraps direct imports rather than a documented allowed client path
- update affected production callers under `agents/`, `benchmarks/`, and `run_single_puzzle.py`
- add or tighten tests that assert production paths do not rely on in-process wrapper modules
- update `ARCHITECTURE.md` and `EXTRACTION_STATUS.md` if the list of transitional wrappers changes

## API/interface changes

- `sidequest_mcp_client` should expose MCP-facing session/client helpers only
- any non-MCP compatibility helpers must be clearly marked test-only or transitional and must not be imported by production ARC paths
- if observability or readiness needs SideQuests support, it must consume a documented endpoint or produce a SideQuests backlog dependency rather than importing internals directly

## Tests to add

- import-boundary validation for production files:
  - fail on `from mcp_engine`
  - fail on `import mcp_engine`
  - fail on `from sidequests`
  - fail on `import sidequests`
- targeted regression tests for any production file rewired off wrapper modules
- update existing MCP seam tests if module names or exports change

## Validation commands

- `rg -n "from mcp_engine|import mcp_engine|from sidequests|import sidequests" agents benchmarks run_single_puzzle.py sidequest_mcp_client`
- `pytest tests/test_mcp_session_manager.py tests/test_mcp_brain_client.py tests/test_readiness.py`
- `pytest -q` for any additional targeted regression tests added by the implementation

## Assumptions/defaults

- production `ARC_AGI` must consume SideQuests through MCP-facing code only
- if required functionality is unavailable over MCP, the fix belongs in `sidequests-brain` as a new card rather than a new ARC-side import wrapper
- test-only fixtures may still import SideQuests internals when necessary, but they must be isolated and documented
