from __future__ import annotations
import json
import logging
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class JudgeVerdict:
    structural_score: int      # 0-5: right dimensions, color palette, structure
    partial_match_score: int   # 0-5: fraction of cells matching solution
    reasoning_score: int       # 0-5: trajectory narrative quality
    composite_score: float     # weighted average
    explanation: str           # judge's reasoning

class OutcomeJudge:
    """B181: LLM-as-Judge for near-miss grading of ARC puzzles."""

    RUBRIC_PROMPT = """You are evaluating an ARC puzzle agent's performance. 
Final Grid produced by agent:
{actual_grid}

Expected Solution:
{expected_grid}

Agent's Reasoning Trajectory:
{trajectory}

Game Archetype: {archetype}

Score the performance on two dimensions (0-5 each) using this rubric:

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

Respond ONLY in JSON format: {{"structural": N, "reasoning": N, "explanation": "..."}}
"""

    def __init__(self, llm_client: Any, model_name: Optional[str] = None):
        self.llm = llm_client
        self.model = model_name

    async def evaluate(
        self,
        final_grid: List[List[int]],
        expected_grid: Optional[List[List[int]]],
        trajectory_summary: str,
        archetype: str,
    ) -> Optional[JudgeVerdict]:
        """Grade the outcome on a 0-5 scale across 3 dimensions."""
        if expected_grid is None:
            return None

        # 1. Algorithmic Partial Match (0-5)
        cell_match_pct = self._compute_cell_match(final_grid, expected_grid)
        partial_score = min(5, int((cell_match_pct / 100.0) * 5))

        # 2. LLM Judge for Structural and Reasoning
        prompt = self.RUBRIC_PROMPT.format(
            actual_grid=self._format_grid(final_grid),
            expected_grid=self._format_grid(expected_grid),
            trajectory=trajectory_summary,
            archetype=archetype
        )

        try:
            # Use achat if available, else sync chat
            if hasattr(self.llm, "achat"):
                response = await self.llm.achat([{"role": "user", "content": prompt}])
            else:
                import asyncio
                response = await asyncio.to_thread(self.llm.chat, [{"role": "user", "content": prompt}])
            
            structural_score, reasoning_score, explanation = self._parse_response(response)
        except Exception as exc:
            logger.warning("B181: Outcome judge LLM failed: %s", exc)
            # Fallback to algorithmic score if LLM fails
            structural_score = partial_score
            reasoning_score = 0
            explanation = f"Judge failed: {exc}"

        return JudgeVerdict(
            structural_score=structural_score,
            partial_match_score=partial_score,
            reasoning_score=reasoning_score,
            composite_score=round((structural_score + partial_score + reasoning_score) / 3.0, 2),
            explanation=explanation
        )

    def _compute_cell_match(self, actual: List[List[int]], expected: List[List[int]]) -> float:
        """Percentage of cells matching between two grids of same dimensions."""
        if not actual or not expected:
            return 0.0
        if len(actual) != len(expected) or len(actual[0]) != len(expected[0]):
            return 0.0
        
        matches = 0
        total = len(expected) * len(expected[0])
        for r in range(len(expected)):
            for c in range(len(expected[0])):
                if actual[r][c] == expected[r][c]:
                    matches += 1
        return (matches / total) * 100.0

    def _format_grid(self, grid: List[List[int]]) -> str:
        """Compact string representation of grid."""
        return "\n".join([" ".join(map(str, row)) for row in grid])

    def _parse_response(self, response: str) -> tuple[int, int, str]:
        """Extract scores from LLM JSON response."""
        text = response.strip()
        # Handle markdown fences
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        
        try:
            data = json.loads(text.strip())
            return (
                min(5, max(0, int(data.get("structural", 0)))),
                min(5, max(0, int(data.get("reasoning", 0)))),
                data.get("explanation", "No explanation provided.")
            )
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("B181: Failed to parse judge response: %s", exc)
            raise ValueError(f"Unparseable judge response: {text[:100]}")
