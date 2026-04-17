# Master Backlog Tracker

This file lists active `A###` cards and points to their matching plans.

Active queue (bootstrap)

| Card | Title | Plan |
|---|---|---|
| A001 | ARC Backlog Rules and Tracker Bootstrap | [A-001 - ARC Backlog Rules and Tracker Bootstrap](backlog/plans/A-001-arc-backlog-rules-and-tracker-bootstrap.md) |
| A002 | Reserved | backlog/plans/A-002-reserved.md |
| A003 | Reserved | backlog/plans/A-003-reserved.md |
| A004 | Reserved | backlog/plans/A-004-reserved.md |
| A005 | Reserved | backlog/plans/A-005-reserved.md |

Notes

- Update this tracker when promoting new active cards; each row must point to the exact plan path for the card.
- See `backlog/BacklogRules.md` for required naming and enforcement rules.
# ARC_AGI Master Backlog Tracker

> Active tracker for `A###` execution cards.
> Historical migrated `B###` artifacts live under `backlog/migrated_from_sidequests/` and are archive/reference only.

| Card | Title | Priority | State | Owner | Depends On | Matched Plan | Validation | Notes |
|---|---|---|---|---|---|---|---|---|
| A001 | ARC Backlog Rules and Tracker Bootstrap | P0 | complete | TBD | None | `backlog/plans/A-001-arc-backlog-rules-and-tracker-bootstrap.md` | Backlog framework files created | Establishes active `A###` queue and archive policy |
| A002 | MCP Client Seam Architecture and Contract | P0 | ready | TBD | A001 | `backlog/plans/A-002-mcp-client-seam-architecture-and-contract.md` | - | Locks the stdio-only MCP design and production import policy |
| A003 | STDIO MCP Session Manager | P0 | ready | TBD | A002 | `backlog/plans/A-003-stdio-mcp-session-manager.md` | - | Implements low-level MCP stdio session management |
| A004 | MCPBrainClient for ARC Runtime | P0 | ready | TBD | A003 | `backlog/plans/A-004-mcp-brain-client-for-arc-runtime.md` | - | Replaces direct tool-handler use in production ARC paths |
| A005 | Remove Direct SideQuests Runtime Bootstrap | P0 | pending | TBD | A003, A004 | `backlog/plans/A-005-remove-direct-sidequests-runtime-bootstrap.md` | - | Removes direct Kuzu/schema/loop bootstrap from production ARC startup |
