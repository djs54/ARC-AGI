"""Tests for A238: plan_generator.py::_fallback_candidate used to invent a
synthetic bookkeeping action_id (e.g. "probe-loop-breaker") whenever every
real action was excluded this cycle, and nothing validated that string before
it reached the live ARC API -- guaranteeing a 404 every time (confirmed live:
5/5 runs that reached this state hit it, 28 wasted real API calls, one run
burning 7/17 of its whole step budget on nothing but repeats of this).

Two tracks, both covered here:

Track A (agents/arc4/plan_generator.py): when every real candidate this
cycle is excluded, _build_candidates now leaves `candidates` empty instead
of manufacturing a doomed fallback -- PlanningResult.candidate becomes None,
which plan_vetter.py::vet already has a real (previously unreachable)
handler for ("missing plan candidate", should_replan=True), routing through
workflow.py's existing veto-retry / second-veto-through-Annatar machinery
without ever invoking execute.

Track B (arc_runtime/game_session.py): a defense-in-depth backstop -- any
action_id that isn't a real ACTION1-ACTION7 command is rejected locally,
before the network call, with the same CRASH-shaped failure a real 404
already produced.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.plan_vetter import PlanVetter
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.types import (
    GoalHypothesis,
    PerceptionSnapshot,
    PhaseResult,
    PhaseStatus,
    PlanningResult,
    ResolvedGoal,
    WorkflowPhase,
    WorkflowState,
    WorkflowStatus,
)
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator
from arc_runtime.game_session import ArcV2GameSession, _VALID_ACTION_ID


def _goal(goal_id: str = "loop-breaker") -> ResolvedGoal:
    return ResolvedGoal(selected=GoalHypothesis(goal_id=goal_id, description="test goal", confidence=0.8))


def _state(**overrides) -> WorkflowState:
    defaults = dict(
        step_index=0,
        action_attempt_counts={},
        action_falsification_counts={},
        consecutive_no_progress_count=0,
    )
    defaults.update(overrides)
    return WorkflowState(**defaults)


LIMITS = PlanGeneratorLimits()


# ---------------------------------------------------------------------------
# Track A: plan_generator.py no longer manufactures a synthetic action_id.
# ---------------------------------------------------------------------------


class TestBuildCandidatesEmptyWhenExhausted:
    def test_build_candidates_returns_empty_list_when_every_real_action_repeated_falsified(self):
        """Every real action this cycle has falsifications >= 2 (A191's
        repeated_falsified exclusion threshold) -- _build_candidates must
        return an empty list, not a synthetic "probe-*" fallback."""
        planner = PlanGenerator(LIMITS)
        state = _state(
            action_falsification_counts={"ACTION1": 2, "ACTION2": 2, "ACTION3": 3},
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2", "ACTION3"]},
            grid_hash="hash-1",
        )

        candidates = planner._build_candidates(state, perception, _goal(), ["ACTION1", "ACTION2", "ACTION3"], [])

        assert candidates == []

    def test_fallback_candidate_method_no_longer_exists(self):
        """A238: the synthetic-id-manufacturing method itself is removed,
        not just unreferenced -- guards against a future call site quietly
        resurrecting it."""
        assert not hasattr(PlanGenerator, "_fallback_candidate")


class TestGenerateSignalsExhaustionViaNoneCandidate:
    def test_generate_produces_none_candidate_when_action_space_exhausted(self):
        """Full generate() call, not just _build_candidates -- confirms the
        None-candidate signal survives ranking/selection and reaches
        PlanningResult intact, with zero candidates or alternatives."""
        planner = PlanGenerator(LIMITS)
        state = _state(
            action_falsification_counts={"ACTION1": 2, "ACTION2": 2},
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2"]},
            grid_hash="hash-1",
        )

        result = planner.generate(state, perception, _goal())

        assert isinstance(result, PhaseResult)
        assert result.payload.candidate is None
        assert result.payload.alternatives == ()

    def test_generated_candidate_action_id_never_matches_probe_pattern_when_candidates_exist(self):
        """Regression guard: when real candidates DO exist, nothing in this
        change path should start producing probe-* ids either."""
        planner = PlanGenerator(LIMITS)
        state = _state(action_falsification_counts={"ACTION1": 2})
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2"]},
            grid_hash="hash-1",
        )

        result = planner.generate(state, perception, _goal())

        assert result.payload.candidate is not None
        assert result.payload.candidate.action_id == "ACTION2"
        assert not result.payload.candidate.action_id.startswith("probe-")


class TestVetterHandlesMissingCandidate:
    def test_missing_candidate_is_vetoed_with_replan_requested(self):
        """plan_vetter.py::vet already had a None-candidate branch -- this
        confirms it does the right thing now that A238 makes it reachable:
        a clean veto asking for a replan, never a silent approval of
        `candidate=None` reaching execute."""
        vetter = PlanVetter()
        state = _state()
        planning_result = PlanningResult(candidate=None, alternatives=())

        result = vetter.vet(state, PerceptionSnapshot(observation={}, grid_hash="h"), _goal(), planning_result)

        assert result.status == PhaseStatus.VETO
        assert result.payload.approved is False
        assert result.payload.candidate is None
        assert result.payload.should_replan is True
        assert result.payload.reason == "missing plan candidate"


# ---------------------------------------------------------------------------
# Workflow-level integration: exhaustion never reaches execute.
# ---------------------------------------------------------------------------


def _perception_phase(grid_hash: str = "grid-1") -> PhaseResult[PerceptionSnapshot]:
    return PhaseResult(
        phase=WorkflowPhase.PERCEIVE,
        payload=PerceptionSnapshot(
            observation={"grid": grid_hash, "available_actions": ["ACTION1", "ACTION2"]},
            grid_hash=grid_hash,
        ),
    )


def _resolve_phase() -> PhaseResult[ResolvedGoal]:
    return PhaseResult(phase=WorkflowPhase.RESOLVE, payload=_goal())


class TestWorkflowNeverExecutesWhenActionSpaceExhausted:
    def test_double_veto_from_empty_candidates_skips_execute_and_evaluate(self):
        """End-to-end through the real PlanGenerator + PlanVetter (not
        scripted): every real action is already repeated_falsified, so both
        the initial plan/vet and the same-cycle replan retry produce
        candidate=None -> vetoed -> vetoed again -> routes to
        _route_second_veto_through_annatar, which (no Annatar configured)
        ends the episode as SKIPPED/second_veto. `execute` and `evaluate`
        are deliberately given empty response queues below -- if either is
        ever invoked, the scripted phase raises AssertionError, which is
        exactly the "zero wasted real API calls" guarantee this card is
        about."""
        state = _state(
            action_falsification_counts={"ACTION1": 2, "ACTION2": 2},
        )

        calls: list[str] = []

        def _perceive(*_args):
            calls.append("perceive")
            return _perception_phase()

        def _resolve(*_args):
            calls.append("resolve")
            return _resolve_phase()

        def _execute(*_args):
            calls.append("execute")
            raise AssertionError("execute must never be invoked when the action space is exhausted")

        def _evaluate(*_args):
            calls.append("evaluate")
            raise AssertionError("evaluate must never be invoked when the action space is exhausted")

        dependencies = WorkflowDependencies(
            perceive=_perceive,
            resolve=_resolve,
            plan=PlanGenerator(LIMITS),
            vet=PlanVetter(),
            execute=_execute,
            evaluate=_evaluate,
        )

        result = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=3)).run(state, {"grid": [[1]]})

        assert "execute" not in calls
        assert "evaluate" not in calls
        assert result.status == WorkflowStatus.SKIPPED
        assert result.reason == "second_veto"


# ---------------------------------------------------------------------------
# Track B: arc_runtime/game_session.py rejects invalid action_ids locally.
# ---------------------------------------------------------------------------


class _SpyClient:
    """Minimal httpx.Client stand-in: records calls, fails the test if
    .post is ever invoked when it shouldn't be."""

    def __init__(self, response_payload: dict[str, Any]) -> None:
        self.post_calls: list[tuple[str, dict[str, Any]]] = []
        self._response_payload = response_payload

    def post(self, url: str, json: dict[str, Any]) -> "_SpyResponse":
        self.post_calls.append((url, json))
        return _SpyResponse(self._response_payload)


class _SpyResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def _real_api_session(client: _SpyClient) -> ArcV2GameSession:
    session = ArcV2GameSession.__new__(ArcV2GameSession)  # bypass __init__ -- no real harness needed
    session._harness = None
    session._game_id = "game-1"
    session._card_id = "card-1"
    session._real_api = True
    session._client = client
    session._guid = "guid-1"
    session._prev_levels_completed = 0
    session._prev_grid_hash = None
    return session


class TestGameSessionRejectsInvalidActionId:
    @pytest.mark.parametrize("action_id", ["probe-loop-breaker", "not-a-real-action", "ACTION0", "ACTION8", "action1", "ACTION6@10,10", ""])
    def test_invalid_action_id_raises_without_network_call(self, action_id):
        client = _SpyClient({"game_id": "game-1", "guid": "guid-1", "state": "NOT_FINISHED", "frame": [[0]], "levels_completed": 0})
        session = _real_api_session(client)

        with pytest.raises(ValueError):
            session.execute_action(action_id, {}, {"session_id": "s1"})

        assert client.post_calls == [], f"no HTTP call should have been made for invalid action_id {action_id!r}"

    @pytest.mark.parametrize("action_id", ["ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5", "ACTION6", "ACTION7"])
    def test_real_action_ids_still_reach_the_transport(self, action_id):
        client = _SpyClient({"game_id": "game-1", "guid": "guid-1", "state": "NOT_FINISHED", "frame": [[0]], "levels_completed": 0})
        session = _real_api_session(client)

        session.execute_action(action_id, {"x": 1, "y": 2}, {"session_id": "s1"})

        assert len(client.post_calls) == 1
        url, _payload = client.post_calls[0]
        assert url == f"/api/cmd/{action_id}"

    def test_valid_action_id_regex_matches_only_bare_action1_through_7(self):
        for n in range(1, 8):
            assert _VALID_ACTION_ID.match(f"ACTION{n}")
        assert not _VALID_ACTION_ID.match("ACTION0")
        assert not _VALID_ACTION_ID.match("ACTION8")
        assert not _VALID_ACTION_ID.match("ACTION6@1,2")
        assert not _VALID_ACTION_ID.match("probe-loop-breaker")
