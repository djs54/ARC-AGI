# Plan: A205 — Reasoner Error Handling

## Card metadata

- ID: A205
- Priority: P1
- Layer: ARC runtime
- Dependencies: A202

## Summary

Add explicit, tested fallback behavior for the two failure modes spec §8 names: graph unreachable mid-cycle, and `AWAITING_LLM` escalation failure. Extend A196's telemetry with `reasoner_degraded` rather than building a second reporting mechanism.

## Technical approach

### 1. Locate where A202's graph calls actually happen

Read the post-A202 state of `agents/arc4/reasoner_signals.py::compute_cycle_signals` and `agents/arc4/workflow.py`'s Reasoner hook in full first — confirm exactly which try/except blocks already exist from A202's own implementation (A202's plan already wraps individual graph calls in `try/except Exception: pass`-style guards per its own sketch) versus what's still missing. This card's job is to make those failures *visible* (telemetry) and to make the *AWAITING_LLM* failure path specifically well-defined, not to duplicate error handling A202 may have already added.

### 2. `reasoner_degraded` flag

Wherever `compute_cycle_signals` (or the orchestrator's Reasoner hook) catches an exception from a graph call, set a local `degraded = True` instead of silently discarding the information. Thread this value through to `ReasonerOutcome` (add a field: `degraded: bool = False` in `types.py`'s `ReasonerOutcome`, added by A202 — confirm it doesn't already exist there before adding) so it reaches the orchestrator, which passes it to telemetry the same way other per-cycle facts already reach `_step_snapshot` (via `execution`/`evaluation` metadata, or a new argument — follow whichever existing pattern `telemetry.py::_step_snapshot` already uses for reading orchestrator-level facts, confirmed by reading its current signature first).

`agents/arc4/telemetry.py::_step_snapshot`: add `"reasoner_degraded": <value>` to the snapshot dict, defaulting to `False` when no Reasoner is configured (matches every other optional-capability field's degrade-to-safe-default convention this session established for `llm_escalated_plan`/`graph_grounded`/etc.).

### 3. `AWAITING_LLM` failure handling — implement `resolve_llm_vote`

A202's plan defines `run_reasoner_cycle` (`agents/arc4/reasoner_signals.py`) with an `AWAITING_LLM` branch that calls `resolve_llm_vote(llm_port, state, signals)` and expects it to return an `InvestigationState`. A202 ships this as a `NotImplementedError` placeholder (deliberately loud, not silent) specifically so this card's job is unambiguous. Implement its real body here:

```python
def resolve_llm_vote(llm_port: LLMPort | None, state: WorkflowState, signals: CycleSignals) -> InvestigationState:
    """Bounded-retry LLM call for AWAITING_LLM. On any failure (exhausted
    retries, unparseable response, no llm_port at all), returns a sentinel
    guaranteed to be outside permissible_llm_transitions -- apply_llm_vote's
    existing out-of-set fallback then handles it, no second fallback rule
    needed here."""
    if llm_port is None:
        return InvestigationState.EXPLORING  # guaranteed out-of-set, see A200's permissible_llm_transitions
    try:
        response = llm_port.chat(_build_transition_vote_prompt(state, signals))  # mirrors goal_resolver.py/plan_generator.py's _query_llm request-shape convention -- read both in full first, match their existing retry/timeout handling rather than inventing new conventions
        parsed = _parse_transition_vote(response)
        if parsed is None:
            return InvestigationState.EXPLORING
        return InvestigationState(parsed)
    except Exception:
        return InvestigationState.EXPLORING
```

Read `goal_resolver.py::_query_llm`/`plan_generator.py::_query_llm` in full before writing `_build_transition_vote_prompt`/`_parse_transition_vote` — match their existing schema-constrained-JSON request shape and response-parsing conventions exactly (required-fields JSON object, not free text), consistent with spec §4.2's requirement that the LLM's vote uses "the same schema-constrained-JSON pattern goal_resolver/plan_generator already use."

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/types.py` | `ReasonerOutcome.degraded` field (if not already present from A202) |
| `agents/arc4/telemetry.py` | `reasoner_degraded` in `_step_snapshot` |
| Wherever A202 put the `AWAITING_LLM` call (confirm exact file) | Bounded-retry + fallback-via-`apply_llm_vote` |
| `tests/test_a205_reasoner_error_handling.py` (new) | Coverage, see Tests |

## Tests

`tests/test_a205_reasoner_error_handling.py`:

1. A graph-client call raising an exception during a Reasoner cycle results in `ReasonerOutcome.degraded=True` and a valid (non-crashing) decision.
2. A normal, successful cycle produces `degraded=False`.
3. `telemetry.py::_step_snapshot` reflects `reasoner_degraded` correctly for both a degraded and a normal evaluation.
4. `AWAITING_LLM` with a mocked LLM call raising an exception resolves via `apply_llm_vote`'s existing fallback (assert the same behavior as A200's `test_out_of_set_vote_falls_back_to_exhausted_when_permitted`/`..._deepening_...` tests, reused conceptually here at the integration level, not just the pure-function level).
5. `AWAITING_LLM` with a mocked LLM call returning an unparseable response — same fallback behavior as item 4.
6. `AWAITING_LLM` with a mocked LLM call succeeding normally — the real vote is used, `degraded` stays `False`, no fallback path taken (regression guard confirming the fallback logic doesn't fire when it shouldn't).

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a205_reasoner_error_handling.py -v
.venv/bin/python -m pytest tests/test_a200_investigation_reasoner_state_machine.py tests/test_a202_reasoner_orchestrator_integration.py -v
make test-a
make test-all
```

## Assumptions/defaults

- This card assumes A202 already has *some* try/except structure around its graph calls (per A202's own plan sketch) — this card's job is making failures visible and giving `AWAITING_LLM` specifically a well-defined, tested fallback, not introducing error handling from scratch where none exists. If A202 landed without the expected try/except structure, add it here and note the gap in the Resolution.
