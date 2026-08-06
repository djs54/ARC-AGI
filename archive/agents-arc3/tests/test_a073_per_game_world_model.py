import pytest
from agents.arc3.world_model import WorldModelGraph

def test_world_model_graph_basic_mutations():
    graph = WorldModelGraph("task1", "session1")
    
    # Record state
    state_id = graph.record_state(1, "hash123456")
    assert state_id.startswith("state-task1-1-hash123")
    assert state_id in graph.nodes
    assert graph.nodes[state_id].label == "State"
    assert graph.nodes[state_id].props["hash"] == "hash123456"
    
    # Record action
    action_id = graph.record_action(1, "ACTION1", {"x": 5, "y": 10}, state_id)
    assert action_id.startswith("action-task1-1-ACTION1")
    assert action_id in graph.nodes
    assert any(e.rel == "ACTION_TAKEN" and e.src == state_id and e.dst == action_id for e in graph.edges)
    
    # Record observation
    obs_id = graph.record_observation(1, "hash123456", 0.5, 5.0)
    assert obs_id.startswith("obs-task1-1-hash123")
    
    # Record effect
    effect_id = graph.record_effect(action_id, obs_id, "pixel_churn", {"magnitude": 10, "meaningful": False})
    assert effect_id in graph.nodes
    assert any(e.rel == "CAUSED" and e.src == action_id and e.dst == effect_id for e in graph.edges)
    assert any(e.rel == "OBSERVED_IN" and e.src == effect_id and e.dst == obs_id for e in graph.edges)
    
    # Upsert hypothesis
    hyp_id = graph.upsert_hypothesis("h1", "rule", "Action 1 moves things", 0.8, "active")
    assert hyp_id == "hyp-h1"
    assert graph.nodes[hyp_id].props["confidence"] == 0.8
    
    # Link support
    graph.link_support(obs_id, hyp_id, 0.9, "Matches prediction")
    assert any(e.rel == "SUPPORTS" and e.src == obs_id and e.dst == hyp_id for e in graph.edges)

def test_world_model_summary_and_snapshot():
    graph = WorldModelGraph("task1", "session1")
    graph.record_state(0, "h0")
    graph.record_observation(0, "h0", 0.0, 0.0)
    graph.upsert_hypothesis("h1", "victory", "Goal is X", 0.9, "active")
    
    summary = graph.to_prompt_summary()
    assert "Active Hypotheses:" in summary
    assert "Goal is X" in summary
    
    snapshot = graph.to_trace_snapshot()
    assert snapshot["task_id"] == "task1"
    assert snapshot["node_count"] > 0
    assert snapshot["edge_count"] > 0
