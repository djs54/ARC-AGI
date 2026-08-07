# Plan: A169 — Feed the LLM a Compact Text Encoding of the Grid

## Context

`perceive.py` builds the grid, hashes it, extracts blob-shaped entities, then discards the grid. Every LLM prompt this session contained only `grid_hash` + abstracted entity stats — never the grid itself. Fix: encode the grid as one-character-per-cell text (ARC's palette is single digits 0-9, confirmed from live entity `color` values) and thread it into both LLM-escalation call sites.

## Implementation

### 1. Encoding helper

In `agents/arc4/perceive.py`, add:

```python
def _encode_grid_text(grid: Sequence[Sequence[Any]], *, max_cells: int = 4096) -> str:
    if not grid:
        return ""
    rows = len(grid)
    cols = max((len(row) for row in grid), default=0)
    if rows * cols > max_cells:
        return f"grid omitted: {rows}x{cols} exceeds {max_cells}-cell encoding limit"
    return "\n".join("".join(str(cell) for cell in row) for row in grid)
```

`max_cells=4096` covers up to 64x64 (the largest grid seen this session) at ~1,000-1,400 tokens; anything larger falls back to a clear placeholder rather than an unbounded prompt.

### 2. Attach to `PerceptionSnapshot`

In `perceive()`, after computing `normalized_grid`:

```python
grid_text = self._encode_grid_text(normalized_grid)
```

Add to `snapshot.metadata["grid_text"] = grid_text` (don't add a new dataclass field to `PerceptionSnapshot` itself — `metadata` is the existing extension point other phases already read from).

### 3. Thread into `plan_generator.py::_query_llm`

Add `perception.metadata.get("grid_text", "")` to the user-message JSON payload, alongside `grid_hash`.

### 4. Thread into `goal_resolver.py`'s LLM-escalation call site

Same pattern — locate its prompt-construction code (search for where it builds the `LLMMessage` list for its own `llm_port.chat(...)` call) and add the grid text there too.

## Tests

New `tests/test_a169_grid_text_encoding.py`:

1. `test_encode_small_grid_exact_output` — a known 3x3 grid → exact expected `"012\n345\n678"`-style string.
2. `test_encode_empty_grid_returns_empty_string`.
3. `test_encode_oversized_grid_returns_fallback_message` — a grid exceeding `max_cells` → the placeholder string, not a huge dump.
4. `test_perception_snapshot_metadata_includes_grid_text` — `PerceiveAgent.perceive(...)` on a real observation → `snapshot.metadata["grid_text"]` present and correct.
5. `test_plan_generator_prompt_includes_grid_text` — stub a recording `LLMPort`, force escalation (low-scoring candidates), assert the captured `LLMMessage` user-content JSON includes the `grid_text` key with the expected value.
6. Equivalent recording-port test for `goal_resolver.py`'s escalation call site.

## Verify

```bash
.venv/bin/python -m pytest tests/test_a169_grid_text_encoding.py -v
make test-a
make test-all
```

Live confirmation:
```bash
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
  PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 15
```
Extract one plan-phase request payload from the log (same technique used earlier this session — parse the `openai._base_client - DEBUG - Request options:` line) and manually confirm the grid text is present and its line count/character-per-line matches `grid_shape`.

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/perceive.py` | New `_encode_grid_text` helper; attached to `snapshot.metadata["grid_text"]` |
| `agents/arc4/plan_generator.py` | `_query_llm`'s user-message payload includes `grid_text` |
| `agents/arc4/goal_resolver.py` | Its LLM-escalation prompt includes `grid_text` |
| `tests/test_a169_grid_text_encoding.py` | New, 6 tests |

## Risks

- Larger prompts mean slower/more expensive LLM calls (already observed: JSON-mode decoding is noticeably slower than free-text) — mitigated by the `max_cells` cap; monitor live-smoke runtime after landing and adjust the cap if it becomes impractical.
- None of the deterministic scoring heuristics change — this is purely additive context for the LLM escalation path, so no risk to A160-A168's already-verified behavior.
