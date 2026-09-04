# A252 — Budget-Exhausted Honest Close-Out: Plan

## Card metadata

- Card: `backlog/A252.md`
- Depends on: A209 (built the function this card modifies), A211 (the direct-close-out pattern this card copies), A221 (why the function's docstring is stale)

## Design (confirmed by direct read before writing this plan)

- `agents/arc4/workflow.py:652-734` — `_route_budget_through_annatar`, the full function this card modifies.
- `agents/arc4/workflow.py`'s crash handler (`except Exception:` block, search for `on_crash_cleanup`) — the exact pattern to mirror: extract `thread_id` from `state.active_investigation_anchor`, call `self._dependencies.on_crash_cleanup(thread_id, "exhausted")` inside its own `try/except Exception: pass`.
- `arc_runtime/bundle.py`'s `_on_crash_cleanup(thread_id: str, state: str) -> None` — confirms the callable's real signature: `(thread_id, state_value) -> None`, calling `graph_port.write_thread_state(thread_id, state)` if available.
- `agents/arc4/annatar_state_machine.py:17-23` — `InvestigationState` enum; `EXHAUSTED = "exhausted"` is a real, valid member, confirming A211's reused string is schema-valid (at minimum syntactically) — but check the sibling `hippocampy` repo's actual `write_thread_state` handler for whether it validates strictly against this enum or accepts any string, before deciding whether to introduce a new value.
- `tests/test_a209_budget_annatar_routing.py::test_annatar_response_does_not_override_budget` — the existing regression test this function's own docstring cites; read it fully before editing, since its construction may currently assume the synthetic-Annatar-call approach this card removes.

### The fix

```python
def _route_budget_through_annatar(
    self,
    state: WorkflowState,
    current_observation: Mapping[str, Any],
    phase_results: list[PhaseResult[Any]],
) -> WorkflowRunResult:
    """A209 fix (2026-08-25): `check_budget` previously ended the episode
    directly, without ever giving anything a chance to close out an open
    investigation thread's graph bookkeeping first -- another place a
    termination could happen with no cleanup. The budget ceiling itself is
    non-negotiable: this method always ends the episode as BUDGET_EXHAUSTED
    regardless of anything below.

    A252: previously ran a full synthetic Annatar cycle (fabricated
    perception/execution/evaluation payloads through the real
    run_annatar_cycle) purely to trigger its own end-of-cycle
    write_thread_state bookkeeping -- discarding the decision it produced
    but keeping the graph write, which was computed from data that doesn't
    reflect anything real and could land on a specific, misleadingly-
    precise InvestigationState value. Also risked a real LLM call
    (resolve_llm_vote) if the open thread happened to be AWAITING_LLM,
    spending real cost on a decision nobody reads. Replaced with the same
    direct close-out A211 already uses for the crash path: call
    on_crash_cleanup(thread_id, ...) directly, no synthetic Annatar cycle,
    no risk of a misleading graph write or a wasted LLM call. See
    backlog/A252.md.
    """
    # First iteration has no prior cycle to report; end immediately.
    if state.step_index == 0:
        return self._finish(state, WorkflowStatus.BUDGET_EXHAUSTED, "budget_exhausted", phase_results)

    anchor = state.active_investigation_anchor
    thread_id = anchor.get("thread_id") if isinstance(anchor, dict) else None
    if thread_id is not None and self._dependencies.on_crash_cleanup is not None:
        try:
            self._dependencies.on_crash_cleanup(thread_id, "exhausted")  # or a new value -- see Step 2 below
        except Exception:
            pass  # best-effort, same non-negotiable as the crash path: never block the real result

    return self._finish(state, WorkflowStatus.BUDGET_EXHAUSTED, "budget_exhausted", phase_results)
```

(Illustrative — confirm the exact current docstring/surrounding code shape before editing; the `state_value` string is the one open design question, resolved by Step 2 below with real evidence, not assumed from this sketch.)

### Step 2 — the state-value question, with evidence

```bash
grep -rn "write_thread_state" ../hippocampy/campy/ 2>/dev/null
```

Read the real handler. If it validates its second argument against a strict enum/allowlist, confirm `"exhausted"` is accepted (it should be, since A211 already relies on this) and decide whether a distinct `"budget_exhausted"` value would need a schema change on the `hippocampy` side first (if so, name that precisely as a follow-up, matching this card's own explicit non-goal of not guessing at a cross-repo schema change). If the handler accepts any string freely (no strict validation), introducing a new, more precise value costs nothing extra and is the better choice for future graph consumers wanting to distinguish "crashed" from "ran out of budget" — but only take that path if the evidence supports it being free; default to reusing `"exhausted"` (A211's own precedent) if there's any doubt.

## Implementation approach

### Files

- Modify: `agents/arc4/workflow.py` — `_route_budget_through_annatar`.
- Test: new `tests/test_a252_budget_exhausted_honest_close_out.py`.
- Check and, if needed, update: `tests/test_a209_budget_annatar_routing.py::test_annatar_response_does_not_override_budget` and any other test in that file that currently asserts on the synthetic-Annatar-call behavior this card removes.

### TDD

- New test: budget exhausted (`step_index > 0`), a real open `active_investigation_anchor` with a `thread_id` — confirm `on_crash_cleanup` is called with `(thread_id, <chosen state value>)`, and confirm `self._dependencies.annatar` (a mock/spy) is **never** called.
- New test: budget exhausted, `active_investigation_anchor is None` — confirm no cleanup call is attempted (nothing to close out), episode still ends `BUDGET_EXHAUSTED`.
- New test: budget exhausted, an anchor present but its `thread_id` is `None` — same as above, no cleanup call, no crash.
- New test: `on_crash_cleanup` raises — episode still ends `BUDGET_EXHAUSTED` cleanly, exception swallowed, mirroring the crash path's own `except Exception: pass` regression guard.
- New test: `step_index == 0` — unchanged behavior (ends immediately, no cleanup attempted at all — there's nothing to close out on the very first cycle).
- Regression: read and, if needed, update `test_a209_budget_annatar_routing.py`'s existing tests (especially `test_annatar_response_does_not_override_budget`, `test_budget_fires_with_annatar_configured`, `test_no_annatar_configured_budget_exhaustion` if it still exists post-A250) to reflect that `self._dependencies.annatar` is no longer called by this function at all — some of these tests may need real edits, not just re-runs, since their whole premise (Annatar is invoked and its decision is checked/ignored) has changed. Read each one fully before deciding whether to keep, delete, or rewrite it, same judgment discipline as A250's own test-classification step.

### Validation commands

```bash
.venv/bin/python -m pytest tests/test_a252_budget_exhausted_honest_close_out.py -v
.venv/bin/python -m pytest tests/test_a209_budget_annatar_routing.py -v
make test-a
make test-all
make check-compliance
```

### Live-verify

Same environment/discipline as every prior card this investigation (`CAMPY_MCP_CMD` pointing at the sibling `hippocampy` repo, `campy status` check first, full `tee`'d output to a log file read completely, generous timeout). Run a live smoke with a small `--max-steps` (e.g. 3-5) deliberately small enough to guarantee budget exhaustion mid-episode, and confirm the episode ends cleanly as `BUDGET_EXHAUSTED` with no errors. Actually landing on an open thread in a specific `InvestigationState` at that exact moment isn't reliably controllable — report honestly whether it happened to occur; the TDD suite is the primary evidence either way, per the card's own explicit fallback standard.

## Assumptions/defaults

- Default to reusing `"exhausted"` (A211's precedent) for the state value unless Step 2's real investigation of `hippocampy`'s `write_thread_state` handler shows a distinct value is free and clearly better.
- If `test_a209_budget_annatar_routing.py` turns out to have several tests whose entire premise no longer applies (Annatar being invoked for the budget path at all), it's fine to delete them once confirmed redundant — same standard as A250's own test-retirement judgment — but only after confirming, not assuming, that nothing else in that file depends on them.
