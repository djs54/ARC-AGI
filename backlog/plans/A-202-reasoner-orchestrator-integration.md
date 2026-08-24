# Plan: A202 — Wire the Reasoner Into `WorkflowOrchestrator`

## Card metadata

- ID: A202
- Priority: P1
- Layer: ARC runtime
- Dependencies: A200, A201

## Summary

Integrate A200's pure state machine and A201's graph client into `WorkflowOrchestrator.run()`. This is the card where the Reasoner's decision starts actually driving control flow. Read `agents/arc4/workflow.py` in full, current state, before starting — it was modified by A190 and A194 earlier this session; do not trust this plan's quoted line numbers without reconfirming them first.

## Technical approach

### 1. `agents/arc4/ports.py` — new Protocol and field

Read the file's current `GraphQueryPort`/`PerceivePhase`/etc. Protocol definitions in full first (they establish the exact style to match). Add:

```python
@runtime_checkable
class ReasonerPhase(Protocol):
    def __call__(
        self,
        state: WorkflowState,
        perception: PerceptionSnapshot,
        execution: ExecutionResult,
        evaluation: EvaluationResult,
        *,
        graph_port: GraphQueryPort | None = None,
    ) -> "ReasonerOutcome": ...
```

Add a small `ReasonerOutcome` dataclass (in `types.py`, alongside the other workflow dataclasses — not in `ports.py`, matching where `PhaseResult`/`VetDecision`/etc. already live):

```python
@dataclass(slots=True)
class ReasonerOutcome:
    decision: str  # one of investigation_reasoner.ReasonerDecision's values, as a plain str to avoid types.py importing investigation_reasoner.py (keep the dependency direction one-way: reasoner modules may import types.py, not the reverse)
    anchor_ref: Any | None = None
    anchor_type: str | None = None  # "goal" | "entity" | None
    required_action_id: str | None = None  # set only for REPEAT_RETRY -- the exact action to re-propose
    required_book_id: str | None = None
```

Add to `WorkflowDependencies`:

```python
@dataclass(slots=True)
class WorkflowDependencies:
    perceive: PerceivePhase
    resolve: ResolvePhase
    plan: PlanPhase
    vet: VetPhase
    execute: ExecutePhase
    evaluate: EvaluatePhase
    reason: ReasonerPhase | None = None
```

### 2. `agents/arc4/reasoner_signals.py` (new)

```python
"""Turns real runtime state (WorkflowState, perception, execution,
evaluation, graph queries) into investigation_reasoner.CycleSignals.
Deliberately separate from investigation_reasoner.py (which must stay
zero-I/O) and from workflow.py (which must stay thin, per its own
existing design principle)."""

from __future__ import annotations

from typing import Any

from .investigation_reasoner import CycleSignals
from .ports import GraphQueryPort
from .types import EvaluationResult, ExecutionResult, PerceptionSnapshot, WorkflowState


def compute_cycle_signals(
    state: WorkflowState,
    perception: PerceptionSnapshot,
    execution: ExecutionResult,
    evaluation: EvaluationResult,
    *,
    anchor_ref: Any,
    anchor_type: str,
    deepening_cycle_count: int,
    already_retried: bool,
    graph_port: GraphQueryPort | None = None,
) -> CycleSignals:
    meaningful_progress = bool(evaluation.meaningful_progress)

    confidence = 0.0
    untested_remaining = True
    all_falsified = False
    if graph_port is not None:
        fetch_neighborhood = getattr(graph_port, "fetch_entity_neighborhood", None)
        if anchor_type == "entity" and fetch_neighborhood is not None:
            try:
                neighborhood = fetch_neighborhood(anchor_ref)
                live_items = [h for h in neighborhood.get("hypotheses", []) + neighborhood.get("rules", []) if not h.get("falsified")]
                confidence = max((h.get("confidence", 0.0) for h in live_items), default=0.0)
            except Exception:
                pass
        fetch_untested = getattr(graph_port, "fetch_untested_actions", None)
        if fetch_untested is not None:
            try:
                untested_remaining = bool(fetch_untested())
            except Exception:
                untested_remaining = True

    # execution_inconclusive: no clear grid change and no explicit progress signal --
    # confirm the exact field names against evaluator.py's real EvaluationResult.metadata
    # shape before finalizing (evaluator.py sets "grid_changed" in its metadata dict per
    # earlier cards this session -- re-verify, don't assume from this plan alone).
    exec_meta = execution.metadata if isinstance(execution.metadata, dict) else {}
    execution_inconclusive = not bool(exec_meta.get("grid_changed", False)) and not meaningful_progress

    return CycleSignals(
        meaningful_progress=meaningful_progress,
        confidence=confidence,
        untested_remaining=untested_remaining,
        all_falsified=all_falsified,
        execution_inconclusive=execution_inconclusive,
        deepening_cycle_count=deepening_cycle_count,
        already_retried=already_retried,
    )
```

Before finalizing, read `agents/arc4/evaluator.py`'s current `EvaluationResult.metadata` construction (the `metadata_dict = {...}` literal established by A194/A195/A197) and `agents/arc4/graph_queries.py`'s current `fetch_untested_actions`/`fetch_entity_neighborhood` signatures in full, and correct any field-name mismatch between what's sketched above and what actually exists — this plan's sketch is a structural guide, not a guarantee every field name is still exactly right after several other cards touched these files this session.

### 2a. `agents/arc4/reasoner_signals.py` — the actual `ReasonerPhase` implementation

`compute_cycle_signals` alone is not a `ReasonerPhase` — it only builds one input to A200's pure functions. This card must also define the glue function that actually implements the `ReasonerPhase` Protocol (§1) and gets passed as `WorkflowDependencies.reason=...` when constructing the orchestrator (find where `WorkflowDependencies(...)` is currently constructed — likely `bundle.py` — and add `reason=run_reasoner_cycle` there, gated the same way other optional capabilities are, e.g. behind a feature flag or simply always-on once this card lands; confirm the right gating convention by checking how similarly-staged capabilities were introduced, e.g. A192's entity-neighborhood work).

First, add the in-memory anchor-tracking field this glue function needs to `WorkflowState` (`agents/arc4/types.py`), since "which anchor is currently active, and for how many deepening cycles" must persist across cycles within one attempt and can't be recomputed from scratch each time:

```python
active_investigation_anchor: dict[str, Any] | None = None
# Shape when set: {"anchor_ref": ..., "anchor_type": "goal"|"entity",
# "thread_id": ..., "state": "exploring", "deepening_cycle_count": 0,
# "already_retried": False}
```

Serialize it in `to_dict()`/`from_dict()` matching the dataclass's existing convention for optional dict fields.

```python
def run_reasoner_cycle(
    state: WorkflowState,
    perception: PerceptionSnapshot,
    execution: ExecutionResult,
    evaluation: EvaluationResult,
    *,
    graph_port: GraphQueryPort | None = None,
    llm_port: LLMPort | None = None,
) -> ReasonerOutcome:
    """The actual ReasonerPhase: resolves the current investigation thread's
    state via investigation_reasoner's pure functions, persists the result
    through A201's graph client, and returns the orchestrator-facing
    decision. If state.active_investigation_anchor is None (fresh attempt,
    or previous thread just ended), pick a starting anchor from the current
    goal/execution before reasoning -- the exact anchor-selection rule is:
    prefer the just-executed candidate's entity_ref if it has one (a click
    just happened, that's the natural next anchor), else the active goal's
    goal_id. Confirm this is a sensible default by reading how A203 expects
    to consume anchor_ref/anchor_type before finalizing -- the two cards
    must agree on shape."""
    anchor = state.active_investigation_anchor
    if anchor is None:
        cand_meta = execution.candidate.metadata if execution.candidate is not None else {}
        entity_ref = cand_meta.get("entity_ref") if isinstance(cand_meta, dict) else None
        if entity_ref is not None:
            anchor_ref, anchor_type = entity_ref, "entity"
        else:
            anchor_ref, anchor_type = state.active_goal.selected.goal_id if state.active_goal else None, "goal"
        thread_id = None
        if graph_port is not None:
            start_or_resume = getattr(graph_port, "start_or_resume_thread", None)
            if start_or_resume is not None:
                try:
                    result = start_or_resume(anchor_ref, anchor_type)
                    thread_id = result.get("thread_id")
                except Exception:
                    thread_id = None
        anchor = {
            "anchor_ref": anchor_ref, "anchor_type": anchor_type, "thread_id": thread_id,
            "state": InvestigationState.EXPLORING.value, "deepening_cycle_count": 0, "already_retried": False,
        }

    current_state = InvestigationState(anchor["state"])
    signals = compute_cycle_signals(
        state, perception, execution, evaluation,
        anchor_ref=anchor["anchor_ref"], anchor_type=anchor["anchor_type"],
        deepening_cycle_count=anchor["deepening_cycle_count"], already_retried=anchor["already_retried"],
        graph_port=graph_port,
    )

    if current_state == InvestigationState.AWAITING_LLM:
        # A205 defines the actual bounded-retry LLM call and failure
        # handling this delegates to -- this card only needs the call point
        # to exist and to feed apply_llm_vote, not the retry/failure logic
        # itself.
        vote = resolve_llm_vote(llm_port, state, signals)  # A205 implements this function's body
        new_state = apply_llm_vote(vote, signals)
    else:
        new_state = transition(current_state, signals)

    write_thread_state = getattr(graph_port, "write_thread_state", None) if graph_port is not None else None
    if write_thread_state is not None and anchor["thread_id"] is not None:
        try:
            write_thread_state(anchor["thread_id"], new_state.value)
        except Exception:
            pass

    decision = decision_for_state(new_state)
    if decision == ReasonerDecision.ADVANCE:
        state.active_investigation_anchor = None  # thread ended, next cycle picks a fresh anchor
    else:
        anchor["state"] = new_state.value
        if new_state == InvestigationState.DEEPENING:
            anchor["deepening_cycle_count"] += 1
        anchor["already_retried"] = new_state == InvestigationState.RETRY
        state.active_investigation_anchor = anchor

    return ReasonerOutcome(
        decision=decision.value,
        anchor_ref=anchor["anchor_ref"] if decision != ReasonerDecision.ADVANCE else None,
        anchor_type=anchor["anchor_type"] if decision != ReasonerDecision.ADVANCE else None,
        required_action_id=execution.action_id if decision == ReasonerDecision.REPEAT_RETRY else None,
        required_book_id=getattr(execution.candidate, "book_id", None) if decision == ReasonerDecision.REPEAT_RETRY else None,
    )
```

`resolve_llm_vote` is declared here as the call point but its actual body (the LLM call itself, retries, timeout, parsing) is A205's responsibility, not this card's — implement it in this card only as a placeholder that raises `NotImplementedError` if reached without A205 having landed yet, so the gap is loud rather than silent; replace with the real implementation when A205 lands, in the same file.

### 3. `agents/arc4/workflow.py` — the hook point

Read the current `WorkflowOrchestrator.run()` in full first. Locate the line `state.step_index += 1` (was line 144 as of this plan's writing) and the `check_stall(...)` call immediately after it (~lines 161-168). Replace that `check_stall` block's standalone `return self._finish(...)` with: compute `CycleSignals` (folding `check_stall`'s own result in as one of the inputs — e.g. a `stalled` boolean, or map it into `all_falsified`/`execution_inconclusive` as appropriate given what `check_stall` actually measures; decide during implementation which `CycleSignals` field(s) it should feed and document the mapping in a comment, since the spec doesn't prescribe this exact mapping), call `self._dependencies.reason(...)` if `reason is not None`, and branch on the returned `ReasonerOutcome.decision`:

```python
if self._dependencies.reason is not None:
    outcome = self._dependencies.reason(state, perception_payload, execution_payload, evaluation_payload, graph_port=...)
    if outcome.decision == "terminate":
        return self._finish(state, WorkflowStatus.TERMINATED, "reasoner_exhausted", phase_results)
    if outcome.decision in ("repeat_deepen", "repeat_retry"):
        state.reasoner_anchor_hint = outcome  # new WorkflowState field, consumed by A203
    else:
        state.reasoner_anchor_hint = None
else:
    # today's exact existing behavior, byte-for-byte, when no Reasoner is configured
    stall_reason = check_stall(...)
    if stall_reason is not None:
        return self._finish(state, WorkflowStatus.STALLED, stall_reason, phase_results)
```

The `graph_port=...` argument needs a real source — check whether `WorkflowOrchestrator` already has access to a `graph_port` reference anywhere (it may not; `plan_vetter.py` and `evaluator.py` each hold their own). If `WorkflowOrchestrator` doesn't currently have one, this card needs to add it as a constructor parameter, threaded through the same way it reaches `plan_vetter`/`evaluator` today (read `bundle.py`'s construction of these objects to find the right pattern to mirror) — confirm and adapt rather than guessing a source.

Add `reasoner_anchor_hint: ReasonerOutcome | None = None` to `WorkflowState` (`agents/arc4/types.py`), serialized in `to_dict()`/`from_dict()` matching the dataclass's existing convention for optional fields.

**Whole-episode `ADVANCE`-but-nothing-left handling:** when `outcome.decision == "advance"`, this card does *not* need special handling beyond clearing `reasoner_anchor_hint` — the existing `check_stall`-equivalent exhaustion logic (now feeding `CycleSignals` instead of independently deciding) already captures "nothing left anywhere" via `untested_remaining`/`all_falsified` at the *next* cycle's `EXPLORING` evaluation, which will naturally resolve to `EXHAUSTED` → `ADVANCE` again with truly nothing to advance to, at which point `resolve`'s own existing fallback behavior (unchanged by this card) determines what happens. Do not add a second, separate "is there really nothing left" check here — that would duplicate logic A203/the existing `resolve` phase already owns.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/ports.py` | `ReasonerPhase` Protocol; `WorkflowDependencies.reason` field |
| `agents/arc4/types.py` | `ReasonerOutcome` dataclass; `WorkflowState.reasoner_anchor_hint` field |
| `agents/arc4/workflow.py` | Hook point; `check_stall` becomes a signal input, not a return path |
| `agents/arc4/reasoner_signals.py` (new) | `compute_cycle_signals` |
| `tests/test_a202_reasoner_orchestrator_integration.py` (new) | Coverage, see Tests |

## Tests

`tests/test_a202_reasoner_orchestrator_integration.py`:

1. `WorkflowDependencies(reason=None, ...)` — full `run()` produces byte-for-byte identical output (status, reason string, phase_results) to a captured baseline from before this card's changes, for at least two existing scenario fixtures already used elsewhere in the test suite (reuse, don't invent new fixtures for this specific regression check).
2. `WorkflowDependencies(reason=<mock returning ReasonerOutcome(decision="terminate")>, ...)` — `run()` returns `WorkflowStatus.TERMINATED` with reason `"reasoner_exhausted"`.
3. `WorkflowDependencies(reason=<mock returning decision="repeat_deepen">, ...)` — `state.reasoner_anchor_hint` is set after the cycle; loop continues (does not return).
4. `WorkflowDependencies(reason=<mock returning decision="advance">, ...)` — `state.reasoner_anchor_hint` is cleared to `None`; loop continues.
5. A stall condition (mirroring an existing `check_stall`-triggering test fixture) with `reason` configured: confirm the stall signal reaches `compute_cycle_signals`/the mock reasoner call (assert what it was called with), rather than independently ending the run via the old standalone path.
6. `termination_from_evaluation`'s check still fires and short-circuits before the Reasoner is even called, when the evaluator reports a real terminal decision — assert the mock reasoner's call count is 0 in that case.
7. `check_budget` unaffected — still checked at the very top of the loop, before anything else, regardless of `reason` being configured or not.
8. `compute_cycle_signals` unit tests (separate from the orchestrator integration tests above): mock `graph_port` returning various `fetch_entity_neighborhood`/`fetch_untested_actions` shapes, confirm the resulting `CycleSignals` fields match; `graph_port=None` produces safe defaults without raising.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a202_reasoner_orchestrator_integration.py -v
.venv/bin/python -m pytest tests/test_arc4_workflow.py -v
make test-a
make test-all
```

`tests/test_arc4_workflow.py` (or whatever the actual existing `WorkflowOrchestrator` test file is named — confirm via `ls tests/ | grep -i workflow` before running) is the regression suite acceptance criterion #1 depends on; it must be unaffected.

## Assumptions/defaults

- The exact `CycleSignals` field mapping for `check_stall`'s result is left to the implementer to decide sensibly and document, since the design spec doesn't prescribe it precisely — pick the mapping that best preserves today's actual stall-detection meaning (a stall today independently terminates the run; folded into the Reasoner, it should strongly push toward `EXHAUSTED`, not be silently ignored).
- If `WorkflowOrchestrator` doesn't currently hold a `graph_port` reference, adding one (threaded the same way `bundle.py` already wires it to `plan_vetter`/`evaluator`) is in scope for this card.
- This card does not make `resolve`/`plan` actually honor `reasoner_anchor_hint` — that's A203. This card only needs to produce and store the hint correctly; A203 consumes it.
