# B-88-hypothesis-engine — Game-Theory-Driven Hypothesis Engine for ARC

**Card:** B88 | **Priority:** P14 | **Depends on:** B87 (orchestrator ✅), B18 (working memory ✅)

## Summary

Add a Hypothesis Engine between Perceive and Plan in the ARC orchestrator. The agent systematically forms, tests, and prunes hypotheses about game rules using an ephemeral in-memory state graph (visuospatial sketchpad), then distills confirmed/refuted knowledge into SideQuests (Kùzu) for cross-game learning.

---

## Technical Approach

### Architecture: Three Memory Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  Layer 1: State Transition Graph (in-memory, per attempt)       │
│  ┌──────┐  ACTION3  ┌──────┐  ACTION1  ┌──────┐               │
│  │ S_0  │──────────→│ S_1  │──────────→│ S_2  │               │
│  │ ab3f │           │ 7c21 │           │ ab3f │ ← LOOP!       │
│  └──────┘           └──────┘           └──────┘               │
│  Python dict-of-dicts. Destroyed on retry. ~100s nodes/puzzle. │
├─────────────────────────────────────────────────────────────────┤
│  Layer 2: Hypothesis Tracker (in-memory, survives retries)      │
│  H1: "ACTION3 moves player right"        confidence=0.85  ✓    │
│  H2: "Row 61 is energy bar"              confidence=0.60  ?    │
│  H3: "INT<10> blocks movement"           confidence=0.92  ✓    │
│  H4: "ACTION5 opens doors"               confidence=0.15  ✗    │
│  Lives in HypothesisManager. Flushed to Kùzu on WIN/GAME_OVER. │
├─────────────────────────────────────────────────────────────────┤
│  Layer 3: SideQuests / Kùzu (durable, cross-game)              │
│  Hypothesis nodes with embeddings → analogical_search finds    │
│  "In LockSmith, INT<10>=wall" when playing a new game.         │
│  Connected via HYPOTHESIZED_IN, CONFIRMS, CONTRADICTS edges.   │
└─────────────────────────────────────────────────────────────────┘
```

### New Loop: Perceive → Hypothesize → Plan → Act → Evaluate

The `hypothesize()` step:

1. **Update state graph** — add the latest frame as a StateNode, add the transition edge from the previous state
2. **Detect invariants** — compare the new frame against stored frames; find regions that never/always change
3. **Generate hypotheses** — from the transition diff, propose what the action did ("ACTION3 shifted player 4px right")
4. **Update confidence** — evidence for/against existing hypotheses based on this transition
5. **Prune/confirm** — confidence < 0.2 → prune, confidence > 0.8 → confirmed
6. **Detect loops** — if grid_hash matches a previous StateNode, warn about looping
7. **Select policy** — explore (test unconfirmed hypothesis) or exploit (use confirmed ones)

---

## File-Level Implementation Plan

### NEW: `agents/arc3/hypothesis.py`

```python
"""Hypothesis Engine — game-theory-driven puzzle reasoning.

Three core components:
  StateGraph         — ephemeral in-memory directed graph of game states
  InvariantDetector  — finds static vs dynamic grid regions
  HypothesisManager  — generates, tracks, prunes, and distills hypotheses
"""

from __future__ import annotations
import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


# ── Data Structures ──────────────────────────────────────────────────

@dataclass
class StateNode:
    """One observed game state."""
    grid_hash: str                    # SHA-256 of flattened grid
    step: int                         # global step number when observed
    key_features: Dict[str, Any]      # color_counts, shape_count, dominant_color
    energy_estimate: Optional[float]  # from InvariantDetector HUD analysis
    grid_snapshot: List[List[int]]    # flattened 2D (first layer only) for diff

    @staticmethod
    def hash_grid(grid: List[List[List[int]]]) -> str:
        flat = str([row for layer in grid for row in layer])
        return hashlib.sha256(flat.encode()).hexdigest()[:16]


@dataclass
class Transition:
    """One observed state→state edge."""
    from_hash: str
    to_hash: str
    action: str                       # e.g. "ACTION3"
    step: int
    diff_summary: str                 # human-readable diff
    pixels_changed: int               # count of changed cells
    regions_changed: List[str]        # "top-left", "center", "HUD-row-61", etc.


@dataclass
class Hypothesis:
    """One hypothesis about the game's rules."""
    id: str                           # "h-{uuid[:8]}"
    description: str                  # "ACTION3 moves player right by ~4 pixels"
    category: str                     # action_semantic | hud_element | rule | invariant
    confidence: float = 0.5           # 0.0–1.0, starts at 0.5
    support_count: int = 0
    contradiction_count: int = 0
    status: str = "active"            # active | confirmed | refuted | pruned
    source_transitions: List[int] = field(default_factory=list)  # step numbers

    def update(self, supports: bool) -> None:
        """Bayesian-ish confidence update."""
        if supports:
            self.support_count += 1
        else:
            self.contradiction_count += 1
        total = self.support_count + self.contradiction_count
        if total > 0:
            self.confidence = self.support_count / total
        # Auto-transition status
        if self.confidence >= 0.8 and total >= 3:
            self.status = "confirmed"
        elif self.confidence <= 0.2 and total >= 3:
            self.status = "refuted"


# ── State Graph ──────────────────────────────────────────────────────

class StateGraph:
    """In-memory directed graph: nodes=states, edges=actions.

    Ephemeral — destroyed on retry, rebuilt from scratch.
    Used for loop detection, path analysis, unexplored action discovery.
    """

    def __init__(self) -> None:
        self.nodes: Dict[str, StateNode] = {}                  # hash → StateNode
        self.edges: Dict[str, List[Transition]] = defaultdict(list)  # from_hash → [Transition]
        self._visit_order: List[str] = []                       # ordered list of visited hashes

    def add_state(self, node: StateNode) -> bool:
        """Add a state. Returns True if this is a NEW state, False if revisit."""
        is_new = node.grid_hash not in self.nodes
        if is_new:
            self.nodes[node.grid_hash] = node
        self._visit_order.append(node.grid_hash)
        return is_new

    def add_transition(self, transition: Transition) -> None:
        self.edges[transition.from_hash].append(transition)

    def detect_loop(self) -> Optional[str]:
        """If the latest state was visited before, return its hash."""
        if len(self._visit_order) < 2:
            return None
        latest = self._visit_order[-1]
        if latest in self._visit_order[:-1]:
            return latest
        return None

    def get_unexplored_actions(
        self, from_hash: str, all_actions: List[str]
    ) -> List[str]:
        """Actions not yet tried from this state."""
        tried = {t.action for t in self.edges.get(from_hash, [])}
        return [a for a in all_actions if a not in tried]

    def get_action_effects(self, action: str) -> List[Transition]:
        """All transitions caused by a given action (across all states)."""
        results = []
        for transitions in self.edges.values():
            for t in transitions:
                if t.action == action:
                    results.append(t)
        return results

    def clear(self) -> None:
        """Reset for a new attempt."""
        self.nodes.clear()
        self.edges.clear()
        self._visit_order.clear()


# ── Invariant Detector ───────────────────────────────────────────────

class InvariantDetector:
    """Discovers which grid regions are static vs dynamic.

    Compares frames across multiple steps. Regions that NEVER change
    are structural (walls, HUD, decoration). Regions that change with
    specific actions reveal game mechanics.
    """

    def __init__(self, min_frames: int = 3) -> None:
        self.min_frames = min_frames
        self._frames: List[List[List[int]]] = []  # 2D snapshots (first layer)

    def add_frame(self, grid_2d: List[List[int]]) -> None:
        self._frames.append(grid_2d)

    def find_static_rows(self) -> List[int]:
        """Rows that haven't changed across all stored frames."""
        if len(self._frames) < self.min_frames:
            return []
        static = []
        for row_idx in range(len(self._frames[0])):
            reference = self._frames[0][row_idx]
            if all(
                f[row_idx] == reference
                for f in self._frames[1:]
                if row_idx < len(f)
            ):
                static.append(row_idx)
        return static

    def find_dynamic_regions(self) -> List[Dict[str, Any]]:
        """Regions that changed between consecutive frames."""
        if len(self._frames) < 2:
            return []
        regions = []
        prev = self._frames[-2]
        curr = self._frames[-1]
        changed_rows = set()
        changed_cols = set()
        for r in range(min(len(prev), len(curr))):
            for c in range(min(len(prev[r]), len(curr[r]))):
                if prev[r][c] != curr[r][c]:
                    changed_rows.add(r)
                    changed_cols.add(c)
        if changed_rows:
            regions.append({
                "rows": sorted(changed_rows),
                "cols": sorted(changed_cols),
                "row_range": (min(changed_rows), max(changed_rows)),
                "col_range": (min(changed_cols), max(changed_cols)),
            })
        return regions

    def estimate_hud_rows(self) -> List[int]:
        """Guess HUD rows: static rows near the bottom of the grid
        that contain bar-like patterns (multiple colors, partial fill)."""
        static = self.find_static_rows()
        if not self._frames:
            return []
        grid_height = len(self._frames[0])
        # HUD is typically in the bottom 10% of the grid
        bottom_threshold = int(grid_height * 0.9)
        hud_candidates = [r for r in static if r >= bottom_threshold]
        return hud_candidates

    def clear(self) -> None:
        self._frames.clear()


# ── Hypothesis Manager ───────────────────────────────────────────────

class HypothesisManager:
    """Top-level controller: generate, track, prune, distill hypotheses.

    Owns the StateGraph and InvariantDetector. Called by the orchestrator's
    hypothesize() step.
    """

    CONFIRM_THRESHOLD: float = 0.8
    PRUNE_THRESHOLD: float = 0.2
    MIN_EVIDENCE: int = 3
    EXPLORE_ENERGY_FLOOR: float = 0.3  # below this, only exploit

    def __init__(self, brain_client: Any, session_id: str) -> None:
        self.brain = brain_client
        self.session_id = session_id
        self.graph = StateGraph()
        self.invariant_detector = InvariantDetector()
        self.hypotheses: Dict[str, Hypothesis] = {}
        self._prev_state_hash: Optional[str] = None
        self._prev_grid_2d: Optional[List[List[int]]] = None

    def observe(
        self,
        grid: List[List[List[int]]],
        action_taken: Optional[str],
        step: int,
        available_actions: List[str],
        observation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Main entry point — called once per frame.

        Returns context dict for the orchestrator:
          - loop_detected: bool
          - active_hypotheses: list of dicts
          - confirmed_hypotheses: list of dicts
          - unexplored_actions: list of str
          - invariant_rows: list of int
          - hud_rows: list of int
          - explore_vs_exploit: str ("explore" | "exploit")
          - energy_from_hud: Optional[float]
        """
        # 1. Hash and register state
        grid_hash = StateNode.hash_grid(grid)
        grid_2d = grid[0] if grid else []
        features = {
            "colors": observation.get("colors", []),
            "shapes": observation.get("shapes", []),
        }
        node = StateNode(
            grid_hash=grid_hash,
            step=step,
            key_features=features,
            energy_estimate=observation.get("energy_estimate"),
            grid_snapshot=grid_2d,
        )
        is_new = self.graph.add_state(node)

        # 2. Record transition
        if action_taken and self._prev_state_hash:
            diff = self._compute_diff(self._prev_grid_2d, grid_2d)
            transition = Transition(
                from_hash=self._prev_state_hash,
                to_hash=grid_hash,
                action=action_taken,
                step=step,
                diff_summary=diff["summary"],
                pixels_changed=diff["pixels_changed"],
                regions_changed=diff.get("regions", []),
            )
            self.graph.add_transition(transition)

            # 3. Generate / update hypotheses from this transition
            self._process_transition(transition, diff)

        # 4. Feed invariant detector
        self.invariant_detector.add_frame(grid_2d)

        # 5. Loop detection
        loop_hash = self.graph.detect_loop()

        # 6. Invariant analysis
        hud_rows = self.invariant_detector.estimate_hud_rows()
        static_rows = self.invariant_detector.find_static_rows()

        # 7. Unexplored actions from current state
        unexplored = self.graph.get_unexplored_actions(grid_hash, available_actions)

        # 8. Explore/exploit policy
        energy = observation.get("energy_estimate", 1.0)
        policy = self._decide_policy(energy)

        # Update tracking
        self._prev_state_hash = grid_hash
        self._prev_grid_2d = grid_2d

        return {
            "loop_detected": loop_hash is not None,
            "loop_hash": loop_hash,
            "is_new_state": is_new,
            "active_hypotheses": self._get_by_status("active"),
            "confirmed_hypotheses": self._get_by_status("confirmed"),
            "refuted_hypotheses": self._get_by_status("refuted"),
            "unexplored_actions": unexplored,
            "invariant_rows": static_rows,
            "hud_rows": hud_rows,
            "explore_vs_exploit": policy,
            "energy_from_hud": self._estimate_energy_from_hud(hud_rows, grid_2d),
            "state_count": len(self.graph.nodes),
            "transition_count": sum(len(v) for v in self.graph.edges.values()),
        }

    def _process_transition(self, t: Transition, diff: Dict) -> None:
        """Generate or update hypotheses from observed transition."""
        # Hypothesis: what does this action do?
        action_hyp_id = f"action-{t.action}"
        if action_hyp_id not in self.hypotheses:
            self.hypotheses[action_hyp_id] = Hypothesis(
                id=action_hyp_id,
                description=f"{t.action}: {t.diff_summary}",
                category="action_semantic",
            )
        # Check consistency: does this action always produce similar effects?
        prior_effects = self.graph.get_action_effects(t.action)
        if len(prior_effects) >= 2:
            consistent = all(
                abs(e.pixels_changed - t.pixels_changed) < 20
                for e in prior_effects[:-1]
            )
            self.hypotheses[action_hyp_id].update(supports=consistent)

        # Hypothesis: no-change means wall/obstacle
        if t.pixels_changed == 0:
            wall_id = f"wall-{t.from_hash}-{t.action}"
            self.hypotheses[wall_id] = Hypothesis(
                id=wall_id,
                description=f"{t.action} from state {t.from_hash[:8]} produces no change — blocked by obstacle",
                category="rule",
                confidence=0.7,
                support_count=1,
            )

    def _compute_diff(
        self,
        prev: Optional[List[List[int]]],
        curr: List[List[int]],
    ) -> Dict[str, Any]:
        """Compute grid diff between two 2D frames."""
        if not prev or not curr:
            return {"summary": "initial frame", "pixels_changed": 0, "regions": []}
        changed = 0
        changed_positions = []
        for r in range(min(len(prev), len(curr))):
            for c in range(min(len(prev[r]), len(curr[r]))):
                if prev[r][c] != curr[r][c]:
                    changed += 1
                    changed_positions.append((r, c))
        if not changed_positions:
            return {"summary": "no visible change", "pixels_changed": 0, "regions": []}
        min_r = min(p[0] for p in changed_positions)
        max_r = max(p[0] for p in changed_positions)
        min_c = min(p[1] for p in changed_positions)
        max_c = max(p[1] for p in changed_positions)
        summary = (
            f"{changed} pixels changed in rows {min_r}-{max_r}, "
            f"cols {min_c}-{max_c}"
        )
        return {"summary": summary, "pixels_changed": changed, "regions": [f"r{min_r}-{max_r}_c{min_c}-{max_c}"]}

    def _decide_policy(self, energy: float) -> str:
        """Explore/exploit based on energy and hypothesis landscape."""
        active = [h for h in self.hypotheses.values() if h.status == "active"]
        confirmed = [h for h in self.hypotheses.values() if h.status == "confirmed"]
        if energy < self.EXPLORE_ENERGY_FLOOR:
            return "exploit"
        if not confirmed and active:
            return "explore"
        if len(active) > len(confirmed):
            return "explore"
        return "exploit"

    def _estimate_energy_from_hud(
        self, hud_rows: List[int], grid_2d: List[List[int]]
    ) -> Optional[float]:
        """Estimate energy from discovered HUD rows (hypothesis-driven)."""
        if not hud_rows or not grid_2d:
            return None
        # Look for bar-like pattern in HUD rows
        for row_idx in hud_rows:
            if row_idx >= len(grid_2d):
                continue
            row = grid_2d[row_idx]
            if not row:
                continue
            non_zero = sum(1 for v in row if v != 0)
            total = len(row)
            ratio = non_zero / total if total > 0 else 0
            if 0.02 < ratio < 0.98:  # looks like a partial bar
                return ratio
        return None

    def _get_by_status(self, status: str) -> List[Dict[str, Any]]:
        return [
            {"id": h.id, "description": h.description, "confidence": round(h.confidence, 2), "category": h.category}
            for h in self.hypotheses.values()
            if h.status == status
        ]

    async def distill_to_brain(self) -> int:
        """Flush confirmed + refuted hypotheses to SideQuests as durable knowledge.

        Called on WIN or GAME_OVER boundaries. Returns count of hypotheses flushed.
        """
        flushed = 0
        for h in self.hypotheses.values():
            if h.status in ("confirmed", "refuted"):
                text = f"[{h.status.upper()}] {h.description} (confidence: {h.confidence:.2f}, evidence: {h.support_count}+/{h.contradiction_count}-)"
                await self.brain.notify_turn(
                    role="assistant",
                    content=text,
                    session_id=self.session_id,
                )
                flushed += 1
        return flushed

    def reset_graph(self) -> None:
        """Clear ephemeral state for retry. Hypotheses survive."""
        self.graph.clear()
        self.invariant_detector.clear()
        self._prev_state_hash = None
        self._prev_grid_2d = None
```

### MODIFY: `agents/arc3/orchestrator.py`

**Change 1: Constructor — add HypothesisManager**

```python
# After existing imports, add:
from agents.arc3.hypothesis import HypothesisManager

# In __init__, after self._step_history, add:
self.hypothesis_mgr = HypothesisManager(brain_client, session_id)
```

**Change 2: New `hypothesize()` method**

```python
async def hypothesize(
    self,
    observation: ARC3Observation,
    action_taken: str | None,
    step: int,
) -> dict:
    """Update state graph, generate/update hypotheses, detect invariants.

    Called after every action, before the next plan/act decision.
    Returns hypothesis context for prompt construction.
    """
    available = observation.get("available_actions") or [f"ACTION{i}" for i in range(1, 8)]
    context = self.hypothesis_mgr.observe(
        grid=observation["grid"],
        action_taken=action_taken,
        step=step,
        available_actions=available,
        observation=observation,
    )

    # Override energy estimate with hypothesis-driven value if available
    hud_energy = context.get("energy_from_hud")
    if hud_energy is not None:
        observation["energy_estimate"] = hud_energy

    return context
```

**Change 3: Update `_draft_plan_steps()` to consume hypothesis context**

```python
def _draft_plan_steps(
    self,
    observation: ARC3Observation,
    memory_context: dict,
    recall: dict,
    hypothesis_context: dict | None = None,
) -> List[str]:
    steps = []

    # Use hypothesis-informed steps instead of static template
    if hypothesis_context:
        confirmed = hypothesis_context.get("confirmed_hypotheses", [])
        unexplored = hypothesis_context.get("unexplored_actions", [])
        policy = hypothesis_context.get("explore_vs_exploit", "explore")

        if policy == "explore" and unexplored:
            steps.append(f"Explore untested actions: {', '.join(unexplored[:3])}")
        for h in confirmed[:2]:
            steps.append(f"Exploit confirmed rule: {h['description']}")
        if hypothesis_context.get("loop_detected"):
            steps.append("BREAK LOOP: avoid the action sequence that returned to a visited state")
    else:
        steps.append("Survey the grid to understand dominant colors and shapes.")
        steps.append("Compare the pattern to lessons and analogies retrieved.")
        steps.append("Apply targeted ACTION commands to drive toward the goal.")

    for plan in recall.get("plans", [])[:2]:
        steps.append(f"Learn from {plan.get('goal')} (valence {plan.get('valence')})")
    return steps
```

**Change 4: Update `build_action_prompt()` — add HYPOTHESIS section**

Insert between REFLEX and PLAN sections:

```python
# After reflex_lines section:
hyp_lines = self._format_hypothesis_section(hypothesis_context)
if hyp_lines:
    sections.append("HYPOTHESIS:\n" + "\n".join(hyp_lines))

# New helper:
def _format_hypothesis_section(self, hyp_ctx: dict | None) -> List[str]:
    if not hyp_ctx:
        return []
    lines = []
    if hyp_ctx.get("loop_detected"):
        lines.append(f"⚠ LOOP DETECTED — revisited state {hyp_ctx['loop_hash'][:8]}. Change strategy.")
    for h in hyp_ctx.get("confirmed_hypotheses", [])[:3]:
        lines.append(f"CONFIRMED ({h['confidence']:.0%}): {h['description']}")
    for h in hyp_ctx.get("active_hypotheses", [])[:3]:
        lines.append(f"TESTING ({h['confidence']:.0%}): {h['description']}")
    unexplored = hyp_ctx.get("unexplored_actions", [])
    if unexplored:
        lines.append(f"Untested actions from this state: {', '.join(unexplored)}")
    lines.append(f"Policy: {hyp_ctx.get('explore_vs_exploit', 'explore').upper()}")
    return lines
```

**Change 5: `reset_for_retry()` — clear graph, keep hypotheses**

```python
def reset_for_retry(self, attempt: int) -> None:
    # ... existing code stays ...
    # Add at end:
    self.hypothesis_mgr.reset_graph()
```

**Change 6: Wire hypothesize into plan()**

`plan()` passes hypothesis context to `_draft_plan_steps()`. Store `_hypothesis_context` on self for prompt use.

### MODIFY: `agents/arc3/runner.py`

**Change 1: Call `hypothesize()` in the action loop**

In `_run_puzzle()`, after each `normalize_observation()`, call:
```python
hypothesis_context = await orchestrator.hypothesize(
    observation, action.get("action_id") if step > 0 else None, total_steps
)
```

**Change 2: Call `distill_to_brain()` on WIN/GAME_OVER**

Before `evaluate()`, call:
```python
await orchestrator.hypothesis_mgr.distill_to_brain()
```

### MODIFY: `benchmarks/arc3/adapter.py`

Remove the `_estimate_energy()` static method body. Replace with a pass-through that calls `HypothesisManager.observe()` output. The `energy_estimate` field stays in the observation schema but its source changes from hardcoded row scanning to hypothesis-driven HUD detection.

### MODIFY: `benchmarks/arc3/schema.py`

Add to `ARC3Observation` TypedDict:
```python
frame_hash: str              # SHA-256[:16] of grid
invariant_regions: list      # discovered static regions
```

### MODIFY: `agents/arc3/api_knowledge.py`

Replace chunk #9 (LockSmith-specific energy) with:
```python
(
    "The game HUD (heads-up display) layout varies by game. "
    "Energy bars, score indicators, and inventory may appear in different "
    "grid regions. The agent must DISCOVER HUD elements by observing which "
    "rows/regions remain static across multiple actions, then hypothesize "
    "their meaning. Do not assume fixed HUD row positions."
),
```

### MODIFY: `mcp_engine/schema.py`

Add Hypothesis node table and relationship tables:
```python
# After existing Plan/PlanStep tables:
CREATE NODE TABLE IF NOT EXISTS Hypothesis (
    id STRING PRIMARY KEY,
    description STRING,
    category STRING,
    confidence FLOAT,
    game_type STRING,
    task_id STRING,
    status STRING,
    evidence_count INT32,
    text_raw STRING,
    embedding FLOAT[384],
    created_at TIMESTAMP DEFAULT current_timestamp
)

CREATE REL TABLE IF NOT EXISTS HYPOTHESIZED_IN (FROM Hypothesis TO Session)
CREATE REL TABLE IF NOT EXISTS CONFIRMS (FROM Concept TO Hypothesis, weight FLOAT)
CREATE REL TABLE IF NOT EXISTS CONTRADICTS (FROM Concept TO Hypothesis, weight FLOAT)
CREATE REL TABLE IF NOT EXISTS GENERALIZES (FROM Hypothesis TO Hypothesis)
CREATE REL TABLE IF NOT EXISTS PRODUCED_HYPOTHESIS (FROM Plan TO Hypothesis)
```

Add HNSW vector index on Hypothesis.embedding.

---

### NEW: `tests/test_arc3_hypothesis.py`

```python
# ── StateGraph tests ─────────────────────────────────────────
# test_add_state_new_returns_true
# test_add_state_revisit_returns_false
# test_detect_loop_on_revisit
# test_no_loop_on_unique_states
# test_get_unexplored_actions
# test_get_action_effects_across_states
# test_clear_resets_all

# ── InvariantDetector tests ──────────────────────────────────
# test_find_static_rows_with_3_frames
# test_find_static_rows_insufficient_frames
# test_find_dynamic_regions
# test_estimate_hud_rows_bottom_10pct

# ── HypothesisManager tests ─────────────────────────────────
# test_observe_creates_state_node
# test_observe_records_transition
# test_hypothesis_generated_from_transition
# test_wall_hypothesis_on_zero_change
# test_confidence_update_supports
# test_confidence_update_contradicts
# test_auto_confirm_at_threshold
# test_auto_prune_at_threshold
# test_explore_policy_when_low_confirmation
# test_exploit_policy_when_low_energy
# test_distill_flushes_confirmed_to_brain
# test_reset_graph_preserves_hypotheses
# test_energy_from_hud_estimation
# test_loop_detection_in_observe_output
# test_compute_diff_accuracy
```

---

## Acceptance Criteria Verification Commands

```bash
# New tests
python -m pytest tests/test_arc3_hypothesis.py -xvs

# Regression — all existing ARC3 tests must pass
python -m pytest tests/test_arc3_*.py -q

# Tool/adapter checks (no new tools, no allow-list changes needed)
rg -n "TOOL_HANDLERS|TOOLS:" mcp_engine/tool_schemas.py mcp_engine/tools/__init__.py adapters
```

## Notes

- No new MCP tools — hypothesis engine is internal agent logic
- `distill_to_brain()` uses existing `notify_turn` for ingestion (consolidation loop handles embedding + entity extraction)
- Future B-card: dedicated `register_hypothesis` / `recall_hypotheses` MCP tools for cross-agent hypothesis sharing
- Future B-card: replace `notify_turn` distillation with proper Hypothesis node creation via Kùzu client
- The Hypothesis Kùzu schema is forward-looking — B88 writes via notify_turn, a follow-up card adds direct graph writes
