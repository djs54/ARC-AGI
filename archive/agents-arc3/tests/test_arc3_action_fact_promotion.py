
import pytest
from unittest.mock import MagicMock
from agents.arc3.hypothesis import HypothesisManager

@pytest.fixture
def mgr():
    return HypothesisManager(MagicMock(), "session1")

@pytest.mark.asyncio
async def test_promotion_deterministic_effect(mgr):
    """AC: Repeated operator evidence can create a durable action fact - deterministic case."""
    # Test that repeated consistent effect promotes to deterministic_effect
    # MIN_EVIDENCE is 3
    await mgr.observe([[[1, 1]]], None, 1, ["A1"], {})
    await mgr.observe([[[1, 2, 2, 2, 2, 2, 2, 2, 2, 2]]], "A1", 2, ["A1"], {}, transition_meta={"reward": 1.0, "state_after": "STATE_2"})
    await mgr.observe([[[1, 3, 3, 3, 3, 3, 3, 3, 3, 3]]], "A1", 3, ["A1"], {}, transition_meta={"reward": 1.0, "state_after": "STATE_3"})
    res = await mgr.observe([[[1, 4, 4, 4, 4, 4, 4, 4, 4, 4]]], "A1", 4, ["A1"], {}, transition_meta={"reward": 1.0, "state_after": "STATE_4"})
    
    fact = next(f for f in res["action_facts"] if f["action"] == "A1")
    assert fact["fact_type"] == "deterministic_effect"
    assert "deterministic effect" in fact["description"]

@pytest.mark.asyncio
async def test_promotion_blocked_action(mgr):
    """AC: Repeated operator evidence can create a durable action fact - blocked case."""
    await mgr.observe([[[1, 1]]], None, 1, ["A1"], {})
    await mgr.observe([[[1, 1]]], "A1", 2, ["A1"], {}, transition_meta={"reward": 0.0})
    await mgr.observe([[[1, 1]]], "A1", 3, ["A1"], {}, transition_meta={"reward": 0.0})
    res = await mgr.observe([[[1, 1]]], "A1", 4, ["A1"], {}, transition_meta={"reward": 0.0})
    
    fact = next(f for f in res["action_facts"] if f["action"] == "A1")
    assert fact["fact_type"] == "blocked"
    assert "is blocked" in fact["description"]

@pytest.mark.asyncio
async def test_promotion_loop_action(mgr):
    """AC: Repeated operator evidence can create a durable action fact - loop case."""
    await mgr.observe([[[1]]], None, 1, ["A1"], {})
    await mgr.observe([[[2]]], "A1", 2, ["A1"], {}, transition_meta={"reward": 0.0})
    res = await mgr.observe([[[1]]], "A1", 3, ["A1"], {}, transition_meta={"reward": 0.0})
    
    fact = next(f for f in res["action_facts"] if f["action"] == "A1")
    assert fact["fact_type"] == "loop"
    assert "loop" in fact["description"]

@pytest.mark.asyncio
async def test_consistent_low_value_not_successful(mgr):
    """AC: Consistent-but-low-value actions are not marked as successful strategies."""
    # "low_value" label from _evaluate_meaningful_change usually means score < 0.35
    # Let's simulate localized change without reward or new state
    await mgr.observe([[[1, 1, 1, 1]]], None, 1, ["A1"], {})
    # Revisit same state with small change
    await mgr.observe([[[1, 1, 1, 2]]], "A1", 2, ["A1"], {}, transition_meta={"reward": 0.0})
    # Another small change, still low value
    await mgr.observe([[[1, 1, 2, 2]]], "A1", 3, ["A1"], {}, transition_meta={"reward": 0.0})
    res = await mgr.observe([[[1, 2, 2, 2]]], "A1", 4, ["A1"], {}, transition_meta={"reward": 0.0})
    
    fact = next(f for f in res["action_facts"] if f["action"] == "A1")
    assert fact["fact_type"] == "low_value"
    assert fact["value_status"] in {"low_value", "ineffective"}
    assert "strong_progress" not in fact["description"]
    assert "valuable" not in fact["value_status"]

@pytest.mark.asyncio
async def test_action_facts_separate_from_path_hypotheses(mgr):
    """AC: Action facts are stored and described separately from path hypotheses."""
    await mgr.observe([[[1, 1]]], None, 1, ["A1", "A2"], {})
    await mgr.observe([[[1, 2]]], "A1", 2, ["A1", "A2"], {}, transition_meta={"reward": 0.0})
    res = await mgr.observe([[[2, 2]]], "A2", 3, ["A1", "A2"], {}, transition_meta={"reward": 0.0})
    
    assert "action_facts" in res
    assert "path_hypotheses" in res
    # Path hypothesis should describe sequence
    assert any("A1 -> A2" in h["description"] for h in res["path_hypotheses"])
    # Action facts should describe single actions
    assert any(f["action"] == "A1" for f in res["action_facts"])
    assert any(f["action"] == "A2" for f in res["action_facts"])

@pytest.mark.asyncio
async def test_no_op_fact_promotion(mgr):
    """Threshold for no-op / blocked action."""
    # Specific test for no-op (zero pixels changed)
    await mgr.observe([[[7]]], None, 1, ["A1"], {})
    await mgr.observe([[[7]]], "A1", 2, ["A1"], {}, transition_meta={"reward": 0.0})
    await mgr.observe([[[7]]], "A1", 3, ["A1"], {}, transition_meta={"reward": 0.0})
    res = await mgr.observe([[[7]]], "A1", 4, ["A1"], {}, transition_meta={"reward": 0.0})
    
    fact = next(f for f in res["action_facts"] if f["action"] == "A1")
    assert fact["fact_type"] == "blocked"
    assert "is blocked" in fact["description"]
