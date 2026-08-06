# Plan: A149 — Sync Manifest Tasks With Live ARC Game Catalog

## Context

`arc_runtime/runner_shell.py` `SingleTaskRunner.initialize()` loads tasks from `benchmarks/arc3/tasks_manifest.json`. The ARC API rotates game ids, so static manifest ids go stale. `benchmarks/arc3/harness.py:82` already exposes `async list_games() -> List[Dict]` (`GET /api/games`, `[]` in mock mode).

This card documents an implementation that already existed uncommitted, and backfills tests. No new feature design — capture + cover what is there.

## Implementation (as shipped)

`initialize()` calls `await self._sync_tasks_with_live_catalog()` when `self.real_api`, after `load_tasks_from_manifest`.

`_sync_tasks_with_live_catalog()`:
- `games = await self.harness.list_games()`; on exception → log warning, return (manifest unchanged).
- filter to dict entries with a non-empty `game_id`; if none → log warning, return.
- if `self.tasks` empty → build `ABTask(task_id=f"arc_live_{i:03d}", category="core", ...)` per live game, set `game_id`/`arc_game_title`/`arc_game_tags`.
- else → for each task: if its `game_id` not in the live id set, remap to `live_games[idx % len]` and count it; always refresh title/tags from the matched/assigned game. Log the remap count if nonzero.

`from benchmarks.ab_harness import ABTask` added to `runner_shell.py` imports.

## Tests — `tests/test_a149_live_catalog_sync.py`

Construct the runner with `object.__new__(SingleTaskRunner)` (the manifest/MCP init is heavy), set `real_api=True`, `tasks`, and a `SimpleNamespace(list_games=<async>)` fake harness. Drive the coroutine with `asyncio.run`.

Cases:
1. empty manifest → tasks generated from catalog (+ metadata)
2. stale manifest game_id → remapped to live id
3. valid game_id → kept, title/tags refreshed
4. `list_games()` raises → manifest unchanged, no crash
5. empty catalog → manifest unchanged

## Verify

```bash
.venv/bin/python -m pytest tests/test_a149_live_catalog_sync.py -q
make test-all
```

## Follow-ups (not in this card)

- Consider a `requires_arc_api` smoke that asserts the chosen `game_id` is present in `/api/games` end to end.
- Round-robin remap is arbitrary when ids are stale; a future card could match by game family/title instead of position.

## Files Modified

| File | Change |
|------|--------|
| `arc_runtime/runner_shell.py` | `_sync_tasks_with_live_catalog` + call site + `ABTask` import (already present; committed here) |
| `tests/test_a149_live_catalog_sync.py` | New, 5 tests |

## Risks

- Round-robin remap can pair a task with an unrelated game when its id is stale — acceptable for smoke coverage; flagged as a follow-up. Real-API only, so no effect on mock runs or the default suite.
