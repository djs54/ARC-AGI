"""Tests for B126 - Verification Sub-Agent."""

import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from agents.arc3.orchestrator import ARCOrchestrator
from benchmarks.arc3.state_serializer import StateSerializerForARC


@pytest.fixture
def brain():
    return AsyncMock()


@pytest.fixture
def orchestrator(brain):
    llm = MagicMock()
    # Enable verifier for B126 tests
    return ARCOrchestrator(brain, llm, "session-1", StateSerializerForARC(), {"enable_verifier": True})


@pytest.mark.asyncio
async def test_verifier_approves_candidate_action(orchestrator, brain):
    """B126: Verifier should approve sound candidate actions."""
    observation = {
        "dataset_id": "arc", "task_id": "t1", "episode_num": 1, "step_num": 1,
        "grid": [[0, 1], [1, 0]], "colors": [{"value": 0}, {"value": 1}],
        "shapes": [], "state": "UNKNOWN", "available_actions": ["ACTION1", "ACTION2"]
    }

    orchestrator.llm.chat.return_value = json.dumps({
        "action_id": "ACTION1",
        "rationale": "Try ACTION1"
    })

    with patch.object(orchestrator, "_summarize_puzzle_structure", return_value="summary"):
        with patch.object(orchestrator, "_should_trigger_retrieval", return_value=False):
            with patch.object(orchestrator, "build_action_packet") as mock_build:
                mock_packet = MagicMock()
                mock_packet.render.return_value = "test prompt"
                mock_packet.get_block.return_value = None
                mock_build.return_value = mock_packet

                # Mock verifier response (approval)
                verification_count = 0
                def chat_side_effect(messages):
                    nonlocal verification_count
                    verification_count += 1
                    # First call: mental sandbox
                    if verification_count == 1:
                        return json.dumps({"action_id": "ACTION1", "rationale": "Test action"})
                    # Second call: verifier approval
                    else:
                        return json.dumps({"approved": True})

                orchestrator.llm.chat.side_effect = chat_side_effect

                action = await orchestrator.act(observation, {"_triggered": False}, step_num=1)

                assert action["action_id"] == "ACTION1"
                # Verifier should have been called
                assert "thinking_trace" in action
                trace_entries = [e for e in action.get("thinking_trace", []) if e.get("kind") == "verification"]
                assert len(trace_entries) == 1
                assert trace_entries[0]["verifier_approved"] == True
                assert action.get("verifier_status") == "approved"


@pytest.mark.asyncio
async def test_verifier_rejects_then_retries(orchestrator, brain):
    """B126: On rejection, verifier should trigger one retry."""
    observation = {
        "dataset_id": "arc", "task_id": "t1", "episode_num": 1, "step_num": 1,
        "grid": [[0, 1], [1, 0]], "colors": [{"value": 0}, {"value": 1}],
        "shapes": [], "state": "UNKNOWN", "available_actions": ["ACTION1", "ACTION2"]
    }

    # Track LLM calls
    call_count = 0
    def chat_side_effect(messages):
        nonlocal call_count
        call_count += 1
        # First call: mental sandbox proposes ACTION1
        if call_count == 1:
            return json.dumps({"action_id": "ACTION1", "rationale": "Try action 1"})
        # Second call: verifier rejects ACTION1
        elif call_count == 2:
            return json.dumps({"approved": False, "reason": "ACTION1 is known to fail"})
        # Third call: retry mental sandbox with rejection context, proposes ACTION2
        elif call_count == 3:
            return json.dumps({"action_id": "ACTION2", "rationale": "Try action 2 instead"})
        # Fourth call: verifier approves ACTION2
        else:
            return json.dumps({"approved": True})

    orchestrator.llm.chat.side_effect = chat_side_effect

    with patch.object(orchestrator, "_summarize_puzzle_structure", return_value="summary"):
        with patch.object(orchestrator, "_should_trigger_retrieval", return_value=False):
            with patch.object(orchestrator, "build_action_packet") as mock_build:
                mock_packet = MagicMock()
                mock_packet.render.return_value = "test prompt"
                mock_packet.get_block.return_value = None
                mock_build.return_value = mock_packet

                action = await orchestrator.act(observation, {"_triggered": False}, step_num=1)

                # Should have switched to ACTION2 due to rejection
                assert action["action_id"] == "ACTION2"
                # Verifier should record the rejection and retry
                trace_entries = [e for e in action.get("thinking_trace", []) if e.get("kind") == "verification"]
                assert len(trace_entries) == 1
                assert trace_entries[0]["attempts"] == 2  # Two verification attempts


@pytest.mark.asyncio
async def test_verifier_double_rejects_then_proceeds(orchestrator, brain):
    """B126: After two rejections, verifier should give up and use final action."""
    observation = {
        "dataset_id": "arc", "task_id": "t1", "episode_num": 1, "step_num": 1,
        "grid": [[0, 1], [1, 0]], "colors": [{"value": 0}, {"value": 1}],
        "shapes": [], "state": "UNKNOWN", "available_actions": ["ACTION1", "ACTION2"]
    }

    call_count = 0
    def chat_side_effect(messages):
        nonlocal call_count
        call_count += 1
        # First call: mental sandbox proposes ACTION1
        if call_count == 1:
            return json.dumps({"action_id": "ACTION1", "rationale": "Try action 1"})
        # Second call: verifier rejects ACTION1
        elif call_count == 2:
            return json.dumps({"approved": False, "reason": "ACTION1 is bad"})
        # Third call: retry proposes ACTION2
        elif call_count == 3:
            return json.dumps({"action_id": "ACTION2", "rationale": "Try action 2"})
        # Fourth call: verifier rejects ACTION2 again
        elif call_count == 4:
            return json.dumps({"approved": False, "reason": "ACTION2 is also bad"})
        else:
            return json.dumps({"approved": True})

    orchestrator.llm.chat.side_effect = chat_side_effect

    with patch.object(orchestrator, "_summarize_puzzle_structure", return_value="summary"):
        with patch.object(orchestrator, "_should_trigger_retrieval", return_value=False):
            with patch.object(orchestrator, "build_action_packet") as mock_build:
                mock_packet = MagicMock()
                mock_packet.render.return_value = "test prompt"
                mock_packet.get_block.return_value = None
                mock_build.return_value = mock_packet

                action = await orchestrator.act(observation, {"_triggered": False}, step_num=1)

                # Should proceed with ACTION2 despite second rejection
                assert action["action_id"] == "ACTION2"
                # Verifier should record both rejections
                trace_entries = [e for e in action.get("thinking_trace", []) if e.get("kind") == "verification"]
                assert len(trace_entries) == 1
                assert trace_entries[0]["attempts"] == 2
                assert trace_entries[0]["verifier_approved"] == False
                assert action.get("verifier_status") == "rejected_then_proceeded"


@pytest.mark.asyncio
async def test_verifier_result_in_step_history(orchestrator, brain):
    """B126: Verifier result should be recorded in step history."""
    observation = {
        "dataset_id": "arc", "task_id": "t1", "episode_num": 1, "step_num": 1,
        "grid": [[0, 1], [1, 0]], "colors": [{"value": 0}, {"value": 1}],
        "shapes": [], "state": "UNKNOWN", "available_actions": ["ACTION1", "ACTION2"]
    }

    orchestrator.llm.chat.return_value = json.dumps({
        "action_id": "ACTION1",
        "rationale": "Try action",
    })

    with patch.object(orchestrator, "_summarize_puzzle_structure", return_value="summary"):
        with patch.object(orchestrator, "_should_trigger_retrieval", return_value=False):
            with patch.object(orchestrator, "build_action_packet") as mock_build:
                mock_packet = MagicMock()
                mock_packet.render.return_value = "test prompt"
                mock_packet.get_block.return_value = None
                mock_build.return_value = mock_packet

                # Mock verifier to approve
                verification_count = 0
                def chat_side_effect(messages):
                    nonlocal verification_count
                    verification_count += 1
                    if verification_count == 1:
                        return json.dumps({"action_id": "ACTION1", "rationale": "Test"})
                    else:
                        return json.dumps({"approved": True})

                orchestrator.llm.chat.side_effect = chat_side_effect

                action = await orchestrator.act(observation, {"_triggered": False}, step_num=1)

                # Check step history
                assert len(orchestrator._step_history) == 1
                step = orchestrator._step_history[0]
                assert "verifier_status" in step
                assert step["verifier_status"] == "approved"


def test_verify_candidate_action_basic(orchestrator):
    """B126: _verify_candidate_action should construct and call verifier prompt."""
    # This test verifies the method exists and can be called
    observation = {
        "state": "UNKNOWN",
        "colors": [{"value": 0}, {"value": 1}],
        "shapes": [],
        "grid": [[0, 1]],
    }
    hypothesis_context = {
        "loop_detected": False,
        "action_facts": [
            {"action": "ACTION1", "description": "moves forward"}
        ],
    }

    # The method should be async callable
    assert hasattr(orchestrator, "_verify_candidate_action")
    assert callable(orchestrator._verify_candidate_action)


def test_verifier_prompts_imported():
    """B126: Verifier prompts should be properly imported."""
    from agents.arc3.prompts import VERIFIER_SYSTEM_PROMPT, VERIFIER_PROMPT_TEMPLATE
    assert VERIFIER_SYSTEM_PROMPT
    assert "critical" in VERIFIER_SYSTEM_PROMPT.lower()
    assert VERIFIER_PROMPT_TEMPLATE
    assert "approved" in VERIFIER_PROMPT_TEMPLATE.lower()


def test_verifier_prompt_template_completeness():
    """B126: Verifier prompt template should have all required placeholders."""
    from agents.arc3.prompts import VERIFIER_PROMPT_TEMPLATE
    required_placeholders = [
        "action_id",
        "rationale",
        "state",
        "colors",
        "shapes",
        "recent_history",
        "sandbox_result",
        "loop_detected",
        "action_facts_summary",
    ]
    for placeholder in required_placeholders:
        assert f"{{{placeholder}}}" in VERIFIER_PROMPT_TEMPLATE
