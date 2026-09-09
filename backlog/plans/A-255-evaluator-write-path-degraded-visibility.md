# A255 — Evaluator Write-Path `degraded` Visibility: Plan

## Card metadata

- Card: `backlog/A255.md`
- Depends on: A244 (the exact pattern this extends, two of five sites already correct)

## Design (confirmed by direct read before writing this plan)

- `agents/arc4/evaluator.py:248-268` — `_record_transition`, the first unguarded site (`except Exception: return "failed"` at line 263).
- `agents/arc4/evaluator.py:270-299` — `_record_rule_evidence`, the second unguarded site (line 293) — this is the exact method a peer session confirmed genuinely raised `RuntimeError` server-side during a real live-smoke run, 3 times, while `evaluate_degraded` stayed `false` throughout.
- `agents/arc4/evaluator.py:318-330` — `_record_evaluation`, the third unguarded site (line 328).
- `agents/arc4/evaluator.py:125-138` — `fetch_causal_path`, A244's own already-correct reference pattern: `except Exception: self._degraded = True` (comment: "graph unavailable — don't override (unchanged fallback), degradation made visible instead of silently absorbed").
- `agents/arc4/evaluator.py:365-397` (line numbers approximate, confirm current) — `_action_space_exhausted`'s `fetch_untested_actions` call, A244's second correct site, same pattern.
- Confirm exact current line numbers before editing — this file has been touched by several cards this session (A233/A240/A244/A249's related work).

### The fix

Identical three-line addition at each of the three sites:

```python
# _record_transition
try:
    result = record(execution, grid_diff, getattr(perception, "entities", ()))
except Exception:
    self._degraded = True  # A255: mirrors fetch_causal_path's A244 pattern -- was silently swallowed
    return "failed"
```

```python
# _record_rule_evidence
try:
    if self._accepts_entities_param(record):
        result = record(execution, grid_diff, getattr(perception, "entities", ()))
    else:
        result = record(execution, grid_diff)
except Exception:
    self._degraded = True  # A255
    return "failed"
```

```python
# _record_evaluation
try:
    record(evaluation)
except Exception:
    self._degraded = True  # A255
    return "failed"
```

No other lines change in any of the three methods — same illustrative caveat as every prior card in this family: confirm the exact current code shape before editing, this is a minimal, additive fix.

## Implementation approach

### Files

- Modify: `agents/arc4/evaluator.py` — three `except Exception:` blocks.
- Test: new `tests/test_a255_evaluator_write_path_degraded_visibility.py` (or extend `tests/test_a244_evaluate_graph_degraded_visibility.py` directly if that reads more naturally as "completing A244's own scope" — check which existing file's structure fits better before deciding, either is acceptable).

### TDD

- New test: a fake `graph_query_port` whose `record_transition` raises → call `evaluate()` end-to-end (or `_record_transition` directly, whichever matches this file's existing test style) → confirm `EvaluationResult.degraded is True` and `evaluation.metadata["transition_recording"] == "failed"` (both must hold — visibility added, behavior unchanged).
- New test: same shape for `record_rule_evidence` raising → `degraded=True`, `metadata["rule_recording"] == "failed"`.
- New test: same shape for `record_evaluation` raising → `degraded=True`, `metadata["graph_recording"] == "failed"`.
- New test: all three healthy (non-raising) → `degraded=False` — the regression guard proving this isn't just hardcoded `True`.
- New test: confirm `self._degraded` correctly accumulates across multiple sites within one `evaluate()` call (mirroring A244's own "accumulates across both potential exception sites within one evaluate() call" verification) — now three potential sites, not two.
- Regression: run existing `test_a244_evaluate_graph_degraded_visibility.py` in full, confirm unchanged.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a255_evaluator_write_path_degraded_visibility.py -v
.venv/bin/python -m pytest tests/test_a244_evaluate_graph_degraded_visibility.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation. This card's own motivating evidence came from an unrelated, real hippocampy-side bug happening to hit during a live run — not reliably reproducible on demand. A live smoke run showing `evaluate_degraded` staying `false` under normal healthy conditions is a reasonable regression check; the TDD suite (raising fake graph-write calls through the real `evaluate()` path) is the primary evidence for the new behavior itself, same standard as A244's own original card.

## Assumptions/defaults

- Exact mirror of A244's own pattern — no design ambiguity, just extending to three more sites of the identical shape.

## Implementation note (added post-implementation)

The design above (and the card's own "What this delivers" snippet) specifies only the per-site `except Exception: self._degraded = True` edits. Implementing exactly that and nothing else leaves the new behavior inert: `evaluate()` builds `EvaluationResult(..., degraded=self._degraded, ...)` *before* calling `_record_evaluation`/`_record_transition`/`_record_rule_evidence` (the three write-path sites this card touches), so a write-path except-branch setting `self._degraded = True` happens too late to affect the already-constructed result's `degraded` field. Verified via TDD: the "raising write call → degraded=True" tests failed (degraded stayed False) even with the three per-site edits in place, until one additional line was added right after the three write-path calls: `evaluation.degraded = self._degraded`. This is a wiring necessity to make the plan's own stated fix actually take effect, not a change to the fix's design.
