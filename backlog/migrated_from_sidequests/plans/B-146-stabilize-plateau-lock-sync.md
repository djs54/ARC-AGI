# Plan for B146 — Stabilize Plateau Lock and Keep Chunk/Action/Trace in Sync

## Card Metadata

- **Card ID**: B146
- **Priority**: P0
- **Dependencies**: B145

## Summary

B145 successfully replaced the old generic `Explore` chunk with a real `plateau_exploitation` chunk, but the latest verified live qwen smoke shows that the plateau lock is still not stable or internally consistent. This plan turns the lock from a mostly local override into a single authoritative late-run state shared by the solver summary, active chunk, and orchestrator execution path.

## Verified Baseline

From `live_qwen25_7b_b145verify_1775255395`:
- `correct: false`
- `steps: 15`
- `final_state: NOT_FINISHED`
- final strategy summary includes:
  - `CHUNK: Plateau Exploitation: commit to top-ranked ACTION2 [plateau_exploitation]`
  - `LOCKED FAMILY: ACTION4`
- late executed actions still include mixed families rather than a stable lock

Interpretation:
- plateau chunk replacement is now working
- but the authoritative late-run family is not being persisted/synchronized cleanly
- the remaining issue is consistency, not initial plateau detection

## Technical Approach

### 1. Persist one authoritative locked family

In `agents/arc3/solver.py` and/or orchestrator state:
- introduce a single `plateau_locked_family` field that is set once when plateau mode first becomes active
- avoid recomputing the displayed locked family independently from later ranked-family lists unless an unlock condition is explicitly met

### 2. Add explicit unlock / relock rules

Only change `plateau_locked_family` when one of these occurs:
- new evidence materially outranks the current family by a defined threshold
- the current family is explicitly exhausted (`plateau_family_exhausted=True`)
- action availability removes the locked family from the available set

The reason must be emitted visibly in trace output.

### 3. Keep solver summary and orchestrator action path synchronized

Ensure these all point at the same family during plateau mode:
- `active_chunk.description`
- `active_chunk.estimated_actions`
- `strategy_summary` / `LOCKED FAMILY`
- orchestrator override logic in `_enforce_action_policy()`

This eliminates the current mismatch where the chunk claims one family and the trace/action path advertises another.

### 4. Add regression coverage for the live mismatch

Add a failing regression that reproduces the observed inconsistency pattern:
- plateau mode active
- plateau chunk says one family
- later ranking or fallback tries to drift to another family without an explicit unlock reason

Expected behavior:
- the family remains stable, or the unlock is explicit and auditable.

## Concrete File Changes

### `agents/arc3/solver.py`
- Add/persist authoritative plateau lock metadata in solve context
- Make `strategy_summary` and chunk text read from that single stored family

### `agents/arc3/orchestrator.py`
- Prefer the stored `plateau_locked_family` over opportunistic reranking during late-run action selection
- Emit explicit unlock/relock trace fields and reasons

### `tests/test_arc3_solver.py`
- Add a regression asserting the chunk description and summary lock text stay aligned once plateau mode starts

### `tests/test_arc3_orchestrator.py`
- Add a regression ensuring actual late-step executed actions remain on the locked family unless an explicit unlock reason is triggered

## API / Schema / Test Updates

- No schema or tool changes expected
- No adapter allow-list changes expected
- No `docs/tool-catalog.md` updates expected
- Test surface stays inside ARC solver/orchestrator regressions

## Acceptance Criteria

- [ ] Plateau lock is stored in one authoritative field and reused across later steps
- [ ] Summary text, active chunk, and executed actions stay aligned on the same family
- [ ] Any plateau family change emits an explicit unlock/relock reason in trace output
- [ ] A regression reproduces and prevents the B145 live mismatch
- [ ] B143 `ACTION6` coordinate policy remains intact when `ACTION6` is the locked family
- [ ] Relevant ARC tests pass with no regressions

## Validation Commands

```bash
.venv/bin/python -m pytest tests/test_arc3_solver.py -k 'plateau or locked_family or sync' -q
.venv/bin/python -m pytest tests/test_arc3_orchestrator.py -k 'plateau or locked_family or unlock' -q
.venv/bin/python -m pytest tests/test_b119_bootstrap_discovery.py tests/test_b142_graduation_evidence.py tests/test_arc3_solver.py tests/test_arc3_orchestrator.py -q
```

For live verification after implementation:

```bash
# run_single_puzzle.py --real-api --num-puzzles 1 with qwen2.5:7b
# confirm the final summary no longer reports mismatched chunk and locked family values
# confirm late actions remain on the locked family unless an explicit unlock reason appears
```

## Risks / Constraints

- The family lock must stay strong without blocking legitimate evidence-driven pivots
- The solver and orchestrator must share one source of truth rather than each deriving a separate lock view
- Must preserve B141 no-progress bail-out, B142 graduation controls, B143 coordinate policy, and B145 plateau chunk replacement

## Done When

- Live plateau mode shows one coherent locked family across chunk text, summary, trace, and executed actions
- The B145 mismatch is covered by regression and no longer reproducible
- Focused and broader ARC suites pass