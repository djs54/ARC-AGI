"""Tests for B137: Reduce redundant chunk register_plan churn via idempotency."""

import pytest
from unittest.mock import AsyncMock, MagicMock, call
import asyncio

from agents.arc3.solver import SolveEngine, PlanChunk


@pytest.fixture
def mock_brain():
    """Create a mock brain client."""
    brain = AsyncMock()
    brain.register_plan = AsyncMock(return_value={"plan_id": "test_plan_1"})
    brain.trace_event = AsyncMock(return_value=None)
    return brain


@pytest.fixture
def mock_llm():
    """Create a mock LLM client."""
    return MagicMock()


@pytest.fixture
def solver(mock_brain, mock_llm):
    """Create a SolveEngine instance with mocks."""
    return SolveEngine(brain_client=mock_brain, llm_client=mock_llm, session_id="test_session")


class TestPlanChanged:
    """Test the _plan_changed() helper method."""

    def test_plan_changed_returns_true_when_no_prior_plan(self, solver):
        """When no plan has been registered yet, should return True."""
        assert solver._plan_changed(
            plan_type="chunk",
            goal="Test goal",
            steps=["step1", "step2"],
        ) is True

    def test_plan_changed_returns_false_for_identical_plan(self, solver):
        """When plan matches last registered, should return False."""
        # A024: dedup is driven by the fingerprint tuple, not the legacy dict.
        solver._last_registered_chunk_fingerprint = solver._plan_fingerprint(
            plan_type="chunk",
            goal="Test goal",
            steps=["step1", "step2"],
        )

        # Same plan should not be re-registered
        assert solver._plan_changed(
            plan_type="chunk",
            goal="Test goal",
            steps=["step1", "step2"],
        ) is False

    def test_plan_changed_returns_true_for_different_goal(self, solver):
        """When goal changes, should return True."""
        solver._last_registered_chunk_plan = {
            "goal": "Old goal",
            "steps": ["step1", "step2"],
        }

        assert solver._plan_changed(
            plan_type="chunk",
            goal="New goal",
            steps=["step1", "step2"],
        ) is True

    def test_plan_changed_returns_true_for_different_steps(self, solver):
        """When steps change, should return True."""
        solver._last_registered_chunk_plan = {
            "goal": "Test goal",
            "steps": ["step1", "step2"],
        }

        assert solver._plan_changed(
            plan_type="chunk",
            goal="Test goal",
            steps=["step1", "step2", "step3"],
        ) is True

    def test_plan_changed_force_flag_always_returns_true(self, solver):
        """When force=True, should always return True."""
        solver._last_registered_chunk_fingerprint = solver._plan_fingerprint(
            plan_type="chunk",
            goal="Test goal",
            steps=["step1", "step2"],
        )

        assert solver._plan_changed(
            plan_type="chunk",
            goal="Test goal",
            steps=["step1", "step2"],
            force=True,
        ) is True

    def test_plan_changed_distinguishes_plan_types(self, solver):
        """Should separately track top and chunk plans."""
        # A024: populate fingerprint caches rather than the legacy dicts.
        solver._last_registered_top_fingerprint = solver._plan_fingerprint(
            plan_type="top",
            goal="Top goal",
            steps=["step1"],
        )
        solver._last_registered_chunk_fingerprint = solver._plan_fingerprint(
            plan_type="chunk",
            goal="Chunk goal",
            steps=["step2"],
        )

        # Same content for chunk plan should not be re-registered
        assert solver._plan_changed(
            plan_type="chunk",
            goal="Chunk goal",
            steps=["step2"],
        ) is False

        # But top plan should be different
        assert solver._plan_changed(
            plan_type="top",
            goal="Top goal",
            steps=["step1", "step2"],  # Different steps
        ) is True


@pytest.mark.asyncio
async def test_register_chunk_plan_first_registration(solver, mock_brain):
    """First chunk plan registration should go through normally."""
    chunk = PlanChunk(
        description="Explore: find hidden objects",
        estimated_actions=["ACTION1", "ACTION1", "ACTION2"],
    )

    await solver._register_chunk_plan(chunk)

    # Should have called register_plan once
    mock_brain.register_plan.assert_called_once_with(
        goal="Explore: find hidden objects",
        steps=["ACTION1", "ACTION1", "ACTION2"],
        session_id="test_session",
    )

    # Should have cached the plan
    assert solver._last_registered_chunk_plan is not None
    assert solver._last_registered_chunk_plan["goal"] == "Explore: find hidden objects"


@pytest.mark.asyncio
async def test_register_chunk_plan_duplicate_skipped(solver, mock_brain):
    """Identical chunk plan should be skipped on second registration."""
    chunk = PlanChunk(
        description="Explore: find hidden objects",
        estimated_actions=["ACTION1", "ACTION1", "ACTION2"],
    )

    # First registration
    await solver._register_chunk_plan(chunk)
    mock_brain.register_plan.reset_mock()

    # Second registration with identical plan
    await solver._register_chunk_plan(chunk)

    # Should NOT have called register_plan again
    mock_brain.register_plan.assert_not_called()

    # Should have emitted trace event for skip
    mock_brain.trace_event.assert_called_once()
    trace_call = mock_brain.trace_event.call_args
    assert trace_call[1]["event_type"] == "plan_registration_skipped"
    assert trace_call[1]["metadata"]["plan_type"] == "chunk"
    # A024: dedup trace reason aligned with fingerprint-based semantics.
    assert trace_call[1]["metadata"]["reason"] == "identical_fingerprint"


@pytest.mark.asyncio
async def test_register_chunk_plan_changed_triggers_reregistration(solver, mock_brain):
    """Changed chunk plan should trigger re-registration."""
    chunk1 = PlanChunk(
        description="Explore: find hidden objects",
        estimated_actions=["ACTION1", "ACTION1", "ACTION2"],
    )
    chunk2 = PlanChunk(
        description="Navigate: reach goal location",  # Different description
        estimated_actions=["ACTION2", "ACTION3"],
    )

    # First registration
    await solver._register_chunk_plan(chunk1)
    mock_brain.register_plan.reset_mock()
    mock_brain.trace_event.reset_mock()

    # Second registration with different plan
    mock_brain.register_plan.return_value = {"plan_id": "test_plan_2"}
    await solver._register_chunk_plan(chunk2)

    # Should have called register_plan again
    mock_brain.register_plan.assert_called_once_with(
        goal="Navigate: reach goal location",
        steps=["ACTION2", "ACTION3"],
        session_id="test_session",
    )

    # Should NOT have emitted skip trace event
    mock_brain.trace_event.assert_not_called()


@pytest.mark.asyncio
async def test_register_solve_plan_first_registration(solver, mock_brain):
    """First solve plan registration should go through normally."""
    observation = {
        "dataset_id": "training",
        "task_id": "007d8e4d",
    }

    await solver._register_solve_plan(observation)

    # Should have called register_plan once
    assert mock_brain.register_plan.call_count == 1
    call_args = mock_brain.register_plan.call_args[1]
    assert "007d8e4d" in call_args["goal"]

    # Should have cached the plan
    assert solver._last_registered_top_plan is not None


@pytest.mark.asyncio
async def test_register_solve_plan_duplicate_skipped(solver, mock_brain):
    """Identical solve plan should be skipped on second registration."""
    observation = {
        "dataset_id": "training",
        "task_id": "007d8e4d",
    }

    # First registration
    await solver._register_solve_plan(observation)
    mock_brain.register_plan.reset_mock()
    mock_brain.trace_event.reset_mock()

    # Second registration with same observation
    await solver._register_solve_plan(observation)

    # Should NOT have called register_plan again
    mock_brain.register_plan.assert_not_called()

    # Should have emitted trace event for skip
    mock_brain.trace_event.assert_called_once()
    trace_call = mock_brain.trace_event.call_args
    assert trace_call[1]["event_type"] == "plan_registration_skipped"
    assert trace_call[1]["metadata"]["plan_type"] == "top"


@pytest.mark.asyncio
async def test_reset_for_retry_clears_plan_state(solver, mock_brain):
    """reset_for_retry() should clear cached plan state."""
    chunk = PlanChunk(
        description="Test chunk",
        estimated_actions=["ACTION1"],
    )

    # Register a plan
    await solver._register_chunk_plan(chunk)
    assert solver._last_registered_chunk_plan is not None

    # Reset
    solver.reset_for_retry()

    # Plan state should be cleared
    assert solver._last_registered_chunk_plan is None
    assert solver._last_registered_top_plan is None

    # Next registration should go through
    mock_brain.register_plan.reset_mock()
    await solver._register_chunk_plan(chunk)
    mock_brain.register_plan.assert_called_once()


@pytest.mark.asyncio
async def test_chunk_plan_with_empty_steps_uses_fallback(solver, mock_brain):
    """Chunk plan with empty steps should use fallback step list."""
    chunk = PlanChunk(
        description="Test chunk",
        estimated_actions=[],  # Empty
    )

    await solver._register_chunk_plan(chunk)

    # Should have used fallback steps
    call_args = mock_brain.register_plan.call_args[1]
    assert call_args["steps"] == ["Execute strategy toward goal"]


def test_plan_tracking_attributes_initialized(solver):
    """Solver should initialize plan tracking attributes."""
    assert hasattr(solver, "_last_registered_top_plan")
    assert hasattr(solver, "_last_registered_chunk_plan")
    assert solver._last_registered_top_plan is None
    assert solver._last_registered_chunk_plan is None
