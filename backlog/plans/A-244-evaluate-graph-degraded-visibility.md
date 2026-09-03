# A244 — Evaluate-Phase Graph-Degraded Visibility: Plan

## Card metadata

- Card: `backlog/A244.md`
- Depends on: A237 (the exact pattern this extends), A205/A224 (the two earlier precedents A237 itself mirrored)

## Design (mechanical extension of a proven pattern — one real question to check first)

Confirmed by direct read of the precedent before writing this plan:

- `agents/arc4/types.py` — `PlanningResult.degraded`/`VetDecision.degraded`/`AnnatarOutcome.degraded`, all dedicated dataclass fields, all wired into `to_dict()`/`from_dict()`.
- `agents/arc4/workflow.py` — `state.plan_degraded = getattr(planning_payload, "degraded", False)` / `state.vet_degraded = getattr(vet_payload, "degraded", False)`, set immediately after each phase call using `getattr(..., False)` (not direct attribute access) specifically because some existing tests fake phase dependencies with plain objects lacking `.degraded` — the same defensive convention must be used here.
- `agents/arc4/telemetry.py` — `"plan_degraded": bool(getattr(state, "plan_degraded", False))` etc. in the per-cycle summary dict.
- `agents/arc4/plan_generator.py::PlanGenerator.__init__` — the A237-established instance-scratch `self._degraded` flag, reset at the top of every `generate()` call, read back at the end. **Check first** whether `Evaluator` is reused the same way across an episode's cycles (instantiated once, called every cycle) the same way `PlanGenerator`/`PlanVetter` are, per A237's own investigated reasoning for why the instance-scratch pattern was safe there (single long-lived instance, no re-entrant/concurrent calls, synchronous codebase) — confirm this holds for `Evaluator` too before reusing the same mechanism, don't assume it transfers just because the shape looks similar.

### The fix

1. `agents/arc4/types.py`: `EvaluationResult` gains `degraded: bool = False`, wired into `to_dict()`/`from_dict()`.
2. `agents/arc4/evaluator.py`: the two identified sites —

```python
# fetch_causal_path, inside evaluate()
except Exception:
    pass  # graph unavailable — don't override
    # A244: -> also set the instance-scratch degraded flag (or whichever
    # mechanism Step 1's investigation settles on)
```

```python
# _action_space_exhausted's fetch_untested_actions call
except Exception:
    pass
    # A244: -> also set the instance-scratch degraded flag
return True, "threshold_only"
```

Both need to flow into the single `EvaluationResult` returned by `evaluate()` — since `_action_space_exhausted` is called from inside `evaluate()` and its return value only reaches a local `action_space_exhausted, exhaustion_source` tuple, not the instance state directly, confirm the chosen mechanism (instance-scratch flag, most likely, matching A237) correctly accumulates across both potential exception sites within one `evaluate()` call, the same way `PlanGenerator._degraded` accumulates across its own three sites.

3. `agents/arc4/types.py`: `WorkflowState` gains `evaluate_degraded: bool = False` (+ `to_dict`/`from_dict`).
4. `agents/arc4/workflow.py`: set `state.evaluate_degraded = getattr(evaluation_payload, "degraded", False)` immediately after each of the evaluate-phase call sites (check how many there are — likely mirrors plan/vet's two call sites, the normal cycle and the probe-path cycle, or possibly more given evaluate is called from more places than plan/vet; enumerate them directly rather than assuming exactly two).
5. `agents/arc4/telemetry.py`: add `"evaluate_degraded": bool(getattr(state, "evaluate_degraded", False))` to the per-cycle summary dict, same location as the other four.

## Implementation approach

### Files

- Modify: `agents/arc4/types.py` — `EvaluationResult.degraded`, `WorkflowState.evaluate_degraded`.
- Modify: `agents/arc4/evaluator.py` — the two exception sites.
- Modify: `agents/arc4/workflow.py` — evaluate-phase call site(s), setting `state.evaluate_degraded`.
- Modify: `agents/arc4/telemetry.py` — per-cycle summary dict.
- Test: new `tests/test_a244_evaluate_graph_degraded_visibility.py`.

### TDD

- New test: a raising `fetch_causal_path` (via a fake `graph_query_port`) → `EvaluationResult.degraded=True`, `meaningful_progress`/`causal_override` behavior unchanged (still no override, since the exception path already skips it).
- New test: a raising `fetch_untested_actions` inside `_action_space_exhausted` → `EvaluationResult.degraded=True`, `exhaustion_source` still `"threshold_only"` (behavior unchanged, only visibility added).
- New test: `graph_query_port=None` (no graph at all) → `degraded=False` — the single most important distinction, mirroring A237's own emphasis: "no graph configured" must never be conflated with "graph failed."
- New test: a healthy `graph_query_port` that returns normally on both calls → `degraded=False`.
- New test: `WorkflowState.evaluate_degraded` correctly reflects the most recent evaluate call's `degraded` value, set at the right call site(s) in `workflow.py` (a real orchestrator-level test if evaluate has multiple call sites, mirroring A237's own two-call-site coverage).
- New test: `telemetry.py`'s per-cycle summary surfaces `evaluate_degraded` correctly, defaulting `False` when `state` is missing the field (old serialized state) or is `None`.
- Regression: existing `evaluator.py`-adjacent tests continue to pass with `degraded` defaulting `False` wherever not specifically exercised.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a244_evaluate_graph_degraded_visibility.py -v
.venv/bin/python -m pytest tests/test_arc4_evaluator.py -v
make test-a
make test-all
```

(Confirm the actual existing evaluator test file name before running — `test_arc4_evaluator.py` is a guess based on this repo's naming convention, check `tests/` directly.)

### Live-verify

Same environment/discipline as every prior card this investigation (`.venv` worktree symlink if isolated, `CAMPY_MCP_CMD` absolute path, `campy start` + warm-up wait if the daemon shows offline, full `tee`'d output read completely, generous timeout — recent runs in this session have taken 1-4+ minutes). Confirm `evaluate_degraded: false` appears correctly in a normal live run's telemetry. For the degraded case, follow A237's own precedent: don't stop the shared hippocampy daemon if there's any doubt other work depends on it being up — a targeted integration test with a raising `graph_query_port` exercising the real `evaluate()`/`workflow.py` path is an authorized substitute, used exactly this way by A237 itself. Be honest in the Outcome about which was actually done.

## Assumptions/defaults

- Mirror A237's instance-scratch-flag mechanism exactly, unless investigation of `Evaluator`'s actual reuse pattern shows a concrete reason it doesn't transfer.
- One combined `degraded` bit for both exception sites (not split into per-site reasons) — no known consumer needs the distinction, same default A237 itself chose for `plan_vetter.py`'s two sites.
