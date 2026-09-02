# A237 — Plan/Vet Graph-Degraded Visibility: Plan

## Card metadata

- Card: `backlog/A237.md`
- Depends on: A205 (`annatar_degraded` pattern), A224 (`readiness_gate_partial` pattern), A135/A177/A192 (the graph calls whose exceptions are the gap), A232 (adjacent `plan_vetter.py` logic, unchanged)

## Design (settled — this is a mechanical extension of an existing, proven pattern, not a new design)

Confirmed by direct read of the precedent before writing this plan:

- `agents/arc4/types.py:340` — `AnnatarOutcome.degraded: bool = False`, a dedicated dataclass field (not a `metadata` dict key).
- `agents/arc4/workflow.py:211` and `:482` — `state.annatar_degraded = outcome.degraded`, set immediately after each of the two call sites that invoke `self._dependencies.annatar(...)`.
- `agents/arc4/types.py:486` — `WorkflowState.annatar_degraded: bool = False`, wired into `to_dict()`/`from_dict()` at lines 546/579.
- `agents/arc4/telemetry.py:309` — `"annatar_degraded": bool(getattr(state, "annatar_degraded", False))` in the per-cycle summary dict, with a comment explaining the `getattr(..., False)` covers both "no Annatar configured" and "state is None."
- `readiness_gate_partial` repeats the identical shape one field over (types.py:518/549/582, workflow.py:131, telemetry.py:317).

This card copies that exact shape onto `PlanningResult`/`VetDecision`/plan+vet's own call sites. No new mechanism to invent.

## Implementation approach

### Files

- Modify: `agents/arc4/types.py` — `PlanningResult` gains `degraded: bool = False` (+ `to_dict`/`from_dict`); `VetDecision` gains `degraded: bool = False` (+ `to_dict`/`from_dict`); `WorkflowState` gains `plan_degraded: bool = False` and `vet_degraded: bool = False` (+ `to_dict`/`from_dict`), placed alongside `annatar_degraded`/`readiness_gate_partial` for locality.
- Modify: `agents/arc4/plan_generator.py` — `_build_candidates` (or `generate`, whichever is cleaner given `_build_candidates`'s current signature doesn't return a value the caller can easily thread a flag through — check during implementation whether `_build_candidates` needs to return `(candidates, degraded)` or whether an instance-level/closure-local approach fits `PlanGenerator`'s existing style better) sets `degraded=True` when any of the three `except Exception` sites fire; `generate()`'s `PhaseResult(payload=PlanningResult(..., degraded=...))` carries it out.
- Modify: `agents/arc4/plan_vetter.py` — `_check_graph_gate`/`_has_live_rule_evidence`'s `except` branches need a way to signal degradation back to `vet()`, which constructs the final `VetDecision`. Simplest: have `_check_graph_gate` return `{"allowed": True, "reason": "graph_error", "degraded": True}` (extending its existing dict return, not changing its shape) and have `_has_live_rule_evidence` return a `(bool, degraded)` tuple or store degradation as an instance attribute set right before use and read right after (pick whichever keeps `vet()`'s control flow easiest to read — this is a small enough function that either works, no strong precedent to match here since `plan_vetter.py` has no existing multi-signal-return pattern like `AnnatarOutcome` to mirror exactly).
- Modify: `agents/arc4/workflow.py` — right after `plan = self._invoke_phase("plan", self._dependencies.plan, ...)` and `vet = self._invoke_phase("vet", self._dependencies.vet, ...)` (find current line numbers during implementation — this file has shifted across many prior cards), add `state.plan_degraded = plan_payload.degraded` and `state.vet_degraded = vet_payload.degraded`, mirroring `state.annatar_degraded = outcome.degraded`'s exact placement (immediately after the call, before any branching on the result).
- Modify: `agents/arc4/telemetry.py` — add `"plan_degraded": bool(getattr(state, "plan_degraded", False))` and `"vet_degraded": bool(getattr(state, "vet_degraded", False))` to the same per-cycle summary dict `annatar_degraded`/`readiness_gate_partial` already live in.
- Test: new `tests/test_a237_plan_vet_degraded_visibility.py`.

### TDD

- New test: a fake `graph_port` whose `fetch_per_action_evidence` raises — `PlanGenerator.generate(...)` returns `PlanningResult.degraded=True`; a fake `graph_port` that returns normally — `degraded=False`.
- New test: same for `fetch_rules_for_action` raising (a graph_port whose `fetch_per_action_evidence` succeeds but `fetch_rules_for_action` raises) — confirms all three sites are actually wired, not just the first one implemented.
- New test: same for `fetch_untested_actions` raising (this one's in `_available_actions`, a different method than the other two in `_build_candidates` — confirm the flag still threads through to the same `PlanningResult.degraded`).
- New test: `graph_port=None` (no graph at all) — `degraded` stays `False` (this is the existing, correct "no graph configured" case, not a failure — must not be conflated with a real exception).
- New test: `PlanVetter._check_graph_gate` raising inside `.vet(...)` — `VetDecision.degraded=True`, `approved` still whatever the fail-open behavior already produces (unchanged).
- New test: `PlanVetter._has_live_rule_evidence` raising — same, `VetDecision.degraded=True`, override behavior unchanged (still doesn't override, per the existing "degrades to False (no override)" comment).
- Regression: every existing test in `tests/test_arc4_plan_generator.py`/`tests/test_arc4_plan_vetter.py` (or equivalent existing test files — check actual names) continues to pass with `degraded` defaulting `False` and not otherwise observed unless a test specifically asserts on it.
- Regression: `agents/arc4/workflow.py`'s existing plan/vet-adjacent tests unaffected — `state.plan_degraded`/`vet_degraded` default `False` and don't influence any existing assertion.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a237_plan_vet_degraded_visibility.py -v
.venv/bin/python -m pytest tests/test_arc4_plan_generator.py tests/test_arc4_plan_vetter.py -v
make test-a
make test-all
```

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline). Two parts:

1. **Normal run:** a standard live smoke (`run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 30`) with the daemon healthy — confirm via the trace/telemetry output that `plan_degraded`/`vet_degraded` are present and `False` throughout.
2. **Degraded run:** if it's safe and practical to arrange within the worktree (does not affect the shared daemon other live work might depend on — check before stopping anything), briefly stop the hippocampy daemon mid-run (or point `CAMPY_MCP_CMD` at a deliberately-broken command for one invocation) and confirm `plan_degraded`/`vet_degraded` flip to `True` and are visible in the resulting trace. If arranging a real live degraded run isn't practical/safe to do without disrupting other work, a targeted integration test that exercises the full `workflow.py` cycle with a raising `graph_port` (not just the unit-level TDD tests above) is an acceptable substitute — document honestly which was actually done, per this session's own standing discipline against overclaiming live verification that didn't happen.

## Assumptions/defaults

- One combined `degraded` bit per phase (not split into more granular reasons like `gate_degraded` vs. `override_check_degraded` for `plan_vetter.py`) unless implementation reveals a concrete reason two consumers need to distinguish them — the card's own "What this delivers" section names this as an open call, default to the simpler option.
- This card does not extend the same pattern to `perceive.py`/`evaluator.py` even if the same gap is found there — name it as a follow-up, don't build it here under scope creep.
