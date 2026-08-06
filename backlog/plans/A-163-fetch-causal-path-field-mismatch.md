# Plan: A163 — `fetch_causal_path`'s `supports`/`contradicts` Are Never Populated

## Context

`agents/arc4/evaluator.py`'s causal-override check (current lines ~101-108) needs itemized supporting/contradicting hypothesis lists from `fetch_causal_path` to decide whether an apparent-progress action's causal path is "contradiction-only." `campy/brain/thalamus/tools/arc_queries.py::arc_get_causal_path` (hippocampy) only ever returns `{"path_exists": bool, "path_length": int, "path_confidence": float}` — an aggregate signal, not itemized lists. This is a genuine design/implementation gap, not a simple rename — see the card's Problem section for the full trace.

## Step 0: Decide the approach (gate)

Before implementing anything, decide, with the user:

1. Is an aggregate `path_confidence`-based override (branch A) an acceptable, if weaker, substitute for the originally-designed itemized-list check? Given `evaluator.py`'s other overrides (A150's weak-prediction path, A152's goal decay) already provide meaningful falsification pressure through other channels, is this specific check's original design still worth preserving in a diminished form, or is it redundant now?
2. Or should hippocampy's `arc_get_causal_path` be asked to return itemized supports/contradicts (branch B), matching the client's original design, with this check staying dormant (but now honestly documented as dormant, not silently broken) until that lands?

Outcome selects branch **(A)** or **(B)**.

## Branch A — adapt to the aggregate signal the server actually provides

### A1. Rework the evaluator check

In `agents/arc4/evaluator.py`, replace the `supports`/`contradicts`-list-based condition with a `path_confidence`-threshold-based one. Add a new `EvaluationLimits` field (e.g. `causal_override_confidence_threshold: float = 0.3`) so the threshold is tunable/testable, matching the pattern of other limits in that dataclass.

### A2. Surface `path_confidence` as a top-level field

`agents/arc4/graph_queries.py::fetch_causal_path`'s return dict currently only has `path_confidence` inside `raw`, not as a first-class key — add it: `"path_confidence": float(result.get("path_confidence", 0.0) or 0.0)`.

### A3. Tests

New file `tests/test_a163_fetch_causal_path_field_mismatch.py`:
1. Low `path_confidence` + `path_exists=True` → override fires.
2. High `path_confidence` + `path_exists=True` → override does not fire.
3. `path_exists=False` → override never fires regardless of confidence (unchanged gating).
4. Regression: `fetch_causal_path`'s `supports`/`contradicts` keys still return `[]` (honest — the server genuinely doesn't provide them; don't fabricate data), only `path_confidence` and `path_exists` are load-bearing now.

## Branch B — leave the client as-is, file the gap upstream

### B1. Hand-off note

Write `docs/handoff/B278-causal-path-itemized-evidence.md` (matching `docs/handoff/B278-graph-evidence.md`'s structure and tone): exact current server return shape, exact client expectation, and the specific ask (itemized `supports`/`contradicts` hypothesis-id lists, or a documented reason why an aggregate-only signal is intentional on hippocampy's side).

### B2. Document the dormant state honestly

Add a code comment at the `evaluator.py` check site noting it's currently unreachable pending the upstream gap, referencing this card and the hand-off doc — so a future reader doesn't mistake "no exceptions, no crashes" for "working."

### B3. Contract test

`tests/test_a163_fetch_causal_path_field_mismatch.py`: one test proving the current always-empty-lists shape (regression guard against silent reintroduction), marked with a comment explaining it documents a known gap, not desired behavior.

## Verify (either branch)

```bash
.venv/bin/python -m pytest tests/test_a163_fetch_causal_path_field_mismatch.py -v
make test-a
make test-all
```

## Files (branch-dependent)

| File | Branch A | Branch B |
|------|----------|----------|
| `agents/arc4/evaluator.py` | New confidence-threshold check | Comment noting dormant/gap status |
| `agents/arc4/graph_queries.py` | Surface `path_confidence` as top-level key | Unchanged |
| `docs/handoff/B278-causal-path-itemized-evidence.md` | N/A | New hand-off doc |
| `tests/test_a163_fetch_causal_path_field_mismatch.py` | Threshold-behavior tests | Gap-documentation regression test |

## Risks

- Branch A risk: a confidence-threshold heuristic is a real design choice (what threshold is right?) not a mechanical fix — get it wrong and it could either never fire (same as now) or fire too eagerly (suppressing real progress). Start conservative (a low threshold) and treat as tunable.
- Branch B risk: leaves a known-dormant safety check in the codebase for an unknown amount of time — acceptable if clearly documented (per B2), matching how A146 handled the same situation for a different tool.
