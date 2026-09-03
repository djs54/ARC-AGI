# A251 — LLM Call Exception Safety: Plan

## Card metadata

- Card: `backlog/A251.md`
- Depends on: A205 (`resolve_llm_vote`'s reference pattern), A237 (`_degraded` flag / `plan_degraded` precedent), A244 (most recent instance of the same phase-visibility extension)

## Design (confirmed by direct read before writing this plan)

- `agents/arc4/goal_resolver.py:484-510` — `_query_llm`, called from `resolve()` at line 76 (`llm_patch = self._query_llm(llm_port, perception, hypotheses)`), no try/except anywhere in between.
- `agents/arc4/plan_generator.py:621-654` — `_query_llm`, called from `generate()` at line 134, same shape.
- `agents/arc4/annatar_signals.py:369-402` — `resolve_llm_vote`, the reference pattern: `try: response = llm_port.chat(...); raw_vote = _parse_transition_vote(response); if raw_vote is None: return EXPLORING; return InvestigationState(raw_vote) except Exception: return EXPLORING`.
- `agents/arc4/plan_generator.py:103,127,157,225,253,614` — `PlanGenerator`'s existing `self._degraded` instance-scratch flag (A237): reset at `__init__` and top of `generate()`, read into `PlanningResult(degraded=self._degraded, ...)`, set `True` at two existing graph-failure sites.
- `agents/arc4/types.py` — `PlanningResult.degraded`, `WorkflowState.plan_degraded`/`vet_degraded`/`evaluate_degraded` (A237/A244) — the exact shape `ResolvedGoal.degraded`/`WorkflowState.resolve_degraded` should mirror.
- `agents/arc4/workflow.py` — confirm current `resolve()` call site count by grepping `self._dependencies.resolve(` (A237's own card emphasized enumerating call sites directly rather than assuming a count — do the same here).
- `agents/arc4/telemetry.py` — per-cycle summary dict, where `plan_degraded`/`vet_degraded`/`evaluate_degraded`/`annatar_degraded` are already surfaced via `bool(getattr(state, "<field>_degraded", False))`.

### The fix

**1. `goal_resolver.py::_query_llm`:**

```python
def _query_llm(
    self,
    llm_port: LLMPort,
    perception: PerceptionSnapshot,
    hypotheses: Sequence[GoalHypothesis],
) -> dict[str, Any] | None:
    messages = [...]  # unchanged
    try:
        response = llm_port.chat(messages)
        return self._parse_llm_response(response)
    except Exception:
        # A251: mirrors annatar_signals.py::resolve_llm_vote's own
        # already-proven pattern (A205) -- a bounded single attempt,
        # degrading to the same "no LLM patch" outcome the caller already
        # handles (`if llm_patch is not None:`) rather than letting a raised
        # exception propagate to workflow.py's outer except and crash the
        # whole episode. See backlog/A251.md.
        self._degraded = True
        return None
```

(Illustrative — confirm `self._degraded` exists on `GoalResolver` first; per this plan's Step 2 below, it doesn't yet and needs to be added as part of this same card.)

**2. `plan_generator.py::_query_llm`:** identical shape, reusing the already-existing `self._degraded` flag (no new field needed on this class — just one more `except Exception: self._degraded = True` site, consistent with the two that already exist at lines 225/253).

**3. `GoalResolver`'s new `_degraded` infrastructure**, mirroring `PlanGenerator`'s exact shape (A237):

```python
class GoalResolver:
    def __init__(self, ...):
        ...
        self._degraded = False  # A251, mirrors PlanGenerator._degraded (A237)

    def resolve(self, ...) -> PhaseResult[ResolvedGoal]:
        self._degraded = False  # reset at top, same as PlanGenerator.generate()
        ...
        return PhaseResult(..., payload=ResolvedGoal(..., degraded=self._degraded))
```

Confirm `resolve()`'s actual current signature/return-construction shape before writing this — read the whole function first, don't assume the return statement's exact shape from this sketch.

**4. `types.py`:** `ResolvedGoal.degraded: bool = False` (wired into `to_dict()`/`from_dict()`, matching `PlanningResult.degraded`'s exact treatment); `WorkflowState.resolve_degraded: bool = False` (+ `to_dict`/`from_dict`).

**5. `workflow.py`:** enumerate every `self._dependencies.resolve(...)` call site (grep first, don't assume a count — A237's own card found this varies by phase). At each, set `state.resolve_degraded = getattr(resolved_goal_payload, "degraded", False)` immediately after the call, before any branching — same placement convention as `plan_degraded`/`vet_degraded`/`evaluate_degraded`.

**6. `telemetry.py`:** add `"resolve_degraded": bool(getattr(state, "resolve_degraded", False))` to the per-cycle summary dict, same location as the other four.

## Implementation approach

### Files

- Modify: `agents/arc4/goal_resolver.py` — `_query_llm`'s try/except, `__init__`'s new `self._degraded`, `resolve()`'s reset + payload construction.
- Modify: `agents/arc4/plan_generator.py` — `_query_llm`'s try/except (reusing existing `self._degraded`).
- Modify: `agents/arc4/types.py` — `ResolvedGoal.degraded`, `WorkflowState.resolve_degraded`.
- Modify: `agents/arc4/workflow.py` — every `resolve` call site.
- Modify: `agents/arc4/telemetry.py` — per-cycle summary dict.
- Test: new `tests/test_a251_llm_call_exception_safety.py`.

### TDD

- New test: `goal_resolver.py` — a fake `LLMPort` whose `chat()` raises → `resolve()` completes without propagating, returns a `ResolvedGoal` with `degraded=True`, and the pre-LLM hypothesis ranking is used unchanged (same as if `_should_escalate_to_llm` had returned `False`).
- New test: `plan_generator.py` — same shape, `PlanningResult.degraded=True`, pre-LLM candidate ranking used unchanged.
- New test (each file): a non-raising, healthy `LLMPort.chat()` → `degraded=False`, confirming the reset-at-top-of-call convention works and a real LLM patch still applies normally when the call succeeds.
- New test (each file): `llm_port=None` (no LLM configured at all) → `degraded=False` — the same "not configured is not the same as failed" distinction A237 itself emphasized; confirm this doesn't accidentally get conflated with the failure path.
- New test: `WorkflowState.resolve_degraded` correctly reflects the most recent `resolve` call's `degraded` value, set at the right call site(s) in `workflow.py` — enumerate and test every real call site found, not just one.
- New test: `telemetry.py`'s per-cycle summary surfaces `resolve_degraded` correctly, defaulting `False` when `state` is missing the field or is `None` — mirroring A244's own equivalent test.
- Regression: existing `goal_resolver.py`/`plan_generator.py`-adjacent tests continue to pass with `degraded` defaulting `False` wherever not specifically exercised; existing tests around the two `_query_llm` sites (parsing, escalation gating) unchanged.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a251_llm_call_exception_safety.py -v
.venv/bin/python -m pytest tests/test_arc4_goal_resolver.py tests/test_arc4_plan_generator.py -v
make test-a
make test-all
```

(Confirm the actual existing test file names before running — `test_arc4_goal_resolver.py`/`test_arc4_plan_generator.py` are guesses based on this repo's naming convention; check `tests/` directly.)

### Live-verify

Same environment/discipline as every prior card this investigation (`CAMPY_MCP_CMD` pointing at the sibling `hippocampy` repo, `campy status` check first, full `tee`'d output to a log file read completely, generous timeout). A healthy run should show `resolve_degraded`/`plan_degraded` both `false` throughout -- this is a regression check confirming the new code path doesn't fire under normal conditions, not a demonstration of the new behavior (which needs a real LLM failure, not reliably reproducible on demand). The TDD suite (a fake raising `LLMPort` exercising the real `resolve()`/`generate()`/`workflow.py` path end-to-end) is the primary evidence for the new failure-handling behavior itself, same authorized standard as A237/A244's own degraded-scenario verification.

## Assumptions/defaults

- Mirror `resolve_llm_vote`'s exact scope (wrap the `chat()` call and its immediate parse, nothing more) and `PlanGenerator._degraded`'s exact instance-scratch-flag shape (A237) -- no new mechanism invented.
- One combined `degraded` bit per phase (not split by failure reason) -- no known consumer needs the distinction, same default A237/A244 both chose.
