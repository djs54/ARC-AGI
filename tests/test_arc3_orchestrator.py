"""Tests for the ARCOrchestrator agent."""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from benchmarks.arc3.schema import ARC3Observation
from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.orchestrator import ARCOrchestrator


@pytest.fixture
def sample_observation() -> ARC3Observation:
    return {
        "dataset_id": "arc",
        "task_id": "task-1",
        "episode_num": 1,
        "step_num": 1,
        "frame_hash": "abc123framehash",
        "grid": [[0, 1], [2, 0]],
        "colors": [{"value": 0, "count": 2}, {"value": 1, "count": 1}],
        "shapes": [],
        "available_actions": ["ACTION1", "ACTION2", "ACTION5"],
        "state": "NOT_FINISHED",
        "energy_estimate": 1.0,
    }


@pytest.fixture
def mock_brain() -> MagicMock:
    brain = MagicMock()
    brain.current_truth = AsyncMock(return_value={"results": ["ctx"]})
    brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": [{"text": "lesson"}]})
    brain.analogical_search = AsyncMock(return_value={"results": [{"text_raw": "similar"}]})
    brain.recall_plans = AsyncMock(return_value={"plans": []})
    brain.register_plan = AsyncMock(return_value={"plan_id": "plan-a", "warnings": ["avoided"], "suggestions": []})
    brain.report_outcome = AsyncMock(return_value={"updated": True})
    brain.notify_turn = AsyncMock(return_value={"status": "queued"})
    return brain


class MockLLM:
    def __init__(self):
        self.last_messages = None
        self.all_messages = []

    def chat(self, messages):
        self.last_messages = messages
        self.all_messages.append(messages)
        # Check if this is a verification call
        if any("ACTION VERIFICATION" in m["content"] for m in messages):
            return json.dumps({"approved": True, "reason": "verified"})
        return json.dumps({"action_id": "ACTION1", "rationale": "mock"})


@pytest.mark.asyncio
async def test_enforce_action_policy_provenance_and_gate(mock_brain):
    """B133: Verify provenance attribution and frame-hash aware repetition gate."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    
    available = ["ACTION1", "ACTION2", "ACTION3"]
    
    # 1. Test standard LLM decision
    action = {"action_id": "ACTION1", "decision_source": "sandbox"}
    enforced = orchestrator._enforce_action_policy(action, available, current_frame_hash="h1")
    assert enforced["action_id"] == "ACTION1"
    assert enforced["decision_source"] == "sandbox"
    
    # Record result to store frame hash
    orchestrator._step_history.append({"action_id": "ACTION1", "board_before": {"frame_hash": "h1"}})
    orchestrator.record_step_result(0.0, False)
    assert orchestrator._action_frame_hashes["ACTION1"] == "h1"
    
    # 2. Test repeated action on SAME frame -> should override to untested
    # Hypothesis context with untested actions
    orchestrator._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION2", "ACTION3"]}
    }
    action = {"action_id": "ACTION1", "decision_source": "sandbox"}
    enforced = orchestrator._enforce_action_policy(action, available, current_frame_hash="h1")
    assert enforced["action_id"] == "ACTION2"
    assert enforced["decision_source"] == "policy_override"
    
    # 3. Test repeated action on DIFFERENT frame -> should trust LLM
    action = {"action_id": "ACTION1", "decision_source": "sandbox"}
    enforced = orchestrator._enforce_action_policy(action, available, current_frame_hash="h2")
    assert enforced["action_id"] == "ACTION1"
    assert enforced["decision_source"] == "sandbox"
    
    # 4. Test fallback from LLM -> should still apply exploration/ranking policy
    # (B133 revised: chunk enforcement is skipped but exploration/ranking still fires)
    action = {"action_id": "ACTION1", "decision_source": "mental_sandbox_fallback"}
    enforced = orchestrator._enforce_action_policy(action, available, current_frame_hash="h1")
    # Exploration policy should redirect to an untested action
    assert enforced["action_id"] == "ACTION2"
    assert enforced["decision_source"] == "policy_override"


@pytest.mark.asyncio
async def test_enforce_action_policy_overrides_stale_low_value_repeat(mock_brain):
    """Repeated low-value sandbox actions should still be overridden on changing frames."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    available = ["ACTION1", "ACTION2", "ACTION7"]
    orchestrator._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION2"]},
        "observed_action_effects": [
            {
                "action": "ACTION7",
                "value_status": "ineffective",
                "last_meaningful_label": "low_value",
                "zero_reward_streak": 4,
                "no_progress_count": 2,
                "avg_meaningful_change": 0.1,
                "rank_score": 0.0,
            }
        ],
    }
    orchestrator._action_frame_hashes["ACTION7"] = "old-frame"

    action = {
        "action_id": "ACTION7",
        "decision_source": "sandbox",
        "rationale": "retry ACTION7",
    }
    enforced = orchestrator._enforce_action_policy(action, available, current_frame_hash="new-frame")

    assert enforced["action_id"] == "ACTION2"
    assert enforced["decision_source"] == "policy_override"
    assert "stale low-value" in enforced["rationale"]


@pytest.mark.asyncio
async def test_enforce_action_policy_preserves_autopilot_direction(mock_brain):
    """Autopilot should retain geometry-driven moves despite sparse-reward fatigue evidence."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    available = ["ACTION1", "ACTION2", "ACTION3"]
    orchestrator._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION2"]},
        "observed_action_effects": [
            {
                "action": "ACTION1",
                "value_status": "ineffective",
                "last_meaningful_label": "low_value",
                "zero_reward_streak": 4,
                "no_progress_count": 2,
                "avg_meaningful_change": 0.1,
                "rank_score": 0.0,
            }
        ],
    }
    orchestrator._action_frame_hashes["ACTION1"] = "old-frame"

    action = {
        "action_id": "ACTION1",
        "decision_source": "autopilot",
        "rationale": "autopilot: target is 10 rows above, using discovered mapping",
    }
    enforced = orchestrator._enforce_action_policy(action, available, current_frame_hash="new-frame")

    assert enforced["action_id"] == "ACTION1"
    assert enforced["decision_source"] == "autopilot"
    assert "autopilot" in enforced["rationale"]


@pytest.mark.asyncio
async def test_enforce_action_policy_preserves_action6_coordinate_probe(mock_brain):
    """ACTION6 should be allowed to retry with new coordinates despite one stale low-value probe."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    available = ["ACTION6", "ACTION7"]
    orchestrator._hypothesis_context = {
        "action_coverage": {"untested_actions": []},
        "observed_action_effects": [
            {
                "action": "ACTION6",
                "value_status": "low_value",
                "last_meaningful_label": "low_value",
                "zero_reward_streak": 3,
                "no_progress_count": 1,
                "avg_meaningful_change": 0.0,
                "rank_score": 0.0,
            }
        ],
    }

    action = {
        "action_id": "ACTION6",
        "decision_source": "sandbox",
        "rationale": "probe a different coordinate",
    }
    enforced = orchestrator._enforce_action_policy(action, available, current_frame_hash="new-frame")

    assert enforced["action_id"] == "ACTION6"
    assert enforced["decision_source"] == "sandbox"


@pytest.mark.asyncio
async def test_enforce_action_policy_realigns_to_expected_route_action(mock_brain):
    """B209: If route expects ACTION3, execute must not silently drift to ACTION2."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    orchestrator._solve_context = {
        "expected_action": "ACTION3",
        "active_chunk": {
            "description": "Plateau Exploitation: commit to top-ranked ACTION3",
            "estimated_actions": ["ACTION3", "ACTION3", "ACTION3"],
        },
    }
    action = {
        "action_id": "ACTION2",
        "decision_source": "sandbox",
        "rationale": "oscillation detected",
    }
    enforced = orchestrator._enforce_action_policy(action, ["ACTION1", "ACTION2", "ACTION3"], current_frame_hash="h1")

    assert enforced["action_id"] == "ACTION3"
    assert enforced["decision_source"] == "policy_override"
    assert enforced["expected_action"] == "ACTION3"
    assert enforced["selected_action"] == "ACTION2"
    assert enforced["adherence_ok"] is False


@pytest.mark.asyncio
async def test_enforce_action_policy_relaxes_expected_action_when_stagnating(mock_brain):
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    orchestrator._consecutive_no_progress_steps = 3
    orchestrator._solve_context = {
        "expected_action": "ACTION3",
        "active_chunk": {
            "description": "Plateau Exploitation: commit to top-ranked ACTION3",
            "estimated_actions": ["ACTION3", "ACTION3"],
        },
    }
    action = {
        "action_id": "ACTION2",
        "decision_source": "sandbox",
        "rationale": "trying alternate route under no progress",
    }

    enforced = orchestrator._enforce_action_policy(action, ["ACTION1", "ACTION2", "ACTION3"])

    # Under stagnation, do not hard-force the expected action.
    assert enforced["action_id"] == "ACTION2"
    assert enforced["expected_action"] == "ACTION3"
    assert enforced["override_reason"] == "stagnation_relaxation"
    assert enforced["adherence_ok"] is False


@pytest.mark.asyncio
def test_enforce_action_policy_preserves_unexplored_exploration(mock_brain):
    """Decay guard must not override when LLM is choosing an unexplored action."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    available = ["ACTION1", "ACTION2", "ACTION4"]
    orchestrator._hypothesis_context = {
        "action_coverage": {"untested_actions": ["ACTION4"]},
        "observed_action_effects": [
            {
                "action": "ACTION4",
                "value_status": "low_value",
                "last_meaningful_label": "low_value",
                "zero_reward_streak": 5,
                "no_progress_count": 2,
                "avg_meaningful_change": 0.0,
                "rank_score": 0.1,
            }
        ],
    }

    action = {
        "action_id": "ACTION4",
        "decision_source": "sandbox",
        "rationale": "ACTION4 is a new action that hasn't been tried yet in this context.",
    }

    enforced = orchestrator._enforce_action_policy(action, available, current_frame_hash="f1")
    assert enforced["action_id"] == "ACTION4"
    assert enforced.get("decision_source") != "policy_override"


def test_detect_split_map_rotate_cross_does_not_exist():
    import agents.arc3.orchestrator as orch_module
    assert not hasattr(orch_module.ARCOrchestrator, "_detect_split_map_rotate_cross"), (
        "_detect_split_map_rotate_cross is a puzzle-specific cheat code and must not exist"
    )


def test_autopilot_confidence_drops_on_no_progress_spatial_lock(mock_brain):
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._consecutive_no_progress_steps = 3
    orchestrator._solve_context = {
        "object_roles": {
            "1": {"role": "player", "confidence": 0.9, "estimated_position": {"row": 2.0, "col": 2.0}},
            "2": {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 9.0, "col": 9.0}},
        }
    }
    # Pre-seed last target to simulate repeated unsuccessful navigation
    orchestrator._last_autopilot_target = (9, 9)
    grid = [[0 for _ in range(12)] for _ in range(12)]
    result = orchestrator._try_autopilot({"grid": grid}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5", "ACTION6"])
    assert result is None


@pytest.mark.asyncio
async def test_mental_sandbox_parse_recovery(mock_brain, sample_observation):
    """B132: Verify sandbox recovers from malformed-but-extractable JSON."""
    class MalformedLLM:
        def chat(self, messages):
            # Return JSON wrapped in text
            return "Thinking... here is the JSON: {\"action_id\": \"ACTION2\", \"rationale\": \"recovered\"} ... end of thought."

    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=MalformedLLM(),
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    
    # We mock _query_llm to ensure it IS NOT called
    with patch.object(orchestrator, '_query_llm', AsyncMock()) as mock_fallback:
        action = await orchestrator._mental_sandbox("prompt", ["ACTION1", "ACTION2"], sample_observation)
        
        assert action["action_id"] == "ACTION2"
        assert action["decision_source"] == "sandbox_recovered"
        assert not mock_fallback.called


@pytest.mark.asyncio
async def test_mental_sandbox_fallback_attribution(mock_brain, sample_observation):
    """B132: Verify sandbox fallback is correctly attributed."""
    class BrokenLLM:
        def chat(self, messages):
            return "Not JSON at all"

    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=BrokenLLM(),
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    
    # Mock _query_llm to return a valid action
    with patch.object(orchestrator, '_query_llm', AsyncMock(return_value={"action_id": "ACTION5", "rationale": "fallback"})) as mock_fallback:
        action = await orchestrator._mental_sandbox("prompt", ["ACTION1", "ACTION2", "ACTION5"], sample_observation)
        
        assert action["action_id"] == "ACTION5"
        assert action["decision_source"] == "mental_sandbox_fallback"
        assert mock_fallback.called
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    ctx = await orchestrator.perceive(sample_observation)
    mock_brain.current_truth.assert_called_once()
    mock_brain.recall_relevant_lessons.assert_called_once()
    mock_brain.analogical_search.assert_called_once()
    assert "lessons" in ctx and "memories" in ctx


@pytest.mark.asyncio
async def test_perceive_step_response(mock_brain, sample_observation):
    """perceive_step_response() should summarize the step, notify, and store the result."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    res = await orchestrator.perceive_step_response(sample_observation, step=1, reward=0.0, done=False, action_id="ACTION1")
    mock_brain.notify_turn.assert_called()
    assert isinstance(res, dict)
    for key in ("step", "state", "reward", "done", "delta", "available_actions", "active_colors"):
        assert key in res
    # B205: perception should include an evolving phase question for step>0
    assert "phase_question" in res
    assert isinstance(res.get("phase_question"), str) and res.get("phase_question")
    # Should mention the action id used for this step when available
    assert "ACTION1" in res.get("phase_question")
    assert getattr(orchestrator, "_last_response_perception", None) == res


@pytest.mark.asyncio
async def test_perceive_step_response_prefers_canonical_step_action(mock_brain, sample_observation):
    """B210: When provided action id is stale, use latest recorded step action id."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._step_history.append({"action_id": "ACTION6"})

    res = await orchestrator.perceive_step_response(
        sample_observation,
        step=16,
        reward=0.0,
        done=False,
        action_id="ACTION2",
    )

    assert "ACTION6" in res.get("phase_question", "")
    assert res.get("action_id") == "ACTION6"


@pytest.mark.asyncio
async def test_perceive_ingests_puzzle_structure(mock_brain, sample_observation):
    """perceive() should call notify_turn with puzzle structure before querying memory."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    await orchestrator.perceive(sample_observation)
    # First notify_turn call should be the puzzle structure ingestion
    first_call = mock_brain.notify_turn.call_args_list[0]
    content = first_call.kwargs["content"]
    assert "[PUZZLE STRUCTURE]" in content
    assert "task-1" in content
    assert "2x2" in content
    assert "Spatial sketch 4x4" in content


@pytest.mark.asyncio
async def test_plan_registers_and_captures_reflex(mock_brain, sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    memory_context = {}
    plan = await orchestrator.plan(sample_observation, memory_context)
    assert orchestrator._plan_id == "plan-a"
    assert "avoided" in orchestrator._reflex_context["warnings"][0]
    assert plan["plan_id"] == "plan-a"


@pytest.mark.asyncio
async def test_act_injects_memory_and_reflex_into_prompt(mock_brain, sample_observation):
    mock_llm = MockLLM()
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=mock_llm,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._reflex_context = {"warnings": ["don't do that"], "suggestions": ["try this"]}
    memory_ctx = {"lessons": [{"text": "lesson"}], "memories": ["ctx"], "analogies": []}
    action = await orchestrator.act(sample_observation, memory_ctx, step_num=1)
    assert mock_llm.last_messages is not None
    prompt = mock_llm.last_messages[-1]["content"]
    assert "WARNING" in prompt or "GOLDEN PATH" in prompt
    mock_brain.notify_turn.assert_called()
    assert action["action_id"] == "ACTION1"


@pytest.mark.asyncio
async def test_evaluate_reports_positive_outcome(mock_brain, sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._plan_id = "pid"
    await orchestrator.evaluate(True, steps_taken=2, max_steps=10, final_observation=sample_observation)
    call = mock_brain.report_outcome.call_args
    assert call is not None
    assert call.kwargs["valence"] > 0.5


@pytest.mark.asyncio
async def test_solve_updates_context(mock_brain, sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=MagicMock(),
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    # Mocking SolveEngine.solve is easier than setting up the whole state
    orchestrator.solve_engine.solve = AsyncMock(return_value=MagicMock(
        archetype=MagicMock(value="race"),
        archetype_confidence=0.8,
        object_roles={3: MagicMock(role=MagicMock(value="wall"), confidence=0.7)},
        victory_condition=MagicMock(
            condition_type=MagicMock(value="reach_goal"),
            description="reach exit",
            confidence=0.6
        ),
        active_chunk=MagicMock(
            description="move to exit",
            estimated_actions=["ACTION1"],
            progress_score=0.0,
            source="bfs"
        ),
        dissonance_detected=False,
        dissonance_reason="",
        strategy_summary="TEST SUMMARY"
    ))

    hyp_ctx = {"current_state_hash": "h1"}
    solve_ctx = await orchestrator.solve(sample_observation, hyp_ctx, step=5)

    assert solve_ctx["archetype"] == "race"
    assert solve_ctx["archetype_confidence"] == 0.8
    assert solve_ctx["victory_condition"]["type"] == "reach_goal"
    assert orchestrator._solve_context == solve_ctx

@pytest.mark.asyncio
async def test_act_includes_solve_section_in_prompt(mock_brain, sample_observation):
    mock_llm = MockLLM()
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=mock_llm,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._solve_context = {
        "archetype": "race",
        "archetype_confidence": 0.8,
        "strategy_summary": "TEST STRATEGY",
        "active_chunk": {"description": "chunk1", "progress": 0.5, "source": "bfs"}
    }
    
    memory_ctx = {"lessons": [], "memories": [], "analogies": []}
    await orchestrator.act(sample_observation, memory_ctx, step_num=1)
    
    prompt = mock_llm.last_messages[-1]["content"]
    assert "=== SOLVE CONTEXT ===" in prompt
    assert "ARCHETYPE: race" in prompt
    assert "ACTIVE CHUNK: chunk1" in prompt


def test_reward_to_valence_correct_fast():
    assert ARCOrchestrator.reward_to_valence(True, 1, 10) == 1.0


def test_reward_to_valence_correct_slow():
    v = ARCOrchestrator.reward_to_valence(True, 9, 10)
    assert 0.3 <= v <= 0.5


def test_reward_to_valence_failed():
    assert ARCOrchestrator.reward_to_valence(False, 10, 10) == -0.5


def test_prompt_contains_memory_reflex_plan_history(sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._plan_steps = ["step one", "step two"]
    orchestrator._reflex_context = {"warnings": ["bad"], "suggestions": ["good"]}
    prompt = orchestrator.build_action_prompt(
        sample_observation,
        {"lessons": [{"text": "l"}], "memories": [], "analogies": [], "_triggered": True},
        step_history=[{"step": 1, "action_id": "ACTION1", "rationale": "r", "reward": 0.0, "done": False}],
        available_actions=["ACTION1", "ACTION6"],
    )
    assert "Available actions" in prompt
    assert "memory" in prompt.lower()
    assert "Step" in prompt
    assert "STATE" in prompt

def test_prompt_asks_for_decision_from_observed_effects(sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._hypothesis_context = {
        "action_coverage": {
            "tested_count": 1,
            "untested_count": 1,
            "untested_actions": ["ACTION2"],
            "initial_exploration_complete": False,
            "top_two_low_value": False,
        },
        "environment_bottleneck": {
            "type": "single_blocked_action",
            "action": "ACTION1",
            "times_seen": 2,
            "message": "Environment bottleneck: only ACTION1 is available and it is blocked/no-op after 2 observation(s).",
        },
        "action_facts": [
            {
                "id": "fact-ACTION1",
                "action": "ACTION1",
                "fact_type": "deterministic_effect",
                "description": "ACTION1 reliably produces localized_change",
                "consistency": 0.9,
                "value_status": "low_value",
                "evidence_count": 2,
                "support_steps": [1, 2],
            }
        ],
        "path_hypotheses": [
            {
                "actions": ["ACTION1", "ACTION2"],
                "description": "path ACTION1 -> ACTION2 ends in tentative_progress with avg_score 0.38",
                "confidence": 0.7,
                "value_status": "tentative",
                "support_steps": [1, 2],
            }
        ],
        "last_transition_effect": {
            "action": "ACTION1",
            "summary": "regional_change: 3 pixels changed in rows 0-0, cols 0-2",
            "meaningful_change_score": 0.42,
            "meaningful_change_label": "tentative_progress",
            "meaningful_change_reasons": ["novel_state", "visible_effect"],
            "zero_reward_streak": 2,
            "before_frame_hash": "beforehash1234",
            "after_frame_hash": "afterhash5678",
            "before_snapshot": {"coarse_map": "0 0\n0 0"},
            "after_snapshot": {"coarse_map": "1 1\n1 1"},
            "changed_region": {
                "row_range": [0, 1],
                "col_range": [0, 1],
                "before_crop": "0 0\n0 0",
                "after_crop": "1 1\n1 1",
            },
        },
        "observed_action_effects": [
            {
                "action": "ACTION1",
                "times_seen": 2,
                "avg_pixels_changed": 1.5,
                "avg_meaningful_change": 0.42,
                "no_change_count": 1,
                "no_progress_count": 0,
                "novel_state_count": 2,
                "reward_hits": 0,
                "zero_reward_streak": 2,
                "last_meaningful_label": "tentative_progress",
                "rank_score": 0.39,
                "retest_budget": 2,
                "over_retest_budget": False,
                "recent_diff": "regional_change: 3 pixels changed in rows 0-0, cols 0-2",
            },
            {
                "action": "ACTION2",
                "times_seen": 0,
                "avg_pixels_changed": 0.0,
                "avg_meaningful_change": 0.0,
                "no_change_count": 0,
                "no_progress_count": 0,
                "novel_state_count": 0,
                "reward_hits": 0,
                "zero_reward_streak": 0,
                "last_meaningful_label": "UNTESTED",
                "rank_score": 0.0,
                "retest_budget": 0,
                "over_retest_budget": False,
                "recent_diff": "UNTESTED",
            },
        ],
    }
    prompt = orchestrator.build_action_prompt(
        sample_observation,
        {"lessons": [], "memories": [], "analogies": [], "_triggered": False},
        step_history=[],
        available_actions=["ACTION1", "ACTION2"],
    )
    assert "ACTION FACTS" in prompt
    assert "PATH HYPOTHESES" in prompt
    assert "OBSERVED EFFECTS" in prompt
    assert "Treat action ids as opaque operators" in prompt
    assert "Choose the next valid action based on observed effects" in prompt
    assert "Start in an exploration phase" in prompt
    assert "strong_progress or tentative_progress" in prompt
    assert "tentative_progress" in prompt
    assert "After 2 consecutive zero-reward tentative steps" in prompt
    assert "ACTION2: UNTESTED" in prompt
    assert "zero_reward_streak 2" in prompt
    assert "Currently available but unobserved actions: ACTION2" in prompt
    assert "Exploration coverage: tested 1, untested 1" in prompt
    assert "PATH TENTATIVE" in prompt
    assert "ACTION1: DETERMINISTIC_EFFECT" in prompt
    assert "rank 0.39" in prompt
    assert "budget 2" in prompt
    assert "Board transition: beforeha -> afterhas" in prompt
    assert "Before board 4x4:" in prompt
    assert "After board 4x4:" in prompt
    assert "Changed region rows 0-1, cols 0-1" in prompt
    assert "Changed region before:" in prompt
    assert "Changed region after:" in prompt
    assert "Environment bottleneck: only ACTION1 is available and it is blocked/no-op after 2 observation(s)." in prompt


@pytest.mark.asyncio
async def test_hypothesize_write_trace_includes_saved_facts_and_paths(sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator.hypothesis_mgr.observe = AsyncMock(
        return_value={
            "last_transition_effect": {
                "action": "ACTION1",
                "summary": "regional_change: 3 pixels changed",
                "meaningful_change_score": 0.42,
                "meaningful_change_label": "tentative_progress",
            },
            "action_facts": [
                {
                    "id": "fact-ACTION1",
                    "action": "ACTION1",
                    "fact_type": "deterministic_effect",
                    "description": "ACTION1 shifts the active region upward",
                    "consistency": 0.9,
                    "value_status": "tentative",
                    "evidence_count": 2,
                    "trend": {
                        "kind": "directional_drift",
                        "axis": "row",
                        "direction": "up",
                        "avg_delta": 1.0,
                        "samples": 2,
                        "stable_region": True,
                        "message": "upward drift by ~1.0 cell(s)/step within a stable region",
                    },
                    "support_steps": [1, 2],
                }
            ],
            "path_hypotheses": [
                {
                    "actions": ["ACTION1", "ACTION3"],
                    "description": "path ACTION1 -> ACTION3 preserves motion while changing region",
                    "confidence": 0.7,
                    "value_status": "tentative",
                    "support_steps": [1, 2],
                }
            ],
            "environment_bottleneck": {
                "type": "single_blocked_action",
                "action": "ACTION1",
                "times_seen": 2,
                "message": "Environment bottleneck: only ACTION1 is available and it is blocked/no-op after 2 observation(s).",
            },
        }
    )

    await orchestrator.hypothesize(sample_observation, "ACTION1", 2, transition_meta={})
    trace = orchestrator.consume_write_trace()

    assert trace[0]["kind"] == "hypothesis_update"
    detail = trace[0]["detail"]
    assert detail["saved_action_facts"][0]["action"] == "ACTION1"
    assert detail["saved_action_facts"][0]["fact_type"] == "deterministic_effect"
    assert detail["saved_action_facts"][0]["trend"]["direction"] == "up"
    assert detail["saved_path_hypotheses"][0]["actions"] == ["ACTION1", "ACTION3"]
    assert detail["saved_path_hypotheses"][0]["value_status"] == "tentative"
    assert detail["environment_bottleneck"]["type"] == "single_blocked_action"


def test_first_move_filters_memory_without_observation_match(sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    prompt = orchestrator.build_action_prompt(
        sample_observation,
        {"lessons": [], "memories": [{"text": "ACTION7"}], "analogies": [], "_triggered": True},
        step_history=[],
        available_actions=["ACTION1", "ACTION2", "ACTION5"],
    )
    assert "ACTION7" not in prompt
    assert "Matched memory" not in prompt


def test_first_move_keeps_matching_memory(sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    prompt = orchestrator.build_action_prompt(
        sample_observation,
        {"lessons": [], "memories": [{"text": "arc_eval_001 ACTION1 state NOT_FINISHED color 1"}], "analogies": [], "_triggered": True},
        step_history=[],
        available_actions=["ACTION1", "ACTION2", "ACTION5"],
    )
    assert "Matched memory" in prompt


def test_policy_override_forces_unexplored_action(sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._hypothesis_context = {
        "action_coverage": {
            "tested_count": 2,
            "untested_count": 2,
            "untested_actions": ["ACTION3", "ACTION4"],
            "initial_exploration_complete": False,
            "top_two_low_value": False,
        }
    }
    orchestrator._current_level = 1
    orchestrator._consecutive_no_progress_steps = 2  # B154: Force explore when stuck
    action = orchestrator._enforce_action_policy(
        {"action_id": "ACTION1", "rationale": "tentative_progress"},
        ["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
    )
    assert action["action_id"] == "ACTION3"
    assert "exploration step 1/5 (level 1)" in action["rationale"]


def test_policy_override_broadens_exploration_after_decay(sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._hypothesis_context = {
        "action_coverage": {
            "tested_count": 4,
            "untested_count": 1,
            "untested_actions": ["ACTION4"],
            "initial_exploration_complete": False,
            "top_two_low_value": True,
        }
    }
    orchestrator._current_level = 1
    orchestrator._consecutive_no_progress_steps = 2  # B154: Force explore when stuck
    action = orchestrator._enforce_action_policy(
        {"action_id": "ACTION2", "rationale": "last low_value"},
        ["ACTION1", "ACTION2", "ACTION3", "ACTION4"],
    )
    assert action["action_id"] == "ACTION4"
    assert "exploration step 1/5 (level 1)" in action["rationale"]


def test_select_ranked_action_prefers_best_under_budget():
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    selected = orchestrator._select_ranked_action([
        {"action": "ACTION1", "rank_score": 0.40, "times_seen": 3, "over_retest_budget": True},
        {"action": "ACTION2", "rank_score": 0.35, "times_seen": 1, "over_retest_budget": False},
        {"action": "ACTION3", "rank_score": 0.10, "times_seen": 0, "over_retest_budget": False},
    ])
    assert selected == "ACTION2"


@pytest.mark.asyncio
async def test_act_uses_available_actions_from_observation(mock_brain, sample_observation):
    """act() should read available_actions from the observation, not hardcode ACTION1-7."""
    mock_llm = MockLLM()
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=mock_llm,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    memory_ctx = {"lessons": [], "memories": [], "analogies": []}
    action = await orchestrator.act(sample_observation, memory_ctx, step_num=1)
    prompt = mock_llm.last_messages[-1]["content"]
    # Observation has ACTION1, ACTION2, ACTION5 — not all 7
    assert "ACTION1" in prompt
    assert "ACTION5" in prompt
    assert "ACTION3" not in prompt  # not in available_actions


@pytest.mark.asyncio
async def test_act_rejects_unavailable_llm_action(mock_brain, sample_observation):
    class InvalidActionLLM:
        def __init__(self):
            self.last_messages = None

        def chat(self, messages):
            self.last_messages = messages
            return json.dumps({"action_id": "ACTION7", "rationale": "bad choice"})

    mock_llm = InvalidActionLLM()
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=mock_llm,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    memory_ctx = {"lessons": [], "memories": [], "analogies": []}
    action = await orchestrator.act(sample_observation, memory_ctx, step_num=1)

    assert action["action_id"] == "ACTION1"
    assert "Invalid LLM action" in action["rationale"]


def test_action6_coordinate_policy_uses_bootstrap_role_positions(mock_brain, sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._solve_context = {
        "archetype": "space",
        "victory_condition": {"type": "reach_goal"},
        "object_roles": {
            "5": {
                "role": "player",
                "confidence": 0.45,
                "estimated_position": {"row": 10.0, "col": 10.0},
            },
            "1": {
                "role": "goal",
                "confidence": 0.76,
                "estimated_position": {"row": 10.0, "col": 14.0},
            },
        },
    }

    observation = dict(sample_observation)
    observation["available_actions"] = ["ACTION6"]
    observation["grid"] = [[0 for _ in range(20)] for _ in range(20)]
    observation["grid"][10][10] = 5
    observation["grid"][10][14] = 1

    candidates = orchestrator._candidate_action6_coordinates(observation)

    assert candidates[0][0] == "goal_vector"
    assert any(coord == (10, 10) for _, coord in candidates[:10])


def test_action6_coordinate_policy_escapes_row_sweep_on_stagnation(mock_brain, sample_observation):
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    # Simulate repeated zero-reward ACTION6 probes along the same row.
    orchestrator._consecutive_no_progress_steps = 6
    orchestrator._step_history = [
        {"action_id": "ACTION6", "x": 10, "y": 2, "reward": 0.0},
        {"action_id": "ACTION6", "x": 11, "y": 2, "reward": 0.0},
        {"action_id": "ACTION6", "x": 12, "y": 2, "reward": 0.0},
    ]

    observation = dict(sample_observation)
    observation["available_actions"] = ["ACTION6"]
    observation["grid"] = [[0 for _ in range(20)] for _ in range(20)]
    # Non-background structure that would otherwise produce row-2 sweep candidates.
    for x in range(10, 15):
        observation["grid"][2][x] = 5

    x, y = orchestrator._infer_action6_coordinates(observation)

    # Under stagnation, the policy should jump out of the failed row cluster.
    assert y != 2


@pytest.mark.asyncio
async def test_act_preserves_action6_coordinates_from_llm(mock_brain, sample_observation):
    class Action6LLM:
        def chat(self, messages):
            return json.dumps({"action_id": "ACTION6", "rationale": "target hotspot", "x": 1, "y": 0})

    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=Action6LLM(),
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    observation = dict(sample_observation)
    observation["available_actions"] = ["ACTION6"]
    memory_ctx = {"lessons": [], "memories": [], "analogies": []}

    action = await orchestrator.act(observation, memory_ctx, step_num=1)

    assert action["action_id"] == "ACTION6"
    assert action["x"] == 1
    assert action["y"] == 0


@pytest.mark.asyncio
async def test_act_infers_action6_coordinates_when_llm_omits_them(mock_brain, sample_observation):
    class Action6NoCoordsLLM:
        def chat(self, messages):
            return json.dumps({"action_id": "ACTION6", "rationale": "UNTESTED"})

    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=Action6NoCoordsLLM(),
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    observation = dict(sample_observation)
    observation["available_actions"] = ["ACTION6"]
    memory_ctx = {"lessons": [], "memories": [], "analogies": []}

    action = await orchestrator.act(observation, memory_ctx, step_num=1)

    assert action["action_id"] == "ACTION6"
    assert (action["x"], action["y"]) in {(1, 0), (0, 1)}
    assert "x=" in action["rationale"] and "y=" in action["rationale"]


@pytest.mark.asyncio
async def test_api_knowledge_ingestion(mock_brain):
    """ingest_api_knowledge should push all chunks into SideQuests."""
    from agents.arc3.api_knowledge import ingest_api_knowledge, API_KNOWLEDGE_CHUNKS
    count = await ingest_api_knowledge(mock_brain, "session-1")
    assert count == len(API_KNOWLEDGE_CHUNKS)
    assert mock_brain.notify_turn.call_count == len(API_KNOWLEDGE_CHUNKS)
    # Verify chunks are tagged
    first_call = mock_brain.notify_turn.call_args_list[0]
    assert "ARC-AGI-3 API Contract" in first_call.kwargs["content"]


def test_reset_for_retry_clears_plan_keeps_history():
    """reset_for_retry should clear the plan but keep step history."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._plan_id = "plan-1"
    orchestrator._reflex_context = {"warnings": ["bad"]}
    orchestrator._plan_steps = ["step one"]
    orchestrator._step_history = [{"step": 1, "action_id": "ACTION1", "rationale": "r", "reward": 0.0, "done": False}]

    orchestrator.reset_for_retry(1)

    assert orchestrator._plan_id is None
    assert orchestrator._reflex_context is None
    assert orchestrator._plan_steps == []
    # History should still have the original step + a GAME_OVER sentinel
    assert len(orchestrator._step_history) == 2
    assert orchestrator._step_history[-1]["action_id"] == "GAME_OVER"
    assert orchestrator._step_history[-1]["reward"] == -1.0


def test_prompt_includes_energy(sample_observation):
    """The action prompt should include energy level."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    sample_observation["energy_estimate"] = 0.42
    prompt = orchestrator.build_action_prompt(
        sample_observation,
        {"lessons": [], "memories": [], "analogies": [], "_triggered": False},
        step_history=[],
        available_actions=["ACTION1"],
    )
    assert "ENERGY" in prompt
    assert "42%" in prompt


# ── B89: Prompt Budget & Retrieval Budget Metrics ──────────────────────────


@pytest.mark.asyncio
async def test_retrieval_payload_size_tracked(mock_brain, sample_observation):
    """B89: perceive() should track retrieval payload sizes."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    await orchestrator.perceive(sample_observation)
    assert len(orchestrator._retrieval_payloads) == 1
    payload = orchestrator._retrieval_payloads[0]
    assert "total_size" in payload
    assert payload["total_size"] >= 0


@pytest.mark.asyncio
async def test_prompt_tokens_estimated_per_step(mock_brain, sample_observation):
    """B89: act() should estimate and track prompt tokens per step."""
    mock_llm = MockLLM()
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=mock_llm,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._reflex_context = {"warnings": [], "suggestions": []}
    memory_ctx = {"lessons": [], "memories": [], "analogies": [], "_retrieval_payload_size": 0}

    await orchestrator.act(sample_observation, memory_ctx, step_num=1)
    assert len(orchestrator._prompt_tokens_per_step) == 1
    assert orchestrator._prompt_tokens_per_step[0] > 0


@pytest.mark.asyncio
async def test_first_prompt_detail_level_tracked(mock_brain, sample_observation):
    """B89: First prompt should track whether it includes rich context."""
    mock_llm = MockLLM()
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=mock_llm,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._reflex_context = {"warnings": [], "suggestions": []}
    memory_ctx = {
        "lessons": [{"text": "lesson"}],  # Non-empty to trigger "rich"
        "memories": ["ctx"],
        "analogies": [],
        "_retrieval_payload_size": 100,
    }

    await orchestrator.act(sample_observation, memory_ctx, step_num=1)
    assert orchestrator._first_prompt_detail_level in ("rich", "compact")


@pytest.mark.asyncio
async def test_no_progress_step_count_incremented_on_zero_reward(mock_brain, sample_observation):
    """B89: record_step_result() should increment no_progress_step_count on zero reward."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    # Simulate a step being recorded
    orchestrator._step_history.append({"step": 1, "reward": None})

    orchestrator.record_step_result(reward=0.0, done=False)
    assert orchestrator._no_progress_step_count == 1

    orchestrator.record_step_result(reward=1.0, done=True)
    assert orchestrator._no_progress_step_count == 1  # Unchanged


def test_get_benchmark_metrics_returns_expected_fields():
    """B89: get_benchmark_metrics() should return all expected metric fields."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    # Populate some metrics
    orchestrator._prompt_tokens_per_step = [100, 120, 110]
    orchestrator._retrieval_payloads = [{"total_size": 1000}, {"total_size": 1200}]
    orchestrator._invalid_action_count = 1
    orchestrator._no_progress_step_count = 2
    orchestrator._first_prompt_detail_level = "rich"
    orchestrator._asked_for_decision_from_effects = True

    metrics = orchestrator.get_benchmark_metrics()

    assert "prompt_budget" in metrics
    assert "retrieval_budget" in metrics
    assert metrics["prompt_budget"]["avg_tokens_per_step"] == 110.0
    assert metrics["prompt_budget"]["invalid_action_count"] == 1
    assert metrics["prompt_budget"]["no_progress_step_count"] == 2
    assert metrics["prompt_budget"]["first_prompt_detail_level"] == "rich"
    assert metrics["retrieval_budget"]["retrieval_count"] == 2
    assert metrics["retrieval_budget"]["total_retrieval_size_bytes"] == 2200


# ── B90: Retrieval Trigger Tests ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_retrieval_triggered_on_initial_bootstrap(mock_brain, sample_observation):
    """B90: perceive() at step=0 should always trigger retrieval."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    ctx = await orchestrator.perceive(sample_observation, step=0)

    assert orchestrator._retrieval_triggered is True
    assert ctx["_triggered"] is True
    assert len(orchestrator._retrieval_payloads) == 1
    mock_brain.current_truth.assert_called_once()
    mock_brain.recall_relevant_lessons.assert_called_once()
    mock_brain.analogical_search.assert_called_once()


@pytest.mark.asyncio
async def test_retrieval_skipped_when_no_trigger_fires(mock_brain, sample_observation):
    """B90: perceive() at step > 0 with no triggers should skip retrieval."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    # Set hypothesis context with no problematic conditions
    orchestrator._hypothesis_context = {
        "loop_detected": False,
        "action_coverage": {
            "tested_count": 1,
            "untested_count": 3,
            "top_two_low_value": False,
        },
        "observed_action_effects": [{"action": "ACTION1", "avg_meaningful_change": 0.5}],
    }
    orchestrator._no_progress_step_count = 0
    orchestrator._invalid_action_count = 0

    ctx = await orchestrator.perceive(sample_observation, step=5)

    assert orchestrator._retrieval_triggered is False
    assert ctx["_triggered"] is False
    assert len(orchestrator._retrieval_payloads) == 0
    mock_brain.current_truth.assert_not_called()
    mock_brain.recall_relevant_lessons.assert_not_called()
    mock_brain.analogical_search.assert_not_called()


@pytest.mark.asyncio
async def test_retrieval_triggered_on_loop_detection(mock_brain, sample_observation):
    """B90: perceive() should trigger retrieval when loop is detected."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._hypothesis_context = {
        "loop_detected": True,
        "loop_hash": "hash123",
        "action_coverage": {},
        "observed_action_effects": [],
    }

    ctx = await orchestrator.perceive(sample_observation, step=5)

    assert orchestrator._retrieval_triggered is True
    assert ctx["_triggered"] is True
    mock_brain.current_truth.assert_called_once()


@pytest.mark.asyncio
async def test_retrieval_triggered_on_no_progress(mock_brain, sample_observation):
    """B90: perceive() should trigger retrieval when no-progress streak persists."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._no_progress_step_count = 3
    orchestrator._consecutive_no_progress_steps = 3
    orchestrator._last_retrieval_step = -1
    orchestrator._hypothesis_context = {
        "loop_detected": False,
        "action_coverage": {},
        "observed_action_effects": [],
    }

    ctx = await orchestrator.perceive(sample_observation, step=5)

    assert orchestrator._retrieval_triggered is True
    assert ctx["_triggered"] is True
    mock_brain.current_truth.assert_called_once()


@pytest.mark.asyncio
async def test_retrieval_triggered_on_invalid_action_count(mock_brain, sample_observation):
    """B90: perceive() should trigger retrieval after an invalid action fallback."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._invalid_action_count = 1
    orchestrator._hypothesis_context = {
        "loop_detected": False,
        "action_coverage": {},
        "observed_action_effects": [],
    }

    ctx = await orchestrator.perceive(sample_observation, step=5)

    assert orchestrator._retrieval_triggered is True
    assert ctx["_triggered"] is True
    mock_brain.current_truth.assert_called_once()


@pytest.mark.asyncio
async def test_retrieval_triggered_on_top_two_low_value(mock_brain, sample_observation):
    """B90: perceive() should trigger retrieval when top actions decay to low_value."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._hypothesis_context = {
        "loop_detected": False,
        "action_coverage": {
            "tested_count": 2,
            "untested_count": 2,
            "top_two_low_value": True,
        },
        "observed_action_effects": [],
    }

    ctx = await orchestrator.perceive(sample_observation, step=5)

    assert orchestrator._retrieval_triggered is True
    assert ctx["_triggered"] is True
    mock_brain.current_truth.assert_called_once()


@pytest.mark.asyncio
async def test_retrieval_triggered_on_large_state_shift(mock_brain, sample_observation):
    """B90: perceive() should trigger retrieval when the latest change is large enough to invalidate assumptions."""
    orchestrator = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._hypothesis_context = {
        "loop_detected": False,
        "action_coverage": {},
        "observed_action_effects": [],
        "last_transition_effect": {
            "meaningful_change_score": 0.8,
            "pixels_changed": 40,
        },
    }

    ctx = await orchestrator.perceive(sample_observation, step=5)

    assert orchestrator._retrieval_triggered is True
    assert ctx["_triggered"] is True
    mock_brain.current_truth.assert_called_once()


def test_prompt_memory_section_excluded_when_not_triggered(sample_observation):
    """B90: build_action_prompt() should exclude MEMORY section when retrieval not triggered."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    # Memory context with _triggered=False (no retrieval)
    memory_ctx = {
        "lessons": [{"text": "this should not appear"}],
        "memories": [{"text": "also should not appear"}],
        "analogies": [],
        "_triggered": False,
    }

    prompt = orchestrator.build_action_prompt(
        sample_observation,
        memory_ctx,
        step_history=[],
        available_actions=["ACTION1", "ACTION2"],
    )

    assert "MEMORY:" not in prompt
    assert "this should not appear" not in prompt
    assert "also should not appear" not in prompt


def test_prompt_memory_section_included_when_triggered(sample_observation):
    """B90: build_action_prompt() should include MEMORY section when retrieval triggered."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    # Memory context with _triggered=True (retrieval happened)
    memory_ctx = {
        "lessons": [{"text": "important lesson"}],
        "memories": [{"text": "relevant memory"}],
        "analogies": [],
        "_triggered": True,
    }

    prompt = orchestrator.build_action_prompt(
        sample_observation,
        memory_ctx,
        step_history=[],
        available_actions=["ACTION1", "ACTION2"],
    )

    assert "=== MEMORY ===" in prompt
    assert "important lesson" in prompt


def test_prompt_smaller_on_no_trigger_path(sample_observation):
    """B90: Prompt size should be smaller when retrieval is not triggered."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    # Prompt with no retrieval
    memory_ctx_no_trigger = {
        "lessons": [],
        "memories": [],
        "analogies": [],
        "_triggered": False,
    }
    prompt_no_trigger = orchestrator.build_action_prompt(
        sample_observation,
        memory_ctx_no_trigger,
        step_history=[],
        available_actions=["ACTION1", "ACTION2"],
    )

    # Prompt with retrieval
    memory_ctx_triggered = {
        "lessons": [{"text": "long lesson text that adds size to the prompt"}],
        "memories": [{"text": "long memory text that also adds size"}],
        "analogies": [{"text": "analogy text"}],
        "_triggered": True,
    }
    prompt_triggered = orchestrator.build_action_prompt(
        sample_observation,
        memory_ctx_triggered,
        step_history=[],
        available_actions=["ACTION1", "ACTION2"],
    )

    assert len(prompt_no_trigger) < len(prompt_triggered)


def test_prompt_instruction_includes_effect_summary(sample_observation):
    """B110: Effect summary is in OBSERVED EFFECTS, not duplicated in INSTRUCTION."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator._hypothesis_context = {
        "last_transition_effect": {
            "action": "ACTION1",
            "meaningful_change_label": "strong_progress",
            "meaningful_change_score": 0.85,
            "meaningful_change_reasons": [],
            "zero_reward_streak": 0,
            "summary": "Board changed"
        }
    }

    memory_ctx = {"lessons": [], "memories": [], "analogies": [], "_triggered": False}
    prompt = orchestrator.build_action_prompt(
        sample_observation,
        memory_ctx,
        step_history=[],
        available_actions=["ACTION1", "ACTION2"],
    )

    # B110: Effect summary should be in OBSERVED EFFECTS section, not INSTRUCTION
    assert "strong_progress" in prompt
    assert "0.85" in prompt
    assert "What should you try next?" in prompt
    assert "Choose the next valid action based on observed effects" in prompt
    # B110: INSTRUCTION no longer duplicates the effect summary
    assert "=== OBSERVED EFFECTS ===" in prompt


def test_prompt_instruction_handles_no_prior_effects(sample_observation):
    """B110: INSTRUCTION should work without prior effects context."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    # No hypothesis context, or no last_transition_effect
    orchestrator._hypothesis_context = None

    memory_ctx = {"lessons": [], "memories": [], "analogies": [], "_triggered": False}
    prompt = orchestrator.build_action_prompt(
        sample_observation,
        memory_ctx,
        step_history=[],
        available_actions=["ACTION1", "ACTION2"],
    )

    # B110: INSTRUCTION should always ask for decision, even without prior effects
    assert "What should you try next?" in prompt
    assert "Choose the next valid action based on observed effects" in prompt
    # OBSERVATION section should be present when no OBSERVED EFFECTS
    assert "=== OBSERVATION ===" in prompt


# ── B117: Typed Decision Packets ──────────────────────────────────────

def test_build_action_packet_creates_all_block_types(sample_observation):
    """B117: build_action_packet() should create blocks for all decision surfaces."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    
    memory_ctx = {
        "lessons": [{"text": "test lesson"}],
        "memories": [{"text": "test memory"}],
        "analogies": [],
        "_triggered": True,
    }
    
    orchestrator._hypothesis_context = {
        "action_facts": [{"action": "ACTION1", "description": "test fact"}],
        "path_hypotheses": [{"description": "test path"}],
        "last_transition_effect": {
            "action": "ACTION1",
            "meaningful_change_label": "progress",
            "meaningful_change_score": 0.5,
            "meaningful_change_reasons": [],
            "zero_reward_streak": 0,
            "summary": "Test"
        }
    }
    
    packet = orchestrator.build_action_packet(
        sample_observation,
        memory_ctx,
        step_history=[],
        available_actions=["ACTION1", "ACTION2"],
    )
    
    # B117: Verify core block types are present
    assert packet.get_block("SYSTEM") is not None
    assert packet.get_block("STATE") is not None
    assert packet.get_block("MEMORY") is not None
    assert packet.get_block("ACTION_FACTS") is not None
    assert packet.get_block("PATH_HYPOTHESES") is not None
    assert packet.get_block("OBSERVED_EFFECTS") is not None
    assert packet.get_block("PLAN") is not None
    assert packet.get_block("HISTORY") is not None
    assert packet.get_block("INSTRUCTION") is not None


def test_packet_render_produces_ordered_output(sample_observation):
    """B117: packet.render() should produce blocks in standard order with headers."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    memory_ctx = {"lessons": [], "memories": [], "analogies": [], "_triggered": False}

    packet = orchestrator.build_action_packet(
        sample_observation,
        memory_ctx,
        step_history=[],
        available_actions=["ACTION1"],
    )

    prompt = packet.render()

    # B117: Verify blocks appear in correct order (STATE and SYSTEM don't have === headers)
    system_idx = prompt.find("SYSTEM:")
    state_idx = prompt.find("STATE:")
    plan_idx = prompt.find("=== PLAN ===")
    history_idx = prompt.find("=== HISTORY ===")
    observation_idx = prompt.find("=== OBSERVATION ===")
    instruction_idx = prompt.find("INSTRUCTION:")

    assert system_idx >= 0, "SYSTEM block should be present"
    assert state_idx > system_idx, "STATE should come after SYSTEM"
    assert plan_idx > state_idx, "PLAN should come after STATE"
    assert history_idx > plan_idx, "HISTORY should come after PLAN"
    assert observation_idx > history_idx, "OBSERVATION should come after HISTORY"
    assert instruction_idx > observation_idx, "INSTRUCTION should come after OBSERVATION"


def test_packet_skip_empty_blocks(sample_observation):
    """B117: packet.render() should skip blocks with empty content."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    # No memory context with _triggered=False means no MEMORY block
    memory_ctx = {"lessons": [], "memories": [], "analogies": [], "_triggered": False}

    # No hypothesis context means no ACTION_FACTS block
    orchestrator._hypothesis_context = None

    packet = orchestrator.build_action_packet(
        sample_observation,
        memory_ctx,
        step_history=[],
        available_actions=["ACTION1"],
    )

    prompt = packet.render()

    # Should not have MEMORY or ACTION_FACTS headers since they're empty
    assert "=== MEMORY ===" not in prompt
    assert "=== ACTION FACTS ===" not in prompt
    # But should still have essential blocks (STATE doesn't have === header)
    assert "STATE:" in prompt
    assert "=== PLAN ===" in prompt


def test_build_action_prompt_calls_packet_render(sample_observation):
    """B117: build_action_prompt() should delegate to build_action_packet() and render."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    memory_ctx = {"lessons": [], "memories": [], "analogies": [], "_triggered": False}

    prompt = orchestrator.build_action_prompt(
        sample_observation,
        memory_ctx,
        step_history=[],
        available_actions=["ACTION1"],
    )

    # B117: Verify the result is a rendered prompt string from a packet
    assert isinstance(prompt, str)
    assert len(prompt) > 0
    # Should contain blocks from the packet render (STATE is without === header)
    assert "STATE:" in prompt
    assert "=== PLAN ===" in prompt
    assert "INSTRUCTION:" in prompt


def test_exploration_compaction_in_prompt(sample_observation):
    """B116: EXPLORATION_SUMMARY should appear in prompt when artifact present."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    from agents.arc3.hypothesis import ExplorationCompaction
    orchestrator._compaction_artifact = ExplorationCompaction(
        action_summaries={"ACTION1": "A1: deterministic"},
        known_loops=[["ACTION1", "ACTION2"]],
        confirmed_rules=["rule confirmed"]
    )

    prompt = orchestrator.build_action_prompt(
        sample_observation,
        {"lessons": [], "memories": [], "analogies": [], "_triggered": False},
        step_history=[],
        available_actions=["ACTION1", "ACTION2"],
    )

    assert "=== EXPLORATION SUMMARY ===" in prompt
    assert "KNOWN ACTION EFFECTS:" in prompt
    assert "A1: deterministic" in prompt
    assert "KNOWN LOOPS" in prompt
    assert "ACTION1 -> ACTION2" in prompt
    assert "CONFIRMED RULES" in prompt
    assert "rule confirmed" in prompt


def test_observation_section_includes_entity_roles(sample_observation):
    """B120: Color summary includes role annotations when entity_map populated."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    from agents.arc3.solver import ObjectRole, RoleType
    # Manually populate object roles in solve engine
    orchestrator.solve_engine._object_roles[5] = ObjectRole(
        color_id=5, role=RoleType.PLAYER, confidence=0.9
    )

    # Grid with color 5
    observation = dict(sample_observation)
    observation["colors"] = [{"value": 5, "count": 10}]

    obs_text = orchestrator._format_observation_section(observation)
    assert "5:10(player)" in obs_text


def test_observation_section_fallback_without_entity_map(sample_observation):
    """B120: Color summary is plain when entity_map is empty."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    # Grid with color 5, but no roles
    observation = dict(sample_observation)
    observation["colors"] = [{"value": 5, "count": 10}]

    obs_text = orchestrator._format_observation_section(observation)
    assert "5:10" in obs_text
    assert "(player)" not in obs_text


def test_puzzle_structure_includes_entity_roles(sample_observation):
    """B120: Structure summary includes entity role annotations."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    from agents.arc3.solver import ObjectRole, RoleType
    orchestrator.solve_engine._object_roles[5] = ObjectRole(
        color_id=5, role=RoleType.PLAYER, confidence=0.9,
        estimated_position={"row": 3.0, "col": 7.0}
    )

    observation = dict(sample_observation)
    observation["colors"] = [{"value": 5, "count": 10}]

    summary = orchestrator._summarize_puzzle_structure(observation)
    assert "Entity roles: color 5 = player at row 3, col 7" in summary


def test_action_packet_includes_entity_context_block(sample_observation):
    """B120: Prompt packet has ENTITY_CONTEXT block when entity_map populated."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    from agents.arc3.solver import ObjectRole, RoleType
    orchestrator.solve_engine._object_roles[5] = ObjectRole(
        color_id=5, role=RoleType.PLAYER, confidence=0.9
    )

    packet = orchestrator.build_action_packet(
        sample_observation,
        {"lessons": [], "memories": [], "analogies": [], "_triggered": False},
        step_history=[],
        available_actions=["ACTION1"],
    )

    block = packet.get_block("ENTITY_CONTEXT")
    assert block is not None
    assert "Color 5: player (confidence=90%)" in block.content

    prompt = packet.render()
    assert "=== ENTITY CONTEXT ===" in prompt
    assert "Color 5: player" in prompt


def test_action_packet_no_entity_context_when_empty(sample_observation):
    """B120: Prompt packet omits ENTITY_CONTEXT when entity_map empty."""
    orchestrator = ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    packet = orchestrator.build_action_packet(
        sample_observation,
        {"lessons": [], "memories": [], "analogies": [], "_triggered": False},
        step_history=[],
        available_actions=["ACTION1"],
    )

    assert packet.get_block("ENTITY_CONTEXT") is None
    assert "=== ENTITY CONTEXT ===" not in packet.render()


def test_entity_gate_pass_multi_color(sample_observation):
    """B121: Gate passes when entity map has non-UNKNOWN roles."""
    orchestrator = ARCOrchestrator(
        brain_client=AsyncMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    from agents.arc3.solver import ObjectRole, RoleType
    # Populate with known role
    orchestrator.solve_engine._object_roles[1] = ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9)
    
    # multi-color grid (background 0 + color 1)
    obs = dict(sample_observation)
    obs["colors"] = [{"value": 0, "count": 10}, {"value": 1, "count": 5}]
    
    res = orchestrator._check_entity_gate(obs)
    assert res["status"] == "pass"
    assert "roles identified" in res["reason"]

def test_entity_gate_skip_single_color(sample_observation):
    """B121: Gate skips when grid has only background color."""
    orchestrator = ARCOrchestrator(
        brain_client=AsyncMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    obs = dict(sample_observation)
    obs["colors"] = [{"value": 0, "count": 100}]
    
    res = orchestrator._check_entity_gate(obs)
    assert res["status"] == "skip"
    assert "single-color" in res["reason"]

@pytest.mark.asyncio
async def test_entity_gate_fail_then_retry(sample_observation):
    """B121: Gate retries when all roles are UNKNOWN on multi-color grid."""
    orchestrator = ARCOrchestrator(
        brain_client=AsyncMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator.brain.notify_turn.return_value = {"status": "ok"}
    orchestrator.brain.current_truth.return_value = {"results": []}
    orchestrator.brain.recall_relevant_lessons.return_value = {"lessons": []}
    orchestrator.brain.analogical_search.return_value = {"results": []}
    
    # Mock seed_bootstrap_roles to return unknown first, then known
    from agents.arc3.solver import ObjectRole, RoleType
    unknown_role = ObjectRole(color_id=1, role=RoleType.UNKNOWN, confidence=0.0)
    known_role = ObjectRole(color_id=1, role=RoleType.PLAYER, confidence=0.9)
    
    with patch.object(orchestrator.solve_engine.role_mapper, "seed_bootstrap_roles") as mock_seed:
        mock_seed.side_effect = [{1: unknown_role}, {1: known_role}]
        
        obs = dict(sample_observation)
        obs["colors"] = [{"value": 0, "count": 10}, {"value": 1, "count": 5}]
        
        await orchestrator.perceive(obs, step=0)
        
        # Should have called seed twice (initial + 1 retry)
        assert mock_seed.call_count == 2
        assert orchestrator._entity_gate_result["status"] == "pass"
        assert orchestrator._entity_gate_result["retry_count"] == 1

@pytest.mark.asyncio
async def test_entity_gate_degrade_after_max_retries(sample_observation):
    """B121: Gate degrades after max retries, does not crash."""
    orchestrator = ARCOrchestrator(
        brain_client=AsyncMock(),
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    orchestrator.brain.notify_turn.return_value = {"status": "ok"}
    orchestrator.brain.current_truth.return_value = {"results": []}
    orchestrator.brain.recall_relevant_lessons.return_value = {"lessons": []}
    orchestrator.brain.analogical_search.return_value = {"results": []}

    from agents.arc3.solver import ObjectRole, RoleType
    unknown_role = ObjectRole(color_id=1, role=RoleType.UNKNOWN, confidence=0.0)

    with patch.object(orchestrator.solve_engine.role_mapper, "seed_bootstrap_roles") as mock_seed:
        # Always return unknown
        mock_seed.return_value = {1: unknown_role}

        obs = dict(sample_observation)
        obs["colors"] = [{"value": 0, "count": 10}, {"value": 1, "count": 5}]

        await orchestrator.perceive(obs, step=0)

        # Initial call + 2 retries = 3 calls
        assert mock_seed.call_count == 3
        assert orchestrator._entity_gate_result["status"] == "degraded"
        assert orchestrator._entity_gate_result["retry_count"] == 2


# ── _parse_llm_response tests ────────────────────────────────────────


AVAILABLE = ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"]


class TestParseLlmResponse:
    """Tests for the robust multi-tier LLM response parser."""

    def test_tier1_direct_json(self):
        raw = '{"action_id": "ACTION2", "rationale": "move down"}'
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "ACTION2"
        assert result["parse_method"] == "json_direct"

    def test_tier1_compact_json_format(self):
        raw = '{"action": 3, "why": "try left"}'
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "3"
        assert result["parse_method"] == "json_direct"

    def test_tier2_json_in_markdown_block(self):
        raw = 'Let me think...\n```json\n{"action_id": "ACTION4", "rationale": "go right"}\n```\nDone.'
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "ACTION4"
        assert result["parse_method"] == "json_code_block"

    def test_tier2_json_embedded_in_prose(self):
        raw = 'I think the best move is {"action_id": "ACTION3", "rationale": "left"} because reasons.'
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "ACTION3"
        assert result["parse_method"] == "json_extracted"

    def test_tier3_plain_text_action_mention(self):
        raw = "Based on my analysis, I should try ACTION4 to move right toward the goal."
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "ACTION4"
        assert result["parse_method"] == "plain_text_action_mention"

    def test_tier3_plain_text_direction(self):
        raw = "The goal is below me, so I should move down."
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "ACTION2"
        assert result["parse_method"] == "plain_text_direction"

    def test_tier3_plain_text_go_left(self):
        raw = "I need to go left to avoid the wall."
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "ACTION3"
        assert result["parse_method"] == "plain_text_direction"

    def test_tier3_bare_number(self):
        raw = "I choose 2"
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "ACTION2"
        assert result["parse_method"] == "plain_text_bare_number"

    def test_empty_string_returns_none(self):
        assert ARCOrchestrator._parse_llm_response("", AVAILABLE) is None
        assert ARCOrchestrator._parse_llm_response("   ", AVAILABLE) is None
        assert ARCOrchestrator._parse_llm_response(None, AVAILABLE) is None

    def test_nonsense_returns_none(self):
        raw = "I don't know what to do here. The sky is blue."
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is None

    def test_respects_available_actions(self):
        limited = ["ACTION1", "ACTION2"]
        raw = "I should try ACTION5 to interact."
        result = ARCOrchestrator._parse_llm_response(raw, limited)
        # ACTION5 not available, so plain_text_action_mention won't match
        # Should fall through to direction words
        assert result is None or result["action_id"] in limited

    def test_last_action_mention_wins(self):
        """When multiple actions mentioned, take the last one (conclusion after reasoning)."""
        raw = "ACTION1 didn't work. ACTION3 hit a wall. I'll try ACTION4."
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "ACTION4"

    def test_interact_keyword(self):
        raw = "I should interact with the object."
        result = ARCOrchestrator._parse_llm_response(raw, AVAILABLE)
        assert result is not None
        assert result["action_id"] == "ACTION5"
        assert result["parse_method"] == "plain_text_direction"


# ── B212: Graph Hypothesize ────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_hypothesize_returns_grounded_evidence(sample_observation):
    """B212: graph_hypothesize should return grounded_hypotheses when evidence exists."""
    # Arrange
    brain = AsyncMock()
    # Tier 1: recall_relevant_lessons returns action_effect-like lessons
    brain.recall_relevant_lessons.return_value = {
        "lessons": [
            {"text": json.dumps({"action": "move", "entity_type": "box", "effect": "moved"})},
            {"text": json.dumps({"action": "push", "entity_type": "box", "effect": "moved"})},
        ]
    }
    # Tier 2: current_truth returns spatial facts (empty is fine)
    brain.current_truth.return_value = {"results": []}
    # Tier 3: recall_procedures returns procedures (unused but present)
    brain.recall_procedures.return_value = {"procedures": []}

    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    # Act
    res = await orchestrator.graph_hypothesize(sample_observation, step=1)

    # Assert
    assert isinstance(res, dict)
    assert "graph_evidence" in res
    ge = res["graph_evidence"]
    assert "grounded_hypotheses" in ge
    # Two lessons mentioning box->moved should be distilled into at least one grounded hypothesis
    assert len(ge["grounded_hypotheses"]) >= 1


@pytest.mark.asyncio
async def test_graph_hypothesize_skips_at_step_zero(sample_observation):
    """B212: graph_hypothesize should be a no-op at step 0."""
    brain = AsyncMock()
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    res = await orchestrator.graph_hypothesize(sample_observation, step=0)
    assert res == {"graph_evidence": {"grounded_hypotheses": []}}


@pytest.mark.asyncio
async def test_graph_hypothesize_skips_unknown_archetype(sample_observation):
    """B212: graph_hypothesize should handle missing puzzle_archetype gracefully."""
    brain = AsyncMock()
    # recall_relevant_lessons returns empty when archetype unknown
    brain.recall_relevant_lessons.return_value = {"lessons": []}
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

    # craft an observation with no archetype info
    obs = dict(sample_observation)
    obs.pop("puzzle_archetype", None)

    res = await orchestrator.graph_hypothesize(obs, step=2)
    assert "graph_evidence" in res
    assert res["graph_evidence"]["grounded_hypotheses"] == []


@pytest.mark.asyncio
async def test_graph_hypothesize_uses_structured_lesson_query(sample_observation):
    """B212: graph_hypothesize should query lessons with lesson_type:action_effect."""
    brain = AsyncMock()
    brain.recall_relevant_lessons = AsyncMock(return_value={"lessons": []})
    brain.current_truth = AsyncMock(return_value={"results": []})
    brain.recall_procedures = AsyncMock(return_value={"procedures": []})

    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=None,
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )
    # Ensure archetype present so orchestrator uses archetype-scoped query
    orchestrator._solve_context = {"archetype": "space"}

    await orchestrator.graph_hypothesize(sample_observation, step=1)

    # Assert recall was awaited with structured query including lesson_type
    brain.recall_relevant_lessons.assert_awaited()
    brain.recall_relevant_lessons.assert_awaited_with(query="lesson_type:action_effect puzzle_archetype:space", limit=5)

