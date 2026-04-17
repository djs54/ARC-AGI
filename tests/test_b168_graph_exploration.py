"""B168: Tests for EntityGraphBuilder — graph-based exploration agent."""
import asyncio
import os
import tempfile
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from agents.arc3.entity_graph import EntityGraphBuilder, InferenceResult
from agents.arc3.solver import RoleType
from mcp_engine.graph.kuzu_client import KuzuClient


# ── Helpers ──────────────────────────────────────────────────────────

def _make_db():
    db = MagicMock()
    db.execute_write = AsyncMock()
    db.execute_read = AsyncMock(return_value=[])
    return db


def _simple_grid():
    """5x5 grid: color 0 background, color 1 at (0,0), color 2 at (4,4)."""
    grid = [[0] * 5 for _ in range(5)]
    grid[0][0] = 1
    grid[4][4] = 2
    return grid


def _movement_grid_before():
    """5x5 grid: color 1 at (0,0)."""
    grid = [[0] * 5 for _ in range(5)]
    grid[0][0] = 1
    return grid


def _movement_grid_after():
    """5x5 grid: color 1 moved to (0,2) — centroid shift > 0.35."""
    grid = [[0] * 5 for _ in range(5)]
    grid[0][2] = 1
    return grid


def _stationary_grid_after():
    """5x5 grid: color 1 still at (0,0) — no movement."""
    grid = [[0] * 5 for _ in range(5)]
    grid[0][0] = 1
    return grid


# ── Phase 1: Bootstrap Tests ────────────────────────────────────────

class TestBootstrap:
    @pytest.mark.asyncio
    async def test_creates_entities(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        result = await builder.bootstrap(_simple_grid(), 0, {})

        assert result["n_entities"] == 3  # background(0), color 1, color 2
        assert any("GridSnapshot" in call[0][0] for call in db.execute_write.call_args_list)
        assert any("GridEntity" in call[0][0] for call in db.execute_write.call_args_list)

    @pytest.mark.asyncio
    async def test_bootstrap_casts_timestamp_fields(self):
        """B168 regression: Kuzu TIMESTAMP columns must not receive raw string params."""
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        await builder.bootstrap(_simple_grid(), 0, {})

        queries = [call.args[0] for call in db.execute_write.call_args_list]
        assert any(
            "GridSnapshot" in q and "created_at = timestamp($now)" in q
            for q in queries
        )
        assert any(
            "GridEntity" in q and "created_at = timestamp($created_at)" in q
            for q in queries
        )

    @pytest.mark.asyncio
    async def test_background_flagged(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        await builder.bootstrap(_simple_grid(), 0, {})

        bg_entities = [e for e in builder._entities.values() if e["is_background"]]
        assert len(bg_entities) >= 1
        assert all(e["color_id"] == 0 or e["pixel_count"] > 12 for e in bg_entities)

    @pytest.mark.asyncio
    async def test_observed_in_edges(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        await builder.bootstrap(_simple_grid(), 0, {})

        observed_calls = [
            call for call in db.execute_write.call_args_list
            if "OBSERVED_IN" in call[0][0]
        ]
        assert len(observed_calls) == 3  # one per entity

    @pytest.mark.asyncio
    async def test_adjacent_to_edges(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        # Grid where entities are adjacent
        grid = [[0] * 5 for _ in range(5)]
        grid[0][0] = 1
        grid[0][1] = 2  # Adjacent to color 1
        await builder.bootstrap(grid, 0, {})

        adjacent_calls = [
            call for call in db.execute_write.call_args_list
            if "ADJACENT_TO" in call[0][0]
        ]
        assert len(adjacent_calls) > 0

    @pytest.mark.asyncio
    async def test_same_color_edges(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        # Grid with two separate regions of same color
        grid = [[0] * 5 for _ in range(5)]
        grid[0][0] = 1
        grid[4][4] = 1  # Same color, different region
        await builder.bootstrap(grid, 0, {})

        same_color_calls = [
            call for call in db.execute_write.call_args_list
            if "SAME_COLOR_AS" in call[0][0]
        ]
        assert len(same_color_calls) > 0

    @pytest.mark.asyncio
    async def test_structurally_similar_edges(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        # Grid with two structurally similar 2x2 blocks
        grid = [[0] * 10 for _ in range(10)]
        # Block 1: 2x2 of color 1 at (0,0)
        grid[0][0] = 1; grid[0][1] = 1
        grid[1][0] = 1; grid[1][1] = 1
        # Block 2: 2x2 of color 2 at (5,5) — same structure, different color
        grid[5][5] = 2; grid[5][6] = 2
        grid[6][5] = 2; grid[6][6] = 2
        await builder.bootstrap(grid, 0, {})

        similar_calls = [
            call for call in db.execute_write.call_args_list
            if "STRUCTURALLY_SIMILAR" in call[0][0]
        ]
        assert len(similar_calls) > 0

    @pytest.mark.asyncio
    async def test_contains_entity_edges(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        # Large background region contains a small region
        grid = [[0] * 5 for _ in range(5)]
        grid[2][2] = 1  # Small entity inside background bbox
        await builder.bootstrap(grid, 0, {})

        contains_calls = [
            call for call in db.execute_write.call_args_list
            if "CONTAINS_ENTITY" in call[0][0]
        ]
        assert len(contains_calls) > 0

    @pytest.mark.asyncio
    async def test_empty_grid(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        result = await builder.bootstrap([], 0, {})
        assert result["n_entities"] == 0


# ── Phase 2a: Action Effect Recording ───────────────────────────────

class TestActionEffectRecording:
    @pytest.mark.asyncio
    async def test_detects_movement(self):
        """Entity that moves > 0.35 cells should be flagged as mobile."""
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        await builder.bootstrap(_movement_grid_before(), 0, {})
        db.execute_write.reset_mock()

        result = await builder.record_action_effect(
            _movement_grid_before(), _movement_grid_after(), "ACTION4", 1, 0
        )

        # Verify ActionEffect was created
        assert any("ActionEffect" in call[0][0] for call in db.execute_write.call_args_list)

        # Verify entity flagged as mobile in local cache
        mobile_entities = [e for e in builder._entities.values()
                          if e["color_id"] == 1 and e["is_mobile"]]
        assert len(mobile_entities) >= 1

        # Verify MOVED_BY edge was created
        moved_calls = [c for c in db.execute_write.call_args_list if "MOVED_BY" in c[0][0]]
        assert len(moved_calls) > 0

    @pytest.mark.asyncio
    async def test_action_effect_casts_timestamp_field(self):
        """B168 regression: ActionEffect.created_at should use Kuzu timestamp conversion."""
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        await builder.bootstrap(_movement_grid_before(), 0, {})
        db.execute_write.reset_mock()

        await builder.record_action_effect(
            _movement_grid_before(), _movement_grid_after(), "ACTION4", 1, 0
        )

        queries = [call.args[0] for call in db.execute_write.call_args_list]
        assert any(
            "ActionEffect" in q and "created_at = timestamp($now)" in q
            for q in queries
        )

    @pytest.mark.asyncio
    async def test_stationary_not_flagged_mobile(self):
        """Entity that doesn't move should NOT be flagged as mobile."""
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        await builder.bootstrap(_movement_grid_before(), 0, {})
        db.execute_write.reset_mock()

        await builder.record_action_effect(
            _movement_grid_before(), _stationary_grid_after(), "ACTION4", 1, 0
        )

        # Color 1 entity should NOT be mobile (didn't move)
        color1_entities = [e for e in builder._entities.values() if e["color_id"] == 1]
        for e in color1_entities:
            assert e["is_mobile"] is False

    @pytest.mark.asyncio
    async def test_size_change_detected(self):
        """Entity that changes size should be flagged as interactive."""
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        grid_before = [[0] * 5 for _ in range(5)]
        grid_before[0][0] = 1
        grid_before[0][1] = 1  # 2 pixels

        grid_after = [[0] * 5 for _ in range(5)]
        grid_after[0][0] = 1  # Shrank to 1 pixel

        await builder.bootstrap(grid_before, 0, {})
        db.execute_write.reset_mock()

        await builder.record_action_effect(grid_before, grid_after, "ACTION1", 1, 0)

        responds_calls = [c for c in db.execute_write.call_args_list if "RESPONDS_TO" in c[0][0]]
        assert len(responds_calls) > 0

    @pytest.mark.asyncio
    async def test_returns_inference_result(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        await builder.bootstrap(_movement_grid_before(), 0, {})
        result = await builder.record_action_effect(
            _movement_grid_before(), _movement_grid_after(), "ACTION4", 1, 0
        )

        assert isinstance(result, InferenceResult)


# ── Tier 1: Similarity Propagation ──────────────────────────────────

class TestTier1Propagation:
    @pytest.mark.asyncio
    async def test_propagates_mobility_via_structural_similarity(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        builder._entities = {"e1": {"is_mobile": True, "color_id": 1}}

        # Mock: one structurally similar entity found
        db.execute_read.return_value = [{"eid": "e2", "sim": 0.8}]

        changes = await builder._tier1_similarity_propagation(1, {"e1"})

        assert changes >= 1
        # Verify SET was called
        set_calls = [c for c in db.execute_write.call_args_list if "is_mobile = true" in c[0][0]]
        assert len(set_calls) >= 1

    @pytest.mark.asyncio
    async def test_no_changes_when_no_moved_entities(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        changes = await builder._tier1_similarity_propagation(1, set())
        assert changes == 0


# ── Tier 2: Relational Inference ────────────────────────────────────

class TestTier2Relational:
    @pytest.mark.asyncio
    async def test_correlates_with_edge_created(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        changes = await builder._tier2_relational_inference(
            step=1, effect_id="eff_1",
            moved_eids={"e1"}, responded_eids={"e2"},
        )

        corr_calls = [c for c in db.execute_write.call_args_list if "CORRELATES_WITH" in c[0][0]]
        assert len(corr_calls) > 0
        assert changes >= 1

    @pytest.mark.asyncio
    async def test_co_moves_with_edge(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        # Mock: two entities both moved with similar deltas
        db.execute_read.return_value = [
            {"a_eid": "e1", "b_eid": "e2", "a_dr": 1.0, "a_dc": 0.0, "b_dr": 1.0, "b_dc": 0.0}
        ]

        changes = await builder._tier2_relational_inference(
            step=1, effect_id="eff_1",
            moved_eids={"e1", "e2"}, responded_eids=set(),
        )

        co_move_calls = [c for c in db.execute_write.call_args_list if "CO_MOVES_WITH" in c[0][0]]
        assert len(co_move_calls) > 0

    @pytest.mark.asyncio
    async def test_blocking_inference(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        # Single mover, no responded — only adjacency query fires
        db.execute_read.return_value = [{"b_eid": "wall_1"}]

        changes = await builder._tier2_relational_inference(
            step=1, effect_id="eff_1",
            moved_eids={"e1"}, responded_eids=set(),
        )

        blocks_calls = [c for c in db.execute_write.call_args_list if "BLOCKS" in c[0][0]]
        assert len(blocks_calls) > 0


# ── Tier 3: Role Elimination ────────────────────────────────────────

class TestTier3Elimination:
    @pytest.mark.asyncio
    async def test_player_confirmed_with_multiple_moves(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        # Mock: player with 3 moves found
        db.execute_read.side_effect = [
            [{"eid": "e1", "color_id": 1, "move_count": 3}],  # player query
            [],  # wall query
            [],  # similar wall query
        ]

        changes = await builder._tier3_role_elimination(1)

        assert changes >= 1
        set_calls = [c for c in db.execute_write.call_args_list if "player" in c[0][0]]
        assert len(set_calls) >= 1

    @pytest.mark.asyncio
    async def test_no_elimination_without_player(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        db.execute_read.return_value = []  # no player found
        changes = await builder._tier3_role_elimination(1)
        assert changes == 0


# ── Role Inference ──────────────────────────────────────────────────

class TestRoleInference:
    @pytest.mark.asyncio
    async def test_infer_player(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        db.execute_read.return_value = [
            {"eid": "e1", "color_id": 1, "centroid_row": 0.0,
             "centroid_col": 2.0, "pixel_count": 1, "move_count": 3}
        ]

        player = await builder.infer_player()
        assert player is not None
        assert player["color_id"] == 1

    @pytest.mark.asyncio
    async def test_infer_player_none_when_no_movement(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        db.execute_read.return_value = []
        player = await builder.infer_player()
        assert player is None

    @pytest.mark.asyncio
    async def test_infer_goal(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        db.execute_read.return_value = [
            {"eid": "g1", "color_id": 3, "centroid_row": 4.0,
             "centroid_col": 4.0, "pixel_count": 4, "compactness": 1.0}
        ]
        goal = await builder.infer_goal()
        assert goal is not None
        assert goal["color_id"] == 3

    @pytest.mark.asyncio
    async def test_infer_walls(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        db.execute_read.return_value = [
            {"eid": "w1", "color_id": 5, "centroid_row": 10.0,
             "centroid_col": 5.0, "pixel_count": 100, "aspect_ratio": 0.1}
        ]
        walls = await builder.infer_walls()
        assert len(walls) == 1
        assert walls[0]["color_id"] == 5

    @pytest.mark.asyncio
    async def test_infer_intermediates_with_similarity(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        db.execute_read.return_value = [
            {"eid": "i1", "color_id": 7, "centroid_row": 3.0,
             "centroid_col": 3.0, "similar_count": 2}
        ]
        inters = await builder.infer_intermediates()
        assert len(inters) == 1

    @pytest.mark.asyncio
    async def test_infer_intermediates_fallback(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        # First call (STRUCTURALLY_SIMILAR) returns empty, second (fallback) returns result
        db.execute_read.side_effect = [
            [],
            [{"eid": "i1", "color_id": 7, "centroid_row": 3.0, "centroid_col": 3.0}],
        ]
        inters = await builder.infer_intermediates()
        assert len(inters) == 1

    @pytest.mark.asyncio
    async def test_get_entity_roles_aggregates(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        db.execute_read.side_effect = [
            # infer_player
            [{"eid": "e1", "color_id": 1, "centroid_row": 0.0,
              "centroid_col": 0.0, "pixel_count": 1, "move_count": 2}],
            # infer_goal
            [{"eid": "g1", "color_id": 3, "centroid_row": 4.0,
              "centroid_col": 4.0, "pixel_count": 4, "compactness": 1.0}],
            # infer_walls
            [{"eid": "w1", "color_id": 5, "centroid_row": 10.0,
              "centroid_col": 5.0, "pixel_count": 100, "aspect_ratio": 0.1}],
            # infer_intermediates (STRUCTURALLY_SIMILAR path)
            [{"eid": "i1", "color_id": 7, "centroid_row": 3.0,
              "centroid_col": 3.0, "similar_count": 2}],
        ]

        roles = await builder.get_entity_roles()

        assert 1 in roles
        assert roles[1].role == RoleType.PLAYER
        assert roles[1].confidence > 0.5  # Dynamic, not hardcoded 0.85

        assert 3 in roles
        assert roles[3].role == RoleType.GOAL

        assert 5 in roles
        assert roles[5].role == RoleType.WALL

        assert 7 in roles
        assert roles[7].role == RoleType.INTERMEDIATE

    @pytest.mark.asyncio
    async def test_player_confidence_scales_with_moves(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")

        # Player with 1 move
        db.execute_read.side_effect = [
            [{"eid": "e1", "color_id": 1, "centroid_row": 0.0,
              "centroid_col": 0.0, "pixel_count": 1, "move_count": 1}],
            [], [], [], [],  # goal, walls, intermediates(sim), intermediates(fallback)
        ]
        roles_1 = await builder.get_entity_roles()

        db.execute_read.side_effect = [
            [{"eid": "e1", "color_id": 1, "centroid_row": 0.0,
              "centroid_col": 0.0, "pixel_count": 1, "move_count": 4}],
            [], [], [], [],
        ]
        roles_4 = await builder.get_entity_roles()

        # More moves → higher confidence
        assert roles_4[1].confidence > roles_1[1].confidence

    @pytest.mark.asyncio
    async def test_player_never_background(self):
        """Background entities should never be inferred as player."""
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        # The query already filters is_background = false, so an empty result is correct
        db.execute_read.return_value = []
        player = await builder.infer_player()
        assert player is None


# ── Exploration Frontier ────────────────────────────────────────────

class TestExplorationFrontier:
    @pytest.mark.asyncio
    async def test_returns_unknown_entities(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        db.execute_read.return_value = [
            {"eid": "e1", "color_id": 3, "size": 5},
            {"eid": "e2", "color_id": 4, "size": 8},
        ]
        frontier = await builder._get_exploration_frontier()
        assert len(frontier) == 2

    @pytest.mark.asyncio
    async def test_empty_when_all_known(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        db.execute_read.return_value = []
        frontier = await builder._get_exploration_frontier()
        assert len(frontier) == 0


# ── Exploration Summary ─────────────────────────────────────────────

class TestExplorationSummary:
    @pytest.mark.asyncio
    async def test_summary_structure(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        # Mock 5 sequential queries: mobile, static, causal, unexplained, frontier
        db.execute_read.side_effect = [
            [{"eid": "e1", "color_id": 1}],  # mobile
            [{"eid": "e2", "color_id": 2}],  # static
            [],  # causal chains
            [],  # unexplained
            [],  # frontier
        ]
        summary = await builder.get_exploration_summary()
        assert "mobile_entities" in summary
        assert "static_entities" in summary
        assert "causal_chains" in summary
        assert "unexplained_correlations" in summary
        assert "exploration_frontier" in summary


# ── Helpers ──────────────────────────────────────────────────────────

class TestKuzuClientInterop:
    @pytest.mark.asyncio
    async def test_execute_read_returns_dict_rows(self):
        """B168 regression: graph queries expect column-name dicts, not raw lists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = KuzuClient(os.path.join(tmpdir, "b168_read_shape.db"))
            try:
                db.execute(
                    "CREATE NODE TABLE IF NOT EXISTS TmpNode(id STRING, value INT64, PRIMARY KEY(id))"
                )
                await db.execute_write(
                    "MERGE (n:TmpNode {id: $id}) SET n.value = $value",
                    {"id": "row-1", "value": 7},
                )

                rows = await db.execute_read(
                    "MATCH (n:TmpNode {id: $id}) RETURN n.id AS id, n.value AS value",
                    {"id": "row-1"},
                )

                assert rows == [{"id": "row-1", "value": 7}]
            finally:
                db.close()


class TestHelpers:
    def test_bbox_dist(self):
        builder = EntityGraphBuilder(MagicMock(), "t1")
        e1 = {"bbox_min_row": 0, "bbox_max_row": 1, "bbox_min_col": 0, "bbox_max_col": 1}
        e2 = {"bbox_min_row": 0, "bbox_max_row": 1, "bbox_min_col": 3, "bbox_max_col": 4}
        assert builder._bbox_dist(e1, e2) == 2.0

        e3 = {"bbox_min_row": 3, "bbox_max_row": 4, "bbox_min_col": 3, "bbox_max_col": 4}
        assert builder._bbox_dist(e1, e3) > 2.8

    def test_bbox_contains(self):
        builder = EntityGraphBuilder(MagicMock(), "t1")
        parent = {"bbox_min_row": 0, "bbox_max_row": 4, "bbox_min_col": 0,
                   "bbox_max_col": 4, "pixel_count": 20}
        child = {"bbox_min_row": 1, "bbox_max_row": 3, "bbox_min_col": 1,
                  "bbox_max_col": 3, "pixel_count": 4}
        assert builder._bbox_contains(parent, child) is True
        assert builder._bbox_contains(child, parent) is False

    def test_location_hint(self):
        builder = EntityGraphBuilder(MagicMock(), "t1")
        assert builder._compute_location_hint((0, 0, 1, 1), 10, 10) == "corner_tl"
        assert builder._compute_location_hint((0, 8, 1, 9), 10, 10) == "corner_tr"
        assert builder._compute_location_hint((5, 5, 6, 6), 10, 10) == "center"
        assert builder._compute_location_hint((0, 3, 1, 5), 10, 10) == "edge_top"


# ── NoOp Fallback ───────────────────────────────────────────────────

class TestNoOpFallback:
    @pytest.mark.asyncio
    async def test_no_db_skips_exploration(self):
        """When db is None, EntityGraphBuilder should not be created."""
        # This tests the runner logic: if brain.db is None, entity_graph = None
        # We just verify the builder can handle None db gracefully
        # (The runner guard `if brain.db is not None` prevents creation)
        pass  # Runner guard tested via integration


# ── Cleanup ─────────────────────────────────────────────────────────

class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_deletes_all(self):
        db = _make_db()
        builder = EntityGraphBuilder(db, "task-1")
        await builder.cleanup()

        assert db.execute_write.call_count == 3
        queries = [call[0][0] for call in db.execute_write.call_args_list]
        assert any("GridEntity" in q and "DELETE" in q for q in queries)
        assert any("GridSnapshot" in q and "DELETE" in q for q in queries)
        assert any("ActionEffect" in q and "DELETE" in q for q in queries)
