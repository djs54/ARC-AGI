# Plan for B190 — Cross-Puzzle Learning via Task Graph

## Card Metadata

- **Card ID**: B190
- **Priority**: P3
- **Dependencies**: B189 (scheduler for ordering)

## Summary

Wire the existing task graph API (B127/B128) to the ARC runner. After solving a puzzle, persist lessons. Before solving the next similar puzzle, retrieve those lessons and inject into orchestrator context.

## Current State

### Existing task graph API (adapter.py:62-76)

```python
class BrainClientProtocol:
    async def register_task_graph(self, label, session_id, owner, tasks): ...
    async def get_ready_tasks(self, graph_id): ...
    async def advance_task(self, graph_id, task_id, status, result): ...
    async def fail_task(self, graph_id, task_id, reason): ...
```

These methods are defined but **never called** by the ARC runner.

### Lesson nodes

`Lesson` nodes exist in KuzuDB schema. `recall_relevant_lessons()` exists in BrainClientProtocol.

## Technical Approach

### Step 1: Create task graph at batch start (runner.py)

```python
# In DurableARCRunner.run():
task_nodes = [{"id": t["task_id"], "label": t["task_id"], "deps": []} for t in tasks]
graph_result = await brain_client.register_task_graph(
    label=f"arc_batch_{int(time.time())}",
    session_id=session_id,
    owner="arc_runner",
    tasks=task_nodes,
)
graph_id = graph_result.get("graph_id")
```

### Step 2: After solving a puzzle, persist lessons

```python
# After successful solve:
if result.get("correct"):
    lesson_content = {
        "archetype": result.get("archetype"),
        "winning_strategy": result.get("strategy_summary"),
        "action_semantics": result.get("action_direction_map"),
        "entity_roles": result.get("object_roles"),
        "game_rules": result.get("game_rule_hypotheses"),
        "steps_to_solve": result.get("steps"),
    }
    await brain_client.notify_turn(
        role="system",
        content=f"LESSON: Solved {task_id} using {json.dumps(lesson_content)}",
        session_id=session_id,
    )

# Advance task node
await brain_client.advance_task(graph_id, task_id, "completed", json.dumps(result))
```

### Step 3: Before solving a puzzle, retrieve lessons from similar puzzles

```python
# Before _run_puzzle():
lessons = await brain_client.recall_relevant_lessons(
    query=f"ARC puzzle {task_id} strategy winning approach",
    limit=3,
)
if lessons:
    # Inject into orchestrator context
    orchestrator_config["prior_lessons"] = lessons
```

### Step 4: Orchestrator uses lessons

In orchestrator.py, when setting up the solve context, include prior lessons:

```python
if self._config.get("prior_lessons"):
    lesson_text = "\n".join([l.get("content", "") for l in self._config["prior_lessons"]])
    # Prepend to system prompt or inject into hypothesis context
```

### Step 5: Ensure NoOpBrainClient handles task graph calls

```python
class NoOpBrainClient:
    async def register_task_graph(self, *args, **kwargs): return {"graph_id": "noop"}
    async def get_ready_tasks(self, *args, **kwargs): return {"tasks": []}
    async def advance_task(self, *args, **kwargs): return {}
    async def fail_task(self, *args, **kwargs): return {}
```

### Step 6: Tests

Create `tests/test_b190_cross_puzzle_learning.py`:
1. Test task graph created at batch start
2. Test lessons persisted after successful solve
3. Test lessons retrieved before solving similar puzzle
4. Test NoOpBrainClient handles all task graph calls
5. Test failed puzzle still advances task (with "failed" status)
6. Test no lessons available → orchestrator runs normally

## Verification

```bash
pytest tests/test_b190_cross_puzzle_learning.py -v
pytest tests/test_arc3_durable_runner.py -v  # regression
```
