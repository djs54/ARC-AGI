# Plan: A211 — Fix: A Mid-Cycle Crash Never Reaches Annatar or the Graph

## Card metadata

- ID: A211
- Priority: P1
- Layer: ARC runtime
- Dependencies: A210, A201, A204

## Summary

`WorkflowOrchestrator.run()`'s exception handler ends the episode as `CRASHED` without ever telling Annatar or the graph an investigation thread was left open. Close it out, best-effort, without risking the original crash's traceback.

## Technical approach

### 1. Read the current state first

Read `agents/arc4/workflow.py`'s `run()` method in full (the naming will already be "Annatar" if A210 landed first — confirm). Locate the `except Exception:` handler and the exact current shape of `_finish(...)`. Read `agents/arc4/graph_queries.py`'s `write_thread_state` signature (added A201) to confirm its exact call convention. Read `backlog/A204.md`'s Outcome section for the write-ahead/confirm reasoning this card's spirit matches (never let a durability write block or corrupt the primary outcome).

### 2. Implement the close-out

```python
except Exception:
    traceback_text = traceback.format_exc()
    anchor = state.active_investigation_anchor
    thread_id = anchor.get("thread_id") if anchor is not None else None
    if self._dependencies.annatar is not None and thread_id is not None and self._graph_port_for_crash_cleanup is not None:
        # confirm during implementation how a graph_port reference is actually
        # available here -- WorkflowOrchestrator itself has never held one
        # directly (see A202/A204's own reasoning for why: graph_port is
        # closed over by bundle.py for resolve/plan/annatar/execute, not
        # stored on the orchestrator). This may require a small, deliberate
        # deviation from that pattern specifically for crash cleanup, OR a
        # cleaner alternative: expose a crash-cleanup closure the same way
        # `reason`/`execute` are already closed over in bundle.py, passed
        # in as a new optional WorkflowDependencies field. Decide which is
        # more consistent with the existing architecture during
        # implementation -- do not just bolt a graph_port attribute onto
        # the orchestrator without checking whether a closure-based
        # approach fits the established pattern better.
        try:
            write_thread_state = getattr(self._graph_port_for_crash_cleanup, "write_thread_state", None)
            if write_thread_state is not None:
                write_thread_state(thread_id, "exhausted")  # confirm this is the right state value against A201's schema -- "exhausted" may not be semantically correct for an abnormal crash close, consider whether A201's schema needs a distinct "crashed"/"abandoned" state value and, if so, whether that requires a small hippocampy-side schema addition (write a short handoff note if so, following the established docs/handoff/B278-*.md pattern)
        except Exception:
            pass  # the close-out attempt must never mask or replace the original crash
    return self._finish(
        state,
        WorkflowStatus.CRASHED,
        "crash",
        phase_results,
        traceback_text=traceback_text,
    )
```

The sketch above intentionally leaves open exactly HOW a graph_port reference reaches this handler — that's a real architectural question to resolve during implementation, not to guess at. Read how `_route_budget_through_annatar`/`_route_second_veto_through_annatar` get their Annatar callable (a closure from `bundle.py`, not a stored reference) and decide whether crash cleanup should follow the same closure pattern (a new optional `WorkflowDependencies` field, e.g. `on_crash_cleanup`) or whether there's a simpler existing seam to reuse. Prefer whichever is more consistent with this repo's established "WorkflowOrchestrator holds no direct graph_port reference" convention (A202/A204's own stated reasoning) — do not introduce the first exception to that pattern without a clear reason written down.

### 3. Non-negotiable: never mask the original crash

Write the test for this FIRST (TDD): mock the close-out call to also raise, confirm `WorkflowRunResult.traceback` still contains the ORIGINAL exception's traceback text, not the close-out failure's. Confirm this test fails against a naive implementation that doesn't wrap the close-out in its own try/except, before implementing the real (correctly wrapped) version.

## Concrete file changes

| File | Change |
|------|--------|
| `agents/arc4/workflow.py` | `except Exception:` handler gains best-effort close-out |
| `arc_runtime/bundle.py` (if a new closure/dependency field is the chosen approach) | Wire the close-out callable |
| `agents/arc4/ports.py` / `agents/arc4/types.py` (if a new `WorkflowDependencies` field is added) | New optional field |
| `tests/test_a211_crash_annatar_cleanup.py` (new) | Coverage, see Tests |
| `docs/handoff/B278-*.md` (new, only if A201's schema needs a new state value for abnormal closes) | Cross-repo ask, following the established handoff pattern |

## Tests

`tests/test_a211_crash_annatar_cleanup.py`:

1. A crash with an open investigation thread and Annatar configured — the close-out call fires with the correct `thread_id`.
2. A crash with NO open investigation thread (`state.active_investigation_anchor is None`) — no close-out call attempted, behaves exactly as before this card.
3. A crash with no Annatar configured at all — no close-out call attempted, behaves exactly as before this card (regression guard).
4. **The non-negotiable one:** close-out call itself raises — `WorkflowRunResult.traceback` still contains the ORIGINAL crash's traceback text, not the close-out failure's. Write this first, confirm it fails against a naive (unwrapped) implementation.
5. A crash where `state` itself is in some way minimal/edge-case (e.g. `active_investigation_anchor` is a dict missing the expected `thread_id` key) — does not raise a new, different exception while trying to clean up (defensive `dict.get`, not direct indexing).

## Validation commands

```bash
.venv/bin/python -m pytest tests/test_a211_crash_annatar_cleanup.py -v
.venv/bin/python -m pytest tests/test_arc4_workflow.py -v
make test-a
make test-all
```

## Assumptions/defaults

- If A201's graph schema turns out to have no good state value for "abnormally closed by a crash," using `"exhausted"` as the closest available fit is an acceptable interim choice — note it plainly as an interim choice in the Outcome section, and write a short handoff doc flagging the gap for hippocampy's side if it seems worth a real schema addition, rather than silently picking a state value that's semantically wrong and moving on.
- This card does not attempt to resume or recover the crashed cycle itself — that's A204's territory (resume/reconciliation on the NEXT process start). This card only ensures the graph's bookkeeping reflects that the thread is no longer actively being worked on, so a future `fetch_entity_neighborhood`/`fetch_untested_actions` call doesn't see a permanently-"in-progress" record that's actually abandoned.
