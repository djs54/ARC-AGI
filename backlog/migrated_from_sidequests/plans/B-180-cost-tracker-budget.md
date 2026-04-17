# Plan for B180 — Token Cost Tracker and Budget Enforcer

## Card Metadata

- **Card ID**: B180
- **Priority**: P1
- **Dependencies**: None

## Summary

Add a `CostTracker` that accumulates token usage and computes dollar cost per puzzle. Enforce per-puzzle budget. Persist cost summaries to KuzuDB.

## Current State

### Token tracking (orchestrator.py:~169)

```python
self._prompt_tokens_per_step: List[int] = []
```

Token estimates are collected per step but never aggregated or converted to cost.

### Submission results (runner.py)

```python
result_payload["tokens_input"] = ...
result_payload["tokens_output"] = ...
```

Token counts are written to results but no dollar conversion.

### LedgerBrainClient (adapter.py:~229)

Records latency per brain call but not token counts or cost.

## Technical Approach

### Step 1: Add pricing config to sidequests.toml

```toml
[cost]
budget_per_puzzle_usd = 0.50
pricing_per_million_tokens = { "llama3.1:8b" = { input = 0.0, output = 0.0 }, "qwen2.5:7b" = { input = 0.0, output = 0.0 }, "claude-sonnet-4-20250514" = { input = 3.0, output = 15.0 } }
```

### Step 2: Create agents/arc3/cost_tracker.py

```python
@dataclass
class CostTracker:
    model_name: str
    input_price_per_m: float = 0.0
    output_price_per_m: float = 0.0
    budget_usd: float = float('inf')

    _tokens_in: int = 0
    _tokens_out: int = 0

    def record(self, tokens_in: int, tokens_out: int):
        self._tokens_in += tokens_in
        self._tokens_out += tokens_out

    @property
    def total_cost_usd(self) -> float:
        return (self._tokens_in * self.input_price_per_m + self._tokens_out * self.output_price_per_m) / 1_000_000

    @property
    def budget_exhausted(self) -> bool:
        return self.total_cost_usd >= self.budget_usd

    def summary(self) -> dict:
        return {"tokens_in": self._tokens_in, "tokens_out": self._tokens_out, "cost_usd": self.total_cost_usd, "budget_usd": self.budget_usd}
```

### Step 3: Wire into orchestrator (orchestrator.py)

In `__init__`, accept `cost_tracker: CostTracker`. After each LLM call (search for `self.llm.chat(`), call `cost_tracker.record(tokens_in, tokens_out)`.

### Step 4: Budget enforcement in runner (runner.py)

After each step in the puzzle loop:

```python
if cost_tracker.budget_exhausted:
    logger.warning("Budget exhausted at step %d: $%.4f", step, cost_tracker.total_cost_usd)
    # Record failure with BUDGET_EXCEEDED class
    break
```

### Step 5: KuzuDB PuzzleCostSummary node (schema.py)

```python
"PuzzleCostSummary": """
    summary_id    STRING,
    task_id       STRING,
    model         STRING,
    tokens_in     INT64,
    tokens_out    INT64,
    cost_usd      DOUBLE,
    outcome       STRING,
    steps         INT32,
    created_at    TIMESTAMP,
    PRIMARY KEY (summary_id)
""",
```

Persist after each puzzle completes in runner.py.

### Step 6: Extend LedgerBrainClient._record() (adapter.py)

Add `tokens_in`, `tokens_out`, `cost_usd` fields to each ledger entry.

### Step 7: Tests

Create `tests/test_b180_cost_tracker.py`:
1. Test accumulation: record(100, 50) twice → tokens_in=200, tokens_out=100
2. Test cost calculation: 1M input tokens at $3/M → $3.00
3. Test budget exhaustion: budget=$0.50, accumulate past it → budget_exhausted=True
4. Test Ollama at $0: still tracks tokens, cost=0.0, never budget_exhausted (unless budget=0)
5. Test summary() output format

## Verification

```bash
pytest tests/test_b180_cost_tracker.py -v
pytest tests/test_arc3_orchestrator.py -v  # regression
```
