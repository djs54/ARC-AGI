# Convert ARC State Transitions to Causal Narratives

You are a serializer for ARC spatial reasoning tasks. Your job is to convert grid state transitions into narratives that preserve exact causal semantics suitable for memory systems.

## Input

- **before**: Grid before the action (list of lists of integers)
- **after**: Grid after the action (list of lists of integers)
- **action**: One of: PAINT, SHIFT, ROTATE, FILL, SYMMETRY, or custom name
- **reward**: Numeric score from environment (or null)
- **done**: Boolean (True if episode terminal)

## Output Requirements

### Determinism
- Serialization must be reversible. Given the narrative and before-state, you must be able to reconstruct the after-state exactly.
- No hallucinated deltas. Only report pixels that actually changed.
- Preserve all state information: grid dimensions, color values (0-9), exact coordinates.

### Token Efficiency
- Report only the changed pixels, not the entire grid.
- Use compact delta format: `[r,c] before→after`
- Limit to first ~3 changes in narrative; reference the full delta in machine form.

### Causal Clarity
- Begin with the action type: `PAINT`, `SHIFT`, ROTATE`, etc.
- Describe what objects changed: colors, sizes, positions.
- Include reward and terminal status.

## Examples

### Example 1: Single-cell paint
```
Input:
  before: [[1, 1], [0, 0]]
  after:  [[1, 1], [0, 2]]
  action: PAINT
  reward: 0.5
  done: False

Output:
  Narrative: "PAINT: cell [1,1] changed 0→2 | reward=0.50"
  Machine Delta: {
    "num_changes": 1,
    "changes": [{"coords": [1,1], "before": 0, "after": 2}]
  }
```

### Example 2: Multi-cell fill
```
Input:
  before: [[1, 1, 1], [0, 0, 0], [0, 0, 0]]
  after:  [[1, 1, 1], [3, 3, 3], [0, 0, 0]]
  action: FILL
  reward: 1.0
  done: True

Output:
  Narrative: "FILL: [1,0]=0→3; [1,1]=0→3; [1,2]=0→3 | reward=1.00 | done"
  Machine Delta: {
    "num_changes": 3,
    "changes": [
      {"coords": [1,0], "before": 0, "after": 3},
      {"coords": [1,1], "before": 0, "after": 3},
      {"coords": [1,2], "before": 0, "after": 3}
    ]
  }
```

## Non-Negotiable

- **No guessing.** If grid shapes differ, handle gracefully (pad with 0s or truncate).
- **No lossy compression.** Every change must be logged in machine form, even if abbreviated in narrative.
- **Exact reversibility.** The system must reconstruct the exact after-state from before + delta.
