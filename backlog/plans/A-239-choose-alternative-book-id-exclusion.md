# A239 — `_choose_alternative` book_id Exclusion Fix: Plan

## Card metadata

- Card: `backlog/A239.md`
- Depends on: A185 (`book_id != action_id` for `ACTION6`), A188 (fixed half of this same function's defect), A238 (adjacent finding, same investigation thread)

## Design (settled — mechanical fix, no open questions)

Confirmed by direct read of the current code (`agents/arc4/plan_vetter.py:209-223`) and a live reproduction before writing this plan — see the card's Problem section for both.

### The fix

```python
def _choose_alternative(
    self,
    state: WorkflowState,
    candidate: PlanCandidate,
    alternatives: tuple[PlanCandidate, ...],
) -> PlanCandidate | None:
    candidate_book_id = str(candidate.metadata.get("book_id") or candidate.action_id) if candidate.metadata else candidate.action_id
    for alternative in alternatives:
        alt_book_id = str(alternative.metadata.get("book_id") or alternative.action_id) if alternative.metadata else alternative.action_id
        if alt_book_id == candidate_book_id:
            continue
        if int(state.action_attempt_counts.get(alt_book_id, 0)) == 0:
            return alternative
    if state.latest_veto_alternative is not None:
        veto_alt_book_id = str(state.latest_veto_alternative.metadata.get("book_id") or state.latest_veto_alternative.action_id) if state.latest_veto_alternative.metadata else state.latest_veto_alternative.action_id
        if veto_alt_book_id != candidate_book_id:
            if int(state.action_attempt_counts.get(veto_alt_book_id, 0)) == 0:
                return state.latest_veto_alternative
    return None
```

**Check during implementation whether `PlanCandidate` already exposes a `.book_id` property/field directly** (the card's repro used `PlanCandidate(..., book_id="ACTION6@10,10", metadata={"book_id": "ACTION6@10,10"})` — both a direct field and a metadata key were set in the repro for safety, but the real object shape may only need one). Read `agents/arc4/types.py::PlanCandidate`'s actual field list before writing the fix — if `.book_id` is already a first-class attribute (not just metadata), use it directly (`alternative.book_id`, `candidate.book_id`) rather than the `metadata.get(...)` resolution shown above, which was written defensively without confirming the exact current shape. Mirror whatever key-resolution convention `vet()` itself already uses for `candidate_book_id` earlier in the same function (it already computes `candidate_book_id = candidate.book_id` at line 71 per the current file — reuse that, don't re-derive it a second way inside `_choose_alternative`).

### Why book_id, not a new field or a different comparison

This is not a new design decision — A185 already established `book_id` as the correct per-coordinate identity for `ACTION6`, and A188 already applied it to the attempt-count lookup in this exact function. This card completes that same, already-settled convention for the one line A188 missed. No alternative design to weigh.

## Implementation approach

### Files

- Modify: `agents/arc4/plan_vetter.py::_choose_alternative` — the two `action_id`-based comparisons (main loop exclusion, `latest_veto_alternative` fallback).
- Test: new `tests/test_a239_choose_alternative_book_id_exclusion.py` (or extend `tests/test_a188_vetter_keys_by_book_id.py` directly, since this is a direct continuation of that card's own scope — check which reads more naturally given the existing file's structure before deciding).

### TDD

- New test: exact reproduction from the card — a falsified `ACTION6@x,y` candidate (falsifications >= `repeated_falsification_threshold`) with a fresh `ACTION6@x',y'` alternative present in `plan.alternatives`. Before the fix: `approved=True`, `alternative=None`. After the fix: `approved=False`, `alternative` is the fresh coordinate, `should_replan=True`.
- New test: same shape but for `excessive_repetition_threshold` (candidate attempted >= threshold with weak score) instead of `repeated_falsification_threshold` — confirm this veto path also now correctly finds the fresh alternative.
- New test: a candidate must never be offered as an "alternative" to itself — construct `alternatives` containing an entry with the exact same `book_id` as `candidate` (e.g. a duplicate/re-scored copy) and confirm `_choose_alternative` skips it, same as before this fix (this is a regression guard, not new behavior — the old `action_id` check accidentally also caught this case, the fix must not lose it).
- New test: the `state.latest_veto_alternative` fallback path — construct a scenario where `plan.alternatives` offers nothing usable but `state.latest_veto_alternative` is a fresh `ACTION6` coordinate different from the vetoed candidate's own coordinate; confirm it's now correctly offered (previously blocked by the same `action_id` comparison).
- Regression: run every existing test in `tests/test_a188_vetter_keys_by_book_id.py` unchanged — all must still pass, since those scenarios (`ACTION1` vs `ACTION6` pairings) were never affected by the bug and must not be affected by the fix either.
- Regression: `tests/test_arc4_plan_vetter.py` (or equivalent existing suite) unchanged.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a239_choose_alternative_book_id_exclusion.py -v
.venv/bin/python -m pytest tests/test_a188_vetter_keys_by_book_id.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline, capture full raw output via `tee` and read the complete file, never a truncated tail). Run a live smoke on a click-heavy puzzle (a puzzle shaped like `vc33-5430563c`/`g50t-5849a774` — many `ACTION6` candidates, coordinates that get falsified quickly) and confirm, via the vet-decision trace or a targeted temporary debug log (removed before commit, same discipline as prior cards' live-verify steps), that a falsified click coordinate actually gets swapped for a fresh one within the same cycle at least once during the run — not just that the unit test passes. The puzzle is randomly assigned each run; if this specific scenario doesn't arise naturally within the 30-step smoke budget, say so honestly and rely on the TDD coverage as primary evidence, per this session's standing discipline against overclaiming live verification that didn't happen.

## Assumptions/defaults

- This is a narrow, mechanical bug fix with a single correct design (already established by A185/A188) — no "investigate before committing" branch point exists here the way other recent cards had one.
