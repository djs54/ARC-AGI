# ARC Backlog Rules

Purpose

These ARC-specific rules govern the active backlog (`A###`) and ensure migrated SideQuests material remains archive-only.

Core rules

1. One card, one plan: Every active item MUST have exactly one card file (`backlog/A###.md`) and one matching plan file (`backlog/plans/A-###-short-title.md`). The numeric ID must match exactly across the card and plan.

2. Naming and placement: Active cards use the `A###` numeric ID and live in the `backlog/` root. Plans use the `A-###-slug.md` form and live under `backlog/plans/`.

3. Tracker requirement: All active `A###` items must be listed in `backlog/masterBacklogTracker.md` with a plan path pointing to the matching plan file.

4. No direct SideQuests imports: Production code MUST NOT import SideQuests internals or rely on migrated SideQuests process files. SideQuests artifacts are archive-only; integrations must occur via documented MCP-client interfaces or approved shims.

5. PR/CI enforcement: Pull requests that add or modify active `A###` items should include validation that both the card and plan exist and that the numeric IDs match. Recommended checks include a lightweight script verifying file presence and filename patterns.

Example validation (pseudo-check)

```sh
# Verify a card A123 has a matching plan
card=backlog/A123.md
plan_glob=backlog/plans/A-123-*.md
[ -f "$card" ] && ls $plan_glob
```

Notes

- Migrated SideQuests material lives under `backlog/migrated_from_sidequests/` and is explicitly archive-only.
- The active queue for this bootstrap starts with `A001` through `A005` by convention.
# ARC_AGI Backlog Rules

Last updated: 2026-04-17

## 1) Why this document exists

`ARC_AGI` now has its own active backlog and must be executable by lower-cost models without losing architectural discipline.

Goals:

- keep ARC work moving through small, auditable cards
- make each card implementable without guesswork
- preserve the MCP-client architecture boundary to SideQuests/Campy
- prevent ARC from reabsorbing SideQuests internals through shortcuts

## 2) Executor and delegation policy

### Core rule

Execute active cards sequentially by dependency order.

### Recommended cheap-model policy

- prefer cheaper models first for doc/process and scoped implementation cards
- escalate only when a card proves too ambiguous or too coupled
- keep prompts short and point the executor at exactly one card and one matching plan

### Delegation order

1. read the active card
2. read the matching plan
3. implement only that card
4. run the listed validation commands
5. report changed files, tests run, and result summary

## 3) Per-card workflow

1. Read `backlog/A###.md`
2. Read `backlog/plans/A-###-<slug>.md`
3. Confirm the card state is `ready`
4. Implement only the scoped changes
5. Run targeted validation first, broader checks when the card risk warrants it
6. Update validation notes only after verification succeeds

## 4) Verification standards

Every completed card must record:

- changed files
- validation commands
- pass/fail summary
- known unrelated failures, clearly separated

Never mark a card complete without test or validation evidence.

## 5) Card authoring criteria

Each active card file must include:

- card ID and title
- state
- priority
- layer
- plan reference
- problem statement
- what it does
- dependencies
- files to create/modify
- acceptance criteria
- outcome

### Execution-ready checklist

A card is execution-ready only if:

- it uses `A###` numbering
- it has exactly one matching `A-###` plan file
- scope is specific enough to implement without guessing
- acceptance criteria are measurable
- dependencies are explicit
- file targets are concrete
- the card states its layer
- token/cost impact is addressed if the card changes tool-call frequency, prompt payload, or transport chatter

## 6) ARC-specific non-negotiables

- active ARC work must use `A###` cards only
- migrated `B###` artifacts are archive/reference only
- every active `A###` card must have one matching `A-###` plan before it can be marked `ready`
- production ARC code must not import `mcp_engine.*` or `sidequests.*` directly
- production ARC integration must go through the MCP seam
- any missing capability required from memory must be added in `sidequests-brain`, not bypassed in ARC
- any card touching production integration must state how it preserves or improves the MCP boundary

### Allowed layers

Every card must declare one primary layer:

- transport/client seam
- ARC runtime
- evaluation/harness
- docs/process

## 7) Plan document requirements

Plan location:

- `backlog/plans/`

Plan naming:

- `A-###-<slug>.md`

Each plan must include:

- card metadata
- summary
- implementation approach
- concrete file additions/edits
- API/interface changes
- tests to add or run
- validation commands
- assumptions/defaults

Each plan must be decision-complete enough that a cheaper model can implement it without deciding:

- module names
- doc targets
- tracker format
- acceptance-test shape
- dependency order

## 8) Tracker and link integrity rules

The active tracker is:

- `backlog/masterBacklogTracker.md`

Rules:

- every active row must point to an exact plan path
- tracker card ID and plan ID must match
- no active `A###` row may exist without a matching plan
- `pending` is allowed without implementation, but not without a plan if the card is intended for execution soon

## 9) Completion criteria

A card can move to `complete` only when all are true:

- implementation landed
- acceptance criteria validated
- relevant tests/checks passed
- no unresolved regressions introduced by the card
- validation note added

## 10) Delegation prompt template

Read `backlog/A###.md` and `backlog/plans/A-###-<slug>.md` and implement exactly as specified.
Use minimal safe changes.
Preserve behavior outside scope.
Run the listed validation commands.
Report:

- changed files
- validation commands
- pass/fail summary
- regressions found/fixed

## 11) Validation note template

Validation Note (YYYY-MM-DD):

- Implemented files: ...
- Tests/checks run: ...
- Result: ...
- Regression checks: ...

## 12) ARC-specific pitfalls and safeguards

### Common pitfalls

- reintroducing direct `mcp_engine` imports into production ARC code
- letting docs say “MCP seam” while code still uses in-process imports
- adding verbose tool payload handling that increases token/cost pressure
- treating migrated `B###` cards as live queue items

### Safeguards

- run import-boundary grep checks on production ARC files
- centralize MCP client/session behavior in one seam
- keep transport normalization in one place
- keep active work in small cards with one matching plan each
- use the migrated backlog only for reference, not active scheduling
