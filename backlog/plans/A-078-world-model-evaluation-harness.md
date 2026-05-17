# Plan: A-078 — world-model evaluation harness and live stream

## Card metadata

- **Card:** A078
- **Priority:** P0
- **Layer:** evaluation/harness
- **Depends on:** A073, A074, A075, A076, A077

## Summary

Create a dedicated evaluation mode for the world-model redesign and write a separate live JSONL artifact. This lets us judge whether the architecture is learning coherent per-game and aggregate models, rather than only checking correctness/failure class after the run ends.

Graph-solution classification: this is testing and evaluation for a graph-enabled system. The evaluation should check graph quality, traversal usefulness, boundedness, and decision impact.

## Implementation approach

1. Add `benchmarks/arc3/world_model_eval.py`.
2. Define evaluation metrics:
   - `world_model_node_count`
   - `world_model_edge_count`
   - `compiled_claim_count`
   - `action_effect_claim_count`
   - `support_edge_count`
   - `contradiction_edge_count`
   - `hypothesis_demotion_count`
   - `mechanic_candidate_count`
   - `mechanic_prior_used_count`
   - `reasoning_skip_count`
   - `reasoning_escalation_count`
   - `planner_candidate_count`
   - `selected_candidate_has_prediction`
   - `selected_candidate_has_falsification_condition`
   - `single_action_stall_detected`
   - `full_reasoning_cycles_avoided`
3. Add health signals:
   - `graph_bounded`
   - `compiler_active`
   - `falsification_active`
   - `reasoning_gated`
   - `planner_grounded`
   - `memory_transfer_active`
4. Add CLI support in `run_single_puzzle.py`:
   - `--world-model-eval`
   - optional `--world-model-live-output PATH`
   - default path: `submission_results_single.world_model.live.jsonl`
5. Emit live JSONL rows:
   - one row per step with compact metrics
   - one final row with summary and pass/fail signals
6. Preserve existing artifacts:
   - do not replace `submission_results_single.live.jsonl`
   - existing smoke consumers must keep working
7. Add fixture-based tests:
   - evaluate a synthetic ACTION6 stall trace
   - evaluate a synthetic contradiction/demotion trace
   - validate JSONL schema

## Concrete file additions/edits

- `benchmarks/arc3/world_model_eval.py`
  - New evaluator and JSONL row builder.
- `run_single_puzzle.py`
  - CLI flags and wiring to live writer.
- `agents/arc3/runner.py`
  - Provide step/final world-model eval payloads.
- `benchmarks/arc3/trajectory_eval.py`
  - Reuse existing trace extraction where appropriate, but keep world-model eval separate.
- `tests/test_a078_world_model_evaluation_harness.py`
  - New tests for evaluator and JSONL output.

## API/interface changes

CLI additions:

```bash
python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 30 --world-model-eval
python run_single_puzzle.py --world-model-eval --world-model-live-output /tmp/world_model.live.jsonl
```

Output additions:

- `submission_results_single.world_model.live.jsonl`
- Optional final summary object in regular result JSON:
  - `world_model_eval_summary`

## JSONL row shape

Example step row:

```json
{
  "kind": "world_model_step",
  "task_id": "arc_eval_001",
  "step": 12,
  "world_model_node_count": 42,
  "world_model_edge_count": 71,
  "compiled_claim_count": 4,
  "action_effect_class": "pixel_churn",
  "contradiction_edge_count": 2,
  "hypothesis_demotion_count": 1,
  "reasoning_mode": "cheap_execute",
  "planner_candidate_count": 2,
  "single_action_stall_detected": false
}
```

Example final row:

```json
{
  "kind": "world_model_summary",
  "task_id": "arc_eval_001",
  "graph_bounded": true,
  "compiler_active": true,
  "falsification_active": true,
  "reasoning_gated": true,
  "planner_grounded": true,
  "memory_transfer_active": false,
  "single_action_stall_detected": true,
  "full_reasoning_cycles_avoided": 18
}
```

## Tests to add or run

Add tests for:

- world-model eval can run on fixture trace without ARC network
- live JSONL step rows include required fields
- final summary row includes redesign health signals
- existing live-smoke JSONL output remains unchanged when `--world-model-eval` is absent
- custom output path works

Validation commands:

```bash
pytest -q tests/test_a078_world_model_evaluation_harness.py
pytest -q tests/test_a074_world_model_compiler.py tests/test_a077_world_model_guided_planner.py
pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- The generated JSONL file should not be committed unless a small fixture is explicitly added under tests.
- Evaluation should prefer deterministic fixture traces for CI and reserve live ARC runs for manual smoke validation.
