import json
from unittest.mock import MagicMock

from agents.arc3.failure_taxonomy import FailureTaxonomy, classify_failure
from agents.arc3.runner import DurableARCRunner
from agents.arc3.phase import SolvePhase
from benchmarks.arc3.adapter import NoOpBrainClient


def _make_runner() -> DurableARCRunner:
    harness = MagicMock()
    harness.mock_api = True
    return DurableARCRunner(harness, NoOpBrainClient(), config={"llm": {"model": "test"}})


def test_classify_failure_timeout():
    result = classify_failure(TimeoutError("LLM request timed out after 30s"))
    assert result is FailureTaxonomy.LLM_TIMEOUT


def test_classify_failure_mcp_tool_timeout():
    from sidequest_mcp_client.mcp_session import MCPTimeoutError
    exc = MCPTimeoutError("timeout waiting for response to abc during tools/call:current_truth")
    result = classify_failure(exc)
    assert result is FailureTaxonomy.TOOL_TIMEOUT


def test_classify_failure_mcp_general_timeout():
    from sidequest_mcp_client.mcp_session import MCPTimeoutError
    exc = MCPTimeoutError("timeout waiting for response during initialize")
    result = classify_failure(exc)
    assert result is FailureTaxonomy.TOOL_TIMEOUT


def test_classify_failure_parse_error():
    err = json.JSONDecodeError("Expecting value", "{", 0)
    result = classify_failure(err)
    assert result is FailureTaxonomy.LLM_PARSE_ERROR


def test_classify_failure_api_error():
    result = classify_failure(RuntimeError("400 Bad Request from ARC API"))
    assert result is FailureTaxonomy.API_ERROR


def test_classify_failure_budget_exceeded():
    result = classify_failure(error_message="Budget exhausted ($0.02)", budget_exhausted=True)
    assert result is FailureTaxonomy.BUDGET_EXCEEDED


def test_classify_failure_stuck_in_loop():
    result = classify_failure(
        error_message="Max attempts reached across all retries",
        final_state="NOT_FINISHED",
        max_steps_reached=True,
        no_progress_steps=20,
        loop_detected=True,
    )
    assert result is FailureTaxonomy.STUCK_IN_LOOP


def test_classify_failure_max_steps_reached():
    result = classify_failure(
        error_message="Max attempts reached across all retries",
        final_state="NOT_FINISHED",
        max_steps_reached=True,
        no_progress_steps=3,
    )
    assert result is FailureTaxonomy.MAX_STEPS_REACHED


def test_classify_failure_crash_fallback():
    result = classify_failure(RuntimeError("boom"))
    assert result is FailureTaxonomy.CRASH


def test_classify_failure_strategy_exhausted_without_exception():
    result = classify_failure(exc=None, final_state="GAME_OVER")
    assert result is FailureTaxonomy.STRATEGY_EXHAUSTED


def test_classify_failure_coverage_saturated_abort():
    # Signal from A010 graduation gate
    result = classify_failure(
        exc=None,
        final_state="GAME_OVER",
        graduation_reason="coverage_saturated_high_confidence",
        coverage_saturated=True
    )
    assert result is FailureTaxonomy.COVERAGE_SATURATED_ABORT


def test_submission_row_includes_failure_class():
    runner = _make_runner()
    row = runner._submission_row_from_result(
        {
            "task_id": "task-1",
            "game_id": "game-1",
            "correct": False,
            "steps": 10,
            "tokens_input": 1,
            "tokens_output": 2,
            "runtime_seconds": 1.2,
            "final_state": "NOT_FINISHED",
            "error_message": "Max attempts reached across all retries",
            "failure_class": FailureTaxonomy.MAX_STEPS_REACHED.value,
        }
    )

    assert row["failure_class"] == FailureTaxonomy.MAX_STEPS_REACHED.value
    assert row["metadata"]["failure_class"] == FailureTaxonomy.MAX_STEPS_REACHED.value


def test_replan_target_escalates_when_signature_repeats():
    runner = _make_runner()
    orchestrator = MagicMock()
    orchestrator._hypothesis_context = {
        "action_coverage": {"initial_exploration_complete": True},
        "loop_detected": True,
    }
    orchestrator._solve_context = {
        "active_chunk": {"source": "plateau_exploitation"},
        "plateau_locked_family": "ACTION6",
        "archetype": "space",
        "archetype_confidence": 0.57,
        "victory_condition": {"type": "unknown", "confidence": 0.1},
    }
    orchestrator.solve_engine = MagicMock()
    orchestrator.solve_engine._archetype_confidence = 0.57
    orchestrator._emit_trace_event = MagicMock()

    first = runner._replan_target(orchestrator)
    second = runner._replan_target(orchestrator)

    assert first is SolvePhase.ROUTE
    assert second is SolvePhase.MODEL
    orchestrator._emit_trace_event.assert_called()


def test_replan_target_allows_route_when_signature_changes():
    runner = _make_runner()
    orchestrator = MagicMock()
    orchestrator._hypothesis_context = {
        "action_coverage": {"initial_exploration_complete": True},
        "loop_detected": True,
    }
    orchestrator._solve_context = {
        "active_chunk": {"source": "plateau_exploitation"},
        "plateau_locked_family": "ACTION6",
        "archetype": "space",
        "archetype_confidence": 0.57,
        "victory_condition": {"type": "unknown", "confidence": 0.1},
    }
    orchestrator.solve_engine = MagicMock()
    orchestrator.solve_engine._archetype_confidence = 0.57
    orchestrator._emit_trace_event = MagicMock()

    first = runner._replan_target(orchestrator)
    orchestrator._solve_context["plateau_locked_family"] = "ACTION4"
    second = runner._replan_target(orchestrator)

    assert first is SolvePhase.ROUTE
    assert second is SolvePhase.ROUTE
