# Plan: A214 — Audit: Reassess How `meaningful_progress` Is Counted and Whether `graph_grounded_decision_rate` Is a Useful Near-Term KPI

## Card metadata

- ID: A214
- Priority: P2
- Layer: ARC runtime
- Dependencies: None

## Summary

Investigation-first card, same shape as A209/A212. `evaluation.meaningful_progress` drives whether a support ever reaches the graph (via `record_reward_prediction_error`'s `actual_reward`), and it is forced to `False` by four separate overrides in `evaluator.py`. Audit whether each override is correctly calibrated against real trace evidence, and separately decide whether `graph_grounded_decision_rate` needs a near-term-useful complement given it structurally cannot move for an unsolved puzzle.

## Technical approach

### 1. Gather real evidence first

Run (or reuse) a live smoke trace: `make smoke` produces `artifacts/agent_execution_trace.json`. For each step in the trace, cross-reference:
- `execution.did_progress` at that step (may need to add temporary debug logging in `evaluator.py::evaluate` if this isn't already surfaced in the trace snapshot — check `agents/arc4/telemetry.py`'s `_step_snapshot` first; if `did_progress`/override flags aren't in the trace today, that itself is worth noting as a visibility gap)
- Whether `stale_override`, `causal_override`, or `weak_prediction_override` fired that step (these are local variables in `evaluator.py::evaluate`, not currently on `EvaluationResult.metadata` — check `agents/arc4/evaluator.py` lines 174-206 for what actually gets stored in `metadata_dict`, and whether the four override booleans are already there or need adding for this audit's own visibility)
- The actual grid diff for that step, read directly to sanity-check whether the override's call was defensible

If none of the four override flags are currently visible in the trace/metadata, add them (a data-only addition to `EvaluationResult.metadata`, not a behavior change) as the FIRST step of this audit, so the rest of the investigation has real data to reason from instead of re-deriving it by hand from raw grid diffs.

### 2. Re-read each override's original justification

- `backlog/A133.md` — the stale-repeat override's original reasoning (lines 98-102 of `evaluator.py`, `stale_repeat_threshold`).
- `backlog/A135.md` and `backlog/A163.md` — the causal-path-confidence override's reasoning (lines 110-120, `causal_override_confidence_threshold`). A163 in particular already documents a "the server only ever returns an aggregate `path_confidence`, never itemized supports/contradicts" workaround (see the inline comment at line 113-115) — re-check whether that workaround's threshold (0.3) is still the right value against real evidence, or whether it was a placeholder that was never revisited.
- `backlog/A150.md` — the weak-prediction override's reasoning (lines 126-129, `WEAK_PREDICTION_KINDS`).

For each, write down: what failure mode was this override built to prevent, and does step 1's real evidence show it correctly distinguishing that failure mode from genuine progress, or over-firing on genuine progress too?

### 3. Reach a verdict per override

For each of the four overrides, one of:
- **Correctly calibrated** — evidence supports keeping it as-is. State the specific evidence.
- **Needs adjustment** — state the specific change (a threshold value, a condition), backed by a specific trace example where the current behavior discarded real progress or failed to discard fake progress.
- **Insufficient evidence** — the available trace(s) didn't exercise this override's edge cases; note what kind of future run would provide better evidence, and leave the override unchanged (do not guess).

Implement only the changes that reached a clear "needs adjustment" verdict. Do not touch overrides that reached "correctly calibrated" or "insufficient evidence."

### 4. Address the KPI-usefulness critique

Read `scripts/graph_compliance_report.py`'s `report()` function (~lines 30-52) and `agents/arc4/telemetry.py`'s `_has_positive_graph_evidence` (~lines 14-33) in full. Decide whether to add a complementary metric, e.g.:

```python
# in report(): alongside the existing `grounded` count
informed = sum(1 for s in steps if s.get("graph_informed"))
# ...
"graph_informed_decision_rate": round(100 * informed / total, 2),
```

where `graph_informed` (a new step-snapshot field, added the same way `graph_grounded` already is in `telemetry.py::_step_snapshot`) is true whenever the executed candidate's `graph_evidence` was non-empty/non-fresh at all (any `attempts > 0`), regardless of whether it nets positive. This gives a rate that CAN move within a single unsolved-puzzle episode (rising as the graph accumulates attempt history), complementing `graph_grounded_decision_rate`'s longer-horizon "confirmed something works" signal rather than replacing it.

Only implement this if the audit concludes it adds real value — do not add it reflexively just because it's sketched above. If implemented, wire it through A198's existing `--append-history`/`--show-history` persistence (do not build a second history mechanism).

### 5. Write the finding

`backlog/A214.md`'s Outcome section: one paragraph per override's verdict (with the specific trace evidence cited), one paragraph on the KPI-usefulness decision (implemented or not, and why), and a summary of what code (if any) changed.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/evaluator.py` | Only the specific override(s) that reached a "needs adjustment" verdict; possibly also exposes override-fired flags in `EvaluationResult.metadata` for visibility (step 1) |
| `agents/arc4/telemetry.py` | Only if a new complementary metric is implemented (step 4) |
| `scripts/graph_compliance_report.py` | Only if a new complementary metric is implemented (step 4) |
| `backlog/A214.md` | Outcome section documents every override's verdict and the KPI decision |
| `tests/test_a214_*.py` (new) | Coverage matching whatever actually changed |
| `ARCHITECTURE.md` | Only if a KPI is added/changed — update near the A195-A198 compliance-measurement entries |

## Tests

Shape depends entirely on what changed. At minimum:
- If an override was adjusted: a regression test using the specific trace scenario that motivated the change, proving the old threshold/condition would have produced the wrong `meaningful_progress` value.
- If the informational override-fired metadata was added: a test confirming it's populated correctly and doesn't change `meaningful_progress`'s own value (data-only addition, zero decision weight — same discipline as A212's `veto_reason`/`veto_alternative_action_id` fields).
- If a complementary KPI was added: a unit test on `report()`'s new field, plus a regression test confirming the existing `graph_grounded_decision_rate` output is byte-for-byte unchanged.

## Validation commands

```bash
# If evaluator.py changed:
.venv/bin/python -m pytest tests/test_a214_*.py -v
.venv/bin/python -m pytest tests/ -q  # full suite, watch for evaluator.py regressions across other test files
# If telemetry.py / graph_compliance_report.py changed:
python3 scripts/graph_compliance_report.py artifacts/agent_execution_trace.json
make test-a
make test-all
```

## Assumptions/defaults

- Same discipline as A209/A212: if the evidence doesn't clearly support changing an override, leave it. A214 concluding "the four overrides are all correctly calibrated, no evaluator.py change" is a complete, valid outcome.
- Do not conflate this card's scope with A213's (the `record_transition`/`record_rule_evidence` no-op gate) — they were found in the same investigation but are independent fixes.
- If `make smoke` cannot be run live in the execution environment (no `CAMPY_MCP_CMD`, no real ARC API credentials), reuse the most recent `artifacts/agent_execution_trace.json` already in the repo tree if one exists and is recent, and say explicitly in the Outcome section which trace(s) were used and how fresh they were — do not fabricate trace evidence or reason abstractly without a real trace.
