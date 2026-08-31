"""Tests for A224 Task 2: plan_generator.py's scoring actually reads
`cynefin_domain` -- A220 surfaced it in candidate metadata, nothing scored
on it, for the same "build the capability, never force anything downstream
to depend on it" reason found five times across this session's
/arc-graph-engineering-review pass. COMPLEX gets a probe-worth bonus
(mirrors A217's own domain-aware patience reasoning, applied to scoring
instead of DEEPENING patience). CHAOTIC gets a penalty -- but CHAOTIC via
the rule/hypothesis path is already excluded by A208's hard-exclusion
before reaching this scoring code (per A221 Finding 3), so this also adds
the same fetch_entity_history-based DISORDER->CHAOTIC reclassification
A218 added to annatar_signals.py, giving plan_generator.py's own
classify_domain() call the same "confirmed-inert via repeated no-op"
visibility -- otherwise the CHAOTIC penalty would be dead code, unreachable
in practice, same mistake this whole card exists to stop repeating.
"""

from unittest.mock import Mock

from agents.arc4.plan_generator import PlanGenerator
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.types import (
    PerceptionSnapshot,
    PerceivedEntity,
    ResolvedGoal,
    GoalHypothesis,
    WorkflowState,
)


def _make_perception_and_goal():
    entity = PerceivedEntity(
        kind="block",
        value=3,
        attributes={
            "coverage": 0.1,
            "cell_count": 5,
            "centroid": (15.0, 20.0),
            "entity_ref": 123,
        },
    )
    perception = PerceptionSnapshot(
        observation={},
        grid_hash="test_hash",
        entities=[entity],
        grid_shape=(30, 30),
        metadata={},
    )
    goal = ResolvedGoal(
        selected=GoalHypothesis(
            goal_id="test_goal",
            description="reach corner",
            confidence=0.5,
            evidence=[],
            metadata={},
        ),
        alternatives=[],
        grounding_gate_passed=True,
        metadata={},
    )
    return perception, goal


def _graph_port_with(neighborhood: dict, entity_history: dict | None = None) -> ArcGraphQueryPort:
    mock_brain = Mock()
    mock_brain.call_tool = Mock(
        side_effect=lambda tool, payload: {
            "arc_get_action_evidence": {"supports": 1, "contradictions": 0, "confidence": 0.3},
            "get_entity_history": entity_history or {"transitions": [], "changed_count_total": 0},
            "get_rules_for_action": {"rules": []},
            "arc_get_entity_neighborhood": neighborhood,
        }.get(tool, {})
    )
    return ArcGraphQueryPort(
        brain_client=mock_brain,
        task_id="test_task",
        session_id="test_session",
        strict=False,
    )


COMPLEX_NEIGHBORHOOD = {
    "hypotheses": [],
    "rules": [
        {"rule_id": "r1", "confidence": 0.6, "falsified": False, "to_color": 3},
        {"rule_id": "r2", "confidence": 0.5, "falsified": False, "to_color": 7},
    ],
    "mechanics": [],
}

CONVERGED_NEIGHBORHOOD = {
    "hypotheses": [],
    "rules": [
        {"rule_id": "r1", "confidence": 0.6, "falsified": False, "to_color": 3},
        {"rule_id": "r2", "confidence": 0.4, "falsified": False, "to_color": 3},
    ],
    "mechanics": [],
}

# No rule/hypothesis evidence at all -- classify_domain() alone reads
# DISORDER. Combined with entity_history showing >=2 confirmed-zero-effect
# transitions, the new A218-style boost reclassifies this to CHAOTIC.
EMPTY_NEIGHBORHOOD = {"hypotheses": [], "rules": [], "mechanics": []}
CONFIRMED_INERT_HISTORY = {
    "transitions": [{"action_id": "ACTION6", "step": 1}, {"action_id": "ACTION6", "step": 2}],
    "changed_count_total": 0,
}


def _best_click_candidate(neighborhood: dict, entity_history: dict | None = None):
    planner = PlanGenerator()
    state = WorkflowState()
    perception, goal = _make_perception_and_goal()
    graph_port = _graph_port_with(neighborhood, entity_history)

    candidates = planner._build_candidates(
        state,
        perception,
        goal,
        available_actions=["ACTION6"],
        graph_records=[],
        graph_port=graph_port,
    )
    click_candidates = [c for c in candidates if c.action_id.startswith("ACTION6")]
    assert click_candidates, "expected at least one ACTION6 click candidate"
    return max(click_candidates, key=lambda c: c.score)


class TestComplexDomainGetsScoringBonus:
    def test_complex_scores_higher_than_converged(self):
        complex_best = _best_click_candidate(COMPLEX_NEIGHBORHOOD)
        converged_best = _best_click_candidate(CONVERGED_NEIGHBORHOOD)
        assert complex_best.metadata["cynefin_domain"] == "complex"
        assert converged_best.metadata["cynefin_domain"] == "converged"
        assert complex_best.score > converged_best.score


class TestChaoticDomainGetsScoringPenalty:
    def test_confirmed_inert_entity_reclassifies_to_chaotic_and_scores_lower(self):
        """The entity_history-based boost makes CHAOTIC reachable here at
        all -- without it, this fixture (empty hypotheses/rules) would just
        read DISORDER forever, the exact A213/A218 gap."""
        chaotic_best = _best_click_candidate(EMPTY_NEIGHBORHOOD, CONFIRMED_INERT_HISTORY)
        disorder_best = _best_click_candidate(EMPTY_NEIGHBORHOOD, {"transitions": [], "changed_count_total": 0})
        assert chaotic_best.metadata["cynefin_domain"] == "chaotic"
        assert disorder_best.metadata["cynefin_domain"] == "disorder"
        assert chaotic_best.score < disorder_best.score

    def test_single_no_op_transition_does_not_reclassify(self):
        """A187-style repeated-not-single threshold, same as A218's own --
        one no-op sample is barely more informative than zero."""
        one_transition_history = {"transitions": [{"action_id": "ACTION6", "step": 1}], "changed_count_total": 0}
        best = _best_click_candidate(EMPTY_NEIGHBORHOOD, one_transition_history)
        assert best.metadata["cynefin_domain"] == "disorder"


class TestVoiBonusAndA208ExclusionUntouched:
    def test_voi_bonus_still_only_reads_rules_argument(self):
        """Regression: _voi_bonus itself is not unified with the new domain
        bonus -- it remains a separate, single-argument function."""
        planner = PlanGenerator()
        import inspect
        sig = inspect.signature(planner._voi_bonus)
        assert list(sig.parameters) == ["rules"]

    def test_a208_hard_exclusion_still_excludes_all_falsified_rule_evidence(self):
        """Regression: an entity with real rule/hypothesis evidence that's
        ALL falsified is still hard-excluded by A208 before ever reaching
        the new domain-bonus code -- this card doesn't touch that path."""
        all_falsified_neighborhood = {
            "hypotheses": [],
            "rules": [{"rule_id": "r1", "confidence": 0.6, "falsified": True, "to_color": 3}],
            "mechanics": [],
        }
        planner = PlanGenerator()
        state = WorkflowState()
        perception, goal = _make_perception_and_goal()
        graph_port = _graph_port_with(all_falsified_neighborhood)
        candidates = planner._build_candidates(
            state, perception, goal, available_actions=["ACTION6"], graph_records=[], graph_port=graph_port,
        )
        click_candidates = [c for c in candidates if c.action_id.startswith("ACTION6")]
        assert not click_candidates, "A208's hard-exclusion should have dropped this candidate entirely"
