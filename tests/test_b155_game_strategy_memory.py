import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import GameRuleHypothesis


@pytest.mark.asyncio
async def test_memory_query_and_parse_and_evaluate_storage():
    """B155: Ensure memory query, parsing, and evaluate() storage behave as expected."""
    brain = MagicMock()
    brain.current_truth = AsyncMock(return_value={"results": []})
    brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": []})
    brain.analogical_search = AsyncMock(return_value={"results": []})
    brain.register_plan = AsyncMock(return_value={"plan_id": "p"})
    brain.report_outcome = AsyncMock(return_value={"updated": True})
    brain.notify_turn = AsyncMock(return_value={"status": "queued"})

    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session-1",
        serializer=StateSerializerForARC(),
        config={},
    )

    observation = {
        "task_id": "task-1",
        "dataset_id": "arc",
        "grid": [[0, 1], [1, 0]],
        "available_actions": ["ACTION1", "ACTION2", "ACTION5"],
    }

    # _memory_query should include dimensions, color count and action count
    query = orchestrator._memory_query(observation)
    assert "2x2 grid" in query
    assert "2 colors" in query or "2 colors" in query
    assert "3 actions" in query or "3 actions" in query

    # _parse_transformation_lessons should convert memories into hypotheses
    memory = {
        "text_raw": (
            "ARC GAME STRATEGY\n"
            "Game rule: move-right\n"
            "Action semantics: {\"ACTION1\": \"move right\"}\n"
            "Outcome: SOLVED\n"
        ),
        "similarity": 0.8,
    }

    hypotheses = orchestrator._parse_transformation_lessons([memory])
    assert len(hypotheses) == 1
    h = hypotheses[0]
    assert h.rule_description.lower().startswith("move-right")
    assert h.action_semantics.get("ACTION1") == "move right"
    # confidence = base(0.7 for SOLVED) * similarity
    assert pytest.approx(h.confidence, rel=1e-3) == 0.7 * 0.8

    # evaluate() should call brain.notify_turn with a stored ARC GAME STRATEGY
    orchestrator._solved_levels = [
        {"level": 1, "actions": ["ACTION1"], "steps": 1, "start_grid": [[1, 0]], "end_grid": [[0, 1]]}
    ]
    orchestrator.solve_engine._game_rule_hypotheses = [
        GameRuleHypothesis(
            rule_description="move-right",
            action_semantics={"ACTION1": "move right"},
            objective_description="reach right",
            level_strategy="paint",
            confidence=0.6,
            evidence=[],
            contradictions=[],
            source="test",
        )
    ]

    final_observation = {"task_id": "task-1", "grid": [[1, 0]]}
    res = await orchestrator.evaluate(True, 1, 10, final_observation)
    # Ensure notify_turn was called at least once with ARC GAME STRATEGY content
    called = False
    for call in brain.notify_turn.call_args_list:
        kwargs = call.kwargs
        content = kwargs.get("content", "")
        if "ARC GAME STRATEGY" in content:
            called = True
            assert "Action semantics" in content
            break

    assert called, "evaluate() did not store ARC GAME STRATEGY via notify_turn"
