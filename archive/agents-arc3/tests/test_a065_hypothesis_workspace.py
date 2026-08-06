
import pytest
import json
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import (
    SolveContext, HypothesisWorkspace, Hypothesis, HypothesisStatus, GameArchetype, VictoryCondition, VictoryType
)

@pytest.mark.asyncio
async def test_hypothesis_workspace_creation_and_sync():
    """A065: Verify that workspace is created and synced from solve context."""
    brain = AsyncMock()
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config={}
    )
    
    # Mock solve context
    solve_ctx = SolveContext(
        archetype=GameArchetype.SPACE,
        archetype_confidence=0.8,
        victory_condition=VictoryCondition(
            condition_type=VictoryType.REACH_GOAL,
            description="reach the red square",
            confidence=0.7
        )
    )
    
    orchestrator._update_hypothesis_workspace(step=1, solve_ctx=solve_ctx)
    
    workspace = solve_ctx.hypothesis_workspace
    assert len(workspace.hypotheses) == 2
    assert any(h.scope == "archetype" and "space" in h.statement for h in workspace.hypotheses)
    assert any(h.scope == "victory-condition" and "reach the red square" in h.statement for h in workspace.hypotheses)

@pytest.mark.asyncio
async def test_hypothesis_demotion_on_coordinate_irrelevance():
    """A065: Verify that hypotheses are demoted based on A062 evidence."""
    brain = AsyncMock()
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config={}
    )
    
    solve_ctx = SolveContext()
    workspace = solve_ctx.hypothesis_workspace
    
    # Add a targeted coordinate hypothesis
    workspace.add_hypothesis(Hypothesis(
        id="coord-target-A6",
        scope="coordinate-causality",
        statement="ACTION6 target position matters",
        confidence=0.8
    ))
    
    # Simulate A062 irrelevance detection
    orchestrator._action_coord_relevance["ACTION6"] = {
        "args_effective": "false",
        "relevance_reason": "varied requests but fixed effect"
    }
    
    orchestrator._update_hypothesis_workspace(step=2, solve_ctx=solve_ctx)
    
    # Verify demotion
    h_target = next(h for h in workspace.hypotheses if h.id == "coord-target-A6")
    assert h_target.status == HypothesisStatus.DEMOTED
    assert "relevance is low" in h_target.evidence_against[-1]
    
    # Verify new hypothesis added
    h_irrel = next(h for h in workspace.hypotheses if h.id == "coord-irrel-ACTION6")
    assert h_irrel.status == HypothesisStatus.ACTIVE
    assert "Coordinates are irrelevant" in h_irrel.statement

@pytest.mark.asyncio
async def test_hypothesis_support_on_object_progress():
    """A065: Verify that hypotheses gain confidence from A063 evidence."""
    brain = AsyncMock()
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config={}
    )
    
    solve_ctx = SolveContext()
    workspace = solve_ctx.hypothesis_workspace
    workspace.add_hypothesis(Hypothesis(
        id="vc-rule",
        scope="victory-condition",
        statement="Goal is to expand player object",
        confidence=0.5
    ))
    
    # Simulate A063 object progress
    orchestrator._step_history = [{
        "object_progress": {
            "score": 0.2,
            "summary": "player_expansion:0.20"
        }
    }]
    
    orchestrator._update_hypothesis_workspace(step=3, solve_ctx=solve_ctx)
    
    h_vc = next(h for h in workspace.hypotheses if h.id == "vc-rule")
    assert h_vc.confidence > 0.5
    assert "Object progress observed" in h_vc.evidence_for[-1]

@pytest.mark.asyncio
async def test_workspace_prompt_compaction():
    """A065: Verify that prompt block is correctly built and bounded."""
    brain = AsyncMock()
    orchestrator = ARCOrchestrator(
        brain_client=brain,
        llm_client=MagicMock(),
        session_id="test",
        serializer=MagicMock(),
        config={}
    )
    
    solve_ctx = SolveContext()
    workspace = solve_ctx.hypothesis_workspace
    orchestrator._solve_context = solve_ctx
    
    # Add many hypotheses
    for i in range(10):
        workspace.add_hypothesis(Hypothesis(
            id=f"h-{i}",
            scope="rule",
            statement=f"Rule {i}",
            confidence=0.1 * i
        ))
        
    workspace.add_hypothesis(Hypothesis(
        id="demoted-1",
        scope="rule",
        statement="Bad Rule",
        confidence=0.1,
        status=HypothesisStatus.DEMOTED,
        evidence_against=["failed test"]
    ))
    
    block = orchestrator._build_workspace_block()
    
    assert block is not None
    content = block.content
    
    # Should only show top 3 active
    assert "Rule 9" in content
    assert "Rule 8" in content
    assert "Rule 7" in content
    assert "Rule 0" not in content
    
    # Should show recently demoted
    assert "Recently Demoted Hypotheses:" in content
    assert "Bad Rule" in content
    assert "Demoted because: failed test" in content
