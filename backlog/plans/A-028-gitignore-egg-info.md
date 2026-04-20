# A-028 - Ignore `*.egg-info/` Build Artifacts

## Card metadata

- Card: A028
- Priority: P3
- Layer: repo hygiene
- Depends on: none

## Summary

Append `*.egg-info/` to `.gitignore` so editable-install artifacts stop polluting `git status`.

## Implementation approach

### 1. Append the pattern

In `.gitignore`, add `*.egg-info/` after the `*.pyc` line. The glob covers `arc_agi_sidequests.egg-info/` and any future distribution name.

### 2. Verify

```sh
git check-ignore -v arc_agi_sidequests.egg-info
git status
```

Expected: the egg-info directory disappears from `git status` untracked listing.

## Concrete file additions/edits

- edit `.gitignore`:
  - add `*.egg-info/` after the `__pycache__/ ... *.pyc` block.

## API/interface changes

None.

## Tests to add or run

None — hygiene-only.

## Validation commands

```sh
git status --porcelain | grep egg-info || echo "OK — egg-info ignored"
```

Expected: `OK — egg-info ignored`.

## Assumptions/defaults

- `*.egg-info/` matches at any depth. No existing tracked file carries that suffix in this repo.
