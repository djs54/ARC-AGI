# Plan: A-095 — DeepSeek smoke prompt compression

## Card metadata

- **Card:** A095
- **Priority:** P1
- **Layer:** ARC runtime
- **Depends on:** A078, A088, A092, A094

## Summary

Use the graph world model as the prompt compression source. The model should reason over belief deltas, active contradictions, and candidate experiments rather than repeated full history.

## Implementation approach

1. Add a compact world-model delta formatter for current goal model, hypothesis changes, action-effect table, contradictions/quarantines, and planner proposals.
2. Replace repeated full summaries after the first reasoning cycle.
3. Add model-aware defaults for local DeepSeek/Ollama.
4. Preserve graph evidence paths in compact form.
5. Emit prompt compression telemetry.

## Concrete file additions/edits

- `agents/arc3/world_model.py`
- `agents/arc3/prompts.py`
- `agents/arc3/orchestrator.py`
- `agents/arc3/runner.py`
- `tests/test_a095_deepseek_prompt_compression.py`

## API/interface changes

```json
{
  "prompt_compression_active": true,
  "estimated_tokens_before_compression": 13000,
  "estimated_tokens_after_compression": 4500,
  "required_world_model_fields_present": true
}
```

## Tests to add or run

```bash
pytest -q tests/test_a095_deepseek_prompt_compression.py
pytest -q tests/test_a088_compact_smoke_artifact_exports.py
make test-a
```

## Assumptions/defaults

- Compression is graph-based, not blind truncation.
- Required action schema and available actions must never be compressed away.
- Use conservative defaults for non-local frontier models unless explicitly enabled.
