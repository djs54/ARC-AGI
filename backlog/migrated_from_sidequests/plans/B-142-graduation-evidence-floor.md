# Plan for B142 — Chunk Graduation Must Respect Evidence Floor

## Card Metadata

- **Card ID**: B142
- **Priority**: P0
- **Dependencies**: None (B141 provides defense-in-depth)

## Summary

Chunk graduation produces 0.87 despite `evidence = 0.10` and `chunk_progress = 0.00` because high player/goal confidence dominates the formula. This inflated score prevents `dissonance_detected` from ever becoming `True`, which blocks replanning. This plan adds an evidence floor and progress-decay penalty to graduation scoring.

## Verified Baseline

From `live_gemma4_e4b_timeout_1775221087`:
- Graduation score: ~0.87 across all 15 steps
- Evidence: 0.10
- Chunk progress: 0.00
- `dissonance_detected`: `False` for entire run
- Player confidence: 0.90, Goal confidence: 0.83, Coverage: 1.00
- The high confidence scores masked zero empirical evidence

## Technical Approach

### 1. Add evidence floor to graduation scoring

In the graduation scoring function within `agents/arc3/solver.py`, add a gate:

```python
# Evidence floor: if no empirical evidence after enough steps, cap graduation
if (evidence < 0.3 and chunk_progress == 0.0 and steps_using_chunk >= 3):
    max_allowed = max(0.4, evidence * 2)
    graduation_score = min(graduation_score, max_allowed)
    capped_reason = "evidence_floor"
```

This ensures that theoretical confidence (player/goal identification) cannot keep graduation high when the agent has zero proof the plan works.

### 2. Add progress-decay penalty

Per consecutive zero-reward step while the chunk is active, apply a decay:

```python
decay = 0.05 * consecutive_zero_reward_steps
graduation_score = max(0.2, graduation_score - decay)
```

This creates urgency: even a well-scored chunk degrades if nothing works.

### 3. Wire graduation drop → dissonance trigger

In the orchestrator, after receiving the updated graduation score:

```python
if graduation_score < 0.5 and not self._solve_context.get("dissonance_detected"):
    self._solve_context["dissonance_detected"] = True
    self._trace_event("graduation_drop_dissonance",
                      graduation_score=graduation_score,
                      reason=capped_reason or "progress_decay")
```

### 4. Add trace fields

The graduation scoring function must return (or attach to context) these additional fields:
- `graduation_capped_reason`: "evidence_floor" | "progress_decay" | None
- `evidence_floor_applied`: bool
- `progress_decay_applied`: float (the decay amount)
- `pre_cap_graduation_score`: float (original score before capping)

## Concrete File Changes

### `agents/arc3/solver.py`
- Locate the graduation scoring function (likely `_compute_graduation_score` or similar)
- Add evidence floor gate after the main scoring formula
- Add progress-decay penalty calculation
- Return/attach the new trace fields alongside the score

### `agents/arc3/orchestrator.py`
- After receiving graduation score from solver, check if it dropped below 0.5
- If so, set `dissonance_detected = True` with trace event
- Ensure the new trace fields from solver are forwarded to step trace output

### `tests/test_b142_graduation_evidence.py` (new)
- Test evidence floor: high confidence + low evidence + zero progress → graduation capped at ≤ 0.4
- Test progress decay: graduation decays by 0.05 per zero-reward step
- Test dissonance trigger: graduation < 0.5 → `dissonance_detected = True`
- Test no false positives: high evidence (≥ 0.3) + progress > 0 → graduation not capped
- Test floor clamp: graduation never goes below 0.2 even with extreme decay

## API/Schema/Test Updates

- No tool catalog changes
- No adapter allow-list changes
- No schema changes
- Trace output gains new fields (additive, non-breaking)

## Acceptance Criteria

- [ ] Chunk with `evidence < 0.3` and `chunk_progress == 0.0` after 3+ steps has graduation ≤ 0.4
- [ ] Graduation decays 0.05 per consecutive zero-reward step (visible in trace)
- [ ] When graduation drops below 0.5, `dissonance_detected` becomes `True`
- [ ] Trace fields include `graduation_capped_reason` and `progress_decay_applied`
- [ ] High-evidence chunks (evidence ≥ 0.3, progress > 0) are NOT penalized
- [ ] Existing test suites pass with no regressions

## Validation Commands

```bash
.venv/bin/python -m pytest tests/test_b142_graduation_evidence.py -q
.venv/bin/python -m pytest tests/test_arc3_solver.py -q
.venv/bin/python -m pytest tests/ -q --timeout=60
```

## Risks / Constraints

- Must locate the exact graduation scoring function in solver.py — may be named differently or computed inline
- The 0.3 evidence threshold and 0.05 decay rate are starting values — may need tuning after live smoke
- Progress-decay must only apply to consecutive zero-reward steps on the *same chunk* — chunk switches should reset the decay

## Done When

- Evidence floor caps graduation in unit tests
- Progress decay degrades graduation over time in unit tests
- Dissonance triggers when graduation drops below threshold
- No regressions in existing solver or orchestrator tests
