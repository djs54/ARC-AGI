import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.arc3.hypothesis import ActionFact, Hypothesis, HypothesisManager
from mcp_engine.schema import NODE_TABLES, REL_TABLES


def _make_manager(db=None):
    mgr = HypothesisManager(MagicMock(), "session-1")
    mgr._task_id = "task-1"
    mgr._current_level = 2
    if db is not None:
        mock_eg = MagicMock()
        mock_eg.db = db
        mgr._entity_graph = mock_eg
    return mgr


def test_actionfact_schema_contains_required_fields():
    assert "ActionFact" in NODE_TABLES
    ddl = NODE_TABLES["ActionFact"]

    for field_name in [
        "fact_id",
        "task_id",
        "level",
        "action_id",
        "effect_description",
        "delta_row",
        "delta_col",
        "n_cells_changed",
        "confidence",
        "observation_count",
        "created_at",
    ]:
        assert field_name in ddl

    assert any("DERIVED_FROM_FACT" in rel for rel in REL_TABLES)
    assert any("SUPPORTS_HYPOTHESIS" in rel for rel in REL_TABLES)


@pytest.mark.asyncio
async def test_persist_action_fact_writes_node_and_relationships():
    db = MagicMock()
    db.execute_write = AsyncMock()

    mgr = _make_manager(db)
    mgr.hypotheses["action-ACTION4"] = Hypothesis(
        id="action-ACTION4",
        description="ACTION4 moves right",
        category="action_semantic",
        confidence=0.9,
    )

    fact = ActionFact(
        id="fact-ACTION4",
        action="ACTION4",
        fact_type="deterministic_effect",
        description="ACTION4 deterministic effect: ACTION4 moves player right by 1 cell",
        consistency=0.92,
        value_status="valuable",
        evidence_count=4,
        trend={
            "kind": "directional_drift",
            "axis": "col",
            "direction": "right",
            "avg_delta": 1.0,
            "message": "rightward drift by ~1.0 cell(s)/step",
        },
        support_steps=[3, 4],
    )

    await mgr._persist_action_fact(fact)

    write_queries = [call.args[0] for call in db.execute_write.await_args_list]
    assert any("MERGE (f:ActionFact {fact_id: $fid})" in query for query in write_queries)
    assert any("DERIVED_FROM_FACT" in query for query in write_queries)
    assert any("SUPPORTS_HYPOTHESIS" in query for query in write_queries)

    first_params = db.execute_write.await_args_list[0].args[1]
    assert first_params["tid"] == "task-1"
    assert first_params["level"] == 2
    assert first_params["action"] == "ACTION4"


@pytest.mark.asyncio
async def test_load_action_facts_rehydrates_state_from_kuzu():
    db = MagicMock()
    db.execute_read = AsyncMock(return_value=[
        {
            "f.fact_id": "task-1_fact-ACTION1",
            "f.action_id": "ACTION1",
            "f.fact_type": "low_value",
            "f.description": "ACTION1 low-value effect: regional change",
            "f.consistency": 0.75,
            "f.value_status": "low_value",
            "f.evidence_count": 3,
        }
    ])

    mgr = _make_manager(db)
    count = await mgr.load_action_facts()

    assert count == 1
    assert "ACTION1" in mgr.action_facts
    assert mgr.action_facts["ACTION1"].fact_type == "low_value"
    assert mgr.action_facts["ACTION1"].consistency == 0.75


@pytest.mark.asyncio
async def test_observe_falls_back_to_in_memory_when_kuzu_unavailable():
    mgr = HypothesisManager(MagicMock(), "session-1")

    grid = [[0, 1], [1, 0]]
    observation = {"grid": grid, "colors": [], "state": "NOT_FINISHED"}

    await mgr.observe(
        grid=grid,
        action_taken=None,
        step=0,
        available_actions=["ACTION1"],
        observation=observation,
    )

    await mgr.observe(
        grid=[[0, 1], [1, 1]],
        action_taken="ACTION1",
        step=1,
        available_actions=["ACTION1"],
        observation=observation,
        transition_meta={"reward": 0.0},
    )

    assert "ACTION1" in mgr.action_facts
    assert mgr.action_facts["ACTION1"].evidence_count >= 1
