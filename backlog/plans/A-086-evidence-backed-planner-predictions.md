# Plan: A-086 — evidence-backed planner predictions and falsification

## Card metadata

- **Card:** A086
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A073, A074, A077, A080

## Summary

Make planner candidates carry operational scientific content: what should happen, what would disprove the hypothesis, and which graph evidence supports the experiment.

Graph-solution classification: LPG query/modeling. Use bounded traversal from current `Game/State` to recent `Action`, `Effect`, `Hypothesis`, and `Mechanic` nodes. Avoid global action supernodes.

## Implementation approach

1. Add graph helpers for recent action-effect summaries by action id.
2. Generate candidate predictions:
   - expected effect class
   - expected object/terminal progress
   - expected coordinate relevance
3. Generate falsification conditions:
   - no change after N probes
   - no terminal-distance improvement
   - repeated frame hash
   - contradictory object movement
4. Rank candidates:
   - predicted + falsifiable
   - falsifiable only
   - generic untested probe
5. Emit evidence paths compactly for prompt/eval.

## Concrete file additions/edits

- `agents/arc3/world_model_planner.py`
  - Add prediction/falsification generation and ranking.
- `agents/arc3/world_model.py`
  - Add bounded query helpers for recent action/effect evidence.
- `agents/arc3/world_model_compiler.py`
  - Ensure effect claims expose fields planner needs.
- `agents/arc3/orchestrator.py`
  - Preserve selected candidate prediction/falsification in trace.
- `benchmarks/arc3/world_model_eval.py`
  - Summarize prediction/falsification rates.
- `tests/test_a086_evidence_backed_planner_predictions.py`
  - Add fixture graph tests.

## API/interface changes

Planner candidate fields become meaningful:

```json
{
  "predicted_observation": "ACTION3 should move player toward goal",
  "falsification_condition": "if terminal distance stays flat for 2 attempts",
  "evidence_path": "State->Action->Effect->Hypothesis"
}
```

## Tests to add or run

```bash
pytest -q tests/test_a086_evidence_backed_planner_predictions.py
pytest -q tests/test_a077_world_model_guided_planner.py tests/test_a080_world_model_eval_controller_metrics.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Predictions should be compact text plus machine-readable effect class where possible.
- Keep query depth bounded to recent episode evidence.
- Do not require aggregate memory priors; per-game graph evidence is enough for this card.
