# A225 — Fix `_execution_step` Always Returning 0: Plan

> Superseded 2026-08-31: this plan originally covered probe-selection diversity. That diagnosis was replaced after a real graph-guided investigation (querying `get_entity_history` live, not reading telemetry) found the actual bug is upstream and more fundamental. See `backlog/A225.md`'s Problem section for the full corrected diagnosis. This plan file is rewritten to match; the old file's name is kept so the card/plan ID pairing (`BacklogRules.md` rule 1) still holds.

## Card metadata

- Card: `backlog/A225.md`
- Depends on: A176 (`agents/arc4/graph_queries.py::record_transition`), A218 (the CHAOTIC consumer this bug defeats)

## Summary

`ArcGraphQueryPort._execution_step(execution)` (`agents/arc4/graph_queries.py:1018-1028`) looks for a real step number in `execution.metadata["step"|"step_num"|"step_index"|"execution_step"]`, falling back to `0` if none are present. Confirmed by direct code read that `Executor._success()` (`agents/arc4/executor.py:66-85`) never actually sets any of those keys — it nests the whole `game_context` dict (which *does* carry `state.step_index` under `"step"`, set by `arc_runtime/bundle.py`'s `_execute_via_transport`) under `metadata["game_context"]` instead, one level deeper than `_execution_step` looks.

Confirmed live, not just by code reading: queried `get_entity_history` directly against the real graph for game `ft09-0d8bbf25` via `MCPBrainClient` — every transition record found was stamped `step: 0`. Since `record_transition`'s graph node is `MERGE`d on `f"{task_id}_{action_id}_step{step}_{entity_ref}"`, this means every repeated attempt at the same entity+action silently overwrites the prior record instead of accumulating history, and `len(transitions) >= 2` (A218's CHAOTIC condition) can never be satisfied.

## Implementation approach

### Files

- Modify: `agents/arc4/executor.py` — `Executor._success` and `Executor._failure`.
- Test: `tests/test_a225_execution_step_metadata.py` — new file.

### Step 1: write the failing test

```python
"""A225: Executor must stamp the real step index onto ExecutionResult.metadata
so ArcGraphQueryPort._execution_step (agents/arc4/graph_queries.py) can find
it -- confirmed via live graph query that this has been silently defaulting
to 0 for every execution since A176, collapsing repeated-attempt history at
the same entity+action into a single overwritten graph node."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.executor import Executor
from agents.arc4.types import PlanCandidate, WorkflowState


class _FakeTransport:
    def execute_action(self, action_id, action_args, context):
        return {"state": "NOT_FINISHED", "grid": [[0]]}


class TestExecutionStepMetadata:
    def test_success_path_stamps_real_step_from_game_context(self):
        executor = Executor(transport=_FakeTransport())
        plan = PlanCandidate(action_id="ACTION6", goal_id="g1", payload={"x": 1, "y": 1})
        state = WorkflowState()
        game_context = {"game_id": "g", "step": 7, "session_id": "s"}

        result = executor.execute(state, plan, game_context)

        assert result.payload.metadata["step"] == 7

    def test_failure_path_stamps_real_step_from_game_context(self):
        executor = Executor(transport=None)  # forces the missing-transport failure path
        plan = PlanCandidate(action_id="ACTION6", goal_id="g1", payload={"x": 1, "y": 1})
        state = WorkflowState()
        game_context = {"game_id": "g", "step": 3, "session_id": "s"}

        result = executor.execute(state, plan, game_context)

        assert result.payload.metadata["step"] == 3

    def test_missing_step_in_game_context_stays_none_not_crash(self):
        """No `step` key at all (e.g. a caller that doesn't set one) must not
        raise -- ArcGraphQueryPort._execution_step already defaults safely to
        0 for a None/missing value, so Executor just needs to pass through
        whatever's there without assuming it exists."""
        executor = Executor(transport=_FakeTransport())
        plan = PlanCandidate(action_id="ACTION6", goal_id="g1", payload={"x": 1, "y": 1})
        state = WorkflowState()
        game_context = {"game_id": "g", "session_id": "s"}

        result = executor.execute(state, plan, game_context)

        assert result.payload.metadata["step"] is None
```

Run: `.venv/bin/python -m pytest tests/test_a225_execution_step_metadata.py -v`
Expected: FAIL — `KeyError: 'step'` (or `assert None == 7`, depending on dict access) on all three, since `metadata["step"]` doesn't exist yet.

### Step 2: implement the fix

In `agents/arc4/executor.py`, `_success`:

```python
    def _success(
        self,
        plan: PlanCandidate,
        transport_result: Any,
        context: Mapping[str, Any],
    ) -> PhaseResult[ExecutionResult]:
        observation, did_progress, actual_effect, metadata = self._normalize_result(transport_result)
        execution = ExecutionResult(
            action_id=plan.action_id,
            candidate=plan,
            observation=observation,
            did_progress=did_progress,
            predicted_effect=plan.expected_effect,
            actual_effect=actual_effect,
            metadata={
                **metadata,
                "game_context": self._compact_context(context),
                # A225: ArcGraphQueryPort._execution_step reads this key to
                # stamp the real cycle number onto record_transition/
                # record_rule_evidence/record_action_effect graph writes --
                # it was never set here, so every write silently defaulted
                # to step 0, MERGE-overwriting each entity's prior attempt
                # instead of accumulating history. See backlog/A225.md.
                "step": context.get("step"),
            },
        )
        return PhaseResult(phase=WorkflowPhase.EXECUTE, payload=execution)
```

And `_failure`, same addition to its `metadata` dict:

```python
        metadata: dict[str, Any] = {
            "game_context": self._compact_context(context),
            "failure_reason": reason,
            "step": context.get("step"),
        }
```

### Step 3: run the test, confirm green

```bash
.venv/bin/python -m pytest tests/test_a225_execution_step_metadata.py -v
```

### Step 4: regression-check `ArcGraphQueryPort._execution_step` and its callers

```bash
.venv/bin/python -m pytest tests/ -k "graph_queries or execution_step or a176 or a213 or a218" -v
```
No existing test should assert `_execution_step` returns `0` for a populated `metadata["step"]` — if one does, read it first; it may have been written around the bug rather than testing the intended behavior, same shape of thing this card's own diagnosis had to correct once already.

### Step 5: full suite + make test-a

```bash
make test-a
make test-all
```

### Step 6: live-graph re-verification

```bash
export CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server"
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 10
```

Then query the graph directly (do not infer from telemetry alone — this card exists because that inference was wrong once already):

```python
import asyncio, sys
sys.path.insert(0, ".")
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient

async def main():
    client = MCPBrainClient()
    await client.start()
    await client.initialize_session()
    # substitute the real game_id from this run's master_timeline.json
    for eref in range(0, 60):
        resp = await client.call_tool("get_entity_history", {"task_id": "<game_id>", "entity_ref": eref})
        if resp.get("transitions"):
            print(eref, resp)
    await client.close()

asyncio.run(main())
```

Confirm at least one entity clicked more than once shows 2+ transitions with distinct, real `step` values (not all `0`).

### Step 7: check Rule-confidence accumulation for the same bug class

Read `hippocampy/campy/brain/thalamus/tools/arc_queries.py::record_rule` (server-side) and `agents/arc4/graph_queries.py::record_rule_evidence` (ARC-side write). Confirm whether Rule nodes are keyed by `rule_id` (content-fingerprint-based, step-independent) rather than by step — if so, this specific bug doesn't affect CONVERGED/COMPLEX and that should be stated plainly in the Outcome, not left implicit. If Rules turn out to also depend on a step value that's been defaulting to 0, that's a second real finding, not assumed away.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a225_execution_step_metadata.py -v
make test-a
make test-all
```

## Assumptions/defaults

- `context.get("step")` may be `None` for callers that don't set it (test fixtures, older call sites) — `Executor` must not assume it's always present; `ArcGraphQueryPort._execution_step` already handles `None`/missing safely (falls through to `0`), so no new defensive code is needed on the read side.
