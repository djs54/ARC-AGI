# Plan for B164 — Improve LLM Prompt Quality for Small Models

## Card Metadata
- **Card**: B164
- **Priority**: P1
- **Dependencies**: None

## Summary

qwen2.5:7b produces ~105 tokens/step. The prompt structure (15+ content blocks, complex headers, strict JSON format) overwhelms small models. Add a compact prompt mode that simplifies structure, increases output budget, and adds chain-of-thought nudging.

## Technical Approach

### Step 1: Detect small model

In `agents/arc3/orchestrator.py`, add a helper:

```python
def _is_compact_model(self) -> bool:
    model_name = (self.config.get("llm_model") or "").lower()
    compact_patterns = ["3b", "7b", "8b", "1b", "mini", "tiny", "small"]
    return any(p in model_name for p in compact_patterns)
```

Set `self._compact_mode = self._is_compact_model()` in `__init__`.

### Step 2: Compact prompt templates

In `agents/arc3/prompts.py`, add:

```python
COMPACT_SYSTEM_PROMPT = (
    "You are solving an ARC grid puzzle. "
    "Available actions: {available_actions}. "
    "Think step by step, then choose an action."
)

COMPACT_INSTRUCTION_TEMPLATE = (
    "What changed after your last action? What should you try next?\n"
    "Return JSON: {{\"action\": N, \"why\": \"...\"}}"
)
```

### Step 3: Compact prompt packet builder

In `agents/arc3/orchestrator.py`, add `_build_compact_packet()`:

- Only 5 blocks: SYSTEM, OBSERVATION, ACTION_FACTS (short), HISTORY (last 2 steps only), INSTRUCTION
- OBSERVATION: compact grid summary (not full grid), colors present, shapes detected
- ACTION_FACTS: one line per tested action: `"ACTION1: no effect | ACTION2: moved player right"`
- HISTORY: `"Step 1: ACTION1 → no change. Step 2: ACTION2 → 3 pixels changed."`
- INSTRUCTION: uses `COMPACT_INSTRUCTION_TEMPLATE`

### Step 4: Increase prompt budget

Change the navigation mode budget:

```python
# In act phase prompt_budget calculation:
budget = 1800 if self._compact_mode else 1200
```

### Step 5: Parse simplified JSON

In `_normalize_action_id()` and the JSON parsing logic, also accept:
- `{"action": 2, "why": "..."}` → normalize to `"ACTION2"`
- `{"action": "ACTION2", "why": "..."}` → normalize to `"ACTION2"`

### Step 6: Chain-of-thought preamble

When compact mode is active, prefix the LLM call with:
```
Let me think about this step by step.
1. What do I see on the grid?
2. What happened when I tried my last action?
3. What should I try next?
```

This goes in the system message, not the instruction, so it doesn't count against the response budget.

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/prompts.py` | Add `COMPACT_SYSTEM_PROMPT`, `COMPACT_INSTRUCTION_TEMPLATE` |
| `agents/arc3/orchestrator.py` | Add `_is_compact_model()`, `_build_compact_packet()`, update prompt budget, update JSON parsing |
| `tests/test_b164_compact_prompts.py` | New: test compact detection, packet building, simplified JSON parsing, budget increase |

## Acceptance Criteria

1. `_is_compact_model()` returns True for `"qwen2.5:7b"`, `"qwen2.5:3b"`, `"llama3.2:3b"`
2. Compact packet has ≤5 content blocks
3. Navigation prompt budget is 1800 in compact mode
4. `{"action": 2, "why": "test"}` parses to `ARC3Action(action_id="ACTION2")`
5. `pytest tests/test_b164_compact_prompts.py tests/test_arc3_orchestrator.py -q` all pass

## Validation Commands

```bash
pytest tests/test_b164_compact_prompts.py -v
pytest tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- Compact mode loses information (fewer blocks). This is intentional — small models process less context better.
- The chain-of-thought preamble adds ~50 tokens to every prompt. Worth it for reasoning quality.
- Must ensure the simplified JSON format doesn't break existing tests that expect `{"action_id": "..."}`.
