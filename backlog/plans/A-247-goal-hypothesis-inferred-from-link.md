# A247 — Goal-Hypothesis `INFERRED_FROM` Linkage: Plan

## Card metadata

- Card: `backlog/A247.md`
- Depends on: A245 (the investigation that found this gap — see its Outcome for the full citation
  trail), `hippocampy` B363 (fixed `VictoryCondition` node creation; this card is a different edge)

## Design

Two-repo fix. Do the `hippocampy` (server) side first and prove it with a real-KuzuDB test, since
the `ARC_AGI` (client) side is only meaningful once the server can actually do something with a
`condition_id` if sent one.

### Step 1 — `hippocampy` server side

1. In `campy/brain/thalamus/tools/arc_queries.py`, add a new helper mirroring
   `_link_entity_hypothesis` (lines 582-600), e.g. `_link_goal_hypothesis(db, task_id, condition_id,
   hypothesis_id, step)`:
   ```cypher
   MERGE (vc:VictoryCondition {condition_id: $cid})
   ON CREATE SET vc.task_id = $tid, vc.created_at = coalesce(vc.created_at, current_timestamp())
   WITH vc
   MATCH (h:Hypothesis {id: $hid})
   MERGE (vc)-[i:INFERRED_FROM]->(h)
   SET i.weight = $weight, i.step = $step
   ```
   (confirm exact `MERGE`/`ON CREATE` Cypher syntax against this codebase's existing patterns in
   `campy/brain/hippocampus/graph/queries/arc.py`'s `arc.merge_victory_condition_confidence`, don't
   assume the sketch above is final — follow the NamedQuery convention that file already
   establishes per its own docstring, since this is new code, not a migration).
2. `arc_confirm_hypothesis` (`arc_queries.py:603-630`): accept optional `condition_id` from
   `params`; when present, call the new helper. Also add `h.status = 'active'` to its existing
   `SET` clause (currently only sets `evidence_count`/`confidence`) — check this doesn't collide
   with `arc_contradict_hypothesis`'s own `status = 'demoted'` write in a way that could flip a
   hypothesis back and forth nonsensically across repeated confirm/contradict calls on the same
   hypothesis_id; if that's a real risk, decide (and document) whether `status` should be
   monotonic (once demoted, stays demoted) or always reflects the most recent call.
3. `arc_contradict_hypothesis` (`arc_queries.py:633-666`): same optional `condition_id` param,
   same new helper call.
4. Real-KuzuDB regression test in `tests/test_arc_queries.py` (production schema via
   `init_schema()`, not `MockDB` — same discipline as
   `test_b363_update_goal_confidence_creates_victory_condition`): confirm a hypothesis with a
   `condition_id`, then call `arc_get_goal_evidence` for that task and assert `supports == 1` (or
   `contradicts == 1` for the contradict path). Also a test confirming the existing
   `entity_ref`-only call shape (no `condition_id`) is completely unaffected — `supports`/
   `contradicts` stay `0` exactly as today.

### Step 2 — `ARC_AGI` client side

1. Find the real call site(s) with the active goal's `condition_id` in scope at the moment
   `confirm_hypothesis`/`contradict_hypothesis` is called — `agents/arc4/graph_queries.py::
   record_plan`/`record_vet` are the two candidates named in the card, but confirm by reading
   `evaluator.py`/`plan_vetter.py`'s own call sites into `record_plan`/`record_vet` (do they
   receive `WorkflowState.active_goal` or equivalent at that point? check, don't assume).
2. Thread `condition_id=state.active_goal.selected.goal_id` (or equivalent) into the payload sent
   to `confirm_hypothesis`/`contradict_hypothesis`, mirroring how `entity_ref` is already threaded
   in the same functions today. No-op (omit the param) when there's no active goal, matching
   `_link_entity_hypothesis`'s own existing "optional, not required" behavior for `entity_ref`.
3. `ARC_AGI`-side test: mock `graph_port.confirm_hypothesis`/`contradict_hypothesis`, assert the
   call payload includes `condition_id` when `state.active_goal` is set, and omits/leaves it `None`
   when it isn't.

### Validation commands

```bash
# hippocampy repo
.venv/bin/python -m pytest tests/test_arc_queries.py -v -k "goal_evidence or inferred_from"
make test   # or this repo's equivalent full-suite target -- confirm exact name

# ARC_AGI repo
.venv/bin/python -m pytest tests/test_a247_goal_hypothesis_linkage.py -v
make test-a
make test-all
```

### Live-verify

Run a live smoke (same environment discipline as prior cards in this sequence — `CAMPY_MCP_CMD`,
`campy start` warm-up, generous timeout, full `tee`'d output). Confirm at least one goal's
`fetch_goal_evidence` response shows non-zero `supports` or `contradicts` for the first time ever
observed in this system. This is the concrete signal that unblocks A245's original Step 2/3 (the
goal-domain Cynefin classifier) as a follow-up.

## Assumptions/defaults

- The exact Cypher `MERGE` syntax in Step 1.1 is a sketch, not final — verify against this
  codebase's real conventions before writing it, per B314's NamedQuery discipline referenced in
  `campy/brain/hippocampus/graph/queries/arc.py`'s own module docstring.
- If the `status='active'` vs `status='demoted'` interaction (Step 1.2) turns out to need real
  design thought (not just "set it and see"), that's this card's own scope to resolve, not a reason
  to punt further — it's a small, local decision, unlike the goal-domain classifier itself.
