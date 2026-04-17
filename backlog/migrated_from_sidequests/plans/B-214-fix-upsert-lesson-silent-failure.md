# B-214 — Fix upsert_lesson Silent Failure: Implementation Plan

- **Card:** backlog/B214.md
- **Priority:** P0
- **Dependencies:** None

## Summary

KuzuDB 0.11.3 rejects `MERGE` on any table that has a HNSW vector index — even for net-new nodes on `ON CREATE SET`. Plain `CREATE` works fine. Replace the `MERGE` pattern in `upsert_lesson` with a SELECT-then-CREATE-or-UPDATE pattern. Also add `logger.exception` to the three silent fallback sites in `LedgerBrainClient`.

## Root Cause (Confirmed)

`mcp_engine/tools/__init__.py:upsert_lesson` uses:
```python
MERGE (l:Lesson {lesson_id: $lid})
ON CREATE SET l.embedding = $emb, ...
ON MATCH SET  l.embedding = $emb, ...
```

KuzuDB 0.11.3 raises:
```
Runtime exception: Cannot set property vec in table embeddings because
it is used in one or more indexes. Try delete and then insert.
```

This fires even when the node doesn't exist (pure CREATE path), because `MERGE` itself is incompatible with vector-indexed tables. The exception is silently swallowed by `LedgerBrainClient.upsert_lesson`'s bare `except Exception: resp = {"lesson_id": None}`.

**Confirmed:** plain `CREATE` works; `MATCH ... SET` on non-embedding fields works; only `MERGE` on a vector-indexed table fails.

## Technical Approach

### 1. Fix `upsert_lesson` in `mcp_engine/tools/__init__.py`

Replace the single `MERGE` write with two operations: a read check then either CREATE or SET.

**Current code (lines ~1945–1974) — replace entirely:**

```python
await db.execute_write(
    """
    MERGE (l:Lesson {lesson_id: $lid})
    ON CREATE SET l.text_raw = $text,
                  l.embedding = $emb,
                  ...
    ON MATCH SET  l.text_raw = $text,
                  l.embedding = $emb,
                  ...
    """,
    {...}
)
```

**Replacement:**

```python
# KuzuDB 0.11.3: MERGE is incompatible with vector-indexed tables.
# Use SELECT→CREATE-or-UPDATE instead.
existing = await db.execute_read(
    "MATCH (l:Lesson {lesson_id: $lid}) RETURN l.lesson_id",
    {"lid": lesson_id},
)
if not existing:
    await db.execute_write(
        """
        CREATE (l:Lesson {
            lesson_id:        $lid,
            text_raw:         $text,
            embedding:        $emb,
            embedding_model:  $model,
            embedding_dim:    $dim,
            domain:           $domain,
            lesson_type:      $type,
            confidence:       0.90,
            confidence_low:   false,
            pathway_strength: 1.0,
            archived:         false,
            created_at:       timestamp($now)
        })
        """,
        {
            "lid":    lesson_id,
            "text":   text,
            "emb":    vector,
            "model":  embedding_model,
            "dim":    len(vector),
            "domain": domain,
            "type":   lesson_type,
            "now":    now,
        }
    )
else:
    # Update non-embedding fields only (embedding cannot be SET on indexed property)
    await db.execute_write(
        """
        MATCH (l:Lesson {lesson_id: $lid})
        SET l.text_raw         = $text,
            l.domain           = $domain,
            l.lesson_type      = $type,
            l.pathway_strength = l.pathway_strength + 0.1
        """,
        {
            "lid":    lesson_id,
            "text":   text,
            "domain": domain,
            "type":   lesson_type,
        }
    )
```

Keep the rest of `upsert_lesson` unchanged (session linking, return value).

### 2. Add `logger.exception` to the three silent-fallback sites in `benchmarks/arc3/adapter.py`

**Site A — `LedgerBrainClient.upsert_lesson` (around line 693):**

```python
except Exception:
    logger.exception(
        "upsert_lesson failed (LedgerBrainClient): domain=%s text_len=%d",
        domain, len(text),
    )
    resp = {"lesson_id": None}
```

**Site B — `LedgerBrainClient.upsert_lesson` fallback `store_lesson` (around line 700):**

```python
except Exception:
    logger.exception(
        "upsert_lesson store_lesson fallback failed: domain=%s", domain
    )
    resp = {"lesson_id": None}
```

**Site C — `LocalBrainClient.store_lesson` missing-handler fallback (line ~291):**

```python
# fallback
logger.error(
    "store_lesson: _store_lesson_handler not set on %s", type(self).__name__
)
return {"lesson_id": None}
```

Add `logger = logging.getLogger(__name__)` near the top of the file if not already present.

### 3. Add round-trip integration test

File: `tests/test_arc3_durable_runner.py`

Add the following test (no mock — uses real KuzuClient with temp DB):

```python
import asyncio, os, tempfile, shutil
import pytest

@pytest.mark.asyncio
async def test_upsert_lesson_round_trip():
    """B214: upsert_lesson must persist; recall_relevant_lessons must find it."""
    from mcp_engine.config import load_config
    from mcp_engine.schema import init_schema
    from mcp_engine.graph.kuzu_client import KuzuClient
    from mcp_engine.graph import embeddings as emb
    from mcp_engine.tools import upsert_lesson, recall_relevant_lessons

    SEED_PATH = str(
        (Path(__file__).resolve().parents[1] / "sidequests/data/GistSeedExamples.md")
    )

    tmp = tempfile.mkdtemp()
    db_path = os.path.join(tmp, "b214_test.db")
    try:
        config = load_config(None)
        embedding_model = config.get("embeddings", {}).get(
            "model", "sentence-transformers/all-MiniLM-L6-v2"
        )
        emb.configure(config)
        emb.prewarm(embedding_model)
        db = KuzuClient(db_path)
        init_schema(db, SEED_PATH, embedding_model)

        # --- Write ---
        result = await upsert_lesson(
            {
                "text": "space archetype: ACTION6 moves player one cell left",
                "domain": "space",
                "lesson_type": "action_effect",
                "session_id": "test-b214",
            },
            db,
            config,
        )
        assert result.get("lesson_id") is not None, (
            f"upsert_lesson returned lesson_id=None; result={result}"
        )
        assert result.get("status") == "upserted"

        # --- Read back ---
        recall = await recall_relevant_lessons(
            {"query": "space archetype action effect", "domain": "space", "limit": 5},
            db,
            config,
        )
        lessons = recall.get("lessons", [])
        assert len(lessons) >= 1, (
            f"recall_relevant_lessons returned 0 lessons after upsert; recall={recall}"
        )

        # --- Second write (update path) ---
        existing_id = result["lesson_id"]
        result2 = await upsert_lesson(
            {
                "text": "space archetype: ACTION6 moves player one cell left (revised)",
                "domain": "space",
                "lesson_type": "action_effect",
                "lesson_id": existing_id,
                "session_id": "test-b214",
            },
            db,
            config,
        )
        assert result2.get("lesson_id") == existing_id
        assert result2.get("status") == "upserted"

    finally:
        shutil.rmtree(tmp, ignore_errors=True)
```

Add `from pathlib import Path` import at the top of the test file if not already present.

## Concrete File Changes

| File | Change |
|------|--------|
| `mcp_engine/tools/__init__.py` | Replace `MERGE` block in `upsert_lesson` with SELECT→CREATE-or-UPDATE |
| `benchmarks/arc3/adapter.py` | Add `logger.exception` at Sites A, B, C |
| `tests/test_arc3_durable_runner.py` | Add `test_upsert_lesson_round_trip` |

## API/Schema/Test Updates

- Schema: no changes (Lesson node table unchanged).
- API: return value `{"lesson_id": ..., "status": "upserted"}` unchanged.
- Tests: 1 new integration test (real DB, no mocks).

## Acceptance Criteria

1. `upsert_lesson` returns a non-None `lesson_id` on a `LocalBrainClient` with a real KuzuDB.
2. `recall_relevant_lessons` immediately after returns `len(lessons) >= 1`.
3. Second call with the same `lesson_id` (update path) succeeds without error.
4. All three exception sites log at ERROR with full traceback.
5. `test_upsert_lesson_round_trip` passes.
6. Existing tests continue passing.

## Validation Commands

```
.venv/bin/python -X dev -m pytest tests/test_arc3_durable_runner.py -q -k lesson
.venv/bin/python -X dev -m pytest tests/ -q --tb=short
.venv/bin/python run_single_puzzle.py --live-smoke --num-puzzles 1
```

After smoke run, verify `master_timeline.json` shows non-None `lesson_id` values for all `upsert_lesson` entries.

## Risks / Constraints

- The `CREATE` path inserts a new row for each new `lesson_id`. Since `upsert_lesson` generates a fresh UUID when no `lesson_id` is passed, every in-run lesson write is a CREATE — this is correct and intentional.
- The update path (same `lesson_id` supplied) skips re-embedding, which is correct: embeddings don't change when only metadata is updated, and KuzuDB 0.11.3 forbids SET on vector-indexed properties anyway.
- Do NOT use `MERGE` anywhere else on `Lesson`, `Hypothesis`, `Procedure`, `Plan`, `PlanStep`, or any other table that has a HNSW vector index — the same constraint applies. Use SELECT→CREATE-or-UPDATE consistently.
