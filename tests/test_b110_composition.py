
import pytest
from unittest.mock import MagicMock
from agents.arc3.orchestrator import ARCOrchestrator
from benchmarks.arc3.state_serializer import StateSerializerForARC

@pytest.fixture
def orchestrator():
    return ARCOrchestrator(
        brain_client=MagicMock(),
        llm_client=MagicMock(),
        session_id="session",
        serializer=StateSerializerForARC(),
        config={},
    )

def test_compose_final_prompt_ordering(orchestrator):
    sections = {
        "SYSTEM": "sys",
        "STATE": "state",
        "PLAN": "plan",
        "OBSERVATION": "obs",
        "INSTRUCTION": "inst"
    }
    observation = {"task_id": "t1"}
    prompt = orchestrator._compose_final_prompt(sections, observation, None)
    
    # Check ordering
    assert prompt.find("sys") < prompt.find("state")
    assert prompt.find("state") < prompt.find("=== PLAN ===")
    assert prompt.find("=== PLAN ===") < prompt.find("=== OBSERVATION ===")
    assert prompt.find("=== OBSERVATION ===") < prompt.find("inst")

def test_observation_suppression_when_effects_rich(orchestrator):
    sections = {
        "OBSERVED_EFFECTS": "x" * 400, # Rich enough
        "OBSERVATION": "Grid: 10x10\nCoarse map:\n0 0\n0 0"
    }
    observation = {"task_id": "t1"}
    prompt = orchestrator._compose_final_prompt(sections, observation, None)
    
    assert "Coarse map" not in prompt
    assert "coarse map suppressed" in prompt
    assert "Grid: 10x10" in prompt

def test_no_observation_suppression_when_effects_lean(orchestrator):
    sections = {
        "OBSERVED_EFFECTS": "short", 
        "OBSERVATION": "Grid: 10x10\nCoarse map:\n0 0\n0 0"
    }
    observation = {"task_id": "t1"}
    prompt = orchestrator._compose_final_prompt(sections, observation, None)
    
    assert "Coarse map" in prompt
    assert "Grid: 10x10" in prompt

def test_format_methods_ownership_and_headers(orchestrator):
    # This test verifies that format methods don't include headers themselves
    # because _compose_final_prompt adds them.
    
    hyp_ctx = {
        "action_facts": [{"action": "ACTION1", "description": "desc"}],
        "action_coverage": {"tested_count": 1, "untested_count": 1}
    }
    
    fact_lines = orchestrator._format_action_fact_section(hyp_ctx)
    assert len(fact_lines) > 0
    assert not any("===" in l for l in fact_lines)
    assert not any("ACTION FACTS" in l for l in fact_lines)

    path_lines = orchestrator._format_path_hypothesis_section(hyp_ctx)
    assert len(path_lines) > 0
    assert any("COVERAGE:" in l for l in path_lines) # Strict ownership
    assert not any("===" in l for l in path_lines)

def test_dedup_logic_placeholder(orchestrator):
    # Currently _compose_final_prompt identifies fact_actions but doesn't 
    # fully rewrite INSTRUCTION yet. Let's verify it at least extracts them.
    sections = {
        "ACTION_FACTS": "ACTION1: something\nACTION2: something else",
        "INSTRUCTION": "inst"
    }
    # We can't easily test internal local variables, but we can verify 
    # the method runs without error.
    prompt = orchestrator._compose_final_prompt(sections, {"task_id": "t1"}, None)
    assert "ACTION1" in prompt
    assert "inst" in prompt
