# Plan: A162 — `fetch_per_action_evidence` Discards Real Falsification Data

## Context

`agents/arc4/graph_queries.py::fetch_per_action_evidence` (current lines 106-119) maps the raw `arc_get_action_evidence` MCP response onto a normalized `{"supports", "contradictions", "confidence", "attempts", "raw"}` shape consumed by `agents/arc4/plan_generator.py::_build_candidates` (current ~lines 139-144) for `falsification_penalty` scoring.

The server (`campy/brain/thalamus/tools/arc_queries.py::arc_get_action_evidence`, hippocampy repo) returns `{"tested", "action_id", "fact_type", "confidence", "value_status", "evidence_count", "steps_used", "falsified_count", "causal_power"}` — no `supports`/`contradictions`/`attempts` keys exist. Only `confidence` matches by coincidence.

`tests/test_a146_graph_evidence_contract.py::_evidence_counts` already has the correct fallback chain (`contradictions` → `contradiction_count` → `falsified_count`; `attempts` → `attempt_count` → `evidence_count`) — but only in the *test's own helper*, never applied to the actual production adapter. This card ports that proven-correct mapping into `graph_queries.py` itself.

Live evidence: 2026-08-06 live-smoke run (game `ls20-9607627b`) — 32 `graph_evidence` log dumps, all showing `contradictions: 0`/`attempts: 0` in the normalized fields while `raw.falsified_count` was genuinely nonzero (e.g. `7`).

## Implementation Steps

### Step 1: Fix the field mapping

In `agents/arc4/graph_queries.py::fetch_per_action_evidence`, current lines 112-116:

```python
return {
    "supports": int(result.get("supports", result.get("support_count", 0)) or 0),
    "contradictions": int(result.get("contradictions", result.get("contradiction_count", 0)) or 0),
    "confidence": float(result.get("confidence", result.get("score", 0.0)) or 0.0),
    "attempts": int(result.get("attempts", result.get("attempt_count", 0)) or 0),
    "raw": dict(result),
}
```

becomes:

```python
return {
    "supports": int(result.get("supports", result.get("support_count", 0)) or 0),
    "contradictions": int(result.get("contradictions", result.get("contradiction_count", result.get("falsified_count", 0))) or 0),
    "confidence": float(result.get("confidence", result.get("score", 0.0)) or 0.0),
    "attempts": int(result.get("attempts", result.get("attempt_count", result.get("evidence_count", 0))) or 0),
    "raw": dict(result),
}
```

`supports` has no server-side equivalent at all currently (the server doesn't track a distinct "supports" count, only `falsified_count`) — leave its fallback chain as-is; it will continue to be `0` unless/until the server adds an equivalent field, which is expected and not part of this card's scope (contradictions-only evidence is exactly what A135's design already anticipates: `if evidence_contradictions > evidence_supports` correctly treats "no known supports, some known contradictions" as net-negative).

### Step 2: Tests

New file `tests/test_a162_fetch_per_action_evidence_field_mismatch.py`. Same stub-`brain_client` pattern as A160/A161.

1. `test_real_server_shape_falsified_count_maps_to_contradictions` — stub returns `{"tested": True, "confidence": 0.0, "falsified_count": 7, "evidence_count": 12, "steps_used": 22, "causal_power": 0.0, "value_status": "unknown"}` — assert `fetch_per_action_evidence(...)` returns `contradictions=7`, `attempts=12`.
2. `test_confidence_still_passes_through_directly` — same fixture — assert `confidence` matches the input `confidence` value unchanged (proves the one field that already worked correctly stays correct).
3. `test_legacy_contradictions_key_takes_priority` — stub returns `{"contradictions": 2, "falsified_count": 9}` (both present) — assert `contradictions == 2` (the more-specific key wins, per the fallback order — regression guard for forward compatibility).
4. `test_capability_missing_still_all_zero` — stub returns `{"status": "capability_missing"}` — assert the all-zero default dict (regression guard).

### Step 3: Integration regression guard in `test_a135_graph_driven_planning.py`

Read that file first to find its existing test/fixture conventions for `plan_generator.py` + a stubbed graph port. Add:

5. `test_falsification_penalty_applies_with_real_server_field_names` — construct a `PlanGenerator` with a stub graph port whose `fetch_per_action_evidence` returns the corrected-mapping output (`contradictions=5, supports=0, confidence=0.0`) for a given action_id — generate candidates and assert the corresponding candidate's `score` is measurably lower than an otherwise-identical candidate with `contradictions=0` (i.e., the penalty line in `_build_candidates` actually executes and changes the outcome). This is the proof that the fix isn't just cosmetically correct but actually changes planner behavior.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a162_fetch_per_action_evidence_field_mismatch.py -v
.venv/bin/python -m pytest tests/test_a135_graph_driven_planning.py -v
make test-a
make test-all
```

Then, separately (manual, not part of automated verify):

```bash
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
  PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 10
grep -o '"contradictions": [0-9]*' <the run's log> | sort | uniq -c
```
Confirm at least one nonzero value appears (previously always `0`).

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/graph_queries.py` | `fetch_per_action_evidence` adds `falsified_count`/`evidence_count` fallbacks for `contradictions`/`attempts` |
| `tests/test_a162_fetch_per_action_evidence_field_mismatch.py` | New, 4 tests |
| `tests/test_a135_graph_driven_planning.py` | +1 integration test proving the scoring penalty now actually applies |

## Risks

- Low technical risk (additive fallback keys). The *behavioral* risk is real but intended: once this lands, actions with real graph-side falsification history will score measurably lower than before — this is the fix doing its job, but worth knowing before/if a live run's behavior shifts noticeably (e.g., more diverse action selection, since previously-favored-by-default repeatedly-falsified actions will finally be penalized).
