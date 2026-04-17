from __future__ import annotations

import pytest

from benchmarks.arc3.adapter import NoOpBrainClient
from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.orchestrator import ARCOrchestrator


class _CompactLLM:
    def chat(self, messages):
        return '{"action": 2, "why": "try the second action"}'


def _make_orch(model_name: str, llm=None) -> ARCOrchestrator:
    return ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=llm,
        session_id="b164-session",
        serializer=StateSerializerForARC(),
        config={"llm_model": model_name},
    )


def test_is_compact_model_detects_small_models():
    orch = _make_orch("qwen2.5:7b")
    assert orch._is_compact_model() is True

    big = _make_orch("gpt-4.1")
    assert big._is_compact_model() is False


def test_build_compact_packet_uses_five_or_fewer_blocks():
    orch = _make_orch("qwen2.5:7b")
    observation = {
        "grid": [[0, 1], [2, 3]],
        "available_actions": ["ACTION1", "ACTION2", "ACTION3"],
        "colors": [{"value": 0, "count": 1}, {"value": 1, "count": 1}],
    }
    orch._compact_mode = True
    packet = orch._build_compact_packet(observation, {}, [], observation["available_actions"])

    assert len(packet.blocks) <= 5
    assert packet.get_block("INSTRUCTION") is not None


@pytest.mark.asyncio
async def test_query_llm_accepts_simplified_action_json():
    orch = _make_orch("qwen2.5:7b", llm=_CompactLLM())
    result = await orch._query_llm("choose", ["ACTION1", "ACTION2", "ACTION3"])

    assert result["action_id"] == "ACTION2"
    assert "second action" in result["rationale"]
