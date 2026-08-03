import pytest
from agents.arc3.world_model_compiler import WorldModelCompiler

def test_world_model_compiler_classification():
    compiler = WorldModelCompiler()
    
    # Case 1: No-op
    delta = compiler.compile_step(
        step=1,
        prev_hash="h1", curr_hash="h1",
        action={"action_id": "ACTION1"},
        reward_components={"meaningful_progress": False},
        terminal_trend="flat",
        object_progress={"score": 0.0},
        available_actions=["ACTION1", "ACTION2"]
    )
    assert delta.claims[0].effect_class == "no_op"
    
    # Case 2: Pixel churn
    delta = compiler.compile_step(
        step=2,
        prev_hash="h1", curr_hash="h2",
        action={"action_id": "ACTION1"},
        reward_components={"meaningful_progress": False},
        terminal_trend="flat",
        object_progress={"score": 0.0},
        available_actions=["ACTION1", "ACTION2"]
    )
    assert delta.claims[0].effect_class == "pixel_churn"
    
    # Case 3: Meaningful progress (object)
    delta = compiler.compile_step(
        step=3,
        prev_hash="h2", curr_hash="h3",
        action={"action_id": "ACTION1"},
        reward_components={"meaningful_progress": True, "progress_class": "object_monotonic"},
        terminal_trend="flat",
        object_progress={"score": 1.0},
        available_actions=["ACTION1", "ACTION2"]
    )
    assert delta.claims[0].effect_class == "object_progress"
    
    # Case 4: Terminal progress
    delta = compiler.compile_step(
        step=4,
        prev_hash="h3", curr_hash="h4",
        action={"action_id": "ACTION1"},
        reward_components={"meaningful_progress": True, "progress_class": "terminal"},
        terminal_trend="improving",
        object_progress={"score": 1.0},
        available_actions=["ACTION1", "ACTION2"]
    )
    assert delta.claims[0].effect_class == "terminal_progress"

    # Case 5: Single action terminal stall
    delta = compiler.compile_step(
        step=5,
        prev_hash="h4", curr_hash="h5",
        action={"action_id": "ACTION1"},
        reward_components={"meaningful_progress": False},
        terminal_trend="flat",
        object_progress={"score": 0.0},
        available_actions=["ACTION1"]
    )
    assert delta.failure_signal == "single_action_terminal_stall"
