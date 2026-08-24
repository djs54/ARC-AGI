"""Tests for the record_vet -> entity_ref wiring found missing during live
verification of B359 (hippocampy's arc_get_entity_neighborhood, 2026-08-23).

A192's read side (fetch_entity_neighborhood) was complete and tested, but
the write side that's supposed to populate the ENTITY_HYPOTHESIS edge it
reads from was never wired -- record_vet's confirm_hypothesis/
contradict_hypothesis payload never included entity_ref, so no live episode
could ever have created the edge fetch_entity_neighborhood looks for,
regardless of how it was steered."""

from __future__ import annotations

from unittest.mock import Mock

from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.types import PlanCandidate, VetDecision


def _port() -> tuple[ArcGraphQueryPort, Mock]:
    mock_brain = Mock()
    mock_brain.call_tool = Mock(return_value={"status": "ok"})
    port = ArcGraphQueryPort(brain_client=mock_brain, task_id="task-1", session_id="session-1", strict=False)
    return port, mock_brain


def test_record_vet_includes_entity_ref_for_click_target_candidate():
    port, mock_brain = _port()
    candidate = PlanCandidate(action_id="ACTION6", metadata={"entity_ref": 7})
    vet = VetDecision(approved=True, candidate=candidate)

    port.record_vet(vet)

    tool_name, payload = mock_brain.call_tool.call_args[0]
    assert tool_name == "arc_confirm_hypothesis"
    assert payload["entity_ref"] == 7


def test_record_vet_omits_entity_ref_for_non_click_candidate():
    port, mock_brain = _port()
    candidate = PlanCandidate(action_id="ACTION1", metadata={})
    vet = VetDecision(approved=True, candidate=candidate)

    port.record_vet(vet)

    tool_name, payload = mock_brain.call_tool.call_args[0]
    assert tool_name == "arc_confirm_hypothesis"
    assert "entity_ref" not in payload


def test_record_vet_uses_alternative_entity_ref_when_no_primary_candidate():
    port, mock_brain = _port()
    alternative = PlanCandidate(action_id="ACTION6", metadata={"entity_ref": 9})
    vet = VetDecision(approved=False, candidate=None, alternative=alternative)

    port.record_vet(vet)

    tool_name, payload = mock_brain.call_tool.call_args[0]
    assert tool_name == "arc_contradict_hypothesis"
    assert payload["entity_ref"] == 9


def test_record_vet_contradict_path_also_includes_entity_ref():
    port, mock_brain = _port()
    candidate = PlanCandidate(action_id="ACTION6", metadata={"entity_ref": 3})
    vet = VetDecision(approved=False, candidate=candidate)

    port.record_vet(vet)

    tool_name, payload = mock_brain.call_tool.call_args[0]
    assert tool_name == "arc_contradict_hypothesis"
    assert payload["entity_ref"] == 3
