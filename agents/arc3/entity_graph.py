"""B168: Graph-Based Exploration Agent — Entity Graph Builder.

Builds a KuzuDB knowledge substrate for ARC puzzle entities through:
  Phase 1: Static structural analysis (no actions consumed)
  Phase 2a: Behavioral discovery via deterministic action sweep
  + Dual inference engine (Tier 1-3 blocking, Tier 4 LLM background)
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

from agents.arc3.grid_analysis import GridDiffEngine, PatternRegion
from agents.arc3.solver import RoleType, ObjectRole
if TYPE_CHECKING:
    from typing import Any as KuzuClient

logger = logging.getLogger(__name__)


@dataclass
class InferenceResult:
    """Result of one inference pass after an exploration step."""
    entities_updated: int = 0
    frontier_size: int = 0
    frontier: List[dict] = field(default_factory=list)


class EntityGraphBuilder:
    """Builds a graph-based knowledge substrate for ARC puzzle entities in KuzuDB.

    Two-phase exploration:
      Phase 1 (bootstrap): Static analysis of initial grid — entities, spatial
        relationships, structural similarity. No actions consumed.
      Phase 2a (record_action_effect): After each action, record what moved/changed,
        then run dual inference (deterministic + background LLM).

    Inference tiers (run after each action):
      Tier 1 — Similarity propagation (blocking, fast)
      Tier 2 — Relational inference: blocking, co-movement, co-occurrence (blocking)
      Tier 3 — Role elimination / constraint propagation (blocking)
      Tier 4 — LLM causal reasoning on CORRELATES_WITH edges (background, non-blocking)
    """

    def __init__(self, db: "KuzuClient", task_id: str, llm_client: Any = None):
        self.db = db
        self.task_id = task_id
        self.llm = llm_client
        self._entities: Dict[str, dict] = {}  # entity_id -> properties (local cache)
        self._pending_llm_inference: Optional[asyncio.Task] = None

    # ══════════════════════════════════════════════════════════════════
    # Phase 1: Static Analysis
    # ══════════════════════════════════════════════════════════════════

    async def bootstrap(self, grid: List[List[int]], level: int, observation: dict) -> dict:
        """Step 0: Extract all entities and structural relationships. No actions consumed."""
        if not grid:
            return {"n_entities": 0}

        rows = len(grid)
        cols = len(grid[0])
        total_pixels = rows * cols
        diff_engine = GridDiffEngine()

        # 1. Extract connected components (including background)
        components = diff_engine.extract_connected_components(grid, color=-1, include_background=True)

        # 2. Create GridSnapshot node
        snapshot_id = f"{self.task_id}_{level}_step0"
        now = datetime.now().isoformat()

        await self.db.execute_write(
            "MERGE (s:GridSnapshot {snapshot_id: $id}) "
            "SET s.task_id = $task_id, s.level = $level, s.step = 0, "
            "s.rows = $rows, s.cols = $cols, s.n_entities = $n_entities, "
            "s.created_at = timestamp($now)",
            {"id": snapshot_id, "task_id": self.task_id, "level": level,
             "rows": rows, "cols": cols, "n_entities": len(components), "now": now},
        )

        # 3. Create GridEntity nodes
        for idx, comp in enumerate(components):
            entity_id = f"{self.task_id}_{level}_{comp.color}_{idx}"
            min_r, min_c, max_r, max_c = comp.bounding_box
            bbox_h = max_r - min_r + 1
            bbox_w = max_c - min_c + 1
            bbox_area = max(bbox_h * bbox_w, 1)

            props = {
                "entity_id": entity_id,
                "task_id": self.task_id,
                "level": level,
                "color_id": int(comp.color),
                "region_index": idx,
                "pixel_count": int(comp.size),
                "centroid_row": float(sum(r for r, c in comp.cells) / comp.size),
                "centroid_col": float(sum(c for r, c in comp.cells) / comp.size),
                "bbox_min_row": int(min_r), "bbox_min_col": int(min_c),
                "bbox_max_row": int(max_r), "bbox_max_col": int(max_c),
                "location_hint": self._compute_location_hint(comp.bounding_box, rows, cols),
                "aspect_ratio": float(bbox_w / max(bbox_h, 1)),
                "compactness": float(comp.size / bbox_area),
                "is_background": comp.color == 0 or comp.size > total_pixels * 0.5,
                "is_mobile": False,
                "is_interactive": False,
                "inferred_role": "unknown",
                "role_confidence": 0.0,
                "created_at": now,
            }

            await self.db.execute_write(
                "MERGE (e:GridEntity {entity_id: $entity_id}) "
                "SET e.task_id = $task_id, e.level = $level, "
                "e.color_id = $color_id, e.region_index = $region_index, "
                "e.pixel_count = $pixel_count, "
                "e.centroid_row = $centroid_row, e.centroid_col = $centroid_col, "
                "e.bbox_min_row = $bbox_min_row, e.bbox_min_col = $bbox_min_col, "
                "e.bbox_max_row = $bbox_max_row, e.bbox_max_col = $bbox_max_col, "
                "e.location_hint = $location_hint, "
                "e.aspect_ratio = $aspect_ratio, e.compactness = $compactness, "
                "e.is_background = $is_background, "
                "e.is_mobile = $is_mobile, e.is_interactive = $is_interactive, "
                "e.inferred_role = $inferred_role, e.role_confidence = $role_confidence, "
                "e.created_at = timestamp($created_at)",
                props,
            )

            # Link to snapshot
            await self.db.execute_write(
                "MATCH (e:GridEntity {entity_id: $eid}), (s:GridSnapshot {snapshot_id: $sid}) "
                "MERGE (e)-[:OBSERVED_IN {step: 0}]->(s)",
                {"eid": entity_id, "sid": snapshot_id},
            )
            self._entities[entity_id] = props

        # 4. Build structural edges (ADJACENT_TO, CONTAINS, SAME_COLOR_AS, STRUCTURALLY_SIMILAR)
        await self._build_structural_edges(grid, level)

        logger.info("B168: bootstrap created %d entities for %s", len(self._entities), self.task_id)
        return {"n_entities": len(self._entities)}

    # ══════════════════════════════════════════════════════════════════
    # Phase 2a: Record Action Effect
    # ══════════════════════════════════════════════════════════════════

    async def record_action_effect(
        self, grid_before, grid_after, action_id, step: int, level: int
    ) -> InferenceResult:
        """After one action: record what changed, then run inference."""
        diff_engine = GridDiffEngine()
        delta = diff_engine.diff_frames(grid_before, grid_after, action_id)

        effect_id = f"{self.task_id}_{level}_{action_id}_{step}"
        now = datetime.now().isoformat()

        # Create ActionEffect node
        dir_row = float(delta.direction[0]) if delta.direction else 0.0
        dir_col = float(delta.direction[1]) if delta.direction else 0.0
        await self.db.execute_write(
            "MERGE (a:ActionEffect {effect_id: $id}) "
            "SET a.task_id = $task_id, a.level = $level, a.action_id = $action_id, "
            "a.step = $step, a.n_cells_changed = $n_cells, "
            "a.apparent_effect = $effect, a.direction_row = $dir_row, "
            "a.direction_col = $dir_col, a.created_at = timestamp($now)",
            {"id": effect_id, "task_id": self.task_id, "level": level,
             "action_id": action_id, "step": step,
             "n_cells": int(delta.n_cells_changed),
             "effect": delta.apparent_effect,
             "dir_row": dir_row, "dir_col": dir_col, "now": now},
        )

        # Match entities across frames by color + centroid proximity
        comps_after = diff_engine.extract_connected_components(
            grid_after, color=-1, include_background=True
        )
        moved_eids: Set[str] = set()
        responded_eids: Set[str] = set()

        for comp_a in comps_after:
            c_row = sum(r for r, c in comp_a.cells) / comp_a.size
            c_col = sum(c for r, c in comp_a.cells) / comp_a.size

            best_match = None
            best_dist = 999.0
            for eid, e_props in self._entities.items():
                if e_props["color_id"] != comp_a.color:
                    continue
                d = ((e_props["centroid_row"] - c_row) ** 2
                     + (e_props["centroid_col"] - c_col) ** 2) ** 0.5
                if d < best_dist:
                    best_dist = d
                    best_match = eid

            if not best_match:
                continue

            # Movement detection: centroid shift > threshold means entity moved
            if best_dist > 0.35:
                dr = c_row - self._entities[best_match]["centroid_row"]
                dc = c_col - self._entities[best_match]["centroid_col"]

                await self.db.execute_write(
                    "MATCH (e:GridEntity {entity_id: $eid}), (a:ActionEffect {effect_id: $aid}) "
                    "MERGE (e)-[:MOVED_BY {delta_row: $dr, delta_col: $dc}]->(a) "
                    "SET e.is_mobile = true",
                    {"eid": best_match, "aid": effect_id, "dr": float(dr), "dc": float(dc)},
                )
                self._entities[best_match]["is_mobile"] = True
                # Update cached centroid
                self._entities[best_match]["centroid_row"] = c_row
                self._entities[best_match]["centroid_col"] = c_col
                moved_eids.add(best_match)

            # Size change detection: entity responded but didn't move
            elif abs(comp_a.size - self._entities[best_match]["pixel_count"]) >= 1:
                effect_type = "grew" if comp_a.size > self._entities[best_match]["pixel_count"] else "shrank"
                await self.db.execute_write(
                    "MATCH (e:GridEntity {entity_id: $eid}), (a:ActionEffect {effect_id: $aid}) "
                    "MERGE (e)-[:RESPONDS_TO {effect_type: $et}]->(a) "
                    "SET e.is_interactive = true",
                    {"eid": best_match, "aid": effect_id, "et": effect_type},
                )
                self._entities[best_match]["is_interactive"] = True
                self._entities[best_match]["pixel_count"] = comp_a.size
                responded_eids.add(best_match)

        # Run dual inference after recording
        inference_result = await self.run_inference(step, effect_id, moved_eids, responded_eids)
        return inference_result

    # ══════════════════════════════════════════════════════════════════
    # DUAL INFERENCE ENGINE
    # ══════════════════════════════════════════════════════════════════

    async def run_inference(
        self, step: int, effect_id: str = "",
        moved_eids: Optional[Set[str]] = None,
        responded_eids: Optional[Set[str]] = None,
    ) -> InferenceResult:
        """Run deterministic inference (blocking) + kick off LLM inference (background)."""
        moved_eids = moved_eids or set()
        responded_eids = responded_eids or set()

        # Foreground: deterministic rules (instant)
        t1 = await self._tier1_similarity_propagation(step, moved_eids)
        t2 = await self._tier2_relational_inference(step, effect_id, moved_eids, responded_eids)
        t3 = await self._tier3_role_elimination(step)

        # Background: LLM causal inference (non-blocking)
        if self.llm:
            self._kick_off_llm_inference(step)

        # Compute exploration frontier
        frontier = await self._get_exploration_frontier()

        return InferenceResult(
            entities_updated=t1 + t2 + t3,
            frontier_size=len(frontier),
            frontier=frontier,
        )

    # ── Tier 1: Similarity Propagation ──────────────────────────────

    async def _tier1_similarity_propagation(self, step: int, moved_eids: Set[str]) -> int:
        """Propagate is_mobile through STRUCTURALLY_SIMILAR and SAME_COLOR_AS edges."""
        changes = 0
        if not moved_eids:
            return changes

        # Propagate is_mobile from moved entities to structurally similar ones
        result = await self.db.execute_read(
            "MATCH (observed:GridEntity)-[s:STRUCTURALLY_SIMILAR]->(similar:GridEntity) "
            "WHERE observed.task_id = $task_id "
            "AND observed.is_mobile = true "
            "AND similar.is_mobile = false "
            "AND similar.is_background = false "
            "AND s.similarity >= 0.7 "
            "RETURN similar.entity_id AS eid, s.similarity AS sim",
            {"task_id": self.task_id},
        )
        for row in result:
            eid = row["eid"]
            sim = row.get("sim", 0.7)
            await self.db.execute_write(
                "MATCH (e:GridEntity {entity_id: $eid}) "
                "SET e.is_mobile = true, e.role_confidence = $conf",
                {"eid": eid, "conf": float(sim * 0.6)},
            )
            if eid in self._entities:
                self._entities[eid]["is_mobile"] = True
                self._entities[eid]["role_confidence"] = sim * 0.6
            changes += 1

        # Propagate is_mobile from moved entities to same-color siblings
        result = await self.db.execute_read(
            "MATCH (observed:GridEntity)-[:SAME_COLOR_AS]->(other:GridEntity) "
            "WHERE observed.task_id = $task_id "
            "AND observed.is_mobile = true "
            "AND other.is_mobile = false "
            "AND other.is_background = false "
            "RETURN other.entity_id AS eid",
            {"task_id": self.task_id},
        )
        for row in result:
            eid = row["eid"]
            await self.db.execute_write(
                "MATCH (e:GridEntity {entity_id: $eid}) "
                "SET e.is_mobile = true, e.role_confidence = $conf",
                {"eid": eid, "conf": 0.4},
            )
            if eid in self._entities:
                self._entities[eid]["is_mobile"] = True
                self._entities[eid]["role_confidence"] = 0.4
            changes += 1

        return changes

    # ── Tier 2: Relational Inference ────────────────────────────────

    async def _tier2_relational_inference(
        self, step: int, effect_id: str,
        moved_eids: Set[str], responded_eids: Set[str],
    ) -> int:
        """Infer relationships from behavioral co-occurrence."""
        changes = 0
        if not effect_id:
            return changes

        # Co-movement: entities that both moved on the same action with similar deltas
        if len(moved_eids) >= 2:
            co_move_result = await self.db.execute_read(
                "MATCH (a:GridEntity)-[ma:MOVED_BY]->(effect:ActionEffect {effect_id: $eid}), "
                "(b:GridEntity)-[mb:MOVED_BY]->(effect) "
                "WHERE a.entity_id <> b.entity_id "
                "RETURN a.entity_id AS a_eid, b.entity_id AS b_eid, "
                "ma.delta_row AS a_dr, ma.delta_col AS a_dc, "
                "mb.delta_row AS b_dr, mb.delta_col AS b_dc",
                {"eid": effect_id},
            )
            for row in co_move_result:
                dr_diff = abs(row.get("a_dr", 0) - row.get("b_dr", 0))
                dc_diff = abs(row.get("a_dc", 0) - row.get("b_dc", 0))
                if dr_diff < 1.0 and dc_diff < 1.0:
                    await self.db.execute_write(
                        "MATCH (a:GridEntity {entity_id: $a_eid}), "
                        "(b:GridEntity {entity_id: $b_eid}) "
                        "MERGE (a)-[:CO_MOVES_WITH {step: $step}]->(b)",
                        {"a_eid": row["a_eid"], "b_eid": row["b_eid"], "step": step},
                    )
                    changes += 1

        # Co-occurrence: mover + reactor on same action → CORRELATES_WITH
        if moved_eids and responded_eids:
            for mover_eid in moved_eids:
                for reactor_eid in responded_eids:
                    if mover_eid == reactor_eid:
                        continue
                    await self.db.execute_write(
                        "MATCH (mover:GridEntity {entity_id: $m_eid}), "
                        "(reactor:GridEntity {entity_id: $r_eid}) "
                        "MERGE (mover)-[:CORRELATES_WITH {step: $step, mechanism: $mech}]->(reactor)",
                        {"m_eid": mover_eid, "r_eid": reactor_eid,
                         "step": step, "mech": "unknown"},
                    )
                    changes += 1

        # Blocking inference: entity tried to move toward ADJACENT entity but didn't
        # (handled implicitly — non-mobile entities adjacent to mobile ones that
        #  didn't move despite action are blocking candidates)
        if moved_eids:
            for mover_eid in moved_eids:
                # Find entities adjacent to mover that are NOT mobile
                adj_result = await self.db.execute_read(
                    "MATCH (mover:GridEntity {entity_id: $eid})-[:ADJACENT_TO]->(blocker:GridEntity) "
                    "WHERE blocker.is_mobile = false AND blocker.is_background = false "
                    "RETURN blocker.entity_id AS b_eid",
                    {"eid": mover_eid},
                )
                for row in adj_result:
                    await self.db.execute_write(
                        "MATCH (blocker:GridEntity {entity_id: $b_eid}), "
                        "(mover:GridEntity {entity_id: $m_eid}) "
                        "MERGE (blocker)-[:BLOCKS {action_id: $aid, step: $step}]->(mover)",
                        {"b_eid": row["b_eid"], "m_eid": mover_eid,
                         "aid": effect_id, "step": step},
                    )
                    changes += 1

        return changes

    # ── Tier 3: Role Elimination ────────────────────────────────────

    async def _tier3_role_elimination(self, step: int) -> int:
        """Eliminate impossible roles based on confirmed assignments."""
        changes = 0

        # If a player is confirmed (most mobile, high confidence), constrain others
        player_result = await self.db.execute_read(
            "MATCH (e:GridEntity)-[m:MOVED_BY]->(a:ActionEffect) "
            "WHERE e.task_id = $task_id AND e.is_background = false "
            "WITH e, count(m) AS move_count "
            "ORDER BY move_count DESC LIMIT 1 "
            "RETURN e.entity_id AS eid, e.color_id AS color_id, move_count",
            {"task_id": self.task_id},
        )

        if player_result:
            player_eid = player_result[0]["eid"]
            player_color = player_result[0]["color_id"]
            move_count = player_result[0]["move_count"]

            if move_count >= 2:
                # High confidence player — update role
                conf = min(0.5 + move_count * 0.15, 0.95)
                await self.db.execute_write(
                    "MATCH (e:GridEntity {entity_id: $eid}) "
                    "SET e.inferred_role = 'player', e.role_confidence = $conf",
                    {"eid": player_eid, "conf": conf},
                )
                if player_eid in self._entities:
                    self._entities[player_eid]["inferred_role"] = "player"
                    self._entities[player_eid]["role_confidence"] = conf
                changes += 1

                # Propagate wall role to large stationary entities
                wall_result = await self.db.execute_read(
                    "MATCH (e:GridEntity) "
                    "WHERE e.task_id = $task_id AND e.is_background = false "
                    "AND e.is_mobile = false AND e.pixel_count > 15 "
                    "AND e.inferred_role = 'unknown' "
                    "AND (e.aspect_ratio < 0.25 OR e.aspect_ratio > 4.0 OR e.compactness < 0.3) "
                    "RETURN e.entity_id AS eid",
                    {"task_id": self.task_id},
                )
                for row in wall_result:
                    await self.db.execute_write(
                        "MATCH (e:GridEntity {entity_id: $eid}) "
                        "SET e.inferred_role = 'wall', e.role_confidence = $conf",
                        {"eid": row["eid"], "conf": 0.65},
                    )
                    if row["eid"] in self._entities:
                        self._entities[row["eid"]]["inferred_role"] = "wall"
                        self._entities[row["eid"]]["role_confidence"] = 0.65
                    changes += 1

                # Propagate wall via STRUCTURALLY_SIMILAR to confirmed walls
                sim_wall_result = await self.db.execute_read(
                    "MATCH (wall:GridEntity)-[:STRUCTURALLY_SIMILAR]->(other:GridEntity) "
                    "WHERE wall.task_id = $task_id "
                    "AND wall.inferred_role = 'wall' "
                    "AND other.inferred_role = 'unknown' "
                    "AND other.is_background = false "
                    "RETURN other.entity_id AS eid",
                    {"task_id": self.task_id},
                )
                for row in sim_wall_result:
                    await self.db.execute_write(
                        "MATCH (e:GridEntity {entity_id: $eid}) "
                        "SET e.inferred_role = 'wall', e.role_confidence = $conf",
                        {"eid": row["eid"], "conf": 0.55},
                    )
                    if row["eid"] in self._entities:
                        self._entities[row["eid"]]["inferred_role"] = "wall"
                        self._entities[row["eid"]]["role_confidence"] = 0.55
                    changes += 1

        return changes

    # ── Tier 4: LLM Causal Inference (background) ──────────────────

    def _kick_off_llm_inference(self, step: int):
        """Launch background LLM inference task (non-blocking)."""
        if self._pending_llm_inference and not self._pending_llm_inference.done():
            self._pending_llm_inference.cancel()
        self._pending_llm_inference = asyncio.create_task(
            self._llm_causal_inference(step)
        )

    async def _llm_causal_inference(self, step: int):
        """Background: LLM examines CORRELATES_WITH edges to explain causation."""
        try:
            # Find unexplained correlations
            corr_result = await self.db.execute_read(
                "MATCH (mover:GridEntity)-[c:CORRELATES_WITH]->(reactor:GridEntity) "
                "WHERE mover.task_id = $task_id AND c.mechanism = 'unknown' "
                "RETURN mover.entity_id AS m_eid, mover.color_id AS m_color, "
                "mover.pixel_count AS m_size, mover.inferred_role AS m_role, "
                "reactor.entity_id AS r_eid, reactor.color_id AS r_color, "
                "reactor.pixel_count AS r_size, reactor.inferred_role AS r_role, "
                "c.step AS step",
                {"task_id": self.task_id},
            )

            if not corr_result:
                return

            # Build context for LLM
            lines = ["You are analyzing an ARC puzzle grid. The following entity pairs changed simultaneously:"]
            for row in corr_result:
                lines.append(
                    f"- Entity (color {row['m_color']}, {row['m_size']}px, role={row['m_role']}) "
                    f"MOVED on step {row['step']}. "
                    f"Entity (color {row['r_color']}, {row['r_size']}px, role={row['r_role']}) "
                    f"CHANGED (size/state) on the same step."
                )
            lines.append(
                "\nFor each pair, explain the likely causal mechanism in ONE phrase "
                "(e.g. 'movement_costs_health', 'switch_opens_gate', 'score_counter'). "
                "Reply as JSON: [{\"mover\": <color>, \"reactor\": <color>, \"mechanism\": \"...\"}]"
            )
            prompt = "\n".join(lines)

            response = await self.llm.achat([{"role": "user", "content": prompt}])
            text = response.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]

            import json
            explanations = json.loads(text.strip())

            for expl in explanations:
                mechanism = expl.get("mechanism", "unknown")
                if mechanism == "unknown":
                    continue
                mover_color = expl.get("mover")
                reactor_color = expl.get("reactor")

                # Update CORRELATES_WITH mechanism
                await self.db.execute_write(
                    "MATCH (m:GridEntity)-[c:CORRELATES_WITH]->(r:GridEntity) "
                    "WHERE m.task_id = $task_id AND m.color_id = $mc AND r.color_id = $rc "
                    "SET c.mechanism = $mech",
                    {"task_id": self.task_id, "mc": mover_color,
                     "rc": reactor_color, "mech": mechanism},
                )

                # Create CAUSES_CHANGE_IN edge
                await self.db.execute_write(
                    "MATCH (m:GridEntity), (r:GridEntity) "
                    "WHERE m.task_id = $task_id AND m.color_id = $mc "
                    "AND r.task_id = $task_id AND r.color_id = $rc "
                    "MERGE (m)-[:CAUSES_CHANGE_IN {mechanism: $mech, confidence: $conf, step: $step}]->(r)",
                    {"task_id": self.task_id, "mc": mover_color,
                     "rc": reactor_color, "mech": mechanism, "conf": 0.7, "step": step},
                )

            logger.info("B168: LLM causal inference found %d mechanisms", len(explanations))

        except asyncio.CancelledError:
            pass  # Superseded by newer inference
        except Exception as e:
            logger.warning("B168: LLM causal inference failed (non-fatal): %s", e)

    # ══════════════════════════════════════════════════════════════════
    # Exploration Frontier
    # ══════════════════════════════════════════════════════════════════

    async def _get_exploration_frontier(self) -> List[dict]:
        """Return entities that still need investigation."""
        result = await self.db.execute_read(
            "MATCH (e:GridEntity) "
            "WHERE e.task_id = $task_id "
            "AND e.is_background = false "
            "AND e.role_confidence < 0.5 "
            "AND e.inferred_role = 'unknown' "
            "RETURN e.entity_id AS eid, e.color_id AS color_id, e.pixel_count AS size "
            "ORDER BY e.pixel_count ASC",
            {"task_id": self.task_id},
        )
        return result

    async def get_exploration_summary(self) -> dict:
        """Return structured summary of what's known for LLM follow-up."""
        # Collect any background LLM inference results first
        if self._pending_llm_inference and not self._pending_llm_inference.done():
            try:
                await asyncio.wait_for(self._pending_llm_inference, timeout=2.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pass

        mobile = await self.db.execute_read(
            "MATCH (e:GridEntity) "
            "WHERE e.task_id = $task_id AND e.is_mobile = true "
            "RETURN e.entity_id AS eid, e.color_id AS color_id",
            {"task_id": self.task_id},
        )
        static = await self.db.execute_read(
            "MATCH (e:GridEntity) "
            "WHERE e.task_id = $task_id AND e.is_mobile = false AND e.is_background = false "
            "RETURN e.entity_id AS eid, e.color_id AS color_id",
            {"task_id": self.task_id},
        )
        causal = await self.db.execute_read(
            "MATCH (a:GridEntity)-[c:CAUSES_CHANGE_IN]->(b:GridEntity) "
            "WHERE a.task_id = $task_id "
            "RETURN a.color_id AS mover, b.color_id AS reactor, c.mechanism AS mechanism",
            {"task_id": self.task_id},
        )
        unexplained = await self.db.execute_read(
            "MATCH (a:GridEntity)-[c:CORRELATES_WITH]->(b:GridEntity) "
            "WHERE a.task_id = $task_id AND c.mechanism = 'unknown' "
            "RETURN a.color_id AS mover, b.color_id AS reactor",
            {"task_id": self.task_id},
        )
        frontier = await self._get_exploration_frontier()

        return {
            "mobile_entities": mobile,
            "static_entities": static,
            "causal_chains": causal,
            "unexplained_correlations": unexplained,
            "exploration_frontier": frontier,
        }

    # ══════════════════════════════════════════════════════════════════
    # Role Inference Queries
    # ══════════════════════════════════════════════════════════════════

    async def infer_player(self) -> Optional[dict]:
        """Most mobile non-background entity."""
        result = await self.db.execute_read(
            "MATCH (e:GridEntity)-[m:MOVED_BY]->(a:ActionEffect) "
            "WHERE e.is_background = false AND e.task_id = $task_id "
            "WITH e, count(m) AS move_count "
            "RETURN e.entity_id AS eid, e.color_id AS color_id, "
            "e.centroid_row AS centroid_row, e.centroid_col AS centroid_col, "
            "e.pixel_count AS pixel_count, move_count "
            "ORDER BY move_count DESC, e.pixel_count ASC LIMIT 1",
            {"task_id": self.task_id},
        )
        return result[0] if result else None

    async def infer_goal(self) -> Optional[dict]:
        """Stationary, structurally unique, non-background entity likely to be the goal."""
        result = await self.db.execute_read(
            "MATCH (e:GridEntity) "
            "WHERE e.task_id = $task_id AND e.is_background = false "
            "AND e.is_mobile = false AND e.is_interactive = false "
            "AND e.inferred_role = 'unknown' "
            "AND e.pixel_count >= 2 AND e.pixel_count <= 50 "
            "RETURN e.entity_id AS eid, e.color_id AS color_id, "
            "e.centroid_row AS centroid_row, e.centroid_col AS centroid_col, "
            "e.pixel_count AS pixel_count, e.compactness AS compactness "
            "ORDER BY e.compactness DESC, e.pixel_count ASC LIMIT 1",
            {"task_id": self.task_id},
        )
        return result[0] if result else None

    async def infer_walls(self) -> List[dict]:
        """Large, elongated, never-moving entities."""
        result = await self.db.execute_read(
            "MATCH (e:GridEntity) "
            "WHERE e.task_id = $task_id AND e.is_background = false "
            "AND e.is_mobile = false AND e.pixel_count > 15 "
            "AND (e.aspect_ratio < 0.25 OR e.aspect_ratio > 4.0 OR e.compactness < 0.3) "
            "RETURN e.entity_id AS eid, e.color_id AS color_id, "
            "e.centroid_row AS centroid_row, e.centroid_col AS centroid_col, "
            "e.pixel_count AS pixel_count, e.aspect_ratio AS aspect_ratio",
            {"task_id": self.task_id},
        )
        return result

    async def infer_intermediates(self) -> List[dict]:
        """Small, stationary, structurally grouped entities."""
        # Try STRUCTURALLY_SIMILAR first (preferred)
        result = await self.db.execute_read(
            "MATCH (e:GridEntity)-[s:STRUCTURALLY_SIMILAR]->(other:GridEntity) "
            "WHERE e.task_id = $task_id AND e.is_background = false "
            "AND e.is_mobile = false AND e.pixel_count >= 2 AND e.pixel_count <= 20 "
            "WITH e, count(other) AS similar_count "
            "RETURN e.entity_id AS eid, e.color_id AS color_id, "
            "e.centroid_row AS centroid_row, e.centroid_col AS centroid_col, "
            "similar_count "
            "ORDER BY similar_count DESC",
            {"task_id": self.task_id},
        )
        if result:
            return result

        # Fallback: size + stationary heuristic
        return await self.db.execute_read(
            "MATCH (e:GridEntity) "
            "WHERE e.task_id = $task_id AND e.is_background = false "
            "AND e.is_mobile = false "
            "AND e.pixel_count >= 2 AND e.pixel_count <= 20 "
            "AND e.inferred_role = 'unknown' "
            "RETURN e.entity_id AS eid, e.color_id AS color_id, "
            "e.centroid_row AS centroid_row, e.centroid_col AS centroid_col",
            {"task_id": self.task_id},
        )

    async def get_entity_roles(self) -> Dict[int, ObjectRole]:
        """Aggregate all inferences into ObjectRole dict keyed by color_id."""
        roles: Dict[int, ObjectRole] = {}

        # Player
        player = await self.infer_player()
        if player:
            move_count = player.get("move_count", 1)
            conf = min(0.5 + move_count * 0.15, 0.95)
            roles[player["color_id"]] = ObjectRole(
                color_id=player["color_id"],
                role=RoleType.PLAYER,
                confidence=conf,
                estimated_position={
                    "row": player["centroid_row"],
                    "col": player["centroid_col"],
                },
            )

        # Goal
        goal = await self.infer_goal()
        if goal and goal["color_id"] not in roles:
            roles[goal["color_id"]] = ObjectRole(
                color_id=goal["color_id"],
                role=RoleType.GOAL,
                confidence=0.55,
                estimated_position={
                    "row": goal["centroid_row"],
                    "col": goal["centroid_col"],
                },
            )

        # Walls
        walls = await self.infer_walls()
        for w in walls:
            if w["color_id"] not in roles:
                # Confidence scales with how elongated the entity is
                ar = w.get("aspect_ratio", 1.0)
                elongation = max(ar, 1.0 / max(ar, 0.01))
                conf = min(0.5 + elongation * 0.05, 0.85)
                roles[w["color_id"]] = ObjectRole(
                    color_id=w["color_id"],
                    role=RoleType.WALL,
                    confidence=conf,
                    estimated_position={
                        "row": w["centroid_row"],
                        "col": w["centroid_col"],
                    },
                )

        # Intermediates
        inters = await self.infer_intermediates()
        for i in inters:
            if i["color_id"] not in roles:
                similar_count = i.get("similar_count", 0)
                conf = min(0.45 + similar_count * 0.1, 0.80)
                roles[i["color_id"]] = ObjectRole(
                    color_id=i["color_id"],
                    role=RoleType.INTERMEDIATE,
                    confidence=conf,
                    estimated_position={
                        "row": i["centroid_row"],
                        "col": i["centroid_col"],
                    },
                )

        return roles

    async def persist_role(self, color_id: int, role: str, confidence: float,
                           position: Optional[Dict[str, float]] = None,
                           level: int = 0) -> None:
        """B169: Write a role assignment to KuzuDB GridEntity node.
        Creates or updates the GridEntity for this color_id."""
        entity_id = f"{self.task_id}_L{level}_c{color_id}"
        now = datetime.now().isoformat()
        
        # MERGE — create if missing, always update role fields
        await self.db.execute_write(
            """
            MERGE (e:GridEntity {entity_id: $eid})
            ON CREATE SET e.task_id = $tid, e.level = $level, e.color_id = $cid,
                          e.inferred_role = $role, e.role_confidence = $conf,
                          e.centroid_row = $crow, e.centroid_col = $ccol,
                          e.created_at = timestamp($now)
            ON MATCH SET e.inferred_role = $role, e.role_confidence = $conf,
                         e.centroid_row = CASE WHEN $crow IS NOT NULL THEN $crow ELSE e.centroid_row END,
                         e.centroid_col = CASE WHEN $ccol IS NOT NULL THEN $ccol ELSE e.centroid_col END,
                         e.last_updated_step = $step
            """,
            {
                "eid": entity_id, "tid": self.task_id, "level": level,
                "cid": color_id, "role": role, "conf": confidence,
                "crow": position.get("row") if position else None,
                "ccol": position.get("col") if position else None,
                "now": now,
                "step": getattr(self, '_current_step', 0),
            }
        )

    async def load_all_roles(self, level: int = 0) -> Dict[int, ObjectRole]:
        """B169: Read all roles from KuzuDB GridEntity nodes for this task/level.
        Returns Dict[color_id, ObjectRole] matching the SolveEngine format."""
        rows = await self.db.execute_read(
            """
            MATCH (e:GridEntity)
            WHERE e.task_id = $tid AND e.level = $level
              AND e.inferred_role IS NOT NULL
              AND e.inferred_role <> 'unknown'
            RETURN e.color_id AS color_id, e.inferred_role AS role,
                   e.role_confidence AS confidence,
                   e.centroid_row AS crow, e.centroid_col AS ccol
            """,
            {"tid": self.task_id, "level": level}
        )
        roles = {}
        for row in rows:
            cid = int(row["color_id"])
            pos = None
            if row.get("crow") is not None and row.get("ccol") is not None:
                pos = {"row": float(row["crow"]), "col": float(row["ccol"])}
            try:
                role_type = RoleType(row["role"])
            except ValueError:
                role_type = RoleType.UNKNOWN
            roles[cid] = ObjectRole(
                color_id=cid,
                role=role_type,
                confidence=row.get("confidence") or 0.0,
                estimated_position=pos,
            )
        return roles

    async def get_action_directions(self, task_id: str, level: int) -> Dict[str, tuple[float, float]]:
        """B178: Query ActionEffect nodes to get observed direction for each action.
        Returns {action_id: (avg_row_delta, avg_col_delta)}."""
        query = """
        MATCH (a:ActionEffect)
        WHERE a.task_id = $task_id AND a.level = $level
        RETURN a.action_id AS action_id, 
               avg(a.direction_row) AS avg_dr, 
               avg(a.direction_col) AS avg_dc
        """
        results = await self.db.execute_read(query, {"task_id": task_id, "level": level})
        return {row["action_id"]: (float(row["avg_dr"] or 0.0), float(row["avg_dc"] or 0.0)) for row in results}

    async def persist_cost_summary(self, summary: dict, outcome: str, steps: int):
        """B180: Persist token cost summary to KuzuDB."""
        summary_id = f"cost_{self.task_id}"
        now = datetime.now().isoformat()
        
        query = """
        MERGE (c:PuzzleCostSummary {summary_id: $id})
        SET c.task_id = $tid,
            c.model = $model,
            c.tokens_in = $tin,
            c.tokens_out = $tout,
            c.cost_usd = $cost,
            c.outcome = $outcome,
            c.steps = $steps,
            c.created_at = timestamp($now)
        """
        await self.db.execute_write(query, {
            "id": summary_id,
            "tid": self.task_id,
            "model": summary["model"],
            "tin": int(summary["tokens_in"]),
            "tout": int(summary["tokens_out"]),
            "cost": float(summary["cost_usd"]),
            "outcome": outcome,
            "steps": int(steps),
            "now": now
        })

    # ══════════════════════════════════════════════════════════════════
    # Structural Edge Builders
    # ══════════════════════════════════════════════════════════════════

    async def _build_structural_edges(self, grid: List[List[int]], level: int):
        """Create ADJACENT_TO, SAME_COLOR_AS, CONTAINS_ENTITY, STRUCTURALLY_SIMILAR edges."""
        entities = list(self._entities.values())
        diff_engine = GridDiffEngine()

        for i in range(len(entities)):
            for j in range(i + 1, len(entities)):
                e1, e2 = entities[i], entities[j]

                # SAME_COLOR_AS
                if e1["color_id"] == e2["color_id"]:
                    await self.db.execute_write(
                        "MATCH (a:GridEntity {entity_id: $id1}), (b:GridEntity {entity_id: $id2}) "
                        "MERGE (a)-[:SAME_COLOR_AS]->(b)",
                        {"id1": e1["entity_id"], "id2": e2["entity_id"]},
                    )

                # ADJACENT_TO
                dist = self._bbox_dist(e1, e2)
                if dist <= 2.0:
                    await self.db.execute_write(
                        "MATCH (a:GridEntity {entity_id: $id1}), (b:GridEntity {entity_id: $id2}) "
                        "MERGE (a)-[:ADJACENT_TO {min_distance: $dist, step: 0}]->(b)",
                        {"id1": e1["entity_id"], "id2": e2["entity_id"], "dist": float(dist)},
                    )

                # CONTAINS_ENTITY
                if self._bbox_contains(e1, e2):
                    await self.db.execute_write(
                        "MATCH (a:GridEntity {entity_id: $id1}), (b:GridEntity {entity_id: $id2}) "
                        "MERGE (a)-[:CONTAINS_ENTITY {step: 0}]->(b)",
                        {"id1": e1["entity_id"], "id2": e2["entity_id"]},
                    )
                elif self._bbox_contains(e2, e1):
                    await self.db.execute_write(
                        "MATCH (a:GridEntity {entity_id: $id1}), (b:GridEntity {entity_id: $id2}) "
                        "MERGE (b)-[:CONTAINS_ENTITY {step: 0}]->(a)",
                        {"id1": e1["entity_id"], "id2": e2["entity_id"]},
                    )

                # STRUCTURALLY_SIMILAR — compare regions of non-background entities
                if (not e1["is_background"] and not e2["is_background"]
                        and e1["pixel_count"] >= 2 and e2["pixel_count"] >= 2):
                    region_a = self._entity_to_pattern_region(e1, grid)
                    region_b = self._entity_to_pattern_region(e2, grid)
                    if region_a and region_b:
                        comparison = diff_engine.compare_regions(region_a, region_b)
                        if comparison.similarity >= 0.5:
                            color_shifted = comparison.color_shift is not None
                            await self.db.execute_write(
                                "MATCH (a:GridEntity {entity_id: $id1}), (b:GridEntity {entity_id: $id2}) "
                                "MERGE (a)-[:STRUCTURALLY_SIMILAR "
                                "{similarity: $sim, color_shifted: $cs, step: 0}]->(b)",
                                {"id1": e1["entity_id"], "id2": e2["entity_id"],
                                 "sim": float(comparison.similarity), "cs": color_shifted},
                            )

    def _entity_to_pattern_region(self, entity: dict, grid: List[List[int]]) -> Optional[PatternRegion]:
        """Convert an entity's cached properties to a PatternRegion for comparison."""
        bbox = (
            entity["bbox_min_row"], entity["bbox_min_col"],
            entity["bbox_max_row"], entity["bbox_max_col"],
        )
        rows = len(grid)
        cols = len(grid[0]) if rows > 0 else 0
        min_r = max(0, bbox[0])
        max_r = min(rows - 1, bbox[2])
        min_c = max(0, bbox[1])
        max_c = min(cols - 1, bbox[3])
        pattern = [row[min_c:max_c + 1] for row in grid[min_r:max_r + 1]]
        if not pattern:
            return None
        palette = {cell for row in pattern for cell in row if cell != 0}
        return PatternRegion(
            bounding_box=bbox,
            pattern=pattern,
            center=(entity["centroid_row"], entity["centroid_col"]),
            color_palette=palette,
            size=entity["pixel_count"],
            location_hint=entity["location_hint"],
        )

    # ══════════════════════════════════════════════════════════════════
    # Helpers
    # ══════════════════════════════════════════════════════════════════

    def _compute_location_hint(self, bbox, rows: int, cols: int) -> str:
        min_r, min_c, max_r, max_c = bbox
        v_pos = "center"
        if min_r == 0:
            v_pos = "top"
        elif max_r == rows - 1:
            v_pos = "bottom"

        h_pos = ""
        if min_c == 0:
            h_pos = "left"
        elif max_c == cols - 1:
            h_pos = "right"

        if v_pos in ("top", "bottom") and h_pos:
            return f"corner_{v_pos[0]}{h_pos[0]}"
        elif v_pos != "center":
            return f"edge_{v_pos}"
        elif h_pos:
            return f"edge_{h_pos}"
        return "center"

    def _bbox_dist(self, e1: dict, e2: dict) -> float:
        dr = max(0, e1["bbox_min_row"] - e2["bbox_max_row"],
                 e2["bbox_min_row"] - e1["bbox_max_row"])
        dc = max(0, e1["bbox_min_col"] - e2["bbox_max_col"],
                 e2["bbox_min_col"] - e1["bbox_max_col"])
        return float((dr ** 2 + dc ** 2) ** 0.5)

    def _bbox_contains(self, parent: dict, child: dict) -> bool:
        return (parent["bbox_min_row"] <= child["bbox_min_row"]
                and parent["bbox_max_row"] >= child["bbox_max_row"]
                and parent["bbox_min_col"] <= child["bbox_min_col"]
                and parent["bbox_max_col"] >= child["bbox_max_col"]
                and parent["pixel_count"] > child["pixel_count"])

    # ══════════════════════════════════════════════════════════════════
    # Cleanup
    # ══════════════════════════════════════════════════════════════════

    async def cleanup(self):
        """Post-puzzle cleanup of ephemeral nodes."""
        await self.db.execute_write(
            "MATCH (e:GridEntity {task_id: $tid}) DETACH DELETE e",
            {"tid": self.task_id},
        )
        await self.db.execute_write(
            "MATCH (s:GridSnapshot {task_id: $tid}) DETACH DELETE s",
            {"tid": self.task_id},
        )
        await self.db.execute_write(
            "MATCH (a:ActionEffect {task_id: $tid}) DETACH DELETE a",
            {"tid": self.task_id},
        )
