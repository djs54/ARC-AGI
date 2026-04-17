# Plan for B181 — LLM-as-Judge for Near-Miss Grading

## Card Metadata

- **Card ID**: B181
- **Priority**: P1
- **Dependencies**: None

## Summary

After a puzzle completes, score it on a 3-dimension rubric (structural correctness, partial match, reasoning quality) using an LLM judge. Replaces binary correct/incorrect with gradient signal.

## Current State

### Result recording (runner.py)

```python
result_payload["correct"] = (final_state == "WIN")
```

Binary pass/fail. No partial credit.

### ABTaskResult (ab_harness.py)

```python
@dataclass
class ABTaskResult:
    correct: bool
    ...
```

No judge_verdict field.

## Technical Approach

### Step 1: Create benchmarks/arc3/outcome_judge.py

```python
@dataclass
class JudgeVerdict:
    structural_score: int      # 0-5: right dimensions, color palette, structure
    partial_match_score: int   # 0-5: fraction of cells matching solution
    reasoning_score: int       # 0-5: trajectory narrative quality
    composite_score: float     # weighted average
    explanation: str           # judge's reasoning

class OutcomeJudge:
    def __init__(self, llm_client, model_name: str = None):
        self.llm = llm_client
        self.model = model_name  # None = use same as agent

    async def evaluate(
        self,
        final_grid: List[List[int]],
        expected_grid: Optional[List[List[int]]],
        trajectory_summary: str,
        archetype: str,
    ) -> Optional[JudgeVerdict]:
        if expected_grid is None:
            return None  # Can't judge without answer key

        # Compute partial match algorithmically (no LLM needed)
        cell_match_pct = self._compute_cell_match(final_grid, expected_grid)
        partial_score = min(5, int(cell_match_pct * 5 / 100))

        # LLM judges structural and reasoning quality
        prompt = self._build_rubric_prompt(final_grid, expected_grid, trajectory_summary, archetype)
        response = await self.llm.chat(prompt)
        structural_score, reasoning_score, explanation = self._parse_verdict(response)

        return JudgeVerdict(
            structural_score=structural_score,
            partial_match_score=partial_score,
            reasoning_score=reasoning_score,
            composite_score=(structural_score + partial_score + reasoning_score) / 3,
            explanation=explanation,
        )

    def _compute_cell_match(self, actual, expected) -> float:
        # Element-wise comparison, return percentage
        ...

    def _build_rubric_prompt(self, actual, expected, trajectory, archetype) -> str:
        # Fixed rubric prompt with scoring criteria
        ...
```

### Step 2: Rubric prompt design

```
You are evaluating an ARC puzzle agent's performance. Score on two dimensions (0-5 each):

STRUCTURAL CORRECTNESS (0-5):
0: Wrong dimensions or completely wrong colors
1: Right dimensions but < 20% cell match
2: Right color palette but wrong arrangement
3: Partial structure correct (some regions match)
4: Most structure correct, minor errors
5: Structurally identical to solution

REASONING QUALITY (0-5):
0: No evidence of understanding the puzzle
1: Identified some entities but wrong roles
2: Correct archetype but wrong strategy
3: Correct strategy, poor execution
4: Good strategy and mostly good execution
5: Excellent reasoning throughout

Respond in JSON: {"structural": N, "reasoning": N, "explanation": "..."}
```

### Step 3: Integration in runner.py

After puzzle loop exits, before result recording:

```python
if outcome_judge and expected_grid:
    trajectory_summary = self._build_trajectory_summary(orchestrator)
    verdict = await outcome_judge.evaluate(final_grid, expected_grid, trajectory_summary, archetype)
    if verdict:
        result_payload["judge_verdict"] = asdict(verdict)
```

### Step 4: ABTaskResult extension (ab_harness.py)

Add `judge_verdict: Optional[dict] = None` field.

### Step 5: Tests

Create `tests/test_b181_outcome_judge.py`:
1. Test 100% cell match → partial_match_score = 5
2. Test 0% cell match → partial_match_score = 0
3. Test 50% cell match → partial_match_score = 2
4. Test missing expected_grid → returns None (no crash)
5. Test LLM parse failure → falls back to partial score only
6. Test rubric prompt includes required dimensions

## Verification

```bash
pytest tests/test_b181_outcome_judge.py -v
```
