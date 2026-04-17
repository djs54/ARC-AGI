# Plan for B156 — ARC Level-Aware Orchestration Loop

## Card Metadata

- **Card ID**: B156
- **Priority**: P0
- **Dependencies**: B150, B151, B152, B157

## Summary

Wire level transitions (B157) to the analysis/hypothesis/verification pipeline (B150→B151→B152). At each level transition, run the knowledge pipeline and configure the next level's prompt mode and exploration policy based on verification confidence. This is the integration card that makes cross-level learning work.

### Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│ Game Start                                                    │
│                                                               │
│  Level 1: EXPLORE (no prior knowledge)                       │
│  ├─ Prompt: exploration mode (B153)                          │
│  ├─ Exploration: full (B154)                                 │
│  └─ ActionFacts: learning from scratch                       │
│                                                               │
│  Level 1 WIN → _on_level_transition()                        │
│  ├─ B157: Capture (start_grid, end_grid, actions)            │
│  ├─ B150: diff_grids() → GridDiff, initial LevelPattern     │
│  ├─ B151: hypothesize() → GameRuleHypothesis[]               │
│  └─ Set confidence for level 2                               │
│                                                               │
│  Level 2: APPLY (partial knowledge)                          │
│  ├─ Prompt: rule_application mode (B153)                     │
│  ├─ Exploration: reduced (B154)                              │
│  └─ ActionFacts: carry over from level 1                     │
│                                                               │
│  Level 2 WIN → _on_level_transition()                        │
│  ├─ B157: Capture level 2 data                               │
│  ├─ B150: Update LevelPattern with 2 levels                 │
│  ├─ B151: Refine hypotheses with more evidence              │
│  ├─ B152: Verify against both solved levels                  │
│  └─ Set confidence for level 3                               │
│                                                               │
│  Level 3+: EXECUTE (growing confidence)                      │
│  ├─ Prompt: execution mode if confidence > 0.8 (B153)       │
│  ├─ Exploration: minimal (B154)                              │
│  └─ Apply learned rule directly                              │
│                                                               │
│  ... continue through all levels ...                         │
│                                                               │
│  Game Complete → B155: Store game strategy to memory          │
└─────────────────────────────────────────────────────────────┘
```

## Technical Approach

### 1. Wire _on_level_transition() to knowledge pipeline

B157 provides the level transition hook. B156 fills it with the analysis pipeline:

```python
def _on_level_transition(self, completed_level, solved_levels):
    """B157 calls this at level transitions. B156 wires the knowledge pipeline."""
    self._current_level = completed_level + 1

    # B150: Analyze the just-completed level
    latest = solved_levels[-1]
    self._analyze_level_transition(latest)

    # B151: Generate/refine game rule hypotheses
    if self._level_pattern:
        asyncio.create_task(self._generate_hypotheses(solved_levels))

    # Emit trace
    self._emit_trace_event("operation", "level_transition", {
        "completed_level": completed_level,
        "total_solved": len(solved_levels),
        "confidence": self._rule_confidence,
        "next_mode": self._select_prompt_mode(),
    })

    # Partial reset: keep learned knowledge, clear per-level state
    self._step_history_this_level = []
    self._consecutive_no_progress_steps = 0
    self._forced_exploration_count = 0  # B154
    if hasattr(self, '_action_fatigue'):
        self._action_fatigue.clear()

async def _generate_hypotheses(self, solved_levels):
    """B151 + B152: Generate hypotheses and verify against solved levels."""
    from agents.arc3.solver import GameRuleHypothesizer
    from agents.arc3.repl_verification import LevelReplayVerifier, RuleRefinementLoop

    hypothesizer = GameRuleHypothesizer()
    hypotheses = await hypothesizer.hypothesize(
        level_pattern=self._level_pattern,
        solved_levels=solved_levels,
        llm_client=self.llm,
        memory_hypotheses=getattr(self, '_memory_hypotheses', None),
    )

    if not hypotheses:
        return

    best = hypotheses[0]

    # B152: Verify against all solved levels (if 2+)
    if len(solved_levels) >= 2:
        verifier = LevelReplayVerifier()
        loop = RuleRefinementLoop(self.llm, verifier)
        best = await loop.refine(best, solved_levels)

    self._game_rule_hypothesis = best
    self._rule_confidence = best.confidence
    self._action_semantics = best.action_semantics or {}

    self._emit_trace_event("operation", "hypothesis_update", {
        "rule": best.rule_description,
        "confidence": best.confidence,
        "n_actions_mapped": len(best.action_semantics or {}),
    })
```

### 2. Progressive knowledge attributes

```python
class ARCOrchestrator:
    def __init__(self, ...):
        # ... existing init ...

        # B156: Progressive knowledge (persists across levels)
        self._game_rule_hypothesis: Optional[GameRuleHypothesis] = None
        self._rule_confidence: float = 0.0
        self._action_semantics: Dict[str, str] = {}
        self._level_pattern: Optional[LevelPattern] = None
        self._solved_level_diffs: List[GridDiff] = []
```

### 3. Confidence-based mode routing

```python
def _select_prompt_mode(self) -> str:
    """B156: Select prompt mode based on level and knowledge state."""
    current_level = getattr(self, '_current_level', 0)
    n_solved = len(getattr(self, '_solved_levels', []))
    confidence = self._rule_confidence

    # High confidence + multiple levels verified → execute
    if confidence > 0.8 and n_solved >= 2:
        return "execution"

    # Some knowledge → show insights
    if n_solved >= 1 and confidence > 0.4:
        return "rule_application"

    # Level 1, no knowledge → explore
    if current_level <= 1 and n_solved == 0:
        return "exploration"

    # Default → existing navigation
    return "navigation"
```

### 4. Level 1 bootstrap (no pipeline — just explore)

```python
# In runner.py, for level 1:
# No knowledge pipeline runs — there are no solved levels yet.
# The agent starts in exploration/navigation mode.
# B155 memory may provide hints if similar games were played before.

# After level 1 is won, the pipeline kicks in via _on_level_transition().
```

### 5. act() routing

```python
async def act(self, observation, memory_context, step):
    mode = self._select_prompt_mode()

    if mode == "execution" and self._game_rule_hypothesis:
        # High confidence — apply rule directly
        # The LLM prompt (B153) shows the rule and strategy
        # The exploration policy (B154) skips forced exploration
        pass  # Fall through to normal act with execution-mode prompt

    # All modes go through the same act() path — the prompt and
    # exploration policy handle the mode differences (B153, B154)
    return await self._act_with_mode(observation, memory_context, step, mode)
```

### 6. Reset on game restart

```python
def reset_for_retry(self):
    # ... existing reset ...
    self._game_rule_hypothesis = None
    self._rule_confidence = 0.0
    self._action_semantics = {}
    self._level_pattern = None
    self._solved_level_diffs = []
    # Note: _solved_levels is managed by runner (B157), not orchestrator
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Add progressive knowledge attributes; wire `_on_level_transition()` to B150→B151→B152 pipeline; add `_generate_hypotheses()`, `_select_prompt_mode()`; modify `act()` for mode routing; modify `reset_for_retry()` |
| `agents/arc3/runner.py` | Ensure `_on_level_transition()` is called at level transitions (B157 provides this) |
| `tests/test_b156_level_orchestration.py` | NEW: test knowledge pipeline, mode routing, progressive confidence |

## Validation Commands

```bash
python3 -m pytest tests/test_b156_level_orchestration.py -v
python3 -m pytest tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- **Async pipeline at level transition**: The hypothesis generation and verification runs between levels. If it's slow (LLM call + REPL), it may delay the next level. Keep refinement rounds to 2 max.
- **Confidence calibration**: The confidence thresholds (0.4, 0.8) for mode selection are initial estimates. May need tuning based on actual game performance.
- **Pipeline failure**: If B150/B151/B152 crash, the orchestrator should catch the exception and fall back to navigation mode. No pipeline failure should block the game loop.
- **Level 1 has no pipeline**: This is by design. The pipeline can only run after at least 1 level is solved.

## Done When

- Level transitions trigger B150→B151→B152 pipeline
- Confidence routing selects correct prompt/exploration mode
- Knowledge accumulates across levels
- Level 1 starts in exploration mode
- High-confidence levels enter execution mode
- Pipeline failures fall back gracefully to navigation mode
- All trace events emitted
- No existing code deleted
- All tests pass
