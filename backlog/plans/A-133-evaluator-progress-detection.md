# Plan: A-133 — Evaluator Progress Detection

## Context

The evaluator says "meaningful progress" 91% of the time despite zero diversity and zero falsification delta. It's not actually measuring progress.

## Approach

### 1. Grid-hash comparison

Store `previous_grid_hash` in WorkflowState. After execution, compare the new perception's grid_hash against previous. If identical, `meaningful_progress = False` regardless of what the LLM says.

### 2. Action repetition detector

If the last N actions (e.g., 3) are identical AND `falsification_delta == 0` for all of them, override `meaningful_progress = False`. This catches cases where the grid changes but the agent isn't learning anything.

### 3. Fix consecutive_no_progress_count increment

Check that the orchestrator (both inline `workflow.py` and `temporal_workflows.py`) correctly increments `consecutive_no_progress_count` when `meaningful_progress == False`. The stall detector at 4 consecutive no-progress steps should then fire.

### 4. Separate "done" from "progress"

The evaluator currently says `decision: terminate, meaningful_progress: True` together. These should be independent signals. "Terminate because done" is fine, but "meaningful progress" should reflect whether the *last action* actually advanced understanding, not whether the puzzle is complete.

## Files to modify

- `agents/arc4/evaluator.py` — grid-hash comparison, repetition detection
- `agents/arc4/types.py` — `previous_grid_hash` field on WorkflowState
- `agents/arc4/workflow.py` — verify no-progress increment
- `agents/arc4/temporal_workflows.py` — verify no-progress increment

## Risks

- Grid-hash comparison only catches pixel-identical states; semantically-similar-but-different states need the falsification_delta signal too.
