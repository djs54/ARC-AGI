"""A253: `_route_second_veto_through_annatar`'s Annatar call never set
`state.annatar_degraded`, unlike the probe-path (workflow.py:258-266) and
normal-cycle (workflow.py:556-584) call sites, which both set
`state.annatar_degraded = outcome.degraded` immediately after their own
`self._dependencies.annatar(...)` call. This left a graph-unreachable
degradation during double-veto routing completely invisible in telemetry.

Fix: one line, `state.annatar_degraded = outcome.degraded`, added
immediately after `_route_second_veto_through_annatar`'s
`self._dependencies.annatar(...)` call, before any branching on the
result -- mirroring the exact placement convention of the other two call
sites. See backlog/A253.md.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.types import AnnatarOutcome, WorkflowState
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator

# Reuse the existing WorkflowOrchestrator regression fixtures, same as
# test_a202_annatar_orchestrator_integration.py's TestSecondVetoRoutesThroughAnnatar.
from test_arc4_workflow import (
    _dependencies as _shared_dependencies,
    _goal,
    _plan,
    _vet,
)


def _double_veto_deps(calls, mock_annatar):
    return _shared_dependencies(
        calls,
        overrides={
            "vet": [_vet(False, reason="first veto"), _vet(False, reason="second veto")],
            "plan": [_plan(), _plan()],
            "resolve": [_goal(), _goal()],
        },
    )


class TestSecondVetoAnnatarDegradedVisibility:
    def test_degraded_true_outcome_sets_state_annatar_degraded_true(self):
        calls: list[str] = []
        mock_annatar = MagicMock(return_value=AnnatarOutcome(decision="terminate", degraded=True))
        deps = _double_veto_deps(calls, mock_annatar)
        deps.annatar = mock_annatar

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(
            WorkflowState(), {"grid": [[1]]}
        )

        assert mock_annatar.call_count == 1
        assert result.state.annatar_degraded is True

    def test_degraded_false_outcome_sets_state_annatar_degraded_false(self):
        # Regression guard: confirms the assignment reads the real value
        # rather than hardcoding True.
        calls: list[str] = []
        mock_annatar = MagicMock(return_value=AnnatarOutcome(decision="terminate", degraded=False))
        deps = _double_veto_deps(calls, mock_annatar)
        deps.annatar = mock_annatar

        result = WorkflowOrchestrator(deps, limits=WorkflowLimits(max_cycles=3)).run(
            WorkflowState(), {"grid": [[1]]}
        )

        assert mock_annatar.call_count == 1
        assert result.state.annatar_degraded is False
