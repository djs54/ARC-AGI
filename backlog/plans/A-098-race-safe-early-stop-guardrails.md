# Plan: A-098 — Race-safe early-stop guardrails

## Card metadata

- **Card:** A098
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A096, A097

## Summary

Prevent premature `strategy_exhausted` decisions in navigation games when graph evidence shows useful state transitions or terminal-distance movement.

## Implementation approach

1. Add a bounded `WorldModelGraph.get_route_transition_evidence(...)` helper.
2. Feed route-transition evidence into `ReasoningController.decide`.
3. When archetype/victory indicate `race/reach_goal`, suppress all-action churn early stop if recent transition evidence exists.
4. Emit a controller decision such as `route_search_required` instead of `multi_action_churn_exhausted`.
5. Preserve true churn exhaustion when all effects are visual churn, no-op, or harmful.

## Concrete file additions/edits

- `agents/arc3/world_model.py`
- `agents/arc3/reasoning_controller.py`
- `agents/arc3/orchestrator.py`
- `benchmarks/arc3/world_model_eval.py`
- `tests/test_a098_race_safe_early_stop_guardrails.py`

## API/interface changes

Reasoning-gating payload may include:

```json
{
  "route_transition_evidence": {
    "has_route_evidence": true,
    "best_distance_delta": -8.5,
    "novel_state_count": 3,
    "evidence_path_ids": ["state-...", "action-...", "effect-..."]
  },
  "world_model_decision": "route_search_required"
}
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a098_race_safe_early_stop_guardrails.py
.venv/bin/python -m pytest -q tests/test_a094_multi_action_churn_exhaustion_decision.py tests/test_a097_movement_transition_effect_taxonomy.py
make test-a
```

## Assumptions/defaults

- Route evidence must be bounded by recent steps and legal actions.
- The guardrail applies to race/reach-goal by default and can expand later if evidence supports it.
- No MCP or SideQuests internals are imported.
