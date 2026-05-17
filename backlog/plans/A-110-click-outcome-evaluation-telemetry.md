# Plan: A-110 — Click outcome evaluation telemetry

## Card metadata

- **Card:** A110
- **Priority:** P1
- **Layer:** evaluation/harness
- **Depends on:** A106, A107, A108, A109

## Summary

Expose click candidate selection and click outcome in smoke artifacts so null-click loops and graph-coordinate progress are visible immediately.

## Implementation approach

1. Extend world-model step metrics with compact click fields:
   - `action_identity`
   - `coordinate_required`
   - `missing_coordinate_click`
   - `click_candidate_count`
   - `selected_click_candidate_id`
   - `selected_click_candidate_role`
   - `selected_click_candidate_rank`
   - `clicked_x`, `clicked_y`
   - `clicked_color`
   - `clicked_panel_id`
   - `click_supported`
   - `click_falsified`
2. Runner/orchestrator step records should populate these fields from selected planner candidate and post-action outcome.
3. Final compact result should include:
   - `missing_coordinate_click_count`
   - `unique_action_identity_count`
   - `click_candidates_tried`
   - `click_candidates_supported`
   - `click_candidates_falsified`
4. World-model summary should include a short sentence when click planning failed:
   - example: `Click planning failed: 30 null ACTION6 clicks with no frame/configuration delta.`
5. Keep artifact size bounded:
   - do not include full candidate lists in final compact output.
   - include at most top 5 candidate summaries when needed for debugging.

## Concrete file additions/edits

- Edit `benchmarks/arc3/world_model_eval.py`
- Edit `agents/arc3/runner.py`
- Edit `agents/arc3/orchestrator.py`
- Edit `run_single_puzzle.py`
- Add `tests/test_a110_click_outcome_evaluation_telemetry.py`

## API/interface changes

World-model JSONL row additions:

```json
{
  "action_identity": "ACTION6@24,17",
  "coordinate_required": true,
  "missing_coordinate_click": false,
  "click_candidate_count": 12,
  "selected_click_candidate_id": "click-ft09-...",
  "selected_click_candidate_role": "framed_center",
  "selected_click_candidate_rank": 1,
  "clicked_x": 24,
  "clicked_y": 17,
  "clicked_color": 2,
  "clicked_panel_id": "panel-target",
  "click_supported": true,
  "click_falsified": false
}
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a110_click_outcome_evaluation_telemetry.py
.venv/bin/python -m pytest -q tests/test_a078_world_model_evaluation_harness.py tests/test_a088_compact_smoke_artifact_exports.py tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Telemetry must remain compact and structured.
- Test fixtures should include both a successful coordinate click and a null-click failure.
- No MCP or SideQuests changes are required.
