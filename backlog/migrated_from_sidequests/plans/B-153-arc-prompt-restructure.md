# Plan for B153 — ARC Level-Aware Prompt Restructure

## Card Metadata

- **Card ID**: B153
- **Priority**: P0
- **Dependencies**: B150, B151, B157

## Summary

Add level-aware prompt modes alongside the existing navigation prompt. The agent selects the appropriate mode based on the current level, number of solved levels, and rule hypothesis confidence:

- **Exploration mode** (Level 1): Encourage action experimentation. Target <400 tokens.
- **Rule-application mode** (Level 2+ with insights): Show learned game rules and action semantics. Target <500 tokens.
- **Execution mode** (High-confidence rule): Minimal — "apply this rule". Target <200 tokens.
- **Navigation mode** (Fallback): Existing 15-section prompt. Actively maintained.

### ARC-AGI-3 Interactive Game Model

The prompt adapts to the level progression:
- Level 1: No prior knowledge → exploration prompt ("try each action")
- Level 2+: Prior levels solved → rule-application prompt (show insights)
- High confidence: Verified rule → execution prompt ("do this specific thing")
- Low confidence: Fall back to navigation prompt (existing approach)

## Technical Approach

### 1. New prompt templates in prompts.py

```python
# Level 1: Exploration mode (discover actions)
ARC_EXPLORATION_SYSTEM_PROMPT = (
    "You are playing level 1 of an ARC-AGI-3 game with {total_levels} levels. "
    "You need to discover what each action does. Try different actions and "
    "observe their effects on the grid."
)

ARC_EXPLORATION_INSTRUCTION_TEMPLATE = (
    "CURRENT GRID:\n{current_grid}\n\n"
    "AVAILABLE ACTIONS: {available_actions}\n\n"
    "You haven't discovered what these actions do yet. "
    "Try an action to learn its effect.\n"
    "Return JSON: {{\"action_id\": \"...\", \"rationale\": \"...\"}}"
)

# Level 2+: Rule-application mode (apply learned insights)
ARC_LEVEL_INSIGHT_SYSTEM_PROMPT = (
    "You are playing level {current_level} of {total_levels} in an ARC game. "
    "From prior levels, you've learned the game's rules."
)

ARC_LEVEL_INSIGHT_TEMPLATE = (
    "GAME RULE: {rule_description}\n\n"
    "ACTION EFFECTS:\n{action_semantics}\n\n"
    "PRIOR LEVELS SOLVED: {n_solved}\n"
    "RULE CONFIDENCE: {confidence:.0%}\n\n"
    "CURRENT GRID:\n{current_grid}\n\n"
    "AVAILABLE ACTIONS: {available_actions}\n\n"
    "Apply your knowledge to solve this level.\n"
    "Return JSON: {{\"action_id\": \"...\", \"rationale\": \"...\"}}"
)

# High-confidence execution mode
ARC_EXECUTION_SYSTEM_PROMPT = (
    "You are playing an ARC game. You know the rule: {rule_description}. "
    "Execute it efficiently."
)

ARC_EXECUTION_INSTRUCTION_TEMPLATE = (
    "RULE: {rule_description}\n"
    "STRATEGY: {level_strategy}\n\n"
    "CURRENT GRID:\n{current_grid}\n\n"
    "Available: {available_actions}\n"
    "Return JSON: {{\"action_id\": \"...\", \"rationale\": \"...\"}}"
)
```

### 2. Compact grid renderer

```python
# In orchestrator.py

_COLOR_CHARS = ".#@*+~^%&$!?<>="  # 0-14 mapped to single chars

@staticmethod
def render_grid_compact(grid: List[List[int]], max_rows: int = 30) -> str:
    """Render 64x64 grid as single-character-per-cell visual.

    For 64x64 grids, truncates rows and shows "... (N more rows)".
    """
    lines = []
    display_grid = grid[:max_rows] if len(grid) > max_rows else grid
    for row in display_grid:
        line = "".join(
            _COLOR_CHARS[min(cell, len(_COLOR_CHARS) - 1)]
            for cell in row
        )
        lines.append(line)
    if len(grid) > max_rows:
        lines.append(f"... ({len(grid) - max_rows} more rows)")
    return "\n".join(lines)
```

### 3. Four-mode auto-selection in build_action_packet()

```python
def build_action_packet(self, observation, memory_context, step_history, available_actions):
    # Determine mode based on level and knowledge state
    mode = self._select_prompt_mode()

    if mode == "execution":
        return self._build_execution_packet(observation, available_actions)
    elif mode == "rule_application":
        return self._build_rule_application_packet(observation, available_actions)
    elif mode == "exploration":
        return self._build_exploration_packet(observation, available_actions)
    else:  # "navigation"
        return self._build_navigation_packet(observation, memory_context, step_history, available_actions)

def _select_prompt_mode(self) -> str:
    """Auto-select prompt mode based on level and knowledge state."""
    current_level = getattr(self, '_current_level', 0)
    n_solved = len(getattr(self, '_solved_levels', []))
    confidence = getattr(self, '_rule_confidence', 0.0)

    if confidence > 0.8 and n_solved >= 2:
        return "execution"
    if n_solved >= 1 and confidence > 0.4:
        return "rule_application"
    if current_level <= 1 and n_solved == 0:
        return "exploration"
    return "navigation"  # fallback
```

### 4. Rule-application packet builder

```python
def _build_rule_application_packet(self, observation, available_actions):
    """Level 2+: Show prior level insights."""
    packet = PromptPacket()
    hypothesis = getattr(self, '_game_rule_hypothesis', None)

    packet.blocks.append(ContentBlock(
        type="SYSTEM",
        content=ARC_LEVEL_INSIGHT_SYSTEM_PROMPT.format(
            current_level=self._current_level,
            total_levels=observation.get("win_levels", "?"),
        ),
    ))

    action_semantics = "\n".join(
        f"  {k}: {v}" for k, v in (hypothesis.action_semantics or {}).items()
    ) if hypothesis else "Not yet determined"

    packet.blocks.append(ContentBlock(
        type="INSTRUCTION",
        content=ARC_LEVEL_INSIGHT_TEMPLATE.format(
            rule_description=hypothesis.rule_description if hypothesis else "Unknown",
            action_semantics=action_semantics,
            n_solved=len(self._solved_levels),
            confidence=self._rule_confidence,
            current_grid=self.render_grid_compact(observation.get("grid", [])),
            available_actions=", ".join(available_actions),
        ),
    ))

    return packet
```

### 5. Token budget tracking

```python
mode = self._select_prompt_mode()
budget = {"exploration": 400, "rule_application": 500, "execution": 200, "navigation": 1200}[mode]
prompt = packet.render()
token_estimate = self.serializer._estimate_tokens(prompt)
if token_estimate > budget:
    logger.warning("B153: %s prompt exceeds %d token target (%d tokens)", mode, budget, token_estimate)
self._emit_trace_event("operation", "prompt_budget", {
    "tokens": token_estimate, "mode": mode, "level": self._current_level,
})
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/prompts.py` | Add ARC_EXPLORATION_*, ARC_LEVEL_INSIGHT_*, ARC_EXECUTION_* templates |
| `agents/arc3/orchestrator.py` | Add `render_grid_compact()`, `_select_prompt_mode()`, `_build_exploration_packet()`, `_build_rule_application_packet()`, `_build_execution_packet()`; modify `build_action_packet()` |
| `tests/test_b153_prompt_restructure.py` | NEW: test mode selection, token budgets, grid rendering |

## Validation Commands

```bash
python3 -m pytest tests/test_b153_prompt_restructure.py -v
python3 -m pytest tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- **64x64 grid rendering**: Full 64x64 = 64 lines × 64 chars = ~4096 chars ≈ ~1000 tokens. Need max_rows truncation to stay under budget. 30 rows = ~500 tokens for grid alone — may need to go lower.
- **Backward compatibility**: Navigation mode must remain fully functional.
- **Mode selection edge cases**: What if the agent is on level 3 but has 0 confidence? Falls back to navigation — correct behavior.

## Done When

- Exploration mode works for level 1 (<400 tokens)
- Rule-application mode shows insights from prior levels (<500 tokens)
- Execution mode is minimal (<200 tokens)
- Navigation mode actively maintained as fallback
- Mode auto-selects based on level and confidence
- All tests pass
