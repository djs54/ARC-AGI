# Plan for B183 — Meta-Supervisor Agent

## Card Metadata

- **Card ID**: B183
- **Priority**: P1
- **Dependencies**: B184 (circuit breaker)

## Summary

Extract the step-count escalation ladder from orchestrator into a `PuzzleSupervisor` that analyzes trajectory quality (not just step counts) and returns CONTINUE, NUDGE, RESET_STRATEGY, or ABANDON decisions.

## Current State

### Escalation ladder (orchestrator.py:1674-1702)

```python
if self._consecutive_no_progress_steps >= 3:
    self._solve_context["dissonance"] = True  # Tier 1
if self._consecutive_no_progress_steps >= 5:
    self._blocked_actions.add(last_action)     # Tier 2
if self._consecutive_no_progress_steps >= 8:
    self._mark_active_chunk_failed(...)        # Tier 3
    self._consecutive_no_progress_steps = 0    # Reset!
```

Purely step-count based. Cannot distinguish oscillation from slow progress.

### Available trace data (orchestrator.py:~145)

```python
self._execution_trace: List[dict] = []  # Full trace events
self._step_history: List[dict] = []      # Per-step action/result records
```

Rich data available but not analyzed for supervision.

## Technical Approach

### Step 1: Create agents/arc3/supervisor.py

```python
class SupervisorDecision(str, Enum):
    CONTINUE = "continue"
    NUDGE = "nudge"
    RESET_STRATEGY = "reset_strategy"
    ABANDON = "abandon"

@dataclass
class SupervisorVerdict:
    decision: SupervisorDecision
    reason: str
    nudge_hint: Optional[str] = None  # Only for NUDGE decisions

class PuzzleSupervisor:
    def __init__(self, llm_client=None, check_interval: int = 5):
        self.llm = llm_client
        self.check_interval = check_interval

    def evaluate(
        self,
        step_history: List[dict],
        execution_trace: List[dict],
        cost_tracker: Optional[CostTracker] = None,
    ) -> SupervisorVerdict:
        step = len(step_history)
        if step % self.check_interval != 0:
            return SupervisorVerdict(SupervisorDecision.CONTINUE, "not check interval")

        # Rule-based checks (fast path, no LLM)
        verdict = self._rule_based_check(step_history, execution_trace, cost_tracker)
        if verdict:
            return verdict

        # Optional LLM escalation for ambiguous cases
        if self.llm and self._is_ambiguous(step_history):
            return await self._llm_evaluate(step_history, execution_trace)

        return SupervisorVerdict(SupervisorDecision.CONTINUE, "no issues detected")
```

### Step 2: Rule-based checks

```python
def _rule_based_check(self, history, trace, cost) -> Optional[SupervisorVerdict]:
    # 1. Oscillation detection: same 2-3 states repeating
    recent_states = [s.get("frame_hash") for s in history[-10:]]
    unique_recent = len(set(recent_states))
    if len(recent_states) >= 8 and unique_recent <= 2:
        return SupervisorVerdict(SupervisorDecision.RESET_STRATEGY,
            f"oscillating between {unique_recent} states for {len(recent_states)} steps")

    # 2. Total stagnation: no reward for 30+ steps
    zero_reward_streak = sum(1 for s in reversed(history) if s.get("reward", 0) == 0)
    if zero_reward_streak >= 30:
        return SupervisorVerdict(SupervisorDecision.ABANDON,
            f"{zero_reward_streak} consecutive zero-reward steps")

    # 3. Action diversity check: only using 1-2 of N available actions
    recent_actions = [s.get("action_id") for s in history[-15:]]
    available = history[-1].get("available_actions", []) if history else []
    used = set(recent_actions)
    if len(available) >= 4 and len(used) <= 2 and len(recent_actions) >= 10:
        untried = [a for a in available if a not in used]
        return SupervisorVerdict(SupervisorDecision.NUDGE,
            f"only using {used}, try {untried}",
            nudge_hint=f"You haven't tried actions: {', '.join(untried)}. Try them.")

    # 4. Budget warning: > 70% budget consumed
    if cost and cost.total_cost_usd > cost.budget_usd * 0.7:
        return SupervisorVerdict(SupervisorDecision.NUDGE,
            "70% budget consumed",
            nudge_hint="Budget is running low. Focus on your most promising strategy.")

    # 5. Centroid not moving: player position same for 10+ steps
    positions = [(s.get("autopilot_player_row"), s.get("autopilot_player_col"))
                 for s in history[-10:] if s.get("autopilot_player_row") is not None]
    if len(positions) >= 8 and len(set(positions)) == 1:
        return SupervisorVerdict(SupervisorDecision.RESET_STRATEGY,
            "player position unchanged for 10+ steps")

    return None
```

### Step 3: Wire into orchestrator (orchestrator.py)

Replace the escalation ladder (lines 1674-1702) with:

```python
verdict = self._supervisor.evaluate(self._step_history, self._execution_trace, self._cost_tracker)
self._emit_trace_event("operation", "supervisor_verdict", {
    "step": step_num, "decision": verdict.decision.value, "reason": verdict.reason,
})

if verdict.decision == SupervisorDecision.NUDGE:
    # Inject hint into next prompt context
    self._supervisor_nudge = verdict.nudge_hint
elif verdict.decision == SupervisorDecision.RESET_STRATEGY:
    self.solve_engine._archetype_confidence *= 0.3
    self.solve_engine._victory_condition = None
    self.solve_engine._plateau_locked_family = None
    self._blocked_actions.clear()
elif verdict.decision == SupervisorDecision.ABANDON:
    self._should_abandon = True  # Checked in runner's step loop
```

### Step 4: Tests

Create `tests/test_b183_supervisor.py`:
1. Test oscillation detection (2 states repeating) → RESET_STRATEGY
2. Test 30+ zero-reward → ABANDON
3. Test low action diversity → NUDGE with untried actions
4. Test budget warning at 70% → NUDGE
5. Test centroid stuck → RESET_STRATEGY
6. Test normal progress → CONTINUE
7. Test check_interval skips non-check steps

## Verification

```bash
pytest tests/test_b183_supervisor.py -v
pytest tests/test_arc3_orchestrator.py -v  # regression
```
