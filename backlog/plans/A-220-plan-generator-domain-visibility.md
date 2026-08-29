# Plan: A220 — Surface Cynefin Domain in `plan_generator.py` Candidate Metadata (Part 3, v1 Slice)

## Card metadata

- ID: A220
- Priority: P2
- Layer: ARC runtime
- Dependencies: A217 (`classify_domain()`)

## Summary

Reuse A217's already-built `classify_domain()` inside `plan_generator.py::_build_candidates`'s existing entity-neighborhood evidence fetch, adding the result to candidate metadata for visibility. No scoring change — purely additive telemetry, matching A219's "compute and surface first" discipline.

## Technical approach

### Step 1: Read the current state first

1. `backlog/A216.md` Part 3 (Cynefin research) and `backlog/A217.md` (the already-shipped `classify_domain()`/`CynefinDomain`) in full.
2. `agents/arc4/plan_generator.py::_build_candidates` in full — find the exact entity-neighborhood block (search for `fetch_entity_neighborhood`, `entity_neighborhood_grounded`, currently around where A192/A208's logic lives) and confirm exactly what evidence shape (`neighborhood.get("hypotheses", [])`/`.get("rules", [])`) is already being fetched there before writing any code.
3. `agents/arc4/annatar_state_machine.py::classify_domain` — confirm its exact signature (`Sequence[Mapping[str, Any]] -> CynefinDomain`) and import it correctly (`from .annatar_state_machine import classify_domain, CynefinDomain`).

### Step 2: Write the regression test FIRST

Before adding the new field, write a test that captures the *current* candidate `score` for a `COMPLEX`-shaped entity-neighborhood fixture (disagreeing live rules/hypotheses) and a `CONVERGED`-shaped one, asserting specific score values. This is your regression guard — confirm it passes against the *unmodified* code first, so you have a real baseline to prove step 3 doesn't change it.

### Step 3: Add the `classify_domain()` call and metadata field

In the entity-neighborhood block, alongside the existing `entity_neighborhood_grounded` computation, add:
```python
domain = classify_domain(neighborhood.get("hypotheses", []) + neighborhood.get("rules", []))
```
using the same combined list shape A217's `compute_cycle_signals` uses (full list, not pre-filtered to live-only — `classify_domain` does its own filtering internally). Add `metadata["cynefin_domain"] = domain.value` to the candidate's metadata dict at the point where `entity_neighborhood_grounded` and other metadata keys are already being set for this candidate.

Re-run Step 2's regression test — confirm `score` is still byte-for-byte identical. If it isn't, something touched scoring by accident — fix it before proceeding, do not adjust the test to match a changed score.

### Step 4: New tests for the metadata field itself

- A candidate whose entity has disagreeing live evidence gets `metadata["cynefin_domain"] == "complex"`.
- A candidate whose entity has agreeing live evidence gets `metadata["cynefin_domain"] == "converged"`.
- A candidate with no entity-neighborhood evidence at all (fresh, untested, or `graph_port is None`) gets `metadata["cynefin_domain"] == "disorder"`.
- A non-`ACTION6` candidate (no `entity_ref`, doesn't hit this code path at all) — confirm it either doesn't get the key at all, or gets a sensible default; check what `entity_neighborhood_grounded` itself does in this case and mirror that convention exactly for consistency.

### Step 5: Confirm `_voi_bonus` and scoring are untouched

`git diff --stat` on `agents/arc4/plan_generator.py` should show changes localized to the entity-neighborhood block only — not `_voi_bonus`, not `_candidate_sort_key`, not any other scoring function. Re-run this repo's existing VOI/entity-neighborhood test files (search `tests/` for `_voi_bonus` and `test_a192_entity_neighborhood_candidate_seeding.py`) and confirm they pass unchanged.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | `_build_candidates`'s entity-neighborhood block gains a `classify_domain()` call and `metadata["cynefin_domain"]` |
| `tests/test_a220_plan_generator_domain_visibility.py` (new) | Coverage per Steps 2-4 |

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a220_plan_generator_domain_visibility.py -v
.venv/bin/python -m pytest tests/ -q
make test-a
make test-all
git diff --stat  # confirm changes are localized to the entity-neighborhood block, not scoring functions
```

## Assumptions/defaults

- If `_voi_bonus`'s existing agree/disagree computation is trivially replaceable with a `classify_domain()` call in the *same* pass (since both read the same `rules` list), it's tempting to unify them here — **do not**. `_voi_bonus` currently only reads `rules` (not `hypotheses`), and `classify_domain` reads both — unifying them changes `_voi_bonus`'s actual behavior (a scoring change), which is explicitly out of scope for this card. Leave `_voi_bonus` exactly as it is; add `classify_domain()` as a second, independent computation.
- Same "no empirical basis yet" honesty convention as every other new signal added tonight — this card adds pure visibility, so there's no threshold to calibrate, but if the follow-up card ever turns this into a scoring change, it should carry that same explicit-interim-value discipline.
