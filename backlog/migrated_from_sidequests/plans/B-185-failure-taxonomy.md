# Plan for B185 — Failure Classification Taxonomy

## Card Metadata

- **Card ID**: B185
- **Priority**: P1
- **Dependencies**: None

## Summary

Classify every puzzle failure into a structured taxonomy. Replace raw `str(exc)` with `classify_failure()` that returns one of 8 categories.

## Current State

### Runner exception handling (runner.py:~135)

```python
except Exception as exc:
    error_msg = str(exc)
    result_payload["error_message"] = error_msg
```

All failures captured as raw text.

## Technical Approach

### Step 1: Create agents/arc3/failure_taxonomy.py

```python
from enum import Enum

class FailureClass(str, Enum):
    LLM_TIMEOUT = "llm_timeout"
    LLM_PARSE_ERROR = "llm_parse_error"
    API_ERROR = "api_error"
    BUDGET_EXCEEDED = "budget_exceeded"
    STRATEGY_EXHAUSTED = "strategy_exhausted"
    STUCK_IN_LOOP = "stuck_in_loop"
    MAX_STEPS_REACHED = "max_steps_reached"
    CRASH = "crash"

def classify_failure(
    exc: Optional[Exception],
    final_state: Optional[str] = None,
    no_progress_steps: int = 0,
    budget_exhausted: bool = False,
    max_steps_reached: bool = False,
) -> FailureClass:
    if budget_exhausted:
        return FailureClass.BUDGET_EXCEEDED
    if max_steps_reached:
        if no_progress_steps > 20:
            return FailureClass.STUCK_IN_LOOP
        return FailureClass.MAX_STEPS_REACHED
    if exc is None:
        return FailureClass.STRATEGY_EXHAUSTED

    exc_str = str(exc).lower()
    if "timeout" in exc_str or "timed out" in exc_str:
        return FailureClass.LLM_TIMEOUT
    if "parse" in exc_str or "json" in exc_str or "invalid" in exc_str:
        return FailureClass.LLM_PARSE_ERROR
    if "400" in exc_str or "500" in exc_str or "api" in exc_str or "client error" in exc_str:
        return FailureClass.API_ERROR
    return FailureClass.CRASH
```

### Step 2: Wire into runner.py

Replace `str(exc)` with:

```python
from agents.arc3.failure_taxonomy import classify_failure, FailureClass

failure_class = classify_failure(
    exc=exc,
    final_state=final_state,
    no_progress_steps=orchestrator._consecutive_no_progress_steps,
    budget_exhausted=cost_tracker.budget_exhausted if cost_tracker else False,
    max_steps_reached=(total_steps >= max_steps),
)
result_payload["failure_class"] = failure_class.value
result_payload["error_message"] = str(exc) if exc else None
```

### Step 3: ABTaskResult extension (ab_harness.py)

Add `failure_class: Optional[str] = None` field.

### Step 4: Tests

Create `tests/test_b185_failure_taxonomy.py`:
1. Test TimeoutError → LLM_TIMEOUT
2. Test JSONDecodeError → LLM_PARSE_ERROR
3. Test "400 Bad Request" → API_ERROR
4. Test budget_exhausted=True → BUDGET_EXCEEDED
5. Test max_steps + high no_progress → STUCK_IN_LOOP
6. Test max_steps + low no_progress → MAX_STEPS_REACHED
7. Test random RuntimeError → CRASH
8. Test no exception + no flags → STRATEGY_EXHAUSTED

## Verification

```bash
pytest tests/test_b185_failure_taxonomy.py -v
```
