# A231 — Wire Whole-Action-Space Coverage Into the Readiness Gate: Plan

## Card metadata

- Card: `backlog/A231.md`
- Depends on: A230 (Annatar now sees and decides on every readiness/probe cycle), A224 (the readiness gate itself), A135 (`fetch_untested_actions`, the existing signal this card wires in)

## Design (settled here; a few specifics are investigation-first, see Track A)

Confirmed by direct read before writing this plan:

- `agents/arc4/annatar_state_machine.py::readiness_status(entity_domains, *, step_index, max_cycles, budget_fraction_before_fallthrough=0.5)` (lines 269-311) — pure, entity-domains-only. `NOT_READY` iff any entity is `DISORDER`. No action-space awareness.
- `arc_runtime/bundle.py::_readiness_gate` (lines 217-233) — the real production closure. Calls `classify_all_entity_domains(perception, graph_port)`, then `readiness_status(entity_domains, ...)`, then (if `NOT_READY`) `plan_agent._select_readiness_probe(perception, entity_domains)`. This closure already has `graph_port` captured — adding a `graph_port.fetch_untested_actions()` call here is a one-line addition, no new capture needed.
- `agents/arc4/plan_generator.py::_select_readiness_probe` (lines 792+) — only ever returns `ACTION6@x,y`-shaped candidates via `_click_targets`.
- `agents/arc4/graph_queries.py::fetch_untested_actions` (line 154) returns `list[str]` of action IDs the graph has never seen attempted — already consumed once, in `plan_generator.py`'s own goal-directed candidate generation (line ~558), filtered there against an `api_action_set` derived from the observation.
- `Executor.execute` (`agents/arc4/executor.py`) is fully generic over `action_id` — no ACTION6-specific branching. Confirmed: routing a non-click action through the exact same probe execute/evaluate/Annatar cycle A230 already wired up requires **no new execution-path code**, only a differently-shaped `PlanCandidate` (no `x`/`y` in `payload`).

### The extension

1. `_readiness_gate` additionally calls `graph_port.fetch_untested_actions()`, filters out `ACTION6` (click coverage is already tracked at the entity level — don't double-count) and anything not in the observation's real available-action set (see Track A for how to extract this cleanly), producing `untested_non_click_actions: list[str]`.
2. `readiness_status()` gains a new parameter, `untested_non_click_actions: Sequence[str] = ()`. `NOT_READY` now also fires when this is non-empty. `PARTIAL_FALLTHROUGH`'s budget-safety-valve logic is otherwise unchanged.
3. `_select_readiness_probe` (or a new sibling function next to it, in `plan_generator.py` — implementer's call, see Track B) picks one untested non-click action as the probe candidate when that's what's blocking readiness. No coordinate needed; the resulting `PlanCandidate` should still carry a clear sentinel in its metadata (mirroring `_select_readiness_probe`'s existing `readiness_probe: True`/`goal_id="readiness_probe"` convention) so it's identifiable in traces the same way entity probes already are.
4. No changes needed to `workflow.py`'s probe-cycle loop or the A230 Annatar-routing logic — the readiness report just carries richer data now, and `readiness_status()` (which `run_annatar_cycle` already reads via the report's `status` field) naturally reflects both coverage questions once it accounts for both.

## Track A: confirm the specifics before implementing (investigation-first, don't assume)

1. **`fetch_untested_actions()`'s real return shape.** Run a live smoke on a puzzle with non-click actions available (check `master_timeline.json`/the observation's `available_actions` field for a game exposing ACTION1-5, not just ACTION6) and call `fetch_untested_actions()` directly against the live graph early in the episode. Confirm: does it include `"ACTION6"` as a generic, non-coordinate entry? Does it ever return duplicate/malformed entries? This determines the exact filter needed in step 1 of the design above.
2. **How to extract the observation's real available-action set inside `_readiness_gate`.** `plan_generator.py::_available_actions` (line 514) does this today but takes a `goal: ResolvedGoal` and `graph_records` the readiness-gate closure doesn't have (no goal exists yet by design at this point in the cycle). Read `_available_actions`'s actual body to determine whether the action-list-extraction part can be pulled out as a smaller, goal-independent helper (preferred, avoids duplicating logic) or whether `_readiness_gate` should just read `perception.observation.get("available_actions", [])` directly (simpler, matches the existing pattern already confirmed in `workflow.py`'s own stall-check code) — pick whichever avoids the most duplication once you've actually read both call sites.
3. **Precedence: entity probes vs. action probes.** Decide (and document the reasoning, not just the choice) whether `_select_readiness_probe`'s existing entity-DISORDER path should run first with untested-actions probed only once no DISORDER entities remain, or the reverse, or interleaved by whichever count is smaller. A reasonable default if nothing in the evidence points elsewhere: probe untested non-click actions FIRST (there are typically far fewer of them — single digits vs. potentially dozens of entities — so they're cheap to clear before the more expensive entity-mapping phase begins), but confirm this doesn't conflict with anything `readiness_status()`'s own docstring says about intent.

## Track B: implement

TDD throughout. New/extended tests:

- `readiness_status()`: new test — `entity_domains` all non-DISORDER but `untested_non_click_actions` non-empty → `NOT_READY`. Regression test — behavior with `untested_non_click_actions=()` (the default) is byte-for-byte unchanged from every existing `readiness_status()` test (run the full existing `tests/test_a224_readiness_gate.py` suite before and after, zero assertion edits expected).
- Probe selection: new test — given a puzzle with untested non-click actions and DISORDER entities both present, the probe path correctly selects per Track A's precedence decision. New test — a probe candidate for a non-click action has no `x`/`y` in its payload and carries the same kind of identifying sentinel metadata `_select_readiness_probe`'s entity probes already do.
- Integration: extend `tests/test_a224_workflow_readiness_integration.py`'s readiness-routing test class with a case where the readiness report's `untested_non_click_actions` keeps the gate `NOT_READY` even after every entity is mapped, and confirm the probe path executes the non-click action via the existing (A230-routed) probe cycle without any changes to `workflow.py` itself.

Run:
```bash
.venv/bin/python -m pytest tests/test_a224_readiness_gate.py tests/test_a224_readiness_probe_selection.py tests/test_a224_workflow_readiness_integration.py -v
make test-a
make test-all
```

## Track C: live-verify

Same environment setup as every prior card in this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path — see A225/A226/A228/A229/A230's own plan files for the exact commands). Run a live smoke on a puzzle with confirmed non-click actions available (check the observation's `available_actions` field first — pick a game where this isn't just `["ACTION6"]`). From the trace, confirm: at least one probe cycle executed a non-ACTION6 action before `exploration_complete` became `True`. This is the concrete, observable proof the fix works, not just a passing unit test.

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a224_readiness_gate.py tests/test_a224_readiness_probe_selection.py tests/test_a224_workflow_readiness_integration.py -v
make test-a
make test-all
```

## Assumptions/defaults

- `untested_non_click_actions` defaults to `()` (empty), so `readiness_status()` is fully backward-compatible for any caller that doesn't pass it — matches every other optional-signal-extension pattern already established this session (A230's `readiness_report`, A226's `EntityNeighborhoodClassification`).
- If Track A finds a puzzle with non-click actions is hard to reliably reproduce (ARC-AGI-3's live catalog rotates), it's acceptable to confirm the mechanism via a targeted unit/integration test plus one honest best-effort live attempt, rather than blocking indefinitely on finding the "right" puzzle — say so plainly in the Outcome if that's what happened, don't overclaim live coverage that wasn't actually achieved.
