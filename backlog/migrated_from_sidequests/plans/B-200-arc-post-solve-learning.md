# Plan for B200 — ARC Agent: Post-Solve Lesson Persistence

## Card Metadata
- **Card ID**: B200
- **Priority**: P1
- **Dependencies**: B181 (outcome judge), B185 (failure taxonomy)

## Summary
After each puzzle completes, call `report_outcome` with rich context (strategy, archetype, judge verdict, failure class) and `upsert_lesson` to persist structured lessons. Closes the learning loop.

## Technical Approach

### Step 1: Add upsert_lesson to BrainClientProtocol (adapter.py)

```python
# In BrainClientProtocol (abstract base)
async def upsert_lesson(self, domain: str, text: str, valence: float,
                        confidence: float = 0.7, tags: list[str] = None) -> dict:
    """Create or update a Lesson in the brain."""
    ...

# In LedgerBrainClient
async def upsert_lesson(self, domain: str, text: str, valence: float,
                        confidence: float = 0.7, tags: list[str] = None) -> dict:
    return await self._call_tool("upsert_lesson", {
        "domain": domain, "text": text, "valence": valence,
        "confidence": confidence, "tags": tags or [],
    })

# In NoOpBrainClient
async def upsert_lesson(self, domain: str, text: str, valence: float,
                        confidence: float = 0.7, tags: list[str] = None) -> dict:
    return {"lesson_id": "noop", "created": False}
```

### Step 2: Structured outcome reporting in runner.py

After puzzle loop completes in `DurableARCRunner._run_puzzle()`:

```python
async def _report_puzzle_outcome(self, orchestrator, result, puzzle_meta):
    """Generate structured report_outcome and upsert_lesson calls."""
    archetype = orchestrator._archetype_label or "unknown"
    correct = result.get("correct", False)
    steps = result.get("steps", 0)
    judge_verdict = result.get("judge_verdict", {})  # From B181
    failure_class = result.get("failure_class", None)  # From B185

    # --- Structured report_outcome ---
    outcome_context = {
        "archetype": archetype,
        "archetype_confidence": orchestrator._archetype_confidence,
        "steps_taken": steps,
        "strategy_summary": self._summarize_strategy(orchestrator),
        "failure_class": failure_class,
        "judge_score": judge_verdict.get("overall_score", 0),
        "entity_roles_discovered": len(getattr(orchestrator, "_entity_roles", {})),
        "action_semantics_discovered": len(getattr(orchestrator, "_action_facts", {})),
    }

    valence = 1.0 if correct else (-0.8 if not judge_verdict.get("near_miss") else -0.3)
    await self.brain.call_tool("report_outcome", {
        "plan_id": orchestrator._current_plan_id,
        "valence": valence,
        "outcome_text": json.dumps(outcome_context),
    })

    # --- Explicit lesson creation ---
    if correct:
        lesson_text = (
            f"Strategy '{self._summarize_strategy(orchestrator)}' solved "
            f"{archetype} puzzle in {steps} steps"
        )
        lesson_valence = 0.9
    elif judge_verdict.get("near_miss"):
        lesson_text = (
            f"Near-miss on {archetype}: judge score {judge_verdict.get('overall_score', 0)}/5. "
            f"Strategy was partially correct but {failure_class or 'incomplete'}"
        )
        lesson_valence = -0.3
    else:
        lesson_text = (
            f"Failed {archetype} after {steps} steps. "
            f"Failure class: {failure_class or 'unknown'}. "
            f"Strategy '{self._summarize_strategy(orchestrator)}' was ineffective"
        )
        lesson_valence = -0.8

    await self.brain.upsert_lesson(
        domain=archetype,
        text=lesson_text,
        valence=lesson_valence,
        confidence=0.7 if not correct else 0.9,
        tags=[archetype, failure_class or "success", f"steps_{steps}"],
    )
```

### Step 3: Strategy summarizer helper

```python
def _summarize_strategy(self, orchestrator) -> str:
    """One-line summary of what strategy the orchestrator used."""
    chunks = getattr(orchestrator, "_plan_chunks", [])
    if not chunks:
        return "no-plan"
    phases = set(c.source for c in chunks if hasattr(c, "source"))
    if "procedure" in phases:
        return f"procedure-guided ({len(chunks)} chunks)"
    return f"{chunks[0].action}-first ({len(chunks)} chunks)"
```

### Step 4: Tests

Create `tests/test_b200_post_solve_learning.py`:
1. Test successful puzzle → positive-valence Lesson created with strategy details
2. Test failed puzzle → negative-valence Lesson with failure class
3. Test near-miss puzzle (judge score >= 3) → mild-negative Lesson noting partial success
4. Test report_outcome called with structured context (archetype, steps, judge score)
5. Test Lesson domain = archetype for recall by `recall_relevant_lessons`
6. Test NoOpBrainClient handles all calls without error
7. Test upsert_lesson creates Lesson with correct tags

## Verification
```bash
pytest tests/test_b200_post_solve_learning.py -v
```
