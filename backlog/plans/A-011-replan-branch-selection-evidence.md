# A-011 - REPLAN Branch Selection Is Signature-Only and Ignores Action-Facts Evidence

## Card metadata

- Card: A011
- Priority: P0
- Depends on: A010

## Summary

Make REPLAN branch selection use the same action-facts and coverage evidence the graduation gate uses, and make plan registration idempotent so repeated REPLAN cycles on unchanged evidence stop generating new UUIDs.

## Implementation approach

1. In `_replan_target` (`agents/arc3/runner.py:2161-2200`) compute three new predicates before the existing heuristics:
   - `all_actions_low_value`: true if every action in `hypothesis_context.action_facts` with `fact_type == "deterministic_effect"` has `value_status == "low_value"` AND `action_coverage.tested_count >= action_coverage.available_total`
   - `geometry_high_conf`: player and goal roles both present with confidences above the directional thresholds from `PlanChunker`
   - `coverage_saturated`: `action_coverage.initial_exploration_complete and untested_count == 0`
2. Decision tree (first match wins, record `route_reason` in trace metadata):
   - `all_actions_low_value and geometry_high_conf` → `MODEL` with reason `low_value_but_known_geometry`
   - signature repeated → `MODEL` with reason `signature_escalation` (existing behavior)
   - exploration incomplete → `MODEL` with reason `exploration_incomplete`
   - `archetype_confidence < 0.3` → `HYPOTHESIZE` with reason `low_archetype_conf`
   - `coverage_saturated` → `ROUTE` with reason `rebuild_route_from_saturation`
   - default → `ROUTE` with reason `default`
3. Emit `route_reason` through the existing `replan_exit` trace metadata.
4. In `agents/arc3/orchestrator.py`, wrap the `register_plan` call path with a last-registered-plan fingerprint: `(description, tuple(estimated_actions), archetype, victory_condition_type)`. If the fingerprint matches the last registered plan for this puzzle, reuse the prior `plan_id` instead of calling `register_plan` again.
5. Add tests in `tests/test_replan_branching.py`:
   - all-low-value evidence → MODEL
   - signature-repeated escalation → MODEL
   - exploration incomplete → MODEL
   - low archetype confidence → HYPOTHESIZE
   - default → ROUTE
6. Add tests in `tests/test_plan_registration_idempotent.py` (new) covering plan-id reuse.

## Concrete file additions/edits

- edit `agents/arc3/runner.py`
- edit `agents/arc3/orchestrator.py`
- add `tests/test_replan_branching.py`
- add `tests/test_plan_registration_idempotent.py`
- add trace-field documentation note in `ARCHITECTURE.md` where the REPLAN section already describes replan as first-class

## API/interface changes

- `replan_exit` trace metadata gains a `route_reason` string field
- no changes to the MCP seam or SideQuests contract

## Tests to add or run

- `pytest -q tests/test_replan_branching.py`
- `pytest -q tests/test_plan_registration_idempotent.py`
- `pytest -q tests/test_orchestrator_replan_loop.py` (regression)

## Validation commands

- `pytest -q -k replan`
- re-run one-puzzle smoke and confirm `register_plan` count in `submission_results_arcServer.json` no longer exceeds the number of *distinct* chunk plans

## Assumptions/defaults

- action-facts `fact_type` and `value_status` vocabulary already exists upstream; no schema changes required
- plan fingerprint is exact-match — near-duplicate plans are intentionally *not* collapsed (would hide real strategy changes)
- ROUTE remains the default resume phase for replans that pass all evidence gates
