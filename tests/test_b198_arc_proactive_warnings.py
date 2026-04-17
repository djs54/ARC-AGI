"""Tests for B198: ARC proactive warning integration.

Verifies parsing of `proactive_context` from `notify_turn`, injection into
hypothesis_context passed to SolveEngine, penalization in scoring, and trace
recording.
"""

from __future__ import annotations

import pytest
from types import SimpleNamespace

from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import SolveEngine, GameArchetype
from benchmarks.arc3.state_serializer import StateSerializerForARC
from benchmarks.arc3.adapter import NoOpBrainClient


class SpyBrain(NoOpBrainClient):
    def __init__(self, proactive=None):
        super().__init__()
        self._proactive = proactive

    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed=None):
        # Return a response shape that may include proactive_context
        resp = {"status": "ok"}
        if self._proactive is not None:
            resp["proactive_context"] = self._proactive
        return resp


@pytest.mark.asyncio
async def test_proactive_context_parsed_and_traced():
    proactive = [{
        "lesson_id": "l1",
        "text": "ACTION4 was ineffective in similar puzzles",
        "type": "warning",
        "domain": "movement",
        "relevance_score": 0.9,
    }]

    spy = SpyBrain(proactive=proactive)
    orchestrator = ARCOrchestrator(spy, llm_client=None, session_id="s1", serializer=StateSerializerForARC(), config={})

    obs = {"grid": [[0]], "colors": [{"value": 1, "count": 1}], "shapes": [], "state": "RUN", "task_id": "t1", "dataset_id": "d1"}
    # Call perceive which exercises a notify_turn path
    mc = await orchestrator.perceive(obs, step=0)

    # Ensure proactive warnings were stored
    assert hasattr(orchestrator, "_proactive_warnings")
    assert orchestrator._proactive_warnings == proactive

    # Ensure a trace event for proactive_warning was emitted
    assert any(e.get("operation") == "proactive_warning" for e in orchestrator._execution_trace)


@pytest.mark.asyncio
async def test_proactive_context_dict_shape_is_normalized():
    proactive = {
        "pushed": True,
        "items": [{
            "lesson_id": "l1",
            "text": "ACTION4 was ineffective in similar puzzles",
            "type": "warning",
            "domain": "movement",
            "relevance_score": 0.9,
        }],
    }

    spy = SpyBrain(proactive=proactive)
    orchestrator = ARCOrchestrator(spy, llm_client=None, session_id="s1b", serializer=StateSerializerForARC(), config={})

    obs = {"grid": [[0]], "colors": [{"value": 1, "count": 1}], "shapes": [], "state": "RUN", "task_id": "t1", "dataset_id": "d1"}
    await orchestrator.perceive(obs, step=0)

    assert orchestrator._proactive_warnings == proactive["items"]
    assert any(e.get("operation") == "proactive_warning" for e in orchestrator._execution_trace)


@pytest.mark.asyncio
async def test_hypothesis_context_includes_proactive_warnings():
    proactive = [{
        "lesson_id": "l2",
        "text": "Avoid ACTION1 in narrow corridors",
        "type": "warning",
        "domain": "navigation",
        "relevance_score": 0.6,
    }]

    spy = SpyBrain(proactive=None)
    orchestrator = ARCOrchestrator(spy, llm_client=None, session_id="s2", serializer=StateSerializerForARC(), config={})

    # Prime internal warnings directly (simulating earlier notify)
    orchestrator._proactive_warnings = proactive

    # Replace solve_engine.solve with a fake to capture hypothesis_context
    captured = {}

    async def fake_solve(observation, hypothesis_context, step, state_graph, current_state_hash, level_pattern, solved_levels):
        captured["ctx"] = hypothesis_context
        # Return a minimal SolveContext-like object
        return SimpleNamespace(
            archetype=GameArchetype.UNKNOWN,
            archetype_confidence=0.0,
            object_roles={},
            victory_condition=None,
            active_chunk=None,
            dissonance_detected=False,
            dissonance_reason="",
            strategy_summary="",
            chunk_ledger=[],
            plateau_mode=False,
            plateau_reason="",
            ranked_action_families=[],
            action_family_scores={},
        )

    orchestrator.solve_engine.solve = fake_solve  # type: ignore

    obs = {"grid": [[0]], "colors": [{"value": 1, "count": 1}], "shapes": [], "state": "RUN", "task_id": "t2", "dataset_id": "d2"}
    # Call solve which should inject proactive_warnings into hypothesis_context
    await orchestrator.solve(obs, hypothesis_context={"some": "ctx"}, step=0)

    assert "ctx" in captured
    hw = captured["ctx"].get("proactive_warnings")
    assert isinstance(hw, list)
    assert hw[0]["text"] == proactive[0]["text"]
    assert hw[0]["type"] == proactive[0]["type"]


def test_score_action_families_penalizes_warned_action():
    se = SolveEngine(brain_client=None, llm_client=None, session_id="s", emit_trace_event=None, cost_tracker=None)

    ctx = {"proactive_warnings": [{"text": "ACTION4 was ineffective", "type": "warning"}]}
    scores = se._score_action_families(ctx, ["ACTION1", "ACTION4"])

    # ACTION4 should be penalized to the minimum floor (0.05)
    assert scores.get("ACTION4") == 0.05
    assert scores.get("ACTION1") == 0.0


def test_score_action_families_ignores_hints():
    se = SolveEngine(brain_client=None, llm_client=None, session_id="s", emit_trace_event=None, cost_tracker=None)

    ctx = {"proactive_warnings": [{"text": "Consider ACTION1 as a hint", "type": "hint"}]}
    scores = se._score_action_families(ctx, ["ACTION1", "ACTION2"])

    # No penalties for 'hint' type
    assert scores.get("ACTION1") == 0.0
    assert scores.get("ACTION2") == 0.0
