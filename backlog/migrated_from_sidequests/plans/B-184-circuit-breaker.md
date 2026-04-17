# Plan for B184 — Circuit Breaker for LLM Calls

## Card Metadata

- **Card ID**: B184
- **Priority**: P1
- **Dependencies**: None

## Summary

Wrap LLM client with circuit breaker pattern: CLOSED → OPEN (after 3 failures) → HALF_OPEN (after cooldown). Exponential backoff on retries.

## Current State

### LLM calls (orchestrator.py)

```python
self.llm = llm_client  # Injected, no wrapper
# Used in multiple places:
response = await self.llm.chat(prompt)  # Can timeout or error with no retry
```

No retry, no backoff, no circuit breaker. A timeout crashes the puzzle.

## Technical Approach

### Step 1: Create agents/arc3/circuit_breaker.py

```python
import asyncio
import time
from enum import Enum

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreakerLLMClient:
    def __init__(self, inner_client, failure_threshold: int = 3, cooldown_seconds: float = 30.0, max_retries: int = 3):
        self._inner = inner_client
        self._failure_threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._max_retries = max_retries
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._last_failure_time = 0.0

    async def chat(self, *args, **kwargs):
        # Check circuit state
        if self._state == CircuitState.OPEN:
            if time.time() - self._last_failure_time > self._cooldown:
                self._state = CircuitState.HALF_OPEN
            else:
                return self._safe_default()

        # Try with exponential backoff
        last_exc = None
        for attempt in range(self._max_retries):
            try:
                result = await self._inner.chat(*args, **kwargs)
                self._on_success()
                return result
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_retries - 1:
                    backoff = 2 ** attempt  # 1s, 2s, 4s
                    await asyncio.sleep(backoff)

        self._on_failure()
        # Return safe default instead of raising
        return self._safe_default()

    def _on_success(self):
        self._consecutive_failures = 0
        if self._state == CircuitState.HALF_OPEN:
            self._state = CircuitState.CLOSED

    def _on_failure(self):
        self._consecutive_failures += 1
        self._last_failure_time = time.time()
        if self._consecutive_failures >= self._failure_threshold:
            self._state = CircuitState.OPEN

    def _safe_default(self):
        # Return empty response that won't crash the caller
        return {"choices": [{"message": {"content": '{"action_id": "ACTION1", "rationale": "circuit breaker fallback"}'}}]}

    # Proxy all other attributes to inner client
    def __getattr__(self, name):
        return getattr(self._inner, name)
```

### Step 2: Wire into orchestrator (orchestrator.py:~129)

```python
from agents.arc3.circuit_breaker import CircuitBreakerLLMClient

class ARCOrchestrator:
    def __init__(self, brain_client, llm_client, ...):
        self.llm = CircuitBreakerLLMClient(llm_client)
```

### Step 3: Tests

Create `tests/test_b184_circuit_breaker.py`:
1. Test CLOSED state: normal call → success → stays CLOSED
2. Test 3 failures → OPEN state
3. Test OPEN state: calls return safe default immediately (no actual call)
4. Test cooldown: after 30s, state → HALF_OPEN
5. Test HALF_OPEN success → CLOSED
6. Test HALF_OPEN failure → OPEN again
7. Test exponential backoff timing (1s, 2s, 4s)
8. Test attribute proxy: `circuit_breaker.model_name` → inner client attribute

## Verification

```bash
pytest tests/test_b184_circuit_breaker.py -v
pytest tests/test_arc3_orchestrator.py -v  # regression
```
