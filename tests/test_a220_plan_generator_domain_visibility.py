"""Tests for A220: surface Cynefin domain in plan_generator.py candidate metadata.

A220 reuses A217's already-built, already-tested `classify_domain()`
(agents/arc4/annatar_state_machine.py) against the same entity-neighborhood
evidence `_build_candidates` already fetches for `entity_neighborhood_grounded`,
adding a `metadata["cynefin_domain"]` field. This is deliberately visibility-only:
no candidate `score` may change as a result of this card.

Step 2 (per backlog/plans/A-220-plan-generator-domain-visibility.md) is the
regression test below (`TestScoreRegressionUnchanged`), which captures the
CURRENT candidate score values for a COMPLEX-shaped and a CONVERGED-shaped
entity-neighborhood fixture -- written and confirmed passing against the
UNMODIFIED code first, then re-confirmed passing (byte-for-byte identical
scores) after the `cynefin_domain` metadata field was added.
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


def _graph_port_with_neighborhood(neighborhood: dict) -> ArcGraphQueryPort:
    mock_brain = Mock()
    mock_brain.call_tool = Mock(
        side_effect=lambda tool, payload: {
            "arc_get_action_evidence": {"supports": 1, "contradictions": 0, "confidence": 0.3},
            "get_entity_history": {"transitions": [], "changed_count_total": 0},
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


# Two live rules that DISAGREE on to_color -> classify_domain() == COMPLEX.
COMPLEX_NEIGHBORHOOD = {
    "hypotheses": [],
    "rules": [
        {"rule_id": "r1", "confidence": 0.6, "falsified": False, "to_color": 3},
        {"rule_id": "r2", "confidence": 0.5, "falsified": False, "to_color": 7},
    ],
    "mechanics": [],
}

# Two live rules that AGREE on to_color -> classify_domain() == CONVERGED.
CONVERGED_NEIGHBORHOOD = {
    "hypotheses": [],
    "rules": [
        {"rule_id": "r1", "confidence": 0.6, "falsified": False, "to_color": 3},
        {"rule_id": "r2", "confidence": 0.4, "falsified": False, "to_color": 3},
    ],
    "mechanics": [],
}


def _best_click_candidate(neighborhood: dict):
    planner = PlanGenerator()
    state = WorkflowState()
    perception, goal = _make_perception_and_goal()
    graph_port = _graph_port_with_neighborhood(neighborhood)

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


class TestScoreRegressionUnchanged:
    """Regression guard (plan Step 2): candidate `score` must be byte-for-byte
    identical to the pre-A220 baseline for both a COMPLEX-shaped and a
    CONVERGED-shaped entity-neighborhood fixture. These exact float values
    were captured by running this test against the unmodified (pre-A220)
    code, confirming it passed, BEFORE any `classify_domain()` wiring was
    added to `_build_candidates`."""

    def test_complex_shaped_candidate_score_unchanged(self):
        best = _best_click_candidate(COMPLEX_NEIGHBORHOOD)
        # graph_positive_score (0.3, from fetch_action_evidence confidence)
        # + _voi_bonus untested fallback (0.22, no family-level rules mocked)
        # + entity_rule_weight (0.2) * max live entity-rule confidence (0.6)
        # = 0.3 + 0.22 + 0.12 = 0.64. Captured by running this test against
        # the unmodified (pre-A220) code and confirming it passed.
        assert best.score == 0.64

    def test_converged_shaped_candidate_score_unchanged(self):
        best = _best_click_candidate(CONVERGED_NEIGHBORHOOD)
        # Same arithmetic as the COMPLEX case above (both fixtures have the
        # same max live entity-rule confidence, 0.6) -- score must be
        # identical regardless of to_color agreement, since agreement only
        # affects the new cynefin_domain field, never score.
        assert best.score == 0.64


class TestCynefinDomainMetadata:
    """New A220 behavior: metadata["cynefin_domain"] reflects classify_domain()
    over the same evidence already fetched for entity_neighborhood_grounded."""

    def test_complex_shaped_candidate_gets_complex_domain(self):
        best = _best_click_candidate(COMPLEX_NEIGHBORHOOD)
        assert best.metadata.get("cynefin_domain") == "complex"

    def test_converged_shaped_candidate_gets_converged_domain(self):
        best = _best_click_candidate(CONVERGED_NEIGHBORHOOD)
        assert best.metadata.get("cynefin_domain") == "converged"

    def test_disorder_shaped_candidate_gets_disorder_domain(self):
        """No hypotheses/rules at all for this entity -> DISORDER (no evidence yet)."""
        best = _best_click_candidate({"hypotheses": [], "rules": [], "mechanics": []})
        assert best.metadata.get("cynefin_domain") == "disorder"

    def test_all_falsified_evidence_excludes_the_candidate_entirely_chaotic_unreachable_here(self):
        """A CHAOTIC-shaped fixture (evidence exists, all of it falsified)
        never actually surfaces `cynefin_domain == "chaotic"` on a surviving
        candidate: A208's pre-existing hard-exclusion (`had_any_record and
        nothing_live_remains -> continue`, plan_generator.py) already drops
        this candidate before A220's metadata is even attached, for the same
        underlying reason (the graph has tested this entity and found
        nothing that holds). This is documented here rather than asserted
        as reachable behavior -- `classify_domain()` itself is independently
        unit-tested for CHAOTIC in test_a217_domain_aware_anchor_patience.py;
        this test only confirms A220 doesn't disturb A208's exclusion."""
        neighborhood = {
            "hypotheses": [],
            "rules": [
                {"rule_id": "r1", "confidence": 0.6, "falsified": True, "to_color": 3},
            ],
            "mechanics": [],
        }
        planner = PlanGenerator()
        state = WorkflowState()
        perception, goal = _make_perception_and_goal()
        graph_port = _graph_port_with_neighborhood(neighborhood)

        candidates = planner._build_candidates(
            state,
            perception,
            goal,
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=graph_port,
        )
        click_candidates = [c for c in candidates if c.action_id.startswith("ACTION6")]
        assert click_candidates == [], "A208 exclusion must still drop this candidate, unaffected by A220"

    def test_non_action6_candidate_defaults_to_disorder_mirroring_entity_neighborhood_grounded_convention(self):
        """Non-ACTION6 candidates never hit the entity-neighborhood block at
        all -- entity_neighborhood_grounded still defaults to False (always
        present, never absent) for them. cynefin_domain must mirror that
        exact convention: always present, defaulting to "disorder" (the
        conservative "we don't know" case), never simply absent from
        metadata."""
        planner = PlanGenerator()
        state = WorkflowState()
        perception, goal = _make_perception_and_goal()
        graph_port = _graph_port_with_neighborhood(COMPLEX_NEIGHBORHOOD)

        candidates = planner._build_candidates(
            state,
            perception,
            goal,
            available_actions=["ACTION1"],
            graph_records=[],
            graph_port=graph_port,
        )
        non_click = [c for c in candidates if c.action_id == "ACTION1"]
        assert non_click, "expected an ACTION1 candidate"
        for c in non_click:
            assert "cynefin_domain" in c.metadata
            assert c.metadata["cynefin_domain"] == "disorder"

    def test_no_graph_port_capability_defaults_to_disorder(self):
        """graph_port lacking fetch_entity_neighborhood -> disorder, same
        convention as entity_neighborhood_grounded defaulting to False."""

        class StubGraphPort:
            def fetch_per_action_evidence(self, action_id):
                return {"supports": 1, "contradictions": 0, "confidence": 0.3}

            def fetch_entity_history(self, entity_ref):
                return {"transitions": [], "changed_count_total": 0}

            def fetch_rules_for_action(self, action_id):
                return []

            # Note: NO fetch_entity_neighborhood method

        planner = PlanGenerator()
        state = WorkflowState()
        perception, goal = _make_perception_and_goal()

        candidates = planner._build_candidates(
            state,
            perception,
            goal,
            available_actions=["ACTION6"],
            graph_records=[],
            graph_port=StubGraphPort(),
        )
        click_candidates = [c for c in candidates if c.action_id.startswith("ACTION6")]
        assert click_candidates
        for c in click_candidates:
            assert c.metadata.get("cynefin_domain") == "disorder"


class TestVoiBonusUntouched:
    """A220 must not modify `_voi_bonus` at all -- it only reads `rules`,
    while `classify_domain` reads `hypotheses + rules`; unifying them would
    be a scoring behavior change, explicitly out of scope for this card."""

    def test_voi_bonus_still_only_reads_rules_argument(self):
        planner = PlanGenerator()
        # _voi_bonus takes a single `rules` positional argument -- confirm
        # the signature is unchanged (would raise TypeError if A220 altered it).
        result = planner._voi_bonus([{"confidence": 0.5, "falsified": False, "to_color": 3}])
        assert isinstance(result, float)
