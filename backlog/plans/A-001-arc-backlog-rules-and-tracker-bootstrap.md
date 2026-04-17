# A-001 - ARC Backlog Rules and Tracker Bootstrap

## Card metadata

- Card: A001
- Priority: P0
- Depends on: None

## Summary

Create the active backlog framework for `ARC_AGI` so new work uses `A###` cards and matching `A-###` plans, while migrated `B###` material remains archive-only.

## Implementation approach

- add an ARC-specific backlog README
- add ARC-specific backlog rules by forking the SideQuests process and adapting it to MCP-client architecture
- add an active tracker for `A001` through `A005`
- mark A001 complete because this batch creates the framework itself

## Concrete file additions/edits

- create `backlog/README.md`
- create `backlog/BacklogRules.md`
- create `backlog/masterBacklogTracker.md`
- create `backlog/A001.md`
- create `backlog/plans/A-001-arc-backlog-rules-and-tracker-bootstrap.md`

## API/interface changes

- no runtime API changes
- process interface change: active work now uses `A###` card IDs and `A-###` plan IDs

## Tests to add or run

- file existence check for backlog framework files
- integrity check that tracker rows for `A001` through `A005` all point to exact plan paths

## Validation commands

- `find backlog -maxdepth 2 -type f | sort`
- `rg -n "A00[1-5]|A-00[1-5]" backlog`

## Assumptions/defaults

- migrated `B###` artifacts stay under `backlog/migrated_from_sidequests/`
- active queue starts with `A001` through `A005`
