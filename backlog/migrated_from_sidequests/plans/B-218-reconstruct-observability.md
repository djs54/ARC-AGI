# B-218 — Reconstruct Codex Observability Framework

- **Card:** backlog/B218.md
- **Priority:** P0
- **Dependencies:** None (B214 already committed at `9c1fb2d2`)

## Background

`mcp_engine/observability.py` and its integration in `runner.py`, `adapter.py`, `orchestrator.py`, and `tests/test_observability.py` were accidentally deleted and reverted before being committed. The files are unrecoverable from git. This plan reconstructs them from the diffs captured in conversation history.

## Known Content (from conversation transcript diffs)

### `mcp_engine/observability.py` — partial header recovered:

```python
"""Phoenix/OpenTelemetry observability helpers for SideQuests.

This module is intentionally defensive:
- observability is disabled by default
- missing Phoenix/OTel dependencies degrade to a no-op backend
- redaction is the default behavior for string-heavy payloads
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Optional


_SENSITIVE_KEY_FRAGMENTS = {
    "content",
    "prompt",
    "query",
    "reasoning",
    "rationale",
    "text",
}
_BULKY_KEY_FRAGMENTS = {
    "frame",
    "grid",
    # ... (additional keys — reconstruct sensibly)
}
```

### Known exports:
- `canonical_span_name(operation: str) -> str` — simple formatter, e.g. `"arc_api.request"` → returned as-is or prefixed
- `build_observability(config: dict) -> Observability` — creates instance; disabled if no `observability` key in config
- `Observability` class with:
  - `.enabled: bool` property — True only when Phoenix/OTel is available AND configured
  - `.span(name: str, attributes: dict) -> ContextManager` — returns a context manager; no-op when disabled
  - `.emit_structured_event(event_type: str, operation: str, details: dict, result: dict, elapsed_ms: float) -> None`

### Integration — `agents/arc3/runner.py`:

```python
# Add to imports (after existing imports):
from mcp_engine.observability import build_observability

# Add to DurableARCRunner.__init__ (after self._replan_backoff_steps):
self._last_replan_signature: dict[str, Any] | None = None
self.observability = build_observability(config if isinstance(config, dict) else {})

# Modify LedgerBrainClient instantiation in __init__:
self.brain = LedgerBrainClient(
    inner=brain_client,
    ledger=self._ledger,
    step_provider=lambda: self._current_step,
    cost_tracker=None,
    observability=self.observability,
)

# In run() method, wrap the entire run with a span:
run_span = self.observability.span(
    "arc.run",
    {
        "card_id": card_id,
        "task_count": len(tasks),
        "model": ((self.config.get("llm") or {}).get("model") if isinstance(self.config, dict) else "unknown") or "unknown",
    },
)
run_span.__enter__()
# ... (existing run body) ...
# wrap each task with task_span similarly

# Also add observability= param when instantiating LedgerBrainClient inside run()
# (the re-instantiation inside run that creates cost_tracker variant):
cost_tracker=cost_tracker,
observability=self.observability,
```

### Integration — `benchmarks/arc3/adapter.py`:

```python
# Add to imports:
from contextlib import nullcontext
from mcp_engine.observability import Observability, canonical_span_name

# Extend LedgerBrainClient.__init__ signature:
def __init__(
    self,
    inner: BrainClientProtocol,
    ledger: List[Mapping[str, Any]],
    step_provider: Callable[[], int | str],
    start_time: Optional[float] = None,
    cost_tracker: Optional[Any] = None,
    observability: Optional[Observability] = None,
):
    # ... existing body ...
    self.observability = observability

# Add helper method to LedgerBrainClient:
def _span_attributes(self, *, phase: str, mode: str, latency_ms: float, extra: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    attrs = {
        "phase": phase if phase != "unknown" else self.current_phase,
        "mode": mode,
        "latency_ms": round(latency_ms, 3),
        "step": self.step_provider(),
    }
    if extra:
        attrs.update(extra)
    return attrs

# Wrap notify_turn, current_truth, register_plan with observability spans:
# Use nullcontext() as no-op fallback when observability disabled.
# Example pattern:
ctx = (
    self.observability.span(canonical_span_name("notify_turn"), self._span_attributes(...))
    if self.observability and self.observability.enabled
    else nullcontext()
)
with ctx:
    resp = await self.inner.notify_turn(...)
```

### Integration — `agents/arc3/orchestrator.py`:

```python
# Add to imports:
from mcp_engine.observability import build_observability

# Add to ARCOrchestrator.__init__ (after self.config = config):
self._observability = build_observability(config if isinstance(config, dict) else {})

# Extend _emit_trace_event to also emit via observability:
def _emit_trace_event(self, event_type, operation, details=None, result=None, elapsed_ms=None):
    """B131: Emit trace event (CloudWatch-style) and forward to observability."""
    # existing body (appends to self._execution_trace) ...

    # NEW: forward to observability
    try:
        if self._observability:
            self._observability.emit_structured_event(
                event_type=event_type,
                operation=operation,
                details=details,
                result=result,
                elapsed_ms=elapsed_ms,
            )
    except Exception:
        logger.debug("Observability event emission failed", exc_info=True)
```

## Full Implementation Spec

### 1. `mcp_engine/observability.py`

Create the complete module. Design requirements:
- **Disabled by default**: `build_observability({})` returns a disabled `Observability`
- **Phoenix opt-in**: enabled only when `config.get("observability", {}).get("enabled")` is truthy AND `phoenix` (arize-phoenix) package is importable
- **Defensive**: all Phoenix imports inside try/except; module loads cleanly with no Phoenix installed
- **No-op span**: when disabled, `.span()` returns `nullcontext()`
- **Redaction**: `_SENSITIVE_KEY_FRAGMENTS` keys have their string values replaced with a SHA-256 prefix + `"[redacted]"` in span attributes; `_BULKY_KEY_FRAGMENTS` keys (frame, grid, pattern, image, pixels) have values replaced with `f"[{len(str(v))} chars]"`
- **`canonical_span_name`**: prepends `"sidequests."` to the operation name, e.g. `canonical_span_name("arc_api.request")` → `"sidequests.arc_api.request"`
- **`Observability.emit_structured_event`**: when enabled, calls Phoenix's `log_spans` or equivalent; when disabled, is a no-op
- **`contextvars` usage**: store current active span in a ContextVar so nested spans can link to parent

Suggested structure:
```python
@dataclass
class _SpanContext:
    name: str
    attributes: dict[str, Any]
    start_time: float = field(default_factory=time.monotonic)

class Observability:
    def __init__(self, *, enabled: bool, backend: Any = None): ...
    @property
    def enabled(self) -> bool: ...
    def span(self, name: str, attributes: dict) -> ContextManager: ...
    def emit_structured_event(self, event_type, operation, details, result, elapsed_ms) -> None: ...

def canonical_span_name(operation: str) -> str:
    return f"sidequests.{operation}"

def build_observability(config: dict) -> Observability:
    obs_cfg = config.get("observability", {}) if isinstance(config, dict) else {}
    if not obs_cfg.get("enabled"):
        return Observability(enabled=False)
    try:
        import phoenix  # noqa: F401
        # configure phoenix backend
        return Observability(enabled=True, backend=phoenix)
    except ImportError:
        return Observability(enabled=False)
```

### 2. `agents/arc3/runner.py`

- Add `from mcp_engine.observability import build_observability` to imports
- Add `self._last_replan_signature: dict[str, Any] | None = None` to `DurableARCRunner.__init__`
- Add `self.observability = build_observability(config if isinstance(config, dict) else {})` to `DurableARCRunner.__init__`
- Pass `observability=self.observability` to all `LedgerBrainClient(...)` instantiations (there are at least 3: initial in `__init__`, cost-tracker variant in `run()`, and variant in strategy racing)
- Wrap `run()` body with `self.observability.span("arc.run", {...})` context manager — use `run_span.__enter__()` / `run_span.__exit__(None, None, None)` style to span across the entire method
- Wrap each per-task iteration with `self.observability.span("arc.task", {...})` and set result attributes on exit

### 3. `benchmarks/arc3/adapter.py`

- Add `from contextlib import nullcontext` to imports
- Add `from mcp_engine.observability import Observability, canonical_span_name` to imports
- Extend `LedgerBrainClient.__init__` to accept `observability: Optional[Observability] = None`
- Add `self.observability = observability` to `__init__` body
- Add `_span_attributes()` helper method (see spec above)
- Wrap `notify_turn`, `current_truth`, `register_plan` calls with observability spans using `nullcontext()` fallback

### 4. `agents/arc3/orchestrator.py`

- Add `from mcp_engine.observability import build_observability` to imports (after existing imports)
- Add `self._observability = build_observability(config if isinstance(config, dict) else {})` to `ARCOrchestrator.__init__` (after `self.config = config`)
- In `_emit_trace_event` (line ~915), after appending to `self._execution_trace`, add the try/except observability forward block

### 5. `tests/test_observability.py`

Create a test file. Tests must pass with NO Phoenix installed (all import guards tested):

```python
"""Tests for mcp_engine.observability — must pass without Phoenix installed."""
import pytest
from mcp_engine.observability import Observability, canonical_span_name, build_observability


def test_canonical_span_name():
    assert canonical_span_name("arc_api.request") == "sidequests.arc_api.request"


def test_build_observability_disabled_by_default():
    obs = build_observability({})
    assert not obs.enabled


def test_build_observability_disabled_without_phoenix():
    """Even if enabled=True in config, disabled when phoenix not installed."""
    obs = build_observability({"observability": {"enabled": True}})
    # Phoenix is not installed in test env, so still disabled
    assert not obs.enabled


def test_noop_span_is_context_manager():
    obs = build_observability({})
    with obs.span("test.span", {"key": "value"}) as span:
        pass  # Must not raise


def test_emit_structured_event_noop():
    obs = build_observability({})
    obs.emit_structured_event("operation", "test_op", {}, {}, 0.0)  # Must not raise


def test_runner_integration():
    """DurableARCRunner accepts and propagates observability without crashing."""
    import asyncio
    from unittest.mock import MagicMock, patch
    from agents.arc3.runner import DurableARCRunner
    brain = MagicMock()
    config = {"llm": {"model": "test"}}
    runner = DurableARCRunner(brain_client=brain, config=config)
    assert hasattr(runner, "observability")
    assert not runner.observability.enabled


def test_ledger_brain_client_accepts_observability():
    from benchmarks.arc3.adapter import LedgerBrainClient
    from unittest.mock import MagicMock
    inner = MagicMock()
    obs = build_observability({})
    client = LedgerBrainClient(
        inner=inner,
        ledger=[],
        step_provider=lambda: 0,
        observability=obs,
    )
    assert client.observability is obs
```

## Concrete File Changes

| File | Change |
|------|--------|
| `mcp_engine/observability.py` | CREATE — full module |
| `agents/arc3/runner.py` | ADD observability import + integration |
| `benchmarks/arc3/adapter.py` | ADD observability import + LedgerBrainClient integration |
| `agents/arc3/orchestrator.py` | ADD observability import + `_emit_trace_event` forward |
| `tests/test_observability.py` | CREATE — all tests pass without Phoenix |

## Validation Commands

```bash
.venv/bin/python -m pytest tests/test_observability.py -v
.venv/bin/python -m pytest tests/ -q --tb=short
.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1
```

## Risks / Constraints

- Do NOT add Phoenix as a hard dependency. All Phoenix imports must be inside try/except ImportError.
- The `span()` context manager must work even when called with `.__enter__()` / `.__exit__()` directly (not just `with` statement).
- Do NOT change the `_emit_trace_event` signature — only add the observability forward inside the existing method body.
- The `LedgerBrainClient` observability parameter is optional with `None` default — existing call sites without it must still work unchanged.
- `DurableARCRunner` has multiple `LedgerBrainClient(...)` instantiations — update ALL of them to pass `observability=self.observability`.
