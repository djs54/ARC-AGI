from __future__ import annotations

from benchmarks.arc3.adapter import NoOpBrainClient
from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.orchestrator import ARCOrchestrator


def _make_orch() -> ARCOrchestrator:
    return ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=None,
        session_id="b162-session",
        serializer=StateSerializerForARC(),
        config={},
    )


def test_step0_packet_includes_grid_analysis_block():
    orch = _make_orch()
    orch._current_level = 1
    observation = {
        "grid": [
            [0, 0, 0, 0],
            [0, 1, 1, 0],
            [0, 0, 2, 0],
            [3, 0, 0, 0],
        ],
        "available_actions": ["ACTION1", "ACTION2", "ACTION5"],
    }

    orch._ensure_bootstrap_grid_analysis(observation, step=0)
    packet = orch.build_action_packet(observation, {}, [], observation["available_actions"])

    block = packet.get_block("GRID_ANALYSIS")
    assert block is not None
    assert "regions" in block.content.lower()
    assert "colors" in block.content.lower()


def test_bootstrap_grid_analysis_emits_trace_event():
    orch = _make_orch()
    observation = {
        "grid": [
            [0, 1, 0],
            [0, 0, 2],
            [3, 0, 0],
        ],
        "available_actions": ["ACTION1"],
    }

    orch._ensure_bootstrap_grid_analysis(observation, step=0)

    assert any(
        event.get("operation") == "bootstrap_grid_analysis"
        for event in orch._execution_trace
    )
