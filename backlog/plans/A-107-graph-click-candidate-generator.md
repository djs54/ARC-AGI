# Plan: A-107 — Graph click candidate generator

## Card metadata

- **Card:** A107
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A101, A102, A106

## Summary

Create graph-backed `ClickableCandidate` records from mechanic graph objects and relations so click-only games get concrete coordinate experiments.

## Implementation approach

1. Add `agents/arc3/click_candidates.py` with:
   - `ClickableCandidate` dataclass.
   - `ClickCandidateGenerator.generate(snapshot, active_goal_hypotheses, limit=32)`.
2. Candidate sources:
   - object centroid.
   - color-block center.
   - framed/gray/white target center.
   - panel center.
   - relation endpoint for `MISMATCH`, `MATCHES_COLOR`, `CANDIDATE_TARGET`, `INSIDE_OR_OVERLAPS`, `CENTER_OF`.
   - active goal support objects.
3. Candidate graph model in `WorldModelGraph`:
   - `(:ClickableCandidate {id, x, y, color, role, confidence, rank})`
   - `(:ClickableCandidate)-[:POINTS_TO]->(:MechanicObject)`
   - `(:ClickableCandidate)-[:IN_PANEL]->(:PatternPanel)` when available.
   - `(:ClickableCandidate)-[:SUPPORTED_BY]->(:GoalHypothesis|:MechanicRelation)`
4. Deduplicate by coordinate:
   - Merge evidence paths.
   - Keep max confidence.
   - Prefer candidates tied to active goal hypotheses.
5. Boundedness:
   - Limit candidates per frame to 32.
   - Limit evidence path ids per candidate to 6.
   - Do not store full grids on candidate nodes.

## Concrete file additions/edits

- Add `agents/arc3/click_candidates.py`
- Edit `agents/arc3/world_model.py`
- Edit `agents/arc3/mechanic_graph.py`
- Edit `agents/arc3/orchestrator.py`
- Edit `benchmarks/arc3/world_model_eval.py`
- Add `tests/test_a107_graph_click_candidate_generator.py`

## API/interface changes

```python
@dataclass
class ClickableCandidate:
    id: str
    x: int
    y: int
    color: int | None
    role: str
    confidence: float
    rank: int
    source_object_id: str | None = None
    panel_id: str | None = None
    goal_type: str | None = None
    evidence_path_ids: list[str] = field(default_factory=list)
```

World-model API:

```python
def upsert_click_candidates(self, candidates: list[ClickableCandidate], frame_hash: str) -> None: ...
def get_click_candidates(self, goal_type: str | None = None, limit: int = 16) -> list[dict]: ...
```

## Tests to add or run

```bash
.venv/bin/python -m pytest -q tests/test_a107_graph_click_candidate_generator.py
.venv/bin/python -m pytest -q tests/test_a101_goal_hypothesis_induction.py tests/test_a102_object_mechanic_graph_extraction.py tests/test_import_boundary.py
make test-a
```

## Assumptions/defaults

- Coordinates use the same grid coordinate convention already used by `ACTION6`.
- If both screen pixels and grid cells exist, use environment grid coordinates, not browser screenshot pixels.
- Preserve the MCP seam; this is graph-local runtime work.
