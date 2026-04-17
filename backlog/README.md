# ARC Backlog

This repository uses a small, opinionated backlog structure tailored for ARC_AGI.

Active work (A###)

- Active cards use `A###` identifiers and live in the repository root `backlog/`.
- Every active card MUST have exactly one matching plan file named `A-###-short-title.md` and placed in `backlog/plans/`.
- The active queue is tracked in `backlog/masterBacklogTracker.md` and governed by `backlog/BacklogRules.md`.

Migrated archive (B###)

- Historical SideQuests backlog material has been migrated to `backlog/migrated_from_sidequests/` and is archive/reference-only.
- Do not use `B###` artifacts as active work; they are read-only references.

How to add an active item

1. Create a new card file `backlog/A###.md` with card metadata (State, Priority, Layer, Plan).
2. Create a matching plan file `backlog/plans/A-###-short-title.md` (the numeric ID must match the card).
3. Add the card to `backlog/masterBacklogTracker.md` with a link to the plan path.

See also:

- [Backlog Rules](backlog/BacklogRules.md)
- [Master Backlog Tracker](backlog/masterBacklogTracker.md)

Validation (example commands)

```sh
find backlog -maxdepth 2 -type f | sort
# if you have ripgrep installed:
rg -n "A00[1-5]|A-00[1-5]" backlog || true
# fallback:
grep -R --line-number -E "A00[1-5]|A-00[1-5]" backlog || true
```
