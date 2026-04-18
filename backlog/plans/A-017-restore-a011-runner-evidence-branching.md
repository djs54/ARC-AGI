# A-017 - Restore A011 Runner-Side Evidence-Aware REPLAN Branching

## Card metadata

- Card: A017
- Priority: P0
- Layer: ARC runtime
- Depends on: A010, A011

## Summary

Land the runner-side half of A011 that was lost during reassembly. Replace the signature-only `_replan_target` at `agents/arc3/runner.py:1989-2028` with an evidence-aware branch selector that inspects `hypothesis_context.action_facts`, `hypothesis_context.action_coverage`, and `orchestrator.solve_engine._archetype_confidence` before deciding which phase REPLAN resumes into, and thread a `route_reason` string through the two `replan_exit` trace emitters.

## Implementation approach

1. **Rewrite `_replan_target`** at `agents/arc3/runner.py:1989-2028`.

   Change the signature from:

   ```python
   def _replan_target(self, orchestrator: ARCOrchestrator) -> SolvePhase:
   ```

   to:

   ```python
   def _replan_target(self, orchestrator: ARCOrchestrator) -> tuple[SolvePhase, str]:
   ```

   Implementation:

   ```python
   def _replan_target(self, orchestrator: ARCOrchestrator) -> tuple[SolvePhase, str]:
       """Choose (target_phase, route_reason) after REPLAN.

       Evidence-aware decision tree (A017, restoring A011 runner-side).
       First match wins.
       """
       try:
           solve_ctx = getattr(orchestrator, "_solve_context", {}) or {}
           hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}

           # --- Evidence predicates (computed once up front) ---
           action_facts = hyp_ctx.get("action_facts") or []
           action_coverage = hyp_ctx.get("action_coverage") or {}
           tested_count = int(action_coverage.get("tested_count") or 0)
           available_total = int(action_coverage.get("available_total") or 0)
           untested_count = int(action_coverage.get("untested_count") or 0)
           exploration_complete = bool(action_coverage.get("initial_exploration_complete"))

           det_effects = [
               f for f in action_facts
               if str(f.get("fact_type") or "").lower() == "deterministic_effect"
           ]
           all_actions_low_value = (
               len(det_effects) > 0
               and tested_count >= available_total
               and available_total > 0
               and all(
                   str(f.get("value_status") or "").lower() == "low_value"
                   for f in det_effects
               )
           )

           roles = solve_ctx.get("object_roles") or {}
           player_conf = float((roles.get("player") or {}).get("confidence") or 0.0)
           goal_conf = float((roles.get("goal") or {}).get("confidence") or 0.0)
           geometry_high_conf = player_conf >= 0.6 and goal_conf >= 0.6

           coverage_saturated = exploration_complete and untested_count == 0

           arch_conf = float(
               getattr(
                   getattr(orchestrator, "solve_engine", None),
                   "_archetype_confidence",
                   0.0,
               )
               or 0.0
           )

           # --- Signature (retains B218 escalation semantics) ---
           signature = {
               "active_chunk_source": (solve_ctx.get("active_chunk") or {}).get("source"),
               "plateau_locked_family": solve_ctx.get("plateau_locked_family"),
               "archetype": solve_ctx.get("archetype"),
               "victory_condition_type": (
                   (solve_ctx.get("victory_condition") or {}).get("type")
                   if isinstance(solve_ctx.get("victory_condition"), dict)
                   else solve_ctx.get("victory_condition")
               ),
           }
           signature_repeated = self._last_replan_signature == signature
           self._last_replan_signature = signature

           # --- Decision tree (first match wins) ---
           if all_actions_low_value and geometry_high_conf:
               return SolvePhase.MODEL, "low_value_but_known_geometry"
           if signature_repeated:
               if hasattr(orchestrator, "_emit_trace_event"):
                   orchestrator._emit_trace_event(
                       "replan_escalation", "escalate", {"signature": signature}
                   )
               return SolvePhase.MODEL, "signature_escalation"
           if not exploration_complete:
               return SolvePhase.MODEL, "exploration_incomplete"
           if arch_conf < 0.3:
               return SolvePhase.HYPOTHESIZE, "low_archetype_conf"
           if coverage_saturated:
               return SolvePhase.ROUTE, "rebuild_route_from_saturation"
       except Exception:
           logger.exception("_replan_target evaluation failed; falling back to ROUTE")
       return SolvePhase.ROUTE, "default"
   ```

2. **Update the two call sites of `_replan_target`** in `agents/arc3/runner.py`.

   Locate both places where `replan_target = self._replan_target(orchestrator)` is assigned (the primary runner loop around line 900–930, and the variant-runner loop around line 1620–1655). In each, change:

   ```python
   replan_target = self._replan_target(orchestrator)
   ```

   to:

   ```python
   replan_target, replan_route_reason = self._replan_target(orchestrator)
   ```

   Then in the corresponding `_record_phase_transition` call (lines 927 and 1649), change:

   ```python
   metadata={"reason": "replan_exit", "target_phase": replan_target.value},
   ```

   to:

   ```python
   metadata={
       "reason": "replan_exit",
       "target_phase": replan_target.value,
       "route_reason": replan_route_reason,
   },
   ```

   If the variable `replan_route_reason` is referenced outside the local block later in the function, initialize it to `"default"` before the `if self._should_replan(...)` check so it is defined even if `_should_replan` returns False.

3. **Emit `route_reason` into the orchestrator trace event too** (so Phoenix and the `agent_execution_trace.json` writer both see it).

   Near each `_record_phase_transition(metadata={...route_reason})` call, if the nearby code also calls `orchestrator._emit_trace_event("replan_exit", ...)`, include `route_reason` in that payload. If no such direct call exists (the runner currently delegates trace emission through the `_record_phase_transition` path), no extra emission is required — the transition metadata already flows to the trace writer via the existing path.

4. **Add tests** in `tests/test_replan_branching.py` (see Tests section).

5. **Update documentation** in `ARCHITECTURE.md`.

   Locate the section that describes REPLAN as a first-class phase (search for `replan_exit` or `REPLAN` heading). Append a subsection titled `#### Route-reason taxonomy` listing the six values and their triggers:

   ```
   - low_value_but_known_geometry → all tested actions are low_value AND player/goal confidences ≥ 0.6 → resume at MODEL to reconsider archetype given the geometry
   - signature_escalation → identical REPLAN signature seen back-to-back → escalate to MODEL
   - exploration_incomplete → action_coverage.initial_exploration_complete is False → stay in MODEL to keep exploring
   - low_archetype_conf → archetype_confidence < 0.3 → drop to HYPOTHESIZE
   - rebuild_route_from_saturation → coverage saturated and geometry known → ROUTE (A010 has already graduated the chunker)
   - default → no evidence gate fired → ROUTE
   ```

## Concrete file additions/edits

- edit `agents/arc3/runner.py` — rewrite `_replan_target` (lines 1989-2028); update both `replan_target = self._replan_target(...)` call sites (around lines 895 and 1615, search for `_replan_target(` to locate); update the two `_record_phase_transition(... metadata={"reason": "replan_exit", ...})` calls at lines 927 and 1649
- edit `ARCHITECTURE.md` — add `#### Route-reason taxonomy` subsection under the REPLAN discussion
- add `tests/test_replan_branching.py` — six unit tests (see Tests section below)
- no changes to `agents/arc3/orchestrator.py` (A011's idempotency is already in place at orchestrator.py:2125-2152)

## API/interface changes

- **`_replan_target` return type**: `SolvePhase` → `tuple[SolvePhase, str]`. This is a private method of `ARCRunner`; no external callers exist (verified via `grep -rn "_replan_target" agents/`).
- **`replan_exit` trace metadata schema**: gains one string field, `route_reason`, drawn from the fixed vocabulary listed in the taxonomy subsection. Downstream consumers that index on the existing `reason` and `target_phase` fields are unaffected.
- No MCP seam changes, no SideQuests contract changes, no config schema changes.

## Tests to add or run

Create `tests/test_replan_branching.py` with the following six tests. Use the existing `ARCRunner` test fixtures if present; otherwise build a minimal orchestrator stub with the required attributes.

```python
import pytest
from agents.arc3.runner import ARCRunner, SolvePhase


class _StubSolveEngine:
    def __init__(self, arch_conf=0.8):
        self._archetype_confidence = arch_conf


class _StubOrchestrator:
    def __init__(self, *, solve_ctx=None, hyp_ctx=None, arch_conf=0.8):
        self._solve_context = solve_ctx or {}
        self._hypothesis_context = hyp_ctx or {}
        self.solve_engine = _StubSolveEngine(arch_conf=arch_conf)
    def _emit_trace_event(self, *args, **kwargs):
        pass


def _runner():
    r = ARCRunner.__new__(ARCRunner)
    r._last_replan_signature = None
    return r


def _high_conf_roles():
    return {"player": {"confidence": 0.9}, "goal": {"confidence": 0.9}}


def test_low_value_but_known_geometry_routes_to_model():
    r = _runner()
    orch = _StubOrchestrator(
        solve_ctx={"object_roles": _high_conf_roles()},
        hyp_ctx={
            "action_facts": [
                {"fact_type": "deterministic_effect", "value_status": "low_value"},
                {"fact_type": "deterministic_effect", "value_status": "low_value"},
            ],
            "action_coverage": {
                "tested_count": 2, "available_total": 2, "untested_count": 0,
                "initial_exploration_complete": True,
            },
        },
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.MODEL
    assert reason == "low_value_but_known_geometry"


def test_signature_repeated_escalates_to_model():
    r = _runner()
    orch = _StubOrchestrator(
        solve_ctx={"archetype": "maze", "active_chunk": {"source": "explore"}},
        hyp_ctx={"action_coverage": {"initial_exploration_complete": True,
                                     "tested_count": 1, "available_total": 4,
                                     "untested_count": 3}},
    )
    r._replan_target(orch)   # prime signature
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.MODEL
    assert reason == "signature_escalation"


def test_exploration_incomplete_routes_to_model():
    r = _runner()
    orch = _StubOrchestrator(
        hyp_ctx={"action_coverage": {"initial_exploration_complete": False,
                                     "tested_count": 0, "available_total": 4,
                                     "untested_count": 4}},
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.MODEL
    assert reason == "exploration_incomplete"


def test_low_archetype_conf_routes_to_hypothesize():
    r = _runner()
    orch = _StubOrchestrator(
        arch_conf=0.1,
        hyp_ctx={"action_coverage": {"initial_exploration_complete": True,
                                     "tested_count": 4, "available_total": 4,
                                     "untested_count": 0}},
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.HYPOTHESIZE
    assert reason == "low_archetype_conf"


def test_coverage_saturated_routes_to_route():
    r = _runner()
    orch = _StubOrchestrator(
        arch_conf=0.8,
        solve_ctx={"object_roles": _high_conf_roles()},
        hyp_ctx={
            "action_facts": [
                {"fact_type": "deterministic_effect", "value_status": "medium_value"},
            ],
            "action_coverage": {
                "tested_count": 4, "available_total": 4, "untested_count": 0,
                "initial_exploration_complete": True,
            },
        },
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.ROUTE
    assert reason == "rebuild_route_from_saturation"


def test_default_fallthrough_routes_to_route():
    r = _runner()
    orch = _StubOrchestrator(
        arch_conf=0.8,
        hyp_ctx={"action_coverage": {"initial_exploration_complete": True,
                                     "tested_count": 2, "available_total": 4,
                                     "untested_count": 2}},
    )
    phase, reason = r._replan_target(orch)
    assert phase == SolvePhase.ROUTE
    assert reason == "default"
```

Run:

- `pytest -q tests/test_replan_branching.py`
- `pytest -q tests/test_orchestrator_replan_loop.py` (regression — must still pass)
- `pytest -q -k replan` (broader regression sweep)

## Validation commands

- `pytest -q tests/test_replan_branching.py` — all six tests pass
- `grep -n "route_reason" agents/arc3/runner.py` — at least three hits (return sites + two metadata sites)
- Smoke verification:
  1. Run the standard one-puzzle smoke.
  2. Open the resulting `agent_execution_trace.json`.
  3. `jq '.[] | select(.event_type == "phase_transition" and .metadata.reason == "replan_exit") | .metadata.route_reason' agent_execution_trace.json` — every emitted row must be a non-null string from the taxonomy.

## Assumptions/defaults

- `hypothesis_context.action_facts[*].fact_type` and `value_status` vocabulary are already stable (verified: `orchestrator.py:5492` filters on `fact_type == "deterministic_effect"`, and `orchestrator.py:1840,1845,4526,4535` reference `value_status` strings `"low_value"` and `"ineffective"`).
- `action_coverage.tested_count`, `available_total`, `untested_count`, and `initial_exploration_complete` all exist on the `hypothesis_context` payload (verified: `orchestrator.py:3200-3209` already reads them).
- `solve_ctx.object_roles[<role>].confidence` is the canonical path for role confidences (matches the existing `PlanChunker` graduation gate at `solver.py`).
- Geometry threshold of 0.6 for `geometry_high_conf` matches the "directional plan is meaningful" bar used in `PlanChunker._graduation_assessment` — keeping them aligned prevents oscillation between the two gates.
- `ROUTE` remains the safe default when no evidence gate fires, preserving prior behavior for any puzzle whose evidence doesn't map cleanly onto the taxonomy.
- Orchestrator-side plan-registration idempotency is **not** in scope (already landed in A011 at `orchestrator.py:2125-2152` — do not modify).
