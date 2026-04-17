from __future__ import annotations

import pytest

from benchmarks.arc3.adapter import NoOpBrainClient
from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.orchestrator import ARCOrchestrator


def _make_orch() -> ARCOrchestrator:
    return ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=None,
        session_id="test-session",
        serializer=StateSerializerForARC(),
        config={},
    )


class StubHypothesis:
    def __init__(self, confidence=0.7):
        self.confidence = confidence
        self.rule_description = "stub rule"
        self.action_semantics = {"ACTION1": "paint"}
        self.objective_description = "match"
        self.level_strategy = "apply"
        self.evidence = []
        self.contradictions = []
        self.source = "test"


@pytest.mark.asyncio
async def test_run_knowledge_pipeline_sets_execution_mode(monkeypatch):
    orch = _make_orch()
    # Ensure there's a level pattern so the pipeline runs
    orch._level_pattern = object()

    solved_levels = [
        {"start_grid": [[0]], "end_grid": [[1]], "steps": 1},
        {"start_grid": [[0]], "end_grid": [[1]], "steps": 1},
    ]

    async def fake_hypothesize(self, level_pattern, solved_levels, llm_client=None, **kwargs):
        return [StubHypothesis(confidence=0.85)]

    async def fake_solve(self, hypotheses, solved_levels=None):
        return hypotheses[0]

    monkeypatch.setattr("agents.arc3.solver.GameRuleHypothesizer.hypothesize", fake_hypothesize)
    monkeypatch.setattr("agents.arc3.repl_verification.RuleRefinementLoop.solve", fake_solve)

    await orch._run_knowledge_pipeline(solved_levels)

    assert orch._phase2_mode == "execution"
    assert orch._game_rule_hypothesis is not None
    assert orch._rule_confidence >= 0.8
    assert orch._action_semantics.get("ACTION1") == "paint"


@pytest.mark.asyncio
async def test_run_knowledge_pipeline_no_hypotheses_falls_back(monkeypatch):
    orch = _make_orch()
    orch._level_pattern = object()
    solved_levels = [{"start_grid": [[0]], "end_grid": [[1]], "steps": 1}]

    async def fake_hypothesize(self, level_pattern, solved_levels, llm_client=None, **kwargs):
        return []

    monkeypatch.setattr("agents.arc3.solver.GameRuleHypothesizer.hypothesize", fake_hypothesize)

    await orch._run_knowledge_pipeline(solved_levels)

    assert orch._phase2_mode == "fallback"


def test_select_prompt_mode_logic():
    orch = _make_orch()

    orch._current_level = 1
    orch._solved_levels = []
    orch._rule_confidence = 0.0
    assert orch._select_prompt_mode() == "exploration"

    orch._current_level = 3
    orch._solved_levels = [{},]
    orch._rule_confidence = 0.5
    assert orch._select_prompt_mode() == "rule_application"

    orch._current_level = 4
    orch._solved_levels = [{}, {}]
    orch._rule_confidence = 0.85
    assert orch._select_prompt_mode() == "execution"

    orch._rule_confidence = 0.2
    assert orch._select_prompt_mode() == "navigation"
