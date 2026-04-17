import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.arc3.solver import SolveEngine, PlanChunk
from mcp_engine.schema import NODE_TABLES, REL_TABLES


@pytest.mark.asyncio
async def test_chunkexecution_schema_contains_required_fields():
    assert "ChunkExecution" in NODE_TABLES
    ddl = NODE_TABLES["ChunkExecution"]

    for field_name in [
        "execution_id",
        "task_id",
        "level",
        "plan_id",
        "chunk_family",
        "description",
        "status",
        "steps_used",
        "graduation_score",
        "evidence_at_end",
        "dissonance_triggered",
        "outcome_summary",
        "created_at",
    ]:
        assert field_name in ddl

    assert any("EXECUTED_AS" in rel for rel in REL_TABLES)


@pytest.mark.asyncio
async def test_mark_chunk_completed_queues_write_and_flushes_to_plan():
    db = MagicMock()
    db.execute_write = AsyncMock()

    engine = SolveEngine(MagicMock(), MagicMock(), "session-1")
    graph = MagicMock()
    graph.db = db
    engine._entity_graph = graph
    engine._task_id = "task-1"
    engine._current_level = 2

    chunk = PlanChunk(
        description="Follow corridor",
        source="directional",
        graduation_score=0.88,
        graduation_components={"evidence_score": 0.67},
        plan_id="plan-123",
    )
    engine._add_chunk_to_ledger_as_active(chunk)

    chunk.steps_executed = 3
    chunk.progress_score = 0.75
    engine._mark_chunk_completed(chunk)

    assert len(engine._pending_chunk_writes) == 1

    await engine._flush_chunk_writes()

    write_queries = [call.args[0] for call in db.execute_write.await_args_list]
    assert any("MERGE (c:ChunkExecution {execution_id: $eid})" in q for q in write_queries)
    assert any("MERGE (p)-[:EXECUTED_AS {seq: $seq}]->(c)" in q for q in write_queries)

    node_params = db.execute_write.await_args_list[0].args[1]
    assert node_params["eid"] == "task-1_L2_chunk_0"
    assert node_params["pid"] == "plan-123"
    assert node_params["status"] == "completed"
    assert node_params["steps"] == 3


@pytest.mark.asyncio
async def test_multiple_chunk_writes_get_unique_execution_ids():
    db = MagicMock()
    db.execute_write = AsyncMock()

    engine = SolveEngine(MagicMock(), MagicMock(), "session-1")
    graph = MagicMock()
    graph.db = db
    engine._entity_graph = graph
    engine._task_id = "task-xyz"
    engine._current_level = 1

    first = PlanChunk(
        description="Chunk one",
        source="bfs",
        graduation_score=0.5,
        graduation_components={"evidence_score": 0.2},
        plan_id="plan-a",
    )
    second = PlanChunk(
        description="Chunk two",
        source="plateau_exploitation",
        graduation_score=0.9,
        graduation_components={"evidence_score": 0.8},
        plan_id="plan-b",
    )

    engine._add_chunk_to_ledger_as_active(first)
    first.steps_executed = 1
    first.progress_score = 0.4
    engine._mark_chunk_completed(first)

    engine._add_chunk_to_ledger_as_active(second)
    second.steps_executed = 2
    engine._mark_chunk_failed(second, "dissonance")

    await engine._flush_chunk_writes()

    chunk_node_calls = [
        call for call in db.execute_write.await_args_list
        if "MERGE (c:ChunkExecution {execution_id: $eid})" in call.args[0]
    ]
    eids = [call.args[1]["eid"] for call in chunk_node_calls]

    assert eids == ["task-xyz_L1_chunk_0", "task-xyz_L1_chunk_1"]


@pytest.mark.asyncio
async def test_noop_without_entity_graph_keeps_ledger_only():
    engine = SolveEngine(MagicMock(), MagicMock(), "session-1")

    chunk = PlanChunk(description="Fallback chunk", source="explore")
    engine._add_chunk_to_ledger_as_active(chunk)
    chunk.steps_executed = 2
    engine._mark_chunk_failed(chunk, "stale")

    assert len(engine._chunk_ledger) == 1
    assert engine._chunk_ledger[0].status == "failed"
    assert engine._pending_chunk_writes == []

    await engine._flush_chunk_writes()
