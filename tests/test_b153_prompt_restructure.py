from __future__ import annotations

import pytest

from benchmarks.arc3.adapter import NoOpBrainClient
from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.solver import GameRuleHypothesis


def _make_orch() -> ARCOrchestrator:
    return ARCOrchestrator(
        brain_client=NoOpBrainClient(),
        llm_client=None,
        session_id="test-session",
        serializer=StateSerializerForARC(),
        config={},
    )


def test_render_grid_compact_64x64():
    orch = _make_orch()
    grid = [[(r + c) % 16 for c in range(64)] for r in range(64)]
    rendered = orch.render_grid_compact(grid, max_rows=30)
    lines = rendered.splitlines()
    # render_grid_compact defaults to max_rows=30 and appends a summary line
    if len(grid) > 30:
        assert len(lines) == 31
        # all rendered grid lines should be the full width
        assert all(len(line) == 64 for line in lines[:-1])
        assert lines[-1].startswith("... (")
        check_lines = lines[:-1]
    else:
        assert len(lines) == 64
        assert all(len(line) == 64 for line in lines)
        check_lines = lines

    allowed = set(ARCOrchestrator._COLOR_CHARS)
    # every character in rendered grid rows must be from the allowed set
    assert all((ch in allowed) for line in check_lines for ch in line)


def test_exploration_prompt_under_400_tokens():
    orch = _make_orch()
    orch._current_level = 1
    orch._solved_levels = []
    obs = {"grid": [[0, 1], [2, 3]], "available_actions": ["ACTION1", "ACTION2"], "win_levels": 8}
    prompt = orch.build_action_prompt(obs, {}, [], obs["available_actions"]) 
    tokens = orch.serializer._estimate_tokens(prompt)
    assert tokens <= 400


def test_rule_application_prompt_contains_insights_and_under_500_tokens():
    orch = _make_orch()
    orch._current_level = 3
    orch._solved_levels = [{"start_grid": [[0]], "end_grid": [[1]], "steps": 1}]

    hyp = GameRuleHypothesis(
        rule_description="paint similar pixels",
        action_semantics={"ACTION1": "paints blue"},
        objective_description="match pattern",
        level_strategy="apply actions",
        confidence=0.65,
        evidence=[],
        contradictions=[],
        source="test",
    )
    # Ensure the solve_engine has the hypotheses list
    orch.solve_engine._game_rule_hypotheses = [hyp]

    obs = {"grid": [[0, 1], [2, 3]], "available_actions": ["ACTION1", "ACTION2"], "win_levels": 8}
    prompt = orch.build_action_prompt(obs, {}, [], obs["available_actions"]) 
    assert "KNOWLEDGE FROM PRIOR LEVELS" in prompt or "From prior levels" in prompt
    tokens = orch.serializer._estimate_tokens(prompt)
    assert tokens <= 500


def test_execution_prompt_under_200_tokens():
    orch = _make_orch()
    orch._phase2_mode = "execution"
    orch._verified_output_grid = [[1, 1], [1, 1]]
    obs = {"grid": [[0, 0], [0, 0]], "available_actions": ["ACTION1", "ACTION2"]}
    prompt = orch.build_action_prompt(obs, {}, [], obs["available_actions"]) 
    tokens = orch.serializer._estimate_tokens(prompt)
    assert tokens <= 200
    assert "TARGET GRID" in prompt
