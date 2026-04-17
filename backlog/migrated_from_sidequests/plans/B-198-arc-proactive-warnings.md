# Plan for B198 — ARC Agent: Proactive Warning Integration

## Card Metadata
- **Card ID**: B198
- **Priority**: P2
- **Dependencies**: B195 (active context push)

## Summary
Parse `proactive_context` from `notify_turn` responses in the ARC orchestrator. Inject warnings into hypothesis context and penalize warned-against actions in `_score_action_families()`.

## Technical Approach

### Step 1: Parse proactive_context in orchestrator.py

In the step processing loop where `notify_turn` is called:

```python
# After calling notify_turn
turn_response = await self.brain.notify_turn(...)
proactive_ctx = turn_response.get("proactive_context", [])

if proactive_ctx:
    self._proactive_warnings = proactive_ctx
    # Log to execution trace
    self._record_trace_event("PROACTIVE_WARNING", {
        "warnings": [{"lesson_id": w["lesson_id"], "text": w["text"][:100], "type": w["type"]}
                     for w in proactive_ctx],
        "step": self._step_count,
    })
```

### Step 2: Inject into hypothesis_context

When building `hypothesis_context` for `SolveEngine.solve()`:

```python
hypothesis_context["proactive_warnings"] = [
    {
        "text": w["text"],
        "type": w["type"],  # "warning" or "hint"
        "domain": w.get("domain", ""),
        "relevance": w.get("relevance_score", 0),
    }
    for w in self._proactive_warnings
]
```

### Step 3: Use warnings in _score_action_families() (solver.py)

```python
def _score_action_families(self, families, context):
    warnings = context.get("proactive_warnings", [])
    warning_texts = " ".join(w["text"].lower() for w in warnings if w["type"] == "warning")

    for family in families:
        base_score = family["score"]

        # Penalize actions mentioned in warnings
        action_name = family["action"].lower()
        if action_name in warning_texts:
            penalty = 0.3  # 30% score reduction
            family["score"] = max(0.05, base_score - penalty)
            family["warning_penalized"] = True
            _logger.info(f"Action {family['action']} penalized by proactive warning")

    return families
```

### Step 4: Graceful when empty

```python
# In orchestrator.__init__
self._proactive_warnings = []

# In hypothesis_context builder — always include key, empty list is fine
hypothesis_context["proactive_warnings"] = getattr(self, "_proactive_warnings", [])
```

### Step 5: Tests

Create `tests/test_b198_arc_proactive_warnings.py`:
1. Test proactive_context parsed from notify_turn response
2. Test warnings injected into hypothesis_context["proactive_warnings"]
3. Test _score_action_families penalizes warned actions by 0.3
4. Test warning recorded in execution trace as PROACTIVE_WARNING event
5. Test graceful when proactive_context is empty (no crash, no penalty)
6. Test graceful when proactive_context is absent from response
7. Test "hint" type warnings don't penalize (only "warning" type does)

## Verification
```bash
pytest tests/test_b198_arc_proactive_warnings.py -v
```
