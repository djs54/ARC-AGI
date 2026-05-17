# Plan: A-050 — Graph-Aware Hybrid Pattern Matcher (Replaces Bugged Pixel-Diff)

## Card metadata

- **Card:** A050
- **Priority:** P0
- **Layer:** ARC runtime
- **Depends on:** A043 (superseded); sibling-repo card for `recall_scene_graph_priors` MCP tool (optional, not blocking for Steps A–E)

## Summary

Debug and replace the static high pattern-match similarity signal that forces finish mode from step 0. Root cause is a pixel-level matcher that hardcodes `similarity = 1.0` in two paths whenever the goal region is empty / background / admits a trivial color permutation. Fix by:

1. Removing both `= 1.0` cliffs in `GridDiffEngine.compare_regions` so "no pattern detected" stops being indistinguishable from "perfect match."
2. Introducing a graph-aware hybrid matcher (`HybridPatternMatcher`) that combines three independent evidence channels — local scene-graph GED, graph-text recall keyed on Weisfeiler-Lehman (WL) canonical hash, and graph-vector analogical search using WL-histogram sparse vectors.
3. Fusing the channels with a disagreement-penalty policy and requiring multi-channel corroboration before finish-mode can engage.

Runtime stays MCP-only. All brain retrieval goes through existing MCP tools (`recall_lessons`, `recall_plans`, `recall_procedures`, `analogical_search`, `current_truth`) with an added `scene_wl_hash` facet passed through the client. An optional sibling-repo tool (`recall_scene_graph_priors`) is wired as a no-op until it lands.

## Fit / model choice (graph-solutions)

- **Fit:** Strong graph fit. This is a relationship-heavy multi-hop retrieval problem: current scene → structurally similar prior scenes → action/outcome patterns → progress priors. Pixel comparison cannot express this. Labeled property graphs plus vector-alongside-node storage are the correct substrate.
- **Model:** Per-frame labeled property graph (connected components as typed nodes, spatial / structural relations as typed edges). Canonical labeling via Weisfeiler-Lehman color refinement. GED for local progress. WL-kernel cosine similarity for the vector channel. Multi-hop Cypher for priors (brain-side).
- **Runtime boundary:** ARC runtime stays MCP-only. Scene-graph construction and WL hashing are pure Python in the runtime. All graph queries go through the existing MCP tools; the WL hash is passed as a new facet/kwarg.

## Why the regression happens (precise)

`agents/arc3/grid_analysis.py:142` (`GridDiffEngine.compare_regions`) contains two code paths that collapse to `similarity = 1.0` without regard to actual cell-matching:

**Path A — trivially consistent color mapping (lines 232–238):**
```python
shift_result = None
if not exact and allow_color_shift and not inconsistent_shift:
    shift_result = mapping
    similarity = 1.0  # 100% structural similarity with color shift
```
A "consistent" mapping just means every color in A maps to one color in B. On any near-empty region, `{0: 0}` satisfies this, triggering a hardcoded `1.0`.

**Path B — empty-foreground fallback (lines 222–229):**
```python
if fg_total > 0:
    similarity = fg_matching / fg_total
else:
    similarity = all_matching / all_total if all_total > 0 else 0
```
When both regions are pure background, this returns `1.0`.

Either path is sufficient to trigger finish mode on step 0, and both fire across puzzles regardless of archetype.

## Implementation approach

### Step A — Kill the two `= 1.0` cliffs (unblocks everything else)

Edit `agents/arc3/grid_analysis.py`:

- Replace the `fg_total == 0` all-cell fallback with: return `RegionComparison(similarity=None, exact_match=False, cells_matching=0, cells_total=0, description="no foreground")`. Callers must treat `None` as "undefined / unknown," not "perfect."
- Remove the `similarity = 1.0` color-shift override. Keep `shift_result = mapping` for telemetry, but set `similarity = cells_matching / cells_total` (the real foreground-match ratio) and include `color_shifted = True` in the result.
- Update `RegionComparison` dataclass: `similarity: Optional[float]` (was `float`). Callers updated to handle `None`.

Update `PatternMatchTracker.update` in `agents/arc3/solver.py`:

- If `comparison.similarity is None`, return `{"phase": "discover", "similarity": None, "reason": "no_foreground"}` — do not enter finish mode.
- Remove the old `>= 0.9 and step >= 1 and goal_changed_steps >= 1` heuristic entirely. It was a patch on top of the bug.

This step alone breaks the constant-1.0 regression without introducing any graph machinery. **Ship it first** so downstream steps are built on a fixed baseline.

### Step B — Scene-graph builder + WL hashing (pure Python, no MCP change)

New file `agents/arc3/scene_graph.py`:

```python
# Public API (deterministic, no randomness)
def build_scene_graph(grid: List[List[int]], background: int = 0) -> SceneGraph: ...
def wl_canonical_hash(sg: SceneGraph, iterations: int = 3) -> str: ...
def wl_histogram_vector(sg: SceneGraph, iterations: int = 3) -> Dict[str, int]: ...
def approximate_ged(a: SceneGraph, b: SceneGraph, beam_width: int = 8, depth_cap: int = 4) -> float: ...
def normalized_ged(a: SceneGraph, b: SceneGraph) -> float: ...  # in [0, 1]
```

**Node schema:**

- `color: int`
- `area: int`
- `bbox: (min_r, min_c, max_r, max_c)`
- `centroid: (float, float)`
- `shape_hash: str` — WL hash of the component's internal 4-adjacency cell graph, color-stripped, rotation/reflection canonicalized (try all 8 dihedral orientations, take lex-min)
- `location_hint: str` — reuses existing logic in `GridDiffEngine.extract_pattern_regions`

**Edge types (typed property edges):**

- `adj(u, v)` — components have touching cells (8-neighbor or 4-neighbor, decide and document; default 4-neighbor)
- `contains(u, v)` — component u's bbox strictly contains v's bbox
- `same_color(u, v)`
- `same_shape(u, v)` — `u.shape_hash == v.shape_hash`
- `h_aligned(u, v)` — centroids share a row band (tolerance 1)
- `v_aligned(u, v)` — centroids share a column band
- `east_of(u, v)`, `south_of(u, v)` — directional, useful for motif matching later

**WL canonical hash:**

Standard color refinement: at each iteration, each node's new label is a hash of `(current_label, sorted_tuple_of_neighbor_labels_with_edge_type)`. After `iterations` rounds, the scene-graph hash is a hash of the sorted multiset of final node labels. Deterministic. Isomorphism-invariant (with high probability; known WL limitations on rare corner cases, acceptable here).

**WL-histogram vector:**

Return a sparse dict mapping `subtree_label -> count` summed over WL iterations 0..N. Used as the graph-vector channel input; cosine similarity between two such dicts is the standard **Weisfeiler-Lehman subtree kernel**. No training required.

**Approximate GED:**

Beam-search-bounded A*-style GED. Operations: node insertion, deletion, relabeling; edge insertion, deletion, relabeling. Cost of 1 per op. Cap beam width and depth to keep runtime sub-millisecond on ARC-sized grids (typical ≤ 30 nodes). Falls back to a trivial degree-sequence lower-bound when the cap is hit. `normalized_ged(a, b) = ged / (nodes(a) + nodes(b) + edges(a) + edges(b))` clamped to `[0, 1]`.

**Tests** (`tests/test_scene_graph.py`):

- `build_scene_graph` correctness on 3 synthetic grids (empty, single region, multi-region)
- `wl_canonical_hash` determinism (same grid → same hash) and isomorphism invariance (relabel colors → same hash if color-stripped; rotate grid → same hash since shape_hash is dihedral-canonical)
- `wl_histogram_vector` cosine similarity properties (self-similarity = 1, disjoint = 0, permuted = 1)
- `approximate_ged` monotonicity on a hand-constructed trajectory where each step adds exactly one node

### Step C — `HybridPatternMatcher` with three channels

New class in `agents/arc3/solver.py` (replaces `PatternMatchTracker`):

```python
@dataclass
class HybridProgressEvidence:
    # Local channel
    local_progress: Optional[float]        # 1 - normalized_ged(sg_t, sg_0)
    local_distance: Optional[float]        # normalized_ged(sg_t, sg_target) if target known, else None
    local_monotone_steps: int              # consecutive non-decreasing local_progress
    scene_wl_hash: str
    scene_node_count: int

    # Graph-text channel (MCP recall keyed on WL hash)
    graph_text_score: Optional[float]      # top-lesson valence-weighted match score
    graph_text_evidence_count: int
    graph_text_top_lesson_ids: List[str]

    # Graph-vector channel (WL-histogram cosine)
    graph_vector_score: Optional[float]
    graph_vector_top_hash: Optional[str]
    graph_vector_top_trajectory_id: Optional[str]

    # Fusion
    combined_similarity: Optional[float]
    combined_confidence: float             # [0, 1]
    channel_agreement_range: float         # max - min across non-None channels
    finish_mode_allowed: bool
    phase: str                             # "discover" | "intermediate" | "finish"
    reason: str                            # human-readable fusion rationale

class HybridPatternMatcher:
    def __init__(self, brain_client, config: HybridPatternConfig): ...
    async def update(self, grid, step, session_id, task_id, archetype) -> HybridProgressEvidence: ...
```

**Update flow per step:**

1. Build `scene_graph_t` from current grid. Compute `wl_hash_t` and `wl_vec_t`. Cache `scene_graph_0` on step 0.
2. Local channel:
   - `local_progress = 1 - normalized_ged(scene_graph_t, scene_graph_0)`
   - `local_distance = 1 - normalized_ged(scene_graph_t, scene_graph_target)` if target is known (currently `None`; future work can identify target scene graph from reference region in `GridDiffEngine.find_reference_goal_pair`, but not required for this card)
   - `local_monotone_steps` tracked in instance state; reset on decrease
3. Graph-text channel (only when `brain_client.memory_degraded` is False):
   - `resp = await brain_client.recall_lessons(lesson_type="pattern_state", scene_wl_hash=wl_hash_t, archetype=archetype, limit=5)`
   - Score = valence-weighted mean of returned lesson match scores
   - Evidence count = len(resp.get("lessons", []))
   - Fallback: if zero hits on WL-hash, retry with archetype-only facet, scoring penalized by factor 0.5
4. Graph-vector channel:
   - `resp = await brain_client.analogical_search(vector=wl_vec_t, current_quest_id=task_id, limit=5, min_similarity=0.4)`
   - Score = top result's similarity; top_hash = returned scene_wl_hash if present
5. Fusion (see Step D).
6. Phase assignment (see Step E).
7. Log all fields to trace (see Step G).

### Step D — Disagreement-penalty fusion

Not a weighted average. Explicit rule:

```python
def fuse(local, graph_text, graph_vector, config):
    channels = [c for c in (local, graph_text, graph_vector) if c is not None]
    if not channels:
        return None, 0.0, 0.0  # combined, confidence, range
    agreement_range = max(channels) - min(channels)
    if agreement_range > config.disagreement_threshold:   # default 0.4
        # channels disagree — conservative; block finish mode
        combined   = min(channels)
        confidence = 0.2
    else:
        # channels agree — weighted mean
        combined   = (
            config.w_local   * (local        if local        is not None else min(channels)) +
            config.w_text    * (graph_text   if graph_text   is not None else min(channels)) +
            config.w_vector  * (graph_vector if graph_vector is not None else min(channels))
        )
        # confidence scales with (1 - range) and with number of live channels
        confidence = (1 - agreement_range) * (len(channels) / 3.0)
    return combined, confidence, agreement_range
```

Default weights: `w_local=0.5, w_text=0.3, w_vector=0.2`. Tunable via `HybridPatternConfig`.

### Step E — Finish-mode gate (graph-query form, not scalar threshold)

Replace the removed `comparison.similarity >= 0.9 and step >= 1 and goal_changed_steps >= 1` heuristic with:

```python
finish_mode_allowed = (
    step >= config.min_step_for_finish                         # default 2
    and len(live_channels) >= 2                                # at least two channels returned a score
    and combined_confidence >= config.min_confidence_for_finish # default 0.6
    and (
        # Either: local progress is monotone-non-decreasing and high
        (local_monotone_steps >= config.min_monotone_steps     # default 3
         and local_progress >= config.min_local_progress_for_finish)  # default 0.7
        # Or: graph-text has direct evidence of a matching solved-state lesson
        or (graph_text_evidence_count >= config.min_text_evidence  # default 2
            and graph_text_score >= config.min_text_score_for_finish)  # default 0.75
    )
)
```

Phase assignment:

- `step == 0` OR `finish_mode_allowed == False` AND `local_progress is None or local_progress < config.intermediate_threshold` → `"discover"`
- `local_progress is not None and 0 < local_progress < config.finish_threshold` → `"intermediate"`
- `finish_mode_allowed == True` → `"finish"`

Explicitly: **`phase=finish` on step 0 is impossible** under this gate.

### Step F — Stale-cache guard (plan hypothesis 3 from the original)

If `scene_wl_hash` has not changed for N consecutive steps (default N=3), invalidate any cached graph-text and graph-vector scores and re-query. Prevents the "cached bootstrap value never overwritten" hypothesis.

Additionally, if `scene_wl_hash` changed this step but graph-text or graph-vector returned identical top hits as last step, log a `retrieval_stale_suspected` trace event (warning only; does not fail the run).

### Step G — Trace visibility (acceptance criterion 4)

Every `pattern_match_progress` trace event must include:

```
scene_wl_hash:             str
scene_node_count:          int
local_progress:            Optional[float]
local_distance:            Optional[float]
local_monotone_steps:      int
graph_text_score:          Optional[float]
graph_text_evidence_count: int
graph_text_top_lesson_ids: List[str]        (deterministic ids only, no free text)
graph_vector_score:        Optional[float]
graph_vector_top_hash:     Optional[str]
graph_vector_top_trajectory_id: Optional[str]
combined_similarity:       Optional[float]
combined_confidence:       float
channel_agreement_range:   float
finish_mode_allowed:       bool
phase:                     str
reason:                    str
```

This is the diagnostic payload future smokes need to debug fusion behavior. Do not omit fields.

### Step H (nice-to-have follow-up; NOT in A050 scope)

Motif / subgraph-isomorphism-based plan retrieval. Once scene graphs exist, mining motifs from solved-puzzle lesson corpora and matching them into the current graph turns pattern-match into a plan-retriever. Spin as a separate card (`A05X — Subgraph-motif plan retrieval`) after A050 ships.

Also out of scope: node2vec / GraphSAGE learned embeddings. WL-histogram cosine is the cheap default; learned embeddings are a follow-up if telemetry shows the sparse kernel is insufficient.

## Concrete file edits

New files:

- `agents/arc3/scene_graph.py`
- `tests/test_scene_graph.py`

Edited files:

- `agents/arc3/grid_analysis.py` — remove two `= 1.0` cliffs; `RegionComparison.similarity` becomes `Optional[float]`
- `agents/arc3/solver.py` — remove `PatternMatchTracker`, add `HybridPatternMatcher` + `HybridProgressEvidence` + `HybridPatternConfig`
- `agents/arc3/orchestrator.py` — consume `HybridProgressEvidence`; log channel-disagreement; enforce finish-mode gate at phase-transition site
- `agents/arc3/runner.py` — thread `scene_wl_hash`, `scene_node_count`, and all fusion fields into `perceive_step_response` payload
- `sidequest_mcp_client/mcp_brain_client.py`:
  - `recall_lessons`: accept optional `scene_wl_hash`, `archetype` kwargs; passthrough only; no SideQuests imports
  - `analogical_search`: accept optional `vector` kwarg; when provided, override `query` handling and pass the precomputed sparse vector through the MCP payload (brain side decides how to match)
  - Add `async def recall_scene_graph_priors(self, *, scene_wl_hash, archetype, min_valence, limit) -> Dict`. Wrapper is a no-op returning `{"status": "tool_unavailable", "priors": []}` until the sibling repo exposes the tool; `HybridPatternMatcher` treats this as "channel unavailable" and continues with two channels.

Tests:

- `tests/test_a043_pattern_match_static_confidence.py` — rewrite to assert:
  - On synthetic fixture where reference region is empty, `RegionComparison.similarity is None`
  - On synthetic fixture where color mapping is trivially consistent, `similarity` is the real ratio, not `1.0`
  - `PatternMatchTracker` legacy heuristic is gone
  - `HybridPatternMatcher` returns non-constant `combined_similarity` across a 10-step synthetic trajectory
- `tests/test_arc3_solver.py` — new cases for `HybridPatternMatcher`:
  - Empty grid → phase `"discover"`, `combined_similarity is None`, `finish_mode_allowed is False`
  - 10-step synthetic monotone progress → `local_monotone_steps` increments, `phase` transitions `discover` → `intermediate` → `finish` at or after step `min_step_for_finish`
  - Channel disagreement fixture (mock `recall_lessons` returns high score while local says low) → `combined_confidence <= 0.2`, `finish_mode_allowed is False`
  - MCP degraded mode → two channels off, matcher uses local only with capped confidence, `finish_mode_allowed is False` until `min_local_steps_for_finish` elapses
- `tests/test_arc3_orchestrator.py` — finish-mode gate:
  - Step 0 under any evidence → finish-mode blocked
  - Two-channel agreement at step 3 with high confidence → finish-mode allowed
  - Single-channel spike (local=0.95, others unavailable) at step 3 → finish-mode blocked (fewer than 2 live channels)
- `tests/test_scene_graph.py` — as above in Step B
- `tests/test_mcp_brain_client.py` — cover new kwargs:
  - `recall_lessons(scene_wl_hash=..., archetype=...)` passthrough behaves identically when kwargs are omitted
  - `analogical_search(vector=...)` sends the vector in payload and accepts the brain's response
  - `recall_scene_graph_priors` returns the no-op shape when upstream is unavailable

## API / interface changes

- `RegionComparison.similarity`: `float → Optional[float]`. Callers must handle `None`.
- `PatternMatchTracker` removed. Replaced by `HybridPatternMatcher`. Any external callers (search `rg -n "PatternMatchTracker"`) must be updated.
- `mcp_brain_client.recall_lessons` gains optional `scene_wl_hash: Optional[str] = None, archetype: Optional[str] = None`.
- `mcp_brain_client.analogical_search` gains optional `vector: Optional[Mapping[str, int]] = None`.
- `mcp_brain_client.recall_scene_graph_priors` is added; no-op until sibling tool lands.

All new kwargs are optional with `None` defaults, so callers not yet updated keep working.

## Sibling-repo dependency (do NOT vendor; track as a sibling card)

The graph-text channel becomes dramatically more informative when the brain exposes a dedicated tool and schema:

- New MCP tool `recall_scene_graph_priors(scene_wl_hash, archetype, min_valence, limit)` returning `{priors: [...], expected_progress, median_progress, evidence_count}`.
- Schema additive: `Lesson.scene_wl_hash: str (indexed)`, `Lesson.scene_graph_vector: Map<str, int>` (optional), `Frame.wl_hash: str (indexed)`, `Frame.completion_percentile: float` (optional, back-filled from outcomes).
- Cypher-style query template (brain side):

  ```cypher
  MATCH (p:Puzzle)-[:HAS_FRAME]->(f:Frame {wl_hash: $wl_hash})
        -[:IN_TRAJECTORY]->(t:Trajectory)-[:TERMINAL]->(o:Outcome)
  WHERE p.archetype = $archetype AND t.valence >= $min_valence
  RETURN avg(f.completion_percentile)                     AS expected_progress,
         percentileCont(f.completion_percentile, 0.5)     AS median_progress,
         count(*)                                         AS evidence_count
  ```

This card does **not** block on the sibling change. `HybridPatternMatcher` degrades gracefully to two channels (local + graph-text via WL-hash facet on `recall_lessons`) when `recall_scene_graph_priors` is unavailable. When the sibling tool lands, the matcher automatically picks it up via the `recall_scene_graph_priors` wrapper.

Track the sibling work as a separate card titled "Scene-graph priors MCP tool + schema for ARC A050" (sibling tracker, not A-series here).

## Tests to run

Targeted (ARC runtime):

```bash
pytest -q tests/test_scene_graph.py
pytest -q tests/test_a043_pattern_match_static_confidence.py
pytest -q tests/test_arc3_solver.py
pytest -q tests/test_arc3_orchestrator.py
pytest -q tests/test_mcp_brain_client.py
```

Boundary:

```bash
pytest -q tests/test_import_boundary.py
```

A-series green baseline:

```bash
make test-a
```

Live smoke (requires brain daemon + `SIDEQUESTS_MCP_CMD`):

```bash
python run_single_puzzle.py --live-smoke --num-puzzles 1 --max-steps 30 \
    --card-id a050_pattern_match_regression
```

## Validation commands

Confirm the cliffs are gone:

```bash
rg -n "similarity\s*=\s*1\.0" agents/arc3/grid_analysis.py
# expect: zero matches outside test files asserting the old behavior is removed
```

Confirm hybrid signal in a fresh smoke trace:

```bash
rg -n "scene_wl_hash|combined_similarity|combined_confidence|channel_agreement_range|finish_mode_allowed" \
    agent_execution_trace.json submission_results_single.live.jsonl
```

Confirm `phase=finish` never appears at step 0:

```bash
python3 - <<'EOF'
import json
with open("master_timeline.json") as f:
    tl = json.load(f)
bad = [e for e in tl
       if e.get("name") == "pattern_match_progress"
       and (e.get("data") or {}).get("step") == 0
       and (e.get("data") or {}).get("phase") == "finish"]
assert not bad, f"phase=finish at step 0: {len(bad)} events"
print("ok: no step-0 finish-mode")
EOF
```

Confirm decision-source share:

```bash
python3 - <<'EOF'
import json, collections
with open("master_timeline.json") as f:
    tl = json.load(f)
sources = collections.Counter(
    (e.get("result") or {}).get("decision_source")
    for e in tl if e.get("name") == "act" and e.get("event") == "phase_end"
)
total = sum(sources.values())
ap = sources.get("autopilot", 0)
share = ap / total if total else 0
assert share < 0.4, f"autopilot share too high: {share:.0%} ({ap}/{total})"
print(f"ok: autopilot share {share:.0%}")
EOF
```

## Assumptions / defaults

- `HybridPatternConfig` defaults (tunable):
  - `disagreement_threshold = 0.4`
  - `w_local = 0.5, w_text = 0.3, w_vector = 0.2`
  - `min_step_for_finish = 2`
  - `min_confidence_for_finish = 0.6`
  - `min_monotone_steps = 3`
  - `min_local_progress_for_finish = 0.7`
  - `min_text_evidence = 2`
  - `min_text_score_for_finish = 0.75`
  - `intermediate_threshold = 0.2`
  - `finish_threshold = 0.7`
  - `min_local_steps_for_finish = 5`  # degraded-mode gate
- WL iterations: `3` (sufficient for ARC-sized grids; tunable via config)
- GED beam width `8`, depth cap `4`; falls back to degree-sequence lower-bound on cap
- Default adjacency: 4-neighbor
- Shape-hash canonicalization: lex-min over 8 dihedral orientations
- All new MCP kwargs are optional; behavior unchanged for callers that omit them
- Graph retrieval consumed through MCP tools only; no direct runtime graph imports from `sidequests.*` or `mcp_engine.*`
- `recall_scene_graph_priors` returns "tool_unavailable" until the sibling repo ships; `HybridPatternMatcher` treats this as the channel being off
- Token/cost impact: ≤ 2 new MCP tool calls per step (one facet `recall_lessons`, one `analogical_search`); no new LLM calls; payload size bounded by WL-histogram sparse vector (typically < 1 KB)
- Green baseline: `make test-a` (18/18). This card must not regress it
- This card is the primary unblocker for A051–A057; downstream cards should be re-evaluated after A050 lands

## Done criteria

A050 moves to `complete` when all of the following are true:

1. All acceptance criteria from `A050.md` pass
2. Targeted test files listed above are green
3. `tests/test_import_boundary.py` passes
4. `make test-a` passes (18/18)
5. Live smoke produces a trace where `phase=finish` never appears at step 0 and `decision_source=autopilot` share is below 40%
6. Validation note appended per `BacklogRules.md` §11
