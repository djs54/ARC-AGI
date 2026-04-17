# B-217 — Victory Condition Convergence: Bootstrap from Archetype and Grid Structure

- **Card:** backlog/B217.md
- **Priority:** P1
- **Dependencies:** B214, B215, B216

## Summary

When archetype is known but victory condition is still unknown after step 3, inject a candidate victory hypothesis derived from the archetype label and grid color inventory rather than leaving the agent goal-blind.

## Technical Approach

### 1. Add archetype → default victory mapping

File: `agents/arc3/solver.py`

Add a class-level constant mapping archetype labels to candidate victory conditions:

```python
_ARCHETYPE_VICTORY_DEFAULTS: dict[str, dict] = {
    "space": {
        "type": "navigate_to_goal",
        "description": "Move the player token to the goal tile",
        "confidence": 0.40,
    },
    "puzzle": {
        "type": "complete_pattern",
        "description": "Fill or match the target pattern",
        "confidence": 0.40,
    },
    "collect": {
        "type": "collect_all_items",
        "description": "Move player to collect all target items",
        "confidence": 0.40,
    },
    # extend as new archetypes are confirmed
}
```

### 2. Add bootstrap injection in hypothesize phase

In the hypothesize phase logic (where `victory_condition` is evaluated), add:

```python
archetype = solve_ctx.get("archetype") or "unknown"
victory = solve_ctx.get("victory_condition") or {}
victory_type = victory.get("type") if isinstance(victory, dict) else "unknown"
archetype_conf = float(solve_ctx.get("archetype_confidence") or 0.0)
current_step = step  # passed in from orchestrate loop

# Bootstrap victory condition from archetype when still unknown past step 3
if (
    victory_type in (None, "unknown", "")
    and archetype != "unknown"
    and archetype_conf >= 0.5
    and current_step >= 3
):
    default = self._ARCHETYPE_VICTORY_DEFAULTS.get(archetype)
    if default:
        solve_ctx["victory_condition"] = {
            "type": default["type"],
            "description": default["description"],
            "confidence": default["confidence"],
            "source": "archetype_bootstrap",
        }
        self._trace(
            "solve_victory_bootstrap",
            "hypothesize",
            {"archetype": archetype, "victory_type": default["type"], "step": current_step},
            f"victory bootstrapped from archetype={archetype}: {default['type']}",
        )
```

### 3. Persist bootstrap as a lesson (requires B214)

After bootstrap injection, call `upsert_lesson` to make it retrievable on future steps:

```python
# Store bootstrap hypothesis as a lesson for recall in later steps
await brain.upsert_lesson(
    domain=archetype,
    text=f"Archetype={archetype}: default victory condition is '{default['type']}' — {default['description']}",
    valence=0.5,
    confidence=default["confidence"],
    tags=[archetype, "hypothesis", "victory_bootstrap"],
)
```

This requires B214 to be fixed first for the lesson to actually persist.

### 4. Structural goal-color scan in model phase

File: `agents/arc3/runner.py`

In the model phase build (where grid observation is processed), when `player` role is identified but no goal color is known, add a heuristic scan:

```python
# When player role is known but goal unknown, identify candidate goal colors
# Goal candidates = colors present in grid that are not background (0), not player, not path
player_color = primary_roles.get("player")
background_color = 0
path_colors = {primary_roles.get("path")}
candidate_goal_colors = [
    c["value"] for c in observation.get("colors", [])
    if c["value"] not in (background_color, player_color)
    and c["value"] not in path_colors
    and c.get("count", 0) < 50  # goal tiles are typically sparse
]
if candidate_goal_colors and not primary_roles.get("goal"):
    primary_roles["goal_candidates"] = candidate_goal_colors
    # Inject into model context for perceive to use
    solve_ctx["goal_color_candidates"] = candidate_goal_colors
```

## Concrete File Changes

- `agents/arc3/solver.py`
  - Add `_ARCHETYPE_VICTORY_DEFAULTS` constant.
  - Add bootstrap injection block in hypothesize phase.
  - Add trace emission for bootstrap.
  - Add `upsert_lesson` call for bootstrap persistence (gated on B214).
- `agents/arc3/runner.py`
  - Add structural goal-color scan in model phase build.
- `tests/test_arc3_solver.py`
  - Add `test_victory_bootstrap_from_archetype`:
    - Given archetype=space, conf=0.6, step=4, victory=unknown.
    - Assert solve_ctx["victory_condition"]["type"] == "navigate_to_goal".
    - Assert source == "archetype_bootstrap".
  - Add `test_victory_bootstrap_not_before_step_3`:
    - Given archetype=space, step=2.
    - Assert victory_condition remains unknown.

## API/Schema/Test Updates

- API/schema: none.
- Tests: 2 new unit tests.

## Acceptance Criteria

1. When archetype conf ≥ 0.5 and victory unknown after step 3, a candidate victory condition is set in solve_ctx.
2. Bootstrap source is recorded as "archetype_bootstrap" in the victory dict.
3. Bootstrap does NOT fire before step 3 (too early to have reliable archetype).
4. Bootstrap does NOT override a non-unknown victory condition.
5. Trace event "solve_victory_bootstrap" is emitted.
6. Smoke run hypothesize `phase_answer` shows a non-unknown victory condition by step 5.

## Validation Commands

```
.venv/bin/python -X dev -m pytest tests/test_arc3_solver.py -q -k victory
.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1
```

## Risks / Constraints

- Bootstrap confidence (0.40) is intentionally low to signal it is a guess, not a confirmed goal.
- If archetype is wrong, bootstrap will be wrong. But a wrong working hypothesis is better than no hypothesis, as it generates evidence (the agent will try to navigate to goal and either succeed or learn the hypothesis is wrong).
- B214 must be fixed first for the lesson persistence step to work; the bootstrap injection itself is independent of B214.
