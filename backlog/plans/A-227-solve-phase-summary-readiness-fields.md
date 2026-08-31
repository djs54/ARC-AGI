# A227 — `solve_phase_summary` Readiness-Gate Fields: Plan

## Card metadata

- Card: `backlog/A227.md`
- Depends on: A224 Task 5 (added the fields to `WorkflowState`), A225 (fixed the underlying data these fields depend on)

## Summary

`agents/arc4/telemetry.py::ArcV2Telemetry._solve_phase_summary` (a `@staticmethod`, lines 437-450) builds the `solve_phase_summary` dict exported into `submission_results_single.json`. It reads several `WorkflowState` fields directly but was never updated when A224 Task 5 added `readiness_gate_resolved`/`readiness_gate_partial`/`readiness_gate_entities_mapped`/`readiness_gate_entities_total` to `WorkflowState`.

## Implementation approach

### Files

- Modify: `agents/arc4/telemetry.py::_solve_phase_summary`
- Test: `tests/test_a227_solve_phase_summary_readiness_fields.py` — new file

### Step 1: write the failing test

```python
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
```

Run: `.venv/bin/python -m pytest tests/test_a227_solve_phase_summary_readiness_fields.py -v`
Expected: FAIL — `KeyError: 'readiness_gate_resolved'`.

### Step 2: implement

```python
    @staticmethod
    def _solve_phase_summary(state: WorkflowState) -> dict[str, Any]:
        return {
            "active_goal_id": state.active_goal.selected.goal_id if state.active_goal is not None else None,
            "active_goal_confidence": state.active_goal.selected.confidence if state.active_goal is not None else 0.0,
            "replan_passes": state.replan_passes,
            "no_progress_count": state.consecutive_no_progress_count,
            "action_attempt_counts": dict(state.action_attempt_counts),
            "action_falsification_counts": dict(state.action_falsification_counts),
            "annatar_unproductive_anchor_streak": state.annatar_unproductive_anchor_streak,
            # A227: readiness-gate fields (A224/A225) -- previously absent
            # from this end-of-episode summary even though _step_snapshot
            # already carried them correctly per-step.
            "readiness_gate_resolved": state.readiness_gate_resolved,
            "readiness_gate_partial": state.readiness_gate_partial,
            "readiness_gate_entities_mapped": state.readiness_gate_entities_mapped,
            "readiness_gate_entities_total": state.readiness_gate_entities_total,
        }
```

### Step 3: run test, confirm green

```bash
.venv/bin/python -m pytest tests/test_a227_solve_phase_summary_readiness_fields.py -v
```

### Step 4: full suite + make test-a

```bash
make test-a
make test-all
```

### Step 5: live-smoke confirmation

```bash
export CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server"
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 10
```

Check `artifacts/submission_results_single.json`'s `solve_phase_summary` carries the four new fields with values matching the last `agent_execution_trace.json` step snapshot's own `readiness_gate_*` fields.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a227_solve_phase_summary_readiness_fields.py -v
make test-a
make test-all
```

## Assumptions/defaults

- None beyond what's already established by `WorkflowState`'s own field defaults (`readiness_gate_resolved`/`readiness_gate_partial` default `False`, the two counts default `None`).
