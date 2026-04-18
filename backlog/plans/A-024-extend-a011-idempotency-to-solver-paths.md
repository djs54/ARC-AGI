# A-024 - Extend A011 Plan-Registration Idempotency to Solver-Side Register Paths

## Card metadata

- Card: A024
- Priority: P0
- Layer: ARC runtime
- Depends on: A011

## Summary

A011 added a fingerprint idempotency check to the orchestrator-side `register_plan` at `agents/arc3/orchestrator.py:2137-2164`. That check is correct but only covers one of three register sites. The Apr 18 16:59 smoke emitted **7 distinct `plan_id`s across 10 steps**, all originating from the two solver-side register paths (`_register_chunk_plan`, `_register_solve_plan`) that use a narrower `_plan_changed(plan_type, goal, steps)` check at `agents/arc3/solver.py:3371-3401`. This card extends the solver-side fingerprint to match A011's semantics so cosmetic rewordings of the same plan dedupe.

Naming note: the card refers to "`_register_top_plan`"; the actual function is **`_register_solve_plan`** (verified at `agents/arc3/solver.py:3731`). Use the real name everywhere below.

## Implementation approach

### 1. Rewrite `_plan_changed` to accept extra fingerprint fields

Current signature at `agents/arc3/solver.py:3371-3401`:

```python
def _plan_changed(
    self,
    plan_type: str,  # "top" | "chunk"
    goal: str,
    steps: List[str],
    force: bool = False,
) -> bool:
```

Replace with a fingerprint-based version. The fingerprint tuple mirrors A011's orchestrator schema (`orchestrator.py:2144-2150`): `(plan_type, goal, tuple(steps), archetype_str, vc_type_str, chunk_desc_or_None)`.

```python
def _plan_fingerprint(
    self,
    plan_type: str,
    goal: str,
    steps: List[str],
    chunk_desc: Optional[str] = None,
) -> tuple:
    """A024: Build the idempotency fingerprint for a register_plan call.

    Mirrors the orchestrator-side schema at orchestrator.py:2144-2150 so
    both sides dedupe with identical semantics.
    """
    archetype_str = str(getattr(self._archetype, "value", self._archetype or "unknown"))
    vc = self._victory_condition
    if vc is not None:
        vc_type_str = str(getattr(vc.condition_type, "value", vc.condition_type))
    else:
        vc_type_str = "unknown"
    return (
        plan_type,
        goal,
        tuple(steps or []),
        archetype_str,
        vc_type_str,
        chunk_desc if plan_type == "chunk" else None,
    )

def _plan_changed(
    self,
    plan_type: str,  # "top" | "chunk"
    goal: str,
    steps: List[str],
    chunk_desc: Optional[str] = None,
    force: bool = False,
) -> bool:
    """B137 + A024: Returns True if the plan should be re-registered.

    Args:
        plan_type: "top" for solve plan, "chunk" for chunk plan
        goal: Plan goal/description
        steps: List of action steps
        chunk_desc: Chunk description (only used when plan_type == "chunk")
        force: If True, always return True (e.g., on dissonance reset)
    """
    if force:
        return True

    fingerprint = self._plan_fingerprint(plan_type, goal, steps, chunk_desc)
    last_fp = (
        self._last_registered_top_fingerprint if plan_type == "top"
        else self._last_registered_chunk_fingerprint
    )
    return last_fp != fingerprint
```

The legacy dict attributes `_last_registered_top_plan` and `_last_registered_chunk_plan` can be deleted in favor of the new `_fingerprint` fields plus a cached plan_id. Leaving the old dicts in place is also acceptable (no harm), but the fingerprint fields below become the source of truth.

### 2. Add fingerprint and plan-id cache fields

In `__init__` at `agents/arc3/solver.py:1838-1842`:

```python
self._solve_plan_id: Optional[str] = None
...
self._last_registered_top_plan: Optional[Dict[str, Any]] = None    # legacy, kept for backcompat
self._last_registered_chunk_plan: Optional[Dict[str, Any]] = None  # legacy, kept for backcompat
# A024: fingerprint-based idempotency cache (mirrors orchestrator A011 semantics)
self._last_registered_top_fingerprint: Optional[tuple] = None
self._last_registered_chunk_fingerprint: Optional[tuple] = None
self._last_registered_chunk_plan_id: Optional[str] = None
```

In `reset_for_retry` at `solver.py:3520-3536`, reset all three new fields alongside the existing ones:

```python
self._last_registered_top_plan = None
self._last_registered_chunk_plan = None
# A024
self._last_registered_top_fingerprint = None
self._last_registered_chunk_fingerprint = None
self._last_registered_chunk_plan_id = None
```

### 3. Convert `_register_chunk_plan` to the new fingerprint and reuse `plan_id` on dedup

Existing site at `agents/arc3/solver.py:3403-3455`. Key change: pass `chunk_desc=chunk.description` to `_plan_changed`, and on a dedup hit, reuse `self._last_registered_chunk_plan_id` by setting `chunk.plan_id` directly and emitting a `plan_registration_dedup_hit` trace event before returning.

```python
async def _register_chunk_plan(self, chunk: PlanChunk, step: int = 0) -> None:
    """B109: Register an active chunk as a plan in SideQuests.

    B137 + A024: Suppresses re-registration of identical chunk plans via
    fingerprint-based idempotency (plan_type, goal, steps, archetype, vc_type, chunk_desc).
    """
    steps_list = chunk.estimated_actions or ["Execute strategy toward goal"]
    fingerprint = self._plan_fingerprint(
        plan_type="chunk",
        goal=chunk.description,
        steps=steps_list,
        chunk_desc=chunk.description,
    )

    if not self._plan_changed(
        plan_type="chunk",
        goal=chunk.description,
        steps=steps_list,
        chunk_desc=chunk.description,
    ):
        logger.debug("Skipping chunk plan registration (identical fingerprint): %s", chunk.description)
        # A024: reuse prior plan_id so downstream code still has a valid id
        if self._last_registered_chunk_plan_id:
            chunk.plan_id = self._last_registered_chunk_plan_id
        # A024 dedup hit trace event
        self._trace(
            "operation",
            "plan_registration_dedup_hit",
            {"plan_type": "chunk", "fingerprint": list(fingerprint)},
            {"reused_plan_id": self._last_registered_chunk_plan_id},
            0.0,
        )
        # preserve existing audit-trail trace event
        try:
            await self.brain.trace_event(
                event_type="plan_registration_skipped",
                metadata={
                    "plan_type": "chunk",
                    "reason": "identical_fingerprint",
                    "chunk_description": chunk.description,
                },
            )
        except Exception:
            pass
        return

    try:
        self._trace("solve_register_plan", "register_plan", {
            "step": step,
            "plan_type": "chunk",
            "goal": chunk.description,
            "steps_count": len(steps_list),
        })
        _t0 = time.perf_counter()
        plan_payload = await self.brain.register_plan(
            goal=chunk.description,
            steps=steps_list,
            session_id=self.session_id,
        )
        _elapsed = (time.perf_counter() - _t0) * 1000
        chunk.plan_id = plan_payload.get("plan_id")
        # A024: cache fingerprint + plan_id for reuse
        self._last_registered_chunk_fingerprint = fingerprint
        self._last_registered_chunk_plan_id = chunk.plan_id
        # Legacy dict cache (kept to minimize diff surface)
        self._last_registered_chunk_plan = {
            "goal": chunk.description,
            "steps": steps_list,
        }
        self._trace("solve_register_plan_done", "register_plan", {"step": step, "plan_type": "chunk"}, {"plan_id": chunk.plan_id}, _elapsed)
        logger.info("Chunk plan registered: %s (%s)", chunk.plan_id, chunk.description)
    except Exception as exc:
        logger.warning("register_chunk_plan failed: %s", exc)
```

### 4. Convert `_register_solve_plan` to the new fingerprint and reuse `plan_id` on dedup

Existing site at `agents/arc3/solver.py:3731-3789`. Apply the same pattern; there is no chunk_desc for the top-plan path (it is `None` by construction, which matches A011's orchestrator fingerprint slot for non-chunk payloads).

```python
async def _register_solve_plan(self, observation: Dict[str, Any], step: int = 0) -> None:
    """Register top-level solve plan.

    B137 + A024: Suppresses re-registration of identical plans via fingerprint check.
    """
    goal = f"Solve ARC task {observation.get('dataset_id', '')}:{observation.get('task_id', '')}"
    steps = [
        "Infer archetype from board dynamics",
        "Map object roles from transition evidence",
        "Hypothesize victory condition",
        "Execute and revise chunked solve path",
    ]
    fingerprint = self._plan_fingerprint(
        plan_type="top",
        goal=goal,
        steps=steps,
    )

    if not self._plan_changed(plan_type="top", goal=goal, steps=steps):
        logger.debug("Skipping solve plan registration (identical fingerprint)")
        # A024 dedup hit trace event
        self._trace(
            "operation",
            "plan_registration_dedup_hit",
            {"plan_type": "top", "fingerprint": list(fingerprint)},
            {"reused_plan_id": self._solve_plan_id},
            0.0,
        )
        try:
            await self.brain.trace_event(
                event_type="plan_registration_skipped",
                metadata={
                    "plan_type": "top",
                    "reason": "identical_fingerprint",
                    "goal": goal,
                },
            )
        except Exception:
            pass
        return

    try:
        self._trace("solve_register_plan", "register_plan", {
            "step": step,
            "plan_type": "top",
            "goal": goal,
            "steps_count": len(steps),
        })
        _t0 = time.perf_counter()
        plan_payload = await self.brain.register_plan(
            goal=goal,
            steps=steps,
            session_id=self.session_id,
        )
        _elapsed = (time.perf_counter() - _t0) * 1000
        self._solve_plan_id = plan_payload.get("plan_id")
        # A024: cache fingerprint
        self._last_registered_top_fingerprint = fingerprint
        # Legacy dict cache (kept to minimize diff surface)
        self._last_registered_top_plan = {
            "goal": goal,
            "steps": steps,
        }
        self._trace("solve_register_plan_done", "register_plan", {"step": step, "plan_type": "top"}, {"plan_id": self._solve_plan_id}, _elapsed)
        logger.info("Solve plan registered: %s", self._solve_plan_id)
    except Exception as exc:
        logger.warning("register_plan failed for solve plan: %s", exc)
```

### 5. Tests

Create or extend `tests/test_plan_registration_idempotent.py` with three new tests. The test helper builds a solver with a stub `brain.register_plan` that records every invocation. The archetype and victory condition are set directly on the solver instance to drive the fingerprint.

```python
import asyncio
import pytest
from agents.arc3.solver import (
    ARC3Solver,
    PlanChunk,
    GameArchetype,
    VictoryCondition,
    VictoryType,
)


class _StubBrain:
    def __init__(self):
        self.register_plan_calls = []

    async def register_plan(self, *, goal, steps, session_id):
        self.register_plan_calls.append({
            "goal": goal, "steps": list(steps), "session_id": session_id,
        })
        return {"plan_id": f"plan-{len(self.register_plan_calls)}"}

    async def trace_event(self, **kwargs):
        return None


def _make_solver():
    solver = ARC3Solver.__new__(ARC3Solver)
    solver.brain = _StubBrain()
    solver.session_id = "test-session"
    solver._archetype = GameArchetype.RACE
    solver._victory_condition = VictoryCondition(
        condition_type=VictoryType.REACH_GOAL,
        description="reach the goal",
        confidence=0.8,
    )
    solver._last_registered_top_plan = None
    solver._last_registered_chunk_plan = None
    solver._last_registered_top_fingerprint = None
    solver._last_registered_chunk_fingerprint = None
    solver._last_registered_chunk_plan_id = None
    solver._solve_plan_id = None
    solver._emit_trace = None
    return solver


def test_chunk_dedup_ignores_cosmetic_description_change():
    """Two chunks that share archetype, vc, and steps but differ only in
    chunk-description wording must produce exactly ONE register_plan call."""
    solver = _make_solver()
    chunk_a = PlanChunk(
        description="Plateau Exploitation: commit to ACTION2",
        estimated_actions=["ACTION2", "ACTION2", "ACTION2"],
    )
    chunk_b = PlanChunk(
        description="Plateau Exploitation: commit to ACTION2 (step 5)",
        estimated_actions=["ACTION2", "ACTION2", "ACTION2"],
    )
    asyncio.run(solver._register_chunk_plan(chunk_a, step=0))
    asyncio.run(solver._register_chunk_plan(chunk_b, step=1))
    # XXX: Although the chunk_desc field differs, the remaining fingerprint
    # slots (plan_type, goal=description, steps, archetype, vc_type) include
    # the full description as `goal`. If the _plan_fingerprint schema uses
    # description as both `goal` AND `chunk_desc`, the two descriptions will
    # differ at the `goal` slot too, so this test would see TWO calls.
    #
    # DECISION: The chunk goal slot must be STABLE across cosmetic rewordings.
    # Implementation must either (a) normalize chunk description when used as
    # `goal` (strip trailing "(step N)" parenthetical), or (b) use a stable
    # source like the plateau family / archetype prefix as the goal.
    # Recommended: prior to fingerprint computation, normalize via
    #     goal_norm = re.sub(r"\s*\(step\s+\d+\)\s*$", "", chunk.description).strip()
    # and pass goal_norm as both goal and chunk_desc in _register_chunk_plan.
    assert len(solver.brain.register_plan_calls) == 1
    assert chunk_a.plan_id == chunk_b.plan_id


def test_chunk_dedup_respects_archetype_change():
    """Two chunks identical except for archetype must produce TWO register_plan calls."""
    solver = _make_solver()
    chunk = PlanChunk(
        description="Explore",
        estimated_actions=["ACTION1"],
    )
    asyncio.run(solver._register_chunk_plan(chunk, step=0))
    solver._archetype = GameArchetype.CHASE  # archetype flip
    asyncio.run(solver._register_chunk_plan(chunk, step=1))
    assert len(solver.brain.register_plan_calls) == 2


def test_chunk_dedup_respects_step_list_change():
    """Two chunks identical except for estimated_actions must produce TWO calls."""
    solver = _make_solver()
    chunk_a = PlanChunk(description="Explore", estimated_actions=["ACTION1"])
    chunk_b = PlanChunk(description="Explore", estimated_actions=["ACTION2"])
    asyncio.run(solver._register_chunk_plan(chunk_a, step=0))
    asyncio.run(solver._register_chunk_plan(chunk_b, step=1))
    assert len(solver.brain.register_plan_calls) == 2
```

The first test reveals a schema-design decision that must be made in implementation: to dedupe "Plateau Exploitation: commit to ACTION2" vs "Plateau Exploitation: commit to ACTION2 (step 5)", the `goal` slot of the fingerprint must be stable across cosmetic rewordings. Two options:

1. **Normalize inside `_register_chunk_plan`** — before computing the fingerprint, strip trailing `"(step N)"` parentheticals:
   ```python
   import re
   goal_norm = re.sub(r"\s*\(step\s+\d+\)\s*$", "", chunk.description).strip()
   # then pass goal_norm as BOTH the fingerprint `goal` AND `chunk_desc`
   ```
2. **Add a `chunk_family` attribute to `PlanChunk`** and use that as the fingerprint `goal`.

Option 1 is the minimal diff and mirrors what A011 does on the orchestrator side (where `ch_desc = str(active_chunk.get("description", "fallback"))` is the raw string — if the orchestrator also exhibits the trailing-parenthetical problem, apply the same normalization there in a follow-up). Recommended: implement option 1, add a `# A024: normalize trailing step annotations` comment, and record the decision in `ARCHITECTURE.md`.

### 6. Documentation

In `ARCHITECTURE.md`, under the REPLAN section that references A011, append:

```
A011 covers only the orchestrator-side `register_plan`. The solver has two
additional register paths (`_register_chunk_plan`, `_register_solve_plan`)
which A024 extends with the same fingerprint semantics:
`(plan_type, goal, tuple(steps), archetype, vc_type, chunk_desc_or_None)`.
Chunk descriptions are normalized (trailing "(step N)" parentheticals are
stripped) before entering the fingerprint so that cosmetic step-ordinal
rewording does not defeat dedup.
```

## Concrete file additions/edits

- edit `agents/arc3/solver.py`:
  - in `__init__` (around line 1838-1842), add `_last_registered_top_fingerprint`, `_last_registered_chunk_fingerprint`, `_last_registered_chunk_plan_id`
  - in `reset_for_retry` (around line 3520-3536), reset all three new fields
  - add a new helper `_plan_fingerprint` adjacent to `_plan_changed` (line 3371-3401)
  - rewrite `_plan_changed` to accept `chunk_desc` kwarg and compare fingerprints
  - rewrite `_register_chunk_plan` (line 3403-3455) to use the new fingerprint, normalize the chunk description for the `goal` slot, reuse `_last_registered_chunk_plan_id` on dedup, and emit `plan_registration_dedup_hit`
  - rewrite `_register_solve_plan` (line 3731-3789) to use the new fingerprint and emit `plan_registration_dedup_hit` on dedup
- extend `tests/test_plan_registration_idempotent.py` (or create if missing) with the three tests above
- edit `ARCHITECTURE.md` — add the paragraph describing the solver-side dedup contract

## API/interface changes

- `_plan_changed` gains an optional `chunk_desc: Optional[str] = None` kwarg (default None). Existing callers continue to work.
- New helper `_plan_fingerprint(plan_type, goal, steps, chunk_desc=None)` returning a tuple. Internal-only.
- New solver attributes: `_last_registered_top_fingerprint`, `_last_registered_chunk_fingerprint`, `_last_registered_chunk_plan_id`. Test-visible, treat as read-only externally.
- New trace event type: `plan_registration_dedup_hit` with metadata `{plan_type, fingerprint}` and result `{reused_plan_id}`. Not consumed by production code; the trace writer accepts arbitrary event names.
- No MCP seam changes, no config changes.

## Tests to add or run

- `pytest -q tests/test_plan_registration_idempotent.py`
- Regression: `pytest -q tests/test_solver*.py`
- Regression: `pytest -q -k "register_plan or plan_idempoten"`

## Validation commands

- `pytest -q tests/test_plan_registration_idempotent.py`
- Re-run the Apr 18 16:59 smoke puzzle:
  1. `python run_single_puzzle.py ...` with the same config
  2. Count distinct `plan_id`s emitted across the run:
     ```sh
     jq -r '.[] | select(.operation == "register_plan" and .result.plan_id != null) | .result.plan_id' agent_execution_trace.json | sort -u | wc -l
     ```
     Must drop from 7 to **≤ 3** on the 16:59 puzzle.
  3. Confirm dedup events fire:
     ```sh
     jq '[.[] | select(.operation == "plan_registration_dedup_hit")] | length' agent_execution_trace.json
     ```
     Must be ≥ 1 on any run that previously churned.

## Assumptions/defaults

- The orchestrator-side A011 fingerprint at `orchestrator.py:2144-2150` is the authoritative schema. This card mirrors it exactly: `(plan_type, goal, tuple(steps), archetype_str, vc_type_str, chunk_desc_if_chunk)`. If A011 later adds a field (e.g., role map hash), both sides must be updated together.
- `PlanChunk.description` carries cosmetic per-step variance (observed in the Apr 18 16:59 smoke as the `"(step N)"` suffix). The normalization regex handles only the trailing `"(step N)"` case because that is the pattern observed in production. If other cosmetic variance emerges (e.g., timestamps, step counts embedded mid-string), extend the normalizer in a follow-up — don't try to pre-emptively handle cases that haven't been seen.
- Reuse of `_last_registered_chunk_plan_id` on dedup is safe because the chunk.plan_id is consumed downstream purely as a SideQuests handle; reusing a prior id means the same underlying plan resource is referenced by multiple chunk objects, which is the intended semantics.
- `reset_for_retry` is the correct reset boundary. It is called on dissonance reset and retry paths; within a single puzzle attempt, the fingerprint cache must persist across chunk transitions so that the dedup actually fires. Verified by inspecting `reset_for_retry` at `solver.py:3520`.
- The `plan_registration_dedup_hit` event is emitted via `self._trace(...)`, not `self.brain.trace_event(...)`. Rationale: `self._trace` routes to the in-process trace writer (captured in `agent_execution_trace.json`) which is the primary diagnostic source per A022. The existing `brain.trace_event("plan_registration_skipped", ...)` call is preserved for MCP-side audit continuity.
- Leaving the legacy `_last_registered_top_plan` / `_last_registered_chunk_plan` dict attributes in place is deliberate. They no longer drive the dedup decision (fingerprints do), but removing them would expand the diff beyond the smallest-possible change, and any downstream inspection tooling that reads them gracefully sees the last registered payload. A follow-up card may remove them after a run proves nothing else reads them.
