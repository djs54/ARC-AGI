# ARC_AGI Master Backlog Tracker

> Active tracker for `A###` execution cards.
> Historical migrated `B###` artifacts live under `backlog/migrated_from_sidequests/` and are archive/reference only.

| Card | Title | Priority | State | Owner | Depends On | Matched Plan | Validation | Notes |
|---|---|---|---|---|---|---|---|---|
| A001 | ARC Backlog Rules and Tracker Bootstrap | P0 | complete | TBD | None | `backlog/plans/A-001-arc-backlog-rules-and-tracker-bootstrap.md` | Backlog framework files created | Establishes active `A###` queue and archive policy |
| A002 | MCP Client Seam Architecture and Contract | P0 | complete | TBD | A001 | `backlog/plans/A-002-mcp-client-seam-architecture-and-contract.md` | Architecture docs updated with stdio-only MCP contract and production import policy | Locks the stdio-only MCP design and production import policy |
| A003 | STDIO MCP Session Manager | P0 | complete | TBD | A002 | `backlog/plans/A-003-stdio-mcp-session-manager.md` | `sidequest_mcp_client/mcp_session.py` and transport tests added | Implements low-level MCP stdio session management |
| A004 | MCPBrainClient for ARC Runtime | P0 | complete | TBD | A003 | `backlog/plans/A-004-mcp-brain-client-for-arc-runtime.md` | `sidequest_mcp_client/mcp_brain_client.py` added and production paths rewired | Replaces direct tool-handler use in production ARC paths |
| A005 | Remove Direct SideQuests Runtime Bootstrap | P0 | complete | TBD | A003, A004 | `backlog/plans/A-005-remove-direct-sidequests-runtime-bootstrap.md` | Production entrypoints now use MCP readiness checks and `MCPBrainClient` | Removes direct Kuzu/schema/loop bootstrap from production ARC startup |
| A006 | Eliminate Remaining In-Process SideQuests Wrappers | P0 | complete | TBD | A001, A002, A003, A004, A005 | `backlog/plans/A-006-eliminate-remaining-in-process-sidequests-wrappers.md` | Production wrappers removed, compatibility helpers isolated to `sidequest_mcp_client/test_compat/`, boundary tests passing | Finishes the MCP-only production seam by removing remaining import-wrapper modules |
