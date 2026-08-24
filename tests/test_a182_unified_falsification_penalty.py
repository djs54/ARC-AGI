"""Tests for A182: plan_generator.py applied a falsification penalty from two
sources that track the identical event within a single continuous episode --
the graph's persistent contradictions count (decayed) and the local
action_falsification_counts (not decayed) -- stacking to roughly double the
calibrated falsification_penalty weight. Confirmed live across three separate
`make smoke` runs by reverse-engineering real candidate scores to exact
matches. Fix: the graph's signal is authoritative once it has real evidence;
the local counter is a fallback only, used when the graph has nothing to say.
See backlog/A182.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, ResolvedGoal, WorkflowState


class _GraphPort:
    """Minimal graph port exposing only fetch_per_action_evidence -- no
    fetch_rules_for_action attribute at all, so getattr(..., None) correctly
    skips A177's rule-bonus path and isolates the falsification-penalty
    behavior this card is about."""

    def __init__(self, evidence: dict[str, Any] | None = None, raises: bool = False) -> None:
        self._evidence = evidence
        self._raises = raises

    def ingest_perception(self, perception: Any) -> Any:
        return None

    def fetch_goal_evidence(self, perception: Any, goal: Any = None) -> Any:
        return {}

    def fetch_per_action_evidence(self, action_id: str) -> dict[str, Any]:
        if self._raises:
            raise RuntimeError("graph unavailable")
        return self._evidence or {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0}


def _state(falsifications: int = 0, attempts: int = 0) -> WorkflowState:
    return WorkflowState(
        step_index=0,
        action_attempt_counts={"action-a": attempts} if attempts else {},
        action_falsification_counts={"action-a": falsifications} if falsifications else {},
        consecutive_no_progress_count=0,
    )


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "hash-1", "available_actions": ["action-a"]},
        grid_hash="hash-1",
    )


def _goal() -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))


def _score_for(graph_port: Any, falsifications: int, attempts: int) -> float:
    planner = PlanGenerator(PlanGeneratorLimits())
    result = planner.generate(_state(falsifications, attempts), _perception(), _goal(), graph_port=graph_port).payload
    return result.candidate.score


LIMITS = PlanGeneratorLimits()


class TestGraphContradictionIsAuthoritativeNotAdditive:
    def test_graph_contradiction_skips_local_penalty_not_double_counted(self):
        """This card's own reproduction: contradictions=2, supports=0, local
        falsifications=2, attempts=2. The OLD formula gave -0.5152 (double
        penalty); the fix must give the single-penalty value instead.

        Expected value updated for A187: the contradiction penalty is no
        longer decayed by repeat_decay_factor ** attempts (A187 fixed a
        second bug where that decay shrank the penalty toward zero as
        attempts grew, inverting its intent) -- it's now subtracted at full
        magnitude after decay is applied to the positive component only.

        Updated for A191 (2026-08-23): local falsifications=1, not 2 --
        A191 excludes any book_id with local falsifications >= 2 from the
        candidate set entirely (this card's own real action would no longer
        be scored at all with the original falsifications=2 fixture). The
        graph's contradictions=2 evidence, which is what this test actually
        verifies is authoritative over the local counter, is unchanged --
        the "graph authoritative, not additive" formula below never
        references the local falsifications count in the first place, so
        this substitution changes nothing about what's being verified."""
        graph = _GraphPort({"supports": 0, "contradictions": 2, "confidence": 0.0, "attempts": 2})

        score = _score_for(graph, falsifications=1, attempts=2)

        old_double_penalty_score = -0.5152
        assert score != pytest.approx(old_double_penalty_score)
        assert score > old_double_penalty_score

        graph_contradiction_penalty = LIMITS.falsification_penalty * 2
        expected = 0.0 - graph_contradiction_penalty - min(LIMITS.repeat_attempt_penalty * 2, 0.18)
        assert score == pytest.approx(expected)


class TestLocalFallbackWhenGraphHasNothing:
    """Updated for A191 (2026-08-23): all three fixtures below now use
    falsifications=1, not 2 -- A191 excludes any book_id with local
    falsifications >= 2 from the candidate set entirely, so the original
    falsifications=2 scenario is no longer scored at all (it's excluded
    before ever reaching this local-fallback logic). falsifications=1 is
    the highest count that still exercises this code path; the expected
    formulas below are recomputed for it (attempts stays 2, since exclusion
    only checks falsifications, not attempts)."""

    def test_no_contradiction_signal_falls_back_to_local_penalty(self):
        """Graph returns the capability-missing-shaped zero dict, but local
        state knows about 1 real falsification this run -- the fallback
        must still apply the local penalty so the signal isn't lost."""
        graph = _GraphPort({"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0})

        score = _score_for(graph, falsifications=1, attempts=2)

        decay = LIMITS.repeat_decay_factor**2
        expected = 0.0 * decay - min(LIMITS.repeat_attempt_penalty * 2, 0.18) - min(LIMITS.falsification_penalty * 1, 0.55)
        assert score == pytest.approx(expected)

    def test_no_graph_port_at_all_falls_back_to_local_penalty(self):
        score = _score_for(None, falsifications=1, attempts=2)

        decay = LIMITS.repeat_decay_factor**2
        expected = 0.0 * decay - min(LIMITS.repeat_attempt_penalty * 2, 0.18) - min(LIMITS.falsification_penalty * 1, 0.55)
        assert score == pytest.approx(expected)

    def test_graph_evidence_call_raising_falls_back_to_local_penalty(self):
        graph = _GraphPort(raises=True)

        score = _score_for(graph, falsifications=1, attempts=2)

        decay = LIMITS.repeat_decay_factor**2
        expected = 0.0 * decay - min(LIMITS.repeat_attempt_penalty * 2, 0.18) - min(LIMITS.falsification_penalty * 1, 0.55)
        assert score == pytest.approx(expected)


class TestUnaffectedPaths:
    def test_untested_action_scoring_unchanged(self):
        """Zero falsifications, zero contradictions, first attempt -- this
        card must not touch the untested-action scoring path at all."""
        graph = _GraphPort({"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0})

        score = _score_for(graph, falsifications=0, attempts=0)

        assert score == pytest.approx(LIMITS.untested_bonus)

    def test_supports_outweighing_contradictions_still_boosts_not_penalizes(self):
        """Regression guard: the evidence_confidence/supports-boost branch
        (unrelated to the falsification-penalty duplication fixed here) is
        untouched."""
        graph = _GraphPort({"supports": 5, "contradictions": 0, "confidence": 0.9, "attempts": 5})

        score = _score_for(graph, falsifications=0, attempts=1)

        assert score > 0
