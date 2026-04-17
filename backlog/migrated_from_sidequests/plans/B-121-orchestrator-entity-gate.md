# B-121 - Orchestrator Entity Discovery Gate and Failure Handling

## Metadata

- Card: B121
- Priority: P1
- Dependencies: B119

## Summary

Add orchestrator enforcement that entity discovery completes successfully before advancing past
bootstrap. If entity mapping returns all UNKNOWN on a multi-color grid, retry with bounded
attempts. Log all gate results for debugging.

## Technical Approach

1. Add `_check_entity_gate()` to orchestrator — validates entity map after bootstrap scan.
2. If gate fails on a multi-color grid, re-run `_bootstrap_entity_discovery()` (max 2 retries).
3. If still failing after retries, log failure reason and proceed with degraded context (do not
   crash).
4. Record gate result in write trace.
5. Add `entity_gate_status` to `orchestration_report`.

### Gate logic

```
if len(colors) <= 1:
    gate = SKIP (single-color grid, no entities expected)
elif any role in entity_map is not UNKNOWN:
    gate = PASS
else:
    gate = FAIL → retry
```

After max retries:
```
gate = DEGRADED (logged, non-fatal)
```

## Concrete File Changes

### `agents/arc3/orchestrator.py`

Add method:

```python
def _check_entity_gate(self, observation: ARC3Observation) -> dict:
    """Check entity discovery completeness.

    Returns:
        {"status": "pass"|"skip"|"fail"|"degraded",
         "reason": str,
         "retry_count": int}
    """
    colors = observation.get("colors", [])
    non_bg_colors = [c for c in colors
                     if (c["value"] if isinstance(c, dict) else c) != 0]

    if len(non_bg_colors) <= 0:
        return {"status": "skip", "reason": "single-color grid", "retry_count": 0}

    if not self._entity_map:
        return {"status": "fail", "reason": "entity map empty", "retry_count": 0}

    has_known = any(
        info["role"] != "unknown" for info in self._entity_map.values()
    )
    if has_known:
        return {"status": "pass", "reason": "entity roles identified", "retry_count": 0}

    return {"status": "fail", "reason": "all roles UNKNOWN", "retry_count": 0}
```

Update `perceive()` to use gate after bootstrap entity discovery:

```python
if step == 0:
    await self._bootstrap_entity_discovery(observation)

    # Entity gate enforcement
    max_entity_retries = 2
    gate_result = self._check_entity_gate(observation)
    retry_count = 0
    while gate_result["status"] == "fail" and retry_count < max_entity_retries:
        retry_count += 1
        logger.warning(
            "Entity gate failed (attempt %d/%d): %s — retrying",
            retry_count, max_entity_retries, gate_result["reason"],
        )
        await self._bootstrap_entity_discovery(observation)
        gate_result = self._check_entity_gate(observation)

    gate_result["retry_count"] = retry_count
    if gate_result["status"] == "fail":
        gate_result["status"] = "degraded"
        gate_result["reason"] = f"entity discovery failed after {retry_count} retries"
        logger.warning("Entity gate degraded: %s", gate_result["reason"])

    self._entity_gate_result = gate_result
    self._record_write_event(
        kind="entity_gate",
        summary=f"Entity gate: {gate_result['status']} ({gate_result['reason']})",
        detail=gate_result,
    )
```

Update `__init__` to initialize `self._entity_gate_result = {}`.

### `agents/arc3/runner.py`

Update `_build_orchestration_report()` (or equivalent) to include entity gate:

```python
# In orchestration_report dict:
"entity_gate_status": getattr(orchestrator, "_entity_gate_result", {}),
```

### `tests/test_arc3_orchestrator.py`

Add tests:

```python
def test_entity_gate_pass_multi_color():
    """Gate passes when entity map has non-UNKNOWN roles."""

def test_entity_gate_skip_single_color():
    """Gate skips when grid has only background color."""

def test_entity_gate_fail_then_retry():
    """Gate retries when all roles are UNKNOWN on multi-color grid."""

def test_entity_gate_degrade_after_max_retries():
    """Gate degrades after max retries, does not crash."""
```

### `tests/test_arc3_durable_runner.py`

Add tests:

```python
def test_entity_gate_in_bootstrap_write_trace():
    """Entity gate event appears in bootstrap_write_trace."""

def test_entity_gate_in_orchestration_report():
    """orchestration_report includes entity_gate_status."""
```

## Validation Commands

```bash
pytest -q tests/test_arc3_orchestrator.py -k "entity_gate"
pytest -q tests/test_arc3_durable_runner.py -k "entity_gate"
pytest -q tests/test_arc3_orchestrator.py tests/test_arc3_durable_runner.py
```

## Risks / Constraints

- Retries call `_bootstrap_entity_discovery()` again with the same observation. Since the scan is
  geometry-only and deterministic, retries will return the same result unless the observation
  changes. In practice, this means retries are useful when paired with a re-fetch of the initial
  frame. For v1, keep retries simple (re-run same scan). A future card can add re-fetch logic.
- Max 2 retries. Do NOT make this unbounded.
- Gate degradation is non-fatal. The system proceeds with empty/unknown entity map and logs why.
- Do NOT change the existing phase transition logic. The gate is an enforcement check, not a new
  phase.

## Outcome

The orchestrator guarantees entity awareness before reasoning begins. Failures are visible in
write trace and orchestration report. The system degrades gracefully instead of running blind.
