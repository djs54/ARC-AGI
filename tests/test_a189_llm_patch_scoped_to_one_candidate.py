"""A189: plan_generator.py's _apply_llm_patch must boost at most one candidate
per LLM patch, not fan out to every candidate sharing the patch's bare
action_id -- click-target (ACTION6) candidates can have several distinct
book_id coordinates sharing that action_id. See backlog/A189.md."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits, _CandidateRecord


def _click_candidate(book_id: str, score: float, repeated_falsified: bool = False) -> _CandidateRecord:
    return _CandidateRecord(
        action_id="ACTION6",
        book_id=book_id,
        payload={},
        score=score,
        rationale=f"consider ACTION6 for {book_id}",
        expected_effect="ACTION6: expect grid_change (p=0.40)",
        predicted_outcome={"kind": "grid_change", "confidence": 0.4},
        metadata={"book_id": book_id, "repeated_falsified": repeated_falsified, "untested": not repeated_falsified},
    )


class TestApplyLlmPatchScopedToOneCandidate:
    def test_patch_boosts_at_most_one_same_family_candidate(self):
        """This card's exact reproduction: 3 distinct ACTION6@x,y candidates,
        one untested and two repeated_falsified, patch names bare 'ACTION6'.
        Only the untested one should be boosted."""
        planner = PlanGenerator(PlanGeneratorLimits())
        untested = _click_candidate("ACTION6@18,17", score=0.1, repeated_falsified=False)
        falsified_a = _click_candidate("ACTION6@36,0", score=-4.648, repeated_falsified=True)
        falsified_b = _click_candidate("ACTION6@45,39", score=-4.648, repeated_falsified=True)
        candidates = [untested, falsified_a, falsified_b]

        patched = planner._apply_llm_patch(candidates, {"action_id": "ACTION6", "reason": "try clicking again"})

        boosted = [c for c in patched if c.metadata.get("llm_guidance")]
        assert len(boosted) == 1
        assert boosted[0].book_id == "ACTION6@18,17"
        for other in patched:
            if other.book_id != "ACTION6@18,17":
                assert "llm_guidance" not in other.metadata
                assert other.score == -4.648

    def test_highest_scoring_same_family_candidate_wins(self):
        """When multiple candidates are eligible for the boost, the highest-scoring one wins, not an arbitrary one."""
        planner = PlanGenerator(PlanGeneratorLimits())
        lower = _click_candidate("ACTION6@1,1", score=-0.2, repeated_falsified=False)
        higher = _click_candidate("ACTION6@2,2", score=0.15, repeated_falsified=False)
        candidates = [lower, higher]

        patched = planner._apply_llm_patch(candidates, {"action_id": "ACTION6", "reason": "try clicking again"})

        boosted = [c for c in patched if c.metadata.get("llm_guidance")]
        assert len(boosted) == 1
        assert boosted[0].book_id == "ACTION6@2,2"

    def test_a184_repeated_falsified_guard_still_applies_to_selected_target(self):
        """A184's guard (never override a repeated_falsified candidate's score) must still hold
        for whichever candidate is selected as this card's single target."""
        planner = PlanGenerator(PlanGeneratorLimits())
        only_candidate = _click_candidate("ACTION6@18,17", score=-0.48, repeated_falsified=True)

        patched = planner._apply_llm_patch([only_candidate], {"action_id": "ACTION6", "reason": "seems worth another try"})

        assert patched[0].score == -0.48
        assert patched[0].metadata.get("llm_guidance_overridden") is True

    def test_non_click_family_single_candidate_unaffected(self):
        """Regression guard: non-click actions (book_id == action_id) can only ever have one
        same-family candidate, so behavior must be identical before and after this change."""
        planner = PlanGenerator(PlanGeneratorLimits())
        candidate = _CandidateRecord(
            action_id="ACTION1", book_id="ACTION1", payload={}, score=0.1,
            rationale="consider ACTION1", expected_effect="ACTION1: expect grid_change (p=0.30)",
            predicted_outcome={"kind": "grid_change", "confidence": 0.3},
            metadata={"book_id": "ACTION1", "repeated_falsified": False, "untested": True},
        )

        patched = planner._apply_llm_patch([candidate], {"action_id": "ACTION1", "reason": "looks promising"})

        assert patched[0].metadata.get("llm_guidance") is True
        assert patched[0].score >= PlanGeneratorLimits().replan_feedback_bonus

    def test_unmatched_action_id_fallback_unaffected(self):
        """Regression guard: the LLM naming an action_id absent from the candidate list is a
        separate path (constructs a single new candidate) this card doesn't touch."""
        planner = PlanGenerator(PlanGeneratorLimits())
        candidate = _click_candidate("ACTION6@1,1", score=0.1, repeated_falsified=False)

        patched = planner._apply_llm_patch([candidate], {"action_id": "ACTION9", "reason": "hallucinated pick"})

        assert len(patched) == 2
        new_candidate = next(c for c in patched if c.action_id == "ACTION9")
        assert new_candidate.score >= PlanGeneratorLimits().replan_feedback_bonus
        original = next(c for c in patched if c.action_id == "ACTION6")
        assert "llm_guidance" not in original.metadata
