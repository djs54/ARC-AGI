# A250 — Retire the No-Annatar-Configured Legacy Branch: Plan

## Card metadata

- Card: `backlog/A250.md`
- Depends on: A202 (the convention this retires), A221 (the "revisit stated tradeoffs" precedent), A249 (most recent evidence of the pattern growing), A224 (related but out-of-scope `readiness_gate` optionality)

## Design (confirmed by direct read before writing this plan)

- `agents/arc4/ports.py:166-185` — `WorkflowDependencies` dataclass; `annatar: AnnatarPhase | None = None` is field #7, positioned before `on_crash_cleanup`/`readiness_gate` (both still defaulted) — removing `annatar`'s default keeps the dataclass field-ordering valid (no reordering needed) since all fields up through `annatar` become required, and the remaining fields after it stay optional.
- `agents/arc4/workflow.py` — six sites, confirmed by direct read as of A249's merge (`35a223b`):
  1. `~line 228` — probe path, `if self._dependencies.annatar is None and evaluation_payload.metadata.get("action_space_exhausted"): return self._finish(...)`.
  2. `~line 254-323` — probe path's `if self._dependencies.annatar is not None: ... else: ... continue` fork.
  3. `~line 468` — normal cycle, `if self._dependencies.annatar is None and evaluation_payload.metadata.get("action_space_exhausted"): return self._finish(...)`.
  4. `~line 509-640` — normal cycle's `if self._dependencies.annatar is not None: ... else: if stall_reason is not None: return self._finish(...)` fork.
  5. `_route_budget_through_annatar`, `~line 708-709` — `if self._dependencies.annatar is None: return self._finish(..., BUDGET_EXHAUSTED, ...)`.
  6. `_route_second_veto_through_annatar`, `~line 789-790` — `if self._dependencies.annatar is None: return self._finish(..., SKIPPED, "second_veto", ...)`.
- `~line 649-653` (inside the crash-handling `except Exception:` block) — `self._dependencies.annatar is not None and thread_id is not None and self._dependencies.on_crash_cleanup is not None` — this one is NOT a legacy-mode fork in the same sense (it's a genuine three-way AND-gate for whether crash cleanup can run at all); once `annatar` is guaranteed non-`None`, this simplifies to `thread_id is not None and self._dependencies.on_crash_cleanup is not None` — a real simplification, but a different kind of change than deleting a whole branch. Include it, but note it's a narrowing, not a branch deletion.
- `tests/fixtures/workflow_pre_a202_baseline.py` — the frozen pre-A202 orchestrator module, loaded via `importlib` by `tests/test_a202_annatar_orchestrator_integration.py::_load_baseline_orchestrator_module` (`~line 84-96`) and used only by `TestBackwardCompatByteForByte` (`~line 123-152`).
- `tests/test_arc4_workflow.py` — 6 tests total, all constructing `WorkflowDependencies` without `annatar=`.

### Step 1 — comprehensive enumeration (do this first, in full, before editing anything)

```bash
grep -rln "annatar is None\|annatar=None\|no_annatar\|NoAnnatar\|preserves_exact_prior_behavior\|workflow_pre_a202_baseline" tests/ agents/ arc_runtime/
```

For each hit, classify it into one of:
- **(a) Production dead branch** — one of the six (or the crash-handler narrowing) enumerated above.
- **(b) Test asserting no-Annatar-*specific* behavior** (the dead branch's own outcome — `STALLED`/`SKIPPED`/`BUDGET_EXHAUSTED` reached via the bare `check_stall`/second-veto/budget path, or a byte-for-byte comparison against the frozen baseline) — candidate for deletion, pending (c)-style confirmation.
- **(c) Confirming equivalent** — an Annatar-configured test that already covers the same underlying mechanism, justifying (b)'s deletion without coverage loss. Likely candidates, confirm each by name and read, don't assume: `test_a202_annatar_orchestrator_integration.py::TestAnnatarControlFlow` (general control flow with Annatar), `TestSecondVetoRoutesThroughAnnatar` (second-veto-with-Annatar), `test_a209_budget_annatar_routing.py::test_budget_fires_with_annatar_configured`/`test_annatar_response_does_not_override_budget` (budget-with-Annatar).
- **(d) Test using a no-Annatar shape incidentally** — not testing no-Annatar behavior itself (e.g. degraded-visibility tests in `test_a237_*.py`/`test_a244_*.py`, fallback-candidate tests in `test_a238_*.py`, or `test_arc4_workflow.py`'s `test_phase_order_runs_in_fixed_sequence`/`test_crash_guard_captures_traceback`/`test_single_veto_triggers_one_replan_pass`) — needs a minimal fake Annatar added, not deletion.

Record this classification in `backlog/A250.md`'s Outcome section as the "comprehensive enumeration" the card's acceptance criteria require — a table or list is fine, but it must be a real, grep-verified list, not the Problem section's sample re-typed.

### Step 2 — production code

```python
# ports.py
@dataclass(slots=True)
class WorkflowDependencies:
    perceive: PerceivePhase
    resolve: ResolvePhase
    plan: PlanPhase
    vet: VetPhase
    execute: ExecutePhase
    evaluate: EvaluatePhase
    annatar: AnnatarPhase  # A250: no longer optional -- always wired in production since A202
    on_crash_cleanup: callable | None = None
    readiness_gate: callable | None = None
```

In `workflow.py`, for each of the six sites: delete the `if self._dependencies.annatar is None: ...` / `else: ...` branch entirely, keeping only the body that ran when Annatar *was* configured (already unconditional in every real invocation). For the probe-path fork (`~254-323`) and normal-cycle fork (`~509-640`), this means de-indenting the `if self._dependencies.annatar is not None:` block's contents to top-level (no more conditional at all) and deleting the trailing `else:` clause. Also fold the crash-handler's three-way AND-gate down to two conditions (`thread_id is not None and self._dependencies.on_crash_cleanup is not None`).

Since `annatar` is now guaranteed present, `A249`'s two dedicated no-Annatar `action_space_exhausted` checks (sites 1/3 in the card's Problem list) are deleted outright — `action_space_exhausted` now unconditionally routes through Annatar via the `stall_reason` fold, with no legacy fallback needed at all.

### Step 3 — test code, per the Step 1 classification

- **(b)+(c) confirmed pairs:** delete the (b) test/class/file. If deleting an entire test *file* (e.g. `tests/test_arc4_workflow.py`, if every one of its 6 tests turns out to fall into (b) or (d)-resolved-elsewhere), delete the file outright rather than leaving an empty shell.
- **(d) incidental users:** add a minimal fake Annatar to each affected `WorkflowDependencies(...)` construction:

```python
def _fake_annatar(state, perception, execution, evaluation, *, stall_reason=None, veto_reason=None, veto_alternative_action_id=None, readiness_report=None, resolve_report=None):
    from agents.arc4.types import AnnatarOutcome
    return AnnatarOutcome(decision="advance", degraded=False, resume_mapping=False, exploration_complete=None)
```

(Illustrative — confirm `AnnatarOutcome`'s real required fields/shape before writing this; check `run_annatar_cycle`'s actual return type and any other tests' existing minimal-fake-Annatar helpers first — there may already be one in `test_a202_annatar_orchestrator_integration.py`'s own helpers worth reusing instead of inventing a second one.)

### Step 4 — delete the frozen baseline

Once `TestBackwardCompatByteForByte` is deleted (per Step 3), confirm nothing else imports `tests/fixtures/workflow_pre_a202_baseline.py` (`grep -rl "workflow_pre_a202_baseline" tests/`) and delete the file.

### Step 5 — ARCHITECTURE.md

```bash
grep -n "annatar.*None\|AnnatarPhase | None\|no Annatar" ARCHITECTURE.md
```

If any hit describes the current architecture as having an optional Annatar, update it to reflect the new mandatory requirement — a one- or two-line factual correction, not a rewrite.

## Implementation approach

### Files

- Modify: `agents/arc4/ports.py` — `annatar` field.
- Modify: `agents/arc4/workflow.py` — six branch deletions + one narrowing.
- Modify/delete: test files per Step 1/3's classification (exact list determined by the enumeration, not fixed in advance by this plan).
- Delete: `tests/fixtures/workflow_pre_a202_baseline.py` (once unreferenced).
- Modify (if needed): `ARCHITECTURE.md`.

### TDD

This card is unusual for this backlog: it's net-*negative* on test count (deleting tests that verify dead behavior), not net-positive. Still follow TDD discipline for the parts that are additions:

- Before deleting any (b)-classified test, run it against the *current* (pre-fix) code and confirm it passes — proving it really does test what you think it tests, not a stale assumption.
- After Step 2's production changes, run the full suite once with the (b)-classified tests still in place (not yet deleted) — they should now *fail* (since the dead branches they exercise no longer exist), confirming your production change actually took effect and these tests really were exercising exactly the code you removed, not something else entirely.
- Only then delete them, and re-run to confirm green.
- For (d)-classified tests: after adding the fake Annatar, confirm each still passes with its *original* assertions unchanged — the fake Annatar's job is to not interfere with what these tests actually check.
- New test (small, if not already implicitly covered): `WorkflowDependencies(...)` constructed without `annatar=` now raises `TypeError` (missing required argument) — a one-line regression guard that the optionality is really gone, not just unused.

### Validation commands

```bash
grep -rln "annatar is None\|annatar=None\|no_annatar\|NoAnnatar\|preserves_exact_prior_behavior\|workflow_pre_a202_baseline" tests/ agents/ arc_runtime/
make test-a
make test-all
make check-compliance
```

### Live-verify

Same environment/discipline as every prior card this investigation (`CAMPY_MCP_CMD` pointing at the sibling `hippocampy` repo, `campy status` check first, full `tee`'d output to a log file read completely, generous timeout — `run_in_background: true` with `timeout: 600000`+). This card's production change should be a genuine no-op for real gameplay (Annatar was already always supplied there) — confirm a live smoke run completes exactly as it would have before this card (no new errors, same general KPI shapes: `ANCHOR_PROGRESS`/`STALL_CHECK`/`RESOLVE_ANNATAR`/`PROBE_ANNATAR` lines still appear normally). This is a *regression* check, not a new-behavior check — there's nothing new to observe live, just confirm nothing broke.

## Assumptions/defaults

- Default to deleting (b)-classified tests once a (c) equivalent is confirmed, rather than keeping them around "just in case" — dead-code-testing-dead-code is exactly the accumulating cost this card exists to stop.
- Default to NOT touching `readiness_gate`'s parallel optionality — flag it in the Outcome as a named follow-up candidate if the investigation surfaces anything concrete, but do not scope-creep into fixing it inside this card.
- If any (b)-classified test turns out to have NO clean (c) equivalent (a real, non-obvious gap), do not delete it — instead, port its actual scenario forward with a fake/real Annatar supplied, preserving the coverage, and note this explicitly in the Outcome rather than silently either deleting real coverage or leaving dead-code testing in place.
