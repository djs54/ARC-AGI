"""Tests for A243: `goal_resolver.py::_should_escalate_to_llm` now keys its
no-progress check off `state.goal_failure_counts[<goal_id>]` (already
correctly per-goal-scoped, see `workflow.py::_record_evaluation_state` and
`goal_resolver.py::_apply_failure_decay`'s existing use of the same field)
instead of the flat, whole-episode `state.consecutive_no_progress_count`.

Confirmed live (RE86, re86-8af5384d, 2026-09-02): `goal_id` changed four
times across ten goal-directed cycles while `consecutive_no_progress_count`
climbed straight through every change with zero resets --
`line-15`'s very first cycle inherited a count of 6 from three completely
unrelated prior goals' failures. See backlog/A243.md and
backlog/plans/A-243-per-goal-escalation-signal.md.

Test groups, matching the plan's TDD list:
  - TestShouldEscalateToLlmPerGoal: direct calls to `_should_escalate_to_llm`
    (mirrors tests/test_a236_ambiguity_escalation_patience.py's own testing
    style) -- precise control over `goal_failure_counts` and hypothesis
    confidence, independent of the graph/LLM ports.
  - TestGoalSwitchIntegration: the realistic per-cycle wiring --
    `WorkflowOrchestrator._record_evaluation_state` (which owns writing
    `goal_failure_counts`) feeding into `GoalResolver._should_escalate_to_llm`
    across a simulated multi-cycle sequence, including a deterministic
    reproduction of this card's own live evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.types import (
    EvaluationResult,
    ExecutionResult,
    GoalHypothesis,
    PlanCandidate,
    ResolvedGoal,
    WorkflowDecision,
    WorkflowState,
)
from agents.arc4.workflow import WorkflowOrchestrator


def _hypothesis(goal_id: str, confidence: float) -> GoalHypothesis:
    return GoalHypothesis(goal_id=goal_id, description=goal_id, confidence=confidence)


def _resolved_goal(goal_id: str, confidence: float) -> ResolvedGoal:
    return ResolvedGoal(selected=_hypothesis(goal_id, confidence), alternatives=(), grounding_gate_passed=True)


def _execution(goal_id: str) -> ExecutionResult:
    candidate = PlanCandidate(action_id="ACTION6@1,1", goal_id=goal_id, metadata={})
    return ExecutionResult(action_id=candidate.action_id, candidate=candidate, observation={"grid": "h"})


def _evaluation(*, meaningful_progress: bool) -> EvaluationResult:
    return EvaluationResult(
        decision=WorkflowDecision.CONTINUE,
        meaningful_progress=meaningful_progress,
        metadata={"grid_changed": meaningful_progress},
        falsification_delta=0 if meaningful_progress else 1,
    )


def _record_failed_cycle(state: WorkflowState, goal_id: str, confidence: float = 0.4) -> None:
    """Simulates one full goal-directed cycle for `goal_id` that makes no
    progress: sets it as the active goal (as workflow.py's resolve phase
    would), then records a no-progress evaluation against it (as
    workflow.py's evaluate phase would) -- the two writes
    `_should_escalate_to_llm` and `goal_failure_counts` both depend on."""
    state.active_goal = _resolved_goal(goal_id, confidence)
    WorkflowOrchestrator._record_evaluation_state(state, _execution(goal_id), _evaluation(meaningful_progress=False))


class TestShouldEscalateToLlmPerGoal:
    def test_fresh_goal_first_cycle_does_not_escalate_from_inherited_history(self):
        """The exact live-observed bug: a goal (`block-5`) fails twice under
        its own goal_id, then Annatar switches to a fresh, never-tried goal
        (`line-15`). `line-15`'s first cycle must NOT escalate purely
        because `block-5` accumulated failures -- goal_failure_counts has
        no entry for `line-15` yet (a dict miss = 0), unlike the old flat
        `consecutive_no_progress_count`, which would have already reached 2."""
        resolver = GoalResolver(GoalResolverLimits(low_confidence_threshold=0.7, llm_patience_steps=2))
        state = WorkflowState()

        _record_failed_cycle(state, "block-5")
        _record_failed_cycle(state, "block-5")
        assert state.goal_failure_counts["block-5"] == 2
        assert state.consecutive_no_progress_count == 2  # old counter still climbs, just no longer read here

        fresh_goal = [_hypothesis("line-15", confidence=0.4)]
        assert resolver._should_escalate_to_llm(state, fresh_goal) is False, (
            "a never-tried goal must not inherit an unrelated goal's failure count"
        )

    def test_revisited_goal_retains_its_own_real_prior_count(self):
        """A goal that failed before, got dropped for another goal, and is
        later revisited must see its OWN real prior history -- not 0 (as if
        it were brand new) and not contaminated by the other goal's history
        in between."""
        resolver = GoalResolver(GoalResolverLimits(low_confidence_threshold=0.7, llm_patience_steps=2))
        state = WorkflowState()

        _record_failed_cycle(state, "block-5")
        _record_failed_cycle(state, "block-5")
        _record_failed_cycle(state, "point-1")  # a different goal, one failure

        block_5_again = [_hypothesis("block-5", confidence=0.4)]
        assert resolver._should_escalate_to_llm(state, block_5_again) is True, (
            "block-5's own count (2) already met patience -- revisiting it must see that real history"
        )
        assert state.goal_failure_counts["block-5"] == 2
        assert state.goal_failure_counts["point-1"] == 1

    def test_goal_genuinely_failing_its_own_patience_steps_still_escalates(self):
        """Regression guard for real stalled-goal detection: this fix must
        only suppress the false-positive cross-goal-inheritance shape, never
        suppress a goal that has genuinely, repeatedly failed under its OWN
        goal_id."""
        resolver = GoalResolver(GoalResolverLimits(low_confidence_threshold=0.7, llm_patience_steps=2))
        state = WorkflowState()

        _record_failed_cycle(state, "line-1")
        same_goal_still_low_confidence = [_hypothesis("line-1", confidence=0.4)]
        assert resolver._should_escalate_to_llm(state, same_goal_still_low_confidence) is False, (
            "one failure is below llm_patience_steps=2 -- must not escalate yet"
        )

        _record_failed_cycle(state, "line-1")
        assert resolver._should_escalate_to_llm(state, same_goal_still_low_confidence) is True, (
            "two failures under its own goal_id meets llm_patience_steps -- must escalate"
        )

    def test_high_confidence_goal_never_escalates_regardless_of_failure_count(self):
        """The confidence gate is untouched by this card -- a high-confidence
        hypothesis must not escalate even with a large per-goal failure count."""
        resolver = GoalResolver(GoalResolverLimits(low_confidence_threshold=0.7, llm_patience_steps=2))
        state = WorkflowState()
        state.goal_failure_counts["block-5"] = 10

        assert resolver._should_escalate_to_llm(state, [_hypothesis("block-5", confidence=0.9)]) is False

    def test_consecutive_no_progress_count_alone_no_longer_triggers_escalation(self):
        """A243 Track A decision: consecutive_no_progress_count is dropped
        from this function entirely, not kept as a second OR-condition --
        see goal_resolver.py::_should_escalate_to_llm's A243 comment for the
        full reasoning (annatar_unproductive_anchor_streak already answers
        the whole-episode question, with a different, more appropriate
        consequence). A high whole-episode count with a genuinely fresh
        goal_id must not escalate."""
        resolver = GoalResolver(GoalResolverLimits(low_confidence_threshold=0.7, llm_patience_steps=2))
        state = WorkflowState(consecutive_no_progress_count=9)

        assert resolver._should_escalate_to_llm(state, [_hypothesis("fresh-goal", confidence=0.4)]) is False

    def test_no_hypotheses_never_escalates(self):
        resolver = GoalResolver(GoalResolverLimits(low_confidence_threshold=0.7, llm_patience_steps=2))
        assert resolver._should_escalate_to_llm(WorkflowState(), []) is False


class TestGoalSwitchIntegration:
    def test_re86_live_evidence_sequence_deterministic_reproduction(self):
        """Deterministic reproduction of this card's own live evidence
        (RE86, re86-8af5384d, 2026-09-02): active_goal sequence
        block-5, block-5, point-1, line-1, line-1, line-15 (x5), no real
        progress on any cycle. Under the OLD flat counter,
        consecutive_no_progress_count climbed 1->2->...->10 straight through
        every goal switch, so line-15's first cycle (cycle 6, count=6)
        would already have been treated as stalled. Under the fix, each
        goal's OWN escalation-relevant count only reflects ITS OWN
        accumulated failures."""
        resolver = GoalResolver(GoalResolverLimits(low_confidence_threshold=0.7, llm_patience_steps=2))
        state = WorkflowState()

        goal_sequence = ["block-5", "block-5", "point-1", "line-1", "line-1", "line-15", "line-15", "line-15", "line-15", "line-15"]
        # own_count_at_escalation_check[i] = what this goal_id's
        # goal_failure_counts entry is BEFORE this cycle's failure is
        # recorded (i.e. what _should_escalate_to_llm would see if asked
        # right before this cycle's evaluate phase runs).
        expected_own_count_before_cycle = []
        running: dict[str, int] = {}
        for goal_id in goal_sequence:
            expected_own_count_before_cycle.append(running.get(goal_id, 0))
            running[goal_id] = running.get(goal_id, 0) + 1

        for index, goal_id in enumerate(goal_sequence):
            own_count_before = state.goal_failure_counts.get(goal_id, 0)
            assert own_count_before == expected_own_count_before_cycle[index], (
                f"cycle {index} ({goal_id}): own failure count must reflect only this goal_id's own history"
            )
            # The whole-episode flat counter, for contrast, keeps climbing
            # every cycle regardless of goal identity -- this is exactly
            # the signal the fix stops reading here.
            assert state.consecutive_no_progress_count == index

            escalate = resolver._should_escalate_to_llm(state, [_hypothesis(goal_id, confidence=0.4)])
            if own_count_before >= 2:
                assert escalate is True, f"cycle {index} ({goal_id}): own count {own_count_before} met patience -- must escalate"
            else:
                assert escalate is False, (
                    f"cycle {index} ({goal_id}): own count {own_count_before} below patience -- must not escalate "
                    "purely from other goals' history (line-15's first two cycles, index 5 and 6, are the exact "
                    "live-observed false positive this card fixes)"
                )

            _record_failed_cycle(state, goal_id)

        # Final tallies match the live evidence shape: block-5 tried twice,
        # point-1 once, line-1 twice, line-15 five times.
        assert state.goal_failure_counts == {"block-5": 2, "point-1": 1, "line-1": 2, "line-15": 5}
        assert state.consecutive_no_progress_count == 10

    def test_resolve_end_to_end_does_not_escalate_on_fresh_goal_after_switch(self):
        """End-to-end through GoalResolver.resolve() itself (not just the
        private predicate): a fresh goal's very first resolve() call, right
        after a prior goal accumulated real failures, must not trigger an
        LLM call."""
        from dataclasses import dataclass

        from agents.arc4.ports import LLMMessage
        from agents.arc4.types import PerceivedEntity, PerceptionSnapshot

        @dataclass
        class _RecordingLLMPort:
            def __post_init__(self) -> None:
                self.calls: list[list[LLMMessage]] = []

            def chat(self, messages):
                self.calls.append(list(messages))
                return '{"goal_id": "line-15", "confidence": 0.9, "reason": "n/a"}'

        resolver = GoalResolver(GoalResolverLimits(low_confidence_threshold=0.7, llm_patience_steps=2))
        state = WorkflowState()
        _record_failed_cycle(state, "block-5")
        _record_failed_cycle(state, "block-5")

        llm = _RecordingLLMPort()
        perception = PerceptionSnapshot(
            observation={"grid": "grid-1"},
            grid_hash="grid-1",
            grid_shape=(2, 2),
            entities=(PerceivedEntity(kind="line", value="15", attributes={}),),
        )

        result = resolver.resolve(state, perception, llm_port=llm)

        assert len(llm.calls) == 0, "a brand-new goal_id must not escalate purely from a prior unrelated goal's failures"
        assert result.payload is not None
        assert result.payload.metadata["llm_escalated"] is False
