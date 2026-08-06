import json
import pytest
from unittest.mock import MagicMock

from agents.arc3.repl_verification import LevelReplayVerifier, RuleRefinementLoop
from agents.arc3.solver import GameRuleHypothesis


@pytest.mark.asyncio
async def test_verify_hypothesis_success():
    """B152: A simple action semantics that moves a pixel to the right should verify."""
    hypothesis = GameRuleHypothesis(
        rule_description="move-right",
        action_semantics={"ACTION1": "move right"},
        objective_description="reach right",
        level_strategy="paint",
        confidence=0.5,
        evidence=[],
        contradictions=[],
        source="test",
    )

    solved_levels = [
        {
            "level": 1,
            "actions": ["ACTION1"],
            "steps": 1,
            "start_grid": [[1, 0]],
            "end_grid": [[0, 1]],
        }
    ]

    verifier = LevelReplayVerifier()
    res = await verifier.verify_hypothesis(hypothesis, solved_levels)

    assert res.total == 1
    assert res.matches == 1
    assert res.verified is True


@pytest.mark.asyncio
async def test_rule_refinement_loop_boosts_confidence_on_verify():
    """Verified hypotheses should receive a confidence boost via the refinement loop."""
    hypothesis = GameRuleHypothesis(
        rule_description="move-right",
        action_semantics={"ACTION1": "move right"},
        objective_description="reach right",
        level_strategy="paint",
        confidence=0.4,
        evidence=[],
        contradictions=[],
        source="test",
    )

    solved_levels = [
        {
            "level": 1,
            "actions": ["ACTION1"],
            "steps": 1,
            "start_grid": [[1, 0]],
            "end_grid": [[0, 1]],
        }
    ]

    verifier = LevelReplayVerifier()
    loop = RuleRefinementLoop(llm_client=None, verifier=verifier)

    best = await loop.solve([hypothesis], solved_levels)

    # Confidence should increase by at least the boost amount (0.3 for 1/1 match)
    assert best.confidence >= 0.7


@pytest.mark.asyncio
async def test_verify_hypothesis_handles_repl_errors():
    """If the REPL executor returns a non-zero exit code, the verifier should record a mismatch."""

    def failing_executor(code, timeout: float = 2.0):
        return {"stdout": "", "stderr": "SyntaxError: invalid", "exit_code": 1, "timeout": False}

    hypothesis = GameRuleHypothesis(
        rule_description="broken",
        action_semantics={"ACTION1": "move right"},
        objective_description="none",
        level_strategy="none",
        confidence=0.1,
        evidence=[],
        contradictions=[],
        source="test",
    )

    solved_levels = [
        {
            "level": 1,
            "actions": ["ACTION1"],
            "steps": 1,
            "start_grid": [[1, 0]],
            "end_grid": [[0, 1]],
        }
    ]

    verifier = LevelReplayVerifier(repl_executor=failing_executor)
    res = await verifier.verify_hypothesis(hypothesis, solved_levels)

    assert res.total == 1
    assert res.matches == 0
    assert len(res.mismatches) == 1
    assert res.verified is False
