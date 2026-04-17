# B-123 - ARC Python REPL Sandbox Tool for Grid Logic Verification

Card: B123
Priority: P1
Dependencies: B114
Ecosystem Layer: Runtime (Layer 3) — ARC Agent tool, non-world-mutating

## Summary

Add a harness-local Python REPL tool the agent can invoke inside the mental sandbox loop to verify grid logic (rotations, counting, pattern matching) computationally before spending a real ARC move.

## Technical Approach

### Step 1: Create `agents/arc3/repl_sandbox.py`

Implement a restricted Python executor that:
- Uses `subprocess.run` with a short timeout (2s).
- Prepend a "prelude" script that overrides `__import__` to block non-whitelisted modules.
- Whitelist: `numpy`, `collections`, `itertools`, `json`, `math`.
- Captures `stdout` and `stderr`.

### Step 2: Update `agents/arc3/prompts.py`

Add `REPL_SANDBOX_INSTRUCTION` constant:
```python
REPL_SANDBOX_INSTRUCTION = (
    "\n\nREPL SANDBOX: You can also use the 'repl_test' tool to verify grid logic or hypotheses with Python. "
    "Accepts a short snippet (numpy, math, collections, itertools are available). "
    "Respond with: {{\"thought\": \"logic to test\", \"repl_test\": \"print(grid_rotate(g))\"}} "
    "to use the REPL. Results appear in your next turn. No file/network allowed. 2s timeout."
)
```

### Step 3: Update `agents/arc3/orchestrator.py`

1. Import `execute_repl` and `REPL_SANDBOX_INSTRUCTION`.
2. Update `_mental_sandbox()` reasoning loop:
   - Append `REPL_SANDBOX_INSTRUCTION` to the prompt.
   - Detect `repl_test` in the LLM's JSON response.
   - If present, execute the snippet via `execute_repl`.
   - Prepend `g = <current_grid_json>` to the snippet for convenience.
   - Append the output/error back to the `current_prompt` and loop.
   - Capture the REPL result in the `thinking_trace`.

### Step 4: Create `tests/test_arc3_repl_sandbox.py`

Test cases:
- Successful calculation (e.g., `print(1+1)`).
- Blocked import (e.g., `import os`).
- Timeout enforcement (e.g., `while True: pass`).
- Error reporting (syntax error, runtime error).

## Validation Commands

```bash
# Run new unit tests
pytest tests/test_arc3_repl_sandbox.py -v

# Run full ARC regression suite
pytest tests/test_arc3_orchestrator.py tests/test_arc3_solver.py -v
```

## Risks / Constraints

- **Latency**: Keep max 2 iterations in the sandbox to avoid doubling turn time.
- **Safety**: Subprocess isolation and import blocking are mandatory to prevent escape.
- **Deterministic**: Tool output must be deterministic for consistent thinking traces.
