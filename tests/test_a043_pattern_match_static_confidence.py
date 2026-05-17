
import pytest
from unittest.mock import MagicMock, AsyncMock
from agents.arc3.solver import HybridPatternMatcher, HybridPatternConfig
from agents.arc3.grid_analysis import GridDiffEngine, PatternRegion, RegionComparison

@pytest.mark.asyncio
async def test_grid_analysis_returns_none_on_empty_foreground():
    """A050: Verify that GridDiffEngine returns similarity=None for empty regions."""
    engine = GridDiffEngine()
    
    # Create two empty regions
    reg_a = PatternRegion(
        bounding_box=(0,0,1,1),
        pattern=[[0,0],[0,0]],
        center=(0.5, 0.5),
        color_palette=set(),
        size=0,
        location_hint="center"
    )
    reg_b = reg_a # same
    
    comp = engine.compare_regions(reg_a, reg_b)
    assert comp.similarity is None
    assert comp.description == "no foreground"

@pytest.mark.asyncio
async def test_grid_analysis_no_longer_hardcodes_one_on_color_shift():
    """A050: Verify that color-shifted matches use the real cell ratio, not 1.0."""
    engine = GridDiffEngine()
    
    # 1x1 regions with different colors
    reg_a = PatternRegion(
        bounding_box=(0,0,0,0),
        pattern=[[1]], # Blue
        center=(0,0),
        color_palette={1},
        size=1,
        location_hint="center"
    )
    reg_b = PatternRegion(
        bounding_box=(0,0,0,0),
        pattern=[[2]], # Red
        center=(0,0),
        color_palette={2},
        size=1,
        location_hint="center"
    )
    
    # These are structurally identical but color-shifted.
    # Pixels don't match (0/1), so similarity should be 0.0, but color_shifted=True.
    comp = engine.compare_regions(reg_a, reg_b)
    assert comp.similarity == 0.0
    assert comp.color_shifted is True
    assert comp.exact_match is False

@pytest.mark.asyncio
async def test_hybrid_matcher_prevents_step_0_finish_mode():
    """A050: Verify that finish mode is not entered at step 0."""
    brain = MagicMock()
    matcher = HybridPatternMatcher(brain)
    
    grid = [[0, 1], [0, 0]]
    # Step 0 always returns discover
    evidence = await matcher.update(grid, step=0, session_id="s", task_id="t", archetype="a")
    assert evidence.phase == "discover"
    assert evidence.finish_mode_allowed is False

@pytest.mark.asyncio
async def test_hybrid_matcher_requires_multi_channel_agreement():
    """A050: Verify that a single channel spike is insufficient for finish mode."""
    brain = MagicMock()
    # Mock brain tools to return nothing (degraded/unavailable)
    brain.recall_lessons = AsyncMock(return_value={"lessons": []})
    brain.analogical_search = AsyncMock(return_value={"results": []})
    
    config = HybridPatternConfig(
        min_step_for_finish=1,
        min_confidence_for_finish=0.6,
        min_local_progress_for_finish=0.7
    )
    matcher = HybridPatternMatcher(brain, config=config)
    
    # 1. Step 0: small initial foreground
    await matcher.update([[1,0]], step=0, session_id="s", task_id="t", archetype="a")
    
    # 2. Step 1: unchanged grid should produce low local progress (near 0.0)
    evidence = await matcher.update([[1,0]], step=1, session_id="s", task_id="t", archetype="a")
    
    assert evidence.local_progress < 0.1
    assert evidence.combined_confidence < 0.5  # single-channel / low-evidence
    assert evidence.finish_mode_allowed is False
    assert evidence.phase in ("discover", "intermediate")


@pytest.mark.asyncio
async def test_hybrid_matcher_finish_on_prior_corroboration():
    """A050: Verify that prior channel can trigger finish mode when corroborated."""
    brain = MagicMock()
    brain.memory_degraded = False
    brain.recall_lessons = AsyncMock(return_value={"lessons": []})
    brain.analogical_search = AsyncMock(return_value={"results": []})
    
    # Mock sibling tool: high expected progress
    brain.recall_scene_graph_priors = AsyncMock(return_value={
        "status": "success",
        "expected_progress": 0.95,
        "evidence_count": 3
    })
    
    config = HybridPatternConfig(
        min_step_for_finish=1,
        min_confidence_for_finish=0.0 # Ignore confidence scaling for this corroboration test
    )
    matcher = HybridPatternMatcher(brain, config=config)
    
    # 1. Step 0: bootstrap (small foreground)
    await matcher.update([[1,0]], step=0, session_id="s", task_id="t", archetype="a")
    
    # 2. Step 1: Change structure to trigger hash update and re-query
    evidence = await matcher.update([[1,0,2]], step=1, session_id="s", task_id="t", archetype="a")
    
    assert evidence.graph_prior_score == 0.95
    assert evidence.local_progress is not None
    assert evidence.finish_mode_allowed is True
    assert evidence.phase == "finish"

@pytest.mark.asyncio
async def test_hybrid_matcher_respects_skip_memory_gate():
    """A059: Verify that skip_memory=True prevents expensive calls."""
    brain = MagicMock()
    brain.memory_degraded = False
    brain.recall_lessons = AsyncMock(return_value={"lessons": []})
    brain.analogical_search = AsyncMock(return_value={"results": []})
    brain.recall_scene_graph_priors = AsyncMock(return_value={"status": "success"})
    
    matcher = HybridPatternMatcher(brain)
    
    # 1. Step 0: bootstrap
    await matcher.update([[1,0]], step=0, session_id="s", task_id="t", archetype="a")
    
    # 2. Step 1: Change grid but pass skip_memory=True
    evidence = await matcher.update([[1,1]], step=1, session_id="s", task_id="t", archetype="a", skip_memory=True)
    
    # Verify brain was NOT called for memory (recall_lessons is skipped at step 0 and step 1)
    assert brain.recall_lessons.call_count == 0
    assert brain.analogical_search.call_count == 0
    assert brain.recall_scene_graph_priors.call_count == 0
    
    # 3. Step 2: Change grid and pass skip_memory=False (default)
    evidence2 = await matcher.update([[1,2]], step=2, session_id="s", task_id="t", archetype="a")
    
    # Now it should have been called (primary + potentially fallback archetype query)
    assert brain.recall_lessons.call_count >= 1

@pytest.mark.asyncio
async def test_hybrid_matcher_fuse_includes_prior():
    """A050: Verify that _fuse correctly incorporates the prior channel."""
    brain = MagicMock()
    matcher = HybridPatternMatcher(brain)
    
    # 1. Fuse with local and prior
    # local=0.8, text=None, vector=None, prior=0.9
    # w_local=0.4, w_prior=0.2. Sum weights = 0.6
    # combined = (0.8*0.4 + 0.9*0.2) / 0.6 = (0.32 + 0.18) / 0.6 = 0.5 / 0.6 = 0.8333
    combined, confidence, agreement = matcher._fuse(0.8, None, None, 0.9)
    
    assert combined == pytest.approx(0.833333)
    assert agreement == pytest.approx(0.1)
    # confidence = (1.0 - 0.1) * (2/4) = 0.9 * 0.5 = 0.45
    assert confidence == pytest.approx(0.45)
