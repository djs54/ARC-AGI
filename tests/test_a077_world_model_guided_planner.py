import pytest
from agents.arc3.world_model_planner import WorldModelPlanner, PlanMode

def test_world_model_planner_basic_selection():
    planner = WorldModelPlanner()
    
    class MockWorldModel:
        def get_active_hypotheses(self):
            return []
            
    selection = planner.select_next_candidate(
        world_model=MockWorldModel(),
        mechanic_priors=[],
        available_actions=["ACTION1", "ACTION2"],
        budget_state={}
    )
    
    assert selection.selected.action_id == "ACTION1"
    assert selection.selected.mode == PlanMode.PROBE
    assert len(selection.candidates) >= 2
    assert "Selected ACTION1" in selection.rationale
