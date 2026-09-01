"""A236: _should_escalate_to_llm's `ambiguous` branch gains a patience
mechanism mirroring the existing `under_confident` branch's
`llm_patience_steps` gate -- so the exact same top-two goal_id pair, still
ambiguous with no new evidence, isn't re-asked to the LLM every single
cycle.

Confirmed live (ls20-9607627b, 2026-09-01): 15/15 goal-directed cycles
escalated, with the identical `top_two_confidence_gap` recurring across
consecutive cycles of the same anchor -- the `ambiguous` branch had no
memory of having already asked. See backlog/A236.md and
backlog/plans/A-236-goal-ambiguity-escalation-patience.md.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.ports import LLMMessage
from agents.arc4.types import GoalHypothesis, PerceivedEntity, PerceptionSnapshot, ResolvedGoal, WorkflowState


@dataclass
class RecordingLLMPort:
    response: str

    def __post_init__(self) -> None:
        self.calls: list[list[LLMMessage]] = []

    def chat(self, messages):
        self.calls.append(list(messages))
        return self.response


def _perception(*, grid_hash: str = "grid-1", entities: tuple[PerceivedEntity, ...] = ()) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": grid_hash},
        grid_hash=grid_hash,
        grid_shape=(2, 2),
        entities=entities,
    )


def _state(*, consecutive_no_progress_count: int = 0) -> WorkflowState:
    return WorkflowState(consecutive_no_progress_count=consecutive_no_progress_count)


def _llm(goal_id: str = "block-red", confidence: float = 0.91) -> RecordingLLMPort:
    return RecordingLLMPort(
        response=json.dumps({"goal_id": goal_id, "confidence": confidence, "reason": "llm pick"})
    )


_AMBIGUOUS_ENTITIES = (
    PerceivedEntity(kind="block", value="red", attributes={}),
    PerceivedEntity(kind="block", value="blue", attributes={}),
)
_AMBIGUOUS_ENTITIES_ALT_RUNNER_UP = (
    PerceivedEntity(kind="block", value="red", attributes={}),
    PerceivedEntity(kind="block", value="green", attributes={}),
)
_SINGLE_ENTITY = (PerceivedEntity(kind="block", value="red", attributes={}),)


def test_ambiguous_pair_escalates_once_then_suppresses_until_patience_elapses():
    """First cycle a pair goes ambiguous: escalate (unchanged responsiveness).
    Second cycle, identical pair, no new evidence: suppressed. Third cycle,
    streak has crossed llm_patience_steps: escalate again."""
    resolver = GoalResolver(GoalResolverLimits(ambiguity_gap=0.12, llm_patience_steps=2))
    perception = _perception(entities=_AMBIGUOUS_ENTITIES)
    llm = _llm()
    state = _state()

    resolver.resolve(state, perception, llm_port=llm)
    assert len(llm.calls) == 1
    assert state.last_ambiguous_pair == ("block-blue", "block-red")
    assert state.ambiguous_pair_streak == 0

    resolver.resolve(state, perception, llm_port=llm)
    assert len(llm.calls) == 1, "identical pair, no new evidence: must not re-escalate within patience window"
    assert state.ambiguous_pair_streak == 1

    resolver.resolve(state, perception, llm_port=llm)
    assert len(llm.calls) == 2, "streak crossed llm_patience_steps: must escalate again"
    assert state.ambiguous_pair_streak == 2


def test_genuinely_new_ambiguous_pair_escalates_immediately():
    """A different runner-up goal_id must never be starved of LLM help just
    because *some other* pair was ambiguous recently."""
    resolver = GoalResolver(GoalResolverLimits(ambiguity_gap=0.12, llm_patience_steps=2))
    llm = _llm()
    state = _state()

    resolver.resolve(state, _perception(entities=_AMBIGUOUS_ENTITIES), llm_port=llm)
    assert len(llm.calls) == 1

    resolver.resolve(state, _perception(entities=_AMBIGUOUS_ENTITIES_ALT_RUNNER_UP), llm_port=llm)
    assert len(llm.calls) == 2, "a genuinely different pair must escalate immediately, not be suppressed"
    assert state.ambiguous_pair_streak == 0


def test_streak_resets_when_pair_becomes_unambiguous_then_re_ambiguous():
    """Real evidence movement (here: dropping to a single hypothesis, so
    there is no ambiguous pair at all) must reset the streak -- re-ambiguity
    afterward is treated as new, not still-suppressed."""
    resolver = GoalResolver(GoalResolverLimits(ambiguity_gap=0.12, llm_patience_steps=2))
    llm = _llm()
    state = _state()

    resolver.resolve(state, _perception(entities=_AMBIGUOUS_ENTITIES), llm_port=llm)
    assert len(llm.calls) == 1
    assert state.last_ambiguous_pair == ("block-blue", "block-red")

    # Single hypothesis: no top-two pair exists at all this cycle, and
    # consecutive_no_progress_count (0) is below llm_patience_steps, so the
    # under_confident branch doesn't fire either -- no escalation.
    resolver.resolve(state, _perception(entities=_SINGLE_ENTITY), llm_port=llm)
    assert len(llm.calls) == 1
    assert state.last_ambiguous_pair is None
    assert state.ambiguous_pair_streak == 0

    resolver.resolve(state, _perception(entities=_AMBIGUOUS_ENTITIES), llm_port=llm)
    assert len(llm.calls) == 2, "re-ambiguity after a real gap must escalate immediately, not stay suppressed"
    assert state.ambiguous_pair_streak == 0


def test_under_confident_branch_is_unaffected_by_the_new_streak_fields():
    """Regression: the under_confident branch still reads only
    consecutive_no_progress_count, never the new ambiguous-pair fields --
    behavior must be byte-for-byte unchanged whether or not an unrelated
    ambiguous-pair streak happens to be active."""
    resolver = GoalResolver(GoalResolverLimits(ambiguity_gap=0.01, low_confidence_threshold=0.9, llm_patience_steps=2))
    # Confidences intentionally far apart (> ambiguity_gap) so `ambiguous`
    # is False; top confidence is still below low_confidence_threshold.
    hypotheses = [
        GoalHypothesis(goal_id="a", description="a", confidence=0.5),
        GoalHypothesis(goal_id="b", description="b", confidence=0.1),
    ]

    state_no_streak = _state(consecutive_no_progress_count=2)
    state_with_unrelated_streak = _state(consecutive_no_progress_count=2)
    state_with_unrelated_streak.last_ambiguous_pair = ("x", "y")
    state_with_unrelated_streak.ambiguous_pair_streak = 5

    assert resolver._should_escalate_to_llm(state_no_streak, hypotheses) is True
    assert resolver._should_escalate_to_llm(state_with_unrelated_streak, hypotheses) is True

    state_not_patient_yet = _state(consecutive_no_progress_count=1)
    assert resolver._should_escalate_to_llm(state_not_patient_yet, hypotheses) is False


def test_workflow_state_ambiguous_pair_fields_default_and_round_trip():
    """Regression guard for the to_dict()/from_dict() wiring the plan
    requires -- defaults are None/0, and a non-default value round-trips
    (tuple -> JSON-safe list -> tuple) through persistence."""
    fresh = WorkflowState()
    assert fresh.last_ambiguous_pair is None
    assert fresh.ambiguous_pair_streak == 0

    state = WorkflowState(last_ambiguous_pair=("block-blue", "block-red"), ambiguous_pair_streak=1)
    payload = state.to_dict()
    assert payload["last_ambiguous_pair"] == ["block-blue", "block-red"]
    assert payload["ambiguous_pair_streak"] == 1

    restored = WorkflowState.from_dict(json.loads(json.dumps(payload)))
    assert restored.last_ambiguous_pair == ("block-blue", "block-red")
    assert restored.ambiguous_pair_streak == 1
