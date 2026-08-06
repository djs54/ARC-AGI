
import pytest
import time
import datetime
from unittest.mock import MagicMock, AsyncMock, patch
from agents.arc3.runner import DurableARCRunner
from benchmarks.ab_harness import ABTask, ABVariant
from benchmarks.arc3.adapter import NoOpBrainClient, LedgerBrainClient

@pytest.mark.asyncio
async def test_b130_logging_requirements():
    # Setup stub harness and runner
    harness = MagicMock()
    harness.mock_api = True
    harness.llm_client = AsyncMock()
    harness.serializer = MagicMock()
    harness.serializer._estimate_tokens.return_value = 10
    
    # B130: properly setup config to avoid MagicMock in numeric comparisons
    harness.config = MagicMock()
    harness.config.parameters = {"max_attempts_per_puzzle": 5}
    
    # Mock initial frame
    harness._get_mock_initial_frame.return_value = {
        "frame": [[0]], "guid": "g1", "state": "NOT_FINISHED", "available_actions": ["ACTION1"]
    }
    # Mock action to succeed then end
    harness._execute_mock_action.return_value = (
        {"frame": [[1]], "guid": "g2", "state": "WIN", "available_actions": []}, 1.0, True
    )

    runner = DurableARCRunner(harness, NoOpBrainClient(), config={
        "llm": {"model": "gpt-4o"},
        "max_retries_per_puzzle": 1,
        "max_steps_per_puzzle": 5,
        "cost": {"budget_per_puzzle_usd": 10.0}
    })
    task = ABTask(task_id="b130-test", category="test", prompt="p")
    setattr(task, "game_id", "game-b130")

    # Mock orchestrator behavior
    with patch("agents.arc3.runner.ARCOrchestrator") as mock_orch_cls:
        mock_orch = mock_orch_cls.return_value
        mock_orch.perceive = AsyncMock(return_value={})
        mock_orch.plan = AsyncMock(return_value={})
        mock_orch.hypothesize = AsyncMock(return_value={})
        mock_orch.solve = AsyncMock(return_value={})
        mock_orch.act = AsyncMock(return_value={"action_id": "ACTION1"})
        mock_orch.evaluate = AsyncMock(return_value={})
        mock_orch.hypothesis_mgr.distill_to_brain = AsyncMock()
        mock_orch.set_write_trace_context = MagicMock()
        mock_orch.consume_write_trace.return_value = []
        mock_orch._step_history = [{
            "step": 1,
            "action_id": "ACTION1",
            "solve_context": {"archetype": "test"},
            "timestamp_iso": "2026-04-02T12:00:00Z",
            "elapsed_mmss": "00:01"
        }]
        mock_orch.get_benchmark_metrics.return_value = {}
        mock_orch._plan_id = "p1"
        mock_orch._should_abandon = False
        
        # B130: avoid JSON serialization error by mocking CheckpointManager
        with patch("agents.arc3.runner.CheckpointManager") as mock_mgr_cls:
            mock_mgr = mock_mgr_cls.return_value
            mock_mgr.load_or_create.return_value = MagicMock(tasks={})
            
            # Run the puzzle
            results = await runner.run([task], "card-b130")
    
    assert len(results) == 1
    res = results[0]
    ledger = res["sidequests_ledger"]
    
    # 1. Verify arc_api_io presence and structure
    arc_calls = [e for e in ledger if e["call_type"] == "arc_api_action"]
    assert len(arc_calls) >= 2 # RESET and ACTION1
    
    for i, call in enumerate(arc_calls):
        assert "arc_api_io" in call
        io = call["arc_api_io"]
        assert "request" in io
        assert "response" in io
        assert "call_seq" in io
        # 2. call_seq monotonicity
        assert io["call_seq"] == i + 1
        
        # 3. Timestamp and elapsed formatting
        assert "timestamp_iso" in call
        assert "elapsed_mmss" in call
        assert "Z" in call["timestamp_iso"]
        assert ":" in call["elapsed_mmss"]

    # 4. Submission metadata Renaming
    assert "submission_metadata" in res
    meta = res["submission_metadata"]
    assert "created_at" in meta
    assert "submission_id" in meta
    assert meta["environment"]["arc_api_endpoint"] == "mock-harness"

    # 5. B130 Timeline Verification
    assert "arc_event_timeline" in res
    timeline = res["arc_event_timeline"]
    assert len(timeline) >= 4  # (RESET start/end) + (ACTION1 start/end)
    
    # Verify order and pairs
    for i in range(0, len(timeline), 2):
        req = timeline[i]
        resp = timeline[i+1]
        
        assert req["kind"] == "request_started"
        assert resp["kind"] == "response_received"
        assert req["call_seq"] == resp["call_seq"]
        assert req["event_seq"] < resp["event_seq"]
        assert "request_started_iso" in req
        assert "response_received_iso" in resp
        
    # Verify human-friendly labels for 1st, 2nd response
    assert timeline[1]["label"] == "INITIAL frame response #1"
    assert timeline[3]["label"] == "ACTION1 response #2"
    
    # Verify monotonic event_seq
    event_seqs = [e["event_seq"] for e in timeline]
    assert event_seqs == sorted(event_seqs)
    assert len(set(event_seqs)) == len(event_seqs)

    # Verify frame payloads are collapsed for readability
    response_payload = timeline[1]["payload"]
    assert "frame" not in response_payload
    assert response_payload["frame_summary"]["elided"] is True
    assert response_payload["frame_summary"]["dimensions"] == [1, 1]

    # Verify a unified chronological log is exported near the top-level view
    assert "chronological_log" in res
    chronological = res["chronological_log"]
    assert len(chronological) >= len(timeline)
    chrono_ts = [entry["timestamp_iso"] for entry in chronological if entry.get("timestamp_iso")]
    assert chrono_ts == sorted(chrono_ts)

    # Verify a dedicated ARC-only paired request/response export exists
    assert "arc_server_responses" in res
    arc_pairs = res["arc_server_responses"]
    assert len(arc_pairs) >= 2
    assert arc_pairs[0]["request"]["label"] == "INITIAL frame request started"
    assert arc_pairs[0]["response"]["label"] == "INITIAL frame response #1"
    assert arc_pairs[1]["response"]["label"] == "ACTION1 response #2"
    assert arc_pairs[0]["raw_response"]["payload"]["available_actions"] == ["ACTION1"]
    assert arc_pairs[0]["raw_response"]["payload"]["frame"] == [[0]]
    assert arc_pairs[1]["raw_request"]["payload"]["action_id"] == "ACTION1"

def test_b130_timeline_labels_show_first_second_third_responses_in_order():
    ledger = []
    brain = LedgerBrainClient(NoOpBrainClient(), ledger, lambda: 0, start_time=time.time() - 5)

    brain.record_arc_api_call(
        phase="bootstrap",
        method="GET",
        endpoint="/api/games/initial",
        request_payload={"game_id": "g1"},
        response_payload={"state": "NOT_FINISHED"},
        latency_ms=10,
    )
    brain.record_arc_api_call(
        phase="act",
        method="POST",
        endpoint="/api/cmd/ACTION1",
        request_payload={"action_id": "ACTION1"},
        response_payload={"state": "NOT_FINISHED"},
        latency_ms=12,
    )
    brain.record_arc_api_call(
        phase="act",
        method="POST",
        endpoint="/api/cmd/ACTION3",
        request_payload={"action_id": "ACTION3"},
        response_payload={"state": "WIN"},
        latency_ms=15,
    )

    timeline = brain.arc_event_timeline
    response_labels = [event["label"] for event in timeline if event["kind"] == "response_received"]

    assert response_labels == [
        "INITIAL frame response #1",
        "ACTION1 response #2",
        "ACTION3 response #3",
    ]


@pytest.mark.asyncio
async def test_b130_failure_path_logging():
    harness = MagicMock()
    harness.mock_api = False # trigger real session path
    harness._session = AsyncMock()
    harness.config = MagicMock()
    harness.config.parameters = {"max_attempts_per_puzzle": 5}
    
    # Mock /api/scorecard/open to succeed
    harness._session.post.side_effect = [
        MagicMock(json=lambda: {"card_id": "c1"}, raise_for_status=lambda: None), # scorecard/open
        RuntimeError("API Timeout") # RESET fails
    ]

    runner = DurableARCRunner(harness, NoOpBrainClient(), config={
        "llm": {"model": "gpt-4o"},
        "max_retries_per_puzzle": 1,
        "max_steps_per_puzzle": 5,
        "cost": {"budget_per_puzzle_usd": 10.0}
    })
    task = ABTask(task_id="b130-fail", category="test", prompt="p")
    setattr(task, "game_id", "game-fail")

    # We expect the runner to catch or propagate, but _ledger should still have the failure
    with pytest.raises(RuntimeError, match="API Timeout"):
        await runner._run_puzzle(MagicMock(), task)
    
    ledger = runner._ledger
    fail_call = [e for e in ledger if e["call_type"] == "arc_api_action" and e["arc_api_io"]["response"]["received"] is False]
    assert len(fail_call) == 1
    io = fail_call[0]["arc_api_io"]
    assert io["response"]["error"]["error_type"] == "RuntimeError"
    assert "API Timeout" in io["response"]["error"]["error_message"]

    # B130 Timeline check in failure
    timeline = runner.brain.arc_event_timeline
    assert len(timeline) >= 2 # 1 pair for RESET (even if failed)
    assert timeline[-2]["kind"] == "request_started"
    assert timeline[-1]["kind"] == "response_received"
    assert timeline[-1]["call_seq"] == timeline[-2]["call_seq"]
    assert timeline[-1]["http_status"] is None
    assert "failed: RuntimeError" in timeline[-1]["response_summary"]
