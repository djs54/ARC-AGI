"""Tests for B111 - ARC SideQuests Call Ledger in Debug Export."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from benchmarks.arc3.adapter import LedgerBrainClient


class MockBrainClient:
    """Mock brain client for testing."""
    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed=None):
        return {"status": "ok", "message": "turn recorded"}

    async def current_truth(self, *, query: str, session_id: str, scope: str, limit: int):
        return {"results": [{"id": "fact-1"}]}

    async def register_plan(self, *, goal: str, steps, session_id: str):
        return {"plan_id": "plan-123"}

    async def report_outcome(self, *, plan_id: str, outcome: str, valence: float, session_id: str, valence_source: str | None = None):
        return {"status": "ok"}

    async def recall_plans(self, *, goal_query: str, session_id: str, min_valence: float, limit: int):
        return {"plans": [{"id": "plan-1"}]}

    async def recall_relevant_lessons(self, *, query: str, limit: int):
        return {"lessons": [{"id": "lesson-1"}]}

    async def analogical_search(self, *, query: str, current_quest_id: str, limit: int, min_similarity: float):
        return {"results": [{"text": "analogous example"}]}

    async def branch_quest(self, *, name: str, purpose: str, parent_quest_id: str):
        return {"side_quest_id": "quest-456"}


@pytest.fixture
def ledger():
    """Create an empty ledger list."""
    return []


@pytest.fixture
def mock_brain(ledger):
    """Create a LedgerBrainClient with mock inner client."""
    inner = MockBrainClient()
    step_provider = lambda: 0
    return LedgerBrainClient(inner, ledger, step_provider)


@pytest.mark.asyncio
async def test_ledger_records_notify_turn(mock_brain, ledger):
    """B111: Ledger should record notify_turn calls."""
    await mock_brain.notify_turn(role="user", content="test content", session_id="test-session")

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["call_type"] == "notify_turn"
    assert entry["mode"] == "write"
    assert entry["input_summary"] == "test content"
    assert entry["result_summary"] == "ok"
    assert "latency_ms" in entry


@pytest.mark.asyncio
async def test_ledger_records_current_truth(mock_brain, ledger):
    """B111: Ledger should record current_truth reads."""
    await mock_brain.current_truth(query="test query", session_id="test-session", scope="general", limit=5)

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["call_type"] == "current_truth"
    assert entry["mode"] == "read"
    assert entry["input_summary"] == "test query"
    assert "found 1 items" in entry["result_summary"]


@pytest.mark.asyncio
async def test_ledger_records_register_plan(mock_brain, ledger):
    """B111: Ledger should record register_plan writes."""
    await mock_brain.register_plan(goal="solve puzzle", steps=["step1", "step2"], session_id="test-session")

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["call_type"] == "register_plan"
    assert entry["mode"] == "write"
    assert "goal=solve puzzle" in entry["input_summary"]
    assert "plan_id=" in entry["result_summary"]


@pytest.mark.asyncio
async def test_ledger_records_report_outcome(mock_brain, ledger):
    """B111: Ledger should record report_outcome writes."""
    await mock_brain.report_outcome(plan_id="plan-1", outcome="succeeded", valence=0.8, session_id="test-session")

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["call_type"] == "report_outcome"
    assert entry["mode"] == "write"
    assert "plan_id=plan-1" in entry["input_summary"]
    assert "valence=0.80" in entry["input_summary"]


@pytest.mark.asyncio
async def test_ledger_records_recall_plans(mock_brain, ledger):
    """B111: Ledger should record recall_plans reads."""
    await mock_brain.recall_plans(goal_query="how to win", session_id="test-session", min_valence=0.5, limit=5)

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["call_type"] == "recall_plans"
    assert entry["mode"] == "read"
    assert entry["input_summary"] == "how to win"
    assert "found 1 plans" in entry["result_summary"]


@pytest.mark.asyncio
async def test_ledger_records_recall_lessons(mock_brain, ledger):
    """B111: Ledger should record recall_lessons reads."""
    await mock_brain.recall_relevant_lessons(query="pattern matching", limit=5)

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["call_type"] == "recall_lessons"
    assert entry["mode"] == "read"
    assert entry["input_summary"] == "pattern matching"
    assert "found 1 lessons" in entry["result_summary"]


@pytest.mark.asyncio
async def test_ledger_records_analogical_search(mock_brain, ledger):
    """B111: Ledger should record analogical_search reads."""
    await mock_brain.analogical_search(query="similar puzzles", current_quest_id="quest-1", limit=5, min_similarity=0.7)

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["call_type"] == "analogical_search"
    assert entry["mode"] == "read"
    assert entry["input_summary"] == "similar puzzles"
    assert "found 1 results" in entry["result_summary"]


@pytest.mark.asyncio
async def test_ledger_records_branch_quest(mock_brain, ledger):
    """B111: Ledger should record branch_quest writes."""
    await mock_brain.branch_quest(name="sub-quest", purpose="solve subproblem", parent_quest_id="quest-1")

    assert len(ledger) == 1
    entry = ledger[0]
    assert entry["call_type"] == "branch_quest"
    assert entry["mode"] == "write"
    assert entry["input_summary"] == "sub-quest"
    assert "side_quest_id=" in entry["result_summary"]


@pytest.mark.asyncio
async def test_ledger_includes_step_and_phase(mock_brain, ledger):
    """B111: Ledger entries should include step and phase."""
    mock_brain.current_phase = "hypothesize"
    await mock_brain.notify_turn(role="user", content="test", session_id="test-session")

    entry = ledger[0]
    assert "step" in entry
    assert entry["step"] == 0
    assert "phase" in entry
    assert entry["phase"] == "hypothesize"


@pytest.mark.asyncio
async def test_ledger_respects_phase_provider(ledger):
    """B111: Ledger should use provided phase when available."""
    inner = MockBrainClient()
    step_count = 0
    step_provider = lambda: step_count

    brain = LedgerBrainClient(inner, ledger, step_provider)
    brain.current_phase = "bootstrap"

    await brain.notify_turn(role="user", content="test", session_id="test-session")
    assert ledger[0]["step"] == 0
    assert ledger[0]["phase"] == "bootstrap"

    step_count = 5
    brain.current_phase = "act"
    await brain.notify_turn(role="user", content="test", session_id="test-session")
    assert ledger[1]["step"] == 5
    assert ledger[1]["phase"] == "act"


def test_ledger_compacts_long_input(ledger):
    """B111: Ledger should compact long input summaries."""
    inner = MockBrainClient()
    brain = LedgerBrainClient(inner, ledger, lambda: 0)

    long_text = "x" * 200
    brain._record("test", "test_call", "read", long_text, "result", 100)

    entry = ledger[0]
    assert len(entry["input_summary"]) <= 121  # 120 + "…"
    assert entry["input_summary"].endswith("…")


def test_ledger_compacts_long_result(ledger):
    """B111: Ledger should compact long result summaries."""
    inner = MockBrainClient()
    brain = LedgerBrainClient(inner, ledger, lambda: 0)

    long_text = "y" * 200
    brain._record("test", "test_call", "write", "input", long_text, 100)

    entry = ledger[0]
    assert len(entry["result_summary"]) <= 121  # 120 + "…"
    assert entry["result_summary"].endswith("…")


def test_ledger_records_latency(ledger):
    """B111: Ledger should record latency in milliseconds."""
    inner = MockBrainClient()
    brain = LedgerBrainClient(inner, ledger, lambda: 0)

    brain._record("test", "test_call", "read", "input", "result", 123.456)

    entry = ledger[0]
    assert entry["latency_ms"] == 123.5  # Rounded to 1 decimal place


@pytest.mark.asyncio
async def test_multiple_calls_accumulated(mock_brain, ledger):
    """B111: Ledger should accumulate multiple calls."""
    await mock_brain.notify_turn(role="user", content="msg1", session_id="test-session")
    await mock_brain.current_truth(query="q1", session_id="test-session", scope="general", limit=5)
    await mock_brain.recall_plans(goal_query="goal1", session_id="test-session", min_valence=0.5, limit=5)

    assert len(ledger) == 3
    assert ledger[0]["call_type"] == "notify_turn"
    assert ledger[1]["call_type"] == "current_truth"
    assert ledger[2]["call_type"] == "recall_plans"


def test_ledger_entry_shape(ledger):
    """B111: Ledger entries should have required fields."""
    inner = MockBrainClient()
    brain = LedgerBrainClient(inner, ledger, lambda: 1)

    brain.current_phase = "solve"
    brain._record("solve", "test_op", "read", "query text", "5 results found", 250.5)

    entry = ledger[0]
    required_fields = ["step", "phase", "call_type", "mode", "input_summary", "result_summary", "latency_ms"]
    for field in required_fields:
        assert field in entry, f"Missing required field: {field}"

    assert entry["step"] == 1
    assert entry["phase"] == "solve"
    assert entry["call_type"] == "test_op"
    assert entry["mode"] == "read"
    assert entry["input_summary"] == "query text"
    assert entry["result_summary"] == "5 results found"
    assert entry["latency_ms"] == 250.5
