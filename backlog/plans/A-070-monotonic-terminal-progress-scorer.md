# Plan: A-070 — monotonic terminal-progress scorer for goal distance

## Card metadata

- **Card:** A070
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A063, A066, A069

## Summary

Build a deterministic local scorer that turns object/goal evidence into terminal progress only when it is monotonic and causally plausible. This gives policy a stronger signal than "some object moved."

## Implementation approach

1. Add terminal-progress observations:
   - player component centroid/bounds
   - goal component centroid/bounds
   - distance to goal
   - level/score/env reward deltas
   - object-progress components
2. Maintain a short rolling window:
   - last 3 to 5 terminal-progress observations
   - monotonic distance improvement
   - oscillation/reversal detection
3. Compute score:
   - high confidence for env/level/score progress
   - moderate confidence for monotonic player-goal distance reduction
   - low/no confidence for one-off movement
   - negative/zero for oscillation and repeated no-op
4. Feed downstream:
   - populate `terminal_value_score`
   - populate `terminal_value_components`
   - expose `terminal_progress_trend`
   - feed A066 `meaningful_progress`
   - feed A069 contradiction/demotion logic
5. Keep it deterministic and local:
   - no LLM call
   - no MCP graph query in execute

## Concrete file additions/edits

- `agents/arc3/grid_analysis.py`
  - Add helper for role-aware component distance and trend inputs.
- `agents/arc3/solver.py`
  - Add or extend terminal-progress scoring structures.
- `agents/arc3/orchestrator.py`
  - Maintain rolling terminal-progress window and attach trace fields.
- `agents/arc3/runner.py`
  - Include terminal-progress fields in progress logs/live snapshots.
- `tests/test_a070_monotonic_terminal_progress.py`
  - New deterministic scorer fixtures.

## API/interface changes

Internal trace/result fields:

- `terminal_progress_trend`
- `terminal_goal_distance`
- `terminal_value_components.goal_distance`
- `terminal_value_components.monotonicity`
- `terminal_value_components.oscillation_penalty`

No external API changes.

## Graph model notes

Graph memory is not needed for the scorer itself. If persisted later, model terminal-progress evidence as compact provenance:

```text
(:ActionTrial {step, action_id})
(:ObjectState {role, color, centroid, bbox})
(:TerminalEvidence {distance_delta, monotonicity, score})

(:ActionTrial)-[:OBSERVED]->(:ObjectState)
(:ObjectState)-[:MEASURED_AS]->(:TerminalEvidence)
(:TerminalEvidence)-[:SUPPORTS|CONTRADICTS]->(:Hypothesis)
```

Do not query graph memory during execute for this scoring path.

## Tests to add or run

Add tests for:

- monotonic player-goal distance reduction increases terminal value
- one-off movement does not create high terminal value
- oscillation/reversal is classified as churn
- env/level/score progress overrides distance uncertainty
- terminal-progress fields appear in progress logs

Validation commands:

```bash
pytest -q tests/test_a070_monotonic_terminal_progress.py
pytest -q tests/test_a063_object_progress_scoring.py tests/test_a066_meaningful_progress_gate.py tests/test_a069_hypothesis_falsification.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Use Manhattan distance by default for grid movement, with Euclidean as optional metadata only if already available.
- Require at least two consecutive improvements before treating distance movement as meaningful terminal progress.
