"""Tests for A169: the LLM never saw the actual grid, only a hash plus
abstracted blob statistics. This adds a compact text-grid encoding and
threads it into both LLM-escalation prompt-construction call sites."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.perceive import PerceiveAgent
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, ResolvedGoal, WorkflowState


class TestEncodeGridText:
    def test_encode_small_grid_exact_output(self):
        grid = [[0, 1, 2], [3, 4, 5], [6, 7, 8]]
        assert PerceiveAgent._encode_grid_text(grid) == "012\n345\n678"

    def test_encode_empty_grid_returns_empty_string(self):
        assert PerceiveAgent._encode_grid_text([]) == ""

    def test_encode_oversized_grid_returns_fallback_message(self):
        grid = [[0] * 100 for _ in range(100)]
        result = PerceiveAgent._encode_grid_text(grid, max_cells=4096)
        assert result == "grid omitted: 100x100 exceeds 4096-cell encoding limit"


class TestPerceptionSnapshotIncludesGridText:
    def test_perception_snapshot_metadata_includes_grid_text(self):
        agent = PerceiveAgent()
        state = WorkflowState()
        observation = {"grid": [[1, 2], [3, 4]]}

        result = agent.perceive(state, observation)

        assert result.payload.metadata["grid_text"] == "12\n34"


class _RecordingLLMPort:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[list[Any]] = []

    def chat(self, messages):
        self.calls.append(list(messages))
        return self.response


class TestPlanGeneratorPromptIncludesGridText:
    def test_plan_generator_prompt_includes_grid_text(self):
        recorder = _RecordingLLMPort('{"action_id": "ACTION1", "reason": "x"}')
        planner = PlanGenerator(PlanGeneratorLimits())
        state = WorkflowState(
            step_index=0,
            action_attempt_counts={},
            action_falsification_counts={},
            consecutive_no_progress_count=0,
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1"]},
            grid_hash="hash-1",
            metadata={"grid_text": "01\n23"},
        )
        goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))

        planner.generate(state, perception, goal, llm_port=recorder)

        assert recorder.calls, "expected the LLM to be escalated to"
        user_message = recorder.calls[0][1]
        payload = json.loads(user_message.content)
        assert payload["grid_text"] == "01\n23"


class TestGoalResolverPromptIncludesGridText:
    def test_goal_resolver_prompt_includes_grid_text(self):
        recorder = _RecordingLLMPort("{}")
        resolver = GoalResolver(GoalResolverLimits(ambiguity_gap=0.12, low_confidence_threshold=0.7, llm_patience_steps=2))
        # A243: _should_escalate_to_llm's under_confident branch now reads
        # state.goal_failure_counts[<goal_id>] instead of the flat
        # state.consecutive_no_progress_count -- this test only needs SOME
        # escalation trigger to exercise the grid_text prompt-threading it
        # actually cares about, so it now seeds the failure count for the
        # exact goal_id this perception's tier-one fallback hypothesis
        # produces (no entities + grid_shape=(2, 2) -> "grid-2x2", see
        # goal_resolver.py::_tier_one_hypotheses's fallback branch).
        state = WorkflowState(goal_failure_counts={"grid-2x2": 2})
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            grid_shape=(2, 2),
            metadata={"grid_text": "01\n23"},
        )

        resolver.resolve(state, perception, llm_port=recorder)

        assert recorder.calls, "expected the LLM to be escalated to"
        user_message = recorder.calls[0][1]
        payload = json.loads(user_message.content)
        assert payload["grid_text"] == "01\n23"
