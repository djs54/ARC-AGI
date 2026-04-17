# Plan for B155 — ARC Cross-Game Strategy Memory

## Card Metadata

- **Card ID**: B155
- **Priority**: P2
- **Dependencies**: B150, B151, B157

## Summary

Make cross-game memory useful by storing full-game strategies (action semantics, game rules, level-solving approaches) and retrieving them when starting a new game with similar initial grid characteristics. Replace generic game-archetype queries with game-characteristic-based queries.

### ARC-AGI-3 Interactive Game Model

Cross-game knowledge includes:
- **Action semantics**: ACTION1-4 are often directional, ACTION5 = interact, ACTION6 = coordinate/paint, ACTION7 = undo. Different games may have different mappings.
- **Game rule patterns**: "Games with color-shifting grids usually require matching a target pattern"
- **Level strategies**: "On later levels, focus on changed cells only"
- **Game difficulty profiles**: "8-level game, levels 1-3 easy, 4-6 medium, 7-8 hard"

Memory is stored once per game (after all levels), retrieved at game start to seed level 1 exploration.

## Technical Approach

### 1. Structured game strategy storage in evaluate()

```python
async def evaluate(self, correct, steps, max_steps, final_observation=None):
    # ... existing evaluation logic ...

    # B155: Store full-game strategy
    solved_levels = getattr(self, '_solved_levels', [])
    hypothesis = getattr(self, '_game_rule_hypothesis', None)

    grid = final_observation.get("grid") or [] if final_observation else []
    from agents.arc3.grid_analysis import grid_characteristic_summary
    chars = grid_characteristic_summary(grid) if grid else {}

    # Per-level action summary
    level_summaries = []
    for lv in solved_levels:
        actions = lv.get("actions", [])
        action_counts = {}
        for a in actions:
            action_counts[a] = action_counts.get(a, 0) + 1
        level_summaries.append(
            f"Level {lv.get('level', '?')}: {len(actions)} actions "
            f"({', '.join(f'{k}x{v}' for k, v in action_counts.items())})"
        )

    lesson_content = (
        f"ARC GAME STRATEGY\n"
        f"Grid: {chars.get('rows', '?')}x{chars.get('cols', '?')}, "
        f"{chars.get('n_colors', '?')} colors\n"
        f"Levels: {len(solved_levels)} solved\n"
        f"Outcome: {'WON' if correct else 'LOST'}\n"
    )

    if hypothesis:
        action_sem = "; ".join(f"{k}={v}" for k, v in hypothesis.action_semantics.items())
        lesson_content += (
            f"Game rule: {hypothesis.rule_description}\n"
            f"Actions: {action_sem}\n"
            f"Objective: {hypothesis.objective_description}\n"
            f"Confidence: {hypothesis.confidence:.2f}\n"
        )

    lesson_content += "Level breakdown:\n" + "\n".join(level_summaries)

    await self.brain.notify_turn(
        role="assistant",
        content=lesson_content,
        session_id=self.session_id,
    )
```

### 2. Game-characteristic-based memory query

```python
def _memory_query(self, observation):
    """B155: Build memory query from initial grid characteristics."""
    grid = observation.get("grid") or []
    if not grid:
        return "ARC interactive game strategy"

    from agents.arc3.grid_analysis import grid_characteristic_summary
    chars = grid_characteristic_summary(grid)

    query_parts = [
        "ARC game",
        f"{chars['rows']}x{chars['cols']}",
        f"{chars['n_colors']} colors",
    ]

    # Include available actions if known
    available = observation.get("available_actions") or []
    if available:
        query_parts.append(f"{len(available)} actions")

    if chars.get("symmetry"):
        for sym in chars["symmetry"]:
            query_parts.append(f"{sym}")

    return " ".join(query_parts)
```

### 3. Parse retrieved game strategies into hypotheses

```python
def _parse_game_strategies(self, memories: List[dict]) -> List[GameRuleHypothesis]:
    """B155: Extract game rule hypotheses from retrieved memories."""
    from agents.arc3.solver import GameRuleHypothesis

    hypotheses = []
    for memory in memories:
        text = memory.get("text_raw", "") or memory.get("content", "")
        if "ARC GAME STRATEGY" not in text:
            continue

        # Parse structured fields
        rule_match = re.search(r"Game rule: (.+?)(?:\n|$)", text)
        actions_match = re.search(r"Actions: (.+?)(?:\n|$)", text)
        objective_match = re.search(r"Objective: (.+?)(?:\n|$)", text)
        outcome_match = re.search(r"Outcome: (\w+)", text)

        if not rule_match:
            continue

        # Parse action semantics from "ACTION1=move up; ACTION2=move down" format
        action_semantics = {}
        if actions_match:
            for pair in actions_match.group(1).split(";"):
                pair = pair.strip()
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    action_semantics[k.strip()] = v.strip()

        # Won games get higher confidence
        confidence = 0.6 if outcome_match and outcome_match.group(1) == "WON" else 0.2

        hypotheses.append(GameRuleHypothesis(
            rule_description=rule_match.group(1),
            action_semantics=action_semantics,
            objective_description=objective_match.group(1) if objective_match else "",
            level_strategy="Apply prior game knowledge",
            confidence=confidence * float(memory.get("similarity", 0.5)),
            evidence=[f"Retrieved from memory (similarity={memory.get('similarity', '?')})"],
            contradictions=[],
            source="memory",
        ))

    return hypotheses
```

### 4. Integration in perceive() at level 1

```python
# In perceive(), at step 0 of level 1:
if self._current_level <= 1 and step == 0 and should_retrieve:
    # ... existing retrieval code ...

    # B155: Parse game strategies from retrieved memories
    memory_hypotheses = self._parse_game_strategies(
        truth.get("results", []) + lessons.get("lessons", [])
    )
    if memory_hypotheses:
        self._memory_hypotheses = memory_hypotheses
        logger.info("B155: Retrieved %d game strategies from memory", len(memory_hypotheses))
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Modify `evaluate()` for game strategy storage; modify `_memory_query()` for game characteristics; add `_parse_game_strategies()`; modify `perceive()` at level 1 |
| `agents/arc3/grid_analysis.py` | Ensure `grid_characteristic_summary()` exists (from B150) |
| `tests/test_b155_game_strategy_memory.py` | NEW: test strategy storage, query generation, strategy parsing |

## Validation Commands

```bash
python3 -m pytest tests/test_b155_game_strategy_memory.py -v
python3 -m pytest tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- **Memory cold start**: First game has no strategies to retrieve. Expected — the system improves over time.
- **Cross-game action variation**: Different games may use actions differently. Retrieved action semantics should be treated as hints, not facts. Confidence reflects this.
- **Text parsing fragility**: Structured text format parsed via regex. Use distinctive markers ("ARC GAME STRATEGY") and simple format.

## Done When

- Completed games store structured game strategies
- Memory queries use grid characteristics
- Retrieved strategies parsed into GameRuleHypothesis objects
- Memory hypotheses merged with B151 hypotheses at level 1
- Failed games store negative-valence strategies
- All tests pass
