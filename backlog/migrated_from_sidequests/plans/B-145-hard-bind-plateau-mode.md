# Plan for B145 — Hard-Bind Plateau Mode to the Top-Ranked Action Family

## Card Metadata

- **Card ID**: B145
- **Priority**: P0
- **Dependencies**: B144

## Summary

The current plateau-aware policy from B144 is visible in traces, but the latest verified qwen smoke shows that it does not yet override the generic `Explore` chunk or tightly constrain late-run action selection. This plan converts plateau mode from a soft ranking signal into an enforced late-run policy that binds execution to the top-ranked action family and keeps the trace, strategy summary, and actual actions consistent.

## Verified Baseline

From `live_qwen25_7b_b144verify_1775249453`:
- `PRIMARY ROLES: player=11, goal=9`
- `PLATEAU: sustained zero-reward streak (15 steps) with grounded entities`
- final chunk still reads `Explore: try unexplored action to gather more information [explore]`
- late steps still include a mixed family sequence across `ACTION3`, `ACTION4`, `ACTION6`, `ACTION7`
- final result remains `NOT_FINISHED`

Interpretation:
- grounding is working
- plateau detection is working
- remaining issue is that plateau mode does not yet *replace* exploration in the live control loop

## Technical Approach

### 1. Create a real plateau exploitation chunk in the solver

In `agents/arc3/solver.py`:
- when `plateau_mode=True`, build an explicit chunk whose source/reason references the top-ranked family
- ensure `strategy_summary` and returned chunk metadata no longer describe generic exploration
- preserve B144’s ranked-family scoring, but make the result authoritative for late-run policy text

### 2. Enforce family locking in the orchestrator

In `agents/arc3/orchestrator.py`:
- when plateau mode is active, prefer the top-ranked family as the default late-run action source
- allow at most one explicit fallback family or a small switch budget when fresh evidence appears
- prevent generic `Explore` fallback from reappearing unless the plateau policy is explicitly exhausted

### 3. Emit explicit exhaustion / unlock reasons

Add trace fields and decision reasons such as:
- `plateau_locked_family`
- `plateau_switch_budget_remaining`
- `plateau_unlock_reason`
- `plateau_family_exhausted`

This keeps the live trace auditable and explains exactly when the orchestrator deviates from the locked family.

### 4. Preserve ACTION6 compatibility

If the ranked family remains `ACTION6`:
- keep B143 coordinate inference intact
- preserve anti-clustering and geometry-aware coordinate sorting
- ensure the new family lock does not flatten coordinate-level intelligence

## Concrete File Changes

### `agents/arc3/solver.py`
- Replace generic explore chunk construction under plateau conditions with an exploitation chunk tied to the ranked family
- Keep summary/trace text synchronized with the ranked family and plateau reason

### `agents/arc3/orchestrator.py`
- Make plateau family lock authoritative during late-run selection
- Bound fallback switching and emit explicit unlock/exhaustion reasons

### `tests/test_arc3_solver.py`
- Add a regression asserting that plateau mode returns a non-explore chunk and summary text naming the ranked family
- Add a consistency regression for summary/trace alignment

### `tests/test_arc3_orchestrator.py`
- Add a regression ensuring late plateau steps stay on the locked family except for a bounded fallback
- Add a regression for explicit exhaustion behavior after the switch budget is consumed

## API / Schema / Test Updates

- No schema or tool changes expected
- No adapter allow-list changes expected
- No `docs/tool-catalog.md` updates expected
- Test surface remains inside ARC solver/orchestrator regressions

## Acceptance Criteria

- [ ] Plateau mode returns a real exploitation chunk rather than a generic `explore` chunk
- [ ] Final-step action selection stays on the top-ranked family or one explicit fallback unless fresh evidence appears
- [ ] Trace and strategy summary consistently name the locked family and plateau reason
- [ ] Explicit exhaustion/unlock reasons appear when the family lock is abandoned
- [ ] B143 `ACTION6` coordinate behavior remains intact when `ACTION6` is the locked family
- [ ] Relevant ARC tests pass with no regressions

## Validation Commands

```bash
.venv/bin/python -m pytest tests/test_arc3_solver.py -k 'plateau or ranked_family or explore' -q
.venv/bin/python -m pytest tests/test_arc3_orchestrator.py -k 'plateau or locked_family or fallback' -q
.venv/bin/python -m pytest tests/test_b119_bootstrap_discovery.py tests/test_b142_graduation_evidence.py tests/test_arc3_solver.py tests/test_arc3_orchestrator.py -q
```

For live verification after implementation:

```bash
# run_single_puzzle.py --real-api --num-puzzles 1 with qwen2.5:7b
# confirm the final strategy summary no longer shows a generic Explore chunk under plateau mode
# confirm late actions stay on the locked family or one explicit fallback
```

## Risks / Constraints

- The family lock must be strong enough to suppress churn without suppressing legitimate evidence-driven pivots
- Solver summary text and orchestrator behavior must remain in sync; this card should eliminate policy/trace mismatches rather than create new ones
- Must preserve B141 no-progress bail-out, B142 graduation controls, and B143 ACTION6 coordinate policy

## Done When

- Plateau mode visibly replaces generic exploration in both live trace and strategy summary
- Late-run action selection is tightly bounded and auditable
- New regressions lock in the behavior
- Focused and broader ARC suites pass