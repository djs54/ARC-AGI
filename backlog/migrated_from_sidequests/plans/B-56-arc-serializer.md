# B-56-arc-serializer — Spatial State-to-Text/State Serializer for Causal Memory

**Card:** B56 | **Priority:** P12 | **Depends on:** B55 (adapter)

## Summary
Implement serializer converting ARC grid state/transitions into structured and natural language forms. Preserves causality for memory extraction without hallucination.

## Technical Approach

### Serialization Modes

1. **Machine form (JSON delta)**
   ```json
   {
     "before_state": [[0, 1, 2], ...],
     "action": "fill_region",
     "changed_cells": [[3, 4, 5], ...],
     "after_state": [[0, 1, 5], ...],
     "reward": 1
   }
   ```

2. **Human/LLM form (narration)**
   ```
   "Changed cells [3,4] [4,4] [5,4] from color 1 to color 5.
    Reward: positive. Grid size: 15x15. Salient objects: red_line (vertical), blue_square (3x3)."
   ```

### Properties
- **Reversibility:** reconstruct after-state from before + delta
- **Compression:** narration stays token-efficient (<100 tokens per step)
- **Causality:** includes action context + reward

## Files to Create/Modify

- `benchmarks/arc3/state_serializer.py` — serialization logic
- `benchmarks/arc3/prompts/state_to_text.md` — narration template
- `tests/test_arc3_state_serializer.py` — round-trip accuracy, compression tests

## Acceptance Criteria

1. Round-trip accuracy: 99%+ (reconstructed after-state matches ground truth)
2. Narration is deterministic
3. Token footprint per step: ≤ 100 tokens
4. Integration: serialized step is readable and can be injected into LLM context

## Notes

- Delta fidelity is critical for causal reasoning
- Compression important for long episodes (could be 1000+ steps)
