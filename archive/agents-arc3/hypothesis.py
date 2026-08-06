"""Hypothesis Engine — game-theory-driven puzzle reasoning.

Three core components:
  StateGraph         — ephemeral in-memory directed graph of game states
  InvariantDetector  — finds static vs dynamic grid regions
  HypothesisManager  — generates, tracks, prunes, and distills hypotheses
"""

from __future__ import annotations
import hashlib
import logging
from datetime import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

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
    def hash_grid(grid: Any) -> str:
        if not grid:
            return "empty"
        # Use entire grid for hashing, handle 2D or 3D
        flat = str(grid)
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
    meaningful_change_score: float = 0.0
    meaningful_change_label: str = "unknown"
    meaningful_change_reasons: List[str] = field(default_factory=list)
    reward_signal: float = 0.0
    novelty_signal: float = 0.0
    progress_signal: float = 0.0
    looped: bool = False
    meaningful_progress: bool = False
    progress_class: str = "none"
    progress_gate_reason: str = ""
    zero_reward_streak: int = 0
    changed_bbox: Dict[str, int] | None = None
    changed_center: Dict[str, float] | None = None


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
    effect_consistency: float = 0.0
    value_score: float = 0.0
    meaningful_progress_ratio: float = 0.0
    value_status: str = "unknown"     # unknown | valuable | tentative | low_value | ineffective

    def update(self, supports: bool) -> None:
        """Bayesian-ish confidence update."""
        if supports:
            self.support_count += 1
        else:
            self.contradiction_count += 1
        total = self.support_count + self.contradiction_count
        if total > 0:
            self.confidence = self.support_count / total

        if total >= 3:
            if self.confidence >= 0.8:
                self.status = "confirmed"
            elif self.confidence <= 0.2:
                self.status = "pruned"


@dataclass
class ActionFact:
    """A compact operator fact extracted from repeated action evidence."""
    id: str
    action: str
    fact_type: str                  # deterministic_effect | blocked | loop | low_value | no_op
    description: str
    consistency: float
    value_status: str
    evidence_count: int
    trend: Dict[str, Any] | None = None
    support_steps: List[int] = field(default_factory=list)

    @property
    def compact_description(self) -> str:
        """A minimal version of the fact for prompt-efficient retrieval."""
        # Get just the core outcome from the description
        if ": " in self.description:
            core = self.description.split(": ")[-1]
        else:
            core = self.description
        return f"{self.action}: {core}"

    def to_dict(self) -> Dict[str, Any]:
        """B117: Serialization for decision packets."""
        return {
            "id": self.id,
            "action": self.action,
            "fact_type": self.fact_type,
            "description": self.description,
            "consistency": self.consistency,
            "value_status": self.value_status,
            "evidence_count": self.evidence_count,
            "trend": self.trend,
            "support_steps": self.support_steps,
        }


@dataclass
class PathHypothesis:
    """A short action sequence hypothesis about what a path seems to achieve."""
    actions: List[str]
    description: str
    confidence: float
    value_status: str
    support_steps: List[int] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """B117: Serialization for decision packets."""
        return {
            "actions": self.actions,
            "description": self.description,
            "confidence": self.confidence,
            "value_status": self.value_status,
            "support_steps": self.support_steps,
        }


@dataclass
class ExplorationCompaction:
    """B116: Compact summary of older exploration state to keep context lean."""
    action_summaries: Dict[str, str] = field(default_factory=dict)  # action -> short summary
    known_loops: List[List[str]] = field(default_factory=list)      # sequences that looped
    confirmed_rules: List[str] = field(default_factory=list)
    refuted_rules: List[str] = field(default_factory=list)
    timestamp_step: int = 0


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

    def find_path(self, from_hash: str, to_hash: str) -> List["Transition"]:
        """BFS from from_hash to to_hash. Returns ordered list of Transitions.

        Returns empty list if no path exists or either hash is unknown.
        Used by PlanChunker to extract exact action sequences.
        """
        if from_hash not in self.nodes or to_hash not in self.nodes:
            return []
        if from_hash == to_hash:
            return []

        from collections import deque
        # BFS: queue of (current_hash, path_so_far)
        queue: deque = deque([(from_hash, [])])
        visited: set = {from_hash}

        while queue:
            current, path = queue.popleft()
            for transition in self.edges.get(current, []):
                if transition.to_hash == to_hash:
                    return path + [transition]
                if transition.to_hash not in visited:
                    visited.add(transition.to_hash)
                    queue.append((transition.to_hash, path + [transition]))

        return []


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
        height = len(self._frames[0])
        for row_idx in range(height):
            reference = self._frames[0][row_idx]
            if all(
                len(f) > row_idx and f[row_idx] == reference
                for f in self._frames[1:]
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
        self.action_facts: Dict[str, ActionFact] = {}
        self._prev_state_hash: Optional[str] = None
        self._prev_grid_2d: Optional[List[List[int]]] = None
        # B170/B171: KuzuDB persistence
        self._entity_graph: Optional["EntityGraphBuilder"] = None
        self._task_id: Optional[str] = None
        self._current_level: int = 0
        self._pending_fact_writes: List[ActionFact] = []

    def _set_task_id(self, task_id: str) -> None:
        """B170: Set task ID for KuzuDB scoping."""
        self._task_id = task_id

    def _get_db(self) -> Optional[Any]:
        """B170: Get KuzuDB client from entity_graph or brain."""
        if self._entity_graph:
            return self._entity_graph.db
        return getattr(self.brain, "db", None)

    async def load_hypotheses(self) -> int:
        """B170: Load existing hypotheses from KuzuDB GridEntity/Hypothesis nodes."""
        db = self._get_db()
        if not db or not self._task_id:
            return 0
        
        try:
            # Match hypotheses associated with this task
            rows = await db.execute_read(
                """
                MATCH (h:Hypothesis)
                WHERE h.task_id = $tid
                RETURN h.id, h.description, h.category, h.confidence, h.status, h.evidence_count
                """,
                {"tid": self._task_id}
            )
            
            count = 0
            for row in rows:
                hid = row["h.id"]
                if hid not in self.hypotheses:
                    self.hypotheses[hid] = Hypothesis(
                        id=hid,
                        description=row["h.description"],
                        category=row["h.category"],
                        confidence=row["h.confidence"],
                        status=row["h.status"],
                        support_count=row["h.evidence_count"], # simplified mapping
                    )
                    count += 1
            
            if count > 0:
                logger.info(f"B170: Loaded {count} hypotheses from KuzuDB for task {self._task_id}")
            return count
        except Exception as exc:
            logger.warning(f"B170: load_hypotheses failed: {exc}")
            return 0

    async def load_action_facts(self) -> int:
        """B171: Load existing deterministic action facts from KuzuDB."""
        db = self._get_db()
        if not db or not self._task_id:
            return 0

        try:
            rows = await db.execute_read(
                """
                MATCH (f:ActionFact)
                WHERE f.task_id = $tid
                RETURN f.fact_id, f.action_id, f.fact_type, f.description,
                       f.consistency, f.confidence, f.value_status,
                       f.evidence_count, f.observation_count,
                       f.delta_row, f.delta_col, f.n_cells_changed
                """,
                {"tid": self._task_id},
            )

            count = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                action = row.get("f.action_id")
                if not action:
                    continue

                evidence_count = int(
                    row.get("f.evidence_count")
                    or row.get("f.observation_count")
                    or 0
                )
                fact_id = str(row.get("f.fact_id") or f"fact-{action}")
                local_id = fact_id.removeprefix(f"{self._task_id}_") if self._task_id else fact_id

                self.action_facts[str(action)] = ActionFact(
                    id=local_id,
                    action=str(action),
                    fact_type=str(row.get("f.fact_type") or "repeatable_effect"),
                    description=str(row.get("f.description") or ""),
                    consistency=float(row.get("f.consistency") or row.get("f.confidence") or 0.0),
                    value_status=str(row.get("f.value_status") or "unknown"),
                    evidence_count=evidence_count,
                )
                count += 1

            if count > 0:
                logger.info(
                    "B171: Loaded %d action facts from KuzuDB for task %s",
                    count,
                    self._task_id,
                )
            return count
        except Exception as exc:
            logger.warning(f"B171: load_action_facts failed: {exc}")
            return 0

    def _derive_action_fact_metrics(self, fact: ActionFact) -> Dict[str, Any]:
        """B171: Derive graph-friendly motion metrics from a compact action fact."""
        delta_row = 0.0
        delta_col = 0.0
        n_cells_changed = 0

        effects = self.graph.get_action_effects(fact.action)
        if effects:
            n_cells_changed = int(effects[-1].pixels_changed)

        trend = fact.trend or {}
        if trend.get("kind") == "directional_drift":
            avg_delta = float(trend.get("avg_delta") or 0.0)
            direction = str(trend.get("direction") or "")
            sign = -1.0 if direction in {"left", "up"} else 1.0
            axis = trend.get("axis")
            if axis == "row":
                delta_row = sign * avg_delta
            elif axis == "col":
                delta_col = sign * avg_delta

        return {
            "delta_row": float(delta_row),
            "delta_col": float(delta_col),
            "n_cells_changed": int(n_cells_changed),
        }

    async def _persist_action_fact(self, fact: ActionFact) -> None:
        """B171: Persist an ActionFact node and its graph edges when KuzuDB is available."""
        db = self._get_db()
        if not db or not self._task_id:
            return

        metrics = self._derive_action_fact_metrics(fact)
        now = datetime.now().isoformat()
        fact_id = str(fact.id)
        if not fact_id.startswith(f"{self._task_id}_"):
            fact_id = f"{self._task_id}_{fact_id}"

        try:
            await db.execute_write(
                """
                MERGE (f:ActionFact {fact_id: $fid})
                ON CREATE SET f.task_id = $tid, f.level = $level,
                              f.action_id = $action, f.fact_type = $ftype,
                              f.description = $descr, f.effect_description = $effect_descr,
                              f.consistency = $cons, f.confidence = $conf,
                              f.value_status = $vs, f.evidence_count = $ec,
                              f.observation_count = $obs_count,
                              f.delta_row = $delta_row, f.delta_col = $delta_col,
                              f.n_cells_changed = $n_cells_changed,
                              f.created_at = timestamp($now), f.last_updated = timestamp($now)
                ON MATCH SET f.level = $level, f.fact_type = $ftype,
                             f.description = $descr, f.effect_description = $effect_descr,
                             f.consistency = $cons, f.confidence = $conf,
                             f.value_status = $vs, f.evidence_count = $ec,
                             f.observation_count = $obs_count,
                             f.delta_row = $delta_row, f.delta_col = $delta_col,
                             f.n_cells_changed = $n_cells_changed,
                             f.last_updated = timestamp($now)
                """,
                {
                    "fid": fact_id,
                    "tid": self._task_id,
                    "level": int(self._current_level),
                    "action": fact.action,
                    "ftype": fact.fact_type,
                    "descr": fact.description,
                    "effect_descr": fact.description,
                    "cons": float(fact.consistency),
                    "conf": float(fact.consistency),
                    "vs": fact.value_status,
                    "ec": int(fact.evidence_count),
                    "obs_count": int(fact.evidence_count),
                    "delta_row": metrics["delta_row"],
                    "delta_col": metrics["delta_col"],
                    "n_cells_changed": metrics["n_cells_changed"],
                    "now": now,
                },
            )

            for support_step in fact.support_steps:
                effect_id = f"{self._task_id}_{self._current_level}_{fact.action}_{support_step}"
                await db.execute_write(
                    """
                    MATCH (f:ActionFact {fact_id: $fid}), (e:ActionEffect {effect_id: $eid})
                    MERGE (f)-[:DERIVED_FROM_FACT {step: $step}]->(e)
                    """,
                    {"fid": fact_id, "eid": effect_id, "step": int(support_step)},
                )

            hypothesis_id = f"action-{fact.action}"
            if hypothesis_id in self.hypotheses:
                await db.execute_write(
                    """
                    MATCH (f:ActionFact {fact_id: $fid}), (h:Hypothesis {id: $hid})
                    MERGE (f)-[:SUPPORTS_HYPOTHESIS {weight: $w}]->(h)
                    """,
                    {"fid": fact_id, "hid": hypothesis_id, "w": float(fact.consistency)},
                )
        except Exception as exc:
            logger.warning(f"B171: persist_action_fact failed for {fact.action}: {exc}")

    async def _flush_action_fact_writes(self) -> None:
        """B171: Flush queued ActionFact writes while preserving in-memory fallback."""
        if not self._pending_fact_writes:
            return

        pending = {fact.action: fact for fact in self._pending_fact_writes}
        self._pending_fact_writes.clear()

        for fact in pending.values():
            await self._persist_action_fact(fact)

    async def persist_hypothesis(self, h: Hypothesis) -> None:
        """B170: Write a hypothesis to KuzuDB."""
        db = self._get_db()
        if not db or not self._task_id:
            return

        now = datetime.now().isoformat()
        try:
            await db.execute_write(
                """
                MERGE (h:Hypothesis {id: $id})
                ON CREATE SET h.description = $descr, h.category = $cat,
                              h.confidence = $conf, h.status = $status,
                              h.evidence_count = $evidence, h.task_id = $tid,
                              h.created_at = timestamp($now), h.text_raw = $descr
                ON MATCH SET h.description = $descr, h.confidence = $conf,
                             h.status = $status, h.evidence_count = $evidence
                """,
                {
                    "id": h.id, "descr": h.description, "cat": h.category,
                    "conf": float(h.confidence), "status": h.status,
                    "evidence": int(h.support_count + h.contradiction_count),
                    "tid": self._task_id, "now": now
                }
            )
            
            # B170: Relate to session
            await db.execute_write(
                """
                MATCH (h:Hypothesis {id: $hid}), (s:Session {session_id: $sid})
                MERGE (h)-[:HYPOTHESIZED_IN]->(s)
                """,
                {"hid": h.id, "sid": self.session_id}
            )
        except Exception as exc:
            logger.warning(f"B170: persist_hypothesis failed for {h.id}: {exc}")

    async def observe(
        self,
        grid: Any,
        action_taken: Optional[str],
        step: int,
        available_actions: List[str],
        observation: Dict[str, Any],
        transition_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Main entry point — called once per frame."""
        # 1. Hash and register state
        grid_hash = StateNode.hash_grid(grid)
        last_effect: Dict[str, Any] | None = None
        transition_meta = transition_meta or {}

        observed_level = observation.get("level")
        if observed_level is None:
            observed_level = observation.get("episode_num")
        if observed_level is None and observation.get("levels_completed") is not None:
            observed_level = int(observation.get("levels_completed") or 0) + 1
        if observed_level is not None:
            try:
                self._current_level = int(observed_level)
            except (TypeError, ValueError):
                pass

        # Resolve 2D grid for analysis
        if grid and isinstance(grid[0], list) and grid[0] and isinstance(grid[0][0], list):
             # 3D: [layer][row][col]
             grid_2d = grid[0]
        else:
             # 2D: [row][col]
             grid_2d = grid or []

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
            prior_effects = self.graph.get_action_effects(action_taken)
            transition = Transition(
                from_hash=self._prev_state_hash,
                to_hash=grid_hash,
                action=action_taken,
                step=step,
                diff_summary=diff["summary"],
                pixels_changed=diff["pixels_changed"],
                regions_changed=diff.get("regions", []),
                changed_bbox=diff.get("bbox"),
                changed_center=diff.get("center"),
            )
        else:
            transition = None
            prior_effects = []

        # 4. Feed invariant detector
        self.invariant_detector.add_frame(grid_2d)

        # 5. Loop detection
        loop_hash = self.graph.detect_loop()

        if transition is not None:
            transition_eval = self._evaluate_meaningful_change(
                diff=diff,
                reward=float(transition_meta.get("reward") or 0.0),
                is_new_state=is_new,
                looped=loop_hash is not None,
                final_state=str(transition_meta.get("state_after") or observation.get("state") or "NOT_FINISHED"),
                prior_zero_reward_streak=self._count_zero_reward_streak(prior_effects),
            )
            transition.meaningful_change_score = transition_eval["score"]
            transition.meaningful_change_label = transition_eval["label"]
            transition.meaningful_change_reasons = transition_eval["reasons"]
            transition.reward_signal = transition_eval["reward_signal"]
            transition.novelty_signal = transition_eval["novelty_signal"]
            transition.progress_signal = transition_eval["progress_signal"]
            transition.looped = transition_eval["looped"]
            transition.zero_reward_streak = transition_eval["zero_reward_streak"]
            
            # A066: pass through meaningful_progress from runtime components
            reward_comps = transition_meta.get("reward_components") or {}
            transition.meaningful_progress = bool(reward_comps.get("meaningful_progress", False))
            transition.progress_class = str(reward_comps.get("progress_class", "none"))
            transition.progress_gate_reason = str(reward_comps.get("progress_gate_reason", ""))
            
            self.graph.add_transition(transition)

            # 3. Generate / update hypotheses from this transition
            self._process_transition(transition, diff)

            # B170: Persist changed hypotheses to KuzuDB
            action_hyp_id = f"action-{transition.action}"
            if action_hyp_id in self.hypotheses:
                await self.persist_hypothesis(self.hypotheses[action_hyp_id])
            
            if transition.pixels_changed == 0:
                wall_id = f"wall-{transition.from_hash}-{transition.action}"
                if wall_id in self.hypotheses:
                    await self.persist_hypothesis(self.hypotheses[wall_id])

            last_effect = {
                "action": transition.action,
                "summary": diff["summary"],
                "effect_kind": diff.get("effect_kind", "unknown"),
                "pixels_changed": diff["pixels_changed"],
                "color_shifts": diff.get("color_shifts", {}),
                "regions_changed": transition.regions_changed,
                "before_frame_hash": self._prev_state_hash,
                "after_frame_hash": grid_hash,
                "before_snapshot": self._snapshot_summary(self._prev_grid_2d),
                "after_snapshot": self._snapshot_summary(grid_2d),
                "changed_region": self._region_snapshot(self._prev_grid_2d, grid_2d),
                "changed_center": diff.get("center"),
                "meaningful_change_score": transition_eval["score"],
                "meaningful_change_label": transition_eval["label"],
                "meaningful_change_reasons": transition_eval["reasons"],
                "zero_reward_streak": transition_eval["zero_reward_streak"],
            }

        # 6. Invariant analysis
        hud_rows = self.invariant_detector.estimate_hud_rows()
        static_rows = self.invariant_detector.find_static_rows()

        # 7. Unexplored actions from current state and overall action coverage
        unexplored = self.graph.get_unexplored_actions(grid_hash, available_actions)
        observed_action_effects = self._build_action_effects(available_actions)
        action_coverage = self._summarize_action_coverage(observed_action_effects)
        bottleneck = self._detect_environment_bottleneck(available_actions, observed_action_effects)

        # 8. Explore/exploit policy
        energy = observation.get("energy_estimate", 1.0)
        policy = self._decide_policy(energy, action_coverage)

        # Update tracking
        self._prev_state_hash = grid_hash
        self._prev_grid_2d = grid_2d

        await self._flush_action_fact_writes()

        return {
            "current_state_hash": grid_hash,
            "loop_detected": loop_hash is not None,
            "loop_hash": loop_hash,
            "is_new_state": is_new,
            "active_hypotheses": self._get_by_status("active"),
            "confirmed_hypotheses": self._get_by_status("confirmed"),
            "refuted_hypotheses": self._get_by_status("refuted"),
            "pruned_hypotheses": self._get_by_status("pruned"),
            "action_facts": self._build_action_facts(),
            "path_hypotheses": self._build_path_hypotheses(),
            "unexplored_actions": unexplored,
            "last_transition_effect": last_effect,
            "observed_action_effects": observed_action_effects,
            "action_coverage": action_coverage,
            "environment_bottleneck": bottleneck,
            "invariant_rows": static_rows,
            "hud_rows": hud_rows,
            "explore_vs_exploit": policy,
            "energy_from_hud": self._estimate_energy_from_hud(hud_rows, grid_2d),
            "state_count": len(self.graph.nodes),
            "transition_count": sum(len(v) for v in self.graph.edges.values()),
        }

    async def generate_hypotheses(
        self,
        grid: Any,
        action_taken: Optional[str],
        step: int,
        available_actions: List[str],
        observation: Dict[str, Any],
        transition_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Compatibility wrapper that performs the same hypothesis update as observe()."""
        return await self.observe(
            grid=grid,
            action_taken=action_taken,
            step=step,
            available_actions=available_actions,
            observation=observation,
            transition_meta=transition_meta,
        )

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
        self._refresh_action_hypothesis(t.action)
        self._refresh_action_fact(t.action)
        
        # Additional: record step
        self.hypotheses[action_hyp_id].source_transitions.append(t.step)

        # Hypothesis: no-change means wall/obstacle
        if t.pixels_changed == 0:
            wall_id = f"wall-{t.from_hash}-{t.action}"
            if wall_id not in self.hypotheses:
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
            return {
                "summary": "initial frame",
                "pixels_changed": 0,
                "regions": [],
                "effect_kind": "initial_frame",
                "color_shifts": {},
            }
        changed = 0
        changed_positions = []
        color_shifts: Dict[str, int] = {}
        total_cells = 0
        for r in range(min(len(prev), len(curr))):
            for c in range(min(len(prev[r]), len(curr[r]))):
                total_cells += 1
                if prev[r][c] != curr[r][c]:
                    changed += 1
                    changed_positions.append((r, c))
                    shift_key = f"{prev[r][c]}->{curr[r][c]}"
                    color_shifts[shift_key] = color_shifts.get(shift_key, 0) + 1
        if not changed_positions:
            return {
                "summary": "no_visible_change: no pixels changed",
                "pixels_changed": 0,
                "regions": [],
                "effect_kind": "no_visible_change",
                "color_shifts": {},
            }
        min_r = min(p[0] for p in changed_positions)
        max_r = max(p[0] for p in changed_positions)
        min_c = min(p[1] for p in changed_positions)
        max_c = max(p[1] for p in changed_positions)
        effect_kind = self._classify_effect(changed, total_cells)
        top_shifts = ", ".join(
            f"{shift} x{count}"
            for shift, count in sorted(color_shifts.items(), key=lambda item: (-item[1], item[0]))[:3]
        )
        summary = (
            f"{effect_kind}: {changed} pixels changed in rows {min_r}-{max_r}, "
            f"cols {min_c}-{max_c}"
        )
        if top_shifts:
            summary += f"; color shifts {top_shifts}"
        return {
            "summary": summary,
            "pixels_changed": changed,
            "regions": [f"r{min_r}-{max_r}_c{min_c}-{max_c}"],
            "effect_kind": effect_kind,
            "color_shifts": color_shifts,
            "bbox": {
                "row_start": min_r,
                "row_end": max_r,
                "col_start": min_c,
                "col_end": max_c,
            },
            "center": {
                "row": round((min_r + max_r) / 2.0, 2),
                "col": round((min_c + max_c) / 2.0, 2),
            },
        }

    def _classify_effect(self, pixels_changed: int, total_cells: int) -> str:
        if pixels_changed <= 0:
            return "no_visible_change"
        if pixels_changed <= 4:
            return "localized_change"
        if total_cells <= 0:
            return "localized_change"
        change_ratio = pixels_changed / total_cells
        if change_ratio <= 0.15:
            return "regional_change"
        return "global_change"

    def _evaluate_meaningful_change(
        self,
        diff: Dict[str, Any],
        reward: float,
        is_new_state: bool,
        looped: bool,
        final_state: str,
        prior_zero_reward_streak: int = 0,
    ) -> Dict[str, Any]:
        reward_signal = max(0.0, min(reward, 1.0))
        base_novelty_signal = 1.0 if is_new_state else 0.0
        pixels_changed = int(diff.get("pixels_changed") or 0)
        effect_visibility = min(pixels_changed / 128.0, 1.0) if pixels_changed > 0 else 0.0
        repeat_zero_reward_penalty = 0.08 * max(prior_zero_reward_streak - 1, 0) if reward_signal == 0.0 else 0.0
        novelty_decay = min(0.15 * max(prior_zero_reward_streak - 1, 0), 0.6) if reward_signal == 0.0 else 0.0
        novelty_signal = base_novelty_signal * (1.0 - novelty_decay)

        if final_state == "WIN" or reward_signal > 0.0:
            progress_signal = 1.0
        elif final_state == "GAME_OVER":
            progress_signal = 0.0
        elif is_new_state and pixels_changed > 0 and not looped:
            progress_signal = 0.25
        elif pixels_changed > 0 and not looped:
            progress_signal = 0.10
        else:
            progress_signal = 0.0

        if reward_signal == 0.0 and prior_zero_reward_streak >= 2:
            progress_signal = min(progress_signal, 0.10)

        loop_penalty = 1.0 if looped else 0.0
        no_change_penalty = 1.0 if pixels_changed == 0 else 0.0

        raw_score = (
            0.40 * reward_signal
            + 0.25 * progress_signal
            + 0.25 * novelty_signal
            + 0.10 * effect_visibility
            - 0.35 * loop_penalty
            - 0.25 * no_change_penalty
            - repeat_zero_reward_penalty
        )
        score = max(0.0, min(raw_score, 1.0))

        reasons: List[str] = []
        if reward_signal > 0.0:
            reasons.append("reward")
        if base_novelty_signal > 0.0:
            reasons.append("novel_state")
        if progress_signal > 0.0 and reward_signal == 0.0:
            reasons.append("new_nonterminal_progress")
        if effect_visibility >= 0.25:
            reasons.append("visible_effect")
        if repeat_zero_reward_penalty > 0.0:
            reasons.append("repeat_zero_reward_decay")
        if looped:
            reasons.append("loop_penalty")
        if no_change_penalty:
            reasons.append("no_visible_change")
        if final_state == "GAME_OVER":
            reasons.append("game_over")

        if score >= 0.75:
            label = "strong_progress"
        elif score >= 0.35:
            label = "tentative_progress"
        elif score > 0.0:
            label = "low_value"
        else:
            label = "no_progress"

        return {
            "score": round(score, 2),
            "label": label,
            "reasons": reasons,
            "reward_signal": reward_signal,
            "novelty_signal": novelty_signal,
            "progress_signal": progress_signal,
            "looped": looped,
            "zero_reward_streak": prior_zero_reward_streak + 1 if reward_signal == 0.0 else 0,
        }

    def _count_zero_reward_streak(self, effects: List[Transition]) -> int:
        streak = 0
        for effect in reversed(effects):
            if effect.reward_signal > 0.0:
                break
            streak += 1
        return streak

    def _build_action_effects(self, available_actions: List[str]) -> List[Dict[str, Any]]:
        action_effects: List[Dict[str, Any]] = []
        for action in available_actions:
            effects = self.graph.get_action_effects(action)
            if not effects:
                action_effects.append(
                    {
                        "action": action,
                        "times_seen": 0,
                        "avg_pixels_changed": 0.0,
                        "avg_meaningful_change": 0.0,
                        "no_change_count": 0,
                        "no_progress_count": 0,
                        "novel_state_count": 0,
                        "reward_hits": 0,
                        "zero_reward_streak": 0,
                        "last_meaningful_label": "UNTESTED",
                        "recent_diff": "UNTESTED",
                    }
                )
                continue

            no_change_count = sum(1 for effect in effects if effect.pixels_changed == 0)
            no_progress_count = sum(1 for effect in effects if effect.meaningful_change_label == "no_progress")
            novel_state_count = sum(1 for effect in effects if effect.novelty_signal > 0)
            reward_hits = sum(1 for effect in effects if effect.reward_signal > 0)
            
            # A066: calculate meaningful progress ratio
            meaningful_hits = sum(1 for effect in effects if getattr(effect, "meaningful_progress", False))
            meaningful_ratio = meaningful_hits / len(effects) if effects else 0.0
            
            zero_reward_streak = self._count_zero_reward_streak(effects)
            avg_pixels_changed = sum(effect.pixels_changed for effect in effects) / len(effects)
            avg_meaningful_change = sum(effect.meaningful_change_score for effect in effects) / len(effects)
            rank_score = self._score_action_rank(effects)
            retest_budget = self._action_retest_budget(effects)
            action_effects.append(
                {
                    "action": action,
                    "times_seen": len(effects),
                    "avg_pixels_changed": round(avg_pixels_changed, 1),
                    "avg_meaningful_change": round(avg_meaningful_change, 2),
                    "meaningful_progress_ratio": round(meaningful_ratio, 2),
                    "no_change_count": no_change_count,
                    "no_progress_count": no_progress_count,
                    "novel_state_count": novel_state_count,
                    "reward_hits": reward_hits,
                    "zero_reward_streak": zero_reward_streak,
                    "last_meaningful_label": effects[-1].meaningful_change_label,
                    "rank_score": round(rank_score, 2),
                    "retest_budget": retest_budget,
                    "over_retest_budget": len(effects) >= retest_budget and effects[-1].meaningful_change_label in {"low_value", "no_progress"},
                    "recent_diff": effects[-1].diff_summary,
                }
            )
        action_effects.sort(key=lambda effect: (-effect.get("rank_score", 0.0), effect.get("times_seen", 0), effect.get("action", "")))
        return action_effects

    def _refresh_action_fact(self, action: str) -> None:
        effects = self.graph.get_action_effects(action)
        if not effects:
            return

        latest_effect = effects[-1]
        consistency = self._measure_effect_consistency(effects)
        value_status = self._classify_action_value(effects, self._score_action_rank(effects))
        evidence_count = len(effects)
        fact_type = self._classify_action_fact(effects, consistency, value_status)
        trend = self._detect_action_trend(effects)
        description = self._describe_action_fact(
            action,
            latest_effect,
            fact_type,
            value_status,
            consistency,
            evidence_count,
            trend,
        )

        fact = ActionFact(
            id=f"fact-{action}",
            action=action,
            fact_type=fact_type,
            description=description,
            consistency=round(consistency, 2),
            value_status=value_status,
            evidence_count=evidence_count,
            trend=trend,
            support_steps=[effect.step for effect in effects],
        )
        self.action_facts[action] = fact
        self._pending_fact_writes = [pending for pending in self._pending_fact_writes if pending.action != action]
        self._pending_fact_writes.append(fact)

    def _classify_action_fact(
        self,
        effects: List[Transition],
        consistency: float,
        value_status: str,
    ) -> str:
        latest = effects[-1]
        attempts = len(effects)

        # 1. Blocked / No-op (B-93)
        if all(effect.pixels_changed == 0 for effect in effects):
            return "blocked"

        # 2. Loop-causing (B-93)
        if any(effect.looped for effect in effects):
            return "loop"

        # 3. Low value (B-93: Consistent-but-low-value actions are not successful)
        if value_status in {"low_value", "ineffective"}:
            return "low_value"

        # 4. Deterministic Visible Effect (B-93)
        if latest.reward_signal > 0.0 or latest.meaningful_change_label == "strong_progress":
            return "deterministic_effect"
        if consistency >= 0.85 and attempts >= self.MIN_EVIDENCE:
            return "deterministic_effect"

        return "repeatable_effect"

    def _describe_action_fact(
        self,
        action: str,
        latest_effect: Transition,
        fact_type: str,
        value_status: str,
        consistency: float,
        evidence_count: int,
        trend: Dict[str, Any] | None,
    ) -> str:
        summary = latest_effect.diff_summary
        trend_clause = self._format_trend_clause(trend)

        if fact_type == "blocked":
            return f"{action} is blocked: {summary}"
        if fact_type == "loop":
            return f"{action} causes state loop: {summary}"
        if fact_type == "deterministic_effect":
            return f"{action} deterministic effect: {summary}{trend_clause}"
        if fact_type == "low_value":
            return f"{action} low-value effect: {summary}{trend_clause}"

        return f"{action} repeatable effect: {summary}{trend_clause}"

    def _refresh_action_hypothesis(self, action: str) -> None:
        action_hypothesis = self.hypotheses.get(f"action-{action}")
        if not action_hypothesis:
            return

        effects = self.graph.get_action_effects(action)
        if not effects:
            return

        attempts = len(effects)
        no_change_count = sum(1 for effect in effects if effect.pixels_changed == 0)
        
        # A066: meaningful progress tracking
        avg_pixels_changed = sum(effect.pixels_changed for effect in effects) / attempts
        avg_meaningful_change = sum(effect.meaningful_change_score for effect in effects) / attempts
        reward_hits = sum(1 for effect in effects if effect.reward_signal > 0)
        
        meaningful_hits = sum(1 for effect in effects if getattr(effect, "meaningful_progress", False))
        meaningful_ratio = meaningful_hits / attempts if attempts > 0 else 0.0
        
        novel_hits = sum(1 for effect in effects if effect.novelty_signal > 0)
        no_progress_hits = sum(1 for effect in effects if effect.meaningful_change_label == "no_progress")
        zero_reward_streak = self._count_zero_reward_streak(effects)
        latest_effect = effects[-1]
        consistency = self._measure_effect_consistency(effects)
        value_score = self._score_action_rank(effects)
        value_status = self._classify_action_value(effects, value_score)

        action_hypothesis.effect_consistency = round(consistency, 2)
        action_hypothesis.value_score = round(value_score, 2)
        action_hypothesis.meaningful_progress_ratio = round(meaningful_ratio, 2)
        action_hypothesis.value_status = value_status
        action_hypothesis.confidence = round(consistency, 2)

        if no_change_count == attempts:
            action_hypothesis.description = (
                f"{action} has produced no visible change in {attempts} attempt(s)"
            )
            action_hypothesis.status = "refuted" if attempts >= self.MIN_EVIDENCE else "active"
            return

        if attempts == 1:
            action_hypothesis.description = (
                f"{action} produced {latest_effect.diff_summary}; "
                f"meaningful_change={latest_effect.meaningful_change_score:.2f} ({latest_effect.meaningful_change_label}); "
                f"value_status={value_status}"
            )
            action_hypothesis.status = "active"
            return

        action_hypothesis.description = (
            f"{action} consistency={consistency:.2f}; "
            f"value_score={value_score:.2f} ({value_status}); "
            f"avg meaningful_change={avg_meaningful_change:.2f}; "
            f"novel {novel_hits}/{attempts}; reward {reward_hits}/{attempts}; "
            f"no_progress {no_progress_hits}/{attempts}; zero_reward_streak {zero_reward_streak}; "
            f"avg pixels {avg_pixels_changed:.1f}; "
            f"last {latest_effect.meaningful_change_label}: {latest_effect.diff_summary}"
        )

        if attempts >= self.MIN_EVIDENCE and value_status == "valuable":
            action_hypothesis.status = "confirmed"
        elif attempts >= self.MIN_EVIDENCE and value_status == "ineffective":
            action_hypothesis.status = "refuted"
        else:
            action_hypothesis.status = "active"

    def _build_action_facts(self) -> List[Dict[str, Any]]:
        facts = list(self.action_facts.values())
        facts.sort(
            key=lambda fact: (
                -fact.consistency,
                -fact.evidence_count,
                fact.action,
            )
        )
        return [
            {
                "id": fact.id,
                "action": fact.action,
                "fact_type": fact.fact_type,
                "description": fact.description,
                "consistency": fact.consistency,
                "value_status": fact.value_status,
                "evidence_count": fact.evidence_count,
                "trend": fact.trend,
                "support_steps": fact.support_steps,
            }
            for fact in facts
        ]

    def _detect_action_trend(self, effects: List[Transition]) -> Dict[str, Any] | None:
        usable = [
            effect for effect in effects
            if effect.pixels_changed > 0 and effect.changed_bbox and effect.changed_center
        ]
        if len(usable) < 2:
            return None

        recent = usable[-4:]
        row_centers = [effect.changed_center["row"] for effect in recent if effect.changed_center]
        col_centers = [effect.changed_center["col"] for effect in recent if effect.changed_center]
        if len(row_centers) < 2 or len(col_centers) < 2:
            return None

        row_deltas = [round(curr - prev, 2) for prev, curr in zip(row_centers, row_centers[1:])]
        col_deltas = [round(curr - prev, 2) for prev, curr in zip(col_centers, col_centers[1:])]
        row_span_stable = self._is_stable_span([effect.changed_bbox for effect in recent], "row")
        col_span_stable = self._is_stable_span([effect.changed_bbox for effect in recent], "col")

        horizontal = self._classify_axis_drift(
            col_deltas,
            axis="col",
            negative_name="left",
            positive_name="right",
        )
        vertical = self._classify_axis_drift(
            row_deltas,
            axis="row",
            negative_name="up",
            positive_name="down",
        )

        if horizontal is None and vertical is None:
            if row_span_stable and col_span_stable:
                return {
                    "kind": "stable_region",
                    "message": "repeats in roughly the same region",
                    "samples": len(recent),
                }
            return None

        dominant = horizontal or vertical
        avg_delta = abs(dominant["avg_delta"])
        axis = dominant["axis"]
        direction = dominant["direction"]
        same_region = row_span_stable and col_span_stable
        region_note = " within a stable region" if same_region else ""
        return {
            "kind": "directional_drift",
            "axis": axis,
            "direction": direction,
            "avg_delta": round(avg_delta, 2),
            "samples": len(recent),
            "stable_region": same_region,
            "message": f"{direction}ward drift by ~{avg_delta:.1f} cell(s)/step{region_note}",
        }

    def _is_stable_span(self, bboxes: List[Dict[str, int] | None], axis: str) -> bool:
        starts: List[int] = []
        ends: List[int] = []
        for bbox in bboxes:
            if not bbox:
                continue
            starts.append(int(bbox[f"{axis}_start"]))
            ends.append(int(bbox[f"{axis}_end"]))
        if len(starts) < 2:
            return False
        return (max(starts) - min(starts) <= 2) and (max(ends) - min(ends) <= 2)

    def _classify_axis_drift(
        self,
        deltas: List[float],
        axis: str,
        negative_name: str,
        positive_name: str,
    ) -> Dict[str, Any] | None:
        if len(deltas) < 1:
            return None
        non_zero = [delta for delta in deltas if abs(delta) >= 0.5]
        if len(non_zero) < max(1, len(deltas) - 1):
            return None
        if all(delta <= -0.5 for delta in non_zero):
            avg_delta = sum(non_zero) / len(non_zero)
            return {"axis": axis, "direction": negative_name, "avg_delta": avg_delta}
        if all(delta >= 0.5 for delta in non_zero):
            avg_delta = sum(non_zero) / len(non_zero)
            return {"axis": axis, "direction": positive_name, "avg_delta": avg_delta}
        return None

    def _format_trend_clause(self, trend: Dict[str, Any] | None) -> str:
        if not trend:
            return ""
        message = trend.get("message")
        if not message:
            return ""
        return f"; trend: {message}"

    def _measure_effect_consistency(self, effects: List[Transition]) -> float:
        if len(effects) <= 1:
            return 0.5
        labels = [effect.meaningful_change_label for effect in effects]
        dominant_label = max(set(labels), key=labels.count)
        label_consistency = labels.count(dominant_label) / len(labels)
        pixel_baseline = effects[-1].pixels_changed
        similar_pixels = sum(1 for effect in effects if abs(effect.pixels_changed - pixel_baseline) <= 4)
        pixel_consistency = similar_pixels / len(effects)
        return max(0.0, min((0.7 * label_consistency) + (0.3 * pixel_consistency), 1.0))

    def _classify_action_value(self, effects: List[Transition], rank_score: float) -> str:
        latest = effects[-1]
        reward_hits = sum(1 for effect in effects if effect.reward_signal > 0.0)
        if reward_hits > 0 or latest.meaningful_change_label == "strong_progress":
            return "valuable"
        if latest.meaningful_change_label == "tentative_progress" and rank_score >= 0.4:
            return "tentative"
        if latest.meaningful_change_label in {"low_value", "no_progress"}:
            return "ineffective" if latest.zero_reward_streak >= 3 else "low_value"
        return "low_value"

    def _score_action_rank(self, effects: List[Transition]) -> float:
        attempts = len(effects)
        latest = effects[-1]
        avg_meaningful_change = sum(effect.meaningful_change_score for effect in effects) / attempts
        novelty_ratio = sum(1 for effect in effects if effect.novelty_signal > 0) / attempts
        reward_ratio = sum(1 for effect in effects if effect.reward_signal > 0) / attempts
        no_progress_ratio = sum(1 for effect in effects if effect.meaningful_change_label == "no_progress") / attempts
        streak_penalty = min(latest.zero_reward_streak * 0.08, 0.4)
        return max(
            0.0,
            min(
                (0.55 * avg_meaningful_change)
                + (0.20 * novelty_ratio)
                + (0.20 * reward_ratio)
                - (0.20 * no_progress_ratio)
                - streak_penalty,
                1.0,
            ),
        )

    def _action_retest_budget(self, effects: List[Transition]) -> int:
        latest = effects[-1]
        if latest.reward_signal > 0.0 or latest.meaningful_change_label == "strong_progress":
            return 4
        if latest.meaningful_change_label == "tentative_progress":
            return 2
        if latest.meaningful_change_label == "low_value":
            return 1
        return 1

    def _summarize_action_coverage(self, action_effects: List[Dict[str, Any]]) -> Dict[str, Any]:
        tested = [effect for effect in action_effects if effect.get("times_seen", 0) > 0]
        untested = [effect.get("action") for effect in action_effects if effect.get("times_seen", 0) <= 0]
        low_value_actions = [
            effect for effect in tested
            if effect.get("last_meaningful_label") in {"low_value", "no_progress"}
        ]
        top_two_decay = len(low_value_actions) >= 2 and all(
            effect.get("avg_meaningful_change", 0.0) <= 0.30 for effect in low_value_actions[:2]
        )
        return {
            "tested_count": len(tested),
            "untested_count": len(untested),
            "untested_actions": untested,
            "initial_exploration_complete": len(untested) == 0,
            "top_two_low_value": top_two_decay,
        }

    def _decide_policy(self, energy: float, action_coverage: Dict[str, Any] | None = None) -> str:
        return self.energy_policy(energy, action_coverage)

    def energy_policy(self, energy: float, action_coverage: Dict[str, Any] | None = None) -> str:
        """Explore/exploit based on energy and hypothesis landscape."""
        active = [h for h in self.hypotheses.values() if h.status == "active"]
        confirmed = [
            h for h in self.hypotheses.values()
            if h.status == "confirmed" and h.confidence > 0.7
        ]
        action_coverage = action_coverage or {}

        if action_coverage.get("untested_count", 0) > 0:
            return "explore"
        if action_coverage.get("top_two_low_value"):
            return "explore"
        if energy < self.EXPLORE_ENERGY_FLOOR:
            return "exploit" if confirmed else "explore"
        if not confirmed and active:
            return "explore"
        if len(active) > len(confirmed):
            return "explore"
        return "exploit"

    def get_best_hypothesis(self) -> Optional[Dict[str, Any]]:
        """Return the highest-confidence actionable hypothesis, if any."""
        candidates = [
            h for h in self.hypotheses.values()
            if h.status in {"active", "confirmed"}
        ]
        if not candidates:
            return None
        best = max(
            candidates,
            key=lambda h: (h.confidence, h.value_score, h.support_count, h.id),
        )
        return {
            "id": best.id,
            "description": best.description,
            "category": best.category,
            "confidence": round(best.confidence, 2),
            "status": best.status,
            "value_score": round(best.value_score, 2),
            "value_status": best.value_status,
        }

    def get_exploration_action(self, available_actions: List[str]) -> str:
        """Prefer an unexplored action from the current state, else fall back safely."""
        if self._prev_state_hash:
            unexplored = self.graph.get_unexplored_actions(self._prev_state_hash, available_actions)
            if unexplored:
                return unexplored[0]

        untested = [action for action in available_actions if not self.graph.get_action_effects(action)]
        if untested:
            return untested[0]

        ranked = self._build_action_effects(available_actions)
        if ranked:
            return ranked[0]["action"]
        return available_actions[0] if available_actions else "ACTION1"

    def _detect_environment_bottleneck(
        self,
        available_actions: List[str],
        action_effects: List[Dict[str, Any]],
    ) -> Dict[str, Any] | None:
        if len(available_actions) != 1:
            return None

        only_action = available_actions[0]
        effect = next((item for item in action_effects if item.get("action") == only_action), None)
        fact = self.action_facts.get(only_action)
        if not effect or not fact:
            return None

        if fact.fact_type != "blocked":
            return None
        if effect.get("times_seen", 0) < 2:
            return None

        return {
            "type": "single_blocked_action",
            "action": only_action,
            "times_seen": effect.get("times_seen", 0),
            "message": (
                f"Environment bottleneck: only {only_action} is available and it is blocked/no-op "
                f"after {effect.get('times_seen', 0)} observation(s)."
            ),
        }

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

    def _all_transitions(self) -> List[Transition]:
        transitions: List[Transition] = []
        for entries in self.graph.edges.values():
            transitions.extend(entries)
        return sorted(transitions, key=lambda transition: transition.step)

    def _build_path_hypotheses(self, limit: int = 3) -> List[Dict[str, Any]]:
        transitions = self._all_transitions()
        if len(transitions) < 2:
            return []

        hypotheses: List[PathHypothesis] = []
        for window_size in (2, 3):
            if len(transitions) < window_size:
                continue
            window = transitions[-window_size:]
            avg_score = sum(transition.meaningful_change_score for transition in window) / window_size
            no_progress = sum(1 for transition in window if transition.meaningful_change_label == "no_progress")
            latest = window[-1]
            component_values = [
                self.action_facts.get(transition.action).value_status
                for transition in window
                if self.action_facts.get(transition.action) is not None
            ]
            all_component_low_value = bool(component_values) and all(
                value in {"low_value", "ineffective"} for value in component_values
            )

            # Detect loop within path (B-94)
            start_hash = window[0].from_hash
            end_hash = window[-1].to_hash
            is_path_loop = (start_hash == end_hash)

            if any(transition.reward_signal > 0.0 for transition in window):
                value_status = "valuable"
            elif is_path_loop:
                value_status = "ineffective"
            elif no_progress == window_size:
                value_status = "ineffective"
            elif all_component_low_value:
                value_status = "low_value"
            elif avg_score >= 0.35:
                value_status = "tentative"
            else:
                value_status = "low_value"

            description = (
                f"path {' -> '.join(transition.action for transition in window)} "
                f"ends in {latest.meaningful_change_label} with avg_score {avg_score:.2f}; "
                f"latest effect: {latest.diff_summary}"
            )
            if is_path_loop:
                description += "; path returns to start (loop)"

            hypotheses.append(
                PathHypothesis(
                    actions=[transition.action for transition in window],
                    description=description,
                    confidence=round(min(1.0, 0.4 + (avg_score * 0.8)), 2),
                    value_status=value_status,
                    support_steps=[transition.step for transition in window],
                )
            )

        hypotheses.sort(key=lambda hyp: (-hyp.confidence, hyp.actions))
        return [
            {
                "actions": hypothesis.actions,
                "description": hypothesis.description,
                "confidence": hypothesis.confidence,
                "value_status": hypothesis.value_status,
                "support_steps": hypothesis.support_steps,
            }
            for hypothesis in hypotheses[:limit]
        ]

    def _snapshot_summary(self, grid_2d: Optional[List[List[int]]], block_count: int = 4) -> Dict[str, Any]:
        if not grid_2d:
            return {
                "rows": 0,
                "cols": 0,
                "top_colors": [],
                "coarse_map": "(empty)",
            }

        rows = len(grid_2d)
        cols = len(grid_2d[0]) if grid_2d and grid_2d[0] else 0
        counter: Counter[int] = Counter()
        for row in grid_2d:
            counter.update(row)
        top_colors = [
            {"value": value, "count": count}
            for value, count in counter.most_common(6)
        ]
        return {
            "rows": rows,
            "cols": cols,
            "top_colors": top_colors,
            "coarse_map": self._coarse_grid_summary(grid_2d, block_count=block_count),
        }

    def _region_snapshot(
        self,
        prev: Optional[List[List[int]]],
        curr: Optional[List[List[int]]],
        max_span: int = 12,
    ) -> Dict[str, Any]:
        if not prev or not curr or not prev[0] or not curr[0]:
            return {
                "row_range": None,
                "col_range": None,
                "before_crop": "(empty)",
                "after_crop": "(empty)",
            }

        changed: list[tuple[int, int]] = []
        for r in range(min(len(prev), len(curr))):
            for c in range(min(len(prev[r]), len(curr[r]))):
                if prev[r][c] != curr[r][c]:
                    changed.append((r, c))

        if not changed:
            return {
                "row_range": None,
                "col_range": None,
                "before_crop": "(no change)",
                "after_crop": "(no change)",
            }

        min_r = min(r for r, _ in changed)
        max_r = max(r for r, _ in changed)
        min_c = min(c for _, c in changed)
        max_c = max(c for _, c in changed)

        row_start, row_end = self._bounded_window(min_r, max_r, len(curr), max_span)
        col_start, col_end = self._bounded_window(min_c, max_c, len(curr[0]), max_span)

        before_crop = self._render_crop(prev, row_start, row_end, col_start, col_end)
        after_crop = self._render_crop(curr, row_start, row_end, col_start, col_end)
        return {
            "row_range": [row_start, row_end],
            "col_range": [col_start, col_end],
            "before_crop": before_crop,
            "after_crop": after_crop,
        }

    def _bounded_window(self, start: int, end: int, limit: int, max_span: int) -> tuple[int, int]:
        span = end - start + 1
        if span >= max_span:
            return start, min(limit - 1, start + max_span - 1)
        padding = max_span - span
        left = padding // 2
        right = padding - left
        bounded_start = max(0, start - left)
        bounded_end = min(limit - 1, end + right)
        if bounded_end - bounded_start + 1 < max_span:
            if bounded_start == 0:
                bounded_end = min(limit - 1, bounded_start + max_span - 1)
            elif bounded_end == limit - 1:
                bounded_start = max(0, bounded_end - max_span + 1)
        return bounded_start, bounded_end

    def _render_crop(
        self,
        grid: List[List[int]],
        row_start: int,
        row_end: int,
        col_start: int,
        col_end: int,
    ) -> str:
        lines: list[str] = []
        for r in range(row_start, min(row_end + 1, len(grid))):
            row = grid[r]
            cells = [str(row[c]) for c in range(col_start, min(col_end + 1, len(row)))]
            lines.append(" ".join(cells))
        return "\n".join(lines) if lines else "(empty)"

    def _coarse_grid_summary(self, grid: List[List[int]], block_count: int = 4) -> str:
        if not grid or not grid[0]:
            return "(empty)"

        rows = len(grid)
        cols = len(grid[0])
        row_block = max(1, rows // block_count)
        col_block = max(1, cols // block_count)
        coarse_rows: list[str] = []
        for row_start in range(0, rows, row_block):
            if len(coarse_rows) >= block_count:
                break
            row_cells: list[str] = []
            for col_start in range(0, cols, col_block):
                if len(row_cells) >= block_count:
                    break
                counts: dict[int, int] = {}
                for r in range(row_start, min(row_start + row_block, rows)):
                    for c in range(col_start, min(col_start + col_block, cols)):
                        value = grid[r][c]
                        counts[value] = counts.get(value, 0) + 1
                dominant = min(counts) if not counts else max(counts, key=counts.get)
                row_cells.append(str(dominant))
            coarse_rows.append(" ".join(row_cells))
        return "\n".join(coarse_rows)

    def compact_exploration(self, current_step: int) -> ExplorationCompaction:
        """B116: Summarize current hypothesis state into a compact artifact."""
        action_summaries = {}
        for action, fact in self.action_facts.items():
            action_summaries[action] = fact.compact_description

        confirmed = []
        refuted = []
        for h in self.hypotheses.values():
            if h.status == "confirmed":
                confirmed.append(h.description)
            elif h.status == "refuted":
                refuted.append(h.description)

        # Detect recent loops from path hypotheses
        known_loops = []
        for path in self._build_path_hypotheses(limit=10):
            if "loop" in path.get("description", "").lower():
                known_loops.append(path["actions"])

        return ExplorationCompaction(
            action_summaries=action_summaries,
            known_loops=known_loops,
            confirmed_rules=confirmed,
            refuted_rules=refuted,
            timestamp_step=current_step,
        )

    async def distill_to_brain(self, object_roles: Dict[int, Any] | None = None) -> int:
        """Flush confirmed + refuted hypotheses, durable action facts, and bootstrap entities to SideQuests.

        Called on WIN or GAME_OVER boundaries. Returns count of entries flushed.
        """
        flushed = 0

        # 0. Bootstrap Entities (B119)
        if object_roles:
            for color_id, role in object_roles.items():
                if role.role != "unknown":
                    text = f"[BOOTSTRAP ENTITY] color_{color_id} identified as {role.role.value} (confidence={role.confidence:.2f})"
                    try:
                        await self.brain.notify_turn(
                            role="assistant",
                            content=text,
                            session_id=self.session_id,
                        )
                        flushed += 1
                    except Exception as e:
                        logger.error(f"Failed to distill bootstrap entity {color_id}: {e}")

        # 1. Action Facts (B-93)
        for fact in self.action_facts.values():
            if fact.evidence_count >= self.MIN_EVIDENCE or fact.fact_type in {"blocked", "loop"}:
                text = f"[ACTION FACT] {fact.compact_description} (type: {fact.fact_type}, consistency: {fact.consistency:.2f}, evidence: {fact.evidence_count})"
                try:
                    await self.brain.notify_turn(
                        role="assistant",
                        content=text,
                        session_id=self.session_id,
                    )
                    flushed += 1
                except Exception as e:
                    logger.error(f"Failed to distill action fact {fact.id}: {e}")

        # 2. Strategic Hypotheses
        for h in self.hypotheses.values():
            if h.status in ("confirmed", "refuted", "pruned"):
                text = f"[{h.status.upper()}] {h.description} (confidence: {h.confidence:.2f}, evidence: {h.support_count}+/{h.contradiction_count}-)"
                try:
                    await self.brain.notify_turn(
                        role="assistant",
                        content=text,
                        session_id=self.session_id,
                    )
                    flushed += 1
                except Exception as e:
                    logger.error(f"Failed to distill hypothesis {h.id}: {e}")
        return flushed

    def reset_graph(self) -> None:
        """Clear ephemeral state for retry. Hypotheses survive."""
        self.graph.clear()
        self.invariant_detector.clear()
        self._prev_state_hash = None
        self._prev_grid_2d = None
