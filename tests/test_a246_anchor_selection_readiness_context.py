"""A246: `run_annatar_cycle`'s anchor-creation block (`if anchor is None:`)
must consult `readiness_report` before preferring an incidentally-
entity_ref-carrying candidate over the active goal.

Before this fix, a fresh anchor was chosen purely from whatever the
just-executed candidate happened to be: if its metadata carried an
`entity_ref` (set unconditionally on every ACTION6 click candidate by
plan_generator.py::_click_targets), the new anchor became entity-type --
completely independent of whether the episode was in probe or
goal-directed phase. Since ACTION6 clicks dominate most puzzles' candidate
pools, this meant that during goal-directed play, the moment a goal-type
anchor concluded, Annatar's very next anchor was very likely to be an
incidental entity pick rather than the actual active goal -- confirmed
live (SK48, sk48-d8078629): a genuinely productive goal-type anchor
(block-5, 4 real graph-growth cycles) concluded, and the next anchor was
anchor_ref=32, anchor_type=entity, mid-goal-directed-play.

The fix: `run_annatar_cycle` already receives `readiness_report` as a
parameter, and the rest of the same function already treats
`readiness_report is None` as the established signal for "this is a
goal-directed cycle, not a probe cycle" (A230's own precedent). The
anchor-creation block now consults it: during goal-directed play
(readiness_report is None) with an active goal available, the new anchor
is goal-type using the active goal's goal_id -- not the just-executed
candidate's entity_ref. During probing (readiness_report is not None), or
when there is no active goal at all, today's entity-preferring behavior is
completely unchanged.

See backlog/A246.md and backlog/plans/A-246-anchor-selection-readiness-
context.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.annatar_signals import run_annatar_cycle
from agents.arc4.types import (
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PerceptionSnapshot,
    PlanCandidate,
    ResolvedGoal,
    WorkflowDecision,
    WorkflowState,
)


def _perception_snapshot(grid_hash: str = "h1") -> PerceptionSnapshot:
    return PerceptionSnapshot(observation={"grid": grid_hash}, grid_hash=grid_hash)


def _execution_result(action_id: str = "ACTION6", candidate: PlanCandidate | None = None) -> ExecutionResult:
    if candidate is None:
        candidate = PlanCandidate(action_id=action_id, goal_id="g1")
    return ExecutionResult(action_id=action_id, candidate=candidate, observation={"grid": "h2"})


def _evaluation_result(*, meaningful_progress: bool = False, grid_changed: bool = True) -> EvaluationResult:
    return EvaluationResult(
        decision=WorkflowDecision.CONTINUE,
        meaningful_progress=meaningful_progress,
        metadata={"grid_changed": grid_changed},
    )


def _state_with_active_goal(goal_id: str = "block-5") -> WorkflowState:
    return WorkflowState(active_goal=ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description="d")))


class TestGoalDirectedAnchorSelectionPrefersActiveGoal:
    """The core fix: goal-directed cycle (readiness_report=None) + an
    entity_ref-carrying candidate + an active goal -> the new anchor must
    be goal-type using the active goal's own goal_id, not the incidental
    entity_ref. This is the exact SK48 live scenario (block-5 concluding,
    the next anchor incorrectly becoming anchor_ref=32/entity) reproduced
    deterministically."""

    def test_goal_directed_cycle_with_entity_ref_and_active_goal_anchors_on_goal(self):
        candidate = PlanCandidate(action_id="ACTION6", goal_id="block-5", metadata={"entity_ref": 32})
        state = _state_with_active_goal("block-5")
        execution = _execution_result(action_id="ACTION6", candidate=candidate)
        evaluation = _evaluation_result()

        outcome = run_annatar_cycle(
            state, _perception_snapshot(), execution, evaluation, graph_port=None, readiness_report=None
        )

        assert outcome.anchor_type == "goal"
        assert outcome.anchor_ref == "block-5"

    def test_readiness_report_omitted_entirely_defaults_to_goal_directed_behavior(self):
        """readiness_report defaults to None when the caller doesn't pass it
        at all (the normal, post-readiness-gate call shape) -- confirms the
        fix keys off the parameter's default, not just an explicit None."""
        candidate = PlanCandidate(action_id="ACTION6", goal_id="g7", metadata={"entity_ref": "e42"})
        state = _state_with_active_goal("g7")
        execution = _execution_result(action_id="ACTION6", candidate=candidate)
        evaluation = _evaluation_result()

        outcome = run_annatar_cycle(state, _perception_snapshot(), execution, evaluation, graph_port=None)

        assert outcome.anchor_type == "goal"
        assert outcome.anchor_ref == "g7"


class TestProbePhaseAnchorSelectionUnchanged:
    """Critical regression guard: probe-phase anchor creation
    (readiness_report is not None) must be completely unaffected --
    entity-preferring behavior stays exactly as before this card, since
    probing is specifically about mapping entities one at a time
    (A224/A230/A231)."""

    def test_probe_cycle_with_entity_ref_and_active_goal_still_anchors_on_entity(self):
        candidate = PlanCandidate(action_id="ACTION6", goal_id="g1", metadata={"entity_ref": "e42"})
        state = _state_with_active_goal("g1")
        execution = _execution_result(action_id="ACTION6", candidate=candidate)
        evaluation = _evaluation_result()

        outcome = run_annatar_cycle(
            state,
            _perception_snapshot(),
            execution,
            evaluation,
            graph_port=None,
            readiness_report={"status": "NOT_READY", "entities_mapped": 1, "entities_total": 3},
        )

        assert outcome.anchor_type == "entity"
        assert outcome.anchor_ref == "e42"


class TestGoalDirectedNoActiveGoalFallsBackSanely:
    """Edge case: goal-directed cycle, but state.active_goal is None (e.g.
    very early in goal-directed play before resolve() has run). Must not
    crash, and must fall back to whatever the pre-existing logic already
    did (entity_ref if present, else a None goal anchor -- the same
    degenerate case the original code already tolerated)."""

    def test_goal_directed_cycle_no_active_goal_falls_back_to_entity_ref(self):
        candidate = PlanCandidate(action_id="ACTION6", goal_id="g1", metadata={"entity_ref": "e9"})
        state = WorkflowState(active_goal=None)
        execution = _execution_result(action_id="ACTION6", candidate=candidate)
        evaluation = _evaluation_result()

        outcome = run_annatar_cycle(
            state, _perception_snapshot(), execution, evaluation, graph_port=None, readiness_report=None
        )

        assert outcome.anchor_type == "entity"
        assert outcome.anchor_ref == "e9"

    def test_goal_directed_cycle_no_active_goal_no_entity_ref_does_not_crash(self):
        candidate = PlanCandidate(action_id="a1", goal_id="g1")
        state = WorkflowState(active_goal=None)
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result()

        outcome = run_annatar_cycle(
            state, _perception_snapshot(), execution, evaluation, graph_port=None, readiness_report=None
        )

        assert outcome.anchor_type == "goal"
        assert outcome.anchor_ref is None


class TestGoalDirectedNoEntityRefUnchanged:
    """Goal-directed cycle where the just-executed candidate carries no
    entity_ref at all (e.g. a non-click action) -- this path already
    correctly anchored on the active goal before this card; confirm it
    still does."""

    def test_goal_directed_cycle_no_entity_ref_anchors_on_active_goal(self):
        candidate = PlanCandidate(action_id="a1", goal_id="g7")
        state = _state_with_active_goal("g7")
        execution = _execution_result(action_id="a1", candidate=candidate)
        evaluation = _evaluation_result()

        outcome = run_annatar_cycle(
            state, _perception_snapshot(), execution, evaluation, graph_port=None, readiness_report=None
        )

        assert outcome.anchor_type == "goal"
        assert outcome.anchor_ref == "g7"
