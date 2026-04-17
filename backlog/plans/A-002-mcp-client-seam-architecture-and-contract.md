# A-002 - MCP Client Seam Architecture and Contract

## Card metadata

- Card: A002
- Priority: P0
- Depends on: A001

## Summary

Document the real MCP client seam for `ARC_AGI` and explicitly mark the current import-wrapper bridge as transitional and non-compliant.

## Implementation approach

- update architecture docs so they describe stdio-only MCP as the v1 production seam
- define the ARC-side client responsibilities
- define the production import policy after migration
- define startup/readiness expectations for future implementation cards

## Concrete file additions/edits

- update `ARCHITECTURE.md`
- update `README.md` if needed for developer-facing setup
- update `EXTRACTION_STATUS.md`
- add a dedicated seam design doc only if `ARCHITECTURE.md` becomes too crowded

## API/interface changes

- define canonical ARC-side client interface:
  - initialize session
  - list tools
  - call tool
  - method-style wrappers for required memory tools

## Tests to add or run

- doc integrity check that architecture references stdio-only MCP
- grep check that docs explicitly call the current bridge transitional/non-compliant

## Validation commands

- `rg -n "stdio|MCP|transitional|non-compliant|import-wrapper" ARCHITECTURE.md README.md EXTRACTION_STATUS.md`

## Assumptions/defaults

- v1 transport is MCP over stdio only
- SideQuests daemon/service boundary is required for production augmented mode
