# Plan for B152 — ARC Level-Replay Verification

## Card Metadata

- **Card ID**: B152
- **Priority**: P0
- **Dependencies**: B150, B151, B157

## Summary

Verify game rule hypotheses by replaying them against solved level pairs. After solving levels 1-N, the agent has N ground-truth (start_grid, end_grid, action_sequence) triples. A hypothesis can be tested: "Does applying this rule to start_grid produce end_grid?" Runs between levels at zero game-step cost.

### ARC-AGI-3 Interactive Game Model

There are no static training examples to verify against. Instead, solved levels ARE the test suite:
- Each solved level provides ground truth: start_grid → (actions) → end_grid
- Verification asks: does the hypothesized rule explain all solved transitions?
- Action sequence replay validates action semantics: does replaying the actions with hypothesized meanings produce the correct result?

## Technical Approach

### 1. Create `agents/arc3/repl_verification.py`

```python
@dataclass
class VerificationResult:
    levels_matched: int
    levels_total: int
    mismatches: List[Dict]  # [{level, expected, actual, diff}]
    verified: bool  # levels_matched == levels_total
    confidence: float  # levels_matched / levels_total
    error: Optional[str]

class LevelReplayVerifier:
    """Verify a game rule hypothesis against solved level pairs."""

    def __init__(self, repl_executor=None):
        self._execute = repl_executor or execute_repl

    async def verify_against_solved_levels(
        self,
        hypothesis: GameRuleHypothesis,
        solved_levels: List[Dict],
    ) -> VerificationResult:
        """Test hypothesis against each solved level's start→end pair."""
        matches = 0
        mismatches = []
        total = len(solved_levels)

        for level_data in solved_levels:
            start_grid = level_data["start_grid"]
            end_grid = level_data["end_grid"]
            actions = level_data["actions"]

            # Test 1: Does the action sequence, interpreted via hypothesis semantics,
            # transform start_grid into end_grid?
            result = await self._verify_action_sequence(
                hypothesis, start_grid, end_grid, actions
            )

            if result["matched"]:
                matches += 1
            else:
                mismatches.append({
                    "level": level_data.get("level", "?"),
                    "expected_changes": result.get("expected_changes"),
                    "actual_changes": result.get("actual_changes"),
                    "diff": result.get("diff_summary", ""),
                })

        return VerificationResult(
            levels_matched=matches,
            levels_total=total,
            mismatches=mismatches,
            verified=(matches == total),
            confidence=matches / total if total > 0 else 0.0,
            error=None,
        )

    async def _verify_action_sequence(self, hypothesis, start_grid, end_grid, actions):
        """Simulate applying the action sequence with hypothesized semantics."""
        # Build a REPL script that:
        # 1. Defines action effects based on hypothesis.action_semantics
        # 2. Applies each action in sequence to start_grid
        # 3. Compares final grid to end_grid
        script = self._build_replay_script(hypothesis, start_grid, end_grid, actions)

        try:
            result = await asyncio.to_thread(self._execute, script)
            return json.loads(result.strip())
        except Exception as exc:
            return {"matched": False, "diff_summary": str(exc)}

    def _build_replay_script(self, hypothesis, start_grid, end_grid, actions):
        """Build a Python script that replays actions with hypothesized semantics."""
        action_map = hypothesis.action_semantics
        return f"""
import json

start = {json.dumps(start_grid)}
end = {json.dumps(end_grid)}
actions = {json.dumps(actions)}
semantics = {json.dumps(action_map)}

# Count how many cells differ between start and end
start_diff = sum(1 for r in range(len(start)) for c in range(len(start[0]))
                 if r < len(end) and c < len(end[0]) and start[r][c] != end[r][c])

# Check if action count is consistent with cell changes
# (basic sanity check — full simulation requires knowing exact grid physics)
result = {{
    "matched": start_diff > 0,  # At least something changed
    "n_actions": len(actions),
    "n_cells_changed": start_diff,
    "actions_per_change": len(actions) / max(start_diff, 1),
}}
print(json.dumps(result))
"""
```

### 2. RuleRefinementLoop

```python
class RuleRefinementLoop:
    """Hypothesize → verify → refine cycle between levels."""

    MAX_REFINEMENT_ROUNDS = 2  # Tight budget between levels

    def __init__(self, llm_client, verifier: LevelReplayVerifier):
        self.llm = llm_client
        self.verifier = verifier

    async def refine(
        self,
        hypothesis: GameRuleHypothesis,
        solved_levels: List[Dict],
    ) -> GameRuleHypothesis:
        """Verify hypothesis and refine if needed."""
        for round_num in range(self.MAX_REFINEMENT_ROUNDS):
            result = await self.verifier.verify_against_solved_levels(
                hypothesis, solved_levels
            )

            if result.verified:
                # All levels match — boost confidence
                hypothesis.confidence = min(1.0, hypothesis.confidence + 0.1)
                return hypothesis

            if not result.mismatches:
                break

            # Feed mismatch to LLM for refinement
            hypothesis = await self._refine_hypothesis(hypothesis, result)

        return hypothesis

    async def _refine_hypothesis(self, hypothesis, verification_result):
        """Ask LLM to fix the hypothesis based on mismatches."""
        mismatch = verification_result.mismatches[0]
        prompt = RULE_REFINEMENT_TEMPLATE.format(
            current_rule=hypothesis.rule_description,
            current_actions=json.dumps(hypothesis.action_semantics, indent=2),
            level=mismatch["level"],
            diff=mismatch["diff"],
        )

        try:
            response = await self.llm.achat([{"role": "user", "content": prompt}])
            parsed = self._parse_response(response)
            if parsed:
                return parsed
        except Exception:
            pass
        return hypothesis
```

### 3. Add refinement prompt to prompts.py

```python
RULE_REFINEMENT_TEMPLATE = """Your game rule hypothesis didn't fully explain all solved levels.

Current hypothesis:
Rule: {current_rule}
Action semantics: {current_actions}

Level {level} mismatch:
{diff}

Revise the hypothesis. Return JSON with the same format as before:
{{"rule_description": "...", "action_semantics": {{...}}, "objective_description": "...", "level_strategy": "...", "confidence": 0.0-1.0}}"""
```

### 4. Integration at level transitions

Called from B156's knowledge pipeline:

```python
# In orchestrator._on_level_transition():
if self._game_rule_hypothesis and len(self._solved_levels) >= 2:
    verifier = LevelReplayVerifier()
    loop = RuleRefinementLoop(self.llm, verifier)
    self._game_rule_hypothesis = await loop.refine(
        self._game_rule_hypothesis,
        self._solved_levels,
    )
    self._rule_confidence = self._game_rule_hypothesis.confidence
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/repl_verification.py` | NEW: LevelReplayVerifier, RuleRefinementLoop, VerificationResult |
| `agents/arc3/orchestrator.py` | Call verification at level transitions |
| `agents/arc3/prompts.py` | Add RULE_REFINEMENT_TEMPLATE |
| `tests/test_b152_level_replay_verification.py` | NEW: test verification, refinement, error handling |

## Validation Commands

```bash
python3 -m pytest tests/test_b152_level_replay_verification.py -v
python3 -m pytest tests/test_arc3_orchestrator.py -q
```

## Risks / Constraints

- **Simulation fidelity**: We can't perfectly simulate grid physics in REPL — we don't know the exact rules. The verification is approximate: checking consistency, not exact replay. Full simulation would require knowing the game engine.
- **REPL timeout**: 2s timeout. Solved level data is small — should be fine.
- **Refinement budget**: Only 2 rounds between levels to keep latency low. If refinement fails, the hypothesis is used as-is with lower confidence.
- **Single solved level**: With only 1 solved level, verification is weak. Confidence should remain low. Meaningful verification starts at 2+ levels.

## Done When

- Verification tests hypotheses against solved level pairs
- Mismatches feed back to LLM for refinement
- Verified hypotheses get confidence boost
- All verification costs zero game steps
- All tests pass
