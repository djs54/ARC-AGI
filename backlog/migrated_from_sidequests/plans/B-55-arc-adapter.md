# B-55-arc-adapter — ARC Interactive Adapter (Protocol-Correct SideQuests Bridge)

**Card:** B55 | **Priority:** P12 | **Depends on:** B54 (rules verified), B48 (A/B contract)

## Summary
Build production adapter that bridges ARC interactive episodes to SideQuests tools. Normalizes environment observations/actions and integrates passive ingestion.

## Technical Approach

### Observation/Action Normalization
```python
class ARC3Adapter:
  def normalize_observation(state) -> dict
    # Convert numpy grid → standardized dict
    # Properties: dataset_id, task_id, episode_num, step_num, grid, colors, shapes
  
  def normalize_action(action) -> dict
    # Standardized action dict: action_type, grid_change, rationale
  
  def to_turn_narrative(obs, action, reward) -> str
    # Natural language summary for passive ingestion
    # e.g., "Changed cell [3,4] from blue to red, reward received"
```

### Integration
- `notify_turn` integration: send episode step as turn
- `current_truth` calls: retrieve memory before next action
- Telemetry logging: step-level traces for reproducibility

## Files to Create/Modify

- `benchmarks/arc3/adapter.py` — ARC3Adapter class
- `benchmarks/arc3/schema.py` — normalized observation/action schemas
- `tests/test_arc3_adapter_contract.py` — protocol validation tests

## Acceptance Criteria

1. Adapter normalizes ARC observations to stable schema
2. Actions are serialized deterministically
3. `notify_turn` calls work correctly with ARC turn data
4. `current_truth` retrieval works within ARC episodes
5. One episode replay yields deterministic logs
6. Contract tests fail on malformed payloads
7. Full end-to-end: load episode → solve with memory → replay produces same trace

## Notes

- Must preserve exact action determinism for reproducibility
- Performance: adapter overhead should be <5% per step
