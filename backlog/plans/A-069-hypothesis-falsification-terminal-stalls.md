# Plan: A-069 — hypothesis falsification from terminal stalls

## Card metadata

- **Card:** A069
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A065, A066

## Summary

Teach the hypothesis workspace to demote beliefs when their predictions fail. This card focuses on terminal stalls: repeated action evidence that changes pixels but fails to improve environment reward, levels, score, terminal value, or monotonic goal progress.

Graph-solution classification: this is a local reasoning/provenance graph. The runtime should maintain edges like `PREDICTS`, `TESTED_BY`, `OBSERVED_AS`, `SUPPORTS`, and `CONTRADICTS` so policy can audit why a hypothesis was demoted.

## Implementation approach

1. Add prediction records:
   - hypothesis id
   - action/action family under test
   - expected terminal/object observation
   - falsification window
2. Add terminal-stall contradiction rules:
   - if action repeated `N` times with `meaningful_progress=false`, add evidence-against
   - if object progress is non-monotonic or flat while hypothesis predicts goal approach, add evidence-against
   - if terminal value remains flat after predicted improvement window, demote or reduce confidence
3. Update reinforcement rules:
   - object progress alone can support a hypothesis once or weakly
   - repeated object progress must be monotonic and terminal-relevant to keep increasing confidence
4. Connect demotion to policy:
   - demoted action-effect hypotheses should block immediate exploitation of that action family
   - demoted victory hypotheses should force route/model reconsideration
5. Trace compact evidence:
   - `hypothesis_prediction`
   - `hypothesis_contradiction`
   - `hypothesis_demoted`
   - `demotion_reason`

## Concrete file additions/edits

- `agents/arc3/orchestrator.py`
  - Update workspace update rules after evaluate/perceive.
  - Add terminal-stall contradiction logic.
- `agents/arc3/solver.py`
  - Surface demoted action/victory hypotheses into route policy.
- `benchmarks/arc3/trajectory_eval.py`
  - Optionally reward useful demotion/escalation quality.
- `tests/test_a069_hypothesis_falsification.py`
  - New contradiction and demotion fixtures.

## API/interface changes

Internal trace fields:

- `hypothesis_prediction`
- `hypothesis_contradiction`
- `hypothesis_demoted`
- `demotion_reason`

No external API changes.

## Graph model notes

Use a bounded labeled property graph in memory:

```text
(:Hypothesis {id, scope, confidence, status})
(:Prediction {id, expected, falsification_window})
(:ActionTrial {step, action_id, meaningful_progress, terminal_delta})
(:Observation {step, object_progress_score, env_reward, terminal_value_score})

(:Hypothesis)-[:PREDICTS]->(:Prediction)
(:Prediction)-[:TESTED_BY]->(:ActionTrial)
(:ActionTrial)-[:OBSERVED_AS]->(:Observation)
(:Observation)-[:CONTRADICTS {reason}]->(:Hypothesis)
(:Observation)-[:SUPPORTS {reason}]->(:Hypothesis)
```

Keep this local and compact. Persist only summary evidence through MCP at safe phases if needed.

## Tests to add or run

Add tests for:

- repeated non-meaningful action trials add evidence-against
- hypothesis confidence drops after terminal stall
- demoted hypothesis is not reused immediately
- object progress without terminal trend stops reinforcing confidence
- trace contains demotion reason

Validation commands:

```bash
pytest -q tests/test_a069_hypothesis_falsification.py
pytest -q tests/test_a065_hypothesis_workspace.py tests/test_a066_meaningful_progress_gate.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Default falsification window should be short for repeated identical action effects, e.g. 3 trials.
- Demotion should reduce confidence and influence policy, but not permanently ban an action for the whole task unless the evidence is very strong.
