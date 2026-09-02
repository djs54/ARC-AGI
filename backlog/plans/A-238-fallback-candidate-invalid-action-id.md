# A238 — Fallback Candidate Invalid Action ID: Plan

## Card metadata

- Card: `backlog/A238.md`
- Depends on: A184 (`_fallback_candidate`'s introduction), A191/A208 (the exclusion mechanisms that empty the candidate list), A232 (`plan_vetter.py`'s veto/alternative logic)

## Design (investigate the two sub-questions during implementation, don't presuppose)

Confirmed by direct read before writing this plan:

- `agents/arc4/plan_generator.py::_fallback_candidate` (~line 469): `action_id = self._slugify(f"probe-{goal.selected.goal_id}")`, called from `_build_candidates` (~line 400-401) when `candidates` is empty after the main per-action loop.
- `agents/arc4/executor.py::Executor._invoke_transport` (~line 50-64): passes `plan.action_id` straight through to whichever of `execute_action`/`run_action`/`step`/callable the transport exposes — zero validation.
- `arc_runtime/game_session.py:56`: `self._client.post(f"/api/cmd/{action_id}", json=request_payload)` — the raw string goes directly into the URL path.
- `agents/arc4/plan_vetter.py::vet` (~line 96-109): the `repeated_falsification_threshold` veto requires `alternative is not None` (from `_choose_alternative`, ~line 181-195) — once every real action has `attempt_count > 0`, no alternative exists, so a repeatedly-failing fallback candidate can never be vetoed away, only re-approved.

### Track A: fix the source — `_fallback_candidate` must never produce a non-transport-valid `action_id`

Two sub-options to investigate against real data before choosing:

**Option A1 — fall back to a real action.** Instead of inventing `probe-{goal_id}`, select a real action_id from `available_actions` (the same list `_build_candidates`'s main loop already iterates) even though every one has already been excluded/falsified this cycle — e.g. the least-recently-attempted real action, or a fixed default like `ACTION1`. This guarantees the transport always receives something valid, at the cost of "trying again" an action already known not to work. Check: does this risk an infinite loop (repeatedly re-trying the same exhausted action forever) if nothing else changes the state? If so, this option needs to be paired with ensuring the whole-episode/action-space-exhaustion termination (A194) still fires promptly — don't let this fix accidentally defeat that mechanism by giving it something that looks like "still trying" forever.

**Option A2 — signal "nothing real left" upward instead of manufacturing a candidate.** When `_build_candidates` would otherwise call `_fallback_candidate`, instead mark the `PlanningResult` (or a new field on it) as exhausted and let `workflow.py` route directly to the existing `_action_space_exhausted`/A194 termination path without an extra wasted plan→vet→execute cycle. Check: does `evaluate.py`'s exhaustion check already run every cycle regardless (so this cycle would terminate on its own next step anyway, meaning the extra cost is only ever exactly one cycle, not several) — if so, this may be simpler and doesn't need the "pick a real action anyway" fallback logic of A1 at all, since letting exhaustion fire one cycle sooner is strictly better than sending any doomed action, real or synthetic. Read `evaluator.py`'s exhaustion check to determine if it's checked pre- or post-execute, and how many wasted cycles A2 would actually save versus A1.

**Default lean:** Option A2 is likely cleaner (avoids re-trying a known-dead action at all) but investigate both against the actual code shape and real live data — this session's own established discipline (A233 Track A/B, A235's node-vs-edge-writes question) is to check before committing, not assume the "obviously better" option is actually better in this codebase's specific control flow.

### Track B: transport-boundary validation backstop

`arc_runtime/game_session.py::execute_action` (or a thin wrapper in `agents/arc4/executor.py`, whichever keeps the validation closest to the actual boundary) gains a cheap regex/set check: `action_id` must match `ACTION[1-7]` (with `ACTION6` optionally carrying `x`/`y` in the payload, not the id itself — confirmed the id itself is always bare `ACTION6`, coordinates travel via `payload`). On a mismatch, fail locally (same `ExecutionResult`/`CRASH`-shaped outcome the real 404 currently produces) without making the HTTP call — same failure signal downstream, zero wasted network round-trip or API rate-limit consumption.

Investigate whether Track A alone (never producing a bad id) makes Track B redundant, or whether Track B is worth keeping anyway as a defensive backstop against some *future* code path making the same mistake (the same "build the capability, never force anything downstream to depend on it" pattern this whole session has repeatedly found — a validation gate here costs little and protects against a class of bug, not just this one instance). Lean toward keeping Track B regardless of Track A's outcome, since it's cheap and the downside of skipping it is "the next new fallback/synthetic-id code path repeats this exact bug with nobody noticing until another live-smoke audit."

## Implementation approach

### Files

- Modify: `agents/arc4/plan_generator.py` — `_fallback_candidate`/`_build_candidates`, per whichever of Track A's options is chosen.
- Modify (Track B): `arc_runtime/game_session.py` and/or `agents/arc4/executor.py` — add the action_id validation.
- Test: new `tests/test_a238_fallback_candidate_valid_action_id.py`.

### TDD

- New test: a scenario where every real action is `repeated_falsified` (matching A191's exclusion condition) — confirm the resulting candidate's `action_id` is either a real `ACTION[1-7]` value (Option A1) or that the plan result signals exhaustion directly without producing a candidate meant for execution (Option A2), whichever is chosen.
- New test (Track B, if kept): a plan candidate with a deliberately invalid `action_id` (e.g. `"not-a-real-action"`) reaching the transport boundary — confirm it fails locally with the same shape of `ExecutionResult`/failure the real API 404 currently produces, and confirm (via a spy/mock transport) that no HTTP call was actually attempted.
- Regression: `_action_space_exhausted`/`strategy_exhausted` termination still fires within a comparable number of cycles once the action space is genuinely exhausted — this fix must not delay or mask real exhaustion, only remove the self-inflicted 404s along the way.
- Regression: every existing `plan_generator`/`plan_vetter`/`executor` test continues to pass; if `_fallback_candidate`'s signature or `_build_candidates`'s empty-candidates behavior changes shape, update the specific tests that assert on today's `probe-{goal_id}` string with a stated reason, not silently.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a238_fallback_candidate_valid_action_id.py -v
.venv/bin/python -m pytest tests/test_arc4_plan_generator.py tests/test_arc4_plan_vetter.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline, capture full raw output via `tee` and read the complete file, never a truncated tail). Run a live smoke on a puzzle likely to reach the exhaustion boundary within budget — a small, fully-mappable puzzle is more likely to genuinely exhaust its action space than a large one that spends its whole budget still probing (this session's own `readiness_gate_entities_total` field in the trace tells you which shape a given puzzle is before the run finishes). Confirm, via `grep -oE "api/cmd/[a-zA-Z0-9_.,@-]+" <log> | sort | uniq -c` against the full captured log, that every entry matches a real `ACTION[1-7]`/`RESET` command — zero `probe-*` or any other non-standard string reaching the API. If the randomly-assigned puzzle for a given run doesn't happen to reach exhaustion within the 30-step smoke budget, say so honestly and either re-run or fall back to the TDD coverage as the primary evidence, per this session's own standing discipline against overclaiming live verification that didn't happen.

## Assumptions/defaults

- Track B (transport-boundary validation) is built regardless of which Track A option is chosen, unless investigation surfaces a concrete reason not to — it's cheap defense-in-depth against the same bug shape recurring elsewhere.
- If Track A's A1 vs. A2 choice is genuinely close, default to A2 (signal exhaustion directly) since it avoids re-trying a known-dead action and this card's own evidence suggests the fallback's repeated failure was likely padding out the exhaustion signal rather than adding real information.
