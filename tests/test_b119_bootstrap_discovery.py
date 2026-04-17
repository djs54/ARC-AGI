
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import SolveEngine, RoleType
from agents.arc3.hypothesis import HypothesisManager
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.fixture
def orchestrator():
    brain = AsyncMock()
    # Ensure mocks return serializable dicts
    brain.notify_turn.return_value = {"status": "ok"}
    brain.current_truth.return_value = {"results": []}
    brain.recall_relevant_lessons.return_value = {"lessons": []}
    brain.analogical_search.return_value = {"results": []}
    
    llm = MagicMock()
    serializer = StateSerializerForARC()
    return ARCOrchestrator(brain, llm, "session-1", serializer, {})

def test_seed_bootstrap_roles_heuristics():
    from agents.arc3.solver import ObjectRoleMapper
    mapper = ObjectRoleMapper()
    
    # Grid with background(0), some color 1 (small), some color 2 (large)
    observation = {
        "colors": [
            {"value": 0, "count": 100},
            {"value": 1, "count": 5},   # Smallest -> Player
            {"value": 2, "count": 50},  # Largest -> Goal
        ]
    }
    
    roles = mapper.seed_bootstrap_roles(observation)
    assert 1 in roles
    assert 2 in roles
    assert roles[1].role == RoleType.PLAYER
    assert roles[2].role == RoleType.GOAL
    assert roles[1].confidence == 0.45


def test_seed_bootstrap_roles_include_estimated_positions_from_grid():
    from agents.arc3.solver import ObjectRoleMapper
    mapper = ObjectRoleMapper()

    observation = {
        "grid": [
            [0, 1, 1],
            [0, 0, 2],
            [0, 0, 2],
        ],
        "colors": [
            {"value": 0, "count": 5},
            {"value": 1, "count": 2},
            {"value": 2, "count": 2},
        ],
    }

    roles = mapper.seed_bootstrap_roles(observation)

    assert roles[1].estimated_position == {"row": 0.0, "col": 1.5}
    assert roles[2].estimated_position == {"row": 1.5, "col": 2.0}

@pytest.mark.asyncio
async def test_perceive_populates_bootstrap_roles(orchestrator):
    observation = {
        "dataset_id": "arc", "task_id": "t1",
        "grid": [[1, 0, 2]],
        "colors": [
            {"value": 0, "count": 1},
            {"value": 1, "count": 1},
            {"value": 2, "count": 1},
        ],
        "shapes": [], "available_actions": ["A1"], "state": "RUNNING", "energy_estimate": 1.0
    }
    
    await orchestrator.perceive(observation, step=0)
    
    # Should have identified roles
    assert len(orchestrator.solve_engine._object_roles) > 0
    
    # Should have recorded a bootstrap_discovery event
    trace = orchestrator.consume_write_trace()
    discovery_events = [e for e in trace if e["kind"] == "bootstrap_discovery"]
    assert len(discovery_events) == 1
    assert "preliminary entities" in discovery_events[0]["summary"]

@pytest.mark.asyncio
async def test_distill_to_brain_flushes_bootstrap_entities(orchestrator):
    # Manually add roles to solve engine
    from agents.arc3.solver import ObjectRole
    orchestrator.solve_engine._object_roles[5] = ObjectRole(color_id=5, role=RoleType.PLAYER, confidence=0.45)
    
    # Distill
    await orchestrator.hypothesis_mgr.distill_to_brain(orchestrator.solve_engine._object_roles)
    
    # Verify brain.notify_turn was called with [BOOTSTRAP ENTITY]
    calls = orchestrator.brain.notify_turn.call_args_list
    bootstrap_calls = [c for c in calls if "[BOOTSTRAP ENTITY]" in str(c.kwargs.get("content", ""))]
    assert len(bootstrap_calls) >= 1
    assert "color_5 identified as player" in bootstrap_calls[0].kwargs["content"]
