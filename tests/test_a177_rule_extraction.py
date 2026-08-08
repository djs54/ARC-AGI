"""Tests for A177's deterministic rule-signature extraction: per-action tally
counters answer "has this worked before," never "what does this do." A rule
signature is a falsifiable claim (action turns color X into color Y) derived
from A176's already-computed color-transition histogram. Pure, no LLM, no
graph dependency -- see backlog/A177.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.rule_extraction import ExistingRule, RuleSignature, action_family, classify_signature, extract_candidate_signatures


class TestActionFamily:
    def test_strips_click_target_suffix(self):
        assert action_family("ACTION6@12,34") == "ACTION6"

    def test_base_action_unchanged(self):
        assert action_family("ACTION1") == "ACTION1"


class TestExtractCandidateSignatures:
    def test_extracts_one_signature_per_color_pair(self):
        color_transitions = [{"from": 2, "to": 5, "count": 3}, {"from": 1, "to": 9, "count": 1}]
        signatures = extract_candidate_signatures("ACTION6@1,2", color_transitions)
        assert set(signatures) == {
            RuleSignature(action_family="ACTION6", from_color=2, to_color=5),
            RuleSignature(action_family="ACTION6", from_color=1, to_color=9),
        }

    def test_empty_transitions_yields_no_signatures(self):
        assert extract_candidate_signatures("ACTION1", []) == []


class TestClassifySignature:
    def test_matching_existing_rule_confirms(self):
        signature = RuleSignature(action_family="ACTION6", from_color=2, to_color=5)
        existing = [ExistingRule(rule_id="r1", action_family="ACTION6", from_color=2, to_color=5, confidence=0.6)]
        result = classify_signature(signature, existing)
        assert result["result"] == "confirms"
        assert result["confirmed_rule_ids"] == ["r1"]
        assert result["falsified_rule_ids"] == []

    def test_contradicting_existing_rule_falsifies(self):
        signature = RuleSignature(action_family="ACTION6", from_color=2, to_color=9)
        existing = [ExistingRule(rule_id="r1", action_family="ACTION6", from_color=2, to_color=5, confidence=0.6)]
        result = classify_signature(signature, existing)
        assert result["result"] == "falsifies"
        assert result["falsified_rule_ids"] == ["r1"]
        assert result["confirmed_rule_ids"] == []

    def test_no_relevant_rule_is_new(self):
        signature = RuleSignature(action_family="ACTION6", from_color=2, to_color=5)
        existing = [ExistingRule(rule_id="r1", action_family="ACTION1", from_color=2, to_color=5, confidence=0.6)]
        result = classify_signature(signature, existing)
        assert result["result"] == "new"

    def test_unrelated_action_family_ignored(self):
        signature = RuleSignature(action_family="ACTION6", from_color=2, to_color=9)
        existing = [ExistingRule(rule_id="r1", action_family="ACTION1", from_color=2, to_color=5, confidence=0.6)]
        result = classify_signature(signature, existing)
        assert result["result"] == "new"
        assert result["falsified_rule_ids"] == []

    def test_multiple_rules_can_all_confirm(self):
        signature = RuleSignature(action_family="ACTION6", from_color=2, to_color=5)
        existing = [
            ExistingRule(rule_id="r1", action_family="ACTION6", from_color=2, to_color=5),
            ExistingRule(rule_id="r2", action_family="ACTION6", from_color=2, to_color=5),
        ]
        result = classify_signature(signature, existing)
        assert set(result["confirmed_rule_ids"]) == {"r1", "r2"}
