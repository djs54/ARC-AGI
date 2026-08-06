# Plan: A136 — Parse Mechanic Prior action_set Into Planner Candidate Pool

## Context

The planner gets mechanic priors from the graph via `fetch_goal_evidence()`, but never parses the `action_set` field. This field is a comma-separated string of known action IDs (e.g. `"ACTION1,ACTION2,ACTION3,ACTION4,ACTION5"`). The priors are present in `graph_records` as dicts with structure:

```python
{
    "goal_id": "mechanic_priors",
    "description": "mechanic priors",
    "confidence": 0.05,
    "evidence": [],
    "metadata": {
        "source": "mechanic_priors",
        "raw": {
            "mechanics": [
                {
                    "action_set": "ACTION1,ACTION2,ACTION3,ACTION4,ACTION5",
                    "confidence": 0.65,
                    "name": "The game follows a space archetype.",
                    ...
                }
            ]
        }
    }
}
```

The planner's `_available_actions()` in `agents/arc4/plan_generator.py` already iterates `graph_records` to collect action IDs, but uses `record["action_id"]` — which is `"mechanic_priors"` (the goal_id), not individual actions.

## Implementation Steps

### Step 1: Add `_extract_mechanic_prior_actions` to PlanGenerator

**File:** `agents/arc4/plan_generator.py`

Add a static method that extracts action IDs from mechanic prior records:

```python
@staticmethod
def _extract_mechanic_prior_actions(graph_records: Sequence[dict[str, Any]]) -> list[str]:
    """Extract individual action IDs from mechanic prior action_set fields."""
    actions: list[str] = []
    for record in graph_records:
        metadata = record.get("metadata") or {}
        if metadata.get("source") != "mechanic_priors":
            continue
        raw = metadata.get("raw") or {}
        mechanics = raw.get("mechanics") or []
        if isinstance(mechanics, Mapping):
            mechanics = [mechanics]
        for mechanic in mechanics:
            if not isinstance(mechanic, Mapping):
                continue
            action_set = mechanic.get("action_set") or ""
            if isinstance(action_set, str):
                for action_id in action_set.split(","):
                    action_id = action_id.strip()
                    if action_id and action_id not in actions:
                        actions.append(action_id)
    return actions
```

### Step 2: Wire into `_available_actions`

**File:** `agents/arc4/plan_generator.py`, method `_available_actions` (~line 237)

After the existing source loop and before the A135 `fetch_untested_actions` block, add:

```python
# A136: Extract actions from mechanic prior action_set fields
mechanic_actions = self._extract_mechanic_prior_actions(graph_records)
for action_id in mechanic_actions:
    if action_id not in candidates:
        candidates.append(action_id)
```

This goes between lines 258-260 (after the source loop) and lines 261-269 (before the graph port untested merge). The mechanic prior actions are a higher-confidence source than `fetch_untested_actions` since they come from the graph's own mechanic memory.

### Step 3: Fix telemetry `mechanic_priors_used_count`

**File:** `agents/arc4/telemetry.py`, line 159

Current (buggy):
```python
"mechanic_priors_used_count": 1 if evaluation and evaluation.meaningful_progress else 0,
```

This counts "1" if progress happened (regardless of whether priors contributed). Replace with counting how many prior-derived candidates made it into the plan:

```python
"mechanic_priors_used_count": self._mechanic_priors_used_count(plan),
```

Add method:
```python
def _mechanic_priors_used_count(self, plan: PlanningResult | None) -> int:
    if plan is None or plan.candidate is None:
        return 0
    count = 0
    all_candidates = [plan.candidate] + list(plan.alternatives)
    for candidate in all_candidates:
        meta = candidate.metadata or {}
        if meta.get("mechanic_prior_source"):
            count += 1
    return count
```

### Step 4: Tag mechanic-prior-sourced candidates in metadata

**File:** `agents/arc4/plan_generator.py`, method `_build_candidates`

When building a candidate whose `action_id` came from mechanic priors, set `metadata["mechanic_prior_source"] = True`. This requires passing the mechanic action list into `_build_candidates`:

In `generate()`, compute `mechanic_actions` and pass to `_build_candidates`:
```python
mechanic_actions = self._extract_mechanic_prior_actions(graph_records)
```

In `_build_candidates`, add to the candidate metadata:
```python
"mechanic_prior_source": action_id in mechanic_action_set,
```

Where `mechanic_action_set` is a `set` built from the `mechanic_actions` list passed in.

### Step 5: Write tests

**File:** `tests/test_a136_mechanic_prior_extraction.py`

Tests needed:
1. `test_extract_mechanic_prior_actions_parses_csv` — basic parsing of `"A,B,C"` → `["A", "B", "C"]`
2. `test_extract_mechanic_prior_actions_deduplicates` — two priors with overlapping action_sets
3. `test_extract_mechanic_prior_actions_empty` — no mechanic priors → empty list
4. `test_extract_mechanic_prior_actions_ignores_non_mechanic_records` — records with other sources
5. `test_planner_uses_mechanic_prior_actions_as_candidates` — end-to-end: planner generates candidates from priors, `planner_candidate_count > 1`
6. `test_planner_candidate_metadata_tags_mechanic_source` — candidates from priors have `mechanic_prior_source=True`
7. `test_planner_deduplicates_mechanic_and_observation_actions` — same action from both sources appears once

### Step 6: Run `make test-a` and existing tests

Verify green baseline and no regressions in A131-A135 tests.

## Files Modified

| File | Change |
|------|--------|
| `agents/arc4/plan_generator.py` | Add `_extract_mechanic_prior_actions`, wire into `_available_actions`, tag metadata |
| `agents/arc4/telemetry.py` | Fix `mechanic_priors_used_count` to count actual prior-derived candidates |
| `tests/test_a136_mechanic_prior_extraction.py` | New test file with 7 tests |

## Risks

- **Mechanic prior action names might not match game API action names.** The mechanic prior stores `"ACTION1,ACTION2,ACTION3,ACTION4,ACTION5"` — if the game API uses different names, the actions will be sent but rejected. Mitigation: the executor already handles unknown actions gracefully (returns error observation, evaluator sees falsification).
- **Duplicate mechanic priors.** The smoke test shows 2 identical mechanics. The `_extract_mechanic_prior_actions` deduplicates by action_id, so this is handled.
