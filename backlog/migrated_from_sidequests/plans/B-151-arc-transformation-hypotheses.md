# Plan for B151 — ARC Cross-Level Game Rule Hypothesizer

## Card Metadata

- **Card ID**: B151
- **Priority**: P0
- **Dependencies**: B150, B157

## Summary

Add a GameRuleHypothesizer that generates game rule hypotheses from solved level data. Uses B150's LevelPattern and solved level action sequences to infer what the game's rules are — what each action does, what the objective is, and how to approach the next level. Runs at level transitions, not pre-game.

### ARC-AGI-3 Interactive Game Model

There are NO static training examples. The agent discovers rules by playing:
- Level 1 = pure exploration (tutorial). No prior data.
- After level 1: the solved level's (start_grid, end_grid, action_sequence) is implicit training data
- After level 2+: cross-level consensus provides stronger evidence
- GameRuleHypothesizer runs at each level transition, refining its understanding

VictoryHypothesizer is **retained** as the fallback for level 1 and when hypothesis confidence is low.

## Technical Approach

### 1. Add GameRuleHypothesis dataclass to solver.py

```python
@dataclass
class GameRuleHypothesis:
    rule_description: str           # "Move colored tiles to match target pattern"
    action_semantics: Dict[str, str]  # {"ACTION1": "move up", "ACTION5": "toggle cell"}
    objective_description: str      # "Match the target pattern shown in the corner"
    level_strategy: str             # "Focus on changed cells, work left-to-right"
    confidence: float               # 0-1, increases with more solved levels
    evidence: List[str]             # Which level diffs support this
    contradictions: List[str]       # What doesn't fit
    source: str                     # "level_analysis" | "llm" | "memory"
```

### 2. Add prompt template to prompts.py

```python
GAME_RULE_HYPOTHESIS_TEMPLATE = """You are playing an ARC-AGI-3 interactive game with {total_levels} levels.
You have solved {n_solved} level(s). Based on the evidence below, hypothesize the GAME RULES.

Solved level summaries:
{level_summaries}

Action effects observed:
{action_effects}

Cross-level patterns:
{cross_level_pattern}

Respond with EXACTLY this JSON format:
{{
  "rule_description": "<one sentence: what is this game about?>",
  "action_semantics": {{"ACTION1": "<what it does>", "ACTION2": "<what it does>", ...}},
  "objective_description": "<what does winning a level require?>",
  "level_strategy": "<approach for the next level>",
  "confidence": <0.0-1.0>
}}"""
```

### 3. GameRuleHypothesizer class

```python
class GameRuleHypothesizer:
    """Generates game rule hypotheses from solved level data."""

    async def hypothesize(
        self,
        level_pattern: LevelPattern,
        solved_levels: List[Dict],
        llm_client: Any,
        memory_hypotheses: Optional[List[GameRuleHypothesis]] = None,
    ) -> List[GameRuleHypothesis]:
        """Generate ranked game rule hypotheses from solved levels.

        1. Check if deterministic analysis alone gives high-confidence answer
        2. If not, use LLM with structured evidence from solved levels
        3. Merge with memory-retrieved hypotheses (B155)
        """
        hypotheses = []

        # Fast path: if action effects are very consistent, skip LLM
        if level_pattern.confidence > 0.9:
            hypotheses.append(self._hypothesis_from_pattern(level_pattern))

        # LLM path: interpret the evidence
        level_summaries = self._format_solved_levels(solved_levels)
        action_effects = self._format_action_effects(level_pattern)
        prompt = GAME_RULE_HYPOTHESIS_TEMPLATE.format(
            total_levels=solved_levels[-1].get("total_levels", "?") if solved_levels else "?",
            n_solved=len(solved_levels),
            level_summaries=level_summaries,
            action_effects=action_effects,
            cross_level_pattern=level_pattern.game_rule_summary,
        )

        try:
            response = await llm_client.achat([{"role": "user", "content": prompt}])
            parsed = self._parse_response(response)
            if parsed:
                hypotheses.append(parsed)
        except Exception as exc:
            logger.warning("GameRuleHypothesizer LLM failed: %s", exc)

        # Merge memory hypotheses (from B155)
        if memory_hypotheses:
            hypotheses.extend(memory_hypotheses)

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses[:3]

    def _hypothesis_from_pattern(self, pattern: LevelPattern) -> GameRuleHypothesis:
        """Convert a high-confidence LevelPattern to a hypothesis."""
        return GameRuleHypothesis(
            rule_description=pattern.game_rule_summary,
            action_semantics=pattern.consistent_action_effects,
            objective_description="Match the pattern suggested by level progression",
            level_strategy="Apply known action effects to reach the goal state",
            confidence=pattern.confidence,
            evidence=[f"Cross-level analysis: {pattern.game_rule_summary}"],
            contradictions=[],
            source="level_analysis",
        )

    def _format_solved_levels(self, solved_levels: List[Dict]) -> str:
        """Format solved level data for the LLM prompt."""
        lines = []
        for level in solved_levels:
            n_actions = len(level.get("actions", []))
            action_seq = " → ".join(level.get("actions", [])[:10])
            if n_actions > 10:
                action_seq += f" ... ({n_actions} total)"
            lines.append(
                f"Level {level.get('level', '?')}: "
                f"{n_actions} actions to solve. "
                f"Sequence: {action_seq}"
            )
        return "\n".join(lines)

    def _format_action_effects(self, pattern: LevelPattern) -> str:
        """Format observed action effects."""
        if not pattern.consistent_action_effects:
            return "No consistent action effects observed yet."
        lines = []
        for action_id, effect in pattern.consistent_action_effects.items():
            lines.append(f"  {action_id}: {effect}")
        return "\n".join(lines)
```

### 4. Integration at level transitions

Called from B157's `_on_level_transition()` via B156's knowledge pipeline:

```python
# In orchestrator, at level transition:
if self._level_pattern and len(self._solved_levels) >= 1:
    hypothesizer = GameRuleHypothesizer()
    hypotheses = await hypothesizer.hypothesize(
        level_pattern=self._level_pattern,
        solved_levels=self._solved_levels,
        llm_client=self.llm,
        memory_hypotheses=getattr(self, '_memory_hypotheses', None),
    )
    if hypotheses:
        self._game_rule_hypothesis = hypotheses[0]
        self._rule_confidence = hypotheses[0].confidence
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/solver.py` | Add GameRuleHypothesis, GameRuleHypothesizer |
| `agents/arc3/prompts.py` | Add GAME_RULE_HYPOTHESIS_TEMPLATE |
| `tests/test_b151_game_rule_hypotheses.py` | NEW: test hypothesis generation from solved level data |

## Validation Commands

```bash
python3 -m pytest tests/test_b151_game_rule_hypotheses.py -v
python3 -m pytest tests/test_arc3_solver.py -q
```

## Risks / Constraints

- **LLM quality with limited data**: After just 1 level, the hypothesis may be weak. Confidence scoring should reflect this — 1 level = low confidence, 3+ levels = higher.
- **Action sequence length**: Some levels may take many actions. Truncate to first 10 in the prompt to stay under token budget.
- **Level 1 has no data**: GameRuleHypothesizer cannot run before level 1 is solved. Level 1 must use exploration/navigation fallback.

## Done When

- GameRuleHypothesizer generates hypotheses from solved level data
- High-confidence patterns produce hypotheses without LLM calls
- LLM prompt includes level summaries, action sequences, cross-level patterns
- Confidence increases as more levels are solved
- VictoryHypothesizer retained for level 1 fallback
- All tests pass
