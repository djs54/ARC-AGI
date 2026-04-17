"""Tests for B199: gap-aware exploration budget integration.

Verifies orchestrator reads `exploration_budget_multiplier` from config,
emits a trace event, and that the multiplier increases forced exploration budget
so that previously-exhausted forced exploration can be resumed.
"""

from __future__ import annotations

from benchmarks.arc3.state_serializer import StateSerializerForARC
from benchmarks.arc3.adapter import NoOpBrainClient
from agents.arc3.orchestrator import ARCOrchestrator


def test_default_multiplier_is_one():
    orch = ARCOrchestrator(NoOpBrainClient(), llm_client=None, session_id="s-default", serializer=StateSerializerForARC(), config={})
    assert getattr(orch, "_exploration_budget_multiplier", 1.0) == 1.0


def test_multiplier_applied_forces_exploration():
    # Configure multiplier=2.0 and ensure trace emitted
    orch = ARCOrchestrator(NoOpBrainClient(), llm_client=None, session_id="s-gap", serializer=StateSerializerForARC(), config={"exploration_budget_multiplier": 2.0})
    assert any(e.get("operation") == "gap_aware_budget" for e in orch._execution_trace)

    # Setup scenario where base_max == 1 (level 4) and forced_exploration_count already 1
    orch._current_level = 4
    orch._forced_exploration_count = 1
    orch._hypothesis_context = {"action_coverage": {"untested_actions": ["ACTION2"]}, "observed_action_effects": []}
    orch._consecutive_no_progress_steps = 1

    action = {"action_id": "ACTION1", "rationale": "test"}
    available = ["ACTION1", "ACTION2"]

    res = orch._enforce_action_policy(action, available)

    # With multiplier, adjusted max_explore should be > base and exploration should be forced
    assert res.get("action_id") == "ACTION2"
    assert res.get("decision_source") == "policy_override"


def test_no_multiplier_preserves_previous_behavior():
    orch = ARCOrchestrator(NoOpBrainClient(), llm_client=None, session_id="s-nogap", serializer=StateSerializerForARC(), config={})
    orch._current_level = 4
    orch._forced_exploration_count = 1
    orch._hypothesis_context = {"action_coverage": {"untested_actions": ["ACTION2"]}, "observed_action_effects": []}
    orch._consecutive_no_progress_steps = 1

    action = {"action_id": "ACTION1", "rationale": "test"}
    available = ["ACTION1", "ACTION2"]

    res = orch._enforce_action_policy(action, available)

    # Without multiplier, forced_exploration_count >= base_max (1) so no forced exploration
    assert res.get("action_id") == "ACTION1"