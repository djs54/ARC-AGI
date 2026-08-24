# Plan: A197 — Phase-Isolated Token Cost Assertions

## Card metadata

- ID: A197
- Priority: P2
- Layer: ARC runtime
- Dependencies: A195

## Summary

Capture a per-phase token-cost delta in `ArcV2Telemetry` (currently only a whole-episode running total exists), add a `check_shift_a_invariants` function to A195's `compliance_checks.py` asserting the four deterministic phases (`perceive`/`vet`/`execute`/`evaluate`) never carry nonzero token cost, and merge its output into the same `compliance_violation_count` field/gate A195 already builds — one compliance-reporting surface for both shifts, not two.

## Technical approach

### 1. Confirm current token-increment wiring first

Before implementing, read how `ArcV2Telemetry.tokens_input`/`tokens_output` actually get incremented today — grep for `.tokens_input +=` / `.tokens_output +=` (or equivalent) across `agents/arc4/`, `arc_runtime/`, and `run_single_puzzle.py`. This plan's delta-capture approach (step 2) works correctly regardless of *where* the increment happens, as long as it happens synchronously within the corresponding phase's `phase_callable(*args, **kwargs)` call inside `wrap_phase`'s `wrapped()` closure — confirm that assumption holds (i.e., an LLM call made during `resolve`'s phase callable updates the counters before `wrapped()` returns) rather than assuming it from this plan alone.

### 2. `telemetry.py::wrap_phase` — capture the delta

```python
def wrap_phase(self, phase_name: str, phase_callable: Callable[..., Any]) -> Callable[..., Any]:
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        tokens_before = (self.tokens_input, self.tokens_output)
        result = phase_callable(*args, **kwargs)
        tokens_after = (self.tokens_input, self.tokens_output)
        phase_token_delta = (tokens_after[0] - tokens_before[0]) + (tokens_after[1] - tokens_before[1])
        self._record_phase_result(phase_name, result, args, phase_token_delta=phase_token_delta)
        return result

    return wrapped
```

Update `_record_phase_result`'s signature to accept `phase_token_delta: int` and store it (e.g. `self._phase_token_costs: dict[str, int] = {}` accumulated per phase name across the episode, or attached directly onto the phase transition snapshot dict — read `_phase_transition_snapshot`'s current dict-building code before deciding which, and match its existing style rather than introducing a second bookkeeping structure if the snapshot dict alone is sufficient).

### 3. `agents/arc4/compliance_checks.py` — new function (this file is introduced by A195; if A195 hasn't landed yet when this card is implemented, create the file per A195's plan first, don't diverge from its shape)

```python
DETERMINISTIC_PHASES = frozenset({"perceive", "vet", "execute", "evaluate"})


def check_shift_a_invariants(phase_token_costs: Mapping[str, int]) -> list[str]:
    """Shift A: perceive/vet/execute/evaluate must never invoke an LLM. A
    nonzero token cost during any of them means something bypassed the
    deterministic-phase boundary -- resolve/plan are the only phases allowed
    real cost here."""
    violations: list[str] = []
    for phase_name in DETERMINISTIC_PHASES:
        cost = int(phase_token_costs.get(phase_name, 0) or 0)
        if cost > 0:
            violations.append(f"phase={phase_name!r} incurred {cost} tokens of LLM cost; this phase must be strictly deterministic")
    return violations
```

### 4. Merge into the existing `compliance_violation_count` field

Read A195's actual implementation of `_step_snapshot`'s `compliance_violation_count` (or, if implementing this card before A195 has landed, read A195's plan) before wiring this in — the merge point should be wherever `_step_snapshot` currently computes `len(evaluation.metadata.get("compliance_violations", []))`. Extend it to also include `check_shift_a_invariants(this_step's phase_token_costs)`'s results in the same count:

```python
shift_b_violations = evaluation.metadata.get("compliance_violations", []) if evaluation is not None else []
shift_a_violations = check_shift_a_invariants(phase_token_costs_for_this_step)
compliance_violation_count = len(shift_b_violations) + len(shift_a_violations)
```

Confirm the exact shape of "phase_token_costs_for_this_step" (per-step vs. cumulative-since-episode-start) matches what step 2 actually produces before wiring this — if step 2 only tracks a running cumulative total per phase rather than a per-step delta, decide during implementation whether Shift-A violations should be reported once (first occurrence) or on every subsequent step until the episode ends, and document the choice in the Resolution; don't leave it ambiguous.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/telemetry.py` | `wrap_phase`/`_record_phase_result`: per-phase token delta capture |
| `agents/arc4/compliance_checks.py` | New `check_shift_a_invariants`, alongside A195's `check_shift_b_invariants` |
| `tests/test_a197_phase_isolated_token_cost.py` (new) | Coverage (see Tests) |

## Tests

New `tests/test_a197_phase_isolated_token_cost.py`:

1. `check_shift_a_invariants({"perceive": 0, "vet": 0, "execute": 0, "evaluate": 0, "resolve": 150, "plan": 40})` returns `[]`.
2. `check_shift_a_invariants({"perceive": 12, "vet": 0, "execute": 0, "evaluate": 0})` returns exactly one violation naming `perceive`.
3. `check_shift_a_invariants` with multiple deterministic phases nonzero returns one violation per offending phase.
4. `check_shift_a_invariants({})` (no data at all) returns `[]` (absence is not evidence of violation, `.get(..., 0)` defaults cleanly).
5. `wrap_phase`'s delta capture: construct an `ArcV2Telemetry`, simulate a phase callable that increments `self.tokens_input` mid-call (representing an LLM call happening during that phase), confirm the captured delta for that phase name matches exactly, and that a subsequent phase call with no token change reports a `0` delta (proving it's a delta, not a running total misattributed to every phase).
6. End-to-end: a normal simulated episode (LLM cost only appearing during `resolve`/`plan` phase calls) reports zero Shift-A violations in its final `compliance_violation_count` across all steps.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a197_phase_isolated_token_cost.py -v
.venv/bin/python -m pytest tests/test_a195_shift_b_invariant_assertions.py -v
.venv/bin/python -m pytest tests/test_observability.py tests/test_trace_durability.py -v
make test-a
make test-all
make smoke && make check-compliance
```

## Assumptions/defaults

- This card explicitly does not touch `benchmarks/ab_harness.py` — confirmed during scoping that it has no live caller anywhere in the codebase (`ABBenchmark._execute_task` is a placeholder, no subclass exists). Flag this as a separate, out-of-scope finding in the Resolution rather than silently working around it.
- If the token-increment wiring confirmed in step 1 turns out to be asynchronous or otherwise not cleanly bracketable by `wrap_phase`'s before/after snapshot (e.g. tokens get attributed after the phase callable returns, via a separate callback), this plan's delta-capture approach needs revision — document what was actually found and how the implementation adapted, rather than forcing the sketch above to fit an incompatible reality.
