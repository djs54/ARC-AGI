"""A155: goal_resolver.py's free-text (non-JSON) LLM fallback parser must never
manufacture a goal_id from raw reasoning prose.

Live smoke evidence (game sp80-589a99af, 2026-08-04, right after A154 landed):
active_goal_hypothesis_id was a 300+ word slugified paragraph on 6/10 steps.
Root cause: _parse_llm_response's regex fallback (used when the LLM response
isn't clean JSON) has an unbounded `reason` capture, and _merge_llm_patch fell
back to slugifying that captured reason into a goal_id when no clean
`goal_id: X` pattern was found. This is the free-text sibling of the bug A154
fixed on the clean-JSON path.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.ports import LLMMessage
from agents.arc4.types import GoalHypothesis, PerceivedEntity, PerceptionSnapshot, WorkflowState

# Shaped like the actual live-run Ollama output: long free-text reasoning that
# contains the word "reason" partway through, no clean "goal_id: X" substring.
FREETEXT_NO_GOAL_ID = (
    "To resolve the ambiguity in the ARC goal we need to select the candidate "
    "with the highest confidence and most relevant description. In this case "
    "the candidate with the highest confidence is not necessarily the best "
    "choice because the descriptions also play a crucial role in determining "
    "the relevance of each candidate. However, based on the given data I "
    "would recommend selecting the first candidate. Confidence: 0.57. "
    "Description: use the line-14 to satisfy the 64x64 grid objective. "
    "Evidence: entity line-14. This candidate has a confidence of 0.57 which "
    "is the highest among all candidates, and its description seems to be "
    "most relevant as it directly mentions satisfying the 64x64 grid "
    "objective. Therefore I would reason that this candidate is the best "
    "choice, with a confidence of 0.57."
)

FREETEXT_WITH_GOAL_ID = (
    "After reviewing the candidates, goal_id: block-red is the best choice. "
    "confidence: 0.8. reason: it directly matches the visible objective."
)


@dataclass
class RecordingLLMPort:
    response: str

    def __post_init__(self) -> None:
        self.calls: list[list[LLMMessage]] = []

    def chat(self, messages):
        self.calls.append(list(messages))
        return self.response


def _perception() -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": "grid-1"},
        grid_hash="grid-1",
        grid_shape=(2, 2),
        entities=(
            PerceivedEntity(kind="line", value="14", attributes={}),
            PerceivedEntity(kind="block", value="12", attributes={}),
        ),
    )


def _state() -> WorkflowState:
    return WorkflowState(consecutive_no_progress_count=2)


def _baseline_hypotheses() -> list[GoalHypothesis]:
    return [
        GoalHypothesis(goal_id="line-14", description="Use line-14", confidence=0.57, evidence=(), metadata={}),
        GoalHypothesis(goal_id="block-12", description="Use block-12", confidence=0.52, evidence=(), metadata={}),
    ]


def test_freetext_response_without_goal_id_does_not_create_hypothesis():
    resolver = GoalResolver()
    patch = resolver._parse_llm_response(FREETEXT_NO_GOAL_ID)

    baseline = _baseline_hypotheses()
    assert patch is not None  # confidence/reason did match something
    assert patch.get("goal_id") is None

    updated = resolver._merge_llm_patch(baseline, patch)

    assert [h.goal_id for h in updated] == [h.goal_id for h in baseline]
    assert len(updated) == len(baseline)


def test_freetext_response_with_clean_goal_id_still_works():
    resolver = GoalResolver()
    patch = resolver._parse_llm_response(FREETEXT_WITH_GOAL_ID)

    assert patch is not None
    assert patch.get("goal_id") == "block-red"

    updated = resolver._merge_llm_patch(_baseline_hypotheses(), patch)

    assert "block-red" in [h.goal_id for h in updated]
    new_hypothesis = next(h for h in updated if h.goal_id == "block-red")
    assert new_hypothesis.confidence == 0.8


def test_reason_capture_is_bounded():
    resolver = GoalResolver()
    long_reason = "x" * 1000
    response = f"goal_id: some-id reason: {long_reason}"

    patch = resolver._parse_llm_response(response)

    assert patch is not None
    assert len(patch.get("reason", "")) <= 200


def test_resolve_end_to_end_no_garbage_goal_after_freetext_llm_response():
    resolver = GoalResolver(GoalResolverLimits(ambiguity_gap=0.12, low_confidence_threshold=0.7, llm_patience_steps=2))
    llm = RecordingLLMPort(response=FREETEXT_NO_GOAL_ID)

    result = resolver.resolve(_state(), _perception(), llm_port=llm)

    assert len(llm.calls) == 1  # escalation did fire (two close-confidence entities)
    assert result.payload is not None
    all_goal_ids = [result.payload.selected.goal_id] + [h.goal_id for h in result.payload.alternatives]
    for goal_id in all_goal_ids:
        assert len(goal_id) < 100, f"unexpectedly long goal_id (likely manufactured from prose): {goal_id!r}"
    assert result.payload.selected.goal_id in ("line-14", "block-12")


def test_json_response_regression_guard_still_works():
    """A154 regression guard: clean JSON with goal_id still merges correctly."""
    resolver = GoalResolver()
    patch = resolver._parse_llm_response(json.dumps({"goal_id": "line-14", "confidence": 0.91, "reason": "clear match"}))

    assert patch is not None
    assert patch["goal_id"] == "line-14"

    updated = resolver._merge_llm_patch(_baseline_hypotheses(), patch)
    merged = next(h for h in updated if h.goal_id == "line-14")
    assert merged.confidence == 0.91
