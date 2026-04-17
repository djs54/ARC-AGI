# B134 Plan: Gate Unconditional `current_truth` Recall in Runner

## Summary

`runner.py` calls `current_truth("What did I learn from similar puzzles?")` after every action, unconditionally. This plan adds a simple conditional gate so the recall fires only when it could produce actionable context.

## Current Code Location

`agents/arc3/runner.py`, approximately line 210–220, inside the main step loop:

```python
# Current (fires every step unconditionally)
recall_query = "What did I learn from similar puzzles?"
await adapter.ingest_step(
    ...,
    recall_query=recall_query,
)
```

## Technical Approach

### Step 1: Identify the gate conditions

Fire recall only when:
```python
should_recall = (
    step_num == 0
    or self._consecutive_no_progress_steps >= 2
    or bool((self._hypothesis_context or {}).get("loop_detected"))
)
```

### Step 2: Wrap the call

```python
if should_recall:
    recall_query = "What did I learn from similar puzzles?"
    await adapter.ingest_step(
        ...,
        recall_query=recall_query,
    )
else:
    # Still call ingest_step but without recall_query (or pass None/empty)
    await adapter.ingest_step(
        ...,
        recall_query=None,
    )
    # Emit trace note so the timeline shows the gate fired
    # (only if orchestrator trace is accessible from runner; else skip)
```

If `ingest_step` requires `recall_query` as non-None, pass `""` and check whether the adapter skips an empty-string query gracefully.

### Step 3: Confirm `_consecutive_no_progress_steps` tracking

Verify `self._consecutive_no_progress_steps` is already tracked in `runner.py`. If it doesn't exist:
- Count steps where `progress_score` (from solve context or frame delta) did not improve vs. previous step.
- Increment counter when no improvement; reset to 0 on progress.

Do **not** add this tracking if it already exists under a different name — find the existing attribute first.

### Step 4: Confirm `_hypothesis_context` access

`runner.py` creates the orchestrator and can access `orchestrator._hypothesis_context`. Access it directly:

```python
loop_detected = bool(
    (self._orchestrator._hypothesis_context or {}).get("loop_detected")
)
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/runner.py` | Wrap `recall_query` + `ingest_step` recall call in `if should_recall:` gate using step_num, consecutive_no_progress, loop_detected conditions |

## Validation Commands

```bash
# Run 15-step smoke test
ARC_API_KEY="$(jq -r '.key' benchmarks/arc3/.arc/arc.json)" \
  .venv/bin/python run_single_puzzle.py --puzzle-id 007bbfb7 --max-steps 15

# Count "What did I learn" recall events — should be ≤3
jq '[.[] | select(.event == "current_truth" and (.data.query // "" | contains("similar puzzles")))] | length' master_timeline.json

# Run tests
cd sidequests-brain && .venv/bin/pytest -q tests/
```

## Acceptance Criteria (checklist)

- [ ] `current_truth("What did I learn from similar puzzles?")` fires at most 3× in a 15-step run (step 0 + ≤2 stuck/loop events)
- [ ] `ingest_step` is still called every step (just without recall_query when gated off)
- [ ] No existing tests broken
- [ ] Trace shows reduced recall noise on happy-path runs

## Notes / Risks

- Check if `ingest_step` signature allows `recall_query=None` — if it requires a string, pass `""` instead and verify adapter skips empty queries.
- Do not add `_consecutive_no_progress_steps` tracking if a similar counter already exists under a different name.
- Scope: only gate this one recall call. Do not refactor other `ingest_step` calls in this PR.
