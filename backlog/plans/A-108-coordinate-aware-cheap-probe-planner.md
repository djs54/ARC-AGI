# Plan: A-108 — Coordinate-aware cheap-probe planner

## Card metadata

- **Card:** A108
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A082, A106, A107

## Summary

Make deterministic cheap probe iterate graph-backed click coordinates instead of repeatedly executing bare `ACTION6`.

## Implementation approach

1. In `WorldModelPlanner`, prefer click candidates when:
   - available actions are exactly `["ACTION6"]`, or
   - active goal type is `color_correspondence`, `pattern_completion`, `endpoint_connection`, or `collect_or_activate`, and
   - `get_click_candidates(...)` returns candidates.
2. Add planner candidate mode:
   - `mode="click_probe"`
   - `action_id="ACTION6"`
   - `x`, `y`
   - `action_identity="ACTION6@x,y"`
   - `click_candidate_id`
   - `evidence_path`
3. In orchestrator cheap-probe path:
   - Select the next untried candidate by rank.
   - Skip candidates whose `action_identity` is quarantined or recently failed.
   - Emit non-null `x/y`.
4. Outcome handling:
   - If frame/config hash changes, mark candidate supported.
   - If no frame/config hash changes twice, mark candidate falsified/quarantined.
   - Do not quarantine all `ACTION6`; quarantine only `ACTION6@x,y`.
5. Exhaustion handling:
   - If all candidates tried/falsified, controller escalates with `click_candidates_exhausted`.

## Concrete file additions/edits

- Edit `agents/arc3/orchestrator.py`
- Edit `agents/arc3/reasoning_controller.py`
- Edit `agents/arc3/world_model_planner.py`
- Edit `agents/arc3/world_model.py`
- Edit `benchmarks/arc3/world_model_eval.py`
- Add `tests/test_a108_coordinate_aware_cheap_probe_planner.py`

## API/interface changes

Planner selected candidate gains:

```python
{
    "mode": "click_probe",
    "action_id": "ACTION6",
    "action_identity": "ACTION6@24,17",
    "x": 24,
    "y": 17,
    "click_candidate_id": "click-ft09-...",
    "click_candidate_role": "framed_center",
}
```

Reasoning decision may use:

```python
trigger = "click_candidate_probe"
stall_policy = "coordinate_probe"
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a108_coordinate_aware_cheap_probe_planner.py
.venv/bin/python -m pytest -q tests/test_a082_deterministic_cheap_probe_action_path.py tests/test_a106_coordinate_aware_click_action_identity.py tests/test_a107_graph_click_candidate_generator.py
.venv/bin/python -m pytest -q tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Cheap probe should be deterministic for reproducible smoke tests.
- Candidate ranking is graph-first, then untried-first, then stable coordinate ordering.
- This card must not call the LLM or MCP on the execute hot path.
