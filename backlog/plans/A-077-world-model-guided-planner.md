# Plan: A-077 — world-model-guided planner

## Card metadata

- **Card:** A077
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A074, A075, A076

## Summary

Implement a planner that chooses evidence-backed experiments from the current world model and aggregate mechanic priors. This turns GPT-style reasoning into testable commitments.

Graph-solution classification: graph is the right planning substrate because candidates depend on traversals from hypotheses to predictions, actions, effects, contradictions, and mechanic priors. Use bounded LPG traversal over the in-memory world model.

## Implementation approach

1. Create `agents/arc3/world_model_planner.py`.
2. Define:
   - `PlanCandidate`
   - `PlanMode`: `exploit`, `probe`, `falsify`, `recover`, `terminate`
   - `PlanSelection`
3. Candidate fields:
   - action id and args
   - target hypothesis/mechanic
   - predicted observation
   - falsification condition
   - expected information gain
   - expected terminal/object progress
   - cost estimate
   - risk
   - evidence path summary
4. Generate candidates from:
   - active hypotheses with untested predictions
   - contradicted hypotheses that need recovery
   - mechanic priors with matching action/effect signatures
   - untested relevant action-effect edges
   - single-action stall recovery policy
5. Rank candidates:
   - prefer terminal/object progress evidence
   - prefer high information gain when uncertain
   - penalize demoted hypotheses
   - penalize pure pixel churn
   - penalize repeated identical experiments unless cheap probe batch is active
6. Integrate with orchestrator:
   - planner returns structured candidate
   - orchestrator executes or asks LLM only to refine bounded candidates
   - selected candidate’s prediction is written before action execution
7. Keep prompts compact:
   - include only top candidates and evidence paths
   - avoid raw histories when world-model summary exists

## Concrete file additions/edits

- `agents/arc3/world_model_planner.py`
  - New planner and candidate dataclasses.
- `agents/arc3/orchestrator.py`
  - Use planner output for route/execute decisions.
- `agents/arc3/solver.py`
  - Score planner candidates with terminal-grounded reward policy.
- `agents/arc3/prompts.py`
  - Add compact planner-candidate prompt section.
- `agents/arc3/runner.py`
  - Export selected candidate and evidence path in traces/results.
- `tests/test_a077_world_model_guided_planner.py`
  - New focused tests.

## API/interface changes

Internal API:

```python
selection = planner.select_next_candidate(
    world_model=...,
    mechanic_priors=...,
    available_actions=...,
    budget_state=...,
)
```

Trace additions:

- `planner_mode`
- `planner_selected_candidate`
- `planner_candidate_count`
- `planner_evidence_path`
- `predicted_observation`
- `falsification_condition`

## Starter traversal

```text
For each active hypothesis:
  Hypothesis -> PREDICTS -> EffectPattern
  EffectPattern <- CAUSED - Action
  Action -> prior observations in current Game
  Filter out actions whose recent effects contradict terminal relevance.
  Score candidate by expected information gain and terminal/object progress.
```

Bound the traversal to current game and top mechanic priors. Do not traverse from global action hubs without a game/mechanic filter.

## Tests to add or run

Add tests for:

- planner returns bounded candidate list
- selected candidate includes prediction and falsification condition
- demoted hypothesis is penalized
- mechanic prior can produce recovery candidate
- single-action stall produces terminate or cheap-probe candidate
- selected candidate trace has evidence path

Validation commands:

```bash
pytest -q tests/test_a077_world_model_guided_planner.py
pytest -q tests/test_a076_evidence_gated_reasoning_controller.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Planner should be deterministic for fixtures.
- LLM can help generate candidate hypotheses, but final candidate selection should be graph/evidence scored.
