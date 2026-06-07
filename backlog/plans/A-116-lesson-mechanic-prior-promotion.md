# Plan: A-116 — Lesson→Mechanic Prior Promotion

## Card metadata

- **Card:** A116
- **Priority:** P2
- **Layer:** MCP/graph memory
- **Depends on:** A112
- **Intended executor:** Haiku subagent

## Summary

Synthesize raw lessons into aggregate mechanic summaries and publish them to the graph so that `recall_mechanic_priors` returns useful transfer-learning candidates for subsequent puzzles.

## Implementation approach

### Step 1: Aggregate raw lessons into summaries

In `sidequest_mcp_client/mcp_brain_client.py`, add:

```python
def _should_promote_lesson(self, lesson_id: str, lesson_data: dict) -> bool:
    """Check if lesson has sufficient evidence for promotion."""
    evidence = lesson_data.get("evidence_count", 0)
    confidence = lesson_data.get("confidence", 0)
    
    # Promote when we have multiple independent observations
    return evidence >= 3 and confidence >= 0.7

def _promote_lesson_to_mechanic(self, lesson_id: str, lesson_data: dict) -> dict:
    """Create a mechanic summary from a lesson."""
    return {
        "summary_id": f"mechanic_{lesson_id}",
        "source_lesson": lesson_id,
        "signature": {
            "action_set": lesson_data.get("actions", []),
            "effect_class": lesson_data.get("effect_type", "unknown"),
            "confidence": lesson_data.get("confidence", 0.5),
        },
        "action_patterns": lesson_data.get("action_patterns", []),
        "failure_modes": lesson_data.get("failure_modes", []),
        "success_conditions": lesson_data.get("success_conditions", []),
        "confidence": lesson_data.get("confidence", 0.5),
    }
```

### Step 2: Publish promoted summaries

In the lesson callback, check for promotion opportunities:

```python
async def _on_lesson_upserted(self, lesson_event: dict):
    """React to lesson writes by potentially promoting to mechanic."""
    lesson_id = lesson_event.get("lesson_id")
    lesson_data = lesson_event.get("data", {})
    
    if self._should_promote_lesson(lesson_id, lesson_data):
        mechanic = self._promote_lesson_to_mechanic(lesson_id, lesson_data)
        
        try:
            await self.publish_mechanic_summary(mechanic)
            logger.info("Promoted lesson %s to mechanic summary", lesson_id)
        except Exception:
            logger.debug("Mechanic promotion failed for %s", lesson_id)
```

### Step 3: Ensure recall_mechanic_priors uses promoted summaries

In MCP callbacks, verify that published mechanics are being recalled:

```python
async def recall_mechanic_priors(self, signature: dict, limit: int = 5):
    """Recall mechanics that match the signature."""
    # This should use the promoted mechanic summaries
    # Verify that the signature fields match what we publish
    try:
        results = await self.brain.recall_mechanic_priors(
            signature=signature,
            limit=limit,
        )
        return results or []
    except Exception:
        return []
```

## Concrete file edits

1. **`sidequest_mcp_client/mcp_brain_client.py`**
   - Add `_should_promote_lesson()` method
   - Add `_promote_lesson_to_mechanic()` method
   - Add `_on_lesson_upserted()` callback or similar hook
   - Call promotion logic when lessons reach evidence threshold
   - Ensure `recall_mechanic_priors()` queries can find promoted summaries

2. **`tests/test_a116_lesson_mechanic_prior_promotion.py`**
   - Test that lessons with 3+ evidence are promoted
   - Test that promoted mechanic has correct signature fields
   - Test that `recall_mechanic_priors` returns the promoted mechanic

## Tests to add or run

- `tests/test_a116_lesson_mechanic_prior_promotion.py`
- `make test-a`

## Validation commands

```bash
pytest tests/test_a116_lesson_mechanic_prior_promotion.py -v
make test-a
```

## Assumptions/defaults

- Evidence threshold is 3 observations
- Confidence threshold is 0.7
- Promoted mechanic signature includes action_set, effect_class, confidence
- Promotion does not require explicit approval
