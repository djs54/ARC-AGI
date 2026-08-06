# Handoff: B278 `falsified_count` increment not persisting

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A146 consumer-side verification (2026-06-13)
**Status:** one upstream bug confirmed; one consumer bug already fixed on the ARC side

## Summary

B278's schema work landed correctly — `ActionFact.falsified_count` exists and
`arc_get_action_evidence` returns it. But the increment path does not persist:
`arc_record_reward_prediction_error` acknowledges a negative RPE yet
`falsified_count` stays `0`. This keeps A135's contradiction penalty inert.

## Reproduction (raw MCP session, no ARC code)

```python
# task_id unique per run; action recorded via effect first, then RPE
arc_record_action_effect {task_id, action_id:"ACTION1", step, effect:{observed_kind:"no_change", did_progress:false, falsification_delta:1}}
arc_record_reward_prediction_error {task_id, action_id:"ACTION1", step, predicted_reward:1.0, actual_reward:0.0}
# ... repeat 3x ...
arc_get_action_evidence {task_id, action_id:"ACTION1"}
```

### Observed

- `arc_record_action_effect` → `{"ok": true, "fact_id": "<task>_ACTION1", ...}` (fact created)
- `arc_record_reward_prediction_error` → `{"status": "ok", "prediction_error": -1.0, "direction": "negative"}`
  (handler computed the negative error and took the `error < -0.3` branch)
- `arc_get_action_evidence` → `{"tested": true, "steps_used": 3, "falsified_count": 0, "value_status": "unknown", ...}`

`steps_used` advances (effects persist), the RPE handler reports `direction:
negative`, but `falsified_count` never moves.

## Where to look

`campy/brain/thalamus/tools/arc_queries.py`:

- `arc_record_reward_prediction_error` (~L568): the `error < -0.3` branch issues
  `MATCH (af:ActionFact {fact_id: $fid}) SET af.falsified_count = COALESCE(af.falsified_count,0)+1, ...`
  with `fact_id = f"{task_id}_{action_id}"` (L581).
- `arc_record_action_effect` (~L292) MERGEs the same `fact_id` (L319). Keys match,
  and the fact is found by `arc_get_action_evidence`, so this is **not** a fact_id
  mismatch.

Most likely a write-visibility / commit issue on the `SET` (the `MATCH ... SET`
appears to affect 0 rows, or the increment is written to a connection whose
change isn't visible to subsequent reads), rather than a keying bug. The handler
returns success regardless of rows affected, which is why it looks OK from the
caller. Suggest asserting `rows_affected`/`RETURN af.falsified_count` from the
`SET` to confirm it matched.

## ARC-side fix already shipped (no action needed from you)

ARC's `agents/arc4/graph_queries.py` was sending a legacy
`reward_prediction_error` key that the tool ignores (the schema declares
`predicted_reward`/`actual_reward`). Fixed to send `predicted_reward: 1.0` and
`actual_reward: 1.0 if meaningful_progress else 0.0` so the production
falsification path actually drives the negative-RPE branch once the persistence
bug above is resolved.

## How ARC will know it's fixed

`tests/test_a146_graph_evidence_contract.py` (marked `requires_mcp` + `xfail
strict=False`) records 3 falsifications and asserts the graph reports
`contradictions >= 3` (or `attempts >= 3`). It currently XFAILs; when the
increment persists it will XPASS, which is our closure signal for A146.
