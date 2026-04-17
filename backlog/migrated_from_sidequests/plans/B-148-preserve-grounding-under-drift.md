# Plan for B148 — Preserve Grounded Player/Goal and Victory Confidence Under Late-Run Drift

## Card Metadata

- **Card ID**: B148
- **Priority**: P0
- **Dependencies**: B147

## Summary

B147 addressed plateau activation jitter near the grounding threshold, but the latest verified live smoke shows that the run can still degrade into low-confidence `Explore` mode because the underlying player/goal/victory grounding collapses too far during noisy later steps. This plan makes late-run grounding more persistent and contradiction-aware so the solver keeps its strongest recent interpretation unless there is real evidence to overturn it.

## Verified Baseline

From `live_qwen25_7b_b147verify_1775257893`:
- `correct: false`
- `steps: 15`
- `final_state: NOT_FINISHED`
- final summary reverts to `CHUNK: Explore: try unexplored action to gather more information [explore]`
- final role confidences collapse to:
  - `player = 0.45`
  - `goal = 0.35`
- `final_victory_confidence = 0.35`

Interpretation:
- B147’s hysteresis logic is present
- but the stronger issue is that grounding itself is not being preserved under late-run drift
- the next fix is not a new plateau rule; it is making grounding and victory belief more resilient once already established

## Technical Approach

### 1. Preserve recent-best grounded roles

In `agents/arc3/solver.py`:
- keep a recent-best view of player/goal role assignments and their positions/confidences
- allow low-confidence refreshes to be ignored or damped if they do not provide explicit contradictory evidence

Examples:
- preserve a `player` role at moderate-to-high confidence when a later refresh only weakly suggests `unknown`
- prefer consistency over opportunistic reassignment during late stalled runs

### 2. Make demotion contradiction-aware

Only demote a grounded role when one of the following is true:
- a new competing role has materially stronger evidence
- the entity’s position/behavior explicitly contradicts the old assignment
- repeated evidence over multiple steps undermines the old interpretation

This prevents single noisy late frames from undoing usable grounding.

### 3. Stabilize victory-condition confidence

Apply similar persistence to the current `reach_goal` interpretation:
- avoid large late-run confidence drops unless the run observes evidence inconsistent with that objective
- keep the solve policy aligned with the strongest recent win hypothesis

### 4. Add regression for the observed late-run collapse

Create a regression where:
- a player/goal/victory condition is previously established at usable confidence
- later steps introduce noisy, lower-confidence refreshes without explicit contradiction
- expected behavior: the solver preserves the earlier grounded interpretation strongly enough to avoid dropping back into generic `Explore`

## Concrete File Changes

### `agents/arc3/solver.py`
- Add recent-best persistence or smoothing for grounded roles and victory confidence
- Refine `_merge_persistent_roles()` and related late-step solve logic to prefer contradiction-aware demotion
- Keep strategy summary aligned with the preserved grounded state

### `tests/test_arc3_solver.py`
- Add a regression for late-run role-confidence collapse without contradiction
- Add a regression ensuring the existing victory hypothesis remains stable under noisy refreshes

### `tests/test_arc3_orchestrator.py`
- Add or adjust solve-summary propagation assertions if needed

## API / Schema / Test Updates

- No schema or tool changes expected
- No adapter allow-list changes expected
- No `docs/tool-catalog.md` updates expected
- Test surface stays within ARC solver/orchestrator regressions

## Acceptance Criteria

- [ ] Previously grounded player/goal assignments persist through noisy late frames unless real contradiction appears
- [ ] Current victory-condition confidence does not collapse sharply without explicit contradictory evidence
- [ ] The B147 live failure mode is reproduced by regression and prevented
- [ ] Late-run strategy summaries remain grounded enough to support plateau/exploitation policy instead of reverting to generic `Explore`
- [ ] Existing B143 and B145–B147 behaviors remain intact
- [ ] Relevant ARC tests pass with no regressions

## Validation Commands

```bash
.venv/bin/python -m pytest tests/test_arc3_solver.py -k 'grounding or persistent or victory or plateau' -q
.venv/bin/python -m pytest tests/test_arc3_orchestrator.py -k 'solve or plateau' -q
.venv/bin/python -m pytest tests/test_b119_bootstrap_discovery.py tests/test_b142_graduation_evidence.py tests/test_arc3_solver.py tests/test_arc3_orchestrator.py -q
```

For live verification after implementation:

```bash
# run_single_puzzle.py --real-api --num-puzzles 1 with qwen2.5:7b
# confirm late-run role/victory confidence remains stable enough to keep the solver out of generic Explore collapse
```

## Risks / Constraints

- Persistence should prevent noise-driven collapse without making stale beliefs impossible to replace
- The demotion policy must tolerate real contradictory evidence and not over-freeze wrong roles
- Must preserve B141 no-progress escalation, B142 graduation behavior, B143 coordinate policy, and B145–B147 plateau logic

## Done When

- Late-run grounding no longer collapses on noise alone
- The verified live collapse pattern is covered by regression and no longer recurs
- Focused and broader ARC suites pass