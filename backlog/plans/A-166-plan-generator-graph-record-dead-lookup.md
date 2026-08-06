# Plan: A166 — `plan_generator.py`'s Per-Action `graph_record` Lookup Is Structurally Dead

## Context

`agents/arc4/plan_generator.py::_build_candidates` builds `records_by_action = {record["action_id"]: record for record in graph_records if record.get("action_id")}` from `graph_records`, which `_fetch_graph_records` sources exclusively from `fetch_goal_evidence` (→ `arc_get_goal_evidence`). That server tool returns `VictoryCondition`-shaped records keyed by `condition_id`, never `action_id` — so `records_by_action` is always empty, `graph_record` is always `{}` for every candidate, and `graph_score`'s `graph_record.get("confidence", ...)` seed is always the hardcoded `0.0` fallback. The real per-action signal already flows correctly via the separate `graph_evidence` (`fetch_per_action_evidence`) lookup a few lines later — this card doesn't touch that working path.

Confirmed no test in the repo asserts on `graph_record`'s presence or shape (`grep -rn "graph_record\b" tests/` → no matches), so removal is a safe, unobserved-by-tests cleanup.

## Step 0: Decide (gate)

Check hippocampy's schema (`campy/brain/hippocampus/schema.py`) for any `VictoryCondition`-to-`ActionFact` (or similar) relationship type before deciding:

- If none exists → **remove** the dead lookup (recommended; matches what the data actually supports).
- If one exists and `arc_get_goal_evidence` simply doesn't traverse it yet → this becomes a hand-off card instead (file it as a new card, don't fold into this one, since it'd be a different kind of fix — server query enhancement, not client cleanup).

## Implementation (assuming removal — the expected path)

### 1. Remove the dead per-action lookup

In `agents/arc4/plan_generator.py::_build_candidates` (current ~L123-144):

```python
records_by_action = {record["action_id"]: record for record in graph_records if record.get("action_id")}
...
graph_record = records_by_action.get(action_id, {})
goal_alignment = self._action_matches_goal(action_id, goal)

graph_evidence: dict[str, Any] = {}
graph_score = float(graph_record.get("confidence", graph_record.get("score", 0.0)) or 0.0)
if graph_port is not None:
    try:
        graph_evidence = graph_port.fetch_per_action_evidence(action_id)
        evidence_confidence = graph_evidence.get("confidence", 0.0)
        ...
        if evidence_confidence > graph_score:
            graph_score = evidence_confidence
```

Remove `records_by_action` and the `graph_record` lookup; `graph_score` starts at `0.0` directly (the hardcoded default `graph_record.get(...)` was already resolving to) and is then set by `evidence_confidence` exactly as before — behaviorally identical, since `graph_record` never contributed a non-default value. Grep every other use of `graph_record` in the file (there are a few further down building candidate metadata/rationale — `graph_record.get("goal_id")`, `graph_record.get("rationale")`, etc., current ~L197-231) and remove those too, falling back to their existing non-`graph_record` alternatives (most already have an `or`/fallback expression alongside the `graph_record.get(...)` call — confirm case-by-case, don't assume).

Leave `graph_records`, `_fetch_graph_records`, and `_extract_mechanic_prior_actions(graph_records)` untouched — the whole-list usage (mechanic-prior action-set extraction) is a separate, working consumer of the same `graph_records` list.

### 2. Tests

No new test file needed if `tests/test_a135_graph_driven_planning.py` / `tests/test_arc4_planning.py` already exercise `_build_candidates` end-to-end (they do, confirmed) — re-run them as the regression guard, since they were passing *despite* `graph_record` always being `{}`, proving the removal is behaviorally invisible to existing coverage. Add one new assertion-light test confirming the candidate metadata no longer includes a `graph_record` key at all (documents the intentional removal so a future edit doesn't silently reintroduce a new dead lookup without noticing):

```python
def test_candidate_metadata_no_longer_includes_dead_graph_record_field(self):
    planner = PlanGenerator(PlanGeneratorLimits())
    result = planner.generate(_state(), _perception(), _goal(), graph_port=MockGraphPort()).payload
    assert "graph_record" not in (result.candidate.metadata or {})
```

## Verify

```bash
.venv/bin/python -m pytest tests/test_a135_graph_driven_planning.py tests/test_arc4_planning.py -v
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | Remove `records_by_action`/`graph_record` dead lookup and all its use sites in `_build_candidates` |
| `tests/test_a135_graph_driven_planning.py` | +1 regression test asserting the field is gone |

## Risks

- Very low — removing a code path proven to always evaluate to the same default it's now replaced by. The only real risk is missing one of the several `graph_record.get(...)` call sites scattered through `_build_candidates` (grep carefully, don't rely on memory of the line numbers above, since they may have shifted after A160-A165 land first).
