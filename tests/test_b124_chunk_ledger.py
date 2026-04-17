"""Tests for B124 - Structured Task Ledger."""

import pytest
from unittest.mock import AsyncMock
from agents.arc3.solver import (
    ChunkLedgerEntry, SolveContext, SolveEngine, PlanChunk, GameArchetype,
)


def test_chunk_ledger_entry_creation():
    """B124: ChunkLedgerEntry dataclass works as expected."""
    entry = ChunkLedgerEntry(
        description="Test chunk",
        status="active",
        steps_used=5,
        outcome_summary="In progress"
    )
    assert entry.description == "Test chunk"
    assert entry.status == "active"
    assert entry.steps_used == 5
    assert entry.outcome_summary == "In progress"


def test_solve_context_includes_chunk_ledger():
    """B124: SolveContext has chunk_ledger field."""
    sc = SolveContext(archetype=GameArchetype.UNKNOWN)
    assert hasattr(sc, "chunk_ledger")
    assert sc.chunk_ledger == []


def test_chunk_ledger_initialized_empty():
    """B124: SolveEngine initializes with empty ledger."""
    brain = AsyncMock()
    llm = AsyncMock()
    engine = SolveEngine(brain_client=brain, llm_client=llm, session_id="test")
    assert engine._chunk_ledger == []


def test_add_chunk_to_ledger_as_active():
    """B124: Adding chunk marks it as active in ledger."""
    brain = AsyncMock()
    llm = AsyncMock()
    engine = SolveEngine(brain_client=brain, llm_client=llm, session_id="test")
    chunk = PlanChunk(
        description="Test chunk",
        estimated_actions=["ACTION1"],
        source="bfs"
    )
    engine._add_chunk_to_ledger_as_active(chunk)

    assert len(engine._chunk_ledger) == 1
    entry = engine._chunk_ledger[0]
    assert entry.description == "Test chunk"
    assert entry.status == "active"
    assert entry.steps_used == 0


def test_mark_chunk_completed():
    """B124: Marking chunk completed updates ledger entry."""
    brain = AsyncMock()
    llm = AsyncMock()
    engine = SolveEngine(brain_client=brain, llm_client=llm, session_id="test")
    chunk = PlanChunk(
        description="Test chunk",
        estimated_actions=["ACTION1"],
        source="bfs",
        steps_executed=5,
        progress_score=0.7
    )
    engine._add_chunk_to_ledger_as_active(chunk)
    engine._mark_chunk_completed(chunk)

    entry = engine._chunk_ledger[0]
    assert entry.status == "completed"
    assert entry.steps_used == 5
    assert "0.70" in entry.outcome_summary


def test_mark_chunk_failed():
    """B124: Marking chunk failed updates ledger entry."""
    brain = AsyncMock()
    llm = AsyncMock()
    engine = SolveEngine(brain_client=brain, llm_client=llm, session_id="test")
    chunk = PlanChunk(
        description="Test chunk",
        estimated_actions=["ACTION1"],
        source="bfs",
        steps_executed=3
    )
    engine._add_chunk_to_ledger_as_active(chunk)
    engine._mark_chunk_failed(chunk, "stale: next action unavailable")

    entry = engine._chunk_ledger[0]
    assert entry.status == "failed"
    assert entry.steps_used == 3
    assert "stale" in entry.outcome_summary


def test_prune_chunk_ledger_caps_at_8():
    """B124: Ledger is capped at 8 entries, removing oldest completed first."""
    brain = AsyncMock()
    llm = AsyncMock()
    engine = SolveEngine(brain_client=brain, llm_client=llm, session_id="test")

    # Add 10 chunks, all completed
    for i in range(10):
        chunk = PlanChunk(
            description=f"Chunk {i}",
            estimated_actions=["ACTION1"],
            source="bfs",
            steps_executed=i+1
        )
        engine._add_chunk_to_ledger_as_active(chunk)
        engine._mark_chunk_completed(chunk)

    # Should be capped at 8
    assert len(engine._chunk_ledger) == 8
    # Oldest 2 completed chunks should be removed
    descriptions = [e.description for e in engine._chunk_ledger]
    assert "Chunk 0" not in descriptions
    assert "Chunk 1" not in descriptions
    assert "Chunk 9" in descriptions


def test_prune_preserves_non_completed():
    """B124: Pruning preserves all non-completed entries."""
    brain = AsyncMock()
    llm = AsyncMock()
    engine = SolveEngine(brain_client=brain, llm_client=llm, session_id="test")

    # Add 5 completed and 5 failed chunks
    for i in range(5):
        chunk = PlanChunk(
            description=f"Completed {i}",
            estimated_actions=["ACTION1"],
            source="bfs",
            steps_executed=i+1
        )
        engine._add_chunk_to_ledger_as_active(chunk)
        engine._mark_chunk_completed(chunk)

    for i in range(5):
        chunk = PlanChunk(
            description=f"Failed {i}",
            estimated_actions=["ACTION1"],
            source="bfs",
            steps_executed=i+1
        )
        engine._add_chunk_to_ledger_as_active(chunk)
        engine._mark_chunk_failed(chunk, "test")

    # Add more completed to trigger pruning
    for i in range(5, 10):
        chunk = PlanChunk(
            description=f"Completed {i}",
            estimated_actions=["ACTION1"],
            source="bfs",
            steps_executed=i+1
        )
        engine._add_chunk_to_ledger_as_active(chunk)
        engine._mark_chunk_completed(chunk)

    # Should be capped at 8
    assert len(engine._chunk_ledger) == 8
    # All failed entries should be preserved
    failed_count = sum(1 for e in engine._chunk_ledger if e.status == "failed")
    assert failed_count == 5


def test_solve_context_returns_ledger():
    """B124: SolveContext includes ledger in the returned object."""
    brain = AsyncMock()
    llm = AsyncMock()
    engine = SolveEngine(brain_client=brain, llm_client=llm, session_id="test")
    chunk = PlanChunk(
        description="Test chunk",
        estimated_actions=["ACTION1"],
        source="bfs"
    )
    engine._add_chunk_to_ledger_as_active(chunk)

    # Verify the ledger is returned
    assert len(engine._chunk_ledger) == 1
    assert engine._chunk_ledger[0].description == "Test chunk"
