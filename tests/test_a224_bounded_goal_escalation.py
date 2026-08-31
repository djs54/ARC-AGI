"""Tests for A224 Task 1: goal_resolver's LLM escalation bounded to
graph-confirmed candidates. Found via /arc-graph-engineering-review applied
to a live-smoke result (2026-08-31): _merge_llm_patch's unmatched-goal_id
branch let an LLM invent a brand-new hypothesis with zero graph evidence,
which _apply_grounding_gate never filters (it's a pass-through when there's
no prior active goal -- exactly the cold-start case this whole investigation
started from) and _order_hypotheses ranks purely by confidence -- so an
LLM-invented, ungrounded goal_id could become `selected` outright. The graph
was informing resolve's escalation, not bounding it, exactly what Shift C
says shouldn't happen.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver
from agents.arc4.types import GoalHypothesis


class TestMergeLlmPatchBoundedToGraphConfirmedCandidates:
    def test_unmatched_llm_goal_id_is_not_accepted_as_a_new_ungrounded_hypothesis(self):
        """A224: an LLM proposing a goal_id that wasn't in the presented,
        graph-derived candidate list must not become a real hypothesis with
        zero graph evidence -- that's the LLM escaping the graph's bound
        entirely."""
        resolver = GoalResolver()
        hypotheses = [
            GoalHypothesis(goal_id="blob-3", description="d", confidence=0.3, evidence=("entity:blob:3",)),
        ]
        patch = {"goal_id": "invented-goal-not-in-list", "confidence": 0.9, "reason": "looks promising"}

        updated = resolver._merge_llm_patch(hypotheses, patch)

        goal_ids = {h.goal_id for h in updated}
        assert "invented-goal-not-in-list" not in goal_ids
        assert goal_ids == {"blob-3"}

    def test_matched_llm_goal_id_still_gets_its_confidence_bump(self):
        """Regression: the LLM voting FOR a graph-derived candidate must
        still work exactly as before -- only the ungrounded-invention path
        changes."""
        resolver = GoalResolver()
        hypotheses = [
            GoalHypothesis(goal_id="blob-3", description="d", confidence=0.3, evidence=("entity:blob:3",)),
            GoalHypothesis(goal_id="blob-5", description="d2", confidence=0.2, evidence=("entity:blob:5",)),
        ]
        patch = {"goal_id": "blob-3", "confidence": 0.85, "reason": "strong signal"}

        updated = resolver._merge_llm_patch(hypotheses, patch)

        goal_ids = {h.goal_id for h in updated}
        assert goal_ids == {"blob-3", "blob-5"}
        matched = next(h for h in updated if h.goal_id == "blob-3")
        assert matched.confidence == 0.85
        assert matched.metadata.get("llm_patch") is True

    def test_no_goal_id_in_patch_leaves_hypotheses_unchanged(self):
        """Regression: a patch with no goal_id at all (e.g. an unparseable
        LLM response) must not add or remove anything."""
        resolver = GoalResolver()
        hypotheses = [
            GoalHypothesis(goal_id="blob-3", description="d", confidence=0.3),
        ]
        patch = {"confidence": 0.5, "reason": "unclear"}

        updated = resolver._merge_llm_patch(hypotheses, patch)

        assert [h.goal_id for h in updated] == ["blob-3"]
        assert updated[0].confidence == 0.3
