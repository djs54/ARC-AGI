# B-122: Extract Hardcoded Prompts to Shared Prompt Module

Card: backlog/B122.md
Priority: P2
Dependencies: None
Ecosystem Layer: Prompt & Knowledge (Layer 4)

## Summary

Move all hardcoded prompt strings from `orchestrator.py` and `solver.py` into a new
`agents/arc3/prompts.py` module. Pure refactor — zero behavioral change.

## Technical Approach

### Step 1: Create `agents/arc3/prompts.py`

```python
"""ARC3 prompt constants — Layer 4 (Prompt & Knowledge).

All prompt templates used by the orchestrator and solver live here.
Imported by runtime modules; never defined inline.
"""

# orchestrator.py build_action_packet() — SYSTEM block
SYSTEM_PROMPT = (
    "SYSTEM: You are an ARC puzzle solver. "
    "Treat action ids as opaque operators until this puzzle provides evidence about their effects. "
    "Available actions: {available_actions}."
)

# orchestrator.py build_action_packet() — INSTRUCTION block
INSTRUCTION_TEMPLATE = (
    "INSTRUCTION: {effect_summary}"
    "What should you try next? "
    "Choose the next valid action based on observed effects. "
    "Start in an exploration phase: until each available action has at least one observed effect, prefer untested actions. "
    "Prefer actions with strong_progress or tentative_progress evidence. "
    "Treat no_progress evidence as a reason to switch actions unless reward improved. "
    "Use an UNTESTED action when repeated actions are low-value or looped. "
    "If the top tested actions both decay into low_value or no_progress, broaden exploration instead of bouncing between them. "
    "After 2 consecutive zero-reward tentative steps on the same action, require stronger evidence than before or switch. "
    "Do not let a memory-only first move override the current observation unless the memory clearly matches this puzzle. "
    "Do not invent human labels for actions beyond the observed effects. "
    "Respond with JSON {{\"action_id\":..., \"rationale\":...}}, and make the rationale cite one observed effect label or say UNTESTED."
)

# orchestrator.py _mental_sandbox_loop() — sandbox instruction appended to prompt
SANDBOX_INSTRUCTION = (
    "\n\nMENTAL SANDBOX: You can use the 'sandbox_thought' tool to peek at the consequences of an action "
    "based on known facts and plans before you commit. Respond with: "
    "{\"thought\": \"I want to test ACTIONX\", \"sandbox_thought\": \"ACTIONX\"} "
    "to use the tool, or provide your final choice as JSON {\"action_id\":..., \"rationale\":...}."
)

# orchestrator.py _mental_sandbox_loop() / _query_llm() — system messages
SANDBOX_SYSTEM_MESSAGE = "You are an ARC reasoning assistant with a mental sandbox."
QUERY_LLM_SYSTEM_MESSAGE = "You are an ARC reasoning assistant."

# solver.py VictoryHypothesizer — hypothesis prompt
VICTORY_HYPOTHESIS_TEMPLATE = """You are analyzing an unknown game. Based on the evidence below,
hypothesize what the WINNING CONDITION is.

Game archetype: {archetype}

Object roles detected:
{object_roles}

Past successful plans with similar goals:
{past_plans}

Known game lessons:
{lessons}

Observed progress signals: {reward_summary}

Respond with EXACTLY this JSON format (no other text):
{{
  "condition_type": "<reach_goal|collect_all|survive|score_threshold|eliminate>",
  "description": "<one sentence describing the win condition>",
  "target_color_id": <integer color id or null>,
  "confidence": <0.0-1.0>
}}"""
```

### Step 2: Update `agents/arc3/orchestrator.py`

1. Add import at top:
   ```python
   from agents.arc3.prompts import (
       SYSTEM_PROMPT,
       INSTRUCTION_TEMPLATE,
       SANDBOX_INSTRUCTION,
       SANDBOX_SYSTEM_MESSAGE,
       QUERY_LLM_SYSTEM_MESSAGE,
   )
   ```

2. In `build_action_packet()` (~line 622), replace the inline SYSTEM block:
   ```python
   packet.blocks.append(ContentBlock(
       type="SYSTEM",
       content=SYSTEM_PROMPT.format(available_actions=', '.join(available_actions))
   ))
   ```

3. In `build_action_packet()` (~line 697), replace the inline INSTRUCTION block:
   ```python
   packet.blocks.append(ContentBlock(
       type="INSTRUCTION",
       content=INSTRUCTION_TEMPLATE.format(effect_summary=effect_summary)
   ))
   ```

4. In `_mental_sandbox_loop()` (~line 845), replace sandbox instruction:
   ```python
   current_prompt += SANDBOX_INSTRUCTION
   ```

5. In `_mental_sandbox_loop()` (~line 852), replace system message:
   ```python
   messages = [
       {"role": "system", "content": SANDBOX_SYSTEM_MESSAGE},
       {"role": "user", "content": current_prompt},
   ]
   ```

6. In `_query_llm()` (~line 911), replace system message:
   ```python
   messages = [
       {"role": "system", "content": QUERY_LLM_SYSTEM_MESSAGE},
       {"role": "user", "content": prompt},
   ]
   ```

### Step 3: Update `agents/arc3/solver.py`

1. Add import at top:
   ```python
   from agents.arc3.prompts import VICTORY_HYPOTHESIS_TEMPLATE
   ```

2. In `VictoryHypothesizer` class, replace:
   ```python
   PROMPT_TEMPLATE = VICTORY_HYPOTHESIS_TEMPLATE
   ```
   Or remove `PROMPT_TEMPLATE` entirely and reference `VICTORY_HYPOTHESIS_TEMPLATE` at the usage site.

### Step 4: Create `tests/test_arc3_prompts.py`

```python
"""Tests for prompt extraction — B122."""
from agents.arc3 import prompts


def test_all_prompt_constants_are_nonempty_strings():
    names = [
        "SYSTEM_PROMPT",
        "INSTRUCTION_TEMPLATE",
        "SANDBOX_INSTRUCTION",
        "SANDBOX_SYSTEM_MESSAGE",
        "QUERY_LLM_SYSTEM_MESSAGE",
        "VICTORY_HYPOTHESIS_TEMPLATE",
    ]
    for name in names:
        val = getattr(prompts, name)
        assert isinstance(val, str), f"{name} is not a string"
        assert len(val) > 10, f"{name} is suspiciously short"


def test_system_prompt_has_placeholder():
    assert "{available_actions}" in prompts.SYSTEM_PROMPT


def test_instruction_template_has_placeholder():
    assert "{effect_summary}" in prompts.INSTRUCTION_TEMPLATE


def test_victory_template_has_all_placeholders():
    for key in ("archetype", "object_roles", "past_plans", "lessons", "reward_summary"):
        assert f"{{{key}}}" in prompts.VICTORY_HYPOTHESIS_TEMPLATE
```

## Validation Commands

```bash
# Verify no hardcoded prompts remain in runtime files
grep -rn "You are an ARC" agents/arc3/orchestrator.py agents/arc3/solver.py
# Should return 0 hits

# Run new tests
pytest tests/test_arc3_prompts.py -q

# Run existing tests to confirm no regression
pytest tests/ -q -x --timeout=60
```

## Risks

- Low risk: pure string relocation, no logic change.
- If any test imports or mocks the old inline constants directly, those tests need updating.

## Gemini CLI Prompt

```
gemini -p "Read backlog/plans/B-122-extract-shared-prompts.md and implement exactly as specified. Read existing files first — especially agents/arc3/orchestrator.py (build_action_packet, _mental_sandbox_loop, _query_llm), agents/arc3/solver.py (VictoryHypothesizer.PROMPT_TEMPLATE), and docs/arc-harness-rules.md. Follow the plan precisely. Do not skip tests. Do not simplify the plan. Use minimal safe changes. Preserve existing behavior outside scope. Add or update tests for the acceptance criteria. Run relevant validation commands and report: changed files, test commands, pass/fail summary, and regressions found/fixed." --yolo
```
