# A225 — Readiness-Probe Diversity: Plan

## Card metadata

- Card: `backlog/A225.md`
- Depends on: A224 (`agents/arc4/plan_generator.py::_select_readiness_probe`, `agents/arc4/annatar_state_machine.py::readiness_status`, `arc_runtime/bundle.py`'s `_readiness_gate` closure)

## Summary

A224's live-smoke run (see `backlog/A224.md` Outcome) found `_select_readiness_probe` re-derives the single highest-salience `DISORDER` entity every cycle with no memory of prior probes, so on a 54-entity puzzle all 5 available probe cycles hit the same coordinate. This plan investigates and fixes probe-selection diversity so a multi-cycle readiness phase actually spreads coverage across the entity population.

## Implementation approach

**Step 0 (required before writing any code): confirm the mechanism.** Re-read `agents/arc4/plan_generator.py::_select_readiness_probe` and `_click_targets` in full. Confirm: (a) selection is purely salience-based among currently-`DISORDER` entities, (b) nothing already tracks "probed this episode" anywhere in `WorkflowState` or the graph. If either assumption is wrong, stop and re-scope — do not proceed on a stale premise.

**Step 1: choose Option A vs B vs C from the card**, and write down why in this plan (edit this file) before implementing. Default recommendation if no stronger signal emerges during Step 0: **Option A** (round-robin/least-recently-probed) — it directly targets the observed failure (breadth of coverage) without changing the readiness bar itself, and reuses `WorkflowState`'s existing pattern of small per-episode tracking fields (e.g. `action_attempt_counts`) rather than a new graph round-trip.

**If Option A:**

### Files

- Modify: `agents/arc4/types.py` — add `readiness_probed_entity_refs: set[Any] = field(default_factory=set)` to `WorkflowState` (exclude from `to_dict`/`from_dict`, same rationale as `previous_grid`/`previous_entities`: ephemeral, runtime-only, not persisted state — episode-scoped bookkeeping, not something a resumed episode needs to reconstruct).
- Modify: `agents/arc4/plan_generator.py` — `_select_readiness_probe(self, perception, entity_domains)` gains an optional `probed_entity_refs: Collection[Any] = ()` parameter. Prefer an unprobed `DISORDER` entity; if all `DISORDER` entities have already been probed at least once, fall back to today's pure-salience ordering among them (don't return `None` just because everything's been touched once — the entity may need a second probe before its classification can move, matching `CHAOTIC`'s own 2+-transition threshold).
- Modify: `agents/arc4/workflow.py` — pass `state.readiness_probed_entity_refs` into the `_select_readiness_probe` call (via the `readiness_gate` payload, same as `entity_domains` today — the gate closure in `bundle.py` needs access to it, so thread it through the `readiness_gate(state, perception)` call signature already established in A224 rather than reaching into `state` from inside `plan_generator.py`). After a probe candidate is selected and its cycle completes (win or lose), add its `entity_ref` to `state.readiness_probed_entity_refs`.
- Test: `tests/test_a225_readiness_probe_diversity.py` — new file.

### Tests to add

1. Two `DISORDER` entities, one already in `probed_entity_refs` — the unprobed one is selected even if the probed one has higher raw salience.
2. All `DISORDER` entities already probed — falls back to salience ordering (doesn't return `None`).
3. Workflow-level integration: extend `tests/test_a224_workflow_readiness_integration.py`'s `TestWorkflowReadinessGateRouting` pattern (or add a new test class in the new A225 test file, whichever reads more naturally once Step 0/1 are done) — run 2 consecutive `NOT_READY` cycles with 2 distinct `DISORDER` entities of different salience, confirm both get probed (different coordinates) rather than the same one twice.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a225_readiness_probe_diversity.py -v
.venv/bin/python -m pytest tests/test_a224_readiness_probe_selection.py tests/test_a224_workflow_readiness_integration.py -v
make test-a
make test-all
```

**If Option B or C instead:** write a new "Implementation approach" section here, in this same plan file, before starting — do not implement against Option A's file list if Step 1 concluded differently.

## Assumptions/defaults

- `state.readiness_probed_entity_refs` resets naturally at episode start (new `WorkflowState()` each episode) — no explicit reset logic needed.
- Diversity tracking is local `WorkflowState`, not a graph write — this is deliberately Shift-A-shaped local bookkeeping about *this episode's* probe history, not a durable graph fact; state it as a stated tradeoff in the PR's Graph-Engineering Review section rather than leaving it implicit.

## Live-smoke re-verification

Same command as A224's own verification:

```bash
export CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server"
PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 10
```

Read `artifacts/agent_execution_trace.json`'s `step` snapshots' `action_x`/`action_y` fields for the probe-path steps — confirm they're no longer identical across consecutive probe cycles on a puzzle with multiple `DISORDER` entities.
