"""A156: per-step telemetry must not overload `failure_class` with WorkflowDecision
labels ("pivot"/"terminate") — that field is reserved for terminal
agents.common.failure_taxonomy.FailureTaxonomy classification.

Live smoke evidence (game tu93-0768757b, 2026-08-05): steps 5-8, mid-run and
non-terminal (the run continued to step 8 of a 10-step budget), all logged
`failure_class: "pivot"` — neither "pivot" nor "terminate" is a FailureTaxonomy
member, and the run hadn't actually failed/terminated at those steps.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import EvaluationResult, WorkflowDecision, WorkflowRunResult, WorkflowState, WorkflowStatus
from agents.common.failure_taxonomy import FailureTaxonomy


def _telemetry() -> ArcV2Telemetry:
    return ArcV2Telemetry(task_id="t1", game_id="g1", game_title="G1")


def _evaluation(decision: WorkflowDecision, reason: str = "repeated_falsification") -> EvaluationResult:
    return EvaluationResult(
        decision=decision,
        meaningful_progress=False,
        falsification_delta=1,
        reason=reason,
        metadata={},
    )


def test_step_snapshot_pivot_decision_does_not_set_failure_class():
    telemetry = _telemetry()
    telemetry._latest_evaluation = _evaluation(WorkflowDecision.PIVOT)

    snapshot = telemetry._step_snapshot(())

    assert snapshot.get("decision") == "pivot"
    assert "failure_class" not in snapshot


def test_step_snapshot_terminate_decision_does_not_set_failure_class():
    telemetry = _telemetry()
    telemetry._latest_evaluation = _evaluation(WorkflowDecision.TERMINATE, reason="action_space_exhausted")

    snapshot = telemetry._step_snapshot(())

    assert snapshot.get("decision") == "terminate"
    assert "failure_class" not in snapshot


def test_step_snapshot_continue_decision_unaffected():
    telemetry = _telemetry()
    telemetry._latest_evaluation = _evaluation(WorkflowDecision.CONTINUE, reason="meaningful_progress")

    snapshot = telemetry._step_snapshot(())

    assert snapshot.get("decision") == "continue"
    assert "failure_class" not in snapshot


def test_final_result_failure_class_still_uses_taxonomy():
    """The final-result path (classify_v2_termination) is a separate code path
    from _step_snapshot and must be unaffected by this fix."""
    telemetry = _telemetry()
    workflow_result = WorkflowRunResult(
        status=WorkflowStatus.STALLED,
        state=WorkflowState(step_index=8),
        reason="stall_detected",
        completed_cycles=8,
        traceback=None,
    )

    final_result = telemetry.build_final_result(workflow_result)

    assert final_result["failure_class"] == FailureTaxonomy.STRATEGY_EXHAUSTED.value
    assert final_result["failure_class"] not in ("pivot", "terminate")
