# Plan: A213 — Fix: A No-Op Action Leaves No Trace in Rule/Transition-Level Graph Knowledge

## Card metadata

- ID: A213
- Priority: P2
- Layer: ARC runtime
- Dependencies: None

## Summary

`record_transition` and `record_rule_evidence` in `agents/arc4/graph_queries.py` both return early, writing nothing, when an action produces zero visible grid change. Unlike the reward-counter path (`record_action_effect`/`record_reward_prediction_error`, already unconditional), these two entity/rule-level write paths leave a no-op action structurally indistinguishable from an untried one to `fetch_rules_for_action`/`fetch_causal_path`/`fetch_entity_neighborhood`. Close that gap, but only after confirming the server side can actually represent it.

## Technical approach

### 1. Read the current state first

- `agents/arc4/graph_queries.py`, `record_transition` (~lines 362–386) and `record_rule_evidence` (~lines 388–437) in full, including the `_TOOL_NAME_MAP`/tool-key constants near the top of the file (~lines 15–35) to find the exact MCP tool names these two call server-side.
- `docs/handoff/B278-rules-as-nodes.md` and `docs/handoff/B278-persist-transitions-as-state-nodes.md` — the existing schema handoff docs for these two write paths. Check whether either anticipates a null/no-op state.
- `agents/arc4/graph_queries.py`'s `record_evaluation` (~lines 565–634) for the working unconditional pattern (`record_action_effect`/`record_reward_prediction_error`) to use as a reference for "what does an unconditional write already look like in this file."

### 2. Determine what the server can actually accept

Before writing any code, either:
- Test against a live/dev hippocampy instance (if `CAMPY_MCP_CMD` is available in this environment) by sending `record_transition`/`record_rule_evidence`-shaped payloads with empty `color_transitions`/`candidate_signatures` lists and observing whether the tool accepts or rejects them, OR
- If no live instance is available, read the referenced handoff docs and any local schema reference for these two tools closely enough to make a confident call, and say explicitly in the Outcome section which of these two methods (live test vs. doc-only read) was used.

### 3a. If the server already tolerates a no-op payload

Modify `record_transition` and/or `record_rule_evidence` (pick whichever is the more natural single choke point — likely `record_rule_evidence`, since "was this action's causal effect ever tested" is closer to what `fetch_rules_for_action`/`fetch_causal_path` actually query) to send a minimal no-op record instead of returning early:

```python
changed_cells = grid_diff.get("changed_cells") if isinstance(grid_diff, Mapping) else None
if not changed_cells:
    payload = {
        "task_id": self.task_id,
        "step": self._execution_step(execution),
        "action_id": execution.action_id,
        "candidate_signatures": [],  # explicit: tried, zero effect -- confirm exact shape the server expects during implementation
    }
    return self._normalize_write_result(self._call_tool("record_rule_evidence", payload), tool_key="record_rule_evidence")
```

Do not guess at the exact no-op payload shape shown above — confirm it against step 2's findings before committing to it.

### 3b. If the server does NOT already tolerate this

Write a new `docs/handoff/B278-no-op-action-signal.md` following the format of the existing `docs/handoff/B278-*.md` docs (see `B278-investigation-thread-schema.md` or `B278-mechanic-fusion.md` for the established structure: summary, current behavior, what's needed, why it matters). Leave the ARC-side behavior unchanged (still `{"status": "no_changes", "recorded": False}`), but note in the code with a comment and in the card's Outcome section that this is blocked on an upstream schema addition, same discipline as A211's "exhausted" interim-choice note and A201's original handoff pattern.

### 4. Tests

`tests/test_a213_no_op_rule_transition_signal.py`:
1. If a fix landed (3a): a no-op action (`changed_cells=[]` or `None`) now triggers a call to the graph tool with the no-op payload — assert the mock/stub client receives the expected call.
2. An action WITH non-empty `changed_cells` behaves exactly as before this card (regression guard) — assert the existing rule-signature extraction path is untouched.
3. If blocked on upstream (3b): a regression test confirming current no-op behavior (`{"status": "no_changes", "recorded": False}`, no call made) is unchanged, plus a `# A213: blocked on docs/handoff/B278-no-op-action-signal.md` comment at the return site so a future card knows to revisit this once hippocampy ships the schema addition.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/graph_queries.py` | `record_transition`/`record_rule_evidence` gain a no-op branch, OR gain a comment marking the blocked-on-upstream state |
| `tests/test_a213_no_op_rule_transition_signal.py` (new) | Coverage per above |
| `docs/handoff/B278-no-op-action-signal.md` (new, only if blocked on upstream) | Schema ask for hippocampy |

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a213_no_op_rule_transition_signal.py -v
make test-a
make test-all
```

## Assumptions/defaults

- If genuinely uncertain whether the server tolerates a no-op payload and no live MCP instance is reachable to test directly, default to the conservative path (3b, write a handoff doc, leave behavior unchanged) rather than guessing at a payload shape that might silently fail or corrupt server-side state — same conservatism this repo has applied at every prior B278-boundary uncertainty (A201, A211).
- This card does not touch `evaluator.py`'s `meaningful_progress` computation or `graph_grounded_decision_rate`'s definition — that is A214's separate, audit-first scope. Do not conflate the two even though they were found in the same investigation.
