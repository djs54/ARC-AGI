import pytest
from agents.arc3.grid_analysis import GridDiffEngine, PatternRegion, RegionComparison
from agents.arc3.solver import PatternMatchTracker, RoleType, ObjectRoleMapper, ObjectRole
from agents.arc3.orchestrator import ARCOrchestrator
from unittest.mock import MagicMock, AsyncMock

class TestRegionComparison:
    def test_exact_match(self):
        engine = GridDiffEngine()
        a = PatternRegion(
            bounding_box=(0,0,2,2),
            pattern=[[1,2,1],[2,0,2],[1,2,1]],
            center=(1,1),
            color_palette={1,2,0},
            size=8,
            location_hint="center"
        )
        b = PatternRegion(
            bounding_box=(10,10,12,12),
            pattern=[[1,2,1],[2,0,2],[1,2,1]],
            center=(11,11),
            color_palette={1,2,0},
            size=8,
            location_hint="edge_bottom"
        )
        result = engine.compare_regions(a, b)
        assert result.exact_match is True
        assert result.similarity == 1.0
        assert result.description == "exact match"

    def test_partial_match(self):
        engine = GridDiffEngine()
        a = PatternRegion(
            bounding_box=(0,0,2,2),
            pattern=[[1,2,1],[2,0,2],[1,2,1]],
            center=(1,1),
            color_palette={1,2,0},
            size=8,
            location_hint="center"
        )
        b = PatternRegion(
            bounding_box=(0,0,2,2),
            pattern=[[1,2,1],[2,3,2],[1,2,1]],  # One cell different (0 -> 3)
            center=(1,1),
            color_palette={1,2,3},
            size=9,
            location_hint="center"
        )
        # Disable color shift to test partial matching
        result = engine.compare_regions(a, b, allow_color_shift=False)
        assert result.exact_match is False
        assert 0.8 < result.similarity < 1.0
        assert "partial match" in result.description

    def test_color_shifted_match(self):
        engine = GridDiffEngine()
        a = PatternRegion(
            bounding_box=(0,0,1,1),
            pattern=[[1,2],[2,1]],
            center=(0.5,0.5),
            color_palette={1,2},
            size=4,
            location_hint="center"
        )
        b = PatternRegion(
            bounding_box=(0,0,1,1),
            pattern=[[3,4],[4,3]],  # 1->3, 2->4
            center=(0.5,0.5),
            color_palette={3,4},
            size=4,
            location_hint="center"
        )
        result = engine.compare_regions(a, b, allow_color_shift=True)
        assert result.similarity == 1.0
        assert result.color_shift == {1: 3, 2: 4}
        assert result.description == "color-shifted match"

    def test_size_mismatch_small(self):
        """B168: Small size differences (<=2) use overlap comparison instead of rejecting."""
        engine = GridDiffEngine()
        a = PatternRegion(pattern=[[1,1],[1,1]], bounding_box=(0,0,1,1), center=(0.5,0.5), color_palette={1}, size=4, location_hint="center")
        b = PatternRegion(pattern=[[1,1,1],[1,1,1]], bounding_box=(0,0,1,2), center=(0.5,1.0), color_palette={1}, size=6, location_hint="center")
        result = engine.compare_regions(a, b)
        assert result.similarity > 0.5  # Overlap region matches, extra column penalized
        assert "overlap" in result.description

    def test_size_mismatch_large(self):
        """B168: Large size differences (>2) still return 0.0."""
        engine = GridDiffEngine()
        a = PatternRegion(pattern=[[1,1],[1,1]], bounding_box=(0,0,1,1), center=(0.5,0.5), color_palette={1}, size=4, location_hint="center")
        b = PatternRegion(pattern=[[1]*5]*5, bounding_box=(0,0,4,4), center=(2,2), color_palette={1}, size=25, location_hint="center")
        result = engine.compare_regions(a, b)
        assert result.similarity == 0.0
        assert result.description == "size mismatch"

class TestReferenceGoalPairing:
    def test_corner_region_is_reference(self):
        engine = GridDiffEngine()
        regions = [
            PatternRegion(bounding_box=(60,0,63,3), pattern=[[0]*4]*4, center=(61.5, 1.5), color_palette={1}, size=16, location_hint="corner_bl"),
            PatternRegion(bounding_box=(30,30,33,33), pattern=[[0]*4]*4, center=(31.5, 31.5), color_palette={1}, size=16, location_hint="center"),
        ]
        pair = engine.find_reference_goal_pair(regions, 64, 64)
        assert pair is not None
        ref, goal = pair
        assert ref.location_hint == "corner_bl"
        assert goal.location_hint == "center"

    def test_pairing_prefers_structurally_matching_bottom_left_reference(self):
        engine = GridDiffEngine()
        regions = [
            PatternRegion(
                bounding_box=(0, 7, 2, 9),
                pattern=[[8, 8, 0], [0, 8, 8], [8, 0, 8]],
                center=(1.0, 8.0),
                color_palette={8},
                size=6,
                location_hint="corner_tr",
            ),
            PatternRegion(
                bounding_box=(7, 0, 9, 2),
                pattern=[[2, 2, 0], [0, 2, 2], [2, 0, 2]],
                center=(8.0, 1.0),
                color_palette={2},
                size=6,
                location_hint="corner_bl",
            ),
            PatternRegion(
                bounding_box=(3, 4, 5, 6),
                pattern=[[5, 5, 0], [0, 5, 5], [5, 0, 5]],
                center=(4.0, 5.0),
                color_palette={5},
                size=6,
                location_hint="center",
            ),
        ]
        ref, goal = engine.find_reference_goal_pair(regions, 10, 10)
        assert ref.location_hint == "corner_bl"
        assert goal.location_hint == "center"

class TestPatternMatchTracker:
    def test_tracker_lifecycle(self):
        tracker = PatternMatchTracker()
        
        # 10x10 grid with reference at BL and goal at center
        grid = [[0]*10 for _ in range(10)]
        # Reference (7 cells of color 2 at BL)
        grid[7][0]=2; grid[7][1]=2
        grid[8][0]=2; grid[8][1]=2; grid[8][2]=2
        grid[9][1]=2; grid[9][2]=2
        
        # Goal (8 cells of color 1 at center)
        grid[4][4]=1; grid[4][5]=1; grid[4][6]=1
        grid[5][4]=1;               grid[5][6]=1
        grid[6][4]=1; grid[6][5]=1; grid[6][6]=1
        
        # Step 0: Discovery
        state = tracker.update(grid, 0)
        assert state["phase"] == "intermediate"
        assert state["reference_location"] == "corner_bl"
        assert state["goal_location"] == "center"
        assert state["similarity"] < 0.5
        
        # Step 1: Change goal to match reference structure (color 2)
        # First clear old goal
        for r in range(4, 7):
            for c in range(4, 7):
                grid[r][c] = 0
        
        grid[4][4]=2; grid[4][5]=2
        grid[5][4]=2; grid[5][5]=2; grid[5][6]=2
        grid[6][5]=2; grid[6][6]=2
        
        state = tracker.update(grid, 1)
        assert state["phase"] == "finish"
        assert state["similarity"] == 1.0

class TestIntermediateDiscovery:
    def test_intermediate_role_assignment(self):
        mapper = ObjectRoleMapper()
        # Mock grid with a small stationary object
        grid = [[0]*10 for _ in range(10)]
        grid[2][2] = 5; grid[2][3] = 5 # 2-cell object of color 5
        
        observation = {
            "colors": [{"value": 5, "count": 2}],
            "grid": grid
        }
        hypothesis_context = {}
        
        # First update seeds it
        mapper.update(hypothesis_context, observation, 0)
        # Second update confirms it's stationary and assigns intermediate
        roles = mapper.update(hypothesis_context, observation, 1)
        
        assert 5 in roles
        assert roles[5].role == RoleType.INTERMEDIATE
        assert roles[5].confidence == 0.45

class TestOrchestratorB167:
    @pytest.mark.asyncio
    async def test_save_recall_puzzle_model(self):
        brain = MagicMock()
        brain.report_outcome = AsyncMock()
        brain.notify_turn = AsyncMock()
        brain.current_truth = AsyncMock(return_value={"results": [{"text": "lesson", "evidence": {"type": "puzzle_model"}}]})
        
        orchestrator = ARCOrchestrator(brain, MagicMock(), "session-1", MagicMock(), {})
        orchestrator._current_level = 1
        
        # Mock some state - B169: Use solve_engine._object_roles
        orchestrator.solve_engine._object_roles = {
            5: ObjectRole(color_id=5, role=RoleType.INTERMEDIATE, confidence=0.8, estimated_position={"row": 2, "col": 2})
        }
        orchestrator._visited_intermediates.add((2,2))
        
        # Save model at end of level 1
        await orchestrator._on_level_transition(1, [])
        
        assert brain.report_outcome.called
        assert brain.notify_turn.called
        args, kwargs = brain.report_outcome.call_args
        evidence = kwargs["evidence"]
        assert evidence["type"] == "puzzle_model"
        assert evidence["grid_structure"]["intermediate_count"] == 1
        
        # Recall model at start of level 2
        orchestrator._current_level = 2
        # Reset tracker to discover phase
        orchestrator._pattern_tracker.phase = "discover"
        
        await orchestrator._recall_puzzle_model()
        assert orchestrator._pattern_tracker.phase == "intermediate"
        assert brain.current_truth.called

    def test_autopilot_phase_aware(self):
        orchestrator = ARCOrchestrator(MagicMock(), MagicMock(), "session-1", MagicMock(), {})
        
        # Mock pattern tracker update to return expected state
        orchestrator._pattern_tracker.update = MagicMock(return_value={
            "phase": "intermediate",
            "similarity": 0.5
        })
        
        # Mock solve context with player and intermediate
        orchestrator._solve_context = {
            "object_roles": {
                "1": {"role": "player", "confidence": 0.9, "estimated_position": {"row": 0.0, "col": 0.0}},
                "5": {"role": "intermediate", "confidence": 0.8, "estimated_position": {"row": 5.0, "col": 5.0}}
            }
        }
        
        # Step 0: should target intermediate
        action = orchestrator._try_autopilot({"grid": [[0]*10 for _ in range(10)]}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert action is not None
        assert "intermediate" in action["rationale"]
        # Moving from 0,0 to 5,5 -> should move down (ACTION2) or right (ACTION4)
        assert action["action_id"] in ("ACTION2", "ACTION4")
        
        # If we are near intermediate, should interact
        orchestrator._solve_context["object_roles"]["1"]["estimated_position"] = {"row": 4.1, "col": 4.1}
        action = orchestrator._try_autopilot({"grid": [[0]*10 for _ in range(10)]}, ["ACTION5"])
        assert action is not None
        assert action["action_id"] == "ACTION5"
        assert (5, 5) in orchestrator._visited_intermediates
        
        # After visit, if phase becomes finish, should target goal
        orchestrator._pattern_tracker.update.return_value = {
            "phase": "finish",
            "similarity": 1.0
        }
        orchestrator._solve_context["object_roles"]["7"] = {"role": "goal", "confidence": 0.9, "estimated_position": {"row": 9.0, "col": 9.0}}
        
        action = orchestrator._try_autopilot({"grid": [[0]*10 for _ in range(10)]}, ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5"])
        assert action is not None
        assert "finish" in action["rationale"]
        assert action["action_id"] in ("ACTION2", "ACTION4")
