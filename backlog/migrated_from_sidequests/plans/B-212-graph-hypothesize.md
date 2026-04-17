# B-212 — Graph Inference Step in HYPOTHESIZE: Implementation Plan

- **Card:** backlog/B212.md
- **Priority:** P1
- **Dependencies:** B211 (ActionEffect writes), B202 (per-step perceive), B213 (clean policy)

## Summary

Add `graph_hypothesize()` and `_build_spatial_query()` to `ARCOrchestrator`. Call `graph_hypothesize()` from inside `hypothesize()` after existing logic, guarded by `step > 0` and archetype != `"unknown"`. Results land in `_hypothesis_context["graph_evidence"]["grounded_hypotheses"]`. Pass `graph_evidence` into solve/act prompt construction in `runner.py`. No new LLM calls — distillation is rule-based evidence counting.

## Technical Approach

### New helper: `_build_spatial_query()`

**File:** `agents/arc3/orchestrator.py`, place near `_build_spatial_query` (new method, alongside other private helpers).

```python
def _build_spatial_query(
    self,
    solve_context: dict,
    observation: dict,
) -> str:
    """Build a structural query string describing the current board layout.

    Used by graph_hypothesize() tier 2 to find past VictoryCondition records
    for spatially similar puzzles.
    """
    parts = []
    archetype = str(solve_context.get("archetype") or "unknown")
    parts.append(archetype)

    roles = solve_context.get("roles") or {}
    role_types = sorted({
        str(v.get("role") or "")
        for v in roles.values()
        if isinstance(v, dict) and v.get("role")
    })
    if role_types:
        parts.extend(role_types)

    n_regions = int(solve_context.get("n_regions") or 0)
    if n_regions >= 2:
        parts.append(f"{n_regions}_regions")

    vc = solve_context.get("victory_condition") or "unknown"
    if vc and vc != "unknown":
        parts.append(str(vc))
    else:
        parts.append("victory_condition_unknown")

    return " ".join(parts)
```

### New method: `graph_hypothesize()`

**File:** `agents/arc3/orchestrator.py`, place after `hypothesize()` (around line 1370).

```python
async def graph_hypothesize(
    self,
    observation: dict,
    step: int,
) -> dict:
    """Run structured graph queries to produce evidence-grounded hypotheses (B212).

    Called from hypothesize() after existing LLM inference, guarded by:
      - step > 0 (no ActionEffect records exist at bootstrap)
      - archetype != "unknown" (queries are archetype-scoped)

    Returns a graph_evidence dict merged into _hypothesis_context.
    No LLM calls are made here — distillation is rule-based.
    """
    solve_ctx = self._solve_context or {}
    archetype = str(solve_ctx.get("archetype") or "unknown")

    if step <= 0 or archetype == "unknown":
        return {}

    graph_evidence: dict = {
        "action_effect_patterns": [],
        "spatial_victory_hints": [],
        "matching_procedures": [],
        "grounded_hypotheses": [],
    }

    # --- Tier 1: entity-action effect patterns ---
    try:
        tier1_query = (
            f"lesson_type:action_effect "
            f"effect_class:large_transformation OR effect_class:local_change "
            f"puzzle_archetype:{archetype}"
        )
        lessons = await self.brain.recall_relevant_lessons(
            query=tier1_query, limit=8
        )
        if lessons:
            graph_evidence["action_effect_patterns"] = lessons if isinstance(lessons, list) else []
    except Exception as exc:
        logger.warning("B212 tier1 query failed: %s", exc)

    # --- Tier 2: spatial victory condition hints ---
    try:
        spatial_query = self._build_spatial_query(solve_ctx, observation)
        truth = await self.brain.current_truth(
            query=spatial_query, scope="branch", limit=3
        )
        if truth:
            graph_evidence["spatial_victory_hints"] = truth if isinstance(truth, list) else []
    except Exception as exc:
        logger.warning("B212 tier2 query failed: %s", exc)

    # --- Tier 3: procedure search by structural fingerprint ---
    try:
        task_id = str(observation.get("task_id") or "")
        proc_query = f"{archetype} trigger_object interaction"
        procedures = await self.brain.recall_procedures(
            task_id=task_id, query=proc_query, limit=3
        )
        if procedures:
            graph_evidence["matching_procedures"] = procedures if isinstance(procedures, list) else []
    except Exception as exc:
        logger.warning("B212 tier3 query failed: %s", exc)

    # --- Distill grounded_hypotheses (rule-based, no LLM) ---
    pattern_counts: dict[tuple, int] = {}
    for lesson in graph_evidence["action_effect_patterns"]:
        if not isinstance(lesson, dict):
            continue
        meta = lesson.get("metadata") or lesson
        action = str(meta.get("action") or "")
        entity_type = str(meta.get("entity_type") or "unknown")
        effect_class = str(meta.get("effect_class") or "")
        if action and effect_class:
            key = (action, entity_type, effect_class)
            pattern_counts[key] = pattern_counts.get(key, 0) + 1

    grounded = [
        {
            "action": action,
            "entity_type": entity_type,
            "expected_effect": effect_class,
            "evidence_count": count,
        }
        for (action, entity_type, effect_class), count in sorted(
            pattern_counts.items(), key=lambda x: -x[1]
        )
    ]
    graph_evidence["grounded_hypotheses"] = grounded

    self._emit_trace_event(
        "operation",
        "graph_hypothesize_complete",
        {"step": step, "archetype": archetype},
        {
            "tier1_lessons": len(graph_evidence["action_effect_patterns"]),
            "tier2_hints": len(graph_evidence["spatial_victory_hints"]),
            "tier3_procs": len(graph_evidence["matching_procedures"]),
            "grounded_hypotheses": len(grounded),
        },
    )

    return graph_evidence
```

### Call site in `hypothesize()`

At the end of `hypothesize()`, after the existing LLM logic returns, add:

```python
# B212: structured graph evidence pass (runs after LLM hypothesize)
if step > 0:
    graph_evidence = await self.graph_hypothesize(observation=observation, step=step)
    if graph_evidence:
        self._hypothesis_context["graph_evidence"] = graph_evidence
```

### runner.py: expose graph_evidence in solve/act prompts

In `_build_orchestration_report()` or wherever hypothesis context is serialized into the LLM prompt for `solve()` and `act()`, add a "GRAPH EVIDENCE" section when `grounded_hypotheses` is non-empty with `evidence_count >= 2`:

```python
graph_evidence = hypothesis_context.get("graph_evidence") or {}
grounded = [
    h for h in (graph_evidence.get("grounded_hypotheses") or [])
    if h.get("evidence_count", 0) >= 2
]
if grounded:
    lines = ["GRAPH EVIDENCE (from prior puzzles, evidence_count >= 2):"]
    for h in grounded:
        lines.append(
            f"  - {h['action']} on {h['entity_type']} → {h['expected_effect']} "
            f"(n={h['evidence_count']})"
        )
    prompt_context["graph_evidence_section"] = "\n".join(lines)
```

## Concrete File Changes

| File | Change |
|------|--------|
| `agents/arc3/orchestrator.py` | Add `_build_spatial_query()` helper (~25 lines) |
| `agents/arc3/orchestrator.py` | Add `graph_hypothesize()` method (~80 lines) |
| `agents/arc3/orchestrator.py` | Call site in `hypothesize()`: 4-line block at end |
| `agents/arc3/runner.py` | Inject `graph_evidence_section` into solve/act prompt construction |

## Test

```python
@pytest.mark.asyncio
async def test_graph_hypothesize_returns_grounded_evidence(mock_brain, sample_observation):
    """B212: graph_hypothesize must return typed grounded_hypotheses from ActionEffect lessons."""
    orch = ARCOrchestrator(mock_brain, MockLLM(), ...)
    orch._solve_context = {"archetype": "space", "roles": {}}

    # Simulate tier 1 returning 3 matching ActionEffect lessons
    mock_brain.recall_relevant_lessons.return_value = [
        {"metadata": {"action": "ACTION5", "entity_type": "compact_object",
                      "effect_class": "large_transformation", "lesson_type": "action_effect"}},
        {"metadata": {"action": "ACTION5", "entity_type": "compact_object",
                      "effect_class": "large_transformation", "lesson_type": "action_effect"}},
        {"metadata": {"action": "ACTION2", "entity_type": "player",
                      "effect_class": "directional_movement", "lesson_type": "action_effect"}},
    ]
    mock_brain.current_truth.return_value = []
    mock_brain.recall_procedures.return_value = []

    result = await orch.graph_hypothesize(observation=sample_observation, step=3)

    assert "grounded_hypotheses" in result
    grounded = result["grounded_hypotheses"]
    assert len(grounded) >= 1

    top = grounded[0]
    assert top["action"] == "ACTION5"
    assert top["entity_type"] == "compact_object"
    assert top["expected_effect"] == "large_transformation"
    assert top["evidence_count"] == 2

    # Verify structured query was used (not free text)
    call_args = mock_brain.recall_relevant_lessons.call_args
    query_str = call_args[1].get("query") or call_args[0][0]
    assert "lesson_type:action_effect" in query_str
    assert "space" in query_str  # archetype-scoped


@pytest.mark.asyncio
async def test_graph_hypothesize_skips_at_step_zero(mock_brain, sample_observation):
    """B212: graph_hypothesize must not query at step 0 (no records exist yet)."""
    orch = ARCOrchestrator(mock_brain, MockLLM(), ...)
    orch._solve_context = {"archetype": "space"}

    result = await orch.graph_hypothesize(observation=sample_observation, step=0)

    assert result == {}
    mock_brain.recall_relevant_lessons.assert_not_called()


@pytest.mark.asyncio
async def test_graph_hypothesize_skips_unknown_archetype(mock_brain, sample_observation):
    """B212: graph_hypothesize must not query when archetype is unknown."""
    orch = ARCOrchestrator(mock_brain, MockLLM(), ...)
    orch._solve_context = {"archetype": "unknown"}

    result = await orch.graph_hypothesize(observation=sample_observation, step=5)

    assert result == {}
    mock_brain.recall_relevant_lessons.assert_not_called()
```

## Validation Commands

```bash
pytest tests/test_arc3_orchestrator.py::test_graph_hypothesize_returns_grounded_evidence -v
pytest tests/test_arc3_orchestrator.py::test_graph_hypothesize_skips_at_step_zero -v
pytest tests/test_arc3_orchestrator.py::test_graph_hypothesize_skips_unknown_archetype -v
pytest tests/test_arc3_orchestrator.py -v
python run_single_puzzle.py
python3 -c "
import json
d = json.load(open('master_timeline.json'))
gh_events = [e for e in d if e.get('name') == 'graph_hypothesize_complete']
print(f'graph_hypothesize events: {len(gh_events)}')
for e in gh_events[:3]:
    print(e.get('what', '')[:300])
"
```

## Risks

- `recall_procedures` signature: confirm it accepts `task_id` and `query` kwargs. Check `benchmarks/arc3/adapter.py` `BrainClientProtocol`.
- `brain.current_truth` scope parameter: confirm `scope="branch"` is a valid argument.
- If `recall_relevant_lessons` returns dicts with nested `metadata` key vs flat dicts, the distillation loop must handle both — the implementation above checks both (`lesson.get("metadata") or lesson`).
- `tool_rules` in `_build_orchestration_report()` must include `"hypothesize"` in allowed_phases for `recall_relevant_lessons` and `current_truth` (verify after B203 fix).
