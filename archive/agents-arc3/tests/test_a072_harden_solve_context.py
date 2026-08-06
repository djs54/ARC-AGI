import pytest
from unittest.mock import MagicMock
from agents.arc3.runner import DurableARCRunner

@pytest.fixture
def runner():
    harness = MagicMock()
    brain = MagicMock()
    config = {}
    return DurableARCRunner(harness, brain, config)

def test_solve_context_get_robustness(runner):
    # Case 1: None
    assert runner._solve_context_get(None, "key", "default") == "default"
    
    # Case 2: dict
    ctx_dict = {"key": "value"}
    assert runner._solve_context_get(ctx_dict, "key", "default") == "value"
    assert runner._solve_context_get(ctx_dict, "missing", "default") == "default"
    
    # Case 3: object/dataclass
    class CtxObj:
        def __init__(self):
            self.key = "obj_value"
    ctx_obj = CtxObj()
    assert runner._solve_context_get(ctx_obj, "key", "default") == "obj_value"
    assert runner._solve_context_get(ctx_obj, "missing", "default") == "default"

def test_build_phase_summary_with_dict_context(runner):
    orchestrator = MagicMock()
    # Mock a dict-shaped solve_context
    orchestrator._solve_context = {
        "archetype": "race",
        "active_chunk": {"description": "test chunk"}
    }
    orchestrator._phase_controller = None
    
    summary = runner._build_phase_summary(orchestrator)
    assert summary["archetype"] == "race"
    assert summary["active_chunk"]["description"] == "test chunk"

def test_build_phase_summary_with_object_context(runner):
    orchestrator = MagicMock()
    # Mock an object-shaped solve_context
    class SolveContext:
        def __init__(self):
            self.archetype = "space"
            self.active_chunk = MagicMock()
            self.active_chunk.description = "obj chunk"
    
    orchestrator._solve_context = SolveContext()
    orchestrator._phase_controller = None
    
    summary = runner._build_phase_summary(orchestrator)
    assert summary["archetype"] == "space"
    assert summary["active_chunk"]["description"] == "obj chunk"
