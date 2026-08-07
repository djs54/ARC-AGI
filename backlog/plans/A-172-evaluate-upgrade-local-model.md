# Plan: A172 — Benchmark and Configure a Deliberate Local Model Choice

## Context

`campy.toml`'s `[llm]` section has no `model` key, so every run this session silently used `arc_runtime/llm.py::create_llm_client`'s hardcoded fallback (`llama3.1:8b`) — an accident, not a decision. Larger/reasoning-tuned models are already available locally (`gemma4:26b`, `deepseek-r1:8b`, `qwen2.5:7b`, etc.) at zero additional cost. This plan benchmarks candidates using the real (post-A169) prompt shape and makes the choice explicit.

## Implementation

### 1. Benchmark script (scratch, not committed to the repo — or committed under `scripts/` if useful as a durable tool; decide based on whether it's worth keeping)

Reuse the direct-reproduction technique already used earlier this session (extract a real captured plan-phase request payload from a live-smoke log, replay it via `curl`/`OpenAI` client against each candidate model with `model` swapped). For each candidate:

- Send the same representative prompt (post-A169, so it includes `grid_text`).
- Record: valid-JSON-on-first-try rate (across a handful of samples, not just one), whether returned `action_id` matches an actual candidate, wall-clock latency, and a qualitative read of the `reason` field's engagement with the grid content.

Candidates: `deepseek-r1:8b`, `gemma4:26b`, `qwen2.5:7b`, against the `llama3.1:8b` baseline.

### 2. Configure the winner

Add to `campy.toml`:

```toml
[llm]
provider = "ollama"
model = "<chosen-model>"
```

### 3. Document the rationale

Short note (comment in `campy.toml` above the `model` line, or a short section in `ARCHITECTURE.md` if there's already a relevant section) stating which models were compared, the latency/quality tradeoff observed, and why the chosen one won — specific numbers, not vague claims.

## Tests

No new unit tests expected (this is a config/benchmarking card, not new production logic) — but audit existing tests for any hardcoded `llama3.1:8b` assumption before landing:

```bash
grep -rn "llama3.1" tests/
```

If any test asserts on the specific model string, confirm it's testing config-loading mechanics (fine, leave it) and not accidentally asserting production behavior should always use that specific model (would need updating).

## Verify

```bash
grep -rn "llama3.1" tests/   # audit, per above
make test-a
make test-all
```

Live confirmation: full `--live-smoke` run against the newly-configured model, end-to-end, no regressions vs. the `llama3.1:8b` baseline runs from earlier this session (completes without error, JSON parses, `llm_guidance` reachable via the same direct-reproduction verification method used for A168).

## Files Modified

| File | Change |
|------|--------|
| `campy.toml` | Explicit `model =` under `[llm]`, with a rationale comment |
| (scratch benchmark script, path TBD — decide whether to keep under `scripts/` or discard after use) | New |

## Risks

- A much larger model could make live-smoke runs impractically slow for future iteration — latency is an explicit part of the benchmark, not an afterthought.
- Real possible outcome: no candidate clearly beats the baseline for this specific task shape. If so, document that finding and leave `model` unset (or explicitly set to `llama3.1:8b` to make the choice deliberate rather than accidental) rather than switching on weak evidence.
