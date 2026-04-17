# B-201 — Durable Phase-State Machine: Implementation Plan

- **Card:** B201
- **Priority:** high
- **Dependencies:** None blocking
- **Ecosystem Layer(s):** Agent Orchestration & Control Plane, Agent Runtime & Harness

## Summary

Replace the ARC runner's ad-hoc `current_phase = "string"` assignments with a formal `SolvePhase` enum and `PhaseController` class that owns transitions, gate enforcement, and phase history. Wire this into `DurableARCRunner` (both `_run_puzzle` AND `_run_puzzle_with_brain`), `ARCOrchestrator`, and `SolveEngine`. Extend checkpoint persistence to include phase state. Update docs and tests.

## Code Audit Findings (ground truth)

Before defining the approach, these findings from auditing the actual codebase constrain the design:

### Two parallel runner methods
`DurableARCRunner` has TWO phase-stamping loops that must both be updated:
- `_run_puzzle()` (~L405–600) — primary loop, 6 phase writes + 8 phase reads
- `_run_puzzle_with_brain()` (~L783–950) — strategy-racing variant, 6 phase writes + 5 phase reads

### Actual per-step loop (both methods)
```
bootstrap: perceive() → plan()       ← runs ONCE per attempt
while steps < max_steps:
    hypothesize()                     ← runs EVERY step
    solve()                           ← runs EVERY step
    act()                             ← runs EVERY step
    execute_action → ingest_step()    ← runs EVERY step
    record_step_result()
    [check WIN/GAME_OVER/done → break or continue]
```
This means the per-step cycle is **HYPOTHESIZE → ROUTE → EXECUTE → EVALUATE → HYPOTHESIZE** (not → ROUTE as originally proposed). The transition table must reflect this.

### Phase consumers use getattr() with string defaults
13 read sites in runner.py use `getattr(brain_client, "current_phase", "bootstrap")` to pass phase to ledger/trace. These all write to `phase=` kwargs — they'll keep working as long as `brain.current_phase` stays a string (backward compat shim needed).

### LedgerBrainClient in adapter.py
`benchmarks/arc3/adapter.py` line 337: `self.current_phase: str = "unknown"` — this is the actual attribute the runner writes to. Not a runner-owned field.

### Checkpoint system does NOT persist phase
`agents/arc3/checkpoint.py` has `TaskCheckpoint` with `status`, `attempt`, `result` — no phase field. If the controller is "durable", it must survive crash recovery via checkpoints.

### Signal access is indirect
Signals are buried in context dicts, not direct properties:
- `initial_exploration_complete`: `context.get("action_coverage", {}).get("initial_exploration_complete")`
- `positions_known`: local float in `_graduation_assessment()`, not exposed
- `archetype_confidence`: `self._archetype_confidence` on SolveEngine, also `solve_ctx.get("archetype_confidence")`
- `victory_confidence`: nested as `_victory_condition.confidence`
- `zero_reward_streak`: **local variable only** — in supervisor.py and runner loop, not persisted anywhere
- `loop_detected`: `orchestrator._hypothesis_context.get("loop_detected")`
- `action_coverage`: dict from `hypothesis._summarize_action_coverage()`

### No "evaluate" phase string exists today
Current phases are: `bootstrap`, `hypothesize`, `solve`, `act`, `ingest`. The old plan incorrectly listed `"evaluate"` as a current phase — it doesn't exist in code.

### Write trace context is separate from brain.current_phase
Orchestrator maintains `_write_trace_context` (set via `set_write_trace_context()`) independently. Both need updating.

### Tests that assert on phase strings
- `tests/test_b111_ledger.py` L95–96: asserts `"bootstrap" in phases`
- `tests/test_b92_write_trace.py` L28, L49: asserts `== "bootstrap"` for write trace context
- `tests/test_arc3_durable_runner.py`: **no direct phase assertions** (only checkpoint/result assertions)

## Technical Approach

### Step 1: Create `agents/arc3/phase.py`

New module containing:

```python
from enum import Enum
import time
import logging
from typing import Callable

logger = logging.getLogger(__name__)


class SolvePhase(Enum):
    PERCEIVE = "perceive"       # Intake server info, initial observation
    MODEL = "model"             # World/map understanding, entity roles, topology
    HYPOTHESIZE = "hypothesize" # Infer game type, victory condition
    ROUTE = "route"             # Select strategy/chunk
    EXECUTE = "execute"         # Take action
    EVALUATE = "evaluate"       # Ingest result, score reward
    REPLAN = "replan"           # Detect stalls, escalate, loop back


class IllegalPhaseTransition(Exception):
    pass


class PhaseController:
    """Durable phase-state machine for ARC solving.
    
    Owns current phase, legal transitions, gate conditions, and history.
    Designed to be checkpointable and inspectable.
    """

    # Legal transition table — reflects actual per-step loop:
    # PERCEIVE → MODEL (once per attempt)
    # MODEL → HYPOTHESIZE (once, or after REPLAN→MODEL)
    # HYPOTHESIZE → ROUTE (every step)
    # ROUTE → EXECUTE (every step)
    # EXECUTE → EVALUATE (every step)
    # EVALUATE → HYPOTHESIZE (normal step continuation)
    # EVALUATE → REPLAN (stall detected)
    # REPLAN → MODEL | HYPOTHESIZE | ROUTE (escalation targets)
    TRANSITIONS: dict[SolvePhase, set[SolvePhase]] = {
        SolvePhase.PERCEIVE:    {SolvePhase.MODEL},
        SolvePhase.MODEL:       {SolvePhase.HYPOTHESIZE},
        SolvePhase.HYPOTHESIZE: {SolvePhase.ROUTE},
        SolvePhase.ROUTE:       {SolvePhase.EXECUTE},
        SolvePhase.EXECUTE:     {SolvePhase.EVALUATE},
        SolvePhase.EVALUATE:    {SolvePhase.HYPOTHESIZE, SolvePhase.REPLAN},
        SolvePhase.REPLAN:      {SolvePhase.MODEL, SolvePhase.HYPOTHESIZE, SolvePhase.ROUTE},
    }

    def __init__(self, initial: SolvePhase = SolvePhase.PERCEIVE):
        self._phase = initial
        self._history: list[tuple[SolvePhase, SolvePhase, float]] = []
        self._gates: dict[tuple[SolvePhase, SolvePhase], Callable[[], bool]] = {}

    @property
    def phase(self) -> SolvePhase:
        return self._phase

    @property
    def phase_name(self) -> str:
        """String value for backward compat with brain.current_phase consumers."""
        return self._phase.value

    def register_gate(self, from_phase: SolvePhase, to_phase: SolvePhase,
                      condition: Callable[[], bool]) -> None:
        self._gates[(from_phase, to_phase)] = condition

    def can_advance(self, to: SolvePhase) -> bool:
        if to not in self.TRANSITIONS.get(self._phase, set()):
            return False
        gate = self._gates.get((self._phase, to))
        return gate() if gate is not None else True

    def advance(self, to: SolvePhase, *, force: bool = False) -> SolvePhase:
        if to not in self.TRANSITIONS.get(self._phase, set()):
            raise IllegalPhaseTransition(
                f"Cannot transition {self._phase.value} -> {to.value}; "
                f"legal targets: {[t.value for t in self.TRANSITIONS.get(self._phase, set())]}"
            )
        gate = self._gates.get((self._phase, to))
        if gate is not None and not gate():
            if not force:
                raise IllegalPhaseTransition(
                    f"Gate not satisfied for {self._phase.value} -> {to.value}"
                )
            logger.warning("Force-advancing %s -> %s with unsatisfied gate",
                           self._phase.value, to.value)
        self._history.append((self._phase, to, time.time()))
        self._phase = to
        return self._phase

    def reset(self, to: SolvePhase = SolvePhase.PERCEIVE) -> None:
        self._phase = to
        self._history.clear()

    # ── Checkpoint support ──────────────────────────────────

    def to_checkpoint(self) -> dict:
        """Serialize for checkpoint persistence."""
        return {
            "phase": self._phase.value,
            "history": [
                {"from": f.value, "to": t.value, "ts": ts}
                for f, t, ts in self._history
            ],
        }

    @classmethod
    def from_checkpoint(cls, data: dict) -> "PhaseController":
        """Restore from checkpoint dict."""
        ctrl = cls(initial=SolvePhase(data["phase"]))
        ctrl._history = [
            (SolvePhase(h["from"]), SolvePhase(h["to"]), h["ts"])
            for h in data.get("history", [])
        ]
        return ctrl

    @property
    def history(self) -> list[dict]:
        return [
            {"from": f.value, "to": t.value, "timestamp": ts}
            for f, t, ts in self._history
        ]

    @property
    def step_count(self) -> int:
        """Number of full HYPOTHESIZE→...→EVALUATE cycles completed."""
        return sum(
            1 for _, to, _ in self._history
            if to == SolvePhase.EVALUATE
        )
```

### Step 2: Wire `PhaseController` into `DurableARCRunner`

**CRITICAL: Two methods must be updated (not one).**

In `agents/arc3/runner.py`:

- Import `SolvePhase`, `PhaseController`, `IllegalPhaseTransition`
- Instantiate `PhaseController` at the start of each game attempt
- **Backward compat shim**: After each `advance()`, also set `brain.current_phase = phase_ctrl.phase_name` so all 13 `getattr()` read sites and `LedgerBrainClient._record()` keep working without changes.

#### Mapping (old → new):

| Code site | Old string | New enum | Notes |
|---|---|---|---|
| Pre-perceive setup | `"bootstrap"` | `PERCEIVE` | PhaseController starts here |
| After `perceive()`, before `plan()` | (implicit bootstrap) | advance to `MODEL` | Split bootstrap in two |
| `orchestrator.hypothesize()` | `"hypothesize"` | `HYPOTHESIZE` | Every step |
| `orchestrator.solve()` | `"solve"` | `ROUTE` | Every step |
| `orchestrator.act()` | `"act"` | `EXECUTE` | Every step |
| `adapter.ingest_step()` | `"ingest"` | `EVALUATE` | Every step |
| (NEW) After ingest, before next iteration | — | REPLAN or → HYPOTHESIZE | New branching point |

#### REPLAN insertion point

After `record_step_result()` and before the next while-loop iteration, insert:

```python
# ── Phase: evaluate → replan or continue ──
if not done:
    if self._should_replan(orchestrator, consecutive_no_progress_steps):
        phase_ctrl.advance(SolvePhase.REPLAN, force=True)
        brain.current_phase = phase_ctrl.phase_name
        replan_target = self._replan_target(orchestrator)
        phase_ctrl.advance(replan_target)
        brain.current_phase = phase_ctrl.phase_name
    # Normal continuation: EVALUATE → HYPOTHESIZE happens at top of loop
```

#### Helper methods on DurableARCRunner:

```python
def _should_replan(self, orchestrator, no_progress_steps: int) -> bool:
    """Check if we should enter REPLAN instead of continuing normally."""
    hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}
    loop_detected = bool(hyp_ctx.get("loop_detected"))
    return loop_detected or no_progress_steps >= 3

def _replan_target(self, orchestrator) -> SolvePhase:
    """Decide where to loop back from REPLAN."""
    hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}
    coverage = hyp_ctx.get("action_coverage", {})
    exploration_complete = coverage.get("initial_exploration_complete", False)

    # If we haven't finished exploring the world, go back to MODEL
    if not exploration_complete:
        return SolvePhase.MODEL

    # If hypothesis confidence is low, re-hypothesize
    solve_ctx = getattr(orchestrator, "_last_solve_context", {}) or {}
    arch_conf = float(solve_ctx.get("archetype_confidence") or 0.0)
    if arch_conf < 0.3:
        return SolvePhase.HYPOTHESIZE

    # Otherwise just pick a new strategy
    return SolvePhase.ROUTE
```

#### Write trace context sync:

After each `advance()`, also call:
```python
if hasattr(orchestrator, "set_write_trace_context"):
    orchestrator.set_write_trace_context(phase_ctrl.phase_name)
```

#### Update `_run_puzzle_with_brain()` identically

Same changes applied to the second loop method. Consider extracting a shared `_step_phase_cycle()` helper to avoid duplication — but only if the two methods are similar enough. If not, apply changes in parallel to both.

### Step 3: Extend checkpoint to persist phase

In `agents/arc3/checkpoint.py`:

Add optional `phase_state: dict | None = None` to `TaskCheckpoint`:

```python
@dataclass
class TaskCheckpoint:
    task_id: str
    status: str
    plan_id: str | None
    result: dict | None
    attempt: int
    phase_state: dict | None = None   # ← NEW: PhaseController.to_checkpoint()
```

In runner, when creating checkpoints:
```python
checkpoint.phase_state = phase_ctrl.to_checkpoint()
```

On crash recovery:
```python
if checkpoint.phase_state:
    phase_ctrl = PhaseController.from_checkpoint(checkpoint.phase_state)
```

### Step 4: Expose signal accessors on SolveEngine

In `agents/arc3/solver.py`, add methods to `SolveEngine` that wrap the indirect signal lookups:

```python
def is_exploration_complete(self) -> bool:
    """Gate: has the agent tested all available actions?"""
    coverage = self._hypothesis_manager._summarize_action_coverage() \
        if hasattr(self, '_hypothesis_manager') else {}
    return bool(coverage.get("initial_exploration_complete", False))

def has_minimum_model(self) -> bool:
    """Gate: do we have basic world understanding (entities + positions)?"""
    ctx = self._build_solve_context() if hasattr(self, '_build_solve_context') else {}
    player = ctx.get("player_role") not in (None, "UNKNOWN")
    goal = ctx.get("goal_role") not in (None, "UNKNOWN")
    return player or goal  # at least one role identified

def has_hypothesis(self) -> bool:
    """Gate: is archetype confidence above minimum threshold?"""
    return self._archetype_confidence >= 0.3

def has_active_chunk(self) -> bool:
    """Gate: has solve() selected a chunk to execute?"""
    return getattr(self, '_active_chunk', None) is not None
```

**Important**: These methods must tolerate being called when internal state isn't fully initialized (early steps). All must default to `False` gracefully.

Register gates after engine creation:
```python
phase_ctrl.register_gate(SolvePhase.MODEL, SolvePhase.HYPOTHESIZE,
                         engine.is_exploration_complete)
phase_ctrl.register_gate(SolvePhase.HYPOTHESIZE, SolvePhase.ROUTE,
                         engine.has_hypothesis)
phase_ctrl.register_gate(SolvePhase.ROUTE, SolvePhase.EXECUTE,
                         engine.has_active_chunk)
```

**Step budget fallback**: Gates on MODEL→HYPOTHESIZE and HYPOTHESIZE→ROUTE must have timeouts. If the gate hasn't opened after N steps, force-advance:

```python
# In the step loop, before hypothesize():
if phase_ctrl.phase == SolvePhase.MODEL and total_steps >= MODEL_BUDGET:
    phase_ctrl.advance(SolvePhase.HYPOTHESIZE, force=True)
elif phase_ctrl.phase == SolvePhase.HYPOTHESIZE and total_steps >= HYPOTHESIS_BUDGET:
    phase_ctrl.advance(SolvePhase.ROUTE, force=True)
```

Constants: `MODEL_BUDGET = 4`, `HYPOTHESIS_BUDGET = 6` (tunable, follow-up card).

### Step 5: Update `ARCOrchestrator` to read phase

In `agents/arc3/orchestrator.py`:

- Accept optional `phase_controller: PhaseController | None = None` in `__init__`
- Use `self._phase_controller.phase` for prompt mode selection where currently inferred from string
- **No ownership of transitions** — orchestrator only reads, never calls `advance()`

### Step 6: Handle `finalization` phase

`finalization` is not a solve phase — it's post-loop cleanup (narrative writes, report generation). Keep it as a bare string set on brain.current_phase after the loop exits. Do NOT add it to `SolvePhase` enum.

Tool contracts that reference `finalization`:
- `report_outcome`: allowed in `evaluate, finalization` → keep `finalization` in the tool contract map as a string, alongside enum-based phases
- `get_task_graph`: allowed in `solve, evaluate, finalization` → same treatment

The tool gate checker must accept both `SolvePhase.value` strings and the literal `"finalization"`:

```python
# In tool gate check:
current = phase_ctrl.phase_name if phase_ctrl else brain.current_phase
if current not in tool_contract["allowed_phases"]:
    raise ToolPhaseViolation(...)
```

### Step 7: Update docs

#### `docs/arc-harness-rules.md`

Replace the phase list:
```
- perceive   (was: bootstrap first half)
- model      (was: bootstrap second half — NEW explicit phase)
- hypothesize
- route      (was: solve)
- execute    (was: act)
- evaluate   (was: ingest + evaluate)
- replan     (NEW — missing escalation path)
- finalization (post-solve cleanup, not a solve phase)
```

Update block-by-phase matrix with 7 columns.

Update all 13 tool `allowed_phases` entries per the mapping table below.

#### `docs/ecosystem-rules.md`

Update phase references in Control Plane section:
```
- phase transitions (perceive → model → hypothesize → route → execute → evaluate → replan/continue)
```

### Phase → allowed_phases mapping for tools

| Tool | Old allowed_phases | New allowed_phases |
|---|---|---|
| `branch_quest` | bootstrap | perceive |
| `notify_turn` | bootstrap, act, ingest, evaluate, finalization | perceive, model, execute, evaluate, replan, finalization |
| `current_truth` | bootstrap, act, ingest, solve | perceive, model, execute, route, evaluate |
| `register_plan` | bootstrap, solve | perceive, route |
| `report_outcome` | evaluate, finalization | evaluate, finalization |
| `recall_plans` | bootstrap, solve | perceive, route, replan |
| `recall_lessons` | bootstrap, solve | perceive, route, replan |
| `analogical_search` | bootstrap, solve | perceive, route, hypothesize |
| `register_task_graph` | bootstrap, solve | perceive, route |
| `get_ready_tasks` | solve | route |
| `advance_task` | solve | route, execute |
| `fail_task` | solve | route, evaluate, replan |
| `get_task_graph` | solve, evaluate, finalization | route, evaluate, replan, finalization |

### Step 8: Create tests

#### `tests/test_phase_controller.py` (~250 lines, 20+ test cases):

**Enum tests:**
1. `SolvePhase` has exactly 7 members
2. All values are lowercase strings
3. `SolvePhase("perceive")` roundtrips correctly

**Transition table tests:**
4. Legal forward: PERCEIVE → MODEL succeeds
5. Every legal transition in TRANSITIONS succeeds
6. Illegal transition PERCEIVE → EXECUTE raises `IllegalPhaseTransition`
7. Illegal transition ROUTE → MODEL raises `IllegalPhaseTransition`
8. Error message includes legal targets

**Gate tests:**
9. Gate returning False blocks transition
10. Gate returning True allows transition
11. No gate registered = transition allowed (open by default)
12. `force=True` bypasses failed gate (logs warning)
13. `can_advance()` returns False for blocked gate
14. `can_advance()` returns True for open gate

**History tests:**
15. History is empty initially
16. History records (from, to, timestamp) on advance
17. History accumulates across multiple transitions
18. Reset clears history

**Checkpoint tests:**
19. `to_checkpoint()` serializes phase + history
20. `from_checkpoint()` restores exact state
21. Roundtrip: `from_checkpoint(ctrl.to_checkpoint())` matches original

**Integration cycle tests:**
22. Full happy-path: PERCEIVE → MODEL → HYPOTHESIZE → ROUTE → EXECUTE → EVALUATE → HYPOTHESIZE (loop)
23. Replan cycle: ... → EVALUATE → REPLAN → MODEL → HYPOTHESIZE → ROUTE → ...
24. REPLAN → HYPOTHESIZE branching
25. REPLAN → ROUTE branching
26. `step_count` property counts EVALUATE arrivals

**Edge cases:**
27. Double advance to same phase raises (no self-loops)
28. Advance from EVALUATE to HYPOTHESIZE (normal continuation, not just ROUTE)

#### Update existing tests:
- `tests/test_b111_ledger.py`: Change `"bootstrap"` assertion to `"perceive"`
- `tests/test_b92_write_trace.py`: Change `"bootstrap"` assertions to `"perceive"`
- `tests/test_arc3_durable_runner.py`: No phase assertions to update (confirmed by audit)

## Concrete File Changes

| File | Action | Approx lines |
|---|---|---|
| `agents/arc3/phase.py` | **Create** | ~150 lines |
| `agents/arc3/runner.py` | **Modify** — both `_run_puzzle` + `_run_puzzle_with_brain`, add REPLAN, add helpers, update tool map | ~80 lines changed |
| `agents/arc3/checkpoint.py` | **Modify** — add `phase_state` field to `TaskCheckpoint` | ~5 lines |
| `agents/arc3/orchestrator.py` | **Modify** — accept + read PhaseController | ~15 lines |
| `agents/arc3/solver.py` | **Modify** — add 4 signal accessor methods | ~40 lines |
| `benchmarks/arc3/adapter.py` | **No change** — backward compat via `brain.current_phase = phase_ctrl.phase_name` shim |  |
| `docs/arc-harness-rules.md` | **Modify** — phase list, block-by-phase matrix, tool contracts | ~60 lines |
| `docs/ecosystem-rules.md` | **Modify** — phase references | ~10 lines |
| `tests/test_phase_controller.py` | **Create** | ~280 lines, 28 tests |
| `tests/test_b111_ledger.py` | **Modify** — phase string assertions | ~3 lines |
| `tests/test_b92_write_trace.py` | **Modify** — phase string assertions | ~3 lines |

## API / Schema / Test Updates

- No KuzuDB schema changes (phases are runtime state)
- No MCP tool contract signature changes (only `allowed_phases` values change)
- No new tools introduced
- Checkpoint schema extended with optional `phase_state` field (backward compatible — existing checkpoints without it still load)

## Acceptance Criteria

1. `SolvePhase` enum with 7 members exists in `agents/arc3/phase.py`
2. `PhaseController` enforces transitions with `IllegalPhaseTransition` on violations
3. Gates block transitions until conditions are met (or force-advance with logging)
4. **Both** `_run_puzzle` and `_run_puzzle_with_brain` use `PhaseController` — no bare string phase assignments
5. `brain.current_phase` backward compat shim keeps all 13 getattr read sites + LedgerBrainClient working
6. Phase transitions are checkpointable (`to_checkpoint` / `from_checkpoint`)
7. Phase history appears in write traces and orchestration report
8. REPLAN phase fires when `loop_detected` or `no_progress_steps >= 3`
9. REPLAN routes to MODEL, HYPOTHESIZE, or ROUTE based on signal state
10. Step budget forces advance from MODEL and HYPOTHESIZE if gates don't open
11. Live smoke test passes: `run_single_puzzle.py --live-smoke --num-puzzles 1`
12. `pytest tests/test_phase_controller.py` — ≥25 tests passing
13. `pytest tests/test_b111_ledger.py tests/test_b92_write_trace.py` — updated and passing
14. `pytest tests/test_arc3_durable_runner.py` — existing tests still passing

## Validation Commands

```bash
# Unit tests for phase controller
.venv/bin/python -m pytest tests/test_phase_controller.py -v

# Updated ledger + write trace tests
.venv/bin/python -m pytest tests/test_b111_ledger.py tests/test_b92_write_trace.py -v

# Existing runner tests (should pass without phase assertion changes)
.venv/bin/python -m pytest tests/test_arc3_durable_runner.py -v

# Live smoke test
.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1
```

## Risks and Constraints

1. **Two runner methods**: Both `_run_puzzle` and `_run_puzzle_with_brain` must be updated atomically. Missing one causes the strategy-racing path to use old bare strings while the primary path uses the controller.
2. **Tool allowed_phases**: Changing phase names means every tool contract must be updated in runner.py AND `arc-harness-rules.md`. If any are missed, tools will be blocked in phases where they should be allowed. **Mitigation**: grep for old phase strings after changes, assert zero matches.
3. **Signal accessor robustness**: Gate functions will be called on every advance attempt. They must be cheap (no LLM calls) and must tolerate uninitialized state (early steps before solve engine has run). All must default to `False` on error.
4. **Backward compatibility**: `brain.current_phase` must remain a string. The shim `brain.current_phase = phase_ctrl.phase_name` satisfies this. Any code comparing `current_phase == "bootstrap"` will break — grep and fix.
5. **Step budget tuning**: `MODEL_BUDGET = 4` and `HYPOTHESIS_BUDGET = 6` are initial guesses. Follow-up card should tune via benchmark analysis.
6. **REPLAN heuristics**: The initial `_should_replan` / `_replan_target` logic is simple. Observing actual failure modes will guide refinement.
7. **Finalization is not a solve phase**: `finalization` stays as bare string for post-loop cleanup. Tool contracts must accept both enum-based phases and the `"finalization"` string.

## Implementation Order

Recommended sequence to minimize breakage:

1. Create `agents/arc3/phase.py` (pure new code, no dependencies)
2. Create `tests/test_phase_controller.py` (validates phase module in isolation)
3. Add signal accessors to `solver.py` (additive, no behavior change)
4. Extend `checkpoint.py` (backward compatible optional field)
5. Wire PhaseController into `_run_puzzle()` (main loop)
6. Wire PhaseController into `_run_puzzle_with_brain()` (variant loop)
7. Update orchestrator to read phase
8. Update tool allowed_phases map in runner.py
9. Update tests (`test_b111_ledger.py`, `test_b92_write_trace.py`)
10. Update docs (`arc-harness-rules.md`, `ecosystem-rules.md`)
11. Run full validation suite
