# Plan: A194 — Make `_action_space_exhausted` Consult the Graph Before Terminating

## Card metadata

- ID: A194
- Priority: P2
- Layer: ARC runtime
- Dependencies: A135

## Summary

`Evaluator._action_space_exhausted` decides whether to end the episode using only a flat per-action attempt-count threshold. `Evaluator` already holds `self._graph_query_port` (set in `__init__`, `evaluator.py:30`) and the runtime already has `fetch_untested_actions()` (A135, used today in `plan_generator.py:454`). Wire the two together: don't terminate purely on repeat-count if the graph says untested alternatives remain.

## Technical approach

1. Read `agents/arc4/evaluator.py` in full before editing — confirm current line numbers for `_action_space_exhausted` and its call site in `evaluate()`.
2. Change `_action_space_exhausted` to return `tuple[bool, str]` (exhausted, source) instead of a bare `bool`, so `evaluate()` can attach *why* to `EvaluationResult.metadata` without re-deriving it:
   ```python
   def _action_space_exhausted(self, execution: ExecutionResult, current_attempt_count: int) -> tuple[bool, str]:
       metadata = execution.metadata if isinstance(execution.metadata, Mapping) else {}
       if bool(metadata.get("action_space_exhausted")) or bool(metadata.get("exhausted_action_space")):
           return True, "env_reported"
       if current_attempt_count < self._limits.exhausted_action_attempt_threshold:
           return False, ""
       if self._graph_query_port is not None:
           fetch_untested = getattr(self._graph_query_port, "fetch_untested_actions", None)
           if fetch_untested is not None:
               try:
                   untested = fetch_untested()
                   if untested:
                       return False, ""
                   return True, "graph_confirmed_no_untested"
               except Exception:
                   pass
       return True, "threshold_only"
   ```
3. Update the call site in `evaluate()` (~line 57: `action_space_exhausted = self._action_space_exhausted(execution, current_attempt_count)`) to unpack the tuple: `action_space_exhausted, exhaustion_source = self._action_space_exhausted(execution, current_attempt_count)`. Thread `exhaustion_source` into the `EvaluationResult.metadata` dict being built later in the same method, only when `action_space_exhausted` is `True` (leave the key absent otherwise, don't set it to `""`).
4. Note the control flow: the threshold check happens *before* the graph check (early-return `(False, "")` when under threshold, avoiding an unnecessary graph call on every single evaluation) — only consult the graph once the local threshold is already crossed and the code is about to decide whether to terminate.
5. Do not change `EvaluationLimits.exhausted_action_attempt_threshold`'s default (4) — this card changes when the threshold's crossing terminates the episode, not the threshold itself.
6. Do not touch the env-reported flag check's precedence (`metadata.get("action_space_exhausted")` / `"exhausted_action_space"`) — that branch returns `(True, "env_reported")` unconditionally and is checked first, before the graph-aware logic.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/evaluator.py` | `_action_space_exhausted`: consult `self._graph_query_port.fetch_untested_actions()` before terminating on threshold alone |
| `tests/test_a194_graph_driven_termination.py` (new) | Coverage (see Tests) |

## Tests

New `tests/test_a194_graph_driven_termination.py`:

1. Mock `graph_query_port.fetch_untested_actions()` returns `["ACTION2", "ACTION3"]`; `current_attempt_count = 5` (above default threshold 4) — `_action_space_exhausted` returns `(False, "")`.
2. Same mock port, `fetch_untested_actions()` returns `[]`; same attempt count — returns `(True, "graph_confirmed_no_untested")`.
3. `graph_query_port=None`; attempt count above threshold — returns `(True, "threshold_only")` (today's exact termination behavior, now with a source label, no crash).
4. Mock port present but lacking `fetch_untested_actions` attribute entirely — returns `(True, "threshold_only")` (degrades via `getattr(..., None)`, no crash).
5. Mock port whose `fetch_untested_actions()` raises an exception — returns `(True, "threshold_only")` (degrades via the `try/except`, no crash, no exception propagates).
6. `metadata.get("action_space_exhausted") = True` on the execution: returns `(True, "env_reported")` immediately, `fetch_untested_actions` is never called (assert the mock's call count is 0) — confirms the env-signal short-circuit still takes priority and skips the graph check entirely.
7. `current_attempt_count` below threshold: returns `(False, "")` without ever calling `fetch_untested_actions` (assert call count 0) — confirms the graph is only consulted once actually deciding whether to terminate, not on every evaluation.
8. `evaluator.py::evaluate()` end-to-end: when termination fires via each of the three sources above, `EvaluationResult.metadata["exhaustion_source"]` matches; when `action_space_exhausted` is `False`, the key is absent from metadata (not set to `""`).

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a194_graph_driven_termination.py -v
.venv/bin/python -m pytest tests/test_a180_*.py tests/test_a163_*.py -v
make test-a
make test-all
```

Live confirmation: best-effort via `make smoke`, needs a run where one action repeats past threshold (4 by default) while other actions remain genuinely untested — per the established live-confirm caveat used throughout the A182-A189 family (may not reproduce in a single short run).

## Assumptions/defaults

- This card's fix only prevents premature termination; it does not change what the planner does with the extra time (A178's existing VoI bonus already biases toward untested actions, so the expected behavior is the planner naturally moves to an untested alternative on the next replan — this card doesn't need to force that, just needs to not cut the episode off before it can happen).
- If `fetch_untested_actions()` is expensive or rate-limited server-side, this card accepts the extra call only at the moment termination is about to be decided (gated behind the attempt-count threshold check), not on every step — call frequency should be no worse than roughly once per `exhausted_action_attempt_threshold`-sized run of repeated attempts on a single action.
