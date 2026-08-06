"""Tests for A168: plan_generator.py's LLM escalation was silently discarding
every response because json.loads() has zero tolerance for prose, and there
was no fallback parsing (unlike goal_resolver.py's equivalent call site)."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, ResolvedGoal, WorkflowState


@dataclass
class _StubCandidate:
    action_id: str


# The exact reproduction text confirmed live against llama3.1:8b for this
# prompt shape (see backlog/A168.md's Problem section).
PROSE_RESPONSE = (
    'The best ARC action_id to try next would be "ACTION7".\n\n'
    "Here's why:\n\n"
    "* The score of ACTION6 is -0.58, which indicates a relatively low confidence "
    "level in its effectiveness.\n"
    "* The score of ACTION7 is -0.42, which is higher than ACTION6 and closer to 0 "
    "(the neutral point). This suggests that ACTION7 has a slightly better chance "
    "of success.\n\n"
    'Therefore, trying "ACTION7" next would be the most logical choice based on the '
    "provided scores."
)


class TestParseLlmResponseFallback:
    def test_prose_response_extracts_action_id_via_candidate_scan(self):
        candidates = [_StubCandidate("ACTION6"), _StubCandidate("ACTION7")]
        parsed = PlanGenerator._parse_llm_response(PROSE_RESPONSE, candidates)
        assert parsed is not None
        assert parsed["action_id"] == "ACTION7"

    def test_well_formed_json_still_works_directly(self):
        candidates = [_StubCandidate("ACTION6"), _StubCandidate("ACTION7")]
        response = '{"action_id": "ACTION7", "reason": "x"}'
        parsed = PlanGenerator._parse_llm_response(response, candidates)
        assert parsed == {"action_id": "ACTION7", "reason": "x"}

    def test_response_mentioning_no_candidate_returns_none(self):
        candidates = [_StubCandidate("ACTION6"), _StubCandidate("ACTION7")]
        response = "I'm not sure which action would work best here, sorry."
        assert PlanGenerator._parse_llm_response(response, candidates) is None

    def test_key_value_shaped_prose_matches_via_regex(self):
        candidates = [_StubCandidate("ACTION6"), _StubCandidate("ACTION7")]
        response = "action_id: ACTION6 because it's untested"
        parsed = PlanGenerator._parse_llm_response(response, candidates)
        assert parsed is not None
        assert parsed["action_id"] == "ACTION6"

    def test_longest_id_first_avoids_short_id_false_match(self):
        candidates = [_StubCandidate("ACTION1"), _StubCandidate("ACTION10")]
        response = 'Go with "ACTION10" since it is untested.'
        parsed = PlanGenerator._parse_llm_response(response, candidates)
        assert parsed is not None
        assert parsed["action_id"] == "ACTION10"


class TestApplyLlmPatchEndToEnd:
    def test_apply_llm_patch_actually_invoked_end_to_end(self):
        class _ProseLLMPort:
            def chat(self, messages):
                return PROSE_RESPONSE

        planner = PlanGenerator(PlanGeneratorLimits())
        state = WorkflowState(
            step_index=0,
            action_attempt_counts={},
            action_falsification_counts={},
            consecutive_no_progress_count=0,
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION6", "ACTION7"]},
            grid_hash="hash-1",
        )
        goal = ResolvedGoal(
            selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8),
        )

        result = planner.generate(state, perception, goal, llm_port=_ProseLLMPort()).payload

        assert result.candidate.action_id == "ACTION7"
        assert result.candidate.metadata.get("llm_guidance") is True
