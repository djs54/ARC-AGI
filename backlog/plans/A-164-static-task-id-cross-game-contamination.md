# Plan: A164 — Graph Evidence Cross-Contaminated Across Unrelated Games (Static `task_id`)

## Context

`benchmarks/arc3/tasks_manifest.json` assigns a fixed `task_id` per manifest slot (`arc_eval_001`, ...). A149's `_sync_tasks_with_live_catalog()` (`arc_runtime/runner_shell.py`) remaps the stale `game_id` on each run but never touches `task_id`. `arc_runtime/dispatch.py` (current lines ~27-39) reads `task.task_id` directly and passes it into both `session_id` construction (`f"arc-v2-{task.task_id}-{int(time.time())}"` — already timestamp-uniqued) and `build_arc_v2_bundle(..., task_id=task.task_id, ...)` (unchanged, static). `arc_runtime/bundle.py` (current line 120) passes that same static `task_id` straight into `ArcGraphQueryPort`, which scopes every graph read/write by it.

Live evidence: six different games across this session's live-smoke runs all used `task_id="arc_eval_001"` (manifest slot 1, the default for `--live-smoke --num-puzzles 1`), confirmed directly from each run's own artifact.

## Step 0: Decide the identity model (gate)

Answer, with the user, before implementing:

1. Should `task_id` be derived from `game_id` (so evidence pools *within* repeated attempts at the *same* game — arguably useful for transfer learning across replays — but never *across* different games)? This is the minimal fix matching the actual bug (cross-game contamination), without changing same-game replay behavior.
2. Or should `task_id` be unique per *run* (incorporating a timestamp/session discriminator, like `session_id` already does), so even replaying the same game twice starts with a clean graph slate? This is more conservative (never any cross-run pooling at all) but discards the "does the graph get smarter about a specific game across replays" capability the architecture seems designed to support (the whole point of accumulating `ActionFact` evidence per task).

Recommendation to weigh: option 1 (derive from `game_id`) seems more consistent with the system's own design intent (persistent per-task learning) — option 2 would make every `ActionFact` a one-shot regardless of replay, which seems to defeat much of the point of a persistent graph model. But this is a real product decision, not a mechanical bug fix — don't assume, ask.

## Implementation (once decided)

### If deriving from `game_id`:

In `arc_runtime/dispatch.py`, at the point `task.task_id` is read (current lines ~27-39), compute an effective task_id: something like `effective_task_id = task.game_id or task.task_id` (fall back to the manifest task_id if `game_id` is somehow unset), and use `effective_task_id` everywhere `task.task_id` currently flows into `build_arc_v2_bundle`'s `task_id` parameter — but keep `session_id`'s construction as-is (it should stay unique per run/timestamp regardless, since that's a different concern — session identity vs. task/graph-evidence identity).

Trace every call site carefully — `run_single_puzzle.py` and `arc_runtime/runner_shell.py` may have their own parallel task-dispatch paths post-A143's decomposition (grep for other `build_arc_v2_bundle(` call sites and `task.task_id` reads beyond `dispatch.py` before assuming this is the only place to fix).

### If deriving from run timestamp instead:

Mirror `session_id`'s existing pattern (`f"arc-v2-{task.task_id}-{int(time.time())}"`) for the `task_id` passed to `build_arc_v2_bundle`, but this discards cross-replay learning — confirm this is really the desired tradeoff before implementing.

## Tests

New file `tests/test_a164_task_id_scoping.py`. Exact shape depends on Step 0's decision and where the fix lands (likely `arc_runtime/dispatch.py`) — at minimum:

1. Two different `game_id`s dispatched through the fixed code path produce two different effective task_ids used for the graph port.
2. The same `game_id` dispatched twice (simulating a replay) produces the same effective task_id both times (if option 1 chosen) or different ones (if option 2 chosen) — whichever matches the Step 0 decision, asserted explicitly so the test documents the chosen behavior.
3. A missing/empty `game_id` falls back safely to the manifest `task_id` without crashing (if option 1 chosen).

## Verify

```bash
.venv/bin/python -m pytest tests/test_a164_task_id_scoping.py -v
make test-a
make test-all
```

Manual live confirmation:
```bash
CAMPY_MCP_CMD="../hippocampy/.venv/bin/python -m campy.adapters.mcp_server" \
  PYTHONPATH=. .venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 3
```
Run twice against two different live-remapped games (may require running it more than once since A149's remap is somewhat game-catalog-dependent) — confirm each run's graph evidence starts clean (no inherited nonzero `falsified_count` from an unrelated prior game).

## Files Modified (indicative — confirm exact call sites during implementation)

| File | Change |
|------|--------|
| `arc_runtime/dispatch.py` | Effective task_id derivation for graph-port scoping, decoupled from `session_id`'s own construction |
| `arc_runtime/bundle.py` | If `task_id` plumbing needs adjustment here too |
| `tests/test_a164_task_id_scoping.py` | New tests matching the Step 0 decision |

## Risks

- Changing `task_id`'s identity affects every existing graph node keyed by the old static values (`arc_eval_001` etc.) — old accumulated evidence under those ids becomes orphaned/unreachable going forward, which is *correct* (it was contaminated anyway) but means a visible "reset" in graph behavior right after this lands. Worth flagging, not blocking.
- Interacts with A149 — should probably land as a follow-up to A149's remap logic rather than duplicating catalog-sync logic; read A149's plan/implementation first to keep the two consistent rather than fighting each other.
