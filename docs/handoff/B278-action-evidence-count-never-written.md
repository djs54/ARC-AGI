# Handoff: B278 `ActionFact.evidence_count`/`supports` are never written

**For:** hippocampy / Campy owner (B278 owns brain internals; ARC consumes across the MCP seam)
**From:** ARC_AGI A165 consumer-side verification (2026-08-06)
**Status:** confirmed server-side gap; ARC-side interim fix already shipped (no action needed there)

## Summary

`ActionFact.evidence_count` is declared in the schema (`campy/brain/hippocampus/schema.py`) and read by `arc_get_action_evidence`, but no tool anywhere in `campy/` ever writes it. It is `0`/`null` for every `ActionFact` node that has ever existed. `supports`/`support_count` have no server-side representation for actions at all — `ActionFact` has no such column; that shape only exists for `Hypothesis` nodes (via `arc_confirm_hypothesis`/`arc_contradict_hypothesis`'s `evidence_count`, a different node type, easy to conflate with `ActionFact.evidence_count`).

This keeps `arc_get_action_evidence`'s `evidence_count` field permanently uninformative — every response reports `0` regardless of how many times the action has genuinely been attempted.

## Reproduction (raw MCP session, no ARC code)

```python
# task_id unique per run
arc_record_action_effect {task_id, action_id: "ACTION1", step: 0, effect: {n_cells_changed: 3, apparent_effect: "grid_change"}}
arc_record_action_effect {task_id, action_id: "ACTION1", step: 1, effect: {n_cells_changed: 0, apparent_effect: "no_change"}}
arc_record_action_effect {task_id, action_id: "ACTION1", step: 2, effect: {n_cells_changed: 0, apparent_effect: "no_change"}}
arc_get_action_evidence {task_id, action_id: "ACTION1"}
```

### Observed

- `arc_record_action_effect` × 3 → each returns `{"ok": true, "status": "ok", "fact_id": "<task>_ACTION1", "effect_id": "..."}`.
- `arc_get_action_evidence` → `{"tested": true, "steps_used": 3, "evidence_count": 0, "falsified_count": 0, ...}`.

`steps_used` (which is actually `ActionFact.observation_count` under a different response key — see below) correctly advances to `3`. `evidence_count` stays `0`.

## Where to look

`campy/brain/thalamus/tools/arc_queries.py`:

- `arc_record_action_effect` (~L292-326): its `SET` clause only touches `af.observation_count`, `af.task_id`, `af.action_id`, `af.last_updated`. Never `af.evidence_count`.
- `arc_record_reward_prediction_error` (~L568-611): only mutates `af.confidence`/`af.falsified_count`/`af.value_status`. Never `af.evidence_count`.
- `arc_get_action_evidence` (~L185-229): reads `af.evidence_count` (line ~225) and returns it verbatim — the read side is correctly wired, there's just nothing to read.
- Confirmed via `grep -rn "evidence_count" campy/` that no write path anywhere in the tree sets this specific field on `ActionFact`.

Separately (informational, not requiring action): `af.observation_count` — the one counter `arc_record_action_effect` genuinely increments — is returned by `arc_get_action_evidence` under the key `steps_used` (~L226: `"steps_used": observation_count`), not `observation_count`. This wasn't a bug on your side; ARC's client simply wasn't reading that key. Already fixed on the ARC side (see below).

## Ask

Pick whichever is true and let us know:

1. **`evidence_count` is meant to mean the same thing as `observation_count`** ("how many times has this action's effect been recorded") — in which case one of the two fields is redundant, and the simplest fix is to have `arc_record_action_effect`'s `SET` clause also increment `evidence_count` (or just have `arc_get_action_evidence` return `observation_count` under both keys until the redundancy is cleaned up).
2. **`evidence_count` is meant to track something different** (e.g. only "meaningful"/non-neutral observations, or something else) — in which case a real write path needs to be added wherever that distinct signal is computed.
3. **The field is vestigial/deprecated** — in which case let us know so we can stop describing it as available signal on the ARC side and drop the fallback keys that reference it.

No urgency — this doesn't block anything on the ARC side; `contradictions` (via `falsified_count`, already working since A146/A162) drives the real falsification-penalty scoring. This only affects the informational `attempts` field surfaced to the planning LLM's context, which ARC has already patched to fall back to the working `steps_used` value in the interim.

## ARC-side fix already shipped (no action needed from you)

`agents/arc4/graph_queries.py::fetch_per_action_evidence`'s `attempts` fallback chain now checks `steps_used` as a last resort (after `attempts`, `attempt_count`, `evidence_count`, all of which are currently always absent/zero), so the planner's LLM context no longer shows a misleading `attempts: 0` for actions with real attempt history. If `evidence_count` is ever wired to a real value per the ask above, it will automatically take priority over `steps_used` in that same fallback chain — no further ARC-side change needed.

## How ARC will know it's fixed

`tests/test_a162_fetch_per_action_evidence_field_mismatch.py::test_evidence_count_still_takes_priority_over_steps_used` already documents that `evidence_count`, once populated, wins over the `steps_used` fallback — no new test needed; if `evidence_count` starts arriving with real values, ARC's existing normalization will pick it up automatically.
