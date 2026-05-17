import pytest
from agents.arc3.reasoning_controller import ReasoningController, ReasoningMode

def test_reasoning_controller_decisions():
    controller = ReasoningController()
    
    # Case 1: Default escalation
    decision = controller.decide(
        world_summary="test summary",
        compiled_delta=None,
        budget_state={"budget_exhausted": False},
        phase="solve",
        active_hypotheses=[],
        available_actions=["ACTION1", "ACTION2"]
    )
    assert decision.mode == ReasoningMode.LLM_REASON
    
    # Case 2: Single legal action stalling
    class MockDelta:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'no_op'})()]
        failure_signal = None
        
    decision = controller.decide(
        world_summary="test summary",
        compiled_delta=MockDelta(),
        budget_state={"budget_exhausted": False},
        phase="solve",
        active_hypotheses=[],
        available_actions=["ACTION1"]
    )
    # Refined Rule 3 in A079 uses CHEAP_PROBE and requires stalls > 1
    # First call: stalls=1, Rule 3 doesn't fire yet -> LLM_REASON
    assert decision.mode == ReasoningMode.LLM_REASON
    
    # Second call: stalls=2, Rule 3 fires -> CHEAP_PROBE
    decision = controller.decide(
        world_summary="test summary",
        compiled_delta=MockDelta(),
        budget_state={"budget_exhausted": False},
        phase="solve",
        active_hypotheses=[],
        available_actions=["ACTION1"]
    )
    assert decision.mode == ReasoningMode.CHEAP_PROBE
    assert decision.trigger == "single_legal_action_stalling"

    # Case 3: Single action terminal stall
    class MockStallDelta:
        claims = []
        failure_signal = "single_action_terminal_stall"

    controller = ReasoningController() # reset
    decision = controller.decide(
        world_summary="test summary",
        compiled_delta=MockStallDelta(),
        budget_state={"budget_exhausted": False},
        phase="solve",
        active_hypotheses=[],
        available_actions=["ACTION1"]
    )
    # In A079, terminal stall returns CHEAP_PROBE until threshold hit
    assert decision.mode == ReasoningMode.CHEAP_PROBE


def test_single_action_pixel_churn_does_not_early_stop_at_default_stall_threshold():
    controller = ReasoningController({"reasoning_gate": {"stall_threshold": 2, "min_probes_before_stop": 0}})

    class MockTickDelta:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'pixel_churn'})()]
        failure_signal = "single_action_terminal_stall"
        step = 1

    decisions = [
        controller.decide(
            world_summary="test summary",
            compiled_delta=MockTickDelta(),
            budget_state={"budget_exhausted": False},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1"],
        )
        for _ in range(5)
    ]

    assert all(d.mode == ReasoningMode.CHEAP_PROBE for d in decisions)
    assert decisions[-1].trigger == "single_action_tick_probe"


def test_single_action_pixel_churn_uses_short_unproductive_tick_budget():
    controller = ReasoningController({
        "reasoning_gate": {
            "max_single_action_tick_probes": 20,
            "max_unproductive_single_action_tick_probes": 3,
        }
    })

    class MockTickDelta:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'pixel_churn'})()]
        failure_signal = "single_action_terminal_stall"
        step = 1

    decisions = [
        controller.decide(
            world_summary="test summary",
            compiled_delta=MockTickDelta(),
            budget_state={"budget_exhausted": False},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1"],
            mechanic_priors=[],
        )
        for _ in range(4)
    ]

    assert [d.mode for d in decisions[:3]] == [ReasoningMode.CHEAP_PROBE] * 3
    assert decisions[-1].mode == ReasoningMode.EARLY_STOP
    assert decisions[-1].trigger == "single_action_tick_budget_exhausted"


def test_delayed_prior_keeps_full_single_action_tick_budget():
    controller = ReasoningController({
        "reasoning_gate": {
            "max_single_action_tick_probes": 5,
            "max_unproductive_single_action_tick_probes": 1,
        }
    })

    class MockTickDelta:
        claims = [type('Claim', (), {'kind': 'action_effect', 'effect_class': 'pixel_churn'})()]
        failure_signal = "single_action_terminal_stall"
        step = 1

    prior = {"id": "m1", "effects": [{"effect_class": "delayed_reward"}]}
    decisions = [
        controller.decide(
            world_summary="test summary",
            compiled_delta=MockTickDelta(),
            budget_state={"budget_exhausted": False},
            phase="solve",
            active_hypotheses=[],
            available_actions=["ACTION1"],
            mechanic_priors=[prior],
        )
        for _ in range(3)
    ]

    assert all(d.mode == ReasoningMode.CHEAP_PROBE for d in decisions)
    assert decisions[-1].trigger == "delayed_reward_probe"
    assert decisions[-1].stall_policy == "delayed_reward_wait"
