# Plan for B154 — ARC Level-Progressive Exploration Policy

## Card Metadata

- **Card ID**: B154
- **Priority**: P1
- **Dependencies**: B157

## Summary

Optimize exploration to adapt per level. Level 1 is a tutorial — explore freely. Later levels should leverage prior knowledge. Cap forced exploration based on level number, action knowledge, and rule confidence. Reduce dissonance thresholds for tight per-level budgets. Remove hardcoded direction mappings.

### ARC-AGI-3 Interactive Game Model

With B157's per-level step budgeting, each level gets ~1-3 steps (10 total / 8 levels). Every forced exploration step is costly:
- **Level 1** (~3-5 steps): Full exploration — this is how the agent learns
- **Level 2-3** (~2-3 steps): Reduced — verify action effects carry over
- **Level 4+** (~1-2 steps): Minimal — action effects should be well-established
- **High-confidence rule**: Zero forced exploration — execute directly

ActionFacts persist across levels (B157 preserves cross-level state). An action tested in level 1 doesn't need re-testing in level 5.

## Technical Approach

### 1. Level-progressive exploration in orchestrator

```python
class ARCOrchestrator:
    def __init__(self, ...):
        ...
        self._forced_exploration_count = 0  # Per-level counter
        self._total_forced_exploration = 0   # Cross-level counter

    def _max_exploration_for_level(self) -> int:
        """B154: How many forced exploration steps this level gets."""
        confidence = getattr(self, '_rule_confidence', 0.0)
        level = getattr(self, '_current_level', 0)

        # High-confidence rule: zero exploration
        if confidence > 0.8:
            return 0

        # Level-progressive
        if level <= 1:
            return 5  # Tutorial level — full exploration
        elif level <= 3:
            return 2  # Early levels — verify carryover
        else:
            # Late levels: only if unknown actions remain
            n_known = len(getattr(self, 'observed_action_effects', {}))
            n_total = len(getattr(self, '_available_actions', []))
            return 1 if n_known < n_total else 0
```

### 2. Modify `_enforce_action_policy()`

```python
# Replace unconditional exploration block:
max_explore = self._max_exploration_for_level()
should_force_explore = (
    unexplored
    and action_id not in unexplored
    and self._forced_exploration_count < max_explore
)

# On level 1, always explore if budget allows
# On later levels, only explore when stuck
if self._current_level > 1 and should_force_explore:
    should_force_explore = self._consecutive_no_progress_steps >= 1

if should_force_explore:
    forced = unexplored[0]
    self._forced_exploration_count += 1
    self._total_forced_exploration += 1
    action.update({
        "action_id": forced,
        "rationale": f"exploration step {self._forced_exploration_count}/{max_explore} (level {self._current_level})",
        "decision_source": "policy_override",
    })
    return action
```

### 3. ActionFact carryover check

```python
# In _enforce_action_policy(), before forcing exploration:
# Check if this action was already tested in a prior level
if action_id in self.observed_action_effects:
    # Already know what this action does — don't force re-exploration
    unexplored = [a for a in unexplored if a not in self.observed_action_effects]
    if not unexplored:
        should_force_explore = False
```

### 4. Reduce DissonanceDetector thresholds

```python
class DissonanceDetector:
    STALL_THRESHOLD: int = 2       # was 6 — per-level budget is tiny
    REWARD_STALL_THRESHOLD: int = 3  # was 8
    MAX_CHUNK_STEPS: int = 4        # was 15
```

### 5. Remove hardcoded direction mapping

In `PlanChunker.generate_chunk()`:
```python
# REMOVE: if aid == "ACTION1": vec = (-1.0, 0.0)  # Up
# REPLACE with:
if not vec:
    continue  # Only use empirically observed action vectors
```

### 6. Reset per-level counter at level transition

```python
# In _on_level_transition() (B157):
self._forced_exploration_count = 0
# NOTE: Do NOT reset observed_action_effects — they carry over
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Add `_max_exploration_for_level()`, `_forced_exploration_count`, `_total_forced_exploration`; modify `_enforce_action_policy()` for level-progressive enforcement; reset per-level counter at transitions |
| `agents/arc3/solver.py` | Reduce DissonanceDetector thresholds (2, 3, 4); remove hardcoded direction mapping in PlanChunker |
| `tests/test_b154_exploration_relaxation.py` | NEW: test level-progressive enforcement, carryover, thresholds |

## Validation Commands

```bash
python3 -m pytest tests/test_b154_exploration_relaxation.py -v
python3 -m pytest tests/test_arc3_orchestrator.py -q
python3 -m pytest tests/test_arc3_solver.py -q
```

## Risks / Constraints

- **Under-exploration on level 1**: If level 1 has too few steps, the agent may not test all actions. With ~3-5 steps on level 1, it should cover most actions. The agent can still voluntarily explore in later levels.
- **Threshold sensitivity**: STALL_THRESHOLD=2 is aggressive. With per-level budgets of 1-2 steps, this fires almost immediately — which is correct (replanning after 2 stalled steps on a 3-step budget).
- **ActionFact persistence**: B157 must NOT reset observed_action_effects between levels. Only per-level counters reset.

## Done When

- Level 1: up to 5 forced exploration steps
- Level 2-3: up to 2 forced exploration steps
- Level 4+: 0-1 based on action knowledge
- High confidence: zero exploration
- ActionFacts carry over across levels
- DissonanceDetector fires at 2 stalled steps
- No hardcoded direction mappings
- All tests pass
