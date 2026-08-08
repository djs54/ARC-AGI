"""Tests for A177's graph-side wiring: causal rules as first-class graph
objects (PREDICTS/FALSIFIED_BY), replacing per-action tally counters.
Deterministic signature extraction is tested separately in
tests/test_a177_rule_extraction.py; this covers the write/read graph calls,
evaluator wiring, and the scoring consumer in plan_generator.py. See
backlog/A177.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.evaluator import EvaluationLimits, Evaluator
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import ExecutionResult, GoalHypothesis, PerceptionSnapshot, PlanCandidate, ResolvedGoal, WorkflowState


class _StubBrainClient:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result if result is not None else {"ok": True}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, payload))
        return self.result


def _execution(action_id: str = "ACTION6") -> ExecutionResult:
    return ExecutionResult(
        action_id=action_id,
        candidate=PlanCandidate(action_id=action_id, goal_id="goal-1"),
        observation={"grid": "new-hash"},
        did_progress=True,
        metadata={"step": 2},
    )


class TestRecordRuleEvidence:
    def test_sends_extracted_candidate_signatures(self):
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        grid_diff = {"changed_cells": [{"row": 0, "col": 0, "from": 2, "to": 5}], "changed_count": 1, "truncated": False}

        result = port.record_rule_evidence(_execution(), grid_diff)

        assert result["ok"] is True
        tool_name, payload = stub.calls[0]
        assert tool_name == "record_rule"
        assert payload["action_id"] == "ACTION6"
        assert payload["candidate_signatures"] == [{"action_family": "ACTION6", "from_color": 2, "to_color": 5}]

    def test_no_changes_skips_the_call(self):
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        result = port.record_rule_evidence(_execution(), {"changed_cells": [], "changed_count": 0, "truncated": False})
        assert result == {"status": "no_changes", "recorded": False}
        assert stub.calls == []


class TestFetchRulesForAction:
    def test_returns_live_rules(self):
        stub = _StubBrainClient(result={"rules": [{"rule_id": "r1", "from_color": 2, "to_color": 5, "confidence": 0.6, "falsified": False}]})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        rules = port.fetch_rules_for_action("ACTION6")
        assert len(rules) == 1
        assert rules[0]["confidence"] == 0.6
        assert rules[0]["falsified"] is False

    def test_capability_missing_returns_empty_list(self):
        stub = _StubBrainClient(result={"status": "capability_missing"})
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)
        assert port.fetch_rules_for_action("ACTION6") == []


class TestEvaluatorWiresRuleRecording:
    def test_calls_record_rule_evidence_when_diff_present(self):
        calls: list[Any] = []

        class _RecordingGraphPort:
            def record_rule_evidence(self, execution, grid_diff):
                calls.append((execution.action_id, grid_diff))
                return {"ok": True}

        perception = PerceptionSnapshot(
            observation={"grid": "hash-1"},
            grid_hash="hash-1",
            metadata={"grid_diff": {"changed_cells": [{"row": 0, "col": 0, "from": 1, "to": 2}], "changed_count": 1, "truncated": False}},
        )
        goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))
        evaluator = Evaluator(graph_query_port=_RecordingGraphPort(), limits=EvaluationLimits())
        state = WorkflowState(step_index=0, action_attempt_counts={}, action_falsification_counts={}, consecutive_no_progress_count=0)

        result = evaluator.evaluate(state, perception, goal, _execution())

        assert len(calls) == 1
        assert result.payload.metadata["rule_recording"] == "ok"


class TestPlanGeneratorConsumesRuleEvidence:
    def test_candidate_with_live_rule_outranks_candidate_without(self):
        class _RuleAwareGraphPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_per_action_evidence(self, action_id):
                return {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0}

            def fetch_untested_actions(self):
                return []

            def fetch_rules_for_action(self, action_id):
                if action_id == "ACTION1":
                    return [{"rule_id": "r1", "from_color": 2, "to_color": 5, "confidence": 0.9, "falsified": False}]
                return []

        planner = PlanGenerator(PlanGeneratorLimits())
        state = WorkflowState(
            step_index=0,
            action_attempt_counts={},
            action_falsification_counts={},
            consecutive_no_progress_count=0,
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2"]},
            grid_hash="hash-1",
        )
        goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))

        result = planner.generate(state, perception, goal, graph_port=_RuleAwareGraphPort()).payload
        candidates_by_id = {c.action_id: c for c in [result.candidate, *result.alternatives]}

        assert candidates_by_id["ACTION1"].score > candidates_by_id["ACTION2"].score

    def test_falsified_rule_does_not_boost_score(self):
        class _FalsifiedRuleGraphPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_per_action_evidence(self, action_id):
                return {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0}

            def fetch_untested_actions(self):
                return []

            def fetch_rules_for_action(self, action_id):
                return [{"rule_id": "r1", "from_color": 2, "to_color": 5, "confidence": 0.9, "falsified": True}]

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
        )
        goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))

        result_with_falsified = planner.generate(state, perception, goal, graph_port=_FalsifiedRuleGraphPort()).payload

        state2 = WorkflowState(
            step_index=0,
            action_attempt_counts={},
            action_falsification_counts={},
            consecutive_no_progress_count=0,
        )
        result_without_rules = planner.generate(state2, perception, goal, graph_port=None).payload

        assert result_with_falsified.candidate.score == result_without_rules.candidate.score
