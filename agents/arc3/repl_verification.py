"""
agents/arc3/repl_verification.py — Level-Replay Verification (B152)

Provides mechanisms to verify game rule hypotheses by replaying action
sequences against solved level start/end pairs in a restricted sandbox.
"""

from __future__ import annotations
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agents.arc3.solver import GameRuleHypothesis
    from agents.arc3.grid_analysis import LevelPattern

from agents.arc3.repl_sandbox import execute_repl

logger = logging.getLogger(__name__)

@dataclass
class MismatchDetail:
    level_index: int
    start_grid: List[List[int]]
    expected_end_grid: List[List[int]]
    actual_end_grid: Optional[List[List[int]]]
    action_sequence: List[str]
    diff_summary: str  # compact text summary of what's wrong
    error: Optional[str]  # Python error if simulation crashed

@dataclass
class VerificationResult:
    matches: int
    total: int
    mismatches: List[MismatchDetail]
    verified: bool  # matches == total
    confidence_boost: float
    execution_time_ms: float

class LevelReplayVerifier:
    """Verify a game rule hypothesis by replaying solved levels."""

    def __init__(self, repl_executor=None):
        self._execute = repl_executor or execute_repl

    async def verify_hypothesis(
        self,
        hypothesis: GameRuleHypothesis,
        solved_levels: List[Dict[str, Any]],
    ) -> VerificationResult:
        """Execute hypothesis logic in REPL against solved level pairs."""
        matches = 0
        mismatches = []
        total = len(solved_levels)
        start_time = asyncio.get_event_loop().time()

        for i, level in enumerate(solved_levels):
            start_grid = level["start_grid"]
            expected_end = level["end_grid"]
            actions = level["actions"]

            # Build script that replays the recorded actions using hypothesis semantics
            script = self._build_replay_script(hypothesis, start_grid, actions)

            try:
                result = await asyncio.to_thread(self._execute, script)
                
                if result.get("timeout"):
                    raise TimeoutError(f"REPL timed out at level {i+1}")
                
                if result.get("exit_code") != 0:
                    raise RuntimeError(result.get("stderr", "Unknown error"))

                actual_end = self._parse_grid_output(result.get("stdout", ""))

                if actual_end == expected_end:
                    matches += 1
                else:
                    diff = self._compute_mismatch_diff(expected_end, actual_end)
                    mismatches.append(MismatchDetail(
                        level_index=i,
                        start_grid=start_grid,
                        expected_end_grid=expected_end,
                        actual_end_grid=actual_end,
                        action_sequence=actions,
                        diff_summary=diff,
                        error=None,
                    ))
            except Exception as exc:
                mismatches.append(MismatchDetail(
                    level_index=i,
                    start_grid=start_grid,
                    expected_end_grid=expected_end,
                    actual_end_grid=None,
                    action_sequence=actions,
                    diff_summary="",
                    error=str(exc),
                ))

        end_time = asyncio.get_event_loop().time()
        
        # Boost confidence if many levels match
        boost = 0.0
        if total > 0:
            boost = (matches / total) * 0.3 # Max 30% boost

        return VerificationResult(
            matches=matches,
            total=total,
            mismatches=mismatches,
            verified=(matches == total and total > 0),
            confidence_boost=boost,
            execution_time_ms=(end_time - start_time) * 1000,
        )

    def _build_replay_script(self, hypothesis: GameRuleHypothesis, start_grid, actions) -> str:
        """Build simulation script mapping hypothesis semantics to behavior."""
        semantics_json = json.dumps(hypothesis.action_semantics)
        
        return f"""
import json

def simulate(grid, actions, semantics):
    rows, cols = len(grid), len(grid[0])
    curr = [row[:] for row in grid]
    
    # Simple simulation logic based on hypothesis-provided semantics only
    for action in actions:
        sem = (semantics.get(action, "") or "").lower()

        # Directional movement inferred from semantics text
        dr, dc = 0, 0
        if "up" in sem:
            dr = -1
        elif "down" in sem:
            dr = 1
        if "left" in sem:
            dc = -1
        elif "right" in sem:
            dc = 1

        if dr != 0 or dc != 0:
            # Find a single non-background pixel and move it
            moved = False
            for r in range(rows):
                for c in range(cols):
                    if curr[r][c] != 0:
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < rows and 0 <= nc < cols:
                            color = curr[r][c]
                            curr[r][c] = 0
                            curr[nr][nc] = color
                            moved = True
                            break
                if moved:
                    break
        else:
            # Placeholder for other semantics like toggle/interact
            pass

    return curr

grid = {json.dumps(start_grid)}
actions = {json.dumps(actions)}
semantics = {semantics_json}

try:
    final = simulate(grid, actions, semantics)
    print(json.dumps(final))
except Exception as e:
    import sys
    print(str(e), file=sys.stderr)
    sys.exit(1)
"""

    def _parse_grid_output(self, repl_output: str) -> List[List[int]]:
        text = repl_output.strip()
        if not text: return []
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"(\[.*\])", text, re.DOTALL)
            if match: return json.loads(match.group(1))
            raise

    def _compute_mismatch_diff(self, expected, actual) -> str:
        if actual is None: return "No output"
        if len(expected) != len(actual): return "Size mismatch"
        diffs = []
        for r in range(len(expected)):
            for c in range(len(expected[0])):
                if expected[r][c] != actual[r][c]:
                    diffs.append(f"({r},{c})")
        return f"{len(diffs)} cells differ: " + ", ".join(diffs[:3])

class RuleRefinementLoop:
    """Orchestrates hypothesize -> verify -> refine cycle."""

    MAX_REFINEMENT_ROUNDS = 2

    def __init__(self, llm_client, verifier: LevelReplayVerifier):
        self.llm = llm_client
        self.verifier = verifier

    async def solve(
        self,
        hypotheses: List[GameRuleHypothesis],
        solved_levels: List[Dict[str, Any]],
    ) -> GameRuleHypothesis:
        """Verify and refine hypotheses, returning the best one."""
        if not hypotheses:
            return None

        best_h = hypotheses[0]
        
        for h in hypotheses:
            refined = await self._verify_and_refine(h, solved_levels)
            if refined.confidence > best_h.confidence:
                best_h = refined
                
        return best_h

    async def _verify_and_refine(
        self,
        hypothesis: GameRuleHypothesis,
        solved_levels: List[Dict[str, Any]],
    ) -> GameRuleHypothesis:
        current = hypothesis
        
        for _ in range(self.MAX_REFINEMENT_ROUNDS):
            res = await self.verifier.verify_hypothesis(current, solved_levels)
            if res.verified:
                current.confidence += res.confidence_boost
                current.evidence.append(f"Verified against {res.total} solved levels")
                return current
            
            if not res.mismatches:
                break
                
            # Refine via LLM (logic similar to B151 but with mismatch evidence)
            # Placeholder for actual LLM refinement call
            break 
            
        return current
