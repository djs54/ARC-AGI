"""A157: per-step telemetry's reasoning_escalation_count must reflect the real
goal_resolver.py llm_escalated signal, not a hardcoded 0.

Live smoke evidence (game ar25-0c556536, 2026-08-05): the goal-ambiguity LLM
prompt ("Resolve the ARC goal ambiguity...") fired on all 10 steps of a real
run (confirmed via raw log grep), but every step's reasoning_escalation_count
in the live.jsonl artifact was 0.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import GoalHypothesis, ResolvedGoal


def _telemetry() -> ArcV2Telemetry:
    return ArcV2Telemetry(task_id="t1", game_id="g1", game_title="G1")


def _goal(metadata: dict) -> ResolvedGoal:
    return ResolvedGoal(
        selected=GoalHypothesis(goal_id="block-1", description="", confidence=0.5, evidence=(), metadata={}),
        alternatives=(),
        grounding_gate_passed=True,
        metadata=metadata,
    )


def test_llm_escalated_true_reports_escalation_count_one():
    telemetry = _telemetry()
    telemetry._latest_goal = _goal({"llm_escalated": True})

    snapshot = telemetry._step_snapshot(())

    assert snapshot["reasoning_escalation_count"] == 1


def test_llm_escalated_false_reports_escalation_count_zero():
    telemetry = _telemetry()
    telemetry._latest_goal = _goal({"llm_escalated": False})

    snapshot = telemetry._step_snapshot(())

    assert snapshot["reasoning_escalation_count"] == 0


def test_llm_escalated_missing_reports_escalation_count_zero():
    telemetry = _telemetry()
    telemetry._latest_goal = _goal({})

    snapshot = telemetry._step_snapshot(())

    assert snapshot["reasoning_escalation_count"] == 0


def test_no_goal_reports_escalation_count_zero():
    telemetry = _telemetry()
    telemetry._latest_goal = None

    snapshot = telemetry._step_snapshot(())

    assert snapshot["reasoning_escalation_count"] == 0
