# Plan: A-067 — multi-action macro eligibility with terminal-stall stop

## Card metadata

- **Card:** A067
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A061, A066

## Summary

Generalize macro eligibility beyond one-action games. A repeated action can enter macro mode if it has deterministic meaningful progress and low contradiction risk. The macro must stop quickly when progress is terminally flat.

## Implementation approach

1. Replace the current hard gate:
   - old: `len(available_actions) == 1`
   - new: dominant-action criteria when multiple actions are available
2. Dominant-action criteria:
   - same action selected at least `min_confirming_steps`
   - observed effect class is stable
   - `meaningful_progress=true`
   - no recent guard blocks for the action
   - no active hypothesis contradiction against the action
3. Macro stop criteria:
   - `meaningful_progress=false`
   - `terminal_value_score` flat for `N` macro steps
   - object-progress score flat or declining
   - repeated frame hash
   - action no longer legal
   - max macro steps reached
4. Add trace fields:
   - `macro_eligibility_reason`
   - `macro_evidence_window`
   - `macro_stop_reason`
   - `macro_terminal_stall_count`
5. Ensure macro mode respects A064/A068 memory firewall behavior.

## Concrete file additions/edits

- `agents/arc3/orchestrator.py`
  - Update `check_macro_eligibility`.
  - Add helper for dominant-action evidence windows.
- `agents/arc3/runner.py`
  - Pass `meaningful_progress`, object progress, and terminal scores into macro stop checks.
- `agents/arc3/solver.py`
  - Surface action-family confidence in a compact form if already available.
- `tests/test_a067_multi_action_macro_eligibility.py`
  - Add multi-action eligibility and stall-stop fixtures.

## API/interface changes

Internal fields only:

- `macro_eligibility_reason`
- `macro_evidence_window`
- `macro_terminal_stall_count`

No external API changes.

## Tests to add or run

Add tests for:

- multi-action available set can still enter macro when one action dominates
- pixel-churn-only action cannot enter macro
- macro stops on terminal-value stall
- macro stops on repeated frame hash
- macro traces preserve stop reason

Validation commands:

```bash
pytest -q tests/test_a067_multi_action_macro_eligibility.py
pytest -q tests/test_a061_single_action_macro_executor.py tests/test_a066_meaningful_progress_gate.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Default `min_confirming_steps=2`.
- Default macro stall window should be short, e.g. 2 to 3 macro steps, until live data proves a longer run is safe.
