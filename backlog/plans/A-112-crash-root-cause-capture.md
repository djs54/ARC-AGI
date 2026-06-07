# Plan: A-112 — Crash Root-Cause Capture In Orchestration Loop

## Card metadata

- **Card:** A112
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A111
- **Intended executor:** Haiku subagent

## Summary

Capture full Python tracebacks when the orchestration loop crashes, so smoke results contain actionable exception details instead of generic "UnknownCrash" messages.

## Implementation approach

### Step 1: Locate the unhandled exception gap

In `agents/arc3/runner.py`, find the main per-step loop (the `for task in tasks` / `while not done` pattern around lines 300-530). The current code has `logger.exception(...)` calls for specific failure paths but the outermost step-execution path can raise without being caught with full traceback detail.

### Step 2: Add traceback capture

Wrap the per-step body in a try/except that:

```python
import traceback

try:
    # ... existing step execution ...
except Exception as exc:
    tb_str = traceback.format_exc()
    failure_class = classify_failure(exc)
    # Store the ACTUAL exception details
    result_dict["failure_reason"] = f"{type(exc).__name__}: {exc}\n{tb_str}"
    result_dict["exception_type"] = type(exc).__name__
    result_dict["exception_message"] = str(exc)
    result_dict["orchestration_status"] = "failed"
    result_dict["failure_class"] = failure_class.value
    logger.error("Orchestration crash at step %d: %s", step, tb_str)
```

### Step 3: Fix the `orchestration_status` consistency

In the final result assembly (the code that writes `submission_results_single.json`), add a guard:

```python
if result.get("failure_class") == "crash" and result.get("orchestration_status") != "failed":
    result["orchestration_status"] = "failed"
```

Search for where `orchestration_status` is set and ensure it cannot be `ok` when `failure_class` is truthy.

### Step 4: Eliminate the generic fallback message

Find the string `"Crash root exception was not captured"` in the codebase. It is likely in `failure_taxonomy.py` or the result packaging code. Replace the fallback with a message that includes whatever partial traceback information is available:

```python
# Before
failure_reason = "Crash root exception was not captured; inspect agent_execution_trace.json and master_timeline.json."
# After  
failure_reason = f"Unhandled {exc_type}: {exc_msg}" if exc_type else "Crash root exception was not captured; inspect agent_execution_trace.json and master_timeline.json."
```

## Concrete file edits

1. **`agents/arc3/runner.py`**
   - Find the main step loop (search for `classify_failure` usage ~line 493)
   - Ensure the outermost try/except around the step body captures `traceback.format_exc()` and writes it to the result dict fields `failure_reason`, `exception_type`, `exception_message`
   - Add the `orchestration_status` consistency guard in the finally/result-packaging block

2. **`agents/arc3/failure_taxonomy.py`**
   - Find where `UnknownCrash` is used as a fallback
   - Ensure the `classify_failure` function preserves the original exception details

3. **`tests/test_a112_crash_root_cause_capture.py`**
   - Test that when an exception is raised during step execution, the result dict contains the actual exception class name, message, and traceback
   - Test that `orchestration_status` is `failed` when `failure_class` is `crash`

## Tests to add or run

- `tests/test_a112_crash_root_cause_capture.py`
- `make test-a`

## Validation commands

```bash
pytest tests/test_a112_crash_root_cause_capture.py -v
make test-a
```

## Assumptions/defaults

- The existing `classify_failure` function can receive the original exception object
- The result dict is accessible in the except handler
- Traceback strings are safe to include in JSON (no binary data)
