"""ARC Solve Engine — goal-directed strategy from game archetype classification.

Five cognitive components:
  ArchetypeClassifier   — What kind of game is this? (centroid-style classification)
  ObjectRoleMapper      — What role does each object play?
  VictoryHypothesizer   — What does winning look like? (inverted pyramid)
  DissonanceDetector    — Is our model wrong? (negative valence encoding)
  PlanChunker           — How do we get there? (BFS + register_plan)
"""

from __future__ import annotations
import logging
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from agents.arc3.prompts import VICTORY_HYPOTHESIS_TEMPLATE, GAME_RULE_HYPOTHESIS_TEMPLATE
from agents.arc3.grid_analysis import grid_characteristic_summary, GridDiffEngine, PatternRegion, RegionComparison

logger = logging.getLogger(__name__)


# ── Enumerations ──────────────────────────────────────────────────────

class GameArchetype(str, Enum):
    RACE     = "race"      # linear path, reach goal first
    SPACE    = "space"     # territorial, open grid positioning
    CHASE    = "chase"     # pursuer/flee dynamic
    DISPLACE = "displace"  # remove targets from board
    UNKNOWN  = "unknown"


class RoleType(str, Enum):
    PLAYER      = "player"
    ENEMY       = "enemy"
    GOAL        = "goal"
    WALL        = "wall"
    COLLECTIBLE = "collectible"
    EXIT        = "exit"
    INTERMEDIATE = "intermediate"  # B167: interaction targets
    DECORATION  = "decoration"
    UNKNOWN     = "unknown"


class VictoryType(str, Enum):
    REACH_GOAL       = "reach_goal"
    COLLECT_ALL      = "collect_all"
    SURVIVE          = "survive"
    SCORE_THRESHOLD  = "score_threshold"
    ELIMINATE        = "eliminate"
    UNKNOWN          = "unknown"


# ── Data Structures ───────────────────────────────────────────────────

@dataclass
class ObjectRole:
    color_id: int
    role: RoleType = RoleType.UNKNOWN
    confidence: float = 0.5
    evidence_steps: List[int] = field(default_factory=list)
    estimated_position: Optional[Dict[str, float]] = None  # {"row": r, "col": c}


@dataclass
class VictoryCondition:
    condition_type: VictoryType = VictoryType.UNKNOWN
    description: str = ""
    target_color_id: Optional[int] = None   # which object to reach/collect/eliminate
    confidence: float = 0.0
    evidence_steps: List[int] = field(default_factory=list)
    source: str = "unknown"                  # "recall_plans" | "llm" | "lesson"


@dataclass
class GameRuleHypothesis:
    rule_description: str
    action_semantics: Dict[str, str]
    objective_description: str
    level_strategy: str
    confidence: float
    evidence: List[str]          # which signals support this
    contradictions: List[str]    # what doesn't fit
    source: str                  # "level_analysis" | "llm" | "memory"


@dataclass
class PlanChunk:
    description: str
    estimated_actions: List[str] = field(default_factory=list)   # e.g. ["ACTION1","ACTION1","ACTION2"]
    progress_score: float = 0.0             # 0.0–1.0, increases as chunk executes
    steps_executed: int = 0
    success_condition: str = ""
    source: str = "bfs"                     # "bfs" | "directional" | "llm"
    graduation_score: float = 0.0
    graduation_reason: str = ""
    graduation_components: Dict[str, float] = field(default_factory=dict)
    plan_id: Optional[str] = None           # SideQuests plan_id for this chunk


@dataclass
class ChunkLedgerEntry:
    """B124: Track chunk lifecycle for prompt visibility and exploration prevention."""
    description: str
    status: str  # "pending" | "active" | "completed" | "failed"
    steps_used: int
    outcome_summary: str


@dataclass
class SolveContext:
    archetype: GameArchetype = GameArchetype.UNKNOWN
    archetype_confidence: float = 0.0
    object_roles: Dict[int, ObjectRole] = field(default_factory=dict)
    victory_condition: Optional[VictoryCondition] = None
    active_chunk: Optional[PlanChunk] = None
    dissonance_detected: bool = False
    dissonance_reason: str = ""
    strategy_summary: str = ""
    chunk_ledger: List["ChunkLedgerEntry"] = field(default_factory=list)
    # B151: Game rule hypotheses from solved levels
    game_rule_hypotheses: List[GameRuleHypothesis] = field(default_factory=list)
    # B144: Plateau-aware exploitation fields
    plateau_mode: bool = False
    plateau_reason: str = ""
    plateau_activation_mode: str = "" # "direct", "sticky", or ""
    plateau_locked_family: Optional[str] = None
    ranked_action_families: List[str] = field(default_factory=list)
    action_family_scores: Dict[str, float] = field(default_factory=dict)


# ── Archetype Classifier ──────────────────────────────────────────────

class ArchetypeClassifier:
    """Classifies game archetype from hypothesis context + analogical_search.

    Algorithm:
      1. Extract signals from hypothesis_context (moving object count, convergence,
         reward pattern, HUD presence).
      2. Score each archetype against signals.
      3. Call analogical_search to find structurally similar past games.
      4. Past game archetype labels vote (weight: 0.4 algorithmic, 0.6 analogy).
      5. Lock when composite confidence > LOCK_THRESHOLD.
    """

    LOCK_THRESHOLD: float = 0.55
    MIN_OBSERVATIONS: int = 2              # gain useful guidance by step 2, not step 5
    CONSISTENCY_BONUS: float = 0.04       # per-step bonus when same archetype wins consecutively

    def __init__(self) -> None:
        self._observation_count: int = 0
        self._signal_history: List[Dict[str, Any]] = []
        self._consecutive_best: GameArchetype = GameArchetype.UNKNOWN
        self._consecutive_count: int = 0

    def fast_track_classify(self, grid_summary: Dict[str, Any]) -> tuple[GameArchetype, float]:
        """Use bootstrap grid-analysis hints to avoid wasting the opening steps."""
        if not grid_summary:
            return GameArchetype.UNKNOWN, 0.0

        n_regions = int(grid_summary.get("n_regions", 0) or 0)
        region_sizes = [int(size) for size in grid_summary.get("region_sizes", [])]
        colors = list(grid_summary.get("colors") or grid_summary.get("distinct_colors") or [])

        if n_regions >= 3 and any(size < 20 for size in region_sizes):
            return GameArchetype.SPACE, 0.4

        if n_regions >= 5 and len(colors) >= 3:
            return GameArchetype.SPACE, 0.3

        if n_regions == 2 and region_sizes:
            largest = max(region_sizes)
            smallest = min(region_sizes)
            if largest >= max(6, smallest * 3):
                return GameArchetype.RACE, 0.35

        return GameArchetype.UNKNOWN, 0.0

    def _extract_signals(self, hypothesis_context: Dict[str, Any]) -> Dict[str, Any]:
        """Pull archetype-relevant signals from HypothesisManager output."""
        action_facts = hypothesis_context.get("action_facts", [])
        # Count how many actions show deterministic single-object movement
        directional_facts = [f for f in action_facts
                             if f.get("fact_type") == "deterministic_effect"]
        # Check for convergence: do any two distinct moving regions approach each other?
        transitions = hypothesis_context.get("last_transition_effect") or {}
        has_hud = bool(hypothesis_context.get("hud_rows"))
        reward_trend = sum(
            1 for f in action_facts if (f.get("value_status") or "") == "valuable"
        )
        path_hypotheses = hypothesis_context.get("path_hypotheses", [])
        return {
            "directional_actions": len(directional_facts),
            "has_hud": has_hud,
            "reward_trend": reward_trend,
            "path_hypotheses_count": len(path_hypotheses),
            "pixels_changed": transitions.get("pixels_changed", 0),
            "loop_detected": bool(hypothesis_context.get("loop_detected")),
        }

    def _score_archetypes(self, signals: Dict[str, Any]) -> Dict[GameArchetype, float]:
        """Heuristic scoring of each archetype from signals."""
        scores: Dict[GameArchetype, float] = {a: 0.0 for a in GameArchetype}
        d = signals["directional_actions"]
        hud = signals["has_hud"]
        reward = signals["reward_trend"]

        # RACE: few directional actions, HUD (energy/score bar), monotonic reward
        if hud and d >= 1 and reward >= 1:
            scores[GameArchetype.RACE] += 0.5
        # CHASE: multiple directional actions, varying reward, no strong single path
        if d >= 2 and signals["path_hypotheses_count"] == 0:
            scores[GameArchetype.CHASE] += 0.4
        # DISPLACE: reward correlates with pixel removal (pixels_changed drops over time)
        if reward >= 2 and signals["pixels_changed"] < 20:
            scores[GameArchetype.DISPLACE] += 0.35
        # SPACE: many path hypotheses, variable reward
        if signals["path_hypotheses_count"] >= 2:
            scores[GameArchetype.SPACE] += 0.45
        return scores

    def update(
        self,
        hypothesis_context: Dict[str, Any],
    ) -> tuple[GameArchetype, float]:
        """Update archetype estimate from latest hypothesis context.

        Returns (archetype, confidence). Does NOT call SideQuests — caller must
        supply analogy_votes from analogical_search results.
        """
        self._observation_count += 1

        bootstrap_summary = (hypothesis_context or {}).get("bootstrap_grid_analysis") or {}
        fast_archetype, fast_confidence = self.fast_track_classify(bootstrap_summary)
        if fast_archetype != GameArchetype.UNKNOWN:
            if fast_archetype == self._consecutive_best:
                self._consecutive_count += 1
            else:
                self._consecutive_best = fast_archetype
                self._consecutive_count = 1

        if self._observation_count < self.MIN_OBSERVATIONS:
            if fast_archetype != GameArchetype.UNKNOWN:
                boosted_fast = min(
                    fast_confidence + self.CONSISTENCY_BONUS * max(self._consecutive_count - 1, 0),
                    0.6,
                )
                return fast_archetype, boosted_fast
            return GameArchetype.UNKNOWN, 0.0

        signals = self._extract_signals(hypothesis_context)
        self._signal_history.append(signals)

        scores = self._score_archetypes(signals)
        best = max(scores, key=lambda a: scores[a])
        best_score = scores[best]
        if best_score < 0.3:
            self._consecutive_best = GameArchetype.UNKNOWN
            self._consecutive_count = 0
            return GameArchetype.UNKNOWN, best_score

        # Temporal consistency boost: repeated same winner builds conviction
        if best == self._consecutive_best:
            self._consecutive_count += 1
        else:
            self._consecutive_best = best
            self._consecutive_count = 1
        boosted = min(best_score + self.CONSISTENCY_BONUS * (self._consecutive_count - 1), 0.95)
        if fast_archetype != GameArchetype.UNKNOWN and fast_archetype == best:
            boosted = max(boosted, fast_confidence)
        return best, boosted

    def apply_analogy_votes(
        self,
        archetype: GameArchetype,
        confidence: float,
        analogy_results: List[Dict[str, Any]],
    ) -> tuple[GameArchetype, float]:
        """Blend analogy votes into the archetype estimate (weight 0.6 analogy)."""
        if not analogy_results:
            return archetype, confidence

        vote_scores: Dict[str, float] = {}
        for result in analogy_results[:5]:
            # Analogical results include text_raw; parse archetype tag if present
            text = (result.get("text_raw") or "").lower()
            for a in GameArchetype:
                if a.value in text and a != GameArchetype.UNKNOWN:
                    vote_scores[a.value] = vote_scores.get(a.value, 0.0) + result.get("similarity", 0.5)

        if not vote_scores:
            return archetype, confidence

        best_vote = max(vote_scores, key=lambda k: vote_scores[k])
        vote_conf = min(vote_scores[best_vote] / len(analogy_results), 0.9)

        # Blend: 0.4 algorithmic + 0.6 analogical
        blended = 0.4 * confidence
        if best_vote == archetype.value:
            blended += 0.6 * vote_conf
        else:
            # Disagreement: cap confidence
            blended = min(blended, 0.5)
            if vote_conf > confidence:
                try:
                    return GameArchetype(best_vote), blended
                except ValueError:
                    return archetype, blended

        return archetype, min(blended, 0.95)


# ── Object Role Mapper ────────────────────────────────────────────────

def _compute_centroids(grid: List[List[int]]) -> Dict[int, Dict[str, float]]:
    """Return per-color centroid and bounds from a grid."""
    from collections import defaultdict

    pixels: Dict[int, List[tuple[int, int]]] = defaultdict(list)
    for r, row in enumerate(grid or []):
        for c, value in enumerate(row or []):
            pixels[value].append((r, c))

    centroids: Dict[int, Dict[str, float]] = {}
    for color_id, points in pixels.items():
        if not points:
            continue
        rows = [point[0] for point in points]
        cols = [point[1] for point in points]
        centroids[color_id] = {
            "row": sum(rows) / len(rows),
            "col": sum(cols) / len(cols),
            "count": float(len(points)),
            "row_start": float(min(rows)),
            "row_end": float(max(rows)),
            "col_start": float(min(cols)),
            "col_end": float(max(cols)),
        }
    return centroids


def _trend_direction_from_fact(action_facts: List[Dict[str, Any]], action_id: Optional[str]) -> Optional[str]:
    if not action_id:
        return None
    fact = next((item for item in action_facts if item.get("action") == action_id), None)
    if not fact:
        return None
    trend = fact.get("trend") or {}
    direction = str(trend.get("direction") or "").lower()
    if direction:
        return direction
    description = str(fact.get("description") or "").lower()
    if "leftward drift" in description or "drift left" in description or "moves left" in description:
        return "left"
    if "rightward drift" in description or "drift right" in description or "moves right" in description:
        return "right"
    if "upward drift" in description or "drift up" in description or "moves up" in description:
        return "up"
    if "downward drift" in description or "drift down" in description or "moves down" in description:
        return "down"
    return None


def _direction_vector(direction: Optional[str]) -> Optional[tuple[float, float]]:
    if direction == "left":
        return (0.0, -1.0)
    if direction == "right":
        return (0.0, 1.0)
    if direction == "up":
        return (-1.0, 0.0)
    if direction == "down":
        return (1.0, 0.0)
    return None


def _value_in_range(value: float, minimum: float, maximum: float) -> bool:
    return minimum <= value <= maximum


class ObjectRoleMapper:
    """Assigns semantic roles to color groups from transitions + invariants.

    Uses evidence fusion:
      - invariant/static row coverage and motion stability for WALL
      - centroid motion that matches inferred operator effects for PLAYER
      - small, stationary, non-background objects for GOAL
      - optional reward/changed-center fallback when the frame lacks richer geometry
    """

    PLAYER_MIN_MATCHES: int = 2
    PLAYER_MIN_MATCH_RATE: float = 0.6
    WALL_MIN_STATIONARY_STEPS: int = 2
    WALL_MIN_OBSERVED_COUNT: int = 2
    WALL_MIN_EXTENT_SPAN: float = 1.0
    GOAL_MAX_COUNT_FRACTION: float = 0.02
    GOAL_MIN_STATIONARY_STEPS: int = 1
    GOAL_MIN_SCORE: float = 0.55
    BACKGROUND_COLOR: int = 0
    EPSILON: float = 0.35

    def __init__(self) -> None:
        self._prev_centroids: Dict[int, Dict[str, float]] = {}
        self._movement_evidence: Dict[int, List[Dict[str, Any]]] = {}
        self._local_activity_evidence: Dict[int, List[Dict[str, Any]]] = {}
        self._stationary_steps: Dict[int, int] = {}
        self._centroid_history: Dict[int, List[Dict[str, float]]] = {}

    def _color_only_in_rows(
        self,
        grid: List[List[int]],
        color_id: int,
        allowed_rows: set[int],
    ) -> bool:
        if not grid or not allowed_rows:
            return False
        seen = False
        for row_idx, row in enumerate(grid):
            for value in row:
                if value != color_id:
                    continue
                seen = True
                if row_idx not in allowed_rows:
                    return False
        return seen

    def _directional_match(
        self,
        delta_row: float,
        delta_col: float,
        direction: Optional[str],
    ) -> bool:
        vec = _direction_vector(direction)
        if vec is None:
            return False
        dr, dc = vec
        if dr != 0.0 and delta_row * dr <= self.EPSILON:
            return False
        if dc != 0.0 and delta_col * dc <= self.EPSILON:
            return False
        return abs(delta_row) > self.EPSILON or abs(delta_col) > self.EPSILON

    def _near_changed_center(
        self,
        centroid: Dict[str, float],
        changed_center: Dict[str, Any] | None,
    ) -> bool:
        if not centroid or not changed_center:
            return False
        row = float(changed_center.get("row", centroid.get("row", 0.0)))
        col = float(changed_center.get("col", centroid.get("col", 0.0)))
        return abs(centroid["row"] - row) <= 1.5 and abs(centroid["col"] - col) <= 1.5

    def _changed_bbox_center(
        self,
        changed_region: Dict[str, Any],
    ) -> Dict[str, float] | None:
        row_range = changed_region.get("row_range")
        col_range = changed_region.get("col_range")
        if not row_range or not col_range:
            return None
        return {
            "row": float(row_range[0] + row_range[1]) / 2.0,
            "col": float(col_range[0] + col_range[1]) / 2.0,
        }

    def _in_changed_bbox(
        self,
        centroid: Dict[str, float],
        changed_region: Dict[str, Any],
        padding: float = 1.5,
    ) -> bool:
        row_range = changed_region.get("row_range")
        col_range = changed_region.get("col_range")
        if not row_range or not col_range:
            return False
        return (
            _value_in_range(float(centroid["row"]), float(row_range[0]) - padding, float(row_range[1]) + padding)
            and _value_in_range(float(centroid["col"]), float(col_range[0]) - padding, float(col_range[1]) + padding)
        )

    def _has_active_evidence(self, color_id: int) -> bool:
        """Return True when a color has participated in a real transition signal."""
        for evidence in self._movement_evidence.get(color_id, []):
            if (
                evidence.get("moved")
                or evidence.get("in_changed_bbox")
                or evidence.get("near_changed_center")
                or evidence.get("matches_direction")
            ):
                return True
        for evidence in self._local_activity_evidence.get(color_id, []):
            if (
                evidence.get("moved")
                or evidence.get("in_changed_bbox")
                or evidence.get("near_changed_center")
                or evidence.get("matches_direction")
            ):
                return True
        return False

    def _has_wall_geometry(self, centroid: Dict[str, float]) -> bool:
        row_span = float(centroid.get("row_end", centroid["row"])) - float(centroid.get("row_start", centroid["row"]))
        col_span = float(centroid.get("col_end", centroid["col"])) - float(centroid.get("col_start", centroid["col"]))
        return row_span >= self.WALL_MIN_EXTENT_SPAN or col_span >= self.WALL_MIN_EXTENT_SPAN

    def _goal_candidate_score(
        self,
        color_id: int,
        centroid: Dict[str, float],
        stationary_steps: int,
        changed_region: Dict[str, Any],
        changed_center: Dict[str, Any],
        player_pos: Dict[str, float] | None,
        prev_player_pos: Dict[str, float] | None,
        total_pixels: int,
        history_len: int,
        pixels_changed: float,
    ) -> float:
        count_fraction = float(centroid.get("count", 0.0)) / float(total_pixels)
        in_changed_bbox = self._in_changed_bbox(centroid, changed_region)
        near_changed_center = self._near_changed_center(centroid, changed_center)

        score = 0.0
        if count_fraction <= 0.02:
            score += 0.30
        elif count_fraction <= 0.05:
            score += 0.22
        elif count_fraction <= 0.08:
            score += 0.12

        if stationary_steps >= 2:
            score += 0.25
        elif stationary_steps >= 1:
            score += 0.15

        if in_changed_bbox:
            score += 0.20
        if near_changed_center:
            score += 0.15
        if pixels_changed > 0.0 and stationary_steps >= 1:
            score += 0.10

        if player_pos:
            distance = abs(float(centroid["row"]) - float(player_pos.get("row", 0.0))) + abs(
                float(centroid["col"]) - float(player_pos.get("col", 0.0))
            )
            if distance <= 3.0:
                score += 0.12
            elif distance <= 6.0:
                score += 0.08
            elif distance <= 10.0:
                score += 0.04
            if prev_player_pos:
                prev_distance = abs(float(centroid["row"]) - float(prev_player_pos.get("row", 0.0))) + abs(
                    float(centroid["col"]) - float(prev_player_pos.get("col", 0.0))
                )
                delta = prev_distance - distance
                if delta >= 1.0:
                    score += 0.15
                elif delta >= 0.5:
                    score += 0.08

        if history_len >= 2:
            score += 0.05
        if history_len >= 3 and stationary_steps >= 2:
            score += 0.05
        return score

    def update(
        self,
        hypothesis_context: Dict[str, Any],
        observation: Dict[str, Any],
        step: int,
    ) -> Dict[int, ObjectRole]:
        """Return updated object role map from current frame evidence."""
        roles: Dict[int, ObjectRole] = {}
        colors = observation.get("colors") or []
        grid = observation.get("grid") or []
        static_rows = set(hypothesis_context.get("static_rows") or [])
        hud_rows = set(hypothesis_context.get("hud_rows") or [])
        last_effect = hypothesis_context.get("last_transition_effect") or {}
        changed_region = last_effect.get("changed_region") or {}
        changed_center = last_effect.get("changed_center") or self._changed_bbox_center(changed_region) or {}
        reward = float(last_effect.get("meaningful_change_score", 0.0))
        pixels_changed = float(last_effect.get("pixels_changed", 0.0))
        action_taken = last_effect.get("action")
        action_facts = hypothesis_context.get("action_facts") or []
        inferred_direction = _trend_direction_from_fact(action_facts, action_taken)
        direction_vector = _direction_vector(inferred_direction)

        curr_centroids = _compute_centroids(grid) if grid else {}
        total_pixels = sum(int(v.get("count", 0)) for v in curr_centroids.values()) or 1

        for color_info in colors:
            color_id = color_info["value"] if isinstance(color_info, dict) else color_info
            roles[color_id] = ObjectRole(color_id=color_id, evidence_steps=[step])

        # Fallback for sparse observations without a grid payload.
        if not curr_centroids:
            for color_id, role in roles.items():
                if static_rows and not changed_region:
                    role.role = RoleType.WALL
                    role.confidence = 0.7
                elif changed_center and reward > 0.3:
                    role.role = RoleType.PLAYER
                    role.confidence = 0.75
                    role.estimated_position = changed_center
            return roles

        for color_id, centroid in curr_centroids.items():
            prev = self._prev_centroids.get(color_id)
            if prev is not None:
                delta_row = centroid["row"] - prev["row"]
                delta_col = centroid["col"] - prev["col"]
                count_delta = int(centroid["count"] - prev.get("count", 0.0))
                moved = abs(delta_row) > self.EPSILON or abs(delta_col) > self.EPSILON
                if moved:
                    self._stationary_steps[color_id] = 0
                else:
                    self._stationary_steps[color_id] = self._stationary_steps.get(color_id, 0) + 1
                in_changed_bbox = self._in_changed_bbox(centroid, changed_region)
                near_center = self._near_changed_center(centroid, changed_center)
                evidence = {
                    "step": step,
                    "action": action_taken,
                    "delta_row": round(delta_row, 2),
                    "delta_col": round(delta_col, 2),
                    "count_delta": count_delta,
                    "moved": moved,
                    "in_changed_region": near_center,
                    "in_changed_bbox": in_changed_bbox,
                    "direction": inferred_direction,
                    "matches_direction": False,
                }
                if direction_vector is not None:
                    evidence["matches_direction"] = self._directional_match(
                        delta_row,
                        delta_col,
                        inferred_direction,
                    )
                self._movement_evidence.setdefault(color_id, []).append(evidence)
                local_activity = {
                    "step": step,
                    "action": action_taken,
                    "count_delta": count_delta,
                    "in_changed_bbox": in_changed_bbox,
                    "near_changed_center": near_center,
                    "matches_direction": evidence["matches_direction"],
                    "moved": moved,
                }
                self._local_activity_evidence.setdefault(color_id, []).append(local_activity)
            else:
                self._stationary_steps.setdefault(color_id, 0)
            self._centroid_history.setdefault(color_id, []).append(centroid)

        # B167: Intermediate detection
        # Small stationary objects that aren't background/wall/goal/player
        # and are potentially interactive.
        for color_id, role in roles.items():
            if role.role != RoleType.UNKNOWN:
                continue
            centroid = curr_centroids.get(color_id)
            if centroid is None or color_id == self.BACKGROUND_COLOR:
                continue
            
            count = int(centroid.get("count", 0.0))
            if 2 <= count <= 20:
                # Potential intermediate candidate
                stationary_steps = self._stationary_steps.get(color_id, 0)
                if stationary_steps >= 1:
                    role.role = RoleType.INTERMEDIATE
                    role.confidence = 0.45
                    role.estimated_position = {"row": centroid["row"], "col": centroid["col"]}

        # WALL detection: require multiple independent signals on real grids.
        for color_id, role in roles.items():
            centroid = curr_centroids.get(color_id)
            if centroid is None or color_id == self.BACKGROUND_COLOR:
                continue
            coverage_static = self._color_only_in_rows(grid, color_id, static_rows)
            coverage_hud = self._color_only_in_rows(grid, color_id, hud_rows)
            stationary_steps = self._stationary_steps.get(color_id, 0)
            history = self._centroid_history.get(color_id, [])
            count = int(centroid.get("count", 0.0))
            active_evidence = self._has_active_evidence(color_id)
            structural_signal = count >= self.WALL_MIN_OBSERVED_COUNT and self._has_wall_geometry(centroid)
            coverage_signal = coverage_static or coverage_hud
            persistence_signal = stationary_steps >= self.WALL_MIN_STATIONARY_STEPS
            drift = 0.0
            if len(history) >= 2:
                drift = sum(
                    abs(curr["row"] - prev["row"]) + abs(curr["col"] - prev["col"])
                    for prev, curr in zip(history[:-1], history[1:])
                )

            # On a real grid, wall labels need coverage + persistence + shape evidence.
            if grid:
                if active_evidence:
                    continue
                if coverage_signal and persistence_signal and structural_signal and drift <= 0.5:
                    role.role = RoleType.WALL
                    role.confidence = 0.72 if coverage_static else 0.68
                    continue
                if coverage_signal and structural_signal and stationary_steps >= (self.WALL_MIN_STATIONARY_STEPS + 1) and drift <= 0.35:
                    role.role = RoleType.WALL
                    role.confidence = 0.7 if coverage_static else 0.66
                    continue
                continue

            if coverage_static or (coverage_hud and stationary_steps >= 1):
                role.role = RoleType.WALL
                role.confidence = 0.7 if coverage_static else 0.65
                continue
            if stationary_steps >= self.WALL_MIN_STATIONARY_STEPS and drift <= 0.5:
                role.role = RoleType.WALL
                role.confidence = 0.68

        # PLAYER detection: consistent motion evidence matching inferred operator trend.
        best_player_id: Optional[int] = None
        best_player_score = 0.0
        for color_id, evidence in self._movement_evidence.items():
            if color_id not in roles:
                continue
            if roles[color_id].role == RoleType.WALL or color_id == self.BACKGROUND_COLOR:
                continue
            moved_events = [item for item in evidence if item.get("moved")]
            if not moved_events:
                continue
            match_rate = sum(1 for item in moved_events if item.get("matches_direction")) / len(moved_events)
            motion_rate = len(moved_events) / len(evidence)
            changed_region_rate = sum(1 for item in evidence if item.get("in_changed_region")) / len(evidence)
            reward_bonus = 0.15 if reward > 0.3 else 0.0
            score = (0.45 * match_rate) + (0.30 * motion_rate) + (0.15 * changed_region_rate) + reward_bonus
            if len(moved_events) >= self.PLAYER_MIN_MATCHES and score >= self.PLAYER_MIN_MATCH_RATE:
                if score > best_player_score:
                    best_player_score = score
                    best_player_id = color_id

        # Local changed-region fallback: prefer a small active color when the transition
        # only moves a tiny frontier and whole-color centroids stay too stable to score well.
        for color_id, evidence in self._local_activity_evidence.items():
            if color_id not in roles:
                continue
            if roles[color_id].role == RoleType.WALL or color_id == self.BACKGROUND_COLOR:
                continue
            centroid = curr_centroids.get(color_id)
            if centroid is None:
                continue
            count_fraction = float(centroid.get("count", 0.0)) / float(total_pixels)
            if count_fraction > 0.08:
                continue
            bbox_hits = sum(1 for item in evidence if item.get("in_changed_bbox"))
            center_hits = sum(1 for item in evidence if item.get("near_changed_center"))
            count_changes = sum(1 for item in evidence if item.get("count_delta", 0) != 0)
            directional_hits = sum(1 for item in evidence if item.get("matches_direction"))
            moved_hits = sum(1 for item in evidence if item.get("moved"))
            if bbox_hits == 0 and center_hits == 0:
                continue
            activity_score = (
                0.30 * min(bbox_hits / max(len(evidence), 1), 1.0)
                + 0.25 * min(center_hits / max(len(evidence), 1), 1.0)
                + 0.20 * min(count_changes / max(len(evidence), 1), 1.0)
                + 0.15 * min(moved_hits / max(len(evidence), 1), 1.0)
                + 0.10 * min(directional_hits / max(len(evidence), 1), 1.0)
            )
            if count_fraction <= 0.02:
                activity_score += 0.10
            if bbox_hits >= 2 and (moved_hits >= 2 or count_changes >= 1) and activity_score > best_player_score:
                best_player_score = activity_score
                best_player_id = color_id

        if best_player_id is None and changed_center and reward > 0.3:
            for color_id, centroid in curr_centroids.items():
                if color_id not in roles or color_id == self.BACKGROUND_COLOR:
                    continue
                if roles[color_id].role == RoleType.WALL:
                    continue
                if self._near_changed_center(centroid, changed_center):
                    best_player_id = color_id
                    best_player_score = 0.75
                    break

        if best_player_id is None:
            # Generic movement fallback: if a single color is the most mobile and not wall-like,
            # treat it as the likely controlled object.
            mobility_scores: List[tuple[float, int]] = []
            for color_id, evidence in self._movement_evidence.items():
                if color_id not in roles or roles[color_id].role == RoleType.WALL:
                    continue
                moved_events = sum(1 for item in evidence if item.get("moved"))
                if moved_events >= 2:
                    mobility_scores.append((moved_events / len(evidence), color_id))
            if mobility_scores:
                mobility_scores.sort(reverse=True)
                top_score, top_color = mobility_scores[0]
                if top_score >= 0.6:
                    best_player_id = top_color
                    best_player_score = top_score

        if best_player_id is not None and best_player_id in roles:
            role = roles[best_player_id]
            role.role = RoleType.PLAYER
            role.confidence = min(0.6 + best_player_score * 0.3, 0.95)
            centroid = curr_centroids.get(best_player_id)
            if centroid:
                role.estimated_position = {"row": centroid["row"], "col": centroid["col"]}

        player_role = next((r for r in roles.values() if r.role == RoleType.PLAYER), None)
        player_pos = player_role.estimated_position if player_role else None
        prev_player_pos: Dict[str, float] | None = None
        if best_player_id is not None:
            prev_player_centroid = self._prev_centroids.get(best_player_id)
            if prev_player_centroid:
                prev_player_pos = {
                    "row": prev_player_centroid["row"],
                    "col": prev_player_centroid["col"],
                }

        # GOAL detection: small, persistent, non-background, and shaped by transition evidence.
        best_goal_id: Optional[int] = None
        best_goal_score = 0.0
        for color_id, role in roles.items():
            if role.role != RoleType.UNKNOWN:
                continue
            centroid = curr_centroids.get(color_id)
            if centroid is None or color_id == self.BACKGROUND_COLOR:
                continue
            stationary_steps = self._stationary_steps.get(color_id, 0)
            in_hud = self._color_only_in_rows(grid, color_id, hud_rows)
            in_static = self._color_only_in_rows(grid, color_id, static_rows)
            if in_hud or in_static:
                continue
            score = self._goal_candidate_score(
                color_id=color_id,
                centroid=centroid,
                stationary_steps=stationary_steps,
                changed_region=changed_region,
                changed_center=changed_center,
                player_pos=player_pos,
                prev_player_pos=prev_player_pos,
                total_pixels=total_pixels,
                history_len=len(self._centroid_history.get(color_id, [])),
                pixels_changed=pixels_changed,
            )
            if score >= self.GOAL_MIN_SCORE and score > best_goal_score:
                best_goal_score = score
                best_goal_id = color_id

        if best_goal_id is not None and best_goal_id in roles:
            goal_role = roles[best_goal_id]
            goal_role.role = RoleType.GOAL
            goal_role.confidence = min(0.5 + best_goal_score * 0.4, 0.95)
            centroid = curr_centroids.get(best_goal_id)
            if centroid:
                goal_role.estimated_position = {"row": centroid["row"], "col": centroid["col"]}

        # Enrich evidence trails.
        for color_id, role in roles.items():
            if role.role == RoleType.UNKNOWN:
                continue
            role.evidence_steps = sorted(set(role.evidence_steps + [step]))

        self._prev_centroids = curr_centroids
        return roles

    def seed_bootstrap_roles(self, observation: Dict[str, Any]) -> Dict[int, ObjectRole]:
        """B119: Initial entity discovery from step 0 frame.
        PLAYER: smallest moving color (heuristic: smallest non-zero color).
        GOAL: larger contrasting color candidate.
        Include centroid positions when a grid is present so early geometry-based
        policies can still operate before strong motion evidence arrives.
        """
        roles: Dict[int, ObjectRole] = {}
        colors = observation.get("colors") or []
        if not colors:
            return roles

        non_bg = [c for c in colors if (c.get("value") if isinstance(c, dict) else c) != 0]
        if not non_bg:
            return roles

        centroids = _compute_centroids(observation.get("grid") or []) if observation.get("grid") else {}

        def _bootstrap_position(color_id: int) -> Optional[Dict[str, float]]:
            centroid = centroids.get(color_id)
            if not centroid:
                return None
            return {"row": float(centroid["row"]), "col": float(centroid["col"])}

        sorted_by_size = sorted(non_bg, key=lambda c: c.get("count") if isinstance(c, dict) else 0)

        player_color_item = sorted_by_size[0]
        p_id = player_color_item.get("value") if isinstance(player_color_item, dict) else player_color_item
        roles[p_id] = ObjectRole(
            color_id=p_id,
            role=RoleType.PLAYER,
            confidence=0.45,
            evidence_steps=[0],
            estimated_position=_bootstrap_position(p_id),
        )

        if len(sorted_by_size) > 1:
            goal_color_item = sorted_by_size[-1]
            g_id = goal_color_item.get("value") if isinstance(goal_color_item, dict) else goal_color_item
            if g_id != p_id:
                roles[g_id] = ObjectRole(
                    color_id=g_id,
                    role=RoleType.GOAL,
                    confidence=0.35,
                    evidence_steps=[0],
                    estimated_position=_bootstrap_position(g_id),
                )

        return roles


# ── Pattern Match Tracker ─────────────────────────────────────────────

class PatternMatchTracker:
    """B167: Tracks whether the goal region is converging toward the reference pattern."""

    def __init__(self):
        self._engine = GridDiffEngine()
        self.reference_region: Optional[PatternRegion] = None
        self.goal_region: Optional[PatternRegion] = None
        self.similarity_history: List[float] = []
        self.phase: str = "discover"  # "discover" → "intermediate" → "finish"

    def update(self, grid: List[List[int]], step: int) -> dict:
        """Called each step. Returns phase and similarity info."""
        if not grid:
            return {"phase": self.phase, "similarity": 0.0}

        # 1. If reference/goal not yet identified, try to find them
        if self.reference_region is None or self.goal_region is None:
            regions = self._engine.extract_pattern_regions(grid)
            pair = self._engine.find_reference_goal_pair(regions, len(grid), len(grid[0]))
            if pair:
                self.reference_region, self.goal_region = pair
                logger.info(
                    "[B167] Found reference/goal pair: ref=%s, goal=%s",
                    self.reference_region.location_hint,
                    self.goal_region.location_hint,
                )

        # 2. If both known, compare current goal state to reference
        if self.reference_region and self.goal_region:
            # Re-extract goal region pattern from current grid (it may have changed)
            bb = self.goal_region.bounding_box
            current_goal_pattern = self._engine.crop_region(grid, bb)
            
            # Use current goal pattern to build a temporary PatternRegion for comparison
            current_region = PatternRegion(
                bounding_box=bb,
                pattern=current_goal_pattern,
                center=self.goal_region.center,
                color_palette=self.goal_region.color_palette,
                size=self.goal_region.size,
                location_hint=self.goal_region.location_hint
            )
            
            comparison = self._engine.compare_regions(current_region, self.reference_region)
            self.similarity_history.append(comparison.similarity)

            # Phase logic
            if comparison.similarity >= 0.9:
                self.phase = "finish"  # Goal matches reference — go touch it
            elif len(self.similarity_history) > 1 and comparison.similarity > self.similarity_history[0]:
                self.phase = "intermediate"  # Making progress
            else:
                if self.phase == "finish":
                    self.phase = "intermediate"
                elif self.phase == "discover":
                    self.phase = "intermediate"

            return {
                "phase": self.phase,
                "similarity": comparison.similarity,
                "similarity_trend": self._trend(),
                "reference_location": self.reference_region.location_hint,
                "goal_location": self.goal_region.location_hint,
                "exact_match": comparison.exact_match,
                "description": comparison.description,
            }

        return {"phase": "discover", "similarity": 0.0}

    def _trend(self) -> str:
        if len(self.similarity_history) < 2:
            return "stable"
        if self.similarity_history[-1] > self.similarity_history[-2]:
            return "improving"
        elif self.similarity_history[-1] < self.similarity_history[-2]:
            return "regressing"
        return "stable"


# ── Victory Hypothesizer ──────────────────────────────────────────────

class VictoryHypothesizer:
    """Identifies the win condition using recall_plans + recall_lessons + one LLM call.

    Called once when archetype confidence > CALL_THRESHOLD.
    Re-called only when DissonanceDetector fires.
    """

    # B179: Lowered threshold from 0.65 to 0.45 to match spatial navigation range
    CALL_THRESHOLD: float = 0.45
    PROMPT_TEMPLATE = VICTORY_HYPOTHESIS_TEMPLATE  # B122: Imported from prompts module

    async def hypothesize(
        self,
        archetype: GameArchetype,
        object_roles: Dict[int, ObjectRole],
        brain_client: Any,
        llm_client: Any,
        session_id: str,
        task_id: str,
        reward_history: List[float],
        dissonance_reason: str = "",
        past_plans: Optional[List[Dict[str, Any]]] = None,
        lessons: Optional[List[Dict[str, Any]]] = None,
    ) -> VictoryCondition:
        """Synthesize the victory condition from retrieved evidence and an LLM call.

        The solve engine is responsible for fetching `past_plans` and `lessons`.
        If they are omitted, we fall back to direct retrieval for compatibility.
        """

        # 1. Recall similar past plans if the caller did not already fetch them.
        if past_plans is None:
            goal_query = f"{archetype.value} game win condition solve puzzle"
            recall = await brain_client.recall_plans(
                goal_query=goal_query,
                session_id=session_id,
                min_valence=0.2,
                limit=3,
            )
            past_plans = recall.get("plans", [])

        # Check if a past plan gives us a high-confidence victory condition directly
        for plan in past_plans:
            if plan.get("valence", 0.0) > 0.75:
                return VictoryCondition(
                    condition_type=VictoryType.UNKNOWN,  # will be refined by LLM
                    description=plan.get("goal", ""),
                    confidence=0.6,
                    source="recall_plans",
                )

        # 2. Recall game-specific lessons if the caller did not already fetch them.
        if lessons is None:
            lessons_result = await brain_client.recall_relevant_lessons(
                query=f"ARC game {archetype.value} win condition",
                limit=3,
            )
            lessons = lessons_result.get("lessons", [])

        # 3. LLM call
        object_roles_text = "\n".join(
            f"  color_id={r.color_id}: {r.role.value} (confidence={r.confidence:.2f})"
            for r in object_roles.values()
            if r.role != RoleType.UNKNOWN
        ) or "  No roles identified yet."

        past_plans_text = "\n".join(
            f"  - {p.get('goal', '')}" for p in past_plans[:3]
        ) or "  No past plans found."

        lessons_text = "\n".join(
            f"  - {l.get('text', '')}" for l in lessons[:3]
        ) or "  No lessons found."

        reward_summary = (
            f"Last 5 rewards: {reward_history[-5:]}"
            if reward_history else "No reward history."
        )
        if dissonance_reason:
            reward_summary += f" DISSONANCE: {dissonance_reason}"

        prompt = self.PROMPT_TEMPLATE.format(
            archetype=archetype.value,
            object_roles=object_roles_text,
            past_plans=past_plans_text,
            lessons=lessons_text,
            reward_summary=reward_summary,
        )

        try:
            response = await llm_client.achat([{"role": "user", "content": prompt}])
            text = response.strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            data = json.loads(text.strip())
            return VictoryCondition(
                condition_type=VictoryType(data.get("condition_type", "unknown")),
                description=data.get("description", ""),
                target_color_id=data.get("target_color_id"),
                confidence=float(data.get("confidence", 0.5)),
                source="llm",
            )
        except Exception as exc:
            logger.warning("VictoryHypothesizer LLM call failed: %s", exc)
            return VictoryCondition(
                condition_type=VictoryType.UNKNOWN,
                description="Victory condition unknown",
                confidence=0.1,
                source="error",
            )


# ── Game Rule Hypothesizer ──────────────────────────────────────────

class GameRuleHypothesizer:
    """Generates game rule hypotheses from solved level data."""

    async def hypothesize(
        self,
        level_pattern: "LevelPattern",
        solved_levels: List[Dict],
        llm_client: Any,
        memory_hypotheses: Optional[List[GameRuleHypothesis]] = None,
    ) -> List[GameRuleHypothesis]:
        """Generate ranked game rule hypotheses from solved levels.

        1. Check if deterministic analysis alone gives high-confidence answer
        2. If not, use LLM with structured evidence from solved levels
        3. Merge with memory-retrieved hypotheses (B155)
        """
        hypotheses = []

        # Fast path: if action effects are very consistent, skip LLM
        if level_pattern.confidence > 0.9:
            hypotheses.append(self._hypothesis_from_pattern(level_pattern))

        # LLM path: interpret the evidence
        if not hypotheses:
            level_summaries = self._format_solved_levels(solved_levels)
            action_effects = self._format_action_effects(level_pattern)
            
            from agents.arc3.prompts import GAME_RULE_HYPOTHESIS_TEMPLATE
            prompt = GAME_RULE_HYPOTHESIS_TEMPLATE.format(
                total_levels=solved_levels[-1].get("win_levels", "?") if solved_levels else "?",
                n_solved=len(solved_levels),
                level_summaries=level_summaries,
                action_effects=action_effects,
                cross_level_pattern=level_pattern.game_rule_summary,
            )

            try:
                # B132: Mental sandbox uses chat() with response_format
                resp = await llm_client.chat(
                    messages=[
                        {"role": "system", "content": "You are a logic engine for discovering game rules."},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"}
                )
                
                content = resp.content if hasattr(resp, 'content') else str(resp)
                data = json.loads(content)
                
                # Allow for single object or list
                hyp_list = data.get("hypotheses", [data]) if isinstance(data, dict) else data
                if not isinstance(hyp_list, list):
                    hyp_list = [data]
                
                for h in hyp_list:
                    hypotheses.append(GameRuleHypothesis(
                        rule_description=h.get("rule_description", ""),
                        action_semantics=h.get("action_semantics", {}),
                        objective_description=h.get("objective_description", ""),
                        level_strategy=h.get("level_strategy", ""),
                        confidence=float(h.get("confidence", 0.5)),
                        evidence=[f"Solved {len(solved_levels)} levels"],
                        contradictions=[],
                        source="llm"
                    ))
            except Exception as exc:
                logger.warning("B151: GameRuleHypothesizer LLM failed: %s", exc)

        # Merge memory hypotheses (from B155)
        if memory_hypotheses:
            hypotheses.extend(memory_hypotheses)

        hypotheses.sort(key=lambda h: h.confidence, reverse=True)
        return hypotheses[:3]

    def _hypothesis_from_pattern(self, pattern: "LevelPattern") -> GameRuleHypothesis:
        """Convert a high-confidence LevelPattern to a hypothesis."""
        return GameRuleHypothesis(
            rule_description=pattern.game_rule_summary,
            action_semantics=pattern.consistent_action_effects,
            objective_description="Match the pattern suggested by level progression",
            level_strategy="Apply known action effects to reach the goal state",
            confidence=pattern.confidence,
            evidence=[f"Cross-level analysis: {pattern.game_rule_summary}"],
            contradictions=[],
            source="level_analysis",
        )

    def _format_solved_levels(self, solved_levels: List[Dict]) -> str:
        """Format solved level data for the LLM prompt."""
        lines = []
        for level in solved_levels:
            n_actions = len(level.get("actions", []))
            action_seq = " → ".join(level.get("actions", [])[:10])
            if n_actions > 10:
                action_seq += f" ... ({n_actions} total)"
            lines.append(
                f"Level {level.get('level', '?')}: "
                f"{n_actions} actions to solve. "
                f"Sequence: {action_seq}"
            )
        return "\n".join(lines)

    def _format_action_effects(self, pattern: "LevelPattern") -> str:
        """Format observed action effects."""
        if not pattern.consistent_action_effects:
            return "No consistent action effects observed yet."
        lines = []
        for action_id, effect in pattern.consistent_action_effects.items():
            lines.append(f"  {action_id}: {effect}")
        return "\n".join(lines)


# ── Dissonance Detector ───────────────────────────────────────────────

class DissonanceDetector:
    """Monitors plan chunk progress. Fires report_outcome(negative) on stall.

    Dissonance conditions:
      - Zero meaningful-change steps >= STALL_THRESHOLD while executing a chunk
      - reward_trend is flat/negative for >= REWARD_STALL_THRESHOLD steps
      - Active chunk exceeded MAX_CHUNK_STEPS without progress_score increase
    """

    STALL_THRESHOLD: int = 2
    REWARD_STALL_THRESHOLD: int = 3
    MAX_CHUNK_STEPS: int = 4

    def __init__(self) -> None:
        self._zero_progress_streak: int = 0
        self._chunk_steps: int = 0

    def update(
        self,
        hypothesis_context: Dict[str, Any],
        active_chunk: Optional[PlanChunk],
        step: int,
    ) -> tuple[bool, str]:
        """Return (should_replan, reason). Pure algorithmic — no async."""
        if active_chunk is None:
            self._zero_progress_streak = 0
            self._chunk_steps = 0
            return False, ""

        self._chunk_steps += 1
        last_effect = hypothesis_context.get("last_transition_effect") or {}
        score = float(last_effect.get("meaningful_change_score", 0.0))

        if score < 0.15:
            self._zero_progress_streak += 1
        else:
            self._zero_progress_streak = 0
            self._chunk_steps = 0  # B109: meaningful change resets chunk step counter

        if self._zero_progress_streak >= self.STALL_THRESHOLD:
            return True, f"no meaningful change for {self._zero_progress_streak} steps"

        if self._chunk_steps >= self.MAX_CHUNK_STEPS and active_chunk.progress_score < 0.2:
            return True, f"chunk exceeded {self.MAX_CHUNK_STEPS} steps with low progress"

        # B109: If chunk is exhausted but didn't reach success, trigger replan
        if not active_chunk.estimated_actions and active_chunk.progress_score < 0.5:
            return True, "chunk actions exhausted without significant progress"

        return False, ""

    def reset_chunk(self) -> None:
        """Call when a new chunk starts."""
        self._zero_progress_streak = 0
        self._chunk_steps = 0


# ── Plan Chunker ──────────────────────────────────────────────────────

class PlanChunker:
    """Decomposes the victory condition into executable macro-action chunks.

    Primary path: BFS on in-memory StateGraph (free, exact, O(V+E)).
    Fallback path: directional chunk toward estimated goal position.
    SolveEngine owns plan registration; this class only returns the next chunk.
    """

    DIRECTIONAL_PLAYER_CONFIDENCE: float = 0.65
    DIRECTIONAL_GOAL_CONFIDENCE: float = 0.55
    MIN_TESTED_ACTIONS_FOR_DIRECTIONAL: int = 3
    GRADUATION_THRESHOLD: float = 0.72
    MIN_EXPLORATION_COMPLETENESS: float = 0.60

    def _graduation_assessment(
        self,
        player_role: Optional[ObjectRole],
        goal_role: Optional[ObjectRole],
        hypothesis_context: Optional[Dict[str, Any]],
        available_actions: List[str],
        chunk_progress: float = 0.0,
        steps_using_chunk: int = 0,
        consecutive_zero_reward_steps: int = 0,
    ) -> Dict[str, Any]:
        context = hypothesis_context or {}
        chunk_progress = max(chunk_progress, float(context.get("chunk_progress", 0.0) or 0.0))
        steps_using_chunk = max(steps_using_chunk, int(context.get("steps_using_chunk", 0) or 0))
        consecutive_zero_reward_steps = max(
            consecutive_zero_reward_steps,
            int(context.get("consecutive_zero_reward_steps", 0) or 0),
        )
        action_coverage = context.get("action_coverage") or {}
        available_total = max(len(available_actions), 1)
        tested_count = int(action_coverage.get("tested_count", 0))
        untested_count = int(action_coverage.get("untested_count", max(available_total - tested_count, 0)))
        coverage_ratio = min(tested_count / available_total, 1.0)

        player_conf = float(player_role.confidence if player_role else 0.0)
        goal_conf = float(goal_role.confidence if goal_role else 0.0)
        player_known = 1.0 if player_role and player_role.estimated_position else 0.0
        goal_known = 1.0 if goal_role and goal_role.estimated_position else 0.0
        positions_known = 1.0 if player_known and goal_known else 0.0

        action_facts = context.get("action_facts") or []
        path_hypotheses = context.get("path_hypotheses") or []

        deterministic_facts = sum(1 for fact in action_facts if fact.get("fact_type") == "deterministic_effect")
        valuable_facts = sum(
            1
            for fact in action_facts
            if fact.get("fact_type") == "deterministic_effect" and fact.get("value_status") == "valuable"
        )
        path_signal = sum(
            1
            for hyp in path_hypotheses
            if hyp.get("value_status") in {"valuable", "tentative"}
        )
        evidence_score = min(
            0.20 * min(deterministic_facts, 3)
            + 0.12 * min(valuable_facts, 3)
            + 0.10 * min(path_signal, 3)
            + (0.10 if action_coverage.get("initial_exploration_complete") else 0.0),
            1.0,
        )

        contradiction_penalty = 0.0
        if context.get("loop_detected"):
            contradiction_penalty += 0.25
        if action_coverage.get("top_two_low_value"):
            contradiction_penalty += 0.10
        if untested_count > 0 and not action_coverage.get("initial_exploration_complete"):
            contradiction_penalty += 0.05

        player_score = min(player_conf / max(self.DIRECTIONAL_PLAYER_CONFIDENCE, 1e-6), 1.0)
        goal_score = min(goal_conf / max(self.DIRECTIONAL_GOAL_CONFIDENCE, 1e-6), 1.0)
        coverage_score = min(coverage_ratio / max(self.MIN_EXPLORATION_COMPLETENESS, 1e-6), 1.0)

        score = (
            0.30 * player_score
            + 0.25 * goal_score
            + 0.15 * positions_known
            + 0.15 * coverage_score
            + 0.15 * evidence_score
            - contradiction_penalty
        )

        # B139: Geometry-aware priority.
        # If we know exactly where the player and goal are, and we are stuck in a loop 
        # or hitting low-value actions, we SHOULD graduate to directional play
        # to break the cycle, even if initial exploration isn't 'complete'.
        geometry_high_conf = (
            player_role is not None 
            and goal_role is not None 
            and player_conf >= self.DIRECTIONAL_PLAYER_CONFIDENCE 
            and goal_conf >= self.DIRECTIONAL_GOAL_CONFIDENCE
            and positions_known > 0.0
        )
        
        stuck_signals = context.get("loop_detected") or action_coverage.get("top_two_low_value")
        
        # If we are stuck but know geometry, give a huge boost to graduation.
        if geometry_high_conf and stuck_signals:
            score += 0.40 # Forced promotion boost
            
        score = max(0.0, min(score, 1.0))

        # B142: Evidence floor gate — if zero empirical evidence after enough steps, cap graduation
        graduation_capped_reason: Optional[str] = None
        evidence_floor_applied = False
        progress_decay_applied = 0.0
        pre_cap_score = score

        if evidence_score < 0.3 and chunk_progress == 0.0 and steps_using_chunk >= 3:
            # No empirical evidence that this plan works despite trying for 3+ steps
            max_allowed = max(0.4, evidence_score * 2)
            if score > max_allowed:
                score = max_allowed
                graduation_capped_reason = "evidence_floor"
                evidence_floor_applied = True

        # B142: Progress-decay penalty — graduation degrades with consecutive failures
        if consecutive_zero_reward_steps > 0:
            decay = 0.05 * consecutive_zero_reward_steps
            score = max(0.2, score - decay)
            progress_decay_applied = decay
            if graduation_capped_reason is None:
                graduation_capped_reason = "progress_decay"

        ready = (
            geometry_high_conf
            and (
                # Normal path: exploration is healthy and complete enough
                (
                    (action_coverage.get("initial_exploration_complete") or coverage_ratio >= self.MIN_EXPLORATION_COMPLETENESS)
                    and not action_coverage.get("top_two_low_value")
                    and not context.get("loop_detected")
                    and score >= self.GRADUATION_THRESHOLD
                )
                # B139 Emergency path: we are stuck but we know where to go
                or (stuck_signals and score >= self.GRADUATION_THRESHOLD)
            )
        )

        components = {
            "player_score": round(player_score, 3),
            "goal_score": round(goal_score, 3),
            "positions_known": round(positions_known, 3),
            "coverage_ratio": round(coverage_ratio, 3),
            "coverage_score": round(coverage_score, 3),
            "evidence_score": round(evidence_score, 3),
            "contradiction_penalty": round(contradiction_penalty, 3),
            "tested_count": float(tested_count),
            "untested_count": float(untested_count),
        }
        if ready:
            reason = (
                f"graduate directional: score={score:.2f} >= {self.GRADUATION_THRESHOLD:.2f}; "
                f"player={player_conf:.2f}, goal={goal_conf:.2f}, coverage={coverage_ratio:.2f}, "
                f"evidence={evidence_score:.2f}, penalty={contradiction_penalty:.2f}"
            )
        else:
            blockers: List[str] = []
            if player_role is None:
                blockers.append("missing player role")
            elif player_conf < self.DIRECTIONAL_PLAYER_CONFIDENCE:
                blockers.append(f"player confidence {player_conf:.2f} < {self.DIRECTIONAL_PLAYER_CONFIDENCE:.2f}")
            if goal_role is None:
                blockers.append("missing goal role")
            elif goal_conf < self.DIRECTIONAL_GOAL_CONFIDENCE:
                blockers.append(f"goal confidence {goal_conf:.2f} < {self.DIRECTIONAL_GOAL_CONFIDENCE:.2f}")
            if not positions_known:
                blockers.append("player/goal positions not both known")
            if coverage_ratio < self.MIN_EXPLORATION_COMPLETENESS and not action_coverage.get("initial_exploration_complete"):
                blockers.append(f"coverage {coverage_ratio:.2f} < {self.MIN_EXPLORATION_COMPLETENESS:.2f}")
            if action_coverage.get("top_two_low_value"):
                blockers.append("top actions are low_value")
            if context.get("loop_detected"):
                blockers.append("loop detected")
            if not blockers:
                blockers.append("score below threshold")
            reason = (
                f"stay explore: score={score:.2f} < {self.GRADUATION_THRESHOLD:.2f}; "
                f"player={player_conf:.2f}, goal={goal_conf:.2f}, coverage={coverage_ratio:.2f}, "
                f"evidence={evidence_score:.2f}, penalty={contradiction_penalty:.2f}; "
                + "; ".join(blockers)
            )

        return {
            "ready": ready,
            "score": score,
            "reason": reason,
            "components": components,
            # B142: Evidence floor and progress-decay trace fields
            "graduation_capped_reason": graduation_capped_reason,
            "evidence_floor_applied": evidence_floor_applied,
            "progress_decay_applied": progress_decay_applied,
            "pre_cap_score": pre_cap_score,
        }

    def _map_actions_to_vectors(self, action_facts: List[Dict[str, Any]]) -> Dict[str, tuple[float, float]]:
        """Map action IDs to their inferred (row, col) movement vectors."""
        mapping = {}
        for fact in action_facts:
            action = fact.get("action")
            if not action: continue
            
            # Use helpers from module level
            direction = _trend_direction_from_fact(action_facts, action)
            vector = _direction_vector(direction)
            if vector:
                mapping[action] = vector
        return mapping

    def generate_chunk(
        self,
        victory_condition: VictoryCondition,
        object_roles: Dict[int, ObjectRole],
        state_graph: Any,       # StateGraph from hypothesis.py
        current_hash: str,
        available_actions: List[str],
        step: int,
        hypothesis_context: Optional[Dict[str, Any]] = None,
    ) -> PlanChunk:
        """Generate the next plan chunk. Pure logic only; no SideQuests calls."""
        context = hypothesis_context or {}
        action_facts = context.get("action_facts") or []
        # B216: honor loop-detection blacklist passed in hypothesis_context
        blacklist = set()
        try:
            bl = (hypothesis_context or {}).get("loop_detected_action_blacklist")
            if bl:
                # accept list or single string
                if isinstance(bl, (list, set, tuple)):
                    blacklist = set(str(x) for x in bl if x)
                else:
                    blacklist = {str(bl)}
        except Exception:
            blacklist = set()

        # 1. Try BFS if we have a known goal state
        player_role = next(
            (r for r in object_roles.values() if r.role == RoleType.PLAYER), None
        )
        goal_role = next(
            (r for r in object_roles.values()
             if r.role in (RoleType.GOAL, RoleType.EXIT)), None
        )

        if goal_role and hasattr(state_graph, "find_path"):
            # Attempt BFS toward any state where goal object has changed
            # (approximate: we search for transitions that produced reward)
            high_reward_states = [
                t.to_hash for transitions in state_graph.edges.values()
                for t in transitions if t.reward_signal > 0.5
            ]
            for target_hash in high_reward_states:
                path = state_graph.find_path(current_hash, target_hash)
                if path:
                    actions = [t.action for t in path]
                    # Skip BFS paths that require a blacklisted action family
                    if blacklist and any((a in blacklist) for a in actions):
                        continue
                    graduation_reason = "bfs path found to known reward state"
                    return PlanChunk(
                        description=f"Navigate via known path to reward state ({len(actions)} steps)",
                        estimated_actions=actions,
                        success_condition="reach high-reward state",
                        source="bfs",
                        graduation_score=1.0,
                        graduation_reason=graduation_reason,
                        graduation_components={"bfs_path_found": 1.0, "path_length": float(len(actions))},
                    )

        # 2. Directional fallback: infer movement direction toward goal
        graduation = self._graduation_assessment(
            player_role,
            goal_role,
            hypothesis_context,
            available_actions,
        )
        if graduation["ready"]:
            p_pos = player_role.estimated_position or {}
            g_pos = goal_role.estimated_position or {}
            
            if p_pos and g_pos:
                dr = g_pos.get("row", 0) - p_pos.get("row", 0)
                dc = g_pos.get("col", 0) - p_pos.get("col", 0)
                goal_vec = (dr, dc)
                dist = abs(dr) + abs(dc)
                
                # B139: Smarter action selection using inferred geometry
                action_map = self._map_actions_to_vectors(action_facts)
                
                scored_actions = []
                for aid in available_actions:
                    if aid in blacklist:
                        # skip actions blacklisted due to loop-detection
                        continue
                    vec = action_map.get(aid)
                    # B154: Only use empirically observed action vectors, never assume
                    if not vec:
                        continue
                    
                    if vec:
                        # Score by dot product (how much does this move us TOWARD the goal?)
                        bias_score = (vec[0] * dr) + (vec[1] * dc)
                        scored_actions.append((bias_score, aid))
                
                # Filter for actions that move us toward the goal (bias_score > 0)
                good_actions = sorted([a for a in scored_actions if a[0] > 0], key=lambda x: x[0], reverse=True)
                
                if good_actions:
                    # Take the best action and repeat it a few times, or mix top two
                    best_aid = good_actions[0][1]
                    directions = [best_aid] * 4
                    if len(good_actions) > 1:
                        # Interleave second best if it also has high score
                        if good_actions[1][0] >= good_actions[0][0] * 0.5:
                            directions = [good_actions[0][1], good_actions[1][1]] * 2
                    
                    return PlanChunk(
                        description=f"Move {victory_condition.condition_type.value} toward goal (dist={dist:.1f})",
                        estimated_actions=directions,
                        success_condition="reduce distance to goal object",
                        source="directional",
                        graduation_score=float(graduation["score"]),
                        graduation_reason=str(graduation["reason"]),
                        graduation_components={
                            **dict(graduation["components"]),
                            "goal_dist": float(dist),
                            "geometry_bias": float(good_actions[0][0])
                        },
                    )
                else:
                    # Even if ready, we might not have 'good' actions yet.
                    # Fall through to explore to find more directional facts.
                    pass

        # 3. Exploration fallback: try unexplored actions
        if hasattr(state_graph, "get_unexplored_actions"):
            unexplored = state_graph.get_unexplored_actions(current_hash, available_actions)
        else:
            unexplored = []
        # Prefer unexplored/non-blacklisted actions when available (B216)
        action = None
        for cand in (unexplored or []) + (available_actions or []):
            if not cand:
                continue
            if cand in blacklist:
                continue
            action = cand
            break
        if action is None:
            action = unexplored[0] if unexplored else (available_actions[0] if available_actions else "ACTION1")
        no_progress_steps = int(
            context.get("consecutive_zero_reward_steps")
            or context.get("no_progress_steps")
            or 0
        )
        role_recovery_actions: List[str] = []
        if (
            player_role is None
            and goal_role is not None
            and (context.get("loop_detected") or no_progress_steps >= 4)
        ):
            for cand in (unexplored or []) + (available_actions or []):
                if not cand or cand in blacklist or cand in role_recovery_actions:
                    continue
                role_recovery_actions.append(cand)
                if len(role_recovery_actions) >= 3:
                    break
            if role_recovery_actions:
                return PlanChunk(
                    description="Role Recovery: probe distinct actions to re-ground the player",
                    estimated_actions=role_recovery_actions,
                    success_condition="recover a stable player role candidate",
                    source="explore",
                    graduation_score=float(graduation["score"]),
                    graduation_reason=f"{graduation['reason']} | role recovery",
                    graduation_components={
                        **dict(graduation["components"]),
                        "role_recovery": 1.0,
                        "probe_actions": float(len(role_recovery_actions)),
                    },
                )
        return PlanChunk(
            description="Explore: try unexplored action to gather more information",
            estimated_actions=[action],
            success_condition="observe new state",
            source="explore",
            graduation_score=float(graduation["score"]),
            graduation_reason=str(graduation["reason"]),
            graduation_components=dict(graduation["components"]),
        )


class DecisionGuard:
    """B115: Pre-execution guard that blocks or revises bad ARC moves."""

    def critique_action(
        self,
        action_id: str,
        available_actions: List[str],
        hypothesis_context: Dict[str, Any],
        active_chunk: Optional[PlanChunk],
        step_history: List[dict],
    ) -> Dict[str, Any]:
        """Inspect action against loop history, chunks, and facts.
        Returns: {
            "status": "approved" | "blocked" | "warned",
            "reason": str,
            "suggested_action": Optional[str]
        }
        """
        if action_id not in available_actions:
            return {
                "status": "blocked",
                "reason": f"Action {action_id} not available in current state.",
                "suggested_action": available_actions[0] if available_actions else None,
            }

        # 1. Loop Check: Avoid repeating moves that produced ZERO VISUAL CHANGE.
        # NOTE: reward==0 is NOT a useful signal — in ARC-AGI-3, reward is binary
        # (0 until you win a level). Use frame_delta.n_cells_changed instead.
        if step_history:
            # Single-action repetition check: same action with zero pixel change
            recent_no_effect = [
                s for s in step_history[-3:]
                if s.get("action_id") == action_id
                and s.get("frame_delta", {}).get("n_cells_changed", -1) == 0
            ]
            if len(recent_no_effect) >= 2:
                return {
                    "status": "warned",
                    "reason": f"Action {action_id} produced zero pixel change in {len(recent_no_effect)} recent attempts.",
                    "suggested_action": None,
                }

            # B139: Multi-action churn check (global stall)
            # If the last 5 steps across DIFFERENT actions all produced zero pixel change,
            # the agent is truly stuck (e.g. boxed in).
            global_no_effect = [
                s for s in step_history[-5:]
                if s.get("frame_delta", {}).get("n_cells_changed", -1) == 0
            ]
            if len(global_no_effect) >= 5:
                # Truly stuck — block actions already proven to have zero effect.
                if any(
                    s.get("action_id") == action_id
                    and s.get("frame_delta", {}).get("n_cells_changed", -1) == 0
                    for s in step_history[-10:]
                ):
                    return {
                        "status": "blocked",
                        "reason": f"Global stall detected (5 steps zero pixel change); blocking zero-effect {action_id}.",
                        "suggested_action": None,
                    }

        # 2. Chunk Alignment Check:
        if (
            active_chunk 
            and active_chunk.source in ("bfs", "directional")
            and active_chunk.estimated_actions
        ):
            chunk_action = active_chunk.estimated_actions[0]
            if action_id != chunk_action and chunk_action in available_actions:
                return {
                    "status": "warned",
                    "reason": f"Action {action_id} deviates from guidance-grade {active_chunk.source} chunk: {active_chunk.description}.",
                    "suggested_action": chunk_action,
                }

        # 3. Locked Evidence Check:
        facts = hypothesis_context.get("action_facts", [])
        fact = next((f for f in facts if f.get("action") == action_id), None)
        if fact and fact.get("value_status") == "harmful":
            return {
                "status": "blocked",
                "reason": f"Action {action_id} is marked as harmful: {fact.get('description')}",
                "suggested_action": None,
            }

        return {"status": "approved", "reason": "No guard violations detected.", "suggested_action": None}


# ── Solve Engine ──────────────────────────────────────────────────────

class SolveEngine:
    """Top-level controller. Called by orchestrator between hypothesize() and plan().

    Owns: ArchetypeClassifier, ObjectRoleMapper, VictoryHypothesizer,
          DissonanceDetector, PlanChunker, DecisionGuard.
    Consumes: hypothesis_context (from HypothesisManager.observe()),
              brain_client, llm_client.
    Produces: SolveContext.
    """

    def __init__(
        self,
        brain_client: Any,
        llm_client: Any,
        session_id: str,
        emit_trace_event: Optional[Callable[[str, str, Dict[str, Any], None, float], None]] = None,
        cost_tracker: Optional[Any] = None,
        loaded_procedures: Optional[List[Mapping[str, Any]]] = None,
    ) -> None:
        self.brain = brain_client
        self.llm = llm_client
        self.session_id = session_id
        self.cost_tracker = cost_tracker
        self._emit_trace = emit_trace_event  # B138: Optional trace callback

        self.archetype_classifier = ArchetypeClassifier()
        self.role_mapper = ObjectRoleMapper()
        self.victory_hypothesizer = VictoryHypothesizer()
        self.game_rule_hypothesizer = GameRuleHypothesizer()  # B151
        self.dissonance_detector = DissonanceDetector()
        self.plan_chunker = PlanChunker()
        self.decision_guard = DecisionGuard()

        self._archetype: GameArchetype = GameArchetype.UNKNOWN
        self._archetype_confidence: float = 0.0
        self._archetype_locked: bool = False
        self._object_roles: Dict[int, ObjectRole] = {}
        self._victory_condition: Optional[VictoryCondition] = None
        self._active_chunk: Optional[PlanChunk] = None
        self._chunk_history: List[PlanChunk] = []
        self._chunk_ledger: List[ChunkLedgerEntry] = []
        self._solve_plan_id: Optional[str] = None
        self._reward_history: List[float] = []
        self._role_resolution_notes: List[str] = []
        self._last_registered_top_plan: Optional[Dict[str, Any]] = None
        self._last_registered_chunk_plan: Optional[Dict[str, Any]] = None
        self._last_graduation_reevaluation: Dict[str, Any] = {}
        self._plateau_locked_family: Optional[str] = None
        self._plateau_active: bool = False
        self._game_rule_hypotheses: List[GameRuleHypothesis] = [] # B151
        # B169: KuzuDB role source of truth
        self._entity_graph: Optional["EntityGraphBuilder"] = None
        self._pending_role_writes: List[tuple[int, ObjectRole]] = []
        self._current_level: int = 0
        # B172: VictoryCondition persistence
        self._pending_vc_write: Optional[VictoryCondition] = None
        self._task_id: Optional[str] = None
        # B173: GameRuleHypothesis persistence
        self._pending_grh_writes: List[GameRuleHypothesis] = []
        # B174: ChunkExecution persistence (seq, entry, chunk) queued on completion/failure
        self._pending_chunk_writes: List[tuple[int, ChunkLedgerEntry, PlanChunk]] = []
        # B176: Plateau exploration state
        self._plateau_lock_duration: int = 0
        # B215: Minimum distinct action families required before entering plateau
        self.PLATEAU_MIN_DISTINCT_ACTIONS: int = 3
        # B216: Loop-detection blacklist (action families to avoid when routing)
        self._loop_detected_action_blacklist: Optional[set[str]] = None
        # B217: Ensure we only bootstrap victory once per task/level
        self._bootstrapped_victory_done: bool = False
        # B207: Plateau lock exhaustion tracking
        self._plateau_lock_family_replan_count: int = 0
        self._plateau_lock_last_family: Optional[str] = None
        # B214: Hard escape when a locked family yields repeated zero-delta outcomes.
        self._plateau_lock_zero_delta_streak: int = 0
        self._plateau_lock_last_family_for_delta: Optional[str] = None
        # B179: Cooldown for expensive victory inference LLM calls
        self._last_victory_attempt_step: int = -100
        # B208: Separate cooldown for replan-triggered victory attempts
        self._last_replan_victory_attempt_step: int = -100
        # B197: Procedure-guided solving state
        self._loaded_procedures: list[Mapping[str, Any]] = list(loaded_procedures or [])
        self._applied_procedure_id: Optional[str] = None
        self._procedure_failed: bool = False

        # If procedures are provided, initialize an active chunk from the top-ranked one
        try:
            if self._loaded_procedures:
                proc = self._loaded_procedures[0]
                steps = []
                if proc is not None:
                    sj = proc.get("steps_json") or proc.get("steps") or "[]"
                    if isinstance(sj, str):
                        try:
                            steps = json.loads(sj) if sj else []
                        except Exception:
                            steps = []
                    elif isinstance(sj, list):
                        steps = sj

                est_actions = [str(s.get("action") or s.get("step") or "") for s in (steps or []) if s]
                desc = proc.get("name") or proc.get("description") or f"procedure:{proc.get('procedure_id')}"
                chunk = PlanChunk(description=f"Procedure: {desc}", estimated_actions=est_actions, source="procedure")
                self._active_chunk = chunk
                self._chunk_history.append(chunk)
                self._applied_procedure_id = proc.get("procedure_id")
                self._procedure_failed = False
        except Exception:
            logger.exception("Failed to initialize procedure chunk")

    def _record_llm_usage(self):
        """B180: Record tokens from last LLM call into CostTracker."""
        if self.cost_tracker and self.llm and hasattr(self.llm, 'last_usage') and self.llm.last_usage:
            u = self.llm.last_usage
            self.cost_tracker.record(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))

    def _trace(
        self,
        event_type: str,
        operation: str,
        details: Dict[str, Any] | None = None,
        result: Dict[str, Any] | None = None,
        elapsed_ms: float | None = None,
    ) -> None:
        """B138: Emit a trace event if callback is registered.

        Args:
            event_type: Type of event (e.g., "solve_recall_plans_start")
            operation: Operation name (e.g., "recall_plans")
            details: Operation details/context
            result: Result of the operation
            elapsed_ms: Elapsed time in milliseconds
        """
        if self._emit_trace is not None:
            self._emit_trace(event_type, operation, details or {}, result, elapsed_ms)

    def _recent_zero_reward_streak(self) -> int:
        """Track global zero-reward momentum across chunk resets."""
        streak = 0
        for reward in reversed(self._reward_history):
            if reward <= 0.0:
                streak += 1
            else:
                break
        return streak

    # ── Gate Accessors for PhaseController (B201) ─────────────────────────
    def is_exploration_complete(self) -> bool:
        """Gate: has the agent sufficiently explored available actions?

        Conservative: return True only when internal graduation/coverage signals
        indicate a high coverage ratio. Defaults to False otherwise.
        """
        try:
            last = getattr(self, "_last_graduation_reevaluation", {}) or {}
            cov = float(last.get("coverage_ratio", 0.0) or 0.0)
            if cov >= PlanChunker.MIN_EXPLORATION_COMPLETENESS:
                return True
        except Exception:
            pass

        try:
            if self._active_chunk and getattr(self._active_chunk, "progress_score", 0.0) > 0.5:
                return True
        except Exception:
            pass

        return False

    def has_minimum_model(self) -> bool:
        """Gate: do we have at least a minimal model (player or goal identified).

        Tolerant: returns True if either a player or a goal role is known, or
        archetype confidence exceeds a low threshold.
        """
        try:
            player = next((r for r in self._object_roles.values() if r.role == RoleType.PLAYER), None)
            goal = next((r for r in self._object_roles.values() if r.role in (RoleType.GOAL, RoleType.EXIT)), None)
            return bool(player or goal or float(self._archetype_confidence or 0.0) >= 0.25)
        except Exception:
            return False

    def has_hypothesis(self) -> bool:
        """Gate: is there a usable hypothesis (archetype or victory) with confidence.

        Conservative threshold: 0.3 confidence.
        """
        try:
            if float(self._archetype_confidence or 0.0) >= 0.3:
                return True
            if self._victory_condition and float(getattr(self._victory_condition, "confidence", 0.0) or 0.0) >= 0.3:
                return True
        except Exception:
            pass
        return False

    def has_active_chunk(self) -> bool:
        """Gate: do we currently have an active chunk selected to execute?"""
        return getattr(self, "_active_chunk", None) is not None

    # ── B169: KuzuDB Role Source ───────────────────────────────────────

    def _set_role(self, color_id: int, role: ObjectRole):
        """Write role to cache and queue for KuzuDB persistence."""
        self._object_roles[color_id] = role
        if self._entity_graph:
            self._pending_role_writes.append((color_id, role))

    async def _flush_role_writes(self):
        """Persist queued roles to KuzuDB. Called at end of solve()."""
        if not self._entity_graph or not self._pending_role_writes:
            return
        
        # Take a snapshot to avoid concurrent mutation issues
        to_write = list(self._pending_role_writes)
        self._pending_role_writes.clear()
        
        for color_id, role in to_write:
            try:
                await self._entity_graph.persist_role(
                    color_id=color_id,
                    role=role.role.value,
                    confidence=role.confidence,
                    position=role.estimated_position,
                    level=self._current_level
                )
            except Exception as exc:
                logger.warning("B169: Failed to persist role for color %d: %s", color_id, exc)

    async def _sync_roles_from_db(self):
        """Load roles from KuzuDB into the _object_roles cache. Authority is the graph."""
        if not self._entity_graph:
            return
        
        try:
            db_roles = await self._entity_graph.load_all_roles(level=self._current_level)
            if db_roles:
                # Merge DB roles into cache. Higher confidence wins if we have un-flushed writes.
                # But usually DB IS the authority.
                for cid, role in db_roles.items():
                    existing = self._object_roles.get(cid)
                    if existing is None or role.confidence >= existing.confidence:
                        self._object_roles[cid] = role
        except Exception as exc:
            logger.warning("B169: _sync_roles_from_db failed: %s", exc)

    # ── B172: VictoryCondition Persistence ────────────────────────────

    def _set_victory_condition(self, vc: VictoryCondition):
        """Set victory condition and schedule KuzuDB persistence."""
        self._victory_condition = vc
        self._pending_vc_write = vc

    def _build_bootstrap_victory_condition(self, min_confidence: float = 0.35) -> Optional[VictoryCondition]:
        """Synthesize a conservative victory candidate from the current role map."""
        player_role = next((r for r in self._object_roles.values() if r.role == RoleType.PLAYER), None)
        goal_candidates = sorted(
            (r for r in self._object_roles.values() if r.role in (RoleType.GOAL, RoleType.EXIT)),
            key=lambda role: float(role.confidence or 0.0),
            reverse=True,
        )

        target_color: Optional[int] = None
        confidence = float(min_confidence)
        description = f"Bootstrap victory from archetype {self._archetype.value}"

        if goal_candidates:
            goal_role = goal_candidates[0]
            target_color = int(goal_role.color_id)
            confidence = max(confidence, min(0.78, 0.35 + float(goal_role.confidence or 0.0) * 0.4))
            description = f"Reach goal-like object at color {target_color}"
            if float(goal_role.confidence or 0.0) >= 0.7:
                description += " inferred from grounded role mapping"
        else:
            candidates = [
                role
                for role in self._object_roles.values()
                if role.color_id not in {0, getattr(player_role, "color_id", None)}
                and role.role != RoleType.DECORATION
            ]
            if not candidates:
                return None
            best_candidate = max(candidates, key=lambda role: float(role.confidence or 0.0))
            target_color = int(best_candidate.color_id)
            confidence = max(confidence, min(0.62, 0.28 + float(best_candidate.confidence or 0.0) * 0.35))
            description += f" (target_color={target_color})"

        if player_role is not None and float(player_role.confidence or 0.0) >= 0.7:
            confidence = min(0.82, confidence + 0.08)

        return VictoryCondition(
            condition_type=VictoryType.REACH_GOAL,
            description=description,
            target_color_id=target_color,
            confidence=confidence,
            source="bootstrap",
        )

    def _blacklist_action_family(self, family: Optional[str], *, step: int, reason: str) -> None:
        """Blacklist an action family after repeated no-progress evidence."""
        if not family:
            return
        current = set(self._loop_detected_action_blacklist or set())
        if family in current:
            return
        current.add(str(family))
        self._loop_detected_action_blacklist = current
        try:
            self._trace(
                "loop_escape",
                "apply_blacklist",
                {"step": step, "reason": reason},
                {"blacklist": sorted(current)},
            )
        except Exception:
            pass

    async def _flush_victory_condition(self):
        """Persist queued victory condition to KuzuDB."""
        if not self._entity_graph:
            # logger.debug("B172: No entity graph for flush")
            return
        if not self._pending_vc_write:
            # logger.debug("B172: No pending VC write")
            return
        
        vc = self._pending_vc_write
        self._pending_vc_write = None
        
        cid = f"{self._task_id}_L{self._current_level}_vc"
        tid = self._task_id
        level = self._current_level
        
        try:
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc).isoformat()
            
            await self._entity_graph.db.execute_write(
                """
                MERGE (v:VictoryCondition {condition_id: $cid})
                ON CREATE SET v.task_id = $tid, v.level = $level,
                              v.condition_type = $ctype, v.description = $descr,
                              v.target_color_id = $tcid, v.confidence = $conf,
                              v.source = $src, v.evidence_steps = $steps,
                              v.created_at = timestamp($now)
                ON MATCH SET v.condition_type = $ctype, v.description = $descr,
                             v.confidence = $conf, v.source = $src,
                             v.evidence_steps = $steps, v.last_updated = timestamp($now)
                """,
                {
                    "cid": cid, "tid": tid, "level": level,
                    "ctype": vc.condition_type.value, "descr": vc.description,
                    "tcid": vc.target_color_id if vc.target_color_id is not None else -1,
                    "conf": float(vc.confidence), "src": vc.source,
                    "steps": ",".join(str(s) for s in vc.evidence_steps),
                    "now": now,
                }
            )
            
            # Step 5: Wire REQUIRES_ENTITY edges if target_color_id exists
            if vc.target_color_id is not None:
                entity_id = f"{tid}_L{level}_c{vc.target_color_id}"
                await self._entity_graph.db.execute_write(
                    """
                    MATCH (v:VictoryCondition {condition_id: $cid}), (e:GridEntity {entity_id: $eid})
                    MERGE (v)-[:REQUIRES_ENTITY {requirement: $req}]->(e)
                    """,
                    {"cid": cid, "eid": entity_id, "req": "target"}
                )
                
        except Exception as exc:
            logger.warning("B172: Failed to persist victory condition: %s", exc)

    async def _load_victory_condition(self):
        """Load the most confident victory condition for this task/level from KuzuDB."""
        if not self._entity_graph or not self._task_id:
            return
        
        try:
            rows = await self._entity_graph.db.execute_read(
                """
                MATCH (v:VictoryCondition)
                WHERE v.task_id = $tid AND v.level = $level
                RETURN v.condition_type, v.description, v.target_color_id,
                       v.confidence, v.source, v.evidence_steps
                ORDER BY v.confidence DESC LIMIT 1
                """,
                {"tid": self._task_id, "level": self._current_level}
            )
            
            if rows:
                row = rows[0]
                tcid = int(row["v.target_color_id"])
                steps_str = row["v.evidence_steps"]
                steps = [int(s) for s in steps_str.split(",") if s] if steps_str else []
                
                self._victory_condition = VictoryCondition(
                    condition_type=VictoryType(row["v.condition_type"]),
                    description=row["v.description"],
                    target_color_id=tcid if tcid != -1 else None,
                    confidence=float(row["v.confidence"]),
                    source=row["v.source"],
                    evidence_steps=steps
                )
                logger.info("B172: Loaded victory condition from KuzuDB: %s", self._victory_condition.condition_type.value)
        except Exception as exc:
            logger.warning("B172: _load_victory_condition failed: %s", exc)

    # ── B173: GameRuleHypothesis Persistence ──────────────────────────

    def _set_game_rule_hypotheses(self, hypotheses: List[GameRuleHypothesis]):
        """B173: Set game rule hypotheses and schedule KuzuDB persistence."""
        self._game_rule_hypotheses = hypotheses
        self._pending_grh_writes = list(hypotheses)

    async def _flush_grh_writes(self):
        """B173: Persist queued game rule hypotheses to KuzuDB as Hypothesis nodes."""
        if not self._entity_graph or not self._pending_grh_writes or not self._task_id:
            return
        
        to_write = list(self._pending_grh_writes)
        self._pending_grh_writes.clear()
        
        db = self._entity_graph.db
        tid = self._task_id
        import hashlib
        
        for grh in to_write:
            hid = f"grh_{tid}_{hashlib.md5(grh.rule_description.encode()).hexdigest()[:8]}"
            now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
            
            params = {
                "id": hid,
                "descr": grh.rule_description,
                "cat": "game_rule",
                "conf": float(grh.confidence),
                "gtype": grh.source,
                "tid": tid,
                "status": "confirmed" if grh.confidence >= 0.8 else "active",
                "ecount": int(len(grh.evidence)),
                "now": now,
                "raw": json.dumps({
                    "action_semantics": grh.action_semantics,
                    "objective": grh.objective_description,
                    "level_strategy": grh.level_strategy,
                    "evidence": grh.evidence,
                    "contradictions": grh.contradictions,
                }),
            }
            
            try:
                await db.execute_write(
                    """
                    MERGE (h:Hypothesis {id: $id})
                    ON CREATE SET h.description = $descr, h.category = $cat,
                                  h.confidence = $conf, h.game_type = $gtype,
                                  h.task_id = $tid, h.status = $status,
                                  h.evidence_count = $ecount, h.text_raw = $raw,
                                  h.created_at = timestamp($now)
                    ON MATCH SET h.description = $descr, h.confidence = $conf,
                                 h.status = $status, h.evidence_count = $ecount,
                                 h.text_raw = $raw
                    """,
                    params
                )
                
                # B173: Wire GENERALIZES edges for same-description hypotheses
                await db.execute_write(
                    """
                    MATCH (h1:Hypothesis {id: $id1}), (h2:Hypothesis)
                    WHERE h2.category = 'game_rule' AND h2.task_id = $tid
                      AND h2.id <> $id1 AND h2.description = $descr
                    MERGE (h1)-[:GENERALIZES]->(h2)
                    """,
                    {"id1": hid, "tid": tid, "descr": grh.rule_description}
                )
            except Exception as exc:
                logger.warning("B173: Failed to persist GameRuleHypothesis %s: %s", hid, exc)
    
    async def _flush_chunk_writes(self):
        """B174: Persist queued ChunkExecution entries to KuzuDB and link to Plan."""
        if not self._entity_graph or not self._pending_chunk_writes or not self._task_id:
            return

        db = self._entity_graph.db
        tid = self._task_id

        to_write = list(self._pending_chunk_writes)
        self._pending_chunk_writes.clear()

        for seq, entry, chunk in to_write:
            try:
                now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
                stable_seq = max(int(seq), 0)
                exec_id = f"{tid}_L{self._current_level}_chunk_{stable_seq}"

                await db.execute_write(
                    """
                    MERGE (c:ChunkExecution {execution_id: $eid})
                    ON CREATE SET c.task_id = $tid, c.level = $level, c.plan_id = $pid,
                                  c.chunk_family = $family, c.description = $descr,
                                  c.status = $status, c.steps_used = $steps, c.graduation_score = $grad,
                                  c.evidence_at_end = $evidence, c.dissonance_triggered = $diss,
                                  c.outcome_summary = $outcome, c.created_at = timestamp($now)
                    ON MATCH SET c.status = $status, c.steps_used = $steps,
                                 c.graduation_score = $grad, c.evidence_at_end = $evidence,
                                 c.dissonance_triggered = $diss, c.outcome_summary = $outcome,
                                 c.last_updated = timestamp($now)
                    """,
                    {
                        "eid": exec_id,
                        "tid": tid,
                        "level": self._current_level,
                        "pid": chunk.plan_id or "",
                        "family": chunk.source,
                        "descr": entry.description,
                        "status": entry.status,
                        "steps": int(entry.steps_used),
                        "grad": float(getattr(chunk, "graduation_score", 0.0)),
                        "evidence": float(getattr(chunk, "graduation_components", {}).get("evidence_score", 0.0)),
                        "diss": True if entry.status == "failed" else False,
                        "outcome": entry.outcome_summary,
                        "now": now,
                    }
                )

                # Link to Plan if available
                if chunk.plan_id:
                    await db.execute_write(
                        """
                        MATCH (p:Plan {plan_id: $pid}), (c:ChunkExecution {execution_id: $eid})
                        MERGE (p)-[:EXECUTED_AS {seq: $seq}]->(c)
                        """,
                        {"pid": chunk.plan_id, "eid": exec_id, "seq": stable_seq}
                    )

            except Exception as exc:
                logger.warning("B174: Failed to persist ChunkExecution: %s", exc)

    async def _sync_grh_from_db(self):
        """B173: Load game rule hypotheses from KuzuDB."""
        if not self._entity_graph or not self._task_id:
            return
        
        try:
            db = self._entity_graph.db
            rows = await db.execute_read(
                """
                MATCH (h:Hypothesis)
                WHERE h.task_id = $tid AND h.category = 'game_rule'
                RETURN h.description, h.confidence, h.game_type, h.text_raw
                ORDER BY h.confidence DESC
                """,
                {"tid": self._task_id}
            )
            
            if rows:
                new_grhs = []
                for row in rows:
                    raw_str = row["h.text_raw"]
                    raw = json.loads(raw_str) if raw_str else {}
                    new_grhs.append(GameRuleHypothesis(
                        rule_description=row["h.description"],
                        action_semantics=raw.get("action_semantics", {}),
                        objective_description=raw.get("objective", ""),
                        level_strategy=raw.get("level_strategy", ""),
                        confidence=float(row["h.confidence"]),
                        evidence=raw.get("evidence", []),
                        contradictions=raw.get("contradictions", []),
                        source=row["h.game_type"]
                    ))
                self._game_rule_hypotheses = new_grhs
                logger.info("B173: Loaded %d game rule hypotheses from KuzuDB", len(new_grhs))
        except Exception as exc:
            logger.warning("B173: _sync_grh_from_db failed: %s", exc)

    def reevaluate_chunk_graduation(
        self,
        hypothesis_context: Optional[Dict[str, Any]],
        available_actions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """B142: Re-evaluate the active chunk's graduation based on actual performance.

        Called during each solve step to check if the chunk's graduation score
        should be degraded based on lack of progress and low evidence.

        Returns a dict with updated graduation info and trace fields for dissonance trigger.
        """
        if not self._active_chunk:
            self._last_graduation_reevaluation = {}
            return {}

        context = hypothesis_context or {}
        chunk_progress = self._active_chunk.progress_score
        consecutive_zero_reward = max(
            self._recent_zero_reward_streak(),
            self.dissonance_detector._zero_progress_streak,
            int(context.get("consecutive_zero_reward_steps", 0) or 0),
            0,
        )
        steps_using_chunk = max(
            self._active_chunk.steps_executed,
            self.dissonance_detector._chunk_steps,
            consecutive_zero_reward,
            int(context.get("steps_using_chunk", 0) or 0),
        )

        player_role = next(
            (r for r in self._object_roles.values() if r.role == RoleType.PLAYER), None
        )
        goal_role = next(
            (r for r in self._object_roles.values() if r.role in (RoleType.GOAL, RoleType.EXIT)), None
        )
        effective_actions = available_actions or context.get("available_actions") or list(self._active_chunk.estimated_actions) or []

        assessment = self.plan_chunker._graduation_assessment(
            player_role=player_role,
            goal_role=goal_role,
            hypothesis_context=context,
            available_actions=effective_actions,
            chunk_progress=chunk_progress,
            steps_using_chunk=steps_using_chunk,
            consecutive_zero_reward_steps=consecutive_zero_reward,
        )

        original_score = self._active_chunk.graduation_score
        assessed_score = float(assessment.get("score", original_score))
        new_score = max(0.0, min(original_score, assessed_score))

        self._active_chunk.graduation_score = new_score
        self._active_chunk.graduation_reason = str(assessment.get("reason", self._active_chunk.graduation_reason))
        self._active_chunk.graduation_components = {
            **dict(self._active_chunk.graduation_components or {}),
            **dict(assessment.get("components", {})),
            "pre_cap_score": round(float(assessment.get("pre_cap_score", original_score)), 3),
            "effective_steps_using_chunk": float(steps_using_chunk),
            "consecutive_zero_reward": float(consecutive_zero_reward),
        }

        result = {
            "original_score": original_score,
            "new_score": new_score,
            "graduation_capped_reason": assessment.get("graduation_capped_reason"),
            "evidence_floor_applied": bool(assessment.get("evidence_floor_applied", False)),
            "progress_decay_applied": float(assessment.get("progress_decay_applied", 0.0)),
            "evidence_score": float(self._active_chunk.graduation_components.get("evidence_score", 0.0)),
            "chunk_progress": chunk_progress,
            "steps_using_chunk": steps_using_chunk,
            "consecutive_zero_reward": consecutive_zero_reward,
        }
        self._last_graduation_reevaluation = result
        return result

    def _add_chunk_to_ledger_as_active(self, chunk: PlanChunk) -> None:
        """B124: Mark a chunk as active in the ledger."""
        entry = ChunkLedgerEntry(
            description=chunk.description,
            status="active",
            steps_used=0,
            outcome_summary=""
        )
        self._chunk_ledger.append(entry)
        # Note: don't prune here; prune only when transitioning to final state (completed/failed)

    def _record_chunk_completion(self, entry: ChunkLedgerEntry, chunk: PlanChunk) -> None:
        """B174: Queue a finalized chunk entry for durable persistence with a stable ledger seq."""
        if not self._entity_graph:
            return

        try:
            seq = next(
                (idx for idx, existing in enumerate(self._chunk_ledger) if existing is entry),
                len(self._chunk_ledger) - 1,
            )
            self._pending_chunk_writes.append((max(seq, 0), entry, chunk))
        except Exception:
            # Don't let persistence queuing break the solver
            logger.exception("B174: Failed to enqueue chunk execution for persistence")

    def _mark_chunk_completed(self, chunk: PlanChunk) -> None:
        """B124: Mark chunk as completed with progress summary."""
        found_entry = None
        if self._chunk_ledger:
            # Find the most recent entry for this chunk
            for entry in reversed(self._chunk_ledger):
                if entry.description == chunk.description and entry.status == "active":
                    entry.status = "completed"
                    entry.steps_used = chunk.steps_executed
                    entry.outcome_summary = f"progress={chunk.progress_score:.2f}"
                    found_entry = entry
                    break

        if found_entry:
            self._record_chunk_completion(found_entry, chunk)

        self._prune_chunk_ledger()

    def _mark_chunk_failed(self, chunk: PlanChunk, reason: str) -> None:
        """B124: Mark chunk as failed with reason."""
        found_entry = None
        if self._chunk_ledger:
            # Find the most recent entry for this chunk
            for entry in reversed(self._chunk_ledger):
                if entry.description == chunk.description and entry.status == "active":
                    entry.status = "failed"
                    entry.steps_used = chunk.steps_executed
                    entry.outcome_summary = reason
                    found_entry = entry
                    break

        if found_entry:
            self._record_chunk_completion(found_entry, chunk)

        self._prune_chunk_ledger()

    def _prune_chunk_ledger(self) -> None:
        """B124: Keep ledger to 8 entries, removing oldest completed entries first."""
        if len(self._chunk_ledger) <= 8:
            return
        # Keep all non-completed entries and the newest completed entries
        completed = [e for e in self._chunk_ledger if e.status == "completed"]
        non_completed = [e for e in self._chunk_ledger if e.status != "completed"]

        # Keep the newest completed entries up to 8 total
        to_keep_completed = max(0, 8 - len(non_completed))
        if to_keep_completed < len(completed):
            completed = completed[-to_keep_completed:]

        self._chunk_ledger = non_completed + completed

    def _score_action_families(self, context: Dict[str, Any], available_actions: List[str]) -> Dict[str, float]:
        """Score each action family using available evidence signals (B144)."""
        scores = {aid: 0.0 for aid in available_actions}
        observed_effects = context.get("observed_action_effects", [])
        
        # Action family mapping (simplified: each ID is its own family for now)
        for effect in observed_effects:
            aid = effect.get("action")
            if aid not in scores: continue
            
            # 1. Reward signal (weighted heavily)
            avg_reward = float(effect.get("avg_reward", 0.0) or 0.0)
            scores[aid] += avg_reward * 2.0
            
            # 2. Meaningful change (indicates interaction even if no reward)
            avg_change = float(effect.get("avg_meaningful_change", 0.0) or 0.0)
            scores[aid] += avg_change * 0.5
            
            # 3. Repeat failure penalty (B144 core requirement)
            zero_streak = int(effect.get("zero_reward_streak", 0) or 0)
            if zero_streak >= 2:
                scores[aid] -= (zero_streak * 0.25)
            # B176: Accelerated penalty for very long streaks
            if zero_streak >= 10:
                scores[aid] -= 1.0
                
            # 4. Rank score from hypothesis manager (composite heuristic)
            rank_score = float(effect.get("rank_score", 0.0) or 0.0)
            scores[aid] += rank_score * 0.3
            
            # 5. Dissonance / low value penalty
            if effect.get("value_status") in {"low_value", "ineffective"}:
                scores[aid] -= 0.5
        
        # B176: Exploration bonus for untried actions during sustained plateau
        tried_actions = {e.get("action") for e in observed_effects}
        global_zero_streak = self._recent_zero_reward_streak()
        
        if global_zero_streak >= 5:
            for aid in available_actions:
                if aid not in tried_actions:
                    curiosity_bonus = 0.3 + min(global_zero_streak * 0.02, 0.2)
                    scores[aid] += curiosity_bonus
                    # Trace for debugging
                    self._trace("explore_bonus_applied", "plateau_policy", {
                        "action": aid,
                        "bonus": round(curiosity_bonus, 2),
                        "streak": global_zero_streak
                    })

        # B198: Penalize actions mentioned in proactive warnings (only type == 'warning')
        try:
            warnings = context.get("proactive_warnings", []) if isinstance(context, dict) else []
            if warnings:
                warning_texts = " ".join(
                    str(w.get("text", "") or "").lower() for w in warnings if (w.get("type") or "").lower() == "warning"
                )
                if warning_texts:
                    for aid in list(scores.keys()):
                        # simple substring match against action id (e.g., 'action4')
                        if aid.lower() in warning_texts:
                            penalty = 0.3
                            old = scores.get(aid, 0.0)
                            new_score = max(0.05, old - penalty)
                            scores[aid] = new_score
                            # Emit trace for penalty application
                            self._trace("proactive_penalty", "proactive_warning_penalty", {"action": aid, "old_score": old, "new_score": new_score}, {"matched_text": warning_texts})
        except Exception:
            # Defensive: don't let proactive warning handling break scoring
            logger.exception("B198: _score_action_families failed to apply proactive warnings")

        return scores

    async def solve(
        self,
        observation: Dict[str, Any],
        hypothesis_context: Dict[str, Any],
        step: int,
        state_graph: Any,           # StateGraph instance from HypothesisManager
        current_state_hash: str,
        level_pattern: Optional["LevelPattern"] = None, # B150
        solved_levels: Optional[List[Dict]] = None,     # B157
    ) -> SolveContext:
        """Run one solve step. Returns SolveContext for orchestrator."""
        # B169: Sync roles from KuzuDB
        await self._sync_roles_from_db()
        # B172: Load victory condition from KuzuDB
        if not self._victory_condition:
            await self._load_victory_condition()
        # B173: Load game rule hypotheses from KuzuDB
        if not self._game_rule_hypotheses:
            await self._sync_grh_from_db()

        # Track reward history
        reward = float((hypothesis_context.get("last_transition_effect") or {}).get(
            "reward_signal", 0.0
        ))
        self._reward_history.append(reward)

        # 1. Archetype classification (algorithmic first)
        if not self._archetype_locked:
            archetype, confidence = self.archetype_classifier.update(hypothesis_context)

            # Call analogical_search for analogy votes (once per 10 steps after first lock candidate)
            if confidence >= 0.35 and step % 10 == 5:
                task_id = observation.get("task_id", "")
                analogy_query = f"ARC game {archetype.value} grid puzzle solve"
                try:
                    analogy_results = await self.brain.analogical_search(
                        query=analogy_query,
                        current_quest_id=task_id,
                        limit=5,
                        min_similarity=0.30,
                    )
                    archetype, confidence = self.archetype_classifier.apply_analogy_votes(
                        archetype, confidence, analogy_results.get("results", [])
                    )
                except Exception as exc:
                    logger.warning("analogical_search failed: %s", exc)

            # B206: Prevent transient regression to UNKNOWN when we already have
            # a usable archetype. If the new classifier output is UNKNOWN but
            # we previously had a non-UNKNOWN archetype with reasonable
            # confidence, hold the prior archetype and apply a modest decay.
            try:
                prior_arch = getattr(self, "_archetype", GameArchetype.UNKNOWN)
                prior_conf = float(getattr(self, "_archetype_confidence", 0.0) or 0.0)
                if (
                    archetype == GameArchetype.UNKNOWN
                    and prior_arch != GameArchetype.UNKNOWN
                    and prior_conf >= 0.25
                ):
                    # Keep prior archetype, decay confidence slightly but floor at 0.25
                    archetype = prior_arch
                    confidence = max(prior_conf - 0.05, 0.25)
                    # Trace the guard application for diagnostics
                    try:
                        self._trace(
                            "archetype_regression_guard",
                            "hold_archetype",
                            {"prior": prior_arch.value, "prior_conf": prior_conf, "applied_conf": confidence},
                        )
                    except Exception:
                        pass
            except Exception:
                # Defensive: do not break solve on guard logic failure
                logger.debug("B206: archetype regression guard evaluation failed", exc_info=True)

            # B148: Preserve grounded archetype confidence
            if archetype == self._archetype and confidence < self._archetype_confidence:
                # Sustain best recent confidence if not a pivot
                confidence = max(confidence, self._archetype_confidence - 0.02)

            self._archetype = archetype
            self._archetype_confidence = confidence
            if confidence >= ArchetypeClassifier.LOCK_THRESHOLD:
                self._archetype_locked = True
                logger.info("Archetype locked: %s (confidence=%.2f)", archetype.value, confidence)

        # B151: Game rule hypothesis from solved levels
        if level_pattern and solved_levels and step == 0:
            hypotheses = await self.game_rule_hypothesizer.hypothesize(
                level_pattern=level_pattern,
                solved_levels=solved_levels,
                llm_client=self.llm,
            )
            self._record_llm_usage()
            self._set_game_rule_hypotheses(hypotheses)
            if self._game_rule_hypotheses:
                logger.info("[B151] Generated %d game rule hypotheses", len(self._game_rule_hypotheses))

        # 2. Object role mapping (runs every step, lightweight)
        new_roles = self.role_mapper.update(hypothesis_context, observation, step)
        self._role_resolution_notes.extend(self._merge_persistent_roles(new_roles, step))
        if len(self._role_resolution_notes) > 6:
            self._role_resolution_notes = self._role_resolution_notes[-6:]

        # 3. Victory condition hypothesis (LLM call, sticky)
        should_replan, dissonance_reason = self.dissonance_detector.update(
            hypothesis_context, self._active_chunk, step
        )
        
        # B177: Accept orchestrator escalation
        if hypothesis_context.get("orchestrator_force_replan"):
            should_replan = True
            dissonance_reason = dissonance_reason or "orchestrator_no_progress_escalation"
        zero_reward_streak = self._recent_zero_reward_streak()
        chunk_context = dict(hypothesis_context or {})
        if zero_reward_streak > 0:
            chunk_context["consecutive_zero_reward_steps"] = max(
                int(chunk_context.get("consecutive_zero_reward_steps", 0) or 0),
                zero_reward_streak,
            )
            chunk_context["steps_using_chunk"] = max(
                int(chunk_context.get("steps_using_chunk", 0) or 0),
                zero_reward_streak,
            )

        # B216: Accept loop-detection blacklist injected by runner/orchestrator.
        # Persist it into solver state so it survives across the solve() call.
        try:
            incoming_bl = (hypothesis_context or {}).get("loop_detected_action_blacklist")
            if incoming_bl:
                if isinstance(incoming_bl, (list, set, tuple)):
                    self._loop_detected_action_blacklist = set(str(x) for x in incoming_bl if x)
                else:
                    self._loop_detected_action_blacklist = {str(incoming_bl)}
                # Trace blacklist application
                try:
                    self._trace("loop_escape", "apply_blacklist", {"step": step}, {"blacklist": list(self._loop_detected_action_blacklist)})
                except Exception:
                    pass
        except Exception:
            pass

        # B216: Clear blacklist when the last transition shows a successful state change
        try:
            last_eff = (hypothesis_context or {}).get("last_transition_effect") or {}
            n_changed = int(last_eff.get("n_cells_changed", last_eff.get("pixels_changed", 0)) or 0)
            if n_changed > 0 and self._loop_detected_action_blacklist:
                try:
                    self._trace("loop_escape", "clear_blacklist", {"step": step}, {"cleared": list(self._loop_detected_action_blacklist)})
                except Exception:
                    pass
                self._loop_detected_action_blacklist = None
        except Exception:
            pass

        # Ensure chunk_context passes the current blacklist to PlanChunker
        if self._loop_detected_action_blacklist:
            try:
                chunk_context = dict(chunk_context)
                chunk_context["loop_detected_action_blacklist"] = list(self._loop_detected_action_blacklist)
            except Exception:
                pass

        # B217: Archetype-seeded victory bootstrap — create a weak candidate when
        # archetype is stable but victory condition is still unknown after a few steps.
        try:
            has_unknown_vc = self._victory_condition is None or (getattr(self._victory_condition, 'condition_type', None) == VictoryType.UNKNOWN)
            if has_unknown_vc and not self._bootstrapped_victory_done and float(self._archetype_confidence or 0.0) >= 0.45 and step >= 2:
                vc = self._build_bootstrap_victory_condition(
                    min_confidence=0.45 if zero_reward_streak >= 3 else 0.35
                )
                if vc is not None:
                    self._set_victory_condition(vc)
                    self._bootstrapped_victory_done = True
                    try:
                        self._trace("victory_bootstrap", "bootstrap", {"step": step, "archetype": self._archetype.value}, {"candidate": vc.description, "confidence": vc.confidence})
                    except Exception:
                        pass
                    try:
                        await self.brain.upsert_lesson(domain=str(self._archetype.value), text=vc.description, valence=0.2, confidence=float(vc.confidence), tags=["bootstrap","victory"])
                    except Exception:
                        logger.debug("B217: upsert_lesson failed for victory bootstrap", exc_info=True)
        except Exception:
            logger.debug("B217: victory bootstrap guard failed", exc_info=True)

        # B179/B208: Multi-path victory condition inference trigger
        # Split cooldowns so replan-triggered attempts can be more aggressive.
        need_victory_hypothesis = False
        trigger_reason = ""

        vc_is_unknown = self._victory_condition is None or getattr(self._victory_condition, "condition_type", None) == VictoryType.UNKNOWN
        vc_confidence = float(getattr(self._victory_condition, "confidence", 0.0) or 0.0)
        vc_is_weak = vc_is_unknown or vc_confidence < 0.55

        GLOBAL_COOLDOWN = 6
        REPLAN_COOLDOWN = 2

        # Archetype-threshold path: conservative global cooldown
        if (
            vc_is_weak
            and self._archetype_confidence >= VictoryHypothesizer.CALL_THRESHOLD
            and (step - self._last_victory_attempt_step) >= GLOBAL_COOLDOWN
        ):
            need_victory_hypothesis = True
            trigger_reason = "archetype_threshold"
        # Replan-triggered path: shorter replan-specific cooldown
        elif (
            should_replan
            and vc_is_weak
            and (step - self._last_replan_victory_attempt_step) >= REPLAN_COOLDOWN
        ):
            need_victory_hypothesis = True
            trigger_reason = "replan"
        # Step-fallback and zero_progress still use the global cooldown
        elif (
            vc_is_weak
            and step >= 8
            and self._archetype != GameArchetype.UNKNOWN
            and (step - self._last_victory_attempt_step) >= GLOBAL_COOLDOWN
        ):
            need_victory_hypothesis = True
            trigger_reason = "step_fallback"
        elif (
            vc_is_weak
            and zero_reward_streak >= 3
            and self._archetype != GameArchetype.UNKNOWN
            and (step - self._last_victory_attempt_step) >= GLOBAL_COOLDOWN
        ):
            need_victory_hypothesis = True
            trigger_reason = "zero_progress"

        if need_victory_hypothesis:
            # logger.debug(f"[B179/B208] TRIGGERED: {trigger_reason}")
            # Always update the global last-attempt
            self._last_victory_attempt_step = step
            # Update replan-specific tracker only for replan-triggered attempts
            if trigger_reason == "replan":
                self._last_replan_victory_attempt_step = step
            self._trace("victory_inference_trigger", "victory_hypothesis", 
                        {"step": step, "trigger": trigger_reason, "archetype_conf": self._archetype_confidence})
            
            goal_query = f"{self._archetype.value} game win condition solve puzzle"
            try:
                # B138: Trace recall_plans call
                self._trace("solve_recall_plans_start", "recall_plans", {"step": step, "query": goal_query})
                _t0 = time.perf_counter()
                recall = await self.brain.recall_plans(
                    goal_query=goal_query,
                    session_id=self.session_id,
                    min_valence=0.2,
                    limit=3,
                )
                _elapsed = (time.perf_counter() - _t0) * 1000
                past_plans = recall.get("plans", [])
                self._trace("solve_recall_plans_end", "recall_plans", {"step": step}, {"count": len(past_plans)}, _elapsed)
            except Exception as exc:
                logger.warning("recall_plans failed: %s", exc)
                past_plans = []

            try:
                # B138: Trace recall_relevant_lessons call
                self._trace("solve_recall_lessons_start", "recall_lessons", {"step": step, "query": f"ARC game {self._archetype.value} win condition"})
                _t0 = time.perf_counter()
                lessons_result = await self.brain.recall_relevant_lessons(
                    query=f"ARC game {self._archetype.value} win condition",
                    limit=3,
                )
                _elapsed = (time.perf_counter() - _t0) * 1000
                lessons = lessons_result.get("lessons", [])
                self._trace("solve_recall_lessons_end", "recall_lessons", {"step": step}, {"count": len(lessons)}, _elapsed)
            except Exception as exc:
                logger.warning("recall_relevant_lessons failed: %s", exc)
                lessons = []

            new_vc = await self.victory_hypothesizer.hypothesize(
                archetype=self._archetype,
                object_roles=self._object_roles,
                brain_client=self.brain,
                llm_client=self.llm,
                session_id=self.session_id,
                task_id=observation.get("task_id", ""),
                reward_history=self._reward_history,
                dissonance_reason=dissonance_reason if should_replan else "",
                past_plans=past_plans,
                lessons=lessons,
            )
            self._record_llm_usage()
            
            # B148: Preserve recent-best victory condition if refresh is weak
            if self._victory_condition and new_vc.confidence < self._victory_condition.confidence:
                # If same condition type, keep best confidence (monotonic grounding)
                if new_vc.condition_type == self._victory_condition.condition_type:
                    new_vc.confidence = max(new_vc.confidence, self._victory_condition.confidence)
                # If old was grounded (>= 0.7) and new is weak (< 0.5), ignore weak refresh
                elif self._victory_condition.confidence >= 0.7 and new_vc.confidence < 0.5:
                    new_vc = self._victory_condition

            bootstrap_vc = self._build_bootstrap_victory_condition(
                min_confidence=0.5 if zero_reward_streak >= 3 else 0.4
            )
            if bootstrap_vc is not None and (
                new_vc.condition_type == VictoryType.UNKNOWN
                or new_vc.confidence + 0.05 < bootstrap_vc.confidence
            ):
                try:
                    self._trace(
                        "victory_inference_fallback",
                        "victory_hypothesis",
                        {"step": step, "reason": trigger_reason},
                        {"fallback": bootstrap_vc.description, "fallback_confidence": bootstrap_vc.confidence},
                    )
                except Exception:
                    pass
                new_vc = bootstrap_vc
            
            self._set_victory_condition(new_vc)

        # 4. Register one top-level solve plan once the victory hypothesis exists.
        if self._victory_condition is not None and self._solve_plan_id is None:
            await self._register_solve_plan(observation, step=step)

        # 5. Dissonance handling: report negative outcome + reset chunk
        if should_replan and self._active_chunk and self._active_chunk.plan_id:
            try:
                await self.brain.report_outcome(
                    plan_id=self._active_chunk.plan_id,
                    outcome=f"Chunk stalled: {dissonance_reason}",
                    valence=-0.6,
                    session_id=self.session_id,
                    valence_source="dissonance_detector",
                )
            except Exception as exc:
                logger.warning("report_outcome failed: %s", exc)
            # B124: Mark chunk as failed due to dissonance
            # If the active chunk originated from a Procedure, record that the procedure failed
            try:
                if getattr(self._active_chunk, "source", "") == "procedure":
                    self._procedure_failed = True
            except Exception:
                pass
            self._mark_chunk_failed(self._active_chunk, f"dissonance: {dissonance_reason}")
            self._active_chunk = None
            self.dissonance_detector.reset_chunk()

        # 6. Plan chunking: generate or continue active chunk
        available_actions = observation.get("available_actions") or [
            f"ACTION{i}" for i in range(1, 8)
        ]
        if self._active_chunk and self._active_chunk.estimated_actions:
            # B112: Align stale detection with orchestrator gate
            # BFS is strict: if the next action is blocked, the path is invalid.
            # Directional/Explore are looser: skip blocked actions if possible.
            first_action = self._active_chunk.estimated_actions[0]
            is_stale = False
            if self._active_chunk.source == "bfs":
                if first_action not in available_actions:
                    is_stale = True
            else:
                if not any(a in available_actions for a in self._active_chunk.estimated_actions):
                    is_stale = True

            if is_stale:
                logger.info(
                    "Discarding stale %s chunk: next action %s not in %s",
                    self._active_chunk.source,
                    first_action,
                    available_actions,
                )
                # B124: Mark chunk as failed due to staleness
                try:
                    if getattr(self._active_chunk, "source", "") == "procedure":
                        self._procedure_failed = True
                except Exception:
                    pass
                self._mark_chunk_failed(self._active_chunk, "stale: next action unavailable")
                self._active_chunk = None
                self.dissonance_detector.reset_chunk()

        # B113: Ensure directional chunks stay actionable by replenishing them
        # if they run low on steps. This avoids "empty shell" summaries.
        if self._active_chunk:
            is_exhausted = not self._active_chunk.estimated_actions
            is_running_low = (
                self._active_chunk.source == "directional"
                and len(self._active_chunk.estimated_actions) < 2
            )
            if is_exhausted or is_running_low:
                logger.info(
                    "Clearing %s chunk (%s) to allow replenishment",
                    self._active_chunk.source,
                    "exhausted" if is_exhausted else "running low",
                )
                # B124: Mark chunk as completed or failed based on progress
                reason = "exhausted" if is_exhausted else "running low"
                if self._active_chunk.progress_score > 0.3:
                    self._mark_chunk_completed(self._active_chunk)
                else:
                    self._mark_chunk_failed(self._active_chunk, reason)
                self._active_chunk = None
                # Note: we don't reset_chunk() here because replenishment isn't dissonance.

        if self._active_chunk is None and self._victory_condition is not None:
            self._active_chunk = self.plan_chunker.generate_chunk(
                victory_condition=self._victory_condition,
                object_roles=self._object_roles,
                state_graph=state_graph,
                current_hash=current_state_hash,
                available_actions=available_actions,
                step=step,
                hypothesis_context=chunk_context,
            )
            if self._active_chunk:
                self._chunk_history.append(self._active_chunk)
                # B124: Add chunk to ledger as active
                self._add_chunk_to_ledger_as_active(self._active_chunk)
                # B109: Register chunk as a Plan in SideQuests
                await self._register_chunk_plan(self._active_chunk, step=step)
            self.dissonance_detector.reset_chunk()

        # 7. Update chunk progress score and consume action
        if self._active_chunk:
            if reward > 0.3:
                self._active_chunk.progress_score = min(
                    self._active_chunk.progress_score + reward * 0.2, 1.0
                )
            self._active_chunk.steps_executed += 1
            # B109: Action consumption happens in the orchestrator via _enforce_action_policy,
            # but we track execution count here.

        # B142: Re-evaluate the chunk after recording the latest execution so the
        # strategy summary and dissonance state reflect the live score, not the pre-cap one.
        graduation_reevaluation = self.reevaluate_chunk_graduation(
            chunk_context,
            available_actions=available_actions,
        )
        if graduation_reevaluation and graduation_reevaluation.get("new_score") is not None:
            if graduation_reevaluation["new_score"] < 0.5 and not should_replan:
                should_replan = True
                dissonance_reason = (
                    f"Graduation dropped to {graduation_reevaluation['new_score']:.2f} "
                    f"(reason: {graduation_reevaluation.get('graduation_capped_reason', 'unknown')})"
                )

        # B144/B145/B146/B147: Plateau-aware exploitation policy logic
        zero_reward_streak = self._recent_zero_reward_streak()
        
        # B147: Grounding hysteresis — enter at 0.70, sustain at 0.65
        ENTER_THRESHOLD = 0.70
        SUSTAIN_THRESHOLD = 0.65
        threshold = SUSTAIN_THRESHOLD if self._plateau_active else ENTER_THRESHOLD
        
        player_conf = max((r.confidence for r in self._object_roles.values() if r.role == RoleType.PLAYER), default=0.0)
        goal_conf = max((r.confidence for r in self._object_roles.values() if r.role in (RoleType.GOAL, RoleType.EXIT)), default=0.0)
        
        player_grounded = player_conf >= threshold
        goal_grounded = goal_conf >= threshold
        
        # Trigger plateau mode once a zero-reward streak is clearly established and key entities are grounded.
        plateau_eligible = (zero_reward_streak >= 4 and player_grounded and goal_grounded)
        plateau_activation_mode = ""

        # B215: Require minimum distinct action families tried before entering plateau
        try:
            if plateau_eligible and not self._plateau_active:
                MIN_DISTINCT = int(getattr(self, 'PLATEAU_MIN_DISTINCT_ACTIONS', 3) or 3)
                action_coverage = (hypothesis_context or {}).get('action_coverage') or {}
                tested_count = int(action_coverage.get('tested_count', 0) or 0)
                observed = (hypothesis_context or {}).get('observed_action_effects') or []
                observed_actions = {e.get('action') for e in observed if e and e.get('action')}
                distinct_tried = tested_count if tested_count > 0 else len(observed_actions)
                if distinct_tried < MIN_DISTINCT:
                    try:
                        self._trace(
                            "plateau_deferred",
                            "plateau_policy",
                            {"step": step, "tested_count": tested_count, "distinct_tried": distinct_tried, "required": MIN_DISTINCT},
                            {"reason": "min_exploration_not_met"},
                        )
                    except Exception:
                        pass
                    plateau_eligible = False
        except Exception:
            pass
        
        if plateau_eligible:
            if not self._plateau_active:
                plateau_activation_mode = "direct"
                self._plateau_active = True
            else:
                # Sustained via hysteresis
                plateau_activation_mode = "sticky" if player_conf < ENTER_THRESHOLD or goal_conf < ENTER_THRESHOLD else "direct"
        else:
            # Grounding collapsed or streak broken
            if self._plateau_active:
                logger.info("B147: Plateau mode deactivated (grounding collapsed or streak broken)")
                self._plateau_active = False
                self._plateau_locked_family = None # Reset lock on deactivation

        plateau_mode = self._plateau_active
        plateau_reason = ""
        action_family_scores = {}
        ranked_families = []
        
        if plateau_mode:
            plateau_reason = f"sustained zero-reward streak ({zero_reward_streak} steps) with grounded entities"
            if plateau_activation_mode == "sticky":
                plateau_reason += f" (sticky sustain: p_conf={player_conf:.3f}, g_conf={goal_conf:.3f})"
            
            action_family_scores = self._score_action_families(hypothesis_context, available_actions)
            ranked_families = sorted(action_family_scores.keys(), key=lambda k: action_family_scores[k], reverse=True)
            if self._loop_detected_action_blacklist:
                ranked_families = [
                    family for family in ranked_families
                    if family not in self._loop_detected_action_blacklist
                ] or ranked_families
            
            # B176: Track lock duration for threshold decay
            if self._plateau_locked_family is not None:
                self._plateau_lock_duration += 1
            else:
                self._plateau_lock_duration = 0

            # B146: Persist authoritative locked family
            best_candidate = ranked_families[0] if ranked_families else None
            unlock_reason = ""
            
            if self._plateau_locked_family is None:
                # Initial lock
                self._plateau_locked_family = best_candidate
                self._plateau_lock_duration = 0
                self._trace("solve_plateau_lock_initial", "plateau_policy", 
                            {"step": step, "family": self._plateau_locked_family}, 
                            {"reason": "plateau entry"})
            else:
                # Check for explicit unlock conditions
                # 1. Best candidate is significantly better than current lock
                # B176: Decay threshold from 0.5 to 0.1 over time to allow curiosity win
                lock_threshold = max(0.1, 0.5 - (self._plateau_lock_duration * 0.05))
                
                current_score = action_family_scores.get(self._plateau_locked_family, -1.0)
                best_score = action_family_scores.get(best_candidate, -1.0) if best_candidate else -1.0
                
                if best_candidate and best_score > current_score + lock_threshold:
                    unlock_reason = f"evidence shift (threshold={lock_threshold:.2f}): {best_candidate}({best_score:.2f}) outranks {self._plateau_locked_family}({current_score:.2f})"
                
                # 2. Current lock is no longer available
                elif self._plateau_locked_family not in available_actions:
                    unlock_reason = f"action removed: {self._plateau_locked_family} no longer available"
                
                # 3. Explicit exhaustion signal (from orchestrator/trace via context)
                # (Future refinement: if reward history shows lock is definitely dead)

                if unlock_reason:
                    old_lock = self._plateau_locked_family
                    self._plateau_locked_family = best_candidate
                    self._plateau_lock_duration = 0 # Reset on switch
                    self._trace("solve_plateau_lock_changed", "plateau_policy", 
                                {"step": step, "from": old_lock, "to": self._plateau_locked_family}, 
                                {"reason": unlock_reason})

            # B214: Hard plateau escape based on repeated zero-delta outcomes.
            # If the same locked family keeps producing no meaningful grid change,
            # force a family rotation (or clear the lock) and trigger replan.
            ZERO_DELTA_THRESHOLD = 0.01
            ZERO_DELTA_ESCAPE_THRESHOLD = 2
            meaningful_change = float((hypothesis_context.get("last_transition_effect") or {}).get(
                "meaningful_change_score", 0.0
            ))
            locked_family = self._plateau_locked_family
            if locked_family is None:
                self._plateau_lock_zero_delta_streak = 0
                self._plateau_lock_last_family_for_delta = None
            else:
                if locked_family != self._plateau_lock_last_family_for_delta:
                    self._plateau_lock_zero_delta_streak = 0
                if meaningful_change <= ZERO_DELTA_THRESHOLD:
                    self._plateau_lock_zero_delta_streak += 1
                else:
                    self._plateau_lock_zero_delta_streak = 0
                self._plateau_lock_last_family_for_delta = locked_family

                if self._plateau_lock_zero_delta_streak >= ZERO_DELTA_ESCAPE_THRESHOLD:
                    alternate_family = next(
                        (
                            family for family in ranked_families
                            if family != locked_family and family in available_actions
                        ),
                        None,
                    )
                    self._trace(
                        "solve_plateau_zero_delta_escape",
                        "plateau_policy",
                        {
                            "step": step,
                            "locked_family": locked_family,
                            "streak": self._plateau_lock_zero_delta_streak,
                            "meaningful_change": meaningful_change,
                        },
                        {
                            "alternate_family": alternate_family,
                            "reason": "repeated_zero_delta",
                        },
                    )

                    if self._active_chunk and getattr(self._active_chunk, "source", "") == "plateau_exploitation":
                        self._mark_chunk_failed(self._active_chunk, "plateau_zero_delta_escape")
                    self._active_chunk = None
                    self._blacklist_action_family(
                        locked_family,
                        step=step,
                        reason="plateau_zero_delta_escape",
                    )

                    self._plateau_locked_family = alternate_family
                    self._plateau_lock_duration = 0
                    self._plateau_lock_family_replan_count = 0
                    self._plateau_lock_last_family = None
                    self._plateau_lock_zero_delta_streak = 0
                    self._plateau_lock_last_family_for_delta = self._plateau_locked_family
                    if self._plateau_locked_family is None:
                        self._plateau_active = False

                    should_replan = True
                    dissonance_reason = (
                        f"plateau lock '{locked_family}' produced repeated zero-delta outcomes; forcing strategy shift"
                    )

            # B207: Plateau lock exhaustion guard — force-unlock after repeated
            # no-progress / replan cycles for the same locked family.
            EXHAUSTION_THRESHOLD = 2
            cur_family = self._plateau_locked_family
            if cur_family is not None:
                # If the family didn't change and we're being asked to replan,
                # count consecutive replan cycles against the locked family.
                if self._plateau_lock_last_family == cur_family and should_replan:
                    self._plateau_lock_family_replan_count += 1
                elif self._plateau_lock_last_family != cur_family:
                    # Reset counter when the family changes
                    self._plateau_lock_family_replan_count = 0
                # Remember last seen family
                self._plateau_lock_last_family = cur_family

                if self._plateau_lock_family_replan_count >= EXHAUSTION_THRESHOLD:
                    try:
                        self._trace(
                            "solve_plateau_lock_exhausted",
                            "plateau_policy",
                            {"step": step, "family": cur_family},
                            {"reason": "plateau_exhausted"},
                        )
                    except Exception:
                        pass
                    # Mark current plateau exploitation chunk failed and clear lock
                    if self._active_chunk and getattr(self._active_chunk, "source", "") == "plateau_exploitation":
                        self._mark_chunk_failed(self._active_chunk, "plateau_exhausted")
                    self._active_chunk = None
                    self._blacklist_action_family(
                        cur_family,
                        step=step,
                        reason="plateau_exhausted",
                    )
                    self._plateau_locked_family = None
                    self._plateau_lock_duration = 0
                    self._plateau_active = False
                    # Reset exhaustion counters
                    self._plateau_lock_family_replan_count = 0
                    self._plateau_lock_last_family = None

            # B145/B146: Replace or Update Plateau Exploitation chunk
            # Use the AUTHORITATIVE locked family for the chunk
            top_family = self._plateau_locked_family
            if top_family:
                if self._active_chunk and (self._active_chunk.source == "explore" or (self._active_chunk.source == "plateau_exploitation" and top_family not in self._active_chunk.description)):
                    logger.info("B146: Syncing Plateau Exploitation chunk to locked family: %s", top_family)
                    
                    # Mark old chunk failed if it was mismatched
                    self._mark_chunk_failed(self._active_chunk, "plateau_sync" if self._active_chunk.source == "plateau_exploitation" else "plateau_replacement")
                    
                    # Create/Sync exploitation chunk
                    self._active_chunk = PlanChunk(
                        description=f"Plateau Exploitation: commit to top-ranked {top_family}",
                        estimated_actions=[top_family] * 3,
                        success_condition="break zero-reward plateau",
                        source="plateau_exploitation",
                        graduation_score=1.0,
                        graduation_reason="plateau_lock",
                        graduation_components={"plateau_mode": 1.0, "locked_family": top_family}
                    )
                    self._chunk_history.append(self._active_chunk)
                    self._add_chunk_to_ledger_as_active(self._active_chunk)
                    await self._register_chunk_plan(self._active_chunk, step=step)

            # Emit regular trace event
            if step % 5 == 0 or zero_reward_streak == 5:
                self._trace("solve_plateau_detection", "plateau_policy", 
                            {"step": step, "streak": zero_reward_streak, "locked": self._plateau_locked_family}, 
                            {"top_candidate": ranked_families[0] if ranked_families else "none", "score": action_family_scores.get(ranked_families[0]) if ranked_families else 0.0})

        # Build strategy summary for prompt
        strategy = self._build_strategy_summary()
        if plateau_mode:
            top_f = self._plateau_locked_family or "none"
            strategy = f"{strategy} | PLATEAU: {plateau_reason} | LOCKED FAMILY: {top_f}"

        # B169: Persist any role updates to KuzuDB
        await self._flush_role_writes()
        # B172: Persist victory condition to KuzuDB
        await self._flush_victory_condition()
        # B173: Persist game rule hypotheses to KuzuDB
        await self._flush_grh_writes()
        # B174: Persist any queued chunk execution writes to KuzuDB
        await self._flush_chunk_writes()

        return SolveContext(
            archetype=self._archetype,
            archetype_confidence=self._archetype_confidence,
            object_roles=dict(self._object_roles),
            victory_condition=self._victory_condition,
            active_chunk=self._active_chunk,
            dissonance_detected=should_replan,
            dissonance_reason=dissonance_reason,
            strategy_summary=strategy,
            chunk_ledger=list(self._chunk_ledger),
            game_rule_hypotheses=list(self._game_rule_hypotheses),
            plateau_mode=plateau_mode,
            plateau_reason=plateau_reason,
            plateau_activation_mode=plateau_activation_mode,
            plateau_locked_family=self._plateau_locked_family,
            ranked_action_families=ranked_families,
            action_family_scores=action_family_scores,
        )

    def _plan_changed(
        self,
        plan_type: str,  # "top" | "chunk"
        goal: str,
        steps: List[str],
        force: bool = False,
    ) -> bool:
        """B137: Check if a plan has materially changed.

        Returns True if the plan should be re-registered, False if it matches the last registered version.

        Args:
            plan_type: "top" for top-level solve plan, "chunk" for chunk plan
            goal: Plan goal/description
            steps: List of action steps
            force: If True, always return True (e.g., on dissonance reset)
        """
        if force:
            return True

        last_plan = (
            self._last_registered_top_plan if plan_type == "top"
            else self._last_registered_chunk_plan
        )

        if last_plan is None:
            return True

        # Compare goal and steps
        return (last_plan.get("goal") != goal or
                last_plan.get("steps") != steps)

    async def _register_chunk_plan(self, chunk: PlanChunk, step: int = 0) -> None:
        """B109: Register an active chunk as a plan in SideQuests.

        B137: Suppresses re-registration of identical chunk plans via idempotency check.
        B138: Emits trace events for solve-internal brain I/O.
        """
        # B137: Check if plan has changed before registering
        if not self._plan_changed(
            plan_type="chunk",
            goal=chunk.description,
            steps=chunk.estimated_actions or ["Execute strategy toward goal"],
        ):
            logger.debug("Skipping chunk plan registration (identical to last): %s", chunk.description)
            # Emit trace event for audit trail
            try:
                await self.brain.trace_event(
                    event_type="plan_registration_skipped",
                    metadata={
                        "plan_type": "chunk",
                        "reason": "identical_to_last_registered",
                        "chunk_description": chunk.description,
                    },
                )
            except Exception:
                pass  # Don't fail the solve step if tracing fails
            return

        try:
            # B138: Trace register_plan call
            steps_list = chunk.estimated_actions or ["Execute strategy toward goal"]
            self._trace("solve_register_plan", "register_plan", {
                "step": step,
                "plan_type": "chunk",
                "goal": chunk.description,
                "steps_count": len(steps_list),
            })
            _t0 = time.perf_counter()
            plan_payload = await self.brain.register_plan(
                goal=chunk.description,
                steps=steps_list,
                session_id=self.session_id,
            )
            _elapsed = (time.perf_counter() - _t0) * 1000
            chunk.plan_id = plan_payload.get("plan_id")
            # B137: Cache the registered plan
            self._last_registered_chunk_plan = {
                "goal": chunk.description,
                "steps": steps_list,
            }
            self._trace("solve_register_plan_done", "register_plan", {"step": step, "plan_type": "chunk"}, {"plan_id": chunk.plan_id}, _elapsed)
            logger.info("Chunk plan registered: %s (%s)", chunk.plan_id, chunk.description)
        except Exception as exc:
            logger.warning("register_chunk_plan failed: %s", exc)

    def peek_action_consequences(self, action_id: str, hypothesis_context: dict) -> dict:
        """B114: Local sandbox check. How does this action align with known facts?"""
        facts = hypothesis_context.get("action_facts", [])
        fact = next((f for f in facts if f.get("action") == action_id), None)
        
        chunk = self._active_chunk
        chunk_match = False
        if chunk and chunk.estimated_actions and chunk.estimated_actions[0] == action_id:
            chunk_match = True
            
        return {
            "action_id": action_id,
            "has_fact": fact is not None,
            "fact_summary": fact.get("description", "no prior evidence") if fact else "none",
            "matches_active_chunk": chunk_match,
            "chunk_description": chunk.description if chunk else "none",
        }

    def critique_action(
        self,
        action_id: str,
        available_actions: List[str],
        hypothesis_context: dict,
        step_history: List[dict],
    ) -> dict:
        """B115: Expose decision guard to orchestrator."""
        return self.decision_guard.critique_action(
            action_id=action_id,
            available_actions=available_actions,
            hypothesis_context=hypothesis_context,
            active_chunk=self._active_chunk,
            step_history=step_history,
        )

    def _build_strategy_summary(self) -> str:
        parts = [f"ARCHETYPE: {self._archetype.value} (conf={self._archetype_confidence:.2f})"]
        if self._victory_condition:
            vc = self._victory_condition
            parts.append(f"GOAL: {vc.condition_type.value} — {vc.description} (conf={vc.confidence:.2f})")
        primary_player = next((color_id for color_id, role in self._object_roles.items() if role.role == RoleType.PLAYER), None)
        primary_goal = next((color_id for color_id, role in self._object_roles.items() if role.role in (RoleType.GOAL, RoleType.EXIT)), None)
        parts.append(
            "PRIMARY ROLES: "
            f"player={primary_player if primary_player is not None else 'none'}, "
            f"goal={primary_goal if primary_goal is not None else 'none'}"
        )
        if self._solve_plan_id:
            parts.append(f"PLAN: {self._solve_plan_id}")
        if self._active_chunk:
            ch = self._active_chunk
            parts.append(
                f"CHUNK: {ch.description} [{ch.source}] progress={ch.progress_score:.2f}"
            )
            if ch.graduation_reason:
                parts.append(
                    f"GRADUATION: {ch.graduation_reason} (score={ch.graduation_score:.2f})"
                )
        if self._role_resolution_notes:
            parts.append("ROLE RESOLUTION: " + " | ".join(self._role_resolution_notes[-3:]))
        if self._chunk_history:
            parts.append(f"CHUNKS: {len(self._chunk_history)}")
        return " | ".join(parts)

    def reset_for_retry(self) -> None:
        """Reset ephemeral state. Preserve archetype and victory condition."""
        self._active_chunk = None
        self._chunk_history = []
        self._solve_plan_id = None
        # B137: Reset plan tracking to allow fresh registrations on retry
        self._last_registered_top_plan = None
        self._last_registered_chunk_plan = None
        self._last_graduation_reevaluation = {}
        self.dissonance_detector.reset_chunk()
        self.dissonance_detector._zero_progress_streak = 0
        self._role_resolution_notes = []
        self._plateau_active = False
        self._plateau_locked_family = None
        self._loop_detected_action_blacklist = None

    def _best_other_primary(self, role_type: RoleType, exclude_color: int) -> tuple[Optional[int], float]:
        """Return the strongest existing primary of a role type, excluding one color."""
        best_color: Optional[int] = None
        best_conf = 0.0
        for color_id, role in self._object_roles.items():
            if color_id == exclude_color or role.role != role_type:
                continue
            conf = float(role.confidence or 0.0)
            if conf > best_conf:
                best_color = color_id
                best_conf = conf
        return best_color, best_conf

    def _merge_persistent_roles(self, new_roles: Dict[int, ObjectRole], step: int) -> List[str]:
        """Merge step-level roles into the persistent role map with conflict handling."""
        notes: List[str] = []
        previous_player_color = next(
            (color_id for color_id, role in self._object_roles.items() if role.role == RoleType.PLAYER),
            None,
        )
        previous_player = self._object_roles.get(previous_player_color) if previous_player_color is not None else None

        for color_id, new_role in new_roles.items():
            existing = self._object_roles.get(color_id)
            if existing is None:
                self._set_role(color_id, new_role)
                continue

            if existing.role == new_role.role:
                # B148: Preserve grounded confidence for same-role refreshes
                if existing.confidence >= 0.7 and new_role.confidence < existing.confidence:
                    # Sustain high confidence if it's the same role assignment
                    new_role.confidence = max(new_role.confidence, existing.confidence)

                if new_role.confidence >= existing.confidence:
                    new_role.evidence_steps = sorted(set((existing.evidence_steps or []) + (new_role.evidence_steps or []) + [step]))
                    if existing.estimated_position and not new_role.estimated_position:
                        new_role.estimated_position = existing.estimated_position
                    self._set_role(color_id, new_role)
                else:
                    existing.evidence_steps = sorted(set((existing.evidence_steps or []) + (new_role.evidence_steps or []) + [step]))
                    if not existing.estimated_position and new_role.estimated_position:
                        existing.estimated_position = new_role.estimated_position
                continue

            if existing.role != RoleType.UNKNOWN and new_role.role == RoleType.UNKNOWN:
                existing.evidence_steps = sorted(set((existing.evidence_steps or []) + (new_role.evidence_steps or []) + [step]))
                if not existing.estimated_position and new_role.estimated_position:
                    existing.estimated_position = new_role.estimated_position
                notes.append(
                    f"step {step}: preserved {existing.role.value} at color_{color_id}; ignored unknown"
                )
                continue

            if existing.role == RoleType.UNKNOWN and new_role.role != RoleType.UNKNOWN:
                new_role.evidence_steps = sorted(set((existing.evidence_steps or []) + (new_role.evidence_steps or []) + [step]))
                if existing.estimated_position and not new_role.estimated_position:
                    new_role.estimated_position = existing.estimated_position
                self._set_role(color_id, new_role)
                notes.append(
                    f"step {step}: replaced unknown with {new_role.role.value} at color_{color_id}"
                )
                continue

            if existing.role == RoleType.DECORATION and new_role.role in {RoleType.INTERMEDIATE, RoleType.GOAL, RoleType.PLAYER}:
                new_role.evidence_steps = sorted(set((existing.evidence_steps or []) + (new_role.evidence_steps or []) + [step]))
                if existing.estimated_position and not new_role.estimated_position:
                    new_role.estimated_position = existing.estimated_position
                should_upgrade = (
                    new_role.confidence >= existing.confidence
                    or existing.confidence <= 0.5
                    or (new_role.role == RoleType.INTERMEDIATE and new_role.estimated_position is not None)
                )
                if should_upgrade:
                    self._set_role(color_id, new_role)
                    notes.append(
                        f"step {step}: replaced decoration with {new_role.role.value} at color_{color_id}"
                    )
                else:
                    existing.evidence_steps = new_role.evidence_steps
                    notes.append(
                        f"step {step}: preserved decoration at color_{color_id}; ignored {new_role.role.value}"
                    )
                continue

            if existing.role == RoleType.PLAYER and new_role.role == RoleType.GOAL:
                existing.evidence_steps = sorted(set((existing.evidence_steps or []) + (new_role.evidence_steps or []) + [step]))
                notes.append(
                    f"step {step}: kept player at color_{color_id}; rejected goal flip (conf={new_role.confidence:.2f})"
                )
                continue

            if existing.role == RoleType.GOAL and new_role.role == RoleType.PLAYER:
                other_player_color, other_player_conf = self._best_other_primary(RoleType.PLAYER, exclude_color=color_id)
                grounded_goal = existing.confidence >= 0.7
                grounded_other_player = other_player_conf >= 0.7
                if grounded_other_player or (grounded_goal and new_role.confidence < existing.confidence + 0.10):
                    existing.evidence_steps = sorted(set((existing.evidence_steps or []) + (new_role.evidence_steps or []) + [step]))
                    if not existing.estimated_position and new_role.estimated_position:
                        existing.estimated_position = new_role.estimated_position
                    anchor = f" while player_{other_player_color} already grounded" if grounded_other_player and other_player_color is not None else ""
                    notes.append(
                        f"step {step}: kept goal at color_{color_id}; rejected player flip{anchor} (conf={new_role.confidence:.2f})"
                    )
                    continue

                new_role.evidence_steps = sorted(set((existing.evidence_steps or []) + (new_role.evidence_steps or []) + [step]))
                self._set_role(color_id, new_role)
                notes.append(
                    f"step {step}: promoted player at color_{color_id}; demoted conflicting goal"
                )
                continue

            # B148: Contradiction-aware demotion for grounded roles
            # If current role is grounded (>= 0.7), only allow replacement if new role 
            # is SIGNIFICANTLY more confident or provides strong contradictory evidence.
            is_grounded = existing.confidence >= 0.7
            significant_threshold = 0.15 if is_grounded else 0.0

            if new_role.confidence > (existing.confidence + significant_threshold):
                self._set_role(color_id, new_role)
                notes.append(
                    f"step {step}: replaced {existing.role.value} with {new_role.role.value} at color_{color_id}"
                )
            else:
                existing.evidence_steps = sorted(set((existing.evidence_steps or []) + (new_role.evidence_steps or []) + [step]))
                notes.append(
                    f"step {step}: preserved {existing.role.value} at color_{color_id}; ignored {new_role.role.value}"
                )

        notes.extend(self._demote_extra_primaries(RoleType.PLAYER, step, new_roles))
        notes.extend(self._demote_extra_primaries(RoleType.GOAL, step, new_roles))

        has_player = any(role.role == RoleType.PLAYER for role in self._object_roles.values())
        if not has_player and previous_player_color is not None and previous_player is not None:
            fallback = self._object_roles.get(previous_player_color, previous_player)
            if fallback.role not in {RoleType.WALL, RoleType.GOAL, RoleType.EXIT}:
                fallback.role = RoleType.PLAYER
                fallback.confidence = max(float(fallback.confidence or 0.0), 0.46)
                fallback.evidence_steps = sorted(set((fallback.evidence_steps or []) + [step]))
                self._set_role(previous_player_color, fallback)
                notes.append(
                    f"step {step}: preserved prior player at color_{previous_player_color}; no stronger replacement found"
                )
        return notes

    def _demote_extra_primaries(self, role_type: RoleType, step: int, new_roles: Optional[Dict[int, "ObjectRole"]] = None) -> List[str]:
        notes: List[str] = []
        candidates = [
            (color_id, role)
            for color_id, role in self._object_roles.items()
            if role.role == role_type
        ]
        if len(candidates) <= 1:
            return notes

        # B148: Prefer historically best primary role (stability over opportunistic refresh)
        primary_color, primary_role = max(
            candidates,
            key=lambda item: (
                float(item[1].confidence) >= 0.7, # Priority 1: previously grounded
                float(item[1].confidence),        # Priority 2: highest current confidence
                len(item[1].evidence_steps or []), # Priority 3: most evidence
                -int(item[0]),
            ),
        )
        for color_id, role in candidates:
            if color_id == primary_color:
                continue
            # B168: If the ObjectRoleMapper identified this color as INTERMEDIATE
            # this step, restore it instead of demoting to DECORATION.
            mapper_role = new_roles.get(color_id) if new_roles else None
            if mapper_role and mapper_role.role == RoleType.INTERMEDIATE:
                role.role = RoleType.INTERMEDIATE
                role.confidence = mapper_role.confidence
                if mapper_role.estimated_position:
                    role.estimated_position = mapper_role.estimated_position
                self._set_role(color_id, role)
                notes.append(
                    f"step {step}: restored intermediate at color_{color_id} (was misclassified as {role_type.value})"
                )
            else:
                role.role = RoleType.DECORATION
                self._set_role(color_id, role)
                notes.append(
                    f"step {step}: demoted stale {role_type.value} at color_{color_id}; primary remains color_{primary_color}"
                )
            role.evidence_steps = sorted(set((role.evidence_steps or []) + [step]))
        if primary_role.role != role_type:
            primary_role.role = role_type
            self._set_role(primary_color, primary_role)
        return notes

    async def _register_solve_plan(self, observation: Dict[str, Any], step: int = 0) -> None:
        """Register top-level solve plan. B137: Suppresses re-registration of identical plans.

        B138: Emits trace events for solve-internal brain I/O.
        """
        goal = f"Solve ARC task {observation.get('dataset_id', '')}:{observation.get('task_id', '')}"
        steps = [
            "Infer archetype from board dynamics",
            "Map object roles from transition evidence",
            "Hypothesize victory condition",
            "Execute and revise chunked solve path",
        ]

        # B137: Check if plan has changed before registering
        if not self._plan_changed(
            plan_type="top",
            goal=goal,
            steps=steps,
        ):
            logger.debug("Skipping solve plan registration (identical to last)")
            # Emit trace event for audit trail
            try:
                await self.brain.trace_event(
                    event_type="plan_registration_skipped",
                    metadata={
                        "plan_type": "top",
                        "reason": "identical_to_last_registered",
                        "goal": goal,
                    },
                )
            except Exception:
                pass  # Don't fail the solve step if tracing fails
            return

        try:
            # B138: Trace register_plan call
            self._trace("solve_register_plan", "register_plan", {
                "step": step,
                "plan_type": "top",
                "goal": goal,
                "steps_count": len(steps),
            })
            _t0 = time.perf_counter()
            plan_payload = await self.brain.register_plan(
                goal=goal,
                steps=steps,
                session_id=self.session_id,
            )
            _elapsed = (time.perf_counter() - _t0) * 1000
            self._solve_plan_id = plan_payload.get("plan_id")
            # B137: Cache the registered plan
            self._last_registered_top_plan = {
                "goal": goal,
                "steps": steps,
            }
            self._trace("solve_register_plan_done", "register_plan", {"step": step, "plan_type": "top"}, {"plan_id": self._solve_plan_id}, _elapsed)
            logger.info("Solve plan registered: %s", self._solve_plan_id)
        except Exception as exc:
            logger.warning("register_plan failed for solve plan: %s", exc)
