# Plan: A160 — `check_action_gate` Can Never Actually Block an Action

## Context

`agents/arc4/graph_queries.py::check_action_gate` (current lines 121-133) reads `result.get("allowed", result.get("approved", True))`. The real server implementation, `campy/brain/thalamus/tools/arc_queries.py::arc_check_action_gate` (hippocampy repo), returns `{"go": bool, "reason": str, "falsification_count": int, "reward_prediction_error": 0, "untested_available": bool}` — no `allowed` or `approved` key ever exists, so the client always falls through to the hardcoded default `True`.

`plan_vetter.py`'s consumer (current lines 59-71) genuinely branches on this value (`if not graph_gate.get("allowed", True) and alternative is not None: [veto]`), so this is a real, previously-silent behavioral bug, not a cosmetic one.

## Implementation Steps

### Step 1: Read the real field

In `agents/arc4/graph_queries.py::check_action_gate`, current line ~128:

```python
"allowed": bool(result.get("allowed", result.get("approved", True))),
```

becomes:

```python
"allowed": bool(result.get("allowed", result.get("approved", result.get("go", True)))),
```

`go` is checked last in the fallback chain — if a future or alternate server implementation ever adopts `allowed`/`approved` directly, those still take priority; the current real shape (`go`-only) now correctly falls through to it instead of the hardcoded `True`.

### Step 2: Tests

New file `tests/test_a160_check_action_gate_field_mismatch.py`. Construct `ArcGraphQueryPort` with a stub `brain_client` (a simple object with a `call_tool(name, payload)` method returning a canned dict — follow the pattern of existing `agents/arc4` test files that stub MCP-like ports, e.g. check `tests/test_arc4_goal_resolver.py`'s `RecordingGraphPort` for the general shape, adapted for a raw `brain_client.call_tool` stub instead).

1. `test_go_false_produces_allowed_false` — stub returns `{"go": False, "reason": "falsified 3 times", "falsification_count": 3, "untested_available": True}` — assert `check_action_gate(...)["allowed"] is False`.
2. `test_go_true_produces_allowed_true` — stub returns `{"go": True, "reason": "approved"}` — assert `allowed is True`.
3. `test_legacy_allowed_key_still_works` — stub returns `{"allowed": False}` directly (no `go` key at all) — assert `allowed is False` (regression guard: the fix must not break a hypothetical/future server shape using the originally-expected key name).
4. `test_capability_missing_defaults_true` — stub returns `{"status": "capability_missing"}` — assert `allowed is True` (regression guard, unchanged existing behavior).

New test class/function in the same file (or a clearly-separated section) for the `plan_vetter.py` integration case:

5. `test_plan_vetter_vetoes_when_graph_gate_reports_go_false` — construct a minimal `PlanVetter` with a stub graph port whose `check_action_gate` returns `{"allowed": False, "reason": "graph says no"}` (i.e., testing `plan_vetter.py` in isolation from `graph_queries.py`, using the corrected output shape directly, since the goal is to prove the CONSUMER correctly branches on it — not to re-test `check_action_gate` itself here), a candidate with attempt history, and an available alternative — call the vet phase and assert the resulting `VetDecision.approved is False` and `should_replan` reflects a veto. Read `agents/arc4/plan_vetter.py`'s `PlanVetter.__call__`/`vet` entrypoint signature first (grep) to construct valid minimal args — mirror whatever construction pattern nearby `agents/arc4` tests already use for phase objects (`PlanCandidate`, `WorkflowState`, etc.).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a160_check_action_gate_field_mismatch.py -v
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/graph_queries.py` | `check_action_gate` adds `go` as a fallback lookup key |
| `tests/test_a160_check_action_gate_field_mismatch.py` | New — unit tests for the field fix plus an integration test proving `plan_vetter.py` actually vetoes when it should |

## Risks

- Low technical risk (additive fallback key, existing behavior for all other cases preserved) — but this is the first time this specific veto path will ever actually fire in a live run, so watch the next live-smoke run after this lands for any unexpected behavior change (e.g., more frequent replanning than before, since the veto was previously permanently inert).
