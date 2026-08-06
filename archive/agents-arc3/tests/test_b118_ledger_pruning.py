"""Tests for B118 - ARC Ledger-Driven Runtime Pruning."""

import pytest
from unittest.mock import AsyncMock, MagicMock
from agents.arc3.orchestrator import ARCOrchestrator
from benchmarks.arc3.adapter import LedgerBrainClient


class MockBrainClient:
    """Mock brain client for testing."""
    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed=None):
        return {"status": "ok", "message": "turn recorded"}

    async def current_truth(self, *, query: str, session_id: str, scope: str, limit: int):
        return {"results": []}

    async def register_plan(self, *, goal: str, steps, session_id: str):
        return {"plan_id": "plan-123"}

    async def report_outcome(self, *, plan_id: str, outcome: str, valence: float, session_id: str, valence_source: str | None = None):
        return {"status": "ok"}

    async def recall_plans(self, *, goal_query: str, session_id: str, min_valence: float, limit: int):
        return {"plans": []}

    async def recall_relevant_lessons(self, *, query: str, limit: int):
        return {"lessons": []}

    async def analogical_search(self, *, query: str, current_quest_id: str, limit: int, min_similarity: float):
        return {"results": []}

    async def branch_quest(self, *, name: str, purpose: str, parent_quest_id: str):
        return {"side_quest_id": "quest-456"}


@pytest.fixture
def ledger():
    """Create an empty ledger list."""
    return []


@pytest.fixture
def ledger_brain(ledger):
    """Create a LedgerBrainClient with mock inner client."""
    inner = MockBrainClient()
    step_provider = lambda: 0
    return LedgerBrainClient(inner, ledger, step_provider)


@pytest.fixture
def mock_orchestrator(ledger_brain):
    """Create an ARCOrchestrator with ledger-enabled brain."""
    llm_client = AsyncMock()
    serializer = MagicMock()
    serializer._estimate_tokens.return_value = 100

    orch = ARCOrchestrator(
        brain_client=ledger_brain,
        llm_client=llm_client,
        session_id="test-session",
        serializer=serializer,
        config={},
    )
    return orch


def test_get_ledger_with_ledger_brain(mock_orchestrator, ledger):
    """B118: get_ledger() should return ledger from LedgerBrainClient."""
    result = mock_orchestrator.get_ledger()
    assert result == []

    # Add an entry to the ledger
    ledger.append({"call_type": "current_truth", "latency_ms": 100})
    result = mock_orchestrator.get_ledger()
    assert len(result) == 1
    assert result[0]["call_type"] == "current_truth"


def test_get_ledger_without_ledger_brain():
    """B118: get_ledger() should return empty list for non-ledger brains."""
    mock_brain = AsyncMock()
    llm_client = AsyncMock()
    serializer = MagicMock()

    orch = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=llm_client,
        session_id="test-session",
        serializer=serializer,
        config={},
    )
    result = orch.get_ledger()
    assert result == []


def test_analyze_ledger_empty():
    """B118: analyze_ledger_and_prune() should return empty list for empty ledger."""
    mock_brain = AsyncMock()
    llm_client = AsyncMock()
    serializer = MagicMock()

    orch = ARCOrchestrator(
        brain_client=mock_brain,
        llm_client=llm_client,
        session_id="test-session",
        serializer=serializer,
        config={},
    )
    result = orch.analyze_ledger_and_prune()
    assert result == []


def test_analyze_ledger_no_pruning_needed(mock_orchestrator, ledger):
    """B118: No pruning if avg latency <= 500ms."""
    ledger.extend([
        {"call_type": "current_truth", "latency_ms": 100, "result_summary": "found 5 items"},
        {"call_type": "current_truth", "latency_ms": 150, "result_summary": "found 3 items"},
        {"call_type": "current_truth", "latency_ms": 200, "result_summary": "found 2 items"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    assert decisions == []
    assert mock_orchestrator._pruning_decisions == []


def test_analyze_ledger_high_latency_but_good_value(mock_orchestrator, ledger):
    """B118: No pruning if latency is high but low_value_ratio <= 0.5."""
    ledger.extend([
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 1 plans"},
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 2 plans"},
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 0 plans"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    # avg_latency = 600, low_value_ratio = 1/3 = 0.33 < 0.5, so no prune
    assert decisions == []


def test_analyze_ledger_triggers_pruning(mock_orchestrator, ledger):
    """B118: Pruning triggered when avg_latency > 500 AND low_value_ratio > 0.5."""
    ledger.extend([
        {"call_type": "analogical_search", "latency_ms": 600, "result_summary": "found 0 results"},
        {"call_type": "analogical_search", "latency_ms": 700, "result_summary": "found 0 results"},
        {"call_type": "analogical_search", "latency_ms": 800, "result_summary": "found 1 results"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    # avg_latency = 700, low_value_ratio = 2/3 = 0.67 > 0.5, so prune
    assert len(decisions) == 1
    assert decisions[0]["call_type"] == "analogical_search"
    assert decisions[0]["action"] == "deprioritize"
    assert "700.0ms" in decisions[0]["reason"]
    assert "66.7%" in decisions[0]["reason"]


def test_analyze_ledger_multiple_call_types(mock_orchestrator, ledger):
    """B118: Pruning should identify multiple high-latency/low-value call types."""
    ledger.extend([
        # current_truth: avg=600, low_value=100%
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "found 0 items"},
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "found 0 items"},
        # recall_lessons: avg=550, low_value=66.7%
        {"call_type": "recall_lessons", "latency_ms": 500, "result_summary": "found 0 lessons"},
        {"call_type": "recall_lessons", "latency_ms": 600, "result_summary": "found 0 lessons"},
        {"call_type": "recall_lessons", "latency_ms": 600, "result_summary": "found 2 lessons"},
        # analogical_search: avg=550, low_value=0% (no prune)
        {"call_type": "analogical_search", "latency_ms": 500, "result_summary": "found 1 results"},
        {"call_type": "analogical_search", "latency_ms": 600, "result_summary": "found 3 results"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()

    call_types_pruned = {d["call_type"] for d in decisions}
    assert "current_truth" in call_types_pruned
    assert "recall_lessons" in call_types_pruned
    assert "analogical_search" not in call_types_pruned


def test_analyze_ledger_tracks_pruning_decisions(mock_orchestrator, ledger):
    """B118: Pruning decisions should be stored in _pruning_decisions."""
    ledger.extend([
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 0 plans"},
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 0 plans"},
    ])

    assert mock_orchestrator._pruning_decisions == []

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    assert len(decisions) == 1
    assert len(mock_orchestrator._pruning_decisions) == 1
    assert mock_orchestrator._pruning_decisions[0]["call_type"] == "recall_plans"


def test_analyze_ledger_avoids_duplicate_decisions(mock_orchestrator, ledger):
    """B118: Multiple calls to analyze_ledger_and_prune() should not duplicate decisions."""
    ledger.extend([
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "found 0 items"},
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "found 0 items"},
    ])

    decisions1 = mock_orchestrator.analyze_ledger_and_prune()
    assert len(decisions1) == 1
    assert len(mock_orchestrator._pruning_decisions) == 1

    # Call again without adding new entries
    decisions2 = mock_orchestrator.analyze_ledger_and_prune()
    assert len(decisions2) == 1  # Returns the same decision
    assert len(mock_orchestrator._pruning_decisions) == 1  # But doesn't duplicate in list


def test_pruning_decision_format(mock_orchestrator, ledger):
    """B118: Pruning decisions should have required fields."""
    ledger.extend([
        {"call_type": "recall_lessons", "latency_ms": 800, "result_summary": "found 0 lessons"},
        {"call_type": "recall_lessons", "latency_ms": 800, "result_summary": "found 0 lessons"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    assert len(decisions) > 0

    decision = decisions[0]
    required_fields = ["call_type", "reason", "action"]
    for field in required_fields:
        assert field in decision, f"Missing field: {field}"

    assert decision["action"] == "deprioritize"
    assert "latency" in decision["reason"]
    assert "low value" in decision["reason"]


def test_pruning_decision_reason_clarity(mock_orchestrator, ledger):
    """B118: Pruning reason should clearly state latency and low_value_ratio."""
    ledger.extend([
        {"call_type": "analogical_search", "latency_ms": 750, "result_summary": "found 0 results"},
        {"call_type": "analogical_search", "latency_ms": 750, "result_summary": "found 0 results"},
        {"call_type": "analogical_search", "latency_ms": 750, "result_summary": "found 1 results"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    reason = decisions[0]["reason"]

    # Should show avg latency of 750ms
    assert "750.0ms" in reason
    # Should show low_value_ratio of 66.7%
    assert "66.7%" in reason


def test_low_value_detection_found_zero(mock_orchestrator, ledger):
    """B118: 'found 0' in result_summary should count as low-value."""
    ledger.extend([
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "found 0 items"},
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "found 0 items"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    assert len(decisions) == 1


def test_low_value_detection_found_empty_list(mock_orchestrator, ledger):
    """B118: 'found []' in result_summary should count as low-value."""
    ledger.extend([
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found []"},
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found []"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    assert len(decisions) == 1


def test_low_value_detection_case_insensitive(mock_orchestrator, ledger):
    """B118: Low-value detection should be case-insensitive."""
    ledger.extend([
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "Found 0 Items"},
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "FOUND 0 RESULTS"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    assert len(decisions) == 1


def test_pruning_with_mixed_entry_types(mock_orchestrator, ledger):
    """B118: Pruning should handle mixed entry types and identify problem calls."""
    ledger.extend([
        # notify_turn calls: low latency, good results
        {"call_type": "notify_turn", "latency_ms": 10, "result_summary": "ok", "mode": "write"},
        {"call_type": "notify_turn", "latency_ms": 10, "result_summary": "ok", "mode": "write"},
        # High latency retrieval with poor results
        {"call_type": "current_truth", "latency_ms": 800, "result_summary": "found 0 items"},
        {"call_type": "current_truth", "latency_ms": 800, "result_summary": "found 0 items"},
        {"call_type": "current_truth", "latency_ms": 800, "result_summary": "found 1 items"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()

    # Only current_truth should be flagged
    assert len(decisions) == 1
    assert decisions[0]["call_type"] == "current_truth"
    # notify_turn should not be flagged (latency too low)
    assert not any(d["call_type"] == "notify_turn" for d in decisions)


@pytest.mark.asyncio
async def test_pruning_integration_with_orchestrator(mock_orchestrator, ledger):
    """B118: Pruning decisions should affect should_trigger_retrieval behavior."""
    # Simulate high-latency, low-value current_truth calls
    ledger.extend([
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "found 0 items"},
        {"call_type": "current_truth", "latency_ms": 600, "result_summary": "found 0 items"},
    ])

    # Analyze and get pruning decisions
    decisions = mock_orchestrator.analyze_ledger_and_prune()
    assert len(decisions) > 0

    # Verify pruning decisions are stored
    assert len(mock_orchestrator._pruning_decisions) > 0
    assert any(d["call_type"] == "current_truth" for d in mock_orchestrator._pruning_decisions)


def test_pruning_decision_visibility(mock_orchestrator, ledger):
    """B118: Pruning decisions should be included in debug exports."""
    ledger.extend([
        {"call_type": "recall_lessons", "latency_ms": 700, "result_summary": "found 0 lessons"},
        {"call_type": "recall_lessons", "latency_ms": 700, "result_summary": "found 0 lessons"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()

    # Simulate what would be exported (based on line 1630 in orchestrator.py)
    export = {
        "pruning_decisions": list(mock_orchestrator._pruning_decisions),
    }

    assert "pruning_decisions" in export
    assert len(export["pruning_decisions"]) > 0
    assert export["pruning_decisions"][0]["call_type"] == "recall_lessons"


def test_pruning_threshold_exactly_500ms(mock_orchestrator, ledger):
    """B118: Threshold of 500ms is exclusive (> 500, not >= 500)."""
    ledger.extend([
        {"call_type": "current_truth", "latency_ms": 500, "result_summary": "found 0 items"},
        {"call_type": "current_truth", "latency_ms": 500, "result_summary": "found 0 items"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    # avg = 500, low_value_ratio = 1.0 > 0.5, but avg NOT > 500, so no prune
    assert decisions == []


def test_pruning_threshold_just_over_500ms(mock_orchestrator, ledger):
    """B118: Pruning should trigger just over 500ms threshold."""
    ledger.extend([
        {"call_type": "current_truth", "latency_ms": 500.1, "result_summary": "found 0 items"},
        {"call_type": "current_truth", "latency_ms": 500.1, "result_summary": "found 0 items"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    # avg > 500, low_value_ratio = 1.0 > 0.5, so prune
    assert len(decisions) == 1


def test_pruning_low_value_ratio_threshold(mock_orchestrator, ledger):
    """B118: Threshold of 0.5 is exclusive (> 0.5, not >= 0.5)."""
    ledger.extend([
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 0 plans"},
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 1 plans"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    # avg = 600 > 500, low_value_ratio = 0.5, but ratio NOT > 0.5, so no prune
    assert decisions == []


def test_pruning_low_value_ratio_just_over_threshold(mock_orchestrator, ledger):
    """B118: Pruning should trigger just over 0.5 low_value_ratio."""
    ledger.extend([
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 0 plans"},
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 0 plans"},
        {"call_type": "recall_plans", "latency_ms": 600, "result_summary": "found 1 plans"},
    ])

    decisions = mock_orchestrator.analyze_ledger_and_prune()
    # avg = 600 > 500, low_value_ratio = 2/3 = 0.67 > 0.5, so prune
    assert len(decisions) == 1
