"""A227: solve_phase_summary must export the readiness-gate fields A224/A225
added to WorkflowState -- previously absent from the end-of-episode summary
even though the per-step trace (_step_snapshot) already carried them
correctly."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.types import WorkflowState


def test_solve_phase_summary_includes_readiness_gate_fields():
    state = WorkflowState(
        readiness_gate_resolved=True,
        readiness_gate_partial=True,
        readiness_gate_entities_mapped=3,
        readiness_gate_entities_total=7,
    )

    summary = ArcV2Telemetry._solve_phase_summary(state)

    assert summary["readiness_gate_resolved"] is True
    assert summary["readiness_gate_partial"] is True
    assert summary["readiness_gate_entities_mapped"] == 3
    assert summary["readiness_gate_entities_total"] == 7


def test_solve_phase_summary_readiness_fields_default_correctly():
    state = WorkflowState()

    summary = ArcV2Telemetry._solve_phase_summary(state)

    assert summary["readiness_gate_resolved"] is False
    assert summary["readiness_gate_partial"] is False
    assert summary["readiness_gate_entities_mapped"] is None
    assert summary["readiness_gate_entities_total"] is None
