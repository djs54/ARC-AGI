# Plan for B147 — Make Plateau Activation Sticky Under Confidence Jitter

## Card Metadata

- **Card ID**: B147
- **Priority**: P0
- **Dependencies**: B146

## Summary

B146 fixed the mismatch between plateau chunk text and the reported locked family, but the latest verified live qwen smoke shows that the plateau policy can still fail to activate entirely when player/goal grounding confidence jitters just under the hard threshold. This plan makes plateau activation sticky so the late-run policy remains engaged in the stalled scenarios it was built to handle.

## Verified Baseline

From `live_qwen25_7b_b146verify_1775256911`:
- `correct: false`
- `steps: 15`
- `final_state: NOT_FINISHED`
- final summary reverted to `CHUNK: Explore: try unexplored action to gather more information [explore]`
- no stable plateau lock is visible in the late-run summaries
- verified role state includes `player` confidence `0.6975`, just under the `>= 0.7` plateau gate, while `goal` remains strongly grounded

Interpretation:
- the lock logic from B146 exists
- but the late-run plateau gate is too brittle to confidence jitter
- the remaining issue is activation hysteresis, not lock consistency once active

## Technical Approach

### 1. Add plateau hysteresis / sticky activation

In `agents/arc3/solver.py`:
- keep the current strong entry signal, but once plateau mode has activated, allow it to remain active with a slightly lower sustain threshold
- alternatively, consider plateau eligibility satisfied if recent steps showed grounded player/goal evidence above threshold even if the current step dips slightly below it

Examples:
- enter threshold: `player >= 0.70` and `goal >= 0.70`
- sustain threshold: `player >= 0.65` and `goal >= 0.70`
- or maintain an `ever_grounded_recently` / `plateau_recently_active` flag across a small window

### 2. Prevent on/off flapping across adjacent steps

Ensure that once plateau mode becomes active during a stalled late run, tiny confidence drift does not drop the solver back to generic explore mode on the next step unless grounding clearly collapses.

### 3. Expose plateau activation source in trace/summary

Add additive trace/state fields such as:
- `plateau_activation_mode: direct | sticky`
- `plateau_grounding_reason`
- `plateau_recent_grounding_window`

This keeps the new hysteresis behavior auditable.

### 4. Add a regression for the exact borderline case

Create a regression where:
- zero-reward streak is already high
- goal is strongly grounded
- player confidence is just below the original threshold (for example `0.6975`)
- expected behavior: plateau mode still activates or remains active instead of dropping back to `explore`

## Concrete File Changes

### `agents/arc3/solver.py`
- Refine the `plateau_mode` gating logic to support sticky activation under minor confidence jitter
- Persist enough recent grounding context to avoid flapping
- Surface the activation reason in solve context / strategy summary if useful

### `tests/test_arc3_solver.py`
- Add a regression for the `0.6975` borderline grounded case
- Add a regression ensuring plateau mode remains active across minor confidence dips once activated

### `tests/test_arc3_orchestrator.py`
- Add or adjust solve-context propagation assertions if needed for the new plateau activation fields

## API / Schema / Test Updates

- No schema or tool changes expected
- No adapter allow-list changes expected
- No `docs/tool-catalog.md` updates expected
- Test surface remains limited to ARC solver/orchestrator regressions

## Acceptance Criteria

- [ ] Plateau mode activates or stays active during late stalled runs despite small confidence jitter around the player threshold
- [ ] Plateau mode no longer flaps on/off across adjacent late steps for borderline grounded states
- [ ] Trace output makes the sticky/hysteresis activation explicit and auditable
- [ ] The B146 live failure mode is reproduced by regression and prevented
- [ ] Existing B143, B145, and B146 behavior remains intact
- [ ] Relevant ARC tests pass with no regressions

## Validation Commands

```bash
.venv/bin/python -m pytest tests/test_arc3_solver.py -k 'plateau or sticky or hysteresis' -q
.venv/bin/python -m pytest tests/test_arc3_orchestrator.py -k 'plateau' -q
.venv/bin/python -m pytest tests/test_b119_bootstrap_discovery.py tests/test_b142_graduation_evidence.py tests/test_arc3_solver.py tests/test_arc3_orchestrator.py -q
```

For live verification after implementation:

```bash
# run_single_puzzle.py --real-api --num-puzzles 1 with qwen2.5:7b
# confirm late-run summaries no longer fall back to generic Explore solely because the player confidence dips slightly below 0.7
```

## Risks / Constraints

- Hysteresis should reduce flapping without making plateau mode trigger far too early
- The sticky rule must not mask genuine loss of grounding; it should tolerate only small jitter, not total collapse
- Must preserve B141 no-progress escalation, B142 graduation behavior, B143 coordinate policy, and B145/B146 plateau-lock consistency

## Done When

- Plateau mode reliably engages in the stalled late-run scenarios it is intended for
- The borderline-confidence live failure is covered by regression and no longer recurs
- Focused and broader ARC suites pass