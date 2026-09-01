"""Tests for A232: `record_evaluation`'s `record_reward_prediction_error` call
always sent a hardcoded `predicted_reward=1.0` and derived `actual_reward`
from whole-puzzle `meaningful_progress` -- since `predicted_reward` never
varied, `error = actual - predicted` could only ever be `0.0` (no-op) or
`-1.0` (falsification); the confidence-boosting branch of B278's
`arc_record_reward_prediction_error` (`error > 0.3`) was mathematically
unreachable. Confirmed live: `falsified_count == attempts` for every action
checked, even ones with real, unfalsified Rule-graph evidence at confidence
up to 1.0 for the exact same action at the exact same moment. This call is
removed entirely; `record_action_effect` (which never touches
confidence/falsified_count server-side) is unaffected. See backlog/A232.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.graph_queries import ARC_V2_TOOL_NAMES, ArcGraphQueryPort
from agents.arc4.types import EvaluationResult, WorkflowDecision


class _StubBrainClient:
    def __init__(self, result: dict[str, Any] | None = None) -> None:
        self.result = result if result is not None else {"status": "ok"}
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((tool_name, payload))
        return self.result


def _evaluation(*, meaningful_progress: bool, action_id: str = "ACTION6", step: int = 4) -> EvaluationResult:
    return EvaluationResult(
        decision=WorkflowDecision.CONTINUE,
        meaningful_progress=meaningful_progress,
        falsification_delta=0 if meaningful_progress else 1,
        metadata={
            "action_id": action_id,
            "step": step,
            "effect_match": meaningful_progress,
            "predicted_kind": "grid_change",
            "observed_kind": "grid_change" if meaningful_progress else "no_change",
        },
    )


class TestRecordEvaluationNoLongerSendsRewardPredictionError:
    def test_no_progress_step_never_calls_record_reward_prediction_error(self):
        """A232: the overwhelming-majority case (exploratory, no whole-puzzle
        progress) used to falsify the ActionFact purely for lacking progress,
        not for having a disproven effect. That call must simply not happen."""
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)

        port.record_evaluation(_evaluation(meaningful_progress=False))

        tool_names_called = [name for name, _payload in stub.calls]
        assert ARC_V2_TOOL_NAMES["record_reward_prediction_error"] not in tool_names_called
        assert "record_reward_prediction_error" not in tool_names_called

    def test_progress_step_also_never_calls_record_reward_prediction_error(self):
        """Even the (rare, no-op-in-the-old-logic) progress case must not send
        the call -- it's removed entirely, not conditionally skipped."""
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)

        port.record_evaluation(_evaluation(meaningful_progress=True))

        tool_names_called = [name for name, _payload in stub.calls]
        assert ARC_V2_TOOL_NAMES["record_reward_prediction_error"] not in tool_names_called
        assert "record_reward_prediction_error" not in tool_names_called

    def test_record_action_effect_is_unaffected(self):
        """record_action_effect writes ActionEffect/bumps observation_count
        only (server-side confirmed: never touches confidence/falsified_count)
        -- its own call and payload must be unchanged by this fix."""
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)

        port.record_evaluation(_evaluation(meaningful_progress=False, action_id="ACTION3", step=7))

        matching = [
            (name, payload)
            for name, payload in stub.calls
            if name == ARC_V2_TOOL_NAMES["record_action_effect"]
        ]
        assert len(matching) == 1
        tool_name, payload = matching[0]
        assert tool_name == "arc_record_action_effect"
        assert payload["task_id"] == "task-1"
        assert payload["action_id"] == "ACTION3"
        assert payload["step"] == 7
        assert payload["effect"]["did_progress"] is False
        assert payload["effect"]["falsification_delta"] == 1

    def test_record_evaluation_still_reports_ok_status(self):
        """Removing one of two writes should not turn a real update into a
        'skipped' result -- record_action_effect alone is enough to report ok."""
        stub = _StubBrainClient()
        port = ArcGraphQueryPort(stub, task_id="task-1", session_id="session-1", strict=False)

        result = port.record_evaluation(_evaluation(meaningful_progress=False))

        assert result["status"] == "ok"
        assert result["tool"] == "record_evaluation"
