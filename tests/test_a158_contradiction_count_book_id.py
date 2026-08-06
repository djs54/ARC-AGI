"""A158: world_model_contradiction_count must look up the same key the
bookkeeping system actually uses (book_id for coordinate-targeted ACTION6
clicks), not the bare action_id, which never matches for ACTION6.

Live smoke evidence (game sp80-589a99af, 2026-08-05): every step's
world_model_contradiction_count was 0, including step 10 (ACTION6@22,0)
despite the run having accumulated 10 falsifications by then. Root cause:
telemetry.py looked up state.action_falsification_counts using the base
action_id ("ACTION6"), but that dict is keyed by book_id ("ACTION6@22,0")
for click actions -- the lookup could never match.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import ExecutionResult, PlanCandidate, WorkflowState


def _telemetry() -> ArcV2Telemetry:
    return ArcV2Telemetry(task_id="t1", game_id="g1", game_title="G1")


def _execution(action_id: str, candidate_metadata: dict) -> ExecutionResult:
    candidate = PlanCandidate(action_id=action_id, metadata=candidate_metadata)
    return ExecutionResult(action_id=action_id, candidate=candidate, observation={})


def test_action6_book_id_falsification_count_now_visible():
    telemetry = _telemetry()
    telemetry._latest_execution = _execution("ACTION6", {"book_id": "ACTION6@22,0"})
    state = WorkflowState(action_falsification_counts={"ACTION6@22,0": 3})

    snapshot = telemetry._step_snapshot((state,))

    assert snapshot["world_model_contradiction_count"] == 3


def test_non_action6_bare_action_id_unaffected():
    telemetry = _telemetry()
    telemetry._latest_execution = _execution("ACTION1", {})
    state = WorkflowState(action_falsification_counts={"ACTION1": 2})

    snapshot = telemetry._step_snapshot((state,))

    assert snapshot["world_model_contradiction_count"] == 2


def test_missing_execution_defaults_to_zero_no_crash():
    telemetry = _telemetry()
    telemetry._latest_execution = None
    state = WorkflowState(action_falsification_counts={"ACTION1": 5})

    snapshot = telemetry._step_snapshot((state,))

    assert snapshot["world_model_contradiction_count"] == 0


def test_candidate_without_book_id_metadata_falls_back_to_action_id():
    telemetry = _telemetry()
    telemetry._latest_execution = _execution("ACTION2", {"something_else": True})
    state = WorkflowState(action_falsification_counts={"ACTION2": 1})

    snapshot = telemetry._step_snapshot((state,))

    assert snapshot["world_model_contradiction_count"] == 1
