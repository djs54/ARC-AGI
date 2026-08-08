"""Tests for A178: the architecture's mission statement says the graph
should decide "what experiment is worth paying for next." Before this card
that was a flat untested_bonus constant regardless of what trying an action
would actually teach. This replaces it with a value-of-information signal:
untested actions whose live (A177) rules genuinely disagree score higher
than ones whose rules already agree (low information value) or that have no
rules at all (falls back to the flat bonus). See backlog/A178.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits


class TestVoiBonus:
    def test_no_rules_falls_back_to_flat_untested_bonus(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        assert planner._voi_bonus([]) == planner._limits.untested_bonus

    def test_agreeing_rules_score_below_flat_bonus(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        rules = [
            {"to_color": 5, "confidence": 0.6, "falsified": False},
            {"to_color": 5, "confidence": 0.4, "falsified": False},
        ]
        bonus = planner._voi_bonus(rules)
        assert bonus == planner._limits.voi_agreement_bonus
        assert bonus < planner._limits.untested_bonus

    def test_disagreeing_rules_score_above_flat_bonus(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        rules = [
            {"to_color": 5, "confidence": 0.6, "falsified": False},
            {"to_color": 9, "confidence": 0.5, "falsified": False},
        ]
        bonus = planner._voi_bonus(rules)
        assert bonus > planner._limits.untested_bonus

    def test_disagreement_bonus_is_capped(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        rules = [{"to_color": i, "confidence": 0.5, "falsified": False} for i in range(20)]
        bonus = planner._voi_bonus(rules)
        assert bonus == planner._limits.voi_max_bonus

    def test_falsified_rules_are_excluded_from_disagreement(self):
        planner = PlanGenerator(PlanGeneratorLimits())
        rules = [
            {"to_color": 5, "confidence": 0.6, "falsified": False},
            {"to_color": 9, "confidence": 0.5, "falsified": True},
        ]
        # Only one live rule remains (to_color=5) -- agreement, not disagreement.
        bonus = planner._voi_bonus(rules)
        assert bonus == planner._limits.voi_agreement_bonus


class TestPlanGeneratorConsumesVoiBonus:
    def test_discriminating_candidate_outranks_non_discriminating_one(self):
        class _DisagreementGraphPort:
            def fetch_goal_evidence(self, perception, goal=None):
                return {}

            def fetch_per_action_evidence(self, action_id):
                return {"supports": 0, "contradictions": 0, "confidence": 0.0, "attempts": 0}

            def fetch_untested_actions(self):
                return []

            def fetch_rules_for_action(self, action_id):
                if action_id == "ACTION1":
                    return [
                        {"rule_id": "r1", "from_color": 2, "to_color": 5, "confidence": 0.6, "falsified": False},
                        {"rule_id": "r2", "from_color": 2, "to_color": 9, "confidence": 0.5, "falsified": False},
                    ]
                if action_id == "ACTION2":
                    return [
                        {"rule_id": "r3", "from_color": 2, "to_color": 5, "confidence": 0.6, "falsified": False},
                    ]
                return []

        from agents.arc4.types import GoalHypothesis, PerceptionSnapshot, ResolvedGoal, WorkflowState

        planner = PlanGenerator(PlanGeneratorLimits())
        state = WorkflowState(
            step_index=0,
            action_attempt_counts={},
            action_falsification_counts={},
            consecutive_no_progress_count=0,
        )
        perception = PerceptionSnapshot(
            observation={"grid": "hash-1", "available_actions": ["ACTION1", "ACTION2"]},
            grid_hash="hash-1",
        )
        goal = ResolvedGoal(selected=GoalHypothesis(goal_id="goal-1", description="test goal", confidence=0.8))

        result = planner.generate(state, perception, goal, graph_port=_DisagreementGraphPort()).payload
        candidates_by_id = {c.action_id: c for c in [result.candidate, *result.alternatives]}

        assert candidates_by_id["ACTION1"].score > candidates_by_id["ACTION2"].score
