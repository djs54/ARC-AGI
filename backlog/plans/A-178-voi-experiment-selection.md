# Plan: A178 — VOI-Driven Experiment Selection

## Context

`untested_bonus: 0.22` is flat regardless of what an untested action would actually teach the system. With A177's rules in place, "trying this would resolve a real disagreement between live theories" is computable and is a strictly better signal.

## Implementation

### Disagreement scoring

For a candidate action, fetch live (unfalsified) rules whose `PREDICTS` edges cover it. If two or more disagree on predicted effect, score proportionally to the number/confidence-spread of disagreeing rules. If zero or one rule applies, fall back to the existing flat `untested_bonus` (this is additive, not a replacement, for the common low-evidence case).

### Integration

`plan_generator.py::_build_candidates`: where `untested_bonus` currently applies unconditionally to untested actions, branch on whether rule-disagreement data is available; use it when present, flat bonus otherwise.

## Tests

New `tests/test_a178_voi_experiment_selection.py`:

1. Disagreement scoring unit tests: constructed sets of agreeing/disagreeing rules produce the expected relative scores.
2. Integration: two untested candidates, one backed by disagreeing rules and one with none — assert the discriminating one ranks higher.
3. Regression: existing untested-action-ranking tests in `test_a135_graph_driven_planning.py`/`test_arc4_planning.py` still pass (fallback path preserves current behavior when no rules apply).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a178_voi_experiment_selection.py tests/test_a135_graph_driven_planning.py tests/test_arc4_planning.py -v
make test-a
make test-all
```

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | Rule-disagreement scoring, additive to `untested_bonus` |
| `tests/test_a178_voi_experiment_selection.py` | New tests |

## Risks

- Low — additive fallback design means the common case (no rules yet) is unaffected; risk is confined to whether the disagreement-scoring weights are well-tuned, which is a refinement question, not a correctness one.
- Hard-blocked on A177 — no rules, nothing to disagree.
