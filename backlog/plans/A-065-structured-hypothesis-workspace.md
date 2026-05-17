# Plan: A-065 — structured hypothesis workspace for deeper ARC reasoning

## Card metadata

- **Card:** A065
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A061, A062, A063, A064

## Summary

Add a compact, auditable reasoning workspace for ARC puzzles. The workspace should help the agent reason more like a strong solver: hold multiple hypotheses, predict what should happen, test those predictions, and demote contradicted beliefs. It should not make every execution step slower.

Graph-solution classification: this is a strong fit for a labeled property graph because hypotheses, actions, observations, predictions, evidence, and outcomes are relationship-heavy, and the agent needs explainable provenance paths. Use a local bounded hypothesis graph in runtime and persist compact summaries through the MCP seam only at safe boundaries.

## Implementation approach

1. Define a `HypothesisWorkspace` structure:
   - hypothesis id
   - statement
   - scope: rule, action-effect, coordinate-causality, object-role, victory-condition
   - confidence
   - evidence for
   - evidence against
   - predicted next observation
   - falsification condition
   - status: active, demoted, confirmed, retired
2. Add update points:
   - after `hypothesize`
   - after `route`
   - after `evaluate`
   - after macro exit summary
3. Add contradiction handling:
   - if observed evidence violates prediction, reduce confidence and add evidence-against
   - if confidence falls below threshold, demote and prevent immediate reuse
4. Add prompt compaction:
   - top 3 active hypotheses
   - top 2 contradicted hypotheses with why they were demoted
   - next falsifying experiment
5. Integrate with A062:
   - coordinate-causality hypotheses consume `args_effective` evidence
   - targeted-coordinate hypothesis should be demoted when coordinate relevance is low
6. Integrate with A063:
   - object-progress evidence updates rule/action hypotheses
7. Keep execute hot path clean:
   - no per-action LLM calls just to update the workspace
   - macro mode emits one summarized workspace update after exit
8. Model workspace as a bounded graph:
   - nodes for hypotheses, observations, actions, predictions, evidence, outcomes
   - edges for supports, contradicts, predicts, observed, caused, demotes
   - stable ids based on task/session/step/hypothesis fingerprint
   - hard caps on active/demoted nodes included in prompts

## Concrete file additions/edits

- `agents/arc3/solver.py`
  - Define workspace dataclasses/helpers or extend existing hypothesis manager if cleaner.
- `agents/arc3/orchestrator.py`
  - Own workspace lifecycle, update points, and trace emission.
- `agents/arc3/prompts.py`
  - Add compact workspace section to model/replan prompts.
- `agents/arc3/runner.py`
  - Preserve workspace summaries in progress logs/final trace.
- `benchmarks/arc3/trajectory_eval.py`
  - Optionally score prediction/evidence consistency.
- `tests/test_a065_hypothesis_workspace.py`
  - New focused tests for hypothesis creation, demotion, compaction, and macro summary update.

## API/interface changes

- No external API changes.
- Add optional internal trace fields:
  - `hypothesis_workspace_summary`
  - `active_hypotheses`
  - `demoted_hypotheses`
  - `predicted_observation`
  - `falsification_result`

## Graph model notes

Recommended model: labeled property graph.

Starter local schema:

```text
(:Hypothesis {id, scope, statement, confidence, status})
(:Prediction {id, expected_observation, falsification_condition})
(:Observation {step, scene_hash, summary})
(:Action {action_id, args_effective})
(:Evidence {kind, summary, confidence})
(:Outcome {state, env_reward, object_progress_score})

(:Hypothesis)-[:PREDICTS]->(:Prediction)
(:Prediction)-[:TESTED_BY]->(:Action)
(:Action)-[:OBSERVED_AS]->(:Observation)
(:Observation)-[:SUPPORTS|CONTRADICTS]->(:Hypothesis)
(:Evidence)-[:DEMOTES|CONFIRMS]->(:Hypothesis)
(:Action)-[:ENDED_WITH]->(:Outcome)
```

Starter bounded query shape:

```text
Given current task/archetype/action/scene_hash, retrieve at most 3 hypotheses
within 2 hops that were confirmed or contradicted in similar scene/action
contexts. Never expand from unfiltered color-only or action-only hubs.
```

Testing rule: add deterministic fixture graphs for coordinate-causality contradiction and macro-progress confirmation.

## Tests to add or run

Add tests for:

- workspace creates competing hypotheses
- prediction is recorded before an exploratory action
- contradiction demotes a hypothesis
- coordinate relevance demotes targeted-coordinate hypothesis
- macro exit emits one summarized workspace update
- prompt summary remains bounded

Validation commands:

```bash
pytest -q tests/test_a065_hypothesis_workspace.py
pytest -q tests/test_a062_coordinate_relevance.py tests/test_a063_object_progress_scoring.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- This card should land after the cheap execution controls. Deep reasoning should be invoked when it can matter, not on every deterministic action.
- The workspace is local to ARC runtime and uses the MCP seam only for optional memory context; it must not import SideQuests internals.
