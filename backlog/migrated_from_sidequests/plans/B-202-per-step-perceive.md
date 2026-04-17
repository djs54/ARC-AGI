# B-202 — Per-Step PERCEIVE Phase: Implementation Plan

- **Card:** backlog/B202.md
- **Priority:** P1
- **Dependencies:** B201 complete (phase.py, PhaseController, runner REPLAN wiring all exist)

## Summary

Wire PERCEIVE as a per-step phase that runs after EVALUATE on every action cycle. The agent currently acts and evaluates but never formally observes what changed. This plan adds that observation loop with a lightweight method that interprets the server response (grid delta, state, reward, colors, available actions) without LLM calls.

## Technical Approach

### Phase Transition Changes

**File:** `agents/arc3/phase.py`, line 38

Two-line change to `TRANSITIONS` dict:

```python
# Before:
SolvePhase.PERCEIVE:    {SolvePhase.MODEL},
SolvePhase.EVALUATE:    {SolvePhase.HYPOTHESIZE, SolvePhase.REPLAN},

# After:
SolvePhase.PERCEIVE:    {SolvePhase.MODEL, SolvePhase.HYPOTHESIZE},
SolvePhase.EVALUATE:    {SolvePhase.PERCEIVE, SolvePhase.REPLAN},
```

Rationale:
- `PERCEIVE → HYPOTHESIZE`: the per-step return path into the main cycle
- `EVALUATE → PERCEIVE`: normal continuation (replaces direct EVALUATE → HYPOTHESIZE)
- `EVALUATE → REPLAN`: stall escalation still bypasses perceive

### New orchestrator method

**File:** `agents/arc3/orchestrator.py`, insert after `perceive()` ends (~line 1318)

```python
async def perceive_step_response(
    self,
    observation: ARC3Observation,
    step: int,
    reward: float,
    done: bool,
    action_id: str | None = None,
) -> dict:
    """Per-step perception: inspect server response after EVALUATE.

    Lightweight — no LLM calls, no entity discovery. Reuses FrameDelta
    already computed by record_step_result().
    """
    self._emit_trace_event(
        "phase_start", "perceive_step",
        {"step": step, "action_id": action_id, "reward": reward, "done": done},
    )

    # Read delta already computed by record_step_result()
    delta_summary: dict = {}
    if self._frame_deltas:
        delta = self._frame_deltas[-1]
        delta_summary = {
            "apparent_effect": getattr(delta, "apparent_effect", None),
            "n_cells_changed": getattr(delta, "n_cells_changed", 0),
            "direction": getattr(delta, "direction", None),
            "new_colors": getattr(delta, "new_colors_introduced", []),
            "removed_colors": getattr(delta, "colors_removed", []),
        }

    state = observation.get("state", "NOT_FINISHED")
    available_actions = observation.get("available_actions", [])
    current_colors = observation.get("colors", [])
    color_set = sorted({c["value"] for c in current_colors} if current_colors else set())

    response_summary = (
        f"[STEP RESPONSE] Step {step}, action={action_id}. "
        f"State: {state}. Reward: {reward}. Done: {done}. "
        f"Grid: {delta_summary.get('n_cells_changed', 0)} cells changed"
        f"{', ' + str(delta_summary['apparent_effect']) if delta_summary.get('apparent_effect') else ''}."
        f"{' Direction: ' + str(delta_summary['direction']) + '.' if delta_summary.get('direction') else ''} "
        f"New colors: {delta_summary.get('new_colors', [])}. "
        f"Removed colors: {delta_summary.get('removed_colors', [])}. "
        f"Available actions: {', '.join(available_actions) if available_actions else 'pending'}."
    )

    notify_response = await self.brain.notify_turn(
        role="user", content=response_summary, session_id=self.session_id
    )
    self._record_write_event(
        kind="notify_turn",
        summary=response_summary,
        detail={"role": "user", "scope": "step_response_perception"},
        response_dict=notify_response,
    )

    perception = {
        "step": step,
        "state": state,
        "reward": reward,
        "done": done,
        "delta": delta_summary,
        "available_actions": available_actions,
        "active_colors": color_set,
    }
    self._last_response_perception = perception

    self._emit_trace_event(
        "phase_end", "perceive_step",
        {"step": step},
        perception,
    )
    return perception
```

Also add `self._last_response_perception: dict = {}` to `__init__` alongside other tracking fields.

### Runner main loop changes

**File:** `agents/arc3/runner.py`

Apply the same change to **both** `_run_puzzle` (around line 737) and `_run_puzzle_with_brain` (around line 1407).

**Restructure the end of each step iteration:**

Current order (after `record_step_result`):
1. reward tracking
2. state/step counters update
3. progress snapshot
4. terminal checks (WIN/GAME_OVER/done) → break
5. REPLAN check

New order:
1. reward tracking
2. state/step counters update
3. progress snapshot
4. terminal checks (WIN/GAME_OVER/done) → break
5. **REPLAN check first** (if triggered: enter REPLAN, skip to loop top)
6. **Per-step PERCEIVE** (if not done and not replanning)

**New PERCEIVE block** (insert after REPLAN check, before loop iteration ends):

```python
# Per-step PERCEIVE: inspect server response before next HYPOTHESIZE
if not done and not success and not _did_replan:
    previous_phase = phase_ctrl.phase_name
    try:
        if phase_ctrl.can_advance(SolvePhase.PERCEIVE):
            phase_ctrl.advance(SolvePhase.PERCEIVE)
        else:
            phase_ctrl.advance(SolvePhase.PERCEIVE, force=True)
    except IllegalPhaseTransition:
        logger.debug("PERCEIVE advance blocked after EVALUATE; skipping per-step perceive")
    except Exception:
        logger.exception("Error advancing to per-step PERCEIVE")

    try:
        self.brain.current_phase = phase_ctrl.phase_name
        if hasattr(orchestrator, "set_write_trace_context"):
            orchestrator.set_write_trace_context(phase_ctrl.phase_name)
        if tc is not None:
            tc.phase_state = phase_ctrl.to_checkpoint()
            mgr.save(checkpoint)
        self._record_phase_transition(
            task=task, orchestrator=orchestrator,
            from_phase=previous_phase, to_phase=phase_ctrl.phase_name,
            step=total_steps, start_time=start_time,
        )
    except Exception:
        logger.exception("Failed to sync phase during per-step perceive")

    last_action_id = None
    if getattr(orchestrator, "_step_history", None):
        last_action_id = orchestrator._step_history[-1].get("action_id")

    try:
        await orchestrator.perceive_step_response(
            observation, step=total_steps, reward=reward, done=done,
            action_id=last_action_id,
        )
    except Exception:
        logger.exception("Per-step perceive_step_response failed")
```

The `_did_replan` boolean is set `True` when the REPLAN block fires, `False` at the start of each iteration.

The advance-to-HYPOTHESIZE at the top of the loop already handles `PERCEIVE → HYPOTHESIZE` naturally since it's now a legal transition.

### ARCHITECTURE.md updates

**File:** `docs/ARCHITECTURE.md`, lines 936-1020

1. **Phase cycle** (line 941): Change per-step cycle to:
   ```
   HYPOTHESIZE → ROUTE → EXECUTE → EVALUATE → PERCEIVE → HYPOTHESIZE (continue)
                                             → REPLAN (stall detected, skips PERCEIVE)
   ```

2. **Phase Definitions table** (line 956): Update PERCEIVE row:
   - Purpose: "Bootstrap: intake initial observation, seed API knowledge cache. Per-step: inspect server response fields (state, grid delta, reward, colors, available actions) and ingest structured summary into SideQuests."
   - Code entry point: `orchestrator.perceive()` (bootstrap), `orchestrator.perceive_step_response()` (per-step)

3. **Transition Table diagram** (line 968): Add EVALUATE → PERCEIVE → HYPOTHESIZE arrow; remove EVALUATE → HYPOTHESIZE direct arrow.

4. **Gate Conditions table** (line 1009):
   - Remove: `EVALUATE → HYPOTHESIZE` row
   - Add: `EVALUATE → PERCEIVE | Action submitted, frame response evaluated | (default path — no gate needed)`
   - Add: `PERCEIVE → HYPOTHESIZE | Step response inspected and ingested | (default path — no gate needed)`
   - Keep: `EVALUATE → REPLAN` row unchanged

## Concrete File Changes

| File | Lines affected | Change |
|------|---------------|--------|
| `agents/arc3/phase.py` | 39, 44 | 2 lines in TRANSITIONS dict |
| `agents/arc3/orchestrator.py` | ~214, ~1318 | Add `_last_response_perception` init + `perceive_step_response()` method (~55 lines) |
| `agents/arc3/runner.py` | ~737-840, ~1407-1497 | Add `_did_replan` flag + PERCEIVE block in both loops (~30 lines each) |
| `docs/ARCHITECTURE.md` | 936-1020 | Phase cycle, table rows, transition diagram |
| `tests/test_phase_controller.py` | append | 5 new tests |
| `tests/test_arc3_orchestrator.py` | append | 1 new test |

## Test Plan

### New tests in `tests/test_phase_controller.py`

```python
def test_evaluate_to_perceive_transition():
    pc = PhaseController()
    # advance to EVALUATE
    for phase in [SolvePhase.MODEL, SolvePhase.HYPOTHESIZE, SolvePhase.ROUTE,
                  SolvePhase.EXECUTE, SolvePhase.EVALUATE]:
        pc.advance(phase, force=True)
    assert pc.can_advance(SolvePhase.PERCEIVE)
    pc.advance(SolvePhase.PERCEIVE)
    assert pc.phase == SolvePhase.PERCEIVE

def test_perceive_to_hypothesize_transition():
    pc = PhaseController()
    pc.advance(SolvePhase.MODEL, force=True)
    pc.advance(SolvePhase.HYPOTHESIZE, force=True)
    pc.advance(SolvePhase.ROUTE, force=True)
    pc.advance(SolvePhase.EXECUTE, force=True)
    pc.advance(SolvePhase.EVALUATE, force=True)
    pc.advance(SolvePhase.PERCEIVE, force=True)
    assert pc.can_advance(SolvePhase.HYPOTHESIZE)
    pc.advance(SolvePhase.HYPOTHESIZE)
    assert pc.phase == SolvePhase.HYPOTHESIZE

def test_evaluate_to_hypothesize_now_illegal():
    pc = PhaseController()
    for phase in [SolvePhase.MODEL, SolvePhase.HYPOTHESIZE, SolvePhase.ROUTE,
                  SolvePhase.EXECUTE, SolvePhase.EVALUATE]:
        pc.advance(phase, force=True)
    with pytest.raises(IllegalPhaseTransition):
        pc.advance(SolvePhase.HYPOTHESIZE)

def test_full_per_step_cycle():
    pc = PhaseController()
    # bootstrap
    pc.advance(SolvePhase.MODEL, force=True)
    # one full step cycle
    for phase in [SolvePhase.HYPOTHESIZE, SolvePhase.ROUTE, SolvePhase.EXECUTE,
                  SolvePhase.EVALUATE, SolvePhase.PERCEIVE, SolvePhase.HYPOTHESIZE]:
        pc.advance(phase, force=True)
    assert pc.phase == SolvePhase.HYPOTHESIZE
    assert pc.step_count == 1

def test_replan_bypasses_perceive():
    pc = PhaseController()
    for phase in [SolvePhase.MODEL, SolvePhase.HYPOTHESIZE, SolvePhase.ROUTE,
                  SolvePhase.EXECUTE, SolvePhase.EVALUATE]:
        pc.advance(phase, force=True)
    # REPLAN path from EVALUATE still legal
    pc.advance(SolvePhase.REPLAN, force=True)
    assert pc.phase == SolvePhase.REPLAN
```

### New test in `tests/test_arc3_orchestrator.py`

```python
async def test_perceive_step_response(mock_brain, sample_observation):
    orch = ARCOrchestrator(mock_brain, MockLLM(), ...)
    sample_observation["state"] = "NOT_FINISHED"
    sample_observation["available_actions"] = ["ACTION1", "ACTION2"]

    result = await orch.perceive_step_response(
        sample_observation, step=3, reward=0.0, done=False, action_id="ACTION2"
    )

    assert result["step"] == 3
    assert result["state"] == "NOT_FINISHED"
    assert result["reward"] == 0.0
    assert result["done"] is False
    assert "delta" in result
    assert "available_actions" in result
    assert result["available_actions"] == ["ACTION1", "ACTION2"]
    mock_brain.notify_turn.assert_called_once()
    call_content = mock_brain.notify_turn.call_args[1].get("content", "")
    assert "[STEP RESPONSE]" in call_content
    assert orch._last_response_perception["step"] == 3
```

## Acceptance Criteria
See backlog/B202.md.

## Validation Commands

```bash
# Phase transition tests
pytest tests/test_phase_controller.py -v

# Orchestrator per-step perceive test
pytest tests/test_arc3_orchestrator.py::test_perceive_step_response -v

# Full test suite
pytest tests/test_arc3_orchestrator.py tests/test_arc3_durable_runner.py tests/test_arc3_solver.py -v

# Smoke test (verify [STEP RESPONSE] in timeline)
python run_single_puzzle.py
grep "STEP RESPONSE" master_timeline_filtered.md | head -5
```

## Risks and Constraints

- **Checkpoint compatibility:** Existing checkpoints in EVALUATE phase will try to advance to HYPOTHESIZE on restore, which is now illegal. The runner uses `force=True` as fallback throughout, so restore will force-advance to HYPOTHESIZE without crashing. No migration needed.
- **Performance:** Each step adds one lightweight `notify_turn` call (~160ms latency based on smoke test). 20-step run adds ~3.2s. Acceptable.
- **REPLAN interaction:** `_did_replan` flag prevents double-advancing on replan steps. Correctly tested by `test_replan_bypasses_perceive`.
