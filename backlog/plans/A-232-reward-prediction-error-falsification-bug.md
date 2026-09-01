# A232 — Reward-Prediction-Error Falsification Bug: Plan

## Card metadata

- Card: `backlog/A232.md`
- Depends on: A177/B278 (`fetch_rules_for_action`), A214 (prior related audit)

## Design (settled here, not left open)

Confirmed by direct read before writing this plan:

- `agents/arc4/graph_queries.py::record_evaluation` (~line 767-838) sends **two** separate writes per evaluated action: `record_action_effect` (writes `ActionEffect`/bumps `ActionFact.observation_count` only — confirmed server-side, `hippocampy/campy/brain/thalamus/tools/arc_queries.py::arc_record_action_effect` never touches `confidence`/`falsified_count`) and `record_reward_prediction_error` (the buggy one — writes `ActionFact.confidence`/`falsified_count`).
- `arc_record_reward_prediction_error` (`hippocampy`, line 782+): `error = actual_reward - predicted_reward`; `error < -0.3` → `falsified_count += 1`, confidence decays; `error > 0.3` → confidence increases.
- The ARC-side call always sends `predicted_reward=1.0` (hardcoded) and `actual_reward = 1.0 if meaningful_progress else 0.0`. **Since `predicted_reward` is always `1.0`, `error` can only ever be `0.0` (no-op, `meaningful_progress=True`) or `-1.0` (falsification, `meaningful_progress=False`) — the positive branch (`error > 0.3`, which would increase confidence) can mathematically never fire.** This call has never once increased an `ActionFact`'s confidence in production; it only ever no-ops or falsifies. Confirmed live: `falsified_count == attempts` for every action checked (`ACTION2`/`3`/`4`/`7`: 1/1; `ACTION6`: 82/82), at the same moment `fetch_rules_for_action` shows real, unfalsified rules for the same actions (confidence 0.5–1.0).
- `agents/arc4/plan_generator.py::_build_candidates` (~line 189-218) already fetches the CORRECT signal, `fetch_rules_for_action` (line 210-218), into a local `rules`/`live_rule_confidences` — but only folds it into `graph_positive_score` (the numeric ranking score), never into the `graph_evidence` dict itself (only ever set from the broken `fetch_per_action_evidence` at line 195). `graph_evidence` is what gets stored in candidate metadata (line 376) and is the ONLY thing `telemetry.py::_has_positive_graph_evidence`/`_has_graph_evidence_at_all` (the `graph_grounded`/`graph_informed` KPIs) ever see.

### The fix (ARC-side, this repo)

1. **Remove the `record_reward_prediction_error` call from `record_evaluation`** (`agents/arc4/graph_queries.py`, ~line 816-837). Justification: it mathematically can never help (confirmed above — the positive branch is unreachable with `predicted_reward` hardcoded at `1.0`), and its only real effect is corrupting `ActionFact.falsified_count`/`confidence` with a whole-puzzle-progress proxy that has nothing to do with whether the action's predicted effect was actually confirmed. Simpler and safer than trying to "fix" what `predicted_reward`/`actual_reward` mean — there is no principled per-step numeric reward signal in ARC-AGI-3 to compute a meaningful prediction error from in the first place; inventing one is out of scope (see "Explicitly NOT this card's job").
2. **Surface `fetch_rules_for_action`'s real evidence into `graph_evidence` itself**, not just `graph_positive_score`, in `_build_candidates`:
   ```python
   fetch_rules = getattr(graph_port, "fetch_rules_for_action", None)
   if fetch_rules is not None:
       try:
           rules = fetch_rules(action_id) or []
           live_rule_confidences = [r.get("confidence", 0.0) for r in rules if not r.get("falsified")]
           if live_rule_confidences:
               graph_positive_score += max(live_rule_confidences) * self._limits.rule_confidence_weight
               # A232: surface real Rule-graph evidence into graph_evidence
               # itself, not just the score -- this is what graph_grounded/
               # graph_informed (telemetry.py) actually read from candidate
               # metadata. fetch_per_action_evidence's own confidence/
               # supports/contradictions (above) was corrupted by the
               # record_reward_prediction_error bug this card also fixes;
               # blend rather than overwrite so a real signal is never lost
               # even where fetch_per_action_evidence happens to agree.
               graph_evidence = dict(graph_evidence)
               graph_evidence["confidence"] = max(graph_evidence.get("confidence", 0.0), max(live_rule_confidences))
               graph_evidence["supports"] = graph_evidence.get("supports", 0) + len(live_rule_confidences)
       except Exception:
           rules = []
   ```
   This makes `_has_positive_graph_evidence`'s existing check (`confidence > 0 or supports > contradictions`) correctly see real evidence without touching that shared, generic function at all — `graph_evidence` is mutated at the source, once, before it's ever stored in metadata (line 376) or used for `_predicted_outcome` (line 359).

### The handoff (hippocampy-side, out of this session's direct edit scope)

3. `plan_vetter.py::_check_graph_gate` → `check_action_gate` → server-side `arc_check_action_gate` (`falsification_count >= 3 and untested_available` → veto) reads the SAME `ActionFact.falsified_count` this card's fix (item 1) stops corrupting *going forward* — but doesn't retroactively fix already-accumulated bad state for in-flight tasks, and the gate itself has no fallback to check real Rule-graph evidence before vetoing. Write `docs/handoff/B278-action-gate-falsification-source.md` in this repo (matching A221 Finding 2's `docs/handoff/B372-*.md` precedent exactly — a plain markdown note in THIS repo describing the issue for `hippocampy` maintainers, not a code change in that repo) covering: the exact mechanism, the live evidence gathered in A232's own investigation, and a suggested direction (e.g., `arc_check_action_gate` could additionally check for live unfalsified rules before trusting a stale falsification count, or `arc_record_reward_prediction_error` could require a real, non-hardcoded reward signal rather than accepting one at all).

## Implementation approach

### Files

- Modify: `agents/arc4/graph_queries.py` — remove the `record_reward_prediction_error` write from `record_evaluation`.
- Modify: `agents/arc4/plan_generator.py` — `_build_candidates`'s `fetch_rules_for_action` block, per the design above.
- Create: `docs/handoff/B278-action-gate-falsification-source.md` (new directory `docs/handoff/` may already exist from A221 — check first).
- Test: extend/add tests in `tests/test_a176_transition_persistence.py` or a new `tests/test_a232_reward_prediction_error_removal.py` (implementer's call on which file — mirror whichever existing test file already covers `record_evaluation`'s write calls, to avoid duplicating fixture setup) and `tests/test_a220_plan_generator_domain_visibility.py`/a new file for the `graph_evidence` blending.

### Step 0: confirm nothing else depends on `record_reward_prediction_error`'s current behavior

Grep for any test or consumer asserting on `record_reward_prediction_error` being called, or asserting on `ActionFact.confidence`/`falsified_count` values that would change once this write stops happening. Read them before removing the call — if any test specifically validates the current (buggy) behavior as intentional, that's a signal to re-examine, not to blindly "fix" the test.

### Step 1: TDD — remove the call

- Write/adapt a test asserting `record_evaluation` no longer calls `record_reward_prediction_error` (mock/spy on `_call_tool`, assert `"record_reward_prediction_error"` never appears in the call list for any `record_evaluation` invocation).
- Remove the call site in `agents/arc4/graph_queries.py`.
- Confirm `record_action_effect`'s own call is unaffected (still sent, same payload).

### Step 2: TDD — blend real rule evidence into `graph_evidence`

- New test: given a `graph_port` mock where `fetch_per_action_evidence` returns a corrupted-looking result (`confidence: 0.0`, `contradictions: 1`, `supports: 0`) and `fetch_rules_for_action` returns a real unfalsified rule with `confidence: 0.7`, the resulting candidate's `metadata["graph_evidence"]["confidence"]` is `0.7` (or higher, whichever the blend logic produces) and `_has_positive_graph_evidence` on that metadata returns `True`.
- Regression test: when `fetch_rules_for_action` returns nothing live, `graph_evidence` is unchanged from today's `fetch_per_action_evidence`-only behavior (existing tests in `tests/test_a220_plan_generator_domain_visibility.py`/`test_a224_cynefin_domain_scoring.py` etc. must still pass unchanged).

### Step 3: write the handoff note

Follow A221 Finding 2's `docs/handoff/` note as the format template exactly (check that file for structure/tone). Cover: the exact server-side mechanism (`arc_check_action_gate`'s `falsification_count >= 3` veto), why it's now reading a signal ARC no longer corrupts going forward but that already has bad historical state, and a suggested direction for `hippocampy` maintainers to evaluate.

### Step 4: full suite + make test-a

```bash
.venv/bin/python -m pytest -q
make test-a
make test-all
```

### Step 5: live-verify

Same environment setup as every prior card in this investigation. Run a live smoke, then:
1. Check the trace for `graph_grounded: True` appearing at least once (the concrete proof this card's core claim — real evidence exists and was previously invisible to this KPI — is now fixed).
2. Query the graph directly for an action the episode attempted 3+ times with real unfalsified rule evidence; confirm `fetch_per_action_evidence`'s `falsified_count` for it stopped climbing after this fix landed (compare a fresh task_id's post-fix trajectory against the pattern documented in A232's own Problem section, where it always matched `attempts` exactly).

## Validation commands

```bash
.venv/bin/python -m pytest -q
make test-a
make test-all
```

## Assumptions/defaults

- Removing `record_reward_prediction_error` entirely (rather than trying to send a "correct" reward signal) is the right scope for this card, per the mathematical proof above that its positive branch was structurally unreachable — it never once helped, only ever hurt or no-op'd. If a future card finds a real, principled reward signal worth wiring in, that's new work, not a revival of this exact mechanism.
- The `graph_evidence` blend in Step 2 prefers the MAX of the two confidence sources and ADDS rule-based supports, rather than fully replacing `fetch_per_action_evidence`'s contribution — deliberately conservative (still shows real contradictions if `fetch_per_action_evidence` ever reports them for a genuinely different reason in the future, once item 1's corruption source is removed and its own signal becomes trustworthy again over time).
