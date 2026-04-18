"""ARC-AGI-3 orchestrator wrapping the local LLM with SideQuests intelligence."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from benchmarks.arc3.adapter import BrainClientProtocol, LedgerBrainClient
from sidequest_mcp_client.observability import (
    REQUIRED_DECISION_FIELDS,
    build_observability,
    ensure_contract_fields,
)
from benchmarks.arc3.schema import ARC3Action, ARC3Observation
from benchmarks.arc3.state_serializer import StateSerializerForARC
from agents.arc3.hypothesis import HypothesisManager
from agents.arc3.solver import SolveEngine, GameRuleHypothesis, PatternMatchTracker, ObjectRole, RoleType
from agents.arc3.cost_tracker import CostTracker
from agents.arc3.circuit_breaker import CircuitBreakerLLMClient
from agents.arc3.supervisor import PuzzleSupervisor, SupervisorDecision, SupervisorVerdict
from agents.arc3.grid_analysis import grid_characteristic_summary

from agents.arc3.repl_verification import LevelReplayVerifier, RuleRefinementLoop
from agents.arc3.prompts import (
    SYSTEM_PROMPT,
    INSTRUCTION_TEMPLATE,
    SANDBOX_INSTRUCTION,
    REPL_SANDBOX_INSTRUCTION,
    SANDBOX_SYSTEM_MESSAGE,
    QUERY_LLM_SYSTEM_MESSAGE,
    VERIFIER_SYSTEM_PROMPT,
    VERIFIER_PROMPT_TEMPLATE,
    ARC_PATTERN_SYSTEM_PROMPT,
    ARC_PATTERN_INSTRUCTION_TEMPLATE,
    ARC_EXECUTION_SYSTEM_PROMPT,
    ARC_EXECUTION_INSTRUCTION_TEMPLATE,
    ARC_ACTION_INSTRUCTION_TEMPLATE,
)

logger = logging.getLogger(__name__)


@dataclass
class ContentBlock:
    """B117: A structured block of prompt content."""
    type: str
    content: str
    header: Optional[str] = None


@dataclass
class PromptPacket:
    """B117: A typed collection of content blocks for the LLM prompt."""
    blocks: List[ContentBlock] = field(default_factory=list)

    def get_block(self, block_type: str) -> Optional[ContentBlock]:
        return next((b for b in self.blocks if b.type == block_type), None)

    def render(self) -> str:
        """Render the packet into a final prompt string."""
        ordered_keys = [
            "SYSTEM", "TRAINING_EXAMPLES", "SOLVED_LEVELS", "PRIOR_INSIGHTS",
            "GRID_ANALYSIS", "REPL_RESULTS", 
            "STATE", "ENTITY_CONTEXT", "MEMORY", "SOLVE_CONTEXT", "NAVIGATION", "PLAN",
            "ACTION_FACTS", "EXPLORATION_SUMMARY", "PATH_HYPOTHESES", "HYPOTHESIS",
            "PATTERN_HYPOTHESIS", "GRID", "TEST_INPUT",
            "OBSERVED_EFFECTS", "REFLEX", "HISTORY", "OBSERVATION",
            "INSTRUCTION", "ACTION_INVOCATION"
        ]
        
        # Mapping of block type to its standard header
        headers = {
            "ENTITY_CONTEXT": "ENTITY CONTEXT",
            "MEMORY": "MEMORY",
            "SOLVE_CONTEXT": "SOLVE CONTEXT",
            "NAVIGATION": "NAVIGATION GUIDANCE",
            "PLAN": "PLAN",
            "ACTION_FACTS": "ACTION FACTS",
            "EXPLORATION_SUMMARY": "EXPLORATION SUMMARY",
            "PATH_HYPOTHESES": "PATH HYPOTHESES",
            "HYPOTHESIS": "HYPOTHESIS",
            "PATTERN_HYPOTHESIS": "PATTERN HYPOTHESIS",
            "OBSERVED_EFFECTS": "OBSERVED EFFECTS",
            "REFLEX": "REFLEX",
            "HISTORY": "HISTORY",
            "OBSERVATION": "OBSERVATION",
        }

        block_map = {b.type: b for b in self.blocks}
        final_parts = []
        for key in ordered_keys:
            if key in block_map:
                block = block_map[key]
                if not block.content.strip():
                    continue
                
                # B117: Some blocks render with headers, others with colons
                if key in {"SYSTEM", "STATE", "INSTRUCTION", "ACTION_INVOCATION"}:
                    final_parts.append(f"{key}: {block.content}")
                else:
                    header = block.header or headers.get(key)
                    if header:
                        final_parts.append(f"=== {header} ===\n{block.content}")
                    else:
                        final_parts.append(block.content)

        return "\n\n".join(final_parts)


class ARCOrchestrator:
    """Perceive → plan → act → evaluate loop powered by SideQuests."""

    MAX_PROMPT_LESSONS = 1
    MAX_PROMPT_MEMORIES = 1
    MAX_PROMPT_ANALOGIES = 1
    MAX_PROMPT_HISTORY = 2
    MAX_PROMPT_PLAN_STEPS = 2
    MAX_PROMPT_HYPOTHESES = 1
    MAX_PROMPT_ACTIONS = 4
    ACTION_FATIGUE_THRESHOLD = 3  # B149
    MAX_FORCED_EXPLORATION_STEPS = 3  # B154

    def __init__(
        self,
        brain_client: BrainClientProtocol,
        llm_client: Any,
        session_id: str,
        serializer: StateSerializerForARC,
        config: dict,
        cost_tracker: Optional[CostTracker] = None,
        phase_controller: object | None = None,
    ):
        self.brain = brain_client
        llm_cfg = config.get("llm", {}) if isinstance(config, dict) else {}
        llm_chat = getattr(llm_client, "chat", None)
        is_mock_like_llm = bool(
            llm_chat is not None
            and (hasattr(llm_chat, "return_value") or hasattr(llm_chat, "side_effect"))
        )
        if llm_client and not isinstance(llm_client, CircuitBreakerLLMClient) and not is_mock_like_llm:
            self.llm = CircuitBreakerLLMClient(
                llm_client,
                failure_threshold=llm_cfg.get("circuit_breaker_failure_threshold", 3),
                cooldown_seconds=llm_cfg.get("circuit_breaker_cooldown_seconds", 30.0),
                max_retries=llm_cfg.get("circuit_breaker_max_retries", 3),
                emit_trace_event=self._emit_trace_event,
            )
        else:
            self.llm = llm_client
        self.session_id = session_id
        self.serializer = serializer
        self.config = config
        self._observability = build_observability(config if isinstance(config, dict) else {})
        self.cost_tracker = cost_tracker
        self._plan_id: str | None = None
        self._last_registered_fingerprint: Optional[tuple] = None
        self._last_registered_payload: Optional[dict] = None
        self._last_observation_fingerprint: Optional[tuple] = None
        self._last_llm_action: Optional[dict] = None
        self._reflex_context: dict | None = None
        self._plan_steps: List[str] = []
        self._step_history: List[dict] = []
        self._write_trace: List[dict] = []
        self._write_trace_context: str = "bootstrap"
        self.hypothesis_mgr = HypothesisManager(brain_client, session_id)
        self._hypothesis_context: dict | None = None
        # B138: Pass trace callback to expose solve-internal brain I/O in agent_trace
        self.solve_engine = SolveEngine(
            brain_client,
            self.llm,
            session_id,
            emit_trace_event=self._emit_trace_event,
            cost_tracker=self.cost_tracker,
            loaded_procedures=(config.get("loaded_procedures") if isinstance(config, dict) else None),
        )
        self._solve_context: dict | None = None
        # B131: Comprehensive execution trace for CloudWatch-style logging
        self._execution_trace: List[dict] = []
        # B199: Allow external guidance to scale exploration budget
        try:
            self._exploration_budget_multiplier = float((config.get("exploration_budget_multiplier") if isinstance(config, dict) else 1.0) or 1.0)
        except Exception:
            self._exploration_budget_multiplier = 1.0
        if self._exploration_budget_multiplier != 1.0:
            try:
                self._emit_trace_event("operation", "gap_aware_budget", {"provided_multiplier": self._exploration_budget_multiplier}, {"status": "configured"})
            except Exception:
                pass
        self._trace_start_time = time.time()
        # B89: Prompt budget metrics
        self._invalid_action_count = 0
        self._action_frame_hashes: Dict[str, str] = {}
        self._no_progress_step_count = 0
        self._prompt_tokens_per_step: List[int] = []
        self._retrieval_payloads: List[dict] = []
        self._first_prompt_detail_level = "unknown"
        self._asked_for_decision_from_effects = False
        # B90: Retrieval triggering
        self._retrieval_triggered = False
        self._last_retrieval_step = -1
        self._last_retrieval_kind_fingerprint: Dict[str, tuple[str, int]] = {}  # kind -> (query_hash, step)
        self._consecutive_no_progress_steps = 0
        self._blocked_actions: set[str] = set()
        self._action_fatigue: dict[str, int] = {}  # B149: action_id -> consecutive zero-reward count
        self._forced_exploration_count = 0  # B154
        self._total_forced_exploration = 0  # B154
        self._last_seen_invalid_action_count = 0
        self._force_replan = False # B177
        self._memory_context: dict | None = None
        self._pruning_decisions: List[dict] = []
        self._entity_gate_result: Dict[str, Any] = {}
        self._compaction_artifact: Any | None = None
        self._guard_escalations: List[dict] = []
        self._recent_frame_hashes: List[str] = []  # B135: Track last N frame hashes for loop detection
        self._exploitation_switch_budget = 2  # B144: Once plateau hits, limit switching families
        # B150: Grid analysis for training examples
        self._transformation_signature: Optional[Any] = None
        self._training_diffs: List[Any] = []
        self._frame_deltas: List[Any] = []
        self._solved_level_diffs: List[Any] = []
        self._level_pattern: Optional[Any] = None
        self._last_grid: Optional[List[List[int]]] = None
        # B152: REPL-driven solving
        self._verified_output_grid = None
        self._phase2_mode = "fallback"  # "execution" | "fallback"
        # B157: Multi-level tracking
        self._solved_levels: List[dict] = []
        self._level_start_grid: Optional[List[List[int]]] = None
        self._level_action_buffer: List[str] = []
        self._current_level: int = 0
        self._rule_confidence: float = 0.0
        self.observed_action_effects: dict[str, dict] = {}
        self._available_actions: List[str] = []
        # B156: Progressive knowledge (persists across levels)
        # B173: Removed local _game_rule_hypothesis; using solve_engine._game_rule_hypotheses[0]
        self._action_semantics: Dict[str, str] = {}
        # B161: Spatial tracking and goal-directed navigation
        self._player_position: tuple[float, float] | None = None
        self._goal_position: tuple[float, float] | None = None
        self._last_interact_effect: dict | None = None
        # B162/B165: Bootstrap structural analysis for step-0 reasoning and recall keys
        self._bootstrap_grid_summary: dict | None = None
        # B164: Compact prompt mode for smaller local models
        self._compact_mode: bool = self._is_compact_model()
        # B167: Pattern match tracking and intermediate targets
        self._pattern_tracker = PatternMatchTracker()
        self._visited_intermediates: set[tuple[int, int]] = set()
        # B169: KuzuDB role source of truth
        self._entity_graph: Optional["EntityGraphBuilder"] = None
        # B175: Autopilot wall detection and rerouting
        self._blocked_axes: Dict[str, int] = {}  # {"row": step_blocked, "col": step_blocked}
        self._last_autopilot_player_pos: Optional[tuple[float, float]] = None
        # B178: Action semantics discovery
        self._action_direction_map: Optional[Dict[str, tuple[float, float]]] = None
        # B183: Meta-Supervisor
        self._supervisor = PuzzleSupervisor(llm_client=llm_client)
        self._supervisor_nudge: Optional[str] = None
        self._should_abandon: bool = False
        # B198: proactive warnings pushed from SideQuests notify_turn
        self._proactive_warnings: List[dict] = []
        # Optional PhaseController (owned by DurableARCRunner; read-only here)
        self._phase_controller = phase_controller

    @property
    def _game_rule_hypothesis(self) -> Any | None:
        """B173: Single source of truth for current game rule."""
        if hasattr(self, 'solve_engine') and self.solve_engine._game_rule_hypotheses:
            return self.solve_engine._game_rule_hypotheses[0]
        return None

    async def _on_level_transition(self, completed_level: int, solved_levels: List[dict]):
        """B157: Called when a level is won. Prepare for next level."""
        self._current_level = completed_level + 1

        # B167: Save puzzle model before clearing state
        await self._save_puzzle_model("solved")
        self._visited_intermediates.clear()
        # B178: Reset action map for new level
        self._action_direction_map = None

        # B150: Analyze the solved level
        if solved_levels:
            self._analyze_level_transition(solved_levels[-1])
            
            # B156: Run the full knowledge pipeline (B151 + B152)
            await self._run_knowledge_pipeline(solved_levels)

        # Emit trace for level completion
        self._emit_trace_event("operation", "level_complete", {
            "level": completed_level,
            "total_levels_solved": len(solved_levels),
            "actions_used": solved_levels[-1]["steps"] if solved_levels else 0,
        })

        # Partial reset: keep learned knowledge, clear per-level state
        self._consecutive_no_progress_steps = 0
        self._forced_exploration_count = 0
        if hasattr(self, '_action_fatigue'):
            self._action_fatigue.clear()
        self._recent_frame_hashes = []
        self._last_grid = None # B150: Reset per-step grid tracking
        # Clear Phase 1 results so we re-analyze for the next level if needed
        # (Though usually the rule stays the same, the application might change)
        self._verified_output_grid = None
        # B156: _phase2_mode is now set by _run_knowledge_pipeline based on confidence

    def _update_player_position(self, observation: dict):
        """B161: Track player centroid after each step."""
        grid = observation.get("grid")
        if not grid:
            return

        # Use solver's identified player color
        player_color = None
        for color_id, role in self.solve_engine._object_roles.items():
            if role.role == RoleType.PLAYER:
                player_color = int(color_id)
                break

        if player_color is None:
            return

        # Compute centroid of player color
        rows, cols, count = 0.0, 0.0, 0
        for r, row in enumerate(grid):
            for c, val in enumerate(row):
                if val == player_color:
                    rows += r
                    cols += c
                    count += 1

        if count > 0:
            self._player_position = (rows / count, cols / count)

    def _update_goal_position(self):
        """B161: Extract goal position from solve context."""
        for color_id, role in self.solve_engine._object_roles.items():
            if role.role in (RoleType.GOAL, RoleType.EXIT) and role.estimated_position:
                pos = role.estimated_position
                self._goal_position = (pos["row"], pos["col"])
                return

    def _pick_action_for_direction(self, dr: float, dc: float, available_actions: List[str]) -> Optional[str]:
        """B178: Pick the action that best moves the player in the (dr, dc) direction."""
        if self._action_direction_map:
            best_action = None
            best_dot = -float('inf')
            for aid, (a_dr, a_dc) in self._action_direction_map.items():
                if aid not in available_actions:
                    continue
                # Dot product: how aligned is this action with desired direction?
                dot = dr * a_dr + dc * a_dc
                if dot > best_dot:
                    best_dot = dot
                    best_action = aid
            
            # If we found an empirical match with positive alignment, use it
            if best_action and best_dot > 0.1:
                return best_action

        # Fallback to convention if no empirical data or it didn't yield a good match
        if abs(dr) >= abs(dc):
            return "ACTION1" if dr < 0 else "ACTION2"
        else:
            return "ACTION3" if dc < 0 else "ACTION4"

    def _nearest_unvisited_intermediate(self, player_info: dict, intermediates: List[dict]) -> Optional[dict]:
        """B167: Find the closest intermediate object the player hasn't visited yet."""
        unvisited = [
            i for i in intermediates
            if (round(i["estimated_position"]["row"]), round(i["estimated_position"]["col"]))
               not in self._visited_intermediates
        ]
        
        if not unvisited:
            # All visited — return None to fall back or pick nearest anyway
            # For now, if all visited, we just return the nearest one again
            # as some puzzles might need multiple visits
            unvisited = intermediates

        if not unvisited:
            return None

        # Sort by Manhattan distance to player
        def dist(i):
            pos = i["estimated_position"]
            return abs(pos["row"] - player_info["row"]) + abs(pos["col"] - player_info["col"])

        return min(unvisited, key=dist)

    def _try_autopilot(self, observation: dict, available_actions: List[str]) -> Optional[ARC3Action]:
        """B166: Deterministic navigation when player/goal positions are known.
        B167: Extended with phase-awareness and intermediate targets.
        """
        sc = self._solve_context or {}
        roles = sc.get("object_roles") or {}
        if not roles:
            roles = self._entity_map

        grid = observation.get("grid") or []
        step = len(self._step_history)


        # B167: Update pattern tracker
        pattern_state = self._pattern_tracker.update(grid, step)
        self._emit_trace_event("operation", "pattern_match_progress", {
            "step": step,
            "phase": pattern_state["phase"],
            "similarity": pattern_state["similarity"],
            "trend": pattern_state.get("similarity_trend", "unknown"),
        })

        player_info = None
        goal_info = None
        for color_id, role_data in roles.items():
            if role_data.get("role") == "player" and role_data.get("confidence", 0) >= 0.7:
                pos = role_data.get("estimated_position")
                if pos and pos.get("row") is not None and pos.get("col") is not None:
                    player_info = {"color": color_id, "row": pos["row"], "col": pos["col"], "conf": role_data["confidence"]}
            elif role_data.get("role") == "goal" and role_data.get("confidence", 0) >= 0.7:
                pos = role_data.get("estimated_position")
                if pos and pos.get("row") is not None and pos.get("col") is not None:
                    goal_info = {"color": color_id, "row": pos["row"], "col": pos["col"], "conf": role_data["confidence"]}

        if not player_info:
            return None

        # Target selection based on phase
        target = None
        rationale_prefix = "autopilot"

        if pattern_state["phase"] == "finish" and goal_info:
            target = goal_info
            rationale_prefix = "autopilot[finish]: goal matches reference"
        elif pattern_state["phase"] == "intermediate":
            # Find nearest intermediate object
            intermediates = [
                r for r in roles.values()
                if r.get("role") == "intermediate" and r.get("estimated_position")
            ]
            if intermediates:
                target = self._nearest_unvisited_intermediate(player_info, intermediates)
                if target:
                    pos = target["estimated_position"]
                    target = {"row": pos["row"], "col": pos["col"]}
                    rationale_prefix = f"autopilot[intermediate]: visiting interactive object at {round(pos['row'])},{round(pos['col'])}"
            
            # If no intermediate found or all visited, fall back to goal if known
            if not target and goal_info:
                target = goal_info
                rationale_prefix = "autopilot[intermediate]: no intermediates, driving to goal"
        
        if not target:
            # Fall back to original goal-only logic if phase-aware targeting failed
            if goal_info:
                target = goal_info
            else:
                return None

        # B168: Disengage if autopilot is not making progress (zero pixel changes).
        # B175: Primary check is centroid delta, but keep this as robust fallback.
        recent_zero_px = sum(
            1
            for s in self._step_history[-2:]
            if s.get("decision_source") == "autopilot" and (s.get("frame_delta", {}).get("n_cells_changed", -1) == 0)
        )
        if recent_zero_px >= 2:
            self._emit_trace_event("operation", "autopilot_disengage", {"reason": "wall_collision", "consecutive_zero_px": recent_zero_px})
            return None

        # B175: Improved wall detection using player centroid delta.
        # Check if player actually moved since last autopilot step.
        if self._last_autopilot_player_pos is not None:
            last_row, last_col = self._last_autopilot_player_pos
            row_delta = abs(player_info["row"] - last_row)
            col_delta = abs(player_info["col"] - last_col)

            # Find the last autopilot step to see which axis we tried to move on
            last_autopilot = next(
                (s for s in reversed(self._step_history) if s.get("decision_source") == "autopilot"),
                None
            )
            if last_autopilot:
                last_aid = last_autopilot.get("action_id")
                # ACTION1 (up), ACTION2 (down) -> row axis
                # ACTION3 (left), ACTION4 (right) -> col axis
                was_row_move = last_aid in ("ACTION1", "ACTION2")
                target_axis_delta = row_delta if was_row_move else col_delta

                if target_axis_delta < 0.5:
                    # Player didn't move on the target axis — wall detected
                    blocked_axis = "row" if was_row_move else "col"
                    self._blocked_axes[blocked_axis] = step
                    self._emit_trace_event("operation", "autopilot_wall_detected", {
                        "axis": blocked_axis,
                        "player_delta": {"row": row_delta, "col": col_delta},
                        "step": step,
                    })

        dr = target["row"] - player_info["row"]
        dc = target["col"] - player_info["col"]

        # B175: Axis rotation when preferred axis is blocked
        # Consider an axis blocked if it was marked blocked in the last 10 steps
        row_blocked = "row" in self._blocked_axes and (step - self._blocked_axes["row"]) < 10
        col_blocked = "col" in self._blocked_axes and (step - self._blocked_axes["col"]) < 10

        # B168: Detect oscillation — if player has been bouncing between same
        # positions in recent autopilot steps, try interact instead of moving
        player_pos_key = (round(player_info["row"]), round(player_info["col"]))
        recent_positions = [
            (round(s.get("autopilot_player_row", -999)), round(s.get("autopilot_player_col", -999)))
            for s in self._step_history[-4:]
            if s.get("decision_source") == "autopilot"
        ]
        oscillating = recent_positions.count(player_pos_key) >= 2 and len(recent_positions) >= 3

        # If already at target (within 1 cell) OR oscillating near target, try interact
        near_target = abs(dr) <= 1.0 and abs(dc) <= 1.0
        close_enough = abs(dr) <= 3.0 and abs(dc) <= 3.0
        if near_target or (oscillating and close_enough):
            if "ACTION5" in available_actions:
                action_id = "ACTION5"
                reason = "arrived" if near_target else "oscillation detected, trying interact"
                rationale = f"{rationale_prefix}, {reason}"

                # Mark as visited if it's an intermediate
                if pattern_state["phase"] == "intermediate":
                    self._visited_intermediates.add((round(target["row"]), round(target["col"])))
            else:
                return None
        elif oscillating:
            # B168: Oscillating but not close enough to interact — try the other axis
            # to break out of the bounce pattern
            if abs(dr) >= abs(dc):
                # Was bouncing on row axis; try column axis instead
                action_id = self._pick_action_for_direction(0, dc if dc != 0 else 1, available_actions)
            else:
                # Was bouncing on column axis; try row axis instead
                action_id = self._pick_action_for_direction(dr if dr != 0 else 1, 0, available_actions)
            rationale = f"{rationale_prefix}: oscillation detected, switching axis"
        else:
            # B175: Choose axis with larger delta, considering blocks
            # B178: Use discovered action semantics
            if abs(dr) >= abs(dc):
                if row_blocked and abs(dc) >= 1.0 and not col_blocked:
                    # Primary axis (row) blocked, rotate to column axis
                    action_id = self._pick_action_for_direction(0, dc, available_actions)
                    rationale = f"{rationale_prefix}: row blocked, rotating to col axis"
                elif row_blocked:
                    # Row axis blocked and (no column delta to try OR column also blocked)
                    self._emit_trace_event("operation", "autopilot_disengage", {"reason": "row_axis_blocked_no_alt"})
                    return None
                else:
                    action_id = self._pick_action_for_direction(dr, 0, available_actions)
                    rationale = f"{rationale_prefix}: target is {abs(dr):.1f} rows {'above' if dr < 0 else 'below'}, using discovered mapping"
            else:
                if col_blocked and abs(dr) >= 1.0 and not row_blocked:
                    # Primary axis (col) blocked, rotate to row axis
                    action_id = self._pick_action_for_direction(dr, 0, available_actions)
                    rationale = f"{rationale_prefix}: col blocked, rotating to row axis"
                elif col_blocked:
                    # Col axis blocked and (no row delta to try OR row also blocked)
                    self._emit_trace_event("operation", "autopilot_disengage", {"reason": "col_axis_blocked_no_alt"})
                    return None
                else:
                    action_id = self._pick_action_for_direction(0, dc, available_actions)
                    rationale = f"{rationale_prefix}: target is {abs(dc):.1f} cols {'left' if dc < 0 else 'right'}, using discovered mapping"

        if action_id not in available_actions:
            return None

        # B177: Make tier 2 blocks checked by autopilot
        if action_id in self._blocked_actions:
            # Try orthogonal axis
            if action_id in ("ACTION1", "ACTION2"):
                # Was row axis, try col axis if there is a delta
                if abs(dc) >= 1.0:
                    alt = "ACTION3" if dc < 0 else "ACTION4"
                else:
                    alt = None
            else:
                # Was col axis, try row axis if there is a delta
                if abs(dr) >= 1.0:
                    alt = "ACTION1" if dr < 0 else "ACTION2"
                else:
                    alt = None
            
            if alt and alt not in self._blocked_actions and alt in available_actions:
                action_id = alt
                rationale = f"{rationale_prefix}: primary blocked, using alternative"
            else:
                self._emit_trace_event("operation", "autopilot_disengage", {"reason": "action_blocked", "action": action_id})
                return None

        # B213: Prevent autopilot spatial lock under sustained no-progress.
        try:
            candidate_target = (round(target["row"]), round(target["col"]))
            last_target = getattr(self, "_last_autopilot_target", None)
            if self._consecutive_no_progress_steps >= 2 and last_target == candidate_target:
                self._emit_trace_event("operation", "autopilot_confidence_drop", {"target": candidate_target}, {"reason": "no_progress_spatial_lock"})
                return None
            self._last_autopilot_target = candidate_target
        except Exception:
            pass

        # B175: Save player position for next wall check
        self._last_autopilot_player_pos = (player_info["row"], player_info["col"])

        self._emit_trace_event("operation", "autopilot_engage", {
            "player": {"row": player_info["row"], "col": player_info["col"]},
            "target": {"row": target["row"], "col": target["col"]},
            "phase": pattern_state["phase"],
            "chosen_action": action_id,
        })

        return {
            "action_id": action_id,
            "rationale": rationale,
            "decision_source": "autopilot",
            "autopilot_player_row": player_info["row"],
            "autopilot_player_col": player_info["col"],
        }

    def _build_puzzle_model(self) -> dict:
        """B167: Build a structured puzzle model from what the agent learned this level."""
        model = {
            "type": "puzzle_model",
            "game_id": getattr(self, "_game_id", "unknown"),
            "level": self._current_level,
            "grid_structure": {
                "reference_location": None,
                "goal_location": None,
                "intermediate_count": 0,
            },
            "mechanic": {
                "description": "",
                "interact_required": True,
            },
            "learned_facts": [],
            "pattern_similarity_at_start": 0.0,
            "pattern_similarity_at_end": 0.0,
            "outcome": "unknown",
        }

        if self._pattern_tracker:
            if self._pattern_tracker.reference_region:
                model["grid_structure"]["reference_location"] = self._pattern_tracker.reference_region.location_hint
            if self._pattern_tracker.goal_region:
                model["grid_structure"]["goal_location"] = self._pattern_tracker.goal_region.location_hint
            if self._pattern_tracker.similarity_history:
                model["pattern_similarity_at_start"] = self._pattern_tracker.similarity_history[0]
                model["pattern_similarity_at_end"] = self._pattern_tracker.similarity_history[-1]

        intermediates = [r for r in self.solve_engine._object_roles.values() if r.role == RoleType.INTERMEDIATE]
        model["grid_structure"]["intermediate_count"] = len(intermediates)

        visited = len(self._visited_intermediates)
        if visited > 0 or len(intermediates) > 0:
            model["mechanic"]["description"] = (
                f"Navigate to {len(intermediates)} intermediate markers and interact (ACTION5). "
                f"Each visit transforms the goal pattern. When goal matches reference, "
                f"interact with goal to complete level."
            )

        # Add learned facts from step history
        for step in self._step_history:
            delta = step.get("frame_delta", {})
            if step.get("action_id") == "ACTION5" and delta.get("n_cells_changed", 0) > 5:
                model["learned_facts"].append({
                    "fact": f"ACTION5 at step {step['step']} caused {delta['n_cells_changed']} pixel change",
                    "interpretation": "interact triggered a state change",
                })

        return model

    async def _save_puzzle_model(self, outcome: str):
        """B167: Persist puzzle understanding to SideQuests for cross-level recall."""
        model = self._build_puzzle_model()
        model["outcome"] = outcome

        description = (
            f"Level {self._current_level} puzzle model: "
            f"reference at {model['grid_structure']['reference_location']}, "
            f"{model['grid_structure']['intermediate_count']} intermediates, "
            f"mechanic: {model['mechanic']['description']}"
        )

        # Save as a structured lesson via report_outcome
        try:
            await self.brain.report_outcome(
                plan_id=None,
                session_id=self.session_id,
                outcome_text=description,
                valence=1.0 if outcome == "solved" else -0.3,
                evidence=model,
            )

            # Also save as a notify_turn so it appears in the conversation history
            await self.brain.notify_turn(
                role="assistant",
                content=f"[PUZZLE MODEL] {description}",
                session_id=self.session_id,
            )

            self._emit_trace_event("operation", "puzzle_model_saved", {
                "level": self._current_level,
                "outcome": outcome,
                "intermediate_count": model["grid_structure"]["intermediate_count"],
                "similarity_start": model["pattern_similarity_at_start"],
                "similarity_end": model["pattern_similarity_at_end"],
            })
        except Exception as exc:
            logger.warning("B167: _save_puzzle_model failed: %s", exc)

    async def _recall_puzzle_model(self) -> Optional[List[dict]]:
        """B167: Recall saved puzzle understanding from earlier levels."""
        if self._current_level <= 1:
            return None

        try:
            results = await self.brain.current_truth(
                query="puzzle model reference pattern intermediate markers",
                session_id=self.session_id,
                scope="branch",
                limit=3,
            )

            if not results or not results.get("results"):
                return None

            memories = results["results"]
            self._emit_trace_event("operation", "puzzle_model_recalled", {
                "level": self._current_level,
                "results_count": len(memories),
            })

            # Apply recalled knowledge:
            # Skip discover phase — go straight to intermediate
            if self._pattern_tracker:
                self._pattern_tracker.phase = "intermediate"
                # If we have a previous model, we could seed reference/goal locations here
                # but for now letting the tracker find them in the new grid is safer.

            return memories
        except Exception as exc:
            logger.warning("B167: _recall_puzzle_model failed: %s", exc)
            return None

    async def merge_graph_roles(self, graph_roles: Dict[int, ObjectRole]):
        """B168: Accept graph-inferred roles from exploration agent.
        Higher confidence wins when merging with existing heuristic roles.
        B169: Single source of truth: KuzuDB."""
        if not graph_roles:
            return

        merged_count = 0
        for color_id, graph_role in graph_roles.items():
            existing = self.solve_engine._object_roles.get(color_id)
            if existing is None or graph_role.confidence > existing.confidence:
                self.solve_engine._set_role(color_id, graph_role)
                merged_count += 1

        # B169: Flush immediately after exploration phase
        await self.solve_engine._flush_role_writes()

        if merged_count > 0:
            self._emit_trace_event(
                "b168_roles_merged",
                "merge_graph_roles",
                details={
                    "merged_count": merged_count,
                    "roles": {
                        str(k): {"role": v.role.value, "confidence": v.confidence}
                        for k, v in graph_roles.items()
                    },
                },
            )

    def _build_movement_summary(self) -> str:
        """B161: Summarize which directions worked and which hit walls."""
        action_names = {
            "ACTION1": "up", 
            "ACTION2": "down", 
            "ACTION3": "left", 
            "ACTION4": "right", 
            "ACTION5": "interact"
        }
        lines = []
        # Last 5 steps
        for step in self._step_history[-5:]:
            aid = step.get("action_id", "?")
            fa = step.get("frame_delta", {})
            px = fa.get("n_cells_changed", 0)
            name = action_names.get(aid, aid)
            if px == 0:
                lines.append(f"{name}: blocked (wall/no-op)")
            else:
                lines.append(f"{name}: moved ({px} pixels changed)")
        return "\n".join(lines)

    def _analyze_level_transition(self, solved_level: dict):
        """B150: Deterministic analysis of a completed level."""
        from agents.arc3.grid_analysis import GridDiffEngine
        diff_engine = GridDiffEngine()
        
        try:
            level_diff = diff_engine.diff_grids(solved_level["start_grid"], solved_level["end_grid"])
            self._solved_level_diffs.append(level_diff)

            # Cross-level consensus across all solved levels
            if len(self._solved_level_diffs) >= 1:
                self._level_pattern = diff_engine.cross_level_consensus(self._solved_level_diffs)
                
                self._emit_trace_event("operation", "level_consensus", {
                    "n_levels": len(self._solved_level_diffs),
                    "summary": self._level_pattern.game_rule_summary,
                    "confidence": round(self._level_pattern.confidence, 3),
                })
        except Exception as exc:
            logger.warning("B150: _analyze_level_transition failed: %s", exc)

    async def _run_knowledge_pipeline(self, solved_levels: List[dict]):
        """B156: Full knowledge pipeline: Hypothesis (B151) -> Verification (B152) -> Mode Routing."""
        if not self._level_pattern:
            return

        from agents.arc3.solver import GameRuleHypothesizer
        from agents.arc3.repl_verification import LevelReplayVerifier, RuleRefinementLoop

        # Step 1: Generate game rule hypotheses (B151)
        hypothesizer = GameRuleHypothesizer()
        hypotheses = await hypothesizer.hypothesize(
            level_pattern=self._level_pattern,
            solved_levels=solved_levels,
            llm_client=self.llm,
        )
        
        if not hypotheses:
            self._emit_trace_event("operation", "pipeline_no_hypotheses", {})
            self._phase2_mode = "fallback"
            return

        # Step 2: Verify via REPL (B152)
        verifier = LevelReplayVerifier()
        loop = RuleRefinementLoop(self.llm, verifier)

        best_hypothesis = await loop.solve(
            hypotheses=hypotheses,
            solved_levels=solved_levels,
        )
        
        if not best_hypothesis:
            self._phase2_mode = "fallback"
            return

        self.solve_engine._set_game_rule_hypotheses([best_hypothesis])
        # B173: Persist best hypothesis into orchestrator-level attributes (B156)
        try:
            self._rule_confidence = float(getattr(best_hypothesis, "confidence", 0.0) or 0.0)
            self._action_semantics = dict(getattr(best_hypothesis, "action_semantics", {}) or {})
        except Exception:
            # Defensive: don't let hypothesis shape break the pipeline
            self._rule_confidence = 0.0
            self._action_semantics = {}
        
        # Step 3: Confidence-based mode routing (B156)
        # Confidence > 0.8: execution (we are sure)
        # Confidence 0.4-0.8: rule_application (we have a good guess)
        # Confidence < 0.4: fallback (navigation)
        
        if best_hypothesis.confidence >= 0.8:
            self._phase2_mode = "execution"
        elif best_hypothesis.confidence >= 0.4:
            self._phase2_mode = "rule_application"
        else:
            self._phase2_mode = "fallback"

        self._emit_trace_event("operation", "pipeline_complete", {
            "best_rule": best_hypothesis.rule_description,
            "confidence": round(best_hypothesis.confidence, 3),
            "selected_mode": self._phase2_mode
        })

    def _select_prompt_mode(self) -> str:
        """B156: Select prompt mode based on level and learned knowledge."""
        current_level = getattr(self, '_current_level', 0)
        n_solved = len(getattr(self, '_solved_levels', []))
        confidence = getattr(self, '_rule_confidence', 0.0)

        # High confidence + multiple levels verified → execution
        if confidence > 0.8 and n_solved >= 2:
            return "execution"

        # Some knowledge → show insights
        if n_solved >= 1 and confidence > 0.4:
            return "rule_application"

        # Level 1, no knowledge → explore
        if current_level <= 1 and n_solved == 0:
            return "exploration"

        # Default → existing navigation
        return "navigation"

    def record_guard_escalation(self, step: int, reason: str, status: str):
        """B130: Record a guard escalation event."""
        self._guard_escalations.append({
            "step": step,
            "reason": reason,
            "guard_state": status
        })

    def _mark_active_chunk_failed(self, reason: str):
        """B141: Mark active chunk as failed and clear it."""
        if self.solve_engine._active_chunk:
            self.solve_engine._mark_chunk_failed(self.solve_engine._active_chunk, reason)
            self.solve_engine._active_chunk = None

    def _emit_trace_event(self, event_type: str, operation: str, details: dict | None = None, result: dict | None = None, elapsed_ms: float | None = None):
        """B131: Emit a timestamped execution trace event (CloudWatch-style)."""
        import time
        timestamp_iso = datetime.datetime.fromtimestamp(time.time(), datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        event = {
            "timestamp_iso": timestamp_iso,
            "event_type": event_type,
            "operation": operation,
            "details": details or {},
            "result": result,
            "elapsed_ms": elapsed_ms,
        }
        self._execution_trace.append(event)
        
        # B218: Forward to observability
        try:
            if hasattr(self, "_observability") and self._observability:
                details_dict = details if isinstance(details, dict) else {}
                result_dict = result if isinstance(result, dict) else {}
                step_val = details_dict.get("step")
                if step_val is None and isinstance(result_dict, dict):
                    step_val = result_dict.get("step")
                phase_val = (
                    details_dict.get("phase")
                    or details_dict.get("to_phase")
                    or getattr(self, "current_phase", None)
                    or getattr(getattr(self, "brain", None), "current_phase", None)
                    or "unknown"
                )
                base_attrs = {
                    "session_id": self.session_id,
                    "task_id": getattr(self, "_task_id", "unknown"),
                    "event_type": event_type,
                    "operation": operation,
                    "phase": phase_val,
                    "step": step_val if step_val is not None else -1,
                    "agent.name": "arc_orchestrator",
                    "agent.role": "orchestrator",
                    "emitter.module": "agents.arc3.orchestrator",
                    "emitter.method": "_emit_trace_event",
                    "trace.contract.version": "v1",
                }
                if elapsed_ms is not None:
                    base_attrs["latency_ms"] = float(elapsed_ms)
                for k, v in details_dict.items():
                    base_attrs[f"details.{k}"] = v
                for k, v in result_dict.items():
                    base_attrs[f"result.{k}"] = v

                op_str = str(operation)
                if op_str.startswith(("brain.", "arc_api.", "agent.", "eval.", "monitor.")):
                    span_name = op_str
                elif event_type in {"phase_start", "phase_end", "phase_transition", "agent_phase_transition"}:
                    span_name = f"agent.phase.{op_str}"
                else:
                    span_name = f"agent.operation.{op_str}"
                with self._observability.span(span_name, base_attrs):
                    pass

                # Promote decision payloads into a dedicated child span for easier tree navigation.
                if op_str == "act" and event_type == "phase_end":
                    step_entry = (getattr(self, "_step_history", []) or [{}])[-1] or {}
                    decision_attrs = ensure_contract_fields({
                        "session_id": self.session_id,
                        "task_id": getattr(self, "_task_id", "unknown"),
                        "step": step_entry.get("step", step_val if step_val is not None else -1),
                        "phase": "act",
                        "prompt": step_entry.get("prompt"),
                        "input_observation": step_entry.get("board_before"),
                        "available_actions": step_entry.get("available_actions"),
                        "action_id": step_entry.get("action_id"),
                        "candidate_action_id": step_entry.get("candidate_action_id"),
                        "decision_source": step_entry.get("decision_source"),
                        "guard_status": step_entry.get("guard_status"),
                        "verifier_status": step_entry.get("verifier_status"),
                        "rationale": step_entry.get("rationale"),
                        "thinking_trace": step_entry.get("thinking_trace"),
                        "agent.name": "arc_orchestrator",
                        "agent.role": "orchestrator",
                        "emitter.module": "agents.arc3.orchestrator",
                        "emitter.method": "_emit_trace_event.act.phase_end",
                        "trace.contract.version": "v1",
                    }, REQUIRED_DECISION_FIELDS, strict=False, defaults={"action_id": "unknown"})
                    with self._observability.span("agent.policy.decision", decision_attrs):
                        pass

                self._observability.emit_structured_event(
                    event_type=event_type,
                    operation=operation,
                    details=details,
                    result=result,
                    elapsed_ms=elapsed_ms,
                )
        except Exception:
            logger.debug("Observability event emission failed", exc_info=True)

    def _handle_notify_turn_response(self, response: dict | None, step: int | None = None) -> None:
        """Parse `proactive_context` from notify_turn responses (B198).

        Stores into `self._proactive_warnings` and emits a trace event when present.
        """
        try:
            if not response or not isinstance(response, dict):
                return
            proactive_ctx = response.get("proactive_context")
            if not proactive_ctx:
                return

            if isinstance(proactive_ctx, dict):
                items = proactive_ctx.get("items") or []
            elif isinstance(proactive_ctx, list):
                items = proactive_ctx
            else:
                return

            normalized_items = [item for item in items if isinstance(item, dict)]
            if not normalized_items:
                return

            # Save latest proactive warnings
            self._proactive_warnings = normalized_items

            # Emit compact trace for downstream evaluation
            warnings_compact = [
                {
                    "lesson_id": w.get("lesson_id"),
                    "text": (w.get("text") or "")[:100],
                    "type": w.get("type"),
                    "domain": w.get("domain"),
                }
                for w in normalized_items
            ]
            self._emit_trace_event(
                "operation",
                "proactive_warning",
                {"step": step},
                {"warnings": warnings_compact},
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("B198: failed to parse proactive_context: %s", exc)

    @property
    def _entity_map(self) -> Dict[int, Dict[str, Any]]:
        """B120: Property proxy for B119 object roles."""
        return {
            k: {
                "role": v.role.value,
                "confidence": v.confidence,
                "position": v.estimated_position,
                "estimated_position": v.estimated_position
            }
            for k, v in self.solve_engine._object_roles.items()
        }

    def get_ledger(self) -> List[dict]:
        """Return the collected SideQuests call ledger."""
        if isinstance(self.brain, LedgerBrainClient):
            return list(self.brain.ledger)
        return []

    def analyze_ledger_and_prune(self) -> List[dict]:
        """B118: Analyze ledger for high-latency/low-value patterns and prune."""
        ledger = self.get_ledger()
        if not ledger:
            return []

        decisions = []
        # Group by call_type to find slow offenders
        stats = {}
        for entry in ledger:
            ctype = entry["call_type"]
            if ctype not in stats:
                stats[ctype] = {"count": 0, "total_latency": 0.0, "low_value_count": 0}
            stats[ctype]["count"] += 1
            stats[ctype]["total_latency"] += entry.get("latency_ms", 0)
            
            # Rough proxy for "low value" in ARC:
            # - retrieval that found 0 items
            # - notify that returned just "ok" but didn't trigger a meaningful hypothesis
            res = str(entry.get("result_summary", "")).lower()
            if "found 0" in res or "found []" in res:
                stats[ctype]["low_value_count"] += 1

        for ctype, data in stats.items():
            avg_latency = data["total_latency"] / data["count"]
            low_value_ratio = data["low_value_count"] / data["count"]
            
            # Pruning criteria: avg > 500ms and > 50% are low value
            if avg_latency > 500 and low_value_ratio > 0.5:
                decision = {
                    "call_type": ctype,
                    "reason": f"high latency ({avg_latency:.1f}ms) with low value ratio ({low_value_ratio:.1%})",
                    "action": "deprioritize",
                }
                decisions.append(decision)
                # Avoid duplicate decisions
                if not any(d["call_type"] == ctype for d in self._pruning_decisions):
                    self._pruning_decisions.append(decision)

        return decisions

    async def _bootstrap_entity_discovery(self, observation: ARC3Observation) -> None:
        """B119: Extract bootstrap entity discovery logic.
        B169: Authority is KuzuDB."""
        bootstrap_roles = self.solve_engine.role_mapper.seed_bootstrap_roles(observation)
        discovered_count = 0
        for color_id, role in bootstrap_roles.items():
            existing = self.solve_engine._object_roles.get(color_id)
            # Update if new, or if existing was unknown/low-conf
            if existing is None or existing.role == "unknown" or role.confidence > existing.confidence:
                self.solve_engine._set_role(color_id, role)
                discovered_count += 1
        
        # B169: Ensure bootstrap roles are flushed to DB immediately
        await self.solve_engine._flush_role_writes()

        if discovered_count > 0:
            detail = {
                str(k): {"role": v.role.value, "confidence": v.confidence}
                for k, v in bootstrap_roles.items()
            }
            self._record_write_event(
                kind="bootstrap_discovery",
                summary=f"Discovered {discovered_count} preliminary entities from initial frame.",
                detail=detail,
                source_step=0,
            )

    def _check_entity_gate(self, observation: ARC3Observation) -> dict:
        """B121: Check entity discovery completeness.

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

    # ── Phase 1: Perceive ───────────────────────────────────────────────

    async def perceive(self, observation: ARC3Observation, step: int = 0) -> dict:
        """Ingest puzzle structure into SideQuests then optionally consult memory based on triggers."""
        self._emit_trace_event(
            "phase_start",
            "perceive",
            {
                "step": step,
                "task_id": observation.get("task_id"),
                "state": observation.get("state"),
            },
        )

        # Feed puzzle structure into SideQuests (raw → short-term → entities via consolidation)
        structure_summary = self._summarize_puzzle_structure(observation)
        notify_start = time.time()
        notify_response = await self.brain.notify_turn(
            role="user", content=structure_summary, session_id=self.session_id
        )
        notify_elapsed = (time.time() - notify_start) * 1000
        self._emit_trace_event(
            "operation",
            "notify_turn[structure_ingest]",
            {"step": step},
            {"summary_length": len(structure_summary)},
            notify_elapsed,
        )
        self._record_write_event(
            kind="notify_turn",
            summary=structure_summary,
            detail={"role": "user", "scope": "structure_ingest"},
            response_dict=notify_response,
        )
        # B198: parse any proactive warnings returned by the notify_turn call
        try:
            self._handle_notify_turn_response(notify_response, step=step)
        except Exception:
            pass

        # B162: Front-load grid analysis before the first action.
        if step == 0:
            self._ensure_bootstrap_grid_analysis(observation, step=step)
            
            # B167: Recall puzzle model on level 2+
            if self._current_level > 1:
                await self._recall_puzzle_model()
                # B170/B171: Also load durable hypotheses + action facts from KuzuDB
                await self.hypothesis_mgr.load_hypotheses()
                await self.hypothesis_mgr.load_action_facts()

        # B150: Analyze training examples if available (Step 0 bootstrap)
        if step == 0:
            training_examples = observation.get("training_examples") or []
            if training_examples:
                try:
                    from agents.arc3.grid_analysis import GridDiffEngine
                    diff_engine = GridDiffEngine()
                    diffs = []
                    for example in training_examples:
                        if "input" in example and "output" in example:
                            diff = diff_engine.diff_grids(example["input"], example["output"])
                            diffs.append(diff)
                    
                    if diffs:
                        self._training_diffs = diffs
                        self._transformation_signature = diff_engine.cross_example_consensus(diffs)
                        
                        self._emit_trace_event(
                            "operation",
                            "grid_diff_analysis",
                            {"example_count": len(diffs)},
                            {
                                "pattern": self._transformation_signature.change_pattern,
                                "confidence": round(self._transformation_signature.confidence, 3),
                                "summary": self._transformation_signature.summary
                            }
                        )
                        logger.info("[B150] Grid analysis complete: %s (conf=%.2f)", 
                                    self._transformation_signature.change_pattern, 
                                    self._transformation_signature.confidence)
                except Exception as exc:
                    logger.warning("B150: grid analysis failed: %s", exc)

        # B150: Update last_grid for per-step analysis
        self._last_grid = observation.get("grid")

        # B119/B121: Bootstrap initial entity map at step 0 with enforcement gate
        if step == 0:
            bootstrap_start = time.time()
            await self._bootstrap_entity_discovery(observation)
            bootstrap_elapsed = (time.time() - bootstrap_start) * 1000
            self._emit_trace_event(
                "operation",
                "bootstrap_entity_discovery",
                {"step": step},
                {"entity_count": len(self._entity_map)},
                bootstrap_elapsed,
            )

            # Entity gate enforcement (B121)
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
            self._emit_trace_event(
                "operation",
                "entity_gate",
                {"step": step},
                gate_result,
            )
            self._record_write_event(
                kind="entity_gate",
                summary=f"Entity gate: {gate_result['status']} ({gate_result['reason']})",
                detail=gate_result,
                source_step=0,
            )

        # B90: Check retrieval triggers to decide whether to fetch memory
        should_retrieve = self._should_trigger_retrieval(observation, step)
        self._retrieval_triggered = should_retrieve
        self._emit_trace_event(
            "operation",
            "retrieval_trigger_check",
            {"step": step},
            {"triggered": should_retrieve},
        )

        if should_retrieve:
            query = self._memory_query(observation)
            
            # current_truth gating/dedup
            if not self._retrieval_needed_for_prompt("current_truth"):
                self._emit_trace_event("operation", "retrieval_gated", {"kind": "current_truth", "step": step}, {})
                truth = {"results": []}
            elif self._retrieval_dedup_check("current_truth", query, step):
                self._emit_trace_event("operation", "retrieval_dedup", {"kind": "current_truth", "step": step, "query": query}, {})
                truth = self._memory_context.get("memories_raw", {"results": []}) if self._memory_context else {"results": []}
            else:
                truth_start = time.time()
                truth = await self.brain.current_truth(
                    query=query, session_id=self.session_id, scope="branch", limit=5
                )
                truth_elapsed = (time.time() - truth_start) * 1000
                self._emit_trace_event(
                    "operation",
                    "current_truth",
                    {"step": step, "query": query},
                    {"results": len(truth.get("results", []))},
                    truth_elapsed,
                )

            # recall_relevant_lessons gating/dedup
            if not self._retrieval_needed_for_prompt("recall_relevant_lessons"):
                self._emit_trace_event("operation", "retrieval_gated", {"kind": "recall_relevant_lessons", "step": step}, {})
                lessons = {"lessons": []}
            elif self._retrieval_dedup_check("recall_relevant_lessons", query, step):
                self._emit_trace_event("operation", "retrieval_dedup", {"kind": "recall_relevant_lessons", "step": step, "query": query}, {})
                lessons = self._memory_context.get("lessons_raw", {"lessons": []}) if self._memory_context else {"lessons": []}
            else:
                lessons_start = time.time()
                lessons = await self.brain.recall_relevant_lessons(query=query, limit=4)
                lessons_elapsed = (time.time() - lessons_start) * 1000
                self._emit_trace_event(
                    "operation",
                    "recall_relevant_lessons",
                    {"step": step, "query": query},
                    {"results": len(lessons.get("lessons", []))},
                    lessons_elapsed,
                )

            # analogical_search gating/dedup
            if not self._retrieval_needed_for_prompt("analogical_search"):
                self._emit_trace_event("operation", "retrieval_gated", {"kind": "analogical_search", "step": step}, {})
                analogies = {"results": []}
            elif self._retrieval_dedup_check("analogical_search", query, step):
                self._emit_trace_event("operation", "retrieval_dedup", {"kind": "analogical_search", "step": step, "query": query}, {})
                analogies = self._memory_context.get("analogies_raw", {"results": []}) if self._memory_context else {"results": []}
            else:
                analog_start = time.time()
                analogies = await self.brain.analogical_search(
                    query=query,
                    current_quest_id=observation.get("dataset_id", ""),
                    limit=3,
                    min_similarity=0.35,
                )
                analog_elapsed = (time.time() - analog_start) * 1000
                self._emit_trace_event(
                    "operation",
                    "analogical_search",
                    {"step": step, "query": query},
                    {"results": len(analogies.get("results", []))},
                    analog_elapsed,
                )
            
            # B89: Track retrieval payload sizes
            retrieval_payload = {
                "memories_size": len(json.dumps(truth.get("results", []))),
                "lessons_size": len(json.dumps(lessons.get("lessons", []))),
                "analogies_size": len(json.dumps(analogies.get("results", []))),
                "total_size": 0,
            }
            retrieval_payload["total_size"] = (
                retrieval_payload["memories_size"]
                + retrieval_payload["lessons_size"]
                + retrieval_payload["analogies_size"]
            )
            self._retrieval_payloads.append(retrieval_payload)
            self._last_retrieval_step = step

            # B155: Parse transformation lessons from retrieved memories
            retrieved_memories = truth.get("results", []) + lessons.get("lessons", [])
            memory_hypotheses = self._parse_transformation_lessons(retrieved_memories)
            if memory_hypotheses:
                self._memory_hypotheses = memory_hypotheses
                self._emit_trace_event(
                    "operation",
                    "retrieved_transformation_lessons",
                    {"count": len(memory_hypotheses)},
                    {"top_rule": memory_hypotheses[0].rule_description}
                )

            memory_context = {
                "memories": truth.get("results", []),
                "lessons": lessons.get("lessons", []),
                "analogies": analogies.get("results", []),
                "memories_raw": truth, # Stored for dedup reuse
                "lessons_raw": lessons,
                "analogies_raw": analogies,
                "query": query,
                "_retrieval_payload_size": retrieval_payload["total_size"],
                "_triggered": True,
            }
        else:
            memory_context = {
                "memories": [],
                "lessons": [],
                "analogies": [],
                "query": "",
                "_retrieval_payload_size": 0,
                "_triggered": False,
            }

        self._memory_context = memory_context
        self._emit_trace_event(
            "phase_end",
            "perceive",
            {"step": step},
            {
                "retrieval_triggered": bool(memory_context.get("_triggered")),
                "memories": len(memory_context.get("memories", [])),
                "lessons": len(memory_context.get("lessons", [])),
                "analogies": len(memory_context.get("analogies", [])),
            },
        )
        return memory_context

    async def perceive_step_response(
        self,
        observation: ARC3Observation,
        step: int,
        reward: float,
        done: bool,
        action_id: Optional[str] = None,
    ) -> dict:
        """Lightweight per-step perception of the server response (B202).

        Interprets the ARC server response without invoking LLMs or entity discovery.
        Stores the result at `self._last_response_perception` and emits a short
        SideQuests notify_turn with a `[STEP RESPONSE]` prefix for visibility.
        """
        try:
            self._emit_trace_event("phase_start", "perceive_step_response", {"step": step, "action_id": action_id, "state": observation.get("state")})
        except Exception:
            pass

        grid = observation.get("grid") or []
        delta_count = 0
        try:
            if self._last_grid and grid:
                # Count differing cells conservatively
                for r in range(min(len(self._last_grid), len(grid))):
                    prev_row = self._last_grid[r]
                    cur_row = grid[r]
                    for c in range(min(len(prev_row), len(cur_row))):
                        if prev_row[c] != cur_row[c]:
                            delta_count += 1
        except Exception:
            delta_count = 0

        delta = {"n_cells_changed": delta_count}
        available_actions = observation.get("available_actions") or []
        active_colors = []
        try:
            s = set()
            for row in grid:
                for v in row:
                    if isinstance(v, int) and v != 0:
                        s.add(int(v))
            active_colors = sorted(list(s))
        except Exception:
            active_colors = []

        # Build a context-aware phase question and short summary for SideQuests ingestion.
        solve_ctx = getattr(self, "_solve_context", {}) or {}
        archetype = solve_ctx.get("archetype")
        archetype_conf = solve_ctx.get("archetype_confidence")
        victory = solve_ctx.get("victory_condition") or {}
        victory_type = None
        if isinstance(victory, dict):
            victory_type = victory.get("type") or victory.get("description")
        else:
            victory_type = victory or None

        active_chunk = (solve_ctx.get("active_chunk") or {})
        chunk_desc = None
        if isinstance(active_chunk, dict):
            chunk_desc = active_chunk.get("description")
        elif active_chunk:
            chunk_desc = str(active_chunk)

        # Interpret the delta against reward to produce an expectation match string.
        expectation = "no effect"
        try:
            n_changed = int(delta.get("n_cells_changed", 0) or 0)
            if reward and float(reward) > 0:
                expectation = "positive outcome"
            elif n_changed > 0:
                expectation = "unexpected movement"
            else:
                expectation = "no effect"
        except Exception:
            expectation = "no effect"

        archetype_label = (
            f"{archetype} (conf={float(archetype_conf):.2f})" if archetype is not None and archetype_conf is not None else (archetype or "unknown")
        )

        victory_label = victory_type or "unknown"

        # Construct a concise, context-rich question for ingestion.
        latest_step_action = None
        try:
            if self._step_history:
                latest_step_action = self._step_history[-1].get("action_id")
        except Exception:
            latest_step_action = None

        # B210: Canonical action attribution guard.
        # Prefer the just-recorded step action when it disagrees with a stale caller value.
        if latest_step_action and action_id and latest_step_action != action_id:
            try:
                self._emit_trace_event(
                    "operation",
                    "perceive_action_attribution_mismatch",
                    {"step": step, "provided_action": action_id},
                    {"canonical_action": latest_step_action},
                )
            except Exception:
                pass
            action_id = latest_step_action
        elif latest_step_action and not action_id:
            action_id = latest_step_action

        pq_parts = []
        if action_id:
            pq_parts.append(f"Did {action_id} advance toward the victory condition?")
        pq_parts.append(f"Archetype={archetype_label}")
        pq_parts.append(f"Victory={victory_label}")
        if chunk_desc:
            pq_parts.append(f"Chunk={chunk_desc}")
        pq = " ".join(pq_parts)

        perception = {
            "step": step,
            "state": observation.get("state"),
            "reward": reward,
            "done": done,
            "delta": delta,
            "available_actions": available_actions,
            "active_colors": active_colors,
            "action_id": action_id,
            "phase_question": pq,
        }

        # Persist for later inspection
        try:
            self._last_response_perception = perception
        except Exception:
            pass

        # Notify SideQuests for timeline visibility; include contextual solve information
        try:
            content_parts = [f"[STEP RESPONSE] step={step}"]
            if action_id:
                content_parts.append(f"action={action_id}")
            content_parts.append(f"State={observation.get('state')}")
            content_parts.append(f"Reward={reward}")
            content_parts.append(f"Done={done}")
            content_parts.append(f"Archetype={archetype_label}")
            content_parts.append(f"Victory={victory_label}")
            if chunk_desc:
                content_parts.append(f"Strategy={chunk_desc}")
            content_parts.append(f"Delta={delta.get('n_cells_changed')}")
            if delta.get("direction"):
                content_parts.append(f"direction={delta.get('direction')}")
            content_parts.append(f"Expectation={expectation}")
            content_parts.append(f"Available={available_actions}")
            content = ", ".join(str(p) for p in content_parts)
            await self.brain.notify_turn(role="assistant", content=content, session_id=self.session_id)
        except Exception:
            logger.debug("B205: notify_turn for step response failed", exc_info=True)

        # B211: Write structured action_effect record for graph inference (B212)
        try:
            # Build a delta summary from available frame delta information
            delta_summary = {
                "n_cells_changed": int(delta.get("n_cells_changed", 0) or 0),
                "apparent_effect": delta.get("apparent_effect"),
                "direction": delta.get("direction"),
                "new_colors": [],
                "removed_colors": [],
            }
            if getattr(self, "_frame_deltas", None):
                try:
                    last_delta = self._frame_deltas[-1]
                    if last_delta:
                        delta_summary.update({
                            "n_cells_changed": int(getattr(last_delta, "n_cells_changed", delta_summary["n_cells_changed"]) or 0),
                            "apparent_effect": getattr(last_delta, "apparent_effect", delta_summary.get("apparent_effect")),
                            "direction": getattr(last_delta, "direction", delta_summary.get("direction")),
                            "new_colors": getattr(last_delta, "new_colors_introduced", getattr(last_delta, "new_colors", [])) or [],
                            "removed_colors": getattr(last_delta, "colors_removed", getattr(last_delta, "removed_colors", [])) or [],
                        })
                except Exception:
                    pass
            await self._write_action_effect_record(
                observation=observation,
                action_id=action_id,
                reward=reward,
                step=step,
                delta_summary=delta_summary,
            )
        except Exception:
            logger.debug("B211: _write_action_effect_record failed", exc_info=True)

        try:
            self._emit_trace_event("phase_end", "perceive_step_response", {"step": step}, {"delta": delta})
        except Exception:
            pass

        # Update last grid snapshot for next-step deltas
        try:
            self._last_grid = grid
        except Exception:
            pass

        return perception


    async def _write_action_effect_record(
        self,
        observation: ARC3Observation,
        action_id: str | None,
        reward: float,
        step: int,
        delta_summary: dict,
    ) -> None:
        """Write a typed ActionEffect lesson record to SideQuests (B211).

        This produces a compact structured payload and calls the Brain client's
        `upsert_lesson` tool so the record is stored and becomes queryable by
        downstream graph hypotheses (B212).
        """
        try:
            if step <= 0 or not action_id:
                return

            # Derive effect class from delta summary
            try:
                n_changed = int(delta_summary.get("n_cells_changed") or 0)
            except Exception:
                n_changed = 0
            apparent_effect = str(delta_summary.get("apparent_effect") or "").lower()
            direction = delta_summary.get("direction")

            if n_changed == 0:
                effect_class = "no_effect"
            elif direction and n_changed <= 4:
                effect_class = "directional_movement"
            elif n_changed > 30:
                effect_class = "large_transformation"
            elif "no_effect" in apparent_effect or "no change" in apparent_effect:
                effect_class = "no_effect"
            else:
                effect_class = "local_change"

            # Pull entity type from solve context if available
            solve_ctx = getattr(self, "_solve_context", {}) or {}
            roles = solve_ctx.get("object_roles") or solve_ctx.get("roles") or {}
            entity_type = "unknown"
            spatial_role = "unknown"
            try:
                player_pos = getattr(self, "_player_position", None)
                if player_pos and isinstance(roles, dict):
                    for role_key, role_data in roles.items():
                        if isinstance(role_data, dict):
                            est_pos = role_data.get("estimated_position") or role_data.get("position")
                            if est_pos and tuple(est_pos) == tuple(player_pos):
                                entity_type = str(role_data.get("entity_type") or role_key or "unknown")
                                spatial_role = str(role_data.get("role") or "unknown")
                                break
                if entity_type == "unknown" and isinstance(roles, dict):
                    for role_key, role_data in roles.items():
                        if isinstance(role_data, dict):
                            role_str = str(role_data.get("role") or "").lower()
                            if role_str in ("trigger", "intermediate", "collectible"):
                                entity_type = str(role_data.get("entity_type") or role_key or "unknown")
                                spatial_role = role_str
                                break
            except Exception:
                pass

            archetype = str(solve_ctx.get("archetype") or "unknown")

            lesson_data = {
                "lesson_type": "action_effect",
                "action": action_id,
                "effect_class": effect_class,
                "n_cells_changed": n_changed,
                "new_colors": delta_summary.get("new_colors") or delta_summary.get("new_colors_introduced") or [],
                "removed_colors": delta_summary.get("removed_colors") or delta_summary.get("colors_removed") or [],
                "direction": direction,
                "reward_signal": float(reward) if reward is not None else 0.0,
                "entity_type": entity_type,
                "spatial_role": spatial_role,
                "puzzle_archetype": archetype,
                "task_id": str(observation.get("task_id") or ""),
                "dataset_id": str(observation.get("dataset_id") or ""),
                "step": step,
            }

            # Prepare textual content and tags for the lesson upsert call.
            content = (
                f"[ACTION_EFFECT] step={step} action={action_id} effect={effect_class} "
                f"n_changed={n_changed} entity={entity_type} archetype={archetype}"
            )
            tags = ["action_effect", effect_class, entity_type, archetype]

            try:
                # Use BrainClientProtocol's upsert_lesson contract (domain, text, valence, ...)
                valence = float(reward) if reward is not None else 0.0
                await self.brain.upsert_lesson(domain="action_effect", text=json.dumps(lesson_data), valence=valence, confidence=0.9, tags=tags)
                self._emit_trace_event(
                    "operation",
                    "action_effect_written",
                    {"step": step, "action": action_id},
                    {"effect_class": effect_class, "entity_type": entity_type},
                )
            except Exception as exc:
                logger.warning("B211: failed to write ActionEffect record: %s", exc)
        except Exception:
            logger.debug("B211: _write_action_effect_record outer failure", exc_info=True)

    # ── Phase 2: Plan ───────────────────────────────────────────────────

    def _record_llm_usage(self):
        """B180: Record tokens from last LLM call into CostTracker."""
        if self.cost_tracker and self.llm and hasattr(self.llm, 'last_usage') and self.llm.last_usage:
            u = self.llm.last_usage
            self.cost_tracker.record(u.get("prompt_tokens", 0), u.get("completion_tokens", 0))

    async def hypothesize(
        self,
        observation: ARC3Observation,
        action_taken: str | None,
        step: int,
        transition_meta: dict | None = None,
    ) -> dict:
        """Update state graph, generate/update hypotheses, detect invariants.

        Called after every action, before the next plan/act decision.
        Returns hypothesis context for prompt construction.
        """
        self._emit_trace_event(
            "phase_start",
            "hypothesize",
            {
                "step": step,
                "action_taken": action_taken,
                "state": observation.get("state"),
            },
        )
        available = observation.get("available_actions") or [f"ACTION{i}" for i in range(1, 8)]
        observe_start = time.time()
        context = await self.hypothesis_mgr.observe(
            grid=observation["grid"],
            action_taken=action_taken,
            step=step,
            available_actions=available,
            observation=observation,
            transition_meta=transition_meta,
        )
        observe_elapsed = (time.time() - observe_start) * 1000
        self._emit_trace_event(
            "operation",
            "hypothesis_mgr.observe",
            {"step": step, "action_taken": action_taken},
            {
                "loop_detected": bool(context.get("loop_detected")),
                "facts": len(context.get("action_facts", []) or []),
                "paths": len(context.get("path_hypotheses", []) or []),
            },
            observe_elapsed,
        )

        # Override energy estimate with hypothesis-driven value if available
        hud_energy = context.get("energy_from_hud")
        if hud_energy is not None:
            observation["energy_estimate"] = hud_energy

        if context.get("last_transition_effect"):
            transition_effect = context["last_transition_effect"]
            action_facts = context.get("action_facts", [])
            path_hypotheses = context.get("path_hypotheses", [])
            top_fact = action_facts[0] if action_facts else None
            top_path = path_hypotheses[0] if path_hypotheses else None
            summary = (
                f"{transition_effect.get('action')} -> {transition_effect.get('meaningful_change_label')} "
                f"(score {transition_effect.get('meaningful_change_score', 0.0):.2f}); "
                f"facts={len(action_facts)} paths={len(path_hypotheses)}"
            )
            detail: dict[str, Any] = {
                "action": transition_effect.get("action"),
                "label": transition_effect.get("meaningful_change_label"),
                "score": transition_effect.get("meaningful_change_score"),
                "facts": len(action_facts),
                "paths": len(path_hypotheses),
                "saved_action_facts": self._compact_fact_trace(action_facts),
                "saved_path_hypotheses": self._compact_path_trace(path_hypotheses),
            }
            if top_fact:
                detail["top_fact"] = {
                    "action": top_fact.get("action"),
                    "fact_type": top_fact.get("fact_type"),
                    "value_status": top_fact.get("value_status"),
                }
            if top_path:
                detail["top_path"] = {
                    "actions": top_path.get("actions"),
                    "value_status": top_path.get("value_status"),
                }
            bottleneck = context.get("environment_bottleneck")
            if bottleneck:
                detail["environment_bottleneck"] = bottleneck
            self._record_write_event(
                kind="hypothesis_update",
                summary=summary,
                detail=detail,
                source_step=step,
            )
            self._emit_trace_event(
                "operation",
                "hypothesis_update",
                {"step": step},
                {
                    "label": transition_effect.get("meaningful_change_label"),
                    "score": transition_effect.get("meaningful_change_score"),
                    "facts": len(action_facts),
                    "paths": len(path_hypotheses),
                },
            )

        self._hypothesis_context = context
        
        # B116: Refresh compaction artifact
        compact_start = time.time()
        self._compaction_artifact = self.hypothesis_mgr.compact_exploration(step)
        compact_elapsed = (time.time() - compact_start) * 1000
        self._emit_trace_event(
            "operation",
            "compact_exploration",
            {"step": step},
            {"artifact_type": type(self._compaction_artifact).__name__},
            compact_elapsed,
        )
        self._emit_trace_event(
            "phase_end",
            "hypothesize",
            {"step": step},
            {
                "loop_detected": bool(context.get("loop_detected")),
                "energy_from_hud": context.get("energy_from_hud"),
            },
        )
        # B212: structured graph evidence pass (runs after LLM hypothesize)
        try:
            if step > 0:
                graph_evidence = await self.graph_hypothesize(observation=observation, step=step)
                if graph_evidence:
                    context["graph_evidence"] = graph_evidence
                    self._hypothesis_context = context
        except Exception:
            logger.debug("B212: graph_hypothesize failed", exc_info=True)

        return context

    async def solve(
        self,
        observation: ARC3Observation,
        hypothesis_context: dict,
        step: int,
    ) -> dict:
        """Classify archetype, assign object roles, hypothesize victory condition, chunk plan."""
        self._emit_trace_event(
            "phase_start",
            "solve",
            {
                "step": step,
                "task_id": observation.get("task_id"),
                "state": observation.get("state"),
            },
        )
        if self._bootstrap_grid_summary and "bootstrap_grid_analysis" not in hypothesis_context:
            hypothesis_context = dict(hypothesis_context)
            hypothesis_context["bootstrap_grid_analysis"] = self._bootstrap_grid_summary

        current_hash = hypothesis_context.get("current_state_hash", "")
        solve_start = time.time()
        
        # B177: Accept orchestrator escalation
        if hasattr(self, '_force_replan') and self._force_replan:
            hypothesis_context = dict(hypothesis_context)
            hypothesis_context["orchestrator_force_replan"] = True
            self._force_replan = False

        # B198: inject proactive warnings into hypothesis_context passed to SolveEngine
        hypothesis_context = dict(hypothesis_context or {})
        hypothesis_context["proactive_warnings"] = [
            {
                "text": w.get("text", ""),
                "type": w.get("type", ""),
                "domain": w.get("domain", ""),
                "relevance": float(w.get("relevance_score", 0.0) or 0.0),
            }
            for w in getattr(self, "_proactive_warnings", [])
            if isinstance(w, dict)
        ]

        solve_ctx = await self.solve_engine.solve(
            observation=observation,
            hypothesis_context=hypothesis_context,
            step=step,
            state_graph=self.hypothesis_mgr.graph,
            current_state_hash=current_hash,
            level_pattern=self._level_pattern,  # B150
            solved_levels=self._solved_levels,  # B157
        )
        solve_elapsed = (time.time() - solve_start) * 1000

        # B142: Use the live reevaluation computed inside SolveEngine so we do not
        # apply progress decay twice after the solve step.
        graduation_reevaluation = dict(getattr(self.solve_engine, "_last_graduation_reevaluation", {}) or {})
        if graduation_reevaluation and graduation_reevaluation.get("new_score") is not None:
            # Check if graduation dropped below 0.5 (dissonance threshold)
            if graduation_reevaluation["new_score"] < 0.5 and not solve_ctx.dissonance_detected:
                solve_ctx.dissonance_detected = True
                solve_ctx.dissonance_reason = (
                    f"Graduation dropped to {graduation_reevaluation['new_score']:.2f} "
                    f"(reason: {graduation_reevaluation.get('graduation_capped_reason', 'unknown')})"
                )
                # Emit trace event for the graduation-triggered dissonance
                self._emit_trace_event(
                    "dissonance_trigger",
                    "graduation_drop",
                    {
                        "step": step,
                        "original_score": graduation_reevaluation["original_score"],
                        "new_score": graduation_reevaluation["new_score"],
                        "reason": graduation_reevaluation.get("graduation_capped_reason"),
                        "evidence_floor_applied": graduation_reevaluation.get("evidence_floor_applied"),
                        "progress_decay_applied": graduation_reevaluation.get("progress_decay_applied"),
                    },
                )

        # B149: Reset fatigue on chunk switch
        old_chunk = self._solve_context.get("active_chunk") if self._solve_context else None
        old_desc = old_chunk.get("description") if old_chunk else None
        new_desc = solve_ctx.active_chunk.description if solve_ctx.active_chunk else None
        if new_desc != old_desc:
            self._action_fatigue.clear()
            # Log only on real transitions to avoid noise
            if old_desc or new_desc:
                logger.info("B149: Action fatigue reset due to chunk switch (%s -> %s)", old_desc, new_desc)

        self._solve_context = {
            "archetype": solve_ctx.archetype.value,
            "archetype_confidence": solve_ctx.archetype_confidence,
            "object_roles": {
                str(k): {
                    "role": v.role.value, 
                    "confidence": v.confidence,
                    "estimated_position": v.estimated_position
                }
                for k, v in solve_ctx.object_roles.items()
            },
            "victory_condition": (
                {
                    "type": solve_ctx.victory_condition.condition_type.value,
                    "description": solve_ctx.victory_condition.description,
                    "confidence": solve_ctx.victory_condition.confidence,
                }
                if solve_ctx.victory_condition else None
            ),
            "active_chunk": (
                {
                    "description": solve_ctx.active_chunk.description,
                    "estimated_actions": solve_ctx.active_chunk.estimated_actions,
                    "progress": solve_ctx.active_chunk.progress_score,
                    "source": solve_ctx.active_chunk.source,
                    "plan_id": solve_ctx.active_chunk.plan_id,
                    "graduation_score": solve_ctx.active_chunk.graduation_score,
                    "graduation_reason": solve_ctx.active_chunk.graduation_reason,
                    "graduation_components": solve_ctx.active_chunk.graduation_components,
                    # B142: Add trace fields for graduation re-evaluation
                    **(
                        {
                            "graduation_capped_reason": graduation_reevaluation.get("graduation_capped_reason"),
                            "evidence_floor_applied": graduation_reevaluation.get("evidence_floor_applied"),
                            "progress_decay_applied": round(graduation_reevaluation.get("progress_decay_applied", 0.0), 3),
                        }
                        if graduation_reevaluation else {}
                    ),
                }
                if solve_ctx.active_chunk else None
            ),
            "dissonance": solve_ctx.dissonance_detected,
            "dissonance_reason": solve_ctx.dissonance_reason,
            "strategy_summary": solve_ctx.strategy_summary,
            "chunk_ledger": [
                {
                    "description": entry.description,
                    "status": entry.status,
                    "steps_used": entry.steps_used,
                    "outcome_summary": entry.outcome_summary,
                }
                for entry in (solve_ctx.chunk_ledger or [])
            ],
            # B209: canonical expected action used by execute-policy adherence checks.
            "expected_action": (
                (solve_ctx.active_chunk.estimated_actions[0] if (solve_ctx.active_chunk and solve_ctx.active_chunk.estimated_actions) else None)
            ),
            "expected_action_family": (
                (solve_ctx.active_chunk.estimated_actions[0] if (solve_ctx.active_chunk and solve_ctx.active_chunk.estimated_actions) else None)
            ),
            "plateau_mode": solve_ctx.plateau_mode,
            "plateau_reason": solve_ctx.plateau_reason,
            "ranked_action_families": solve_ctx.ranked_action_families,
            "action_family_scores": solve_ctx.action_family_scores,
        }
        
        # B161: Update goal position from latest solve context
        self._update_goal_position()

        archetype = self._solve_context["archetype"]
        conf = self._solve_context["archetype_confidence"]
        victory = (self._solve_context.get("victory_condition") or {}).get("type", "unknown")
        chunk = (self._solve_context.get("active_chunk") or {}).get("description", "none")
        dissonance = self._solve_context.get("dissonance", False)
        logger.info(
            "[SOLVE] step=%d archetype=%s(%.2f) victory=%s chunk=%s dissonance=%s plateau=%s",
            step, archetype, conf, victory, chunk[:40] if chunk else "none", dissonance, solve_ctx.plateau_mode
        )
        self._emit_trace_event(
            "phase_end",
            "solve",
            {"step": step},
            {
                "archetype": archetype,
                "archetype_confidence": conf,
                "victory": victory,
                "dissonance": dissonance,
                "plateau_mode": solve_ctx.plateau_mode,
                "ranked_action_families": solve_ctx.ranked_action_families[:3],
            },
            solve_elapsed,
        )

        return self._solve_context

    async def plan(self, observation: ARC3Observation, memory_context: dict) -> dict:
        """Declare a plan and capture Amygdala Reflex context."""
        import time
        plan_start = time.time()
        self._emit_trace_event("phase_start", "plan", {"goal": f"Solve ARC task {observation['dataset_id']}:{observation['task_id']}"})
        
        goal = f"Solve ARC task {observation['dataset_id']}:{observation['task_id']}"
        recall_start = time.time()
        recall = await self.brain.recall_plans(
            goal_query=goal, session_id=self.session_id, min_valence=0.0, limit=3
        )
        recall_elapsed = (time.time() - recall_start) * 1000
        self._emit_trace_event("operation", "recall_plans", {"goal_query": goal}, {"found": len(recall.get("plans", []))}, recall_elapsed)
        
        draft_start = time.time()
        self._plan_steps = self._draft_plan_steps(
            observation, memory_context, recall, self._hypothesis_context
        )
        draft_elapsed = (time.time() - draft_start) * 1000
        self._emit_trace_event("operation", "draft_plan_steps", {}, {"steps_count": len(self._plan_steps)}, draft_elapsed)
        
        # B131: Emit reasoning trace explaining plan strategy
        sc = self._solve_context
        reasoning_parts = []
        if sc and sc.get("archetype"):
            reasoning_parts.append(f"Archetype: {sc['archetype']} (conf={sc['archetype_confidence']:.2f})")
        if sc and sc.get("victory_condition"):
            vc = sc["victory_condition"]
            reasoning_parts.append(f"Win condition: {vc['type']} (conf={vc['confidence']:.2f})")
        if sc and sc.get("active_chunk"):
            ch = sc["active_chunk"]
            reasoning_parts.append(f"Active chunk: {ch['description']}")
        
        reasoning_summary = " | ".join(reasoning_parts) if reasoning_parts else "Fallback exploration strategy"
        reasoning_narrative = f"[PLAN REASONING] Goal: {goal}. Strategy: {reasoning_summary}. Steps: {len(self._plan_steps)}"
        reason_start = time.time()
        await self.brain.notify_turn(role="assistant", content=reasoning_narrative, session_id=self.session_id)
        reason_elapsed = (time.time() - reason_start) * 1000
        self._emit_trace_event("operation", "notify_turn[plan_reasoning]", {"content": reasoning_narrative}, {}, reason_elapsed)
        
        # A011: Plan-registration idempotency
        sc = self._solve_context or {}
        active_chunk = sc.get("active_chunk") or {}
        ch_desc = str(active_chunk.get("description", "fallback"))
        vc = sc.get("victory_condition") or {}
        vc_type = vc.get("type") if isinstance(vc, dict) else (getattr(vc, "condition_type", None).value if hasattr(vc, "condition_type") and hasattr(vc.condition_type, "value") else str(vc))
        
        fingerprint = (
            goal,
            tuple(self._plan_steps),
            str(sc.get("archetype")),
            str(vc_type),
            ch_desc
        )
        
        if getattr(self, "_last_registered_fingerprint", None) == fingerprint and getattr(self, "_last_registered_payload", None):
            self._emit_trace_event("operation", "register_plan", {"goal": goal, "steps": len(self._plan_steps), "idempotent": True}, {"plan_id": self._plan_id}, 0.0)
            plan_payload = self._last_registered_payload
        else:
            register_start = time.time()
            plan_payload = await self.brain.register_plan(
                goal=goal, steps=self._plan_steps, session_id=self.session_id
            )
            register_elapsed = (time.time() - register_start) * 1000
            self._emit_trace_event("operation", "register_plan", {"goal": goal, "steps": len(self._plan_steps)}, {"plan_id": plan_payload.get("plan_id")}, register_elapsed)
            self._last_registered_fingerprint = fingerprint
            self._last_registered_payload = plan_payload
            self._plan_id = plan_payload.get("plan_id")

        self._reflex_context = plan_payload
        self._record_write_event(
            kind="register_plan",
            summary=f"registered plan {self._plan_id or 'unknown'} with {len(self._plan_steps)} step(s)",
            detail={
                "plan_id": self._plan_id,
                "steps": len(self._plan_steps),
            },
            response_dict=plan_payload,
        )
        memory_context["similar_plans"] = recall.get("plans", [])
        return plan_payload

    # ── Phase 1: Understand (B156) ───────────────────────────────────────

    async def run_phase1(self, observation: ARC3Observation, training_examples: List[dict]):
        """B156: Phase 1 (UNDERSTAND) — analyze solved levels, hypothesize, verify. B151/B152."""
        # Skip if already set
        if hasattr(self, '_phase2_mode') and self._phase2_mode == "execution":
            return {"verified": True, "output_grid": self._verified_output_grid}

        from agents.arc3.grid_analysis import GridDiffEngine
        from agents.arc3.solver import GameRuleHypothesizer

        phase1_start = time.time()

        # Step 1: Analyze solved levels (B150)
        # We treat training_examples as "solved levels" for analysis consistency
        diff_engine = GridDiffEngine()
        
        # If we have real solved levels from B157, use them. 
        # Otherwise use static training_examples.
        level_data = []
        if hasattr(self, '_solved_levels') and self._solved_levels:
            level_data = self._solved_levels
        else:
            for ex in training_examples:
                level_data.append({
                    "start_grid": ex["input"],
                    "end_grid": ex["output"],
                    "actions": ["given_example"],
                    "steps": 0
                })

        diffs = []
        for level in level_data:
            diff = diff_engine.diff_grids(level["start_grid"], level["end_grid"])
            diffs.append(diff)
        
        self._level_pattern = diff_engine.cross_level_consensus(diffs)
        self._solved_level_diffs = diffs

        self._emit_trace_event("operation", "phase1_analysis", {
            "n_levels": len(level_data),
            "signature": self._level_pattern.game_rule_summary,
            "confidence": self._level_pattern.confidence,
        })

        # Step 2: Generate game rule hypotheses (B151)
        hypothesizer = GameRuleHypothesizer()
        hypotheses = await hypothesizer.hypothesize(
            level_pattern=self._level_pattern,
            solved_levels=level_data,
            llm_client=self.llm,
        )
        self.solve_engine._set_game_rule_hypotheses(hypotheses)

        if not hypotheses:
            self._emit_trace_event("operation", "phase1_no_hypotheses", {})
            return None

        self._emit_trace_event("operation", "phase1_hypotheses", {
            "count": len(hypotheses),
            "top_rule": hypotheses[0].rule_description,
            "top_confidence": hypotheses[0].confidence,
        })

        # Step 3: Verify via REPL (B152)
        verifier = LevelReplayVerifier()
        loop = RuleRefinementLoop(self.llm, verifier)

        best_hypothesis = await loop.solve(
            hypotheses=hypotheses,
            solved_levels=level_data,
        )

        if best_hypothesis and best_hypothesis.confidence >= 0.7:
            # Verified!
            self.solve_engine._set_game_rule_hypotheses([best_hypothesis])
            self._emit_trace_event("operation", "phase1_verified", {
                "rule": best_hypothesis.rule_description,
                "confidence": best_hypothesis.confidence,
            })
            # Note: For ARC-AGI-3, Phase 1 doesn't produce a target grid for Phase 2 
            # execution mode directly because the levels differ.
            # But high confidence will bias the fallback strategy.
            return {
                "verified": True,
                "hypothesis": best_hypothesis,
            }
        else:
            self._emit_trace_event("operation", "phase1_verification_failed", {
                "best_confidence": best_hypothesis.confidence if best_hypothesis else 0.0
            })
            return None


    def _next_execution_action(self, observation: ARC3Observation, available_actions: List[str]) -> Optional[ARC3Action]:
        """B156: Deterministic action for painting the known solution grid.

        Computes diff between current and target, returns next ACTION6 call.
        Returns None when all cells match (puzzle should be solved).
        """
        target = self._verified_output_grid
        current = observation.get("grid") or []

        if not target:
            return None

        # Find first cell that differs
        for r in range(len(target)):
            for c in range(len(target[0])):
                target_val = target[r][c]
                if r < len(current) and c < len(current[0]):
                    current_val = current[r][c]
                else:
                    current_val = -1

                if target_val != current_val:
                    # Prefer ACTION6 (paint) if available
                    if "ACTION6" in available_actions:
                        return {
                            "action_id": "ACTION6",
                            "x": c,
                            "y": r,
                            "color": target_val,
                            "rationale": f"Phase 2 execution: paint ({r},{c}) from {current_val} to {target_val}",
                            "decision_source": "phase2_execution",
                        }

        return None  # All cells match — done

    # ── Phase 3: Act ───────────────────────────────────────────────────

    async def act(
        self,
        observation: ARC3Observation,
        memory_context: dict,
        step_num: int,
    ) -> ARC3Action:
        """Choose an action using integrated memory, reflex, and plan context."""
        self._emit_trace_event(
            "phase_start",
            "act",
            {
                "step": step_num,
                "state": observation.get("state"),
                "available_actions": len(observation.get("available_actions") or []),
                "mode": self._phase2_mode,
            },
        )

        # B178: Load empirical action directions (once per level, cached)
        if self._action_direction_map is None and self._entity_graph:
            try:
                # Use task_id from memory context or attr if set
                tid = getattr(self, '_task_id', None)
                if tid:
                    self._action_direction_map = await self._entity_graph.get_action_directions(
                        task_id=tid, level=self._current_level
                    )
                    if self._action_direction_map:
                        self._emit_trace_event("operation", "action_semantics_loaded", {
                            "count": len(self._action_direction_map),
                            "mappings": self._action_direction_map
                        })
            except Exception as exc:
                logger.warning("B178: Failed to load action semantics: %s", exc)
                self._action_direction_map = {}

        # B156: If in execution mode, use deterministic painter
        if self._phase2_mode == "execution":
            available_actions = observation.get("available_actions") or []
            action = self._next_execution_action(observation, available_actions)
            if action:
                # Update trace and return
                self._emit_trace_event(
                    "operation",
                    "execution_painter",
                    {"step": step_num},
                    {"action_id": action["action_id"], "target": [action["y"], action["x"]]},
                )
                return action
            
            # All cells match but puzzle not solved? Fall back to LLM
            self._phase2_mode = "fallback"
            logger.warning("B156: Execution mode exhausted but puzzle not solved — falling back")
            self._emit_trace_event(
                "operation",
                "execution_fallback",
                {"step": step_num},
                {"reason": "Grid matches target but game not DONE"}
            )

        # B135: Evidence-based loop check — only fire when context suggests a loop is likely
        _no_progress = self._consecutive_no_progress_steps
        _frame_dupe = len(self._recent_frame_hashes) != len(set(self._recent_frame_hashes))
        _dissonance = bool((self._solve_context or {}).get("dissonance") or (self._hypothesis_context or {}).get("dissonance"))

        _should_check_loop = (
            _no_progress >= 2
            or _frame_dupe
            or _dissonance
        )

        if _should_check_loop:
            loop_check_start = time.time()
            await self.brain.current_truth(
                query="Am I looping?", session_id=self.session_id, scope="branch", limit=3
            )
            loop_check_elapsed = (time.time() - loop_check_start) * 1000
            self._emit_trace_event(
                "operation",
                "current_truth[loop_check]",
                {"step": step_num},
                {},
                loop_check_elapsed,
            )

            # B183: Meta-Supervisor evaluation (replaces B141 escalation ladder)
            verdict = await self._supervisor.evaluate(
                step_history=self._step_history,
                execution_trace=self._execution_trace,
                cost_tracker=getattr(self, 'cost_tracker', None)
            )
            
            if verdict.decision != SupervisorDecision.CONTINUE:
                self._emit_trace_event("operation", "supervisor_verdict", {
                    "step": step_num, 
                    "decision": verdict.decision.value, 
                    "reason": verdict.reason,
                })

            if verdict.decision == SupervisorDecision.NUDGE:
                # Inject hint into next prompt context (handled in build_action_packet)
                self._supervisor_nudge = verdict.nudge_hint
            elif verdict.decision == SupervisorDecision.RESET_STRATEGY:
                # Tier 3 equivalent + B177 strategy wipe
                self.solve_engine._archetype_confidence *= 0.3
                self.solve_engine._victory_condition = None
                self.solve_engine._plateau_locked_family = None
                self._blocked_actions.clear()
                if hasattr(self, '_blocked_axes'):
                    self._blocked_axes.clear()
                self._mark_active_chunk_failed("supervisor_reset")
            elif verdict.decision == SupervisorDecision.ABANDON:
                self._should_abandon = True
                logger.warning(f"B183: Supervisor deciding to ABANDON at step {step_num}: {verdict.reason}")
        else:
            self._emit_trace_event(
                "loop_check_skipped",
                {
                    "step": step_num,
                    "reason": f"no evidence of loop (no_progress={_no_progress}, frame_dupe={_frame_dupe}, dissonance={_dissonance})",
                },
            )

        narrative = f"Step {step_num} observation: state={observation.get('state', 'UNKNOWN')} colors={observation['colors']} shapes={observation['shapes']}"
        step_notify_start = time.time()
        notify_response = await self.brain.notify_turn(role="user", content=narrative, session_id=self.session_id)
        step_notify_elapsed = (time.time() - step_notify_start) * 1000
        self._emit_trace_event(
            "operation",
            "notify_turn[step_observation]",
            {"step": step_num},
            {"summary_length": len(narrative)},
            step_notify_elapsed,
        )
        self._record_write_event(
            kind="notify_turn",
            summary=narrative,
            detail={"role": "user", "scope": "step_observation", "step": step_num},
            response_dict=notify_response,
            source_step=step_num,
        )
        # B198: parse any proactive warnings returned by the notify_turn call
        try:
            self._handle_notify_turn_response(notify_response, step=step_num)
        except Exception:
            pass

        available_actions = observation.get("available_actions") or [f"ACTION{i}" for i in range(1, 8)]

        # A013: Prompt-skip no-op short-circuit logic
        hyp_ctx = self._hypothesis_context or {}
        roles = (self._solve_context or {}).get("object_roles", {})
        grounded_roles_sig = tuple(sorted([(cid, getattr(r, 'role', None).value if hasattr(getattr(r, 'role', None), 'value') else str(getattr(r, 'role', None))) for cid, r in roles.items()]))
        
        current_fingerprint = (
            observation.get("frame_hash"),
            tuple(sorted(available_actions)),
            grounded_roles_sig,
            len(hyp_ctx.get("action_facts", []))
        )
        
        # If fingerprint matches and we have a previous action, we can short-circuit
        if self._last_observation_fingerprint == current_fingerprint and self._last_llm_action:
            self._emit_trace_event(
                "operation", 
                "prompt_skip_noop", 
                {"step": step_num, "reason": "identical observation and facts"}, 
                {"action_id": self._last_llm_action.get("action_id")}
            )
            # Add a small skip notification to step history trace
            self._record_write_event(
                kind="prompt_skip",
                summary="skipped LLM call (identical observation)",
                detail={"action_id": self._last_llm_action.get("action_id")},
                source_step=step_num
            )
            # Re-use the last cached action result (contains rationale etc)
            action = dict(self._last_llm_action)
            # Update the step count in history correctly
            self._step_history.append({
                "step": len(self._step_history) + 1,
                "state_before": observation.get("state"),
                "board_before": self._snapshot_for_trace(observation),
                "solve_context": dict(self._solve_context) if self._solve_context else None,
                "available_actions": list(available_actions),
                "prompt": "(skipped: same as last step)",
                "decision_flow": {
                    "proposed_by": "short_circuit",
                    "executed_by": "short_circuit",
                    "candidate_action": action.get("action_id"),
                    "executed_action": action.get("action_id"),
                    "decision_source": "short_circuit",
                },
                "action_id": action.get("action_id"),
                "x": action.get("x"),
                "y": action.get("y"),
                "rationale": f"[REUSED] {action.get('rationale')}",
                "thinking_trace": action.get("thinking_trace", []),
            })
            return action

        self._last_observation_fingerprint = current_fingerprint

        # B117: Use PromptPacket model
        packet = self.build_action_packet(
            observation=observation,
            memory_context=memory_context,
            step_history=self._step_history,
            available_actions=available_actions,
        )
        prompt = packet.render()
        
        # B89: Estimate prompt tokens and track first-prompt detail level
        prompt_tokens = self.serializer._estimate_tokens(prompt)
        self._prompt_tokens_per_step.append(prompt_tokens)
        if not self._step_history:
            # This is the first prompt - determine detail level
            # Check for specific block presence
            has_memory = packet.get_block("MEMORY") is not None
            has_facts = packet.get_block("ACTION_FACTS") is not None
            has_effects = packet.get_block("OBSERVED_EFFECTS") is not None
            self._first_prompt_detail_level = "rich" if (has_memory or has_facts or has_effects) else "compact"
        
        # Check if prompt asks for decision from observed effects
        if packet.get_block("OBSERVED_EFFECTS") and packet.get_block("INSTRUCTION"):
            self._asked_for_decision_from_effects = "effect" in prompt.lower()

        # B166: Deterministic autopilot — bypass LLM when player/goal positions are known
        autopilot_action = self._try_autopilot(observation, available_actions)
        if autopilot_action:
            action = autopilot_action
            sandbox_elapsed = 0.0
            candidate_action_id = action.get("action_id")
            llm_source = "autopilot"

            self._emit_trace_event(
                "operation",
                "mental_sandbox",
                {"step": step_num},
                {"action_id": candidate_action_id, "decision_source": "autopilot", "skipped": True},
                0.0,
            )
        else:
            # B114/B123: Mental Sandbox reasoning loop (includes REPL)
            sandbox_start = time.time()
            action = await self._mental_sandbox(prompt, available_actions, observation)
            sandbox_elapsed = (time.time() - sandbox_start) * 1000
            
            # B133: Record candidate chosen by LLM/Sandbox before policy/guards
            candidate_action_id = action.get("action_id")
            llm_source = action.get("decision_source", "unknown")

            self._emit_trace_event(
                "operation",
                "mental_sandbox",
                {"step": step_num},
                {
                    "action_id": candidate_action_id,
                    "decision_source": llm_source
                },
                sandbox_elapsed,
            )

        # B133: Pass frame_hash to policy enforcement
        action = self._enforce_action_policy(
            action,
            available_actions,
            current_frame_hash=observation.get("frame_hash"),
            observation=observation,
        )
        action = self._ensure_action6_coordinates(action, observation)

        # B115: Final pre-execution decision guard
        guard_result = self.solve_engine.critique_action(
            action_id=action["action_id"],
            available_actions=available_actions,
            hypothesis_context=self._hypothesis_context or {},
            step_history=self._step_history,
        )
        self._emit_trace_event(
            "operation",
            "critique_action",
            {"step": step_num, "candidate_action": action.get("action_id")},
            {
                "status": guard_result.get("status"),
                "suggested_action": guard_result.get("suggested_action"),
            },
        )
        
        executed_by = "llm"
        final_source = action.get("decision_source", llm_source)

        if guard_result["status"] in ("blocked", "warned"):
            self.record_guard_escalation(step_num, guard_result["reason"], guard_result["status"])
            if guard_result.get("suggested_action"):
                old_id = action["action_id"]
                new_id = guard_result["suggested_action"]
                
                # B133: Explicit guard_override_reason event
                self._emit_trace_event(
                    "operation",
                    "guard_override_reason",
                    {"step": step_num, "original": old_id, "override": new_id},
                    {"reason": guard_result["reason"], "guard_status": guard_result["status"]}
                )

                action["action_id"] = new_id
                action["rationale"] = (
                    f"{action.get('rationale', '')} (guard override: {old_id} -> {new_id} :: {guard_result['reason']})"
                )
                executed_by = "guard_override"
                final_source = "guard_override"
            elif guard_result["status"] == "blocked":
                # If blocked and no suggestion, we must still move. 
                # Policy enforcement should have already ensured it's at least valid if possible.
                action["rationale"] = f"{action.get('rationale', '')} (guard blocked: {guard_result['reason']})"
                executed_by = "guard_blocked_fallback"
                final_source = "guard_blocked_fallback"
        
        action = self._ensure_action6_coordinates(action, observation)
        action["guard_status"] = guard_result["status"]
        action["decision_source"] = final_source

        # B126: Adversarial verification of candidate action (optional, can be disabled via config)
        verifier_enabled = self.config.get("enable_verifier", False)
        verifier_attempts = 0
        verifier_result = None
        original_action_id = action["action_id"]

        while verifier_enabled and verifier_attempts < 2:
            verifier_result = await self._verify_candidate_action(
                action_id=action["action_id"],
                rationale=action.get("rationale", ""),
                observation=observation,
                step_history=self._step_history,
                hypothesis_context=self._hypothesis_context or {},
            )

            verifier_attempts += 1

            if verifier_result["approved"]:
                break

            # First rejection: retry with rejection context
            if verifier_attempts < 2:
                logger.info(
                    "Verifier rejected %s: %s — retrying",
                    action["action_id"],
                    verifier_result["rejection_reason"],
                )
                # Append rejection context to the original prompt and retry
                retry_prompt = prompt + f"\n\nVerifier feedback: {verifier_result['rejection_reason']}\nReconsider: what action is better?"
                retry_action = await self._mental_sandbox(retry_prompt, available_actions, observation)
                retry_action = self._enforce_action_policy(
                    retry_action,
                    available_actions,
                    observation=observation,
                )
                retry_action = self._ensure_action6_coordinates(retry_action, observation)
                action = retry_action
            else:
                # Second rejection: log and proceed with original action
                logger.warning(
                    "Verifier double-rejected %s (%s), proceeding with original %s",
                    action["action_id"],
                    verifier_result["rejection_reason"],
                    original_action_id,
                )
                # Don't revert action, proceed with final candidate

        # Record verifier result in thinking trace (only if verifier was enabled)
        if verifier_enabled:
            if action.get("thinking_trace") is None:
                action["thinking_trace"] = []

            action["thinking_trace"].append({
                "kind": "verification",
                "candidate_action": original_action_id,
                "verifier_approved": verifier_result.get("approved") if verifier_result else True,
                "rejection_reason": verifier_result.get("rejection_reason") if verifier_result else None,
                "attempts": verifier_attempts,
                "final_action": action["action_id"],
            })

            action["verifier_status"] = "approved" if (verifier_result and verifier_result["approved"]) else "rejected_then_proceeded"
            self._emit_trace_event(
                "operation",
                "verifier",
                {"step": step_num, "attempts": verifier_attempts},
                {
                    "enabled": True,
                    "approved": bool(verifier_result and verifier_result.get("approved")),
                    "final_action": action.get("action_id"),
                },
            )
        else:
            action["verifier_status"] = "disabled"
            self._emit_trace_event(
                "operation",
                "verifier",
                {"step": step_num},
                {"enabled": False},
            )

        # B89: Track invalid actions
        action_id = action.get("action_id")
        if action_id not in available_actions:
            self._invalid_action_count += 1

        self._step_history.append({
            "step": len(self._step_history) + 1,
            "state_before": observation.get("state"),
            "board_before": self._snapshot_for_trace(observation),
            "solve_context": dict(self._solve_context) if self._solve_context else None,
            "available_actions": list(available_actions),
            "prompt": prompt,
            "decision_flow": {
                "proposed_by": "llm",
                "executed_by": executed_by,
                "candidate_action": candidate_action_id,
                "executed_action": action.get("action_id"),
                "decision_source": action.get("decision_source"),
                "expected_action": action.get("expected_action"),
                "selected_action": action.get("selected_action"),
                "override_reason": action.get("override_reason"),
                "adherence_ok": action.get("adherence_ok"),
                "guard_status": guard_result["status"],
                "guard_reason": guard_result["reason"] if guard_result["status"] != "approved" else None
            },
            "action_id": action.get("action_id"),
            "candidate_action_id": candidate_action_id,
            "decision_source": action.get("decision_source"),
            "expected_action": action.get("expected_action"),
            "selected_action": action.get("selected_action"),
            "override_reason": action.get("override_reason"),
            "adherence_ok": action.get("adherence_ok"),
            "x": action.get("x"),
            "y": action.get("y"),
            "rationale": action.get("rationale"),
            "thinking_trace": action.get("thinking_trace", []),
            "guard_status": action.get("guard_status", "unknown"),
            "verifier_status": action.get("verifier_status", "unknown"),
            "autopilot_player_row": action.get("autopilot_player_row"),
            "autopilot_player_col": action.get("autopilot_player_col"),
            "reward": None,
            "done": False,
            "prompt_tokens": prompt_tokens,
        })
        # A013: cache the action for potential short-circuit in the next step
        self._last_llm_action = dict(action)

        self._emit_trace_event(
            "phase_end",
            "act",
            {"step": step_num},
            {
                "candidate_action": candidate_action_id,
                "action_id": action.get("action_id"),
                "decision_source": action.get("decision_source"),
                "guard_status": action.get("guard_status"),
                "verifier_status": action.get("verifier_status"),
                "prompt_tokens": prompt_tokens,
            },
        )
        return action

    def _parse_llm_json(self, raw: str) -> dict:
        """Harden JSON parsing with extraction recovery (B132)."""
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Attempt to extract JSON object using regex
            match = re.search(r"(\{.*\})", raw, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass
            raise

    async def _mental_sandbox(self, initial_prompt: str, available_actions: List[str], observation: ARC3Observation) -> ARC3Action:
        """B114/B123: Bounded internal reasoning loop before final move."""
        max_iterations = 2
        iteration = 0
        current_prompt = initial_prompt
        thinking_trace = []
        self._emit_trace_event(
            "operation",
            "mental_sandbox_start",
            {"max_iterations": max_iterations},
            {"available_actions": len(available_actions)},
        )
        
        # B122/B123: Use extracted sandbox instructions
        current_prompt += SANDBOX_INSTRUCTION
        current_prompt += REPL_SANDBOX_INSTRUCTION

        while iteration < max_iterations:
            iteration += 1
            self._emit_trace_event(
                "operation",
                "mental_sandbox_iteration",
                {"iteration": iteration},
            )
            messages = [
                {"role": "system", "content": SANDBOX_SYSTEM_MESSAGE},
                {"role": "user", "content": current_prompt},
            ]
            try:
                try:
                    raw = await asyncio.to_thread(
                        self.llm.chat, messages,
                        response_format={"type": "json_object"},
                    )
                except TypeError:
                    # Provider doesn't support response_format (e.g. mock LLMs)
                    raw = await asyncio.to_thread(self.llm.chat, messages)
                    self._record_llm_usage()
                
                # B180: Record usage for the try block too
                self._record_llm_usage()

                # Robust multi-tier parsing: JSON → embedded JSON → plain text
                result = self._parse_llm_response(raw, available_actions)
                parse_method = result["parse_method"] if result else "failed"

                if result and result.get("_parsed"):
                    parsed = result["_parsed"]
                elif result:
                    # Plain text parse succeeded — we have an action but no structured data
                    parsed = None
                else:
                    parsed = None

                if result and parse_method != "json_direct":
                    self._emit_trace_event(
                        "operation",
                        "mental_sandbox_parse_recovery",
                        {"iteration": iteration},
                        {"method": parse_method, "raw_preview": (raw or "")[:150]},
                    )

                # If plain text parse found an action but no structured JSON,
                # return it directly (skip sandbox_thought/repl_test which need JSON)
                if result and parsed is None:
                    action_id = self._normalize_action_id(result["action_id"])
                    if action_id and action_id in available_actions:
                        self._emit_trace_event(
                            "operation",
                            "mental_sandbox_final_decision",
                            {"iteration": iteration},
                            {"action_id": action_id, "source": f"sandbox_{parse_method}"},
                        )
                        action: ARC3Action = {
                            "action_id": action_id,
                            "rationale": result.get("rationale", ""),
                            "thinking_trace": thinking_trace,
                            "decision_source": f"sandbox_{parse_method}",
                        }
                        return action

                if parsed is None:
                    raise json.JSONDecodeError(
                        f"No action found in LLM response",
                        (raw or "")[:200], 0,
                    )

                # Check for sandbox thought tool (B114)

                if "sandbox_thought" in parsed:
                    test_action = parsed["sandbox_thought"]
                    result = self.solve_engine.peek_action_consequences(test_action, self._hypothesis_context or {})
                    self._emit_trace_event(
                        "operation",
                        "sandbox_thought",
                        {"iteration": iteration, "test_action": test_action},
                        {
                            "estimated_score": result.get("estimated_score") if isinstance(result, dict) else None,
                            "meaningful_change": result.get("meaningful_change") if isinstance(result, dict) else None,
                        },
                    )
                    
                    thought_entry = {
                        "iteration": iteration,
                        "thought": parsed.get("thought", ""),
                        "tool": "sandbox_thought",
                        "test_action": test_action,
                        "result": result
                    }
                    thinking_trace.append(thought_entry)
                    
                    current_prompt += f"\n\nSandbox Result for {test_action}: {json.dumps(result)}\nWhat is your next thought or final decision?"
                    continue

                # Check for REPL test tool (B123)
                if "repl_test" in parsed:
                    code = parsed["repl_test"]
                    # Add simple grid variable for convenience
                    grid_code = f"g = {json.dumps(observation.get('grid', []))}\n" + code
                    result = await asyncio.to_thread(execute_repl, grid_code)
                    self._emit_trace_event(
                        "operation",
                        "repl_test",
                        {"iteration": iteration},
                        {
                            "stderr": result.get("stderr", "")[:200],
                            "stdout_len": len(result.get("stdout", "")),
                        },
                    )
                    
                    thought_entry = {
                        "iteration": iteration,
                        "thought": parsed.get("thought", ""),
                        "tool": "repl_test",
                        "code": code,
                        "result": result
                    }
                    thinking_trace.append(thought_entry)
                    
                    current_prompt += f"\n\nREPL Result:\nstdout: {result['stdout']}\nstderr: {result['stderr']}\nWhat is your next thought or final decision?"
                    continue
                
                # Final decision found
                if "action_id" in parsed or "action" in parsed:
                    action_id = self._normalize_action_id(parsed.get("action_id") or parsed.get("action"))
                    rationale = parsed.get("rationale") or parsed.get("why") or ""
                    
                    source = "sandbox"
                    if parse_method not in ("json_direct", "direct"):
                        source = "sandbox_recovered"
                    
                    self._emit_trace_event(
                        "operation",
                        "mental_sandbox_final_decision",
                        {"iteration": iteration},
                        {"action_id": action_id, "source": source},
                    )
                    
                    if action_id not in available_actions:
                        fallback = available_actions[0]
                        logger.warning(
                            "LLM selected unavailable action %r in sandbox; falling back to %r.",
                            action_id, fallback
                        )
                        return {
                            "action_id": fallback,
                            "rationale": f"Invalid LLM action {action_id!r} in sandbox; fallback to {fallback}. Original rationale: {rationale}",
                            "thinking_trace": thinking_trace,
                            "decision_source": f"{source}_invalid_fallback"
                        }

                    if thinking_trace:
                        rationale = f"{rationale} (sandbox refined)"
                    action: ARC3Action = {
                        "action_id": action_id,
                        "rationale": rationale,
                        "thinking_trace": thinking_trace,
                        "decision_source": source,
                    }
                    x = self._coerce_action6_coordinate(parsed.get("x"))
                    y = self._coerce_action6_coordinate(parsed.get("y"))
                    if x is not None:
                        action["x"] = x
                    if y is not None:
                        action["y"] = y
                    return action
                
                # Fallback if neither
                iteration = max_iterations
            except Exception as exc:
                logger.warning("Mental sandbox parse failed: %s", exc)
                self._emit_trace_event(
                    "operation",
                    "mental_sandbox_parse_error",
                    {"iteration": iteration},
                    {"error": str(exc)},
                )
                break

        # Fallback to standard query if sandbox fails or exhausts
        final_action = await self._query_llm(initial_prompt, available_actions)
        self._emit_trace_event(
            "operation",
            "mental_sandbox_fallback_query_llm",
            {},
            {"action_id": final_action.get("action_id")},
        )
        if thinking_trace:
            final_action["thinking_trace"] = thinking_trace
        final_action["decision_source"] = "mental_sandbox_fallback"
        return final_action


    # ── Phase 4: Evaluate ──────────────────────────────────────────────

    async def evaluate(
        self,
        correct: bool,
        steps_taken: int,
        max_steps: int,
        final_observation: ARC3Observation,
    ) -> dict:
        """Report outcome and trigger valence propagation."""
        self._emit_trace_event(
            "phase_start",
            "evaluate",
            {
                "task_id": final_observation.get("task_id"),
                "steps_taken": steps_taken,
                "max_steps": max_steps,
                "correct": correct,
            },
        )
        valence = self.reward_to_valence(correct, steps_taken, max_steps)
        payload = {
            "plan_id": self._plan_id,
            "outcome": "correct" if correct else "failed",
            "valence": valence,
            "session_id": self.session_id,
        }
        if self._plan_id:
            report_start = time.time()
            outcome_response = await self.brain.report_outcome(**payload)
            report_elapsed = (time.time() - report_start) * 1000
            self._emit_trace_event(
                "operation",
                "report_outcome",
                {"plan_id": self._plan_id},
                {
                    "outcome": payload["outcome"],
                    "valence": round(valence, 2),
                },
                report_elapsed,
            )
            self._record_write_event(
                kind="report_outcome",
                summary=(
                    f"plan {self._plan_id} outcome={payload['outcome']} valence={valence:.2f}"
                ),
                detail={
                    "plan_id": self._plan_id,
                    "outcome": payload["outcome"],
                    "valence": round(valence, 2),
                },
                response_dict=outcome_response,
            )
        narrative = (
            f"Final observation for {final_observation['task_id']}: "
            f"correct={correct}, steps={steps_taken}, valence={valence:.2f}"
        )
        final_notify_start = time.time()
        final_notify_response = await self.brain.notify_turn(role="assistant", content=narrative, session_id=self.session_id)
        final_notify_elapsed = (time.time() - final_notify_start) * 1000
        self._emit_trace_event(
            "operation",
            "notify_turn[final_narrative]",
            {"task_id": final_observation.get("task_id")},
            {"summary_length": len(narrative)},
            final_notify_elapsed,
        )
        self._record_write_event(
            kind="notify_turn",
            summary=narrative,
            detail={"role": "assistant", "scope": "final_narrative"},
            response_dict=final_notify_response,
        )
        # B198: parse any proactive warnings returned by the notify_turn call
        try:
            self._handle_notify_turn_response(final_notify_response, step=None)
        except Exception:
            pass
        self._emit_trace_event(
            "phase_end",
            "evaluate",
            {"task_id": final_observation.get("task_id")},
            {
                "correct": correct,
                "steps_taken": steps_taken,
                "valence": round(valence, 2),
            },
        )

        # B155: Store full game strategy
        if hasattr(self, '_solved_levels') and self._solved_levels:
            hypothesis = (
                self.solve_engine._game_rule_hypotheses[0]
                if hasattr(self.solve_engine, '_game_rule_hypotheses') and self.solve_engine._game_rule_hypotheses
                else None
            )

            # Build per-level action patterns
            level_summaries = []
            all_action_ids = set()
            for level in self._solved_levels:
                actions = level["actions"]
                level_summaries.append(f"Level {level['level']}: {len(actions)} steps")
                all_action_ids.update(actions)

            lesson_content = (
                f"ARC GAME STRATEGY\n"
                f"Levels: {len(self._solved_levels)}, Outcome: {'SOLVED' if correct else 'FAILED'}\n"
                f"Actions used: {sorted(list(all_action_ids))}\n"
            )
            
            if hypothesis:
                lesson_content += (
                    f"Game rule: {hypothesis.rule_description}\n"
                    f"Action semantics: {json.dumps(hypothesis.action_semantics)}\n"
                    f"Confidence: {hypothesis.confidence:.2f}\n"
                )
            
            lesson_content += "Level summaries:\n" + "\n".join(level_summaries)

            try:
                await self.brain.notify_turn(
                    role="assistant",
                    content=lesson_content,
                    session_id=self.session_id,
                )
                self._emit_trace_event("operation", "store_game_strategy", {"status": "success"})
            except Exception as exc:
                logger.warning("B155: failed to store game strategy: %s", exc)

        # B165: Persist structured run lessons and a puzzle-fingerprint analogy anchor.
        try:
            lessons_payload = self._extract_run_lessons(correct, final_observation)
            store_lesson = getattr(self.brain, "store_lesson", None)
            if callable(store_lesson):
                await store_lesson(
                    content=json.dumps(lessons_payload),
                    tags=[
                        "arc_run",
                        str(lessons_payload.get("archetype", "unknown")),
                        str(lessons_payload.get("outcome", "failed")),
                    ],
                    session_id=self.session_id,
                )
                self._emit_trace_event("operation", "store_run_lesson", {"status": "success"})

            fingerprint = lessons_payload.get("puzzle_fingerprint", {})
            analogy_text = (
                f"[PUZZLE ANALOGY] ARC puzzle {fingerprint.get('grid_size', '0x0')} "
                f"{fingerprint.get('n_colors', 0)} colors {fingerprint.get('n_regions', 0)} regions. "
                f"Outcome: {lessons_payload.get('outcome')}. "
                f"Strategy: {lessons_payload.get('strategy_attempted')}. "
                f"Effective actions: {lessons_payload.get('effective_actions', [])}."
            )
            await self.brain.notify_turn(
                role="assistant",
                content=analogy_text,
                session_id=self.session_id,
            )
        except Exception as exc:
            logger.warning("B165: failed to persist run lessons: %s", exc)

        return {"valence": valence}

    # ── Retrieval Trigger Logic ──────────────────────────────────────

    def _should_trigger_retrieval(self, observation: ARC3Observation, step: int) -> bool:
        """Determine if memory retrieval should be triggered based on puzzle state.

        Triggers:
        1. Initial puzzle bootstrapping (step == 0)
        2. Repeated no-progress steps (3+ consecutive)
        3. Fallback or invalid-action correction (invalid_action_count increased)
        4. Loop suspicion (loop_detected in hypothesis context)
        5. Evidence gap (no good action candidates in hypothesis context)
        """
        # Trigger 1: Initial puzzle bootstrapping
        if step == 0:
            return True

        # B118: Pruning check - if retrieval tools are already marked as low-value/high-latency,
        # skip optional mid-run retrieval to save time.
        pruned_types = {d["call_type"] for d in self._pruning_decisions if d["action"] == "deprioritize"}
        retrieval_types = {"current_truth", "recall_lessons", "analogical_search"}
        if retrieval_types.intersection(pruned_types) and step > 0:
            logger.info("[B118] Skipping retrieval trigger due to prior pruning decisions.")
            return False

        # Trigger 2: Repeated no-progress steps (3+ consecutive)
        if self._consecutive_no_progress_steps >= 3 and step > self._last_retrieval_step:
            return True

        # Trigger 3: Invalid action correction (attempted invalid action)
        if self._invalid_action_count > self._last_seen_invalid_action_count and step > self._last_retrieval_step:
            self._last_seen_invalid_action_count = self._invalid_action_count
            return True

        hyp_ctx = self._hypothesis_context or {}

        # Trigger 4: Loop suspicion
        if hyp_ctx.get("loop_detected"):
            return True

        # Trigger 5: Large state shift that can invalidate prior assumptions
        if self._should_trigger_large_state_shift(hyp_ctx):
            return True

        # Trigger 6: Evidence gap - no clear action candidates
        observed_effects = hyp_ctx.get("observed_action_effects", [])
        action_coverage = hyp_ctx.get("action_coverage") or {}
        untested_count = action_coverage.get("untested_count", 0)
        tested_count = action_coverage.get("tested_count", 0)

        if tested_count > 2 and not observed_effects:
            # Tested multiple actions but no usable effects recorded
            return True

        # Trigger 6: All tested actions have decayed to low_value (top_two_low_value)
        if action_coverage.get("top_two_low_value"):
            return True

        return False

    def _should_trigger_large_state_shift(self, hyp_ctx: dict | None) -> bool:
        """Cheap proxy for a sudden board change that invalidates prior assumptions."""
        if not hyp_ctx:
            return False
        last_effect = hyp_ctx.get("last_transition_effect") or {}
        score = float(last_effect.get("meaningful_change_score", 0.0))
        pixels_changed = int(last_effect.get("pixels_changed", 0) or 0)
        if pixels_changed >= 32:
            return True
        if score >= 0.65 and pixels_changed >= 12:
            return True
        return False

    # B153: Map ARC colors to single characters for compact display
    _COLOR_CHARS = ".#@*+~^%&$!?<>="

    @staticmethod
    def render_grid_compact(grid: List[List[int]], max_rows: int = 30) -> str:
        """Render grid as single-character-per-cell visual (B153)."""
        if not grid or not isinstance(grid, list):
            return "Empty grid"
        lines = []
        display_grid = grid[:max_rows]
        for row in display_grid:
            if not isinstance(row, list):
                continue
            line = "".join(
                ARCOrchestrator._COLOR_CHARS[min(max(0, int(cell)), len(ARCOrchestrator._COLOR_CHARS) - 1)]
                for cell in row
            )
            lines.append(line)
        if len(grid) > max_rows:
            lines.append(f"... ({len(grid) - max_rows} more rows)")
        return "\n".join(lines)

    @staticmethod
    def render_training_example(input_grid: List[List[int]], output_grid: List[List[int]]) -> str:
        """Render input→output pair side by side (B153)."""
        input_lines = ARCOrchestrator.render_grid_compact(input_grid).split("\n")
        output_lines = ARCOrchestrator.render_grid_compact(output_grid).split("\n")
        max_in_len = max(len(l) for l in input_lines) if input_lines else 0
        pairs = []
        for i in range(max(len(input_lines), len(output_lines))):
            il = input_lines[i] if i < len(input_lines) else ""
            ol = output_lines[i] if i < len(output_lines) else ""
            pairs.append(f"{il:<{max_in_len}} -> {ol}")
        return "\n".join(pairs)

    def _is_compact_model(self) -> bool:
        """B164: Detect whether the configured model likely benefits from compact prompts."""
        llm_cfg = self.config.get("llm", {}) if isinstance(self.config.get("llm"), dict) else {}
        model_name = str(
            self.config.get("llm_model")
            or self.config.get("model")
            or llm_cfg.get("model")
            or ""
        ).lower()
        compact_patterns = ("1b", "3b", "7b", "8b", "mini", "tiny", "small")
        return any(pattern in model_name for pattern in compact_patterns)

    def _ensure_bootstrap_grid_analysis(self, observation: ARC3Observation, step: int = 0) -> dict | None:
        """B162: Compute and cache a structural summary before the first action."""
        grid = observation.get("grid") or []
        if not grid:
            return self._bootstrap_grid_summary

        if step != 0 and self._bootstrap_grid_summary is not None:
            return self._bootstrap_grid_summary

        summary = grid_characteristic_summary(grid)
        self._bootstrap_grid_summary = summary
        observation["bootstrap_grid_summary"] = summary

        self._emit_trace_event(
            "operation",
            "bootstrap_grid_analysis",
            {"step": step},
            {
                "n_regions": summary.get("n_regions", 0),
                "colors": summary.get("colors", []),
                "summary": str(summary.get("text_summary", ""))[:200],
            },
        )
        return summary

    def _build_spatial_query(self, solve_context: dict, observation: dict) -> str:
        """Build a compact structural query string describing the current board layout.

        The query is intentionally simple (space-separated keywords) so MCP
        retrieval handlers can match on archetype, role types, region counts,
        and victory descriptors.
        """
        parts: list[str] = []
        archetype = str(solve_context.get("archetype") or "unknown")
        parts.append(archetype)

        roles = solve_context.get("object_roles") or solve_context.get("roles") or {}
        try:
            role_types = sorted({
                str(v.get("role") or "")
                for v in (roles.values() if isinstance(roles, dict) else [])
                if isinstance(v, dict) and v.get("role")
            })
            if role_types:
                parts.extend(role_types)
        except Exception:
            pass

        try:
            n_regions = int(solve_context.get("n_regions") or 0)
            if n_regions >= 2:
                parts.append(f"{n_regions}_regions")
        except Exception:
            pass

        vc = solve_context.get("victory_condition") or "unknown"
        if isinstance(vc, dict):
            vc_desc = vc.get("type") or vc.get("description") or "victory_condition_unknown"
        else:
            vc_desc = vc or "victory_condition_unknown"
        parts.append(str(vc_desc))

        return " ".join(str(p) for p in parts if p)

    async def graph_hypothesize(self, observation: dict, step: int) -> dict:
        """B212: Tiered graph evidence pass.

        Queries the brain for relevant lessons, current truth, and procedures
        scoped to the current puzzle archetype / spatial query and distills
        simple action->entity->effect patterns into `grounded_hypotheses`.
        """
        # No-op at step 0
        if step == 0:
            return {"graph_evidence": {"grounded_hypotheses": []}}

        # Determine archetype from solve context or observation
        archetype = None
        try:
            if self._solve_context and isinstance(self._solve_context, dict):
                archetype = self._solve_context.get("archetype")
        except Exception:
            archetype = None

        if not archetype:
            archetype = observation.get("puzzle_archetype") if isinstance(observation, dict) else None

        # If archetype is not known, we'll still perform broad recalls
        # (some brain handlers may ignore missing scope and return global matches).

        try:
            # Tier 1: recall structured lessons (action_effect / action semantics)
            # Use a compact structured query so MCP handlers can match on lesson_type
            # and archetype. This aligns with B212 acceptance criteria.
            if archetype:
                query = f"lesson_type:action_effect puzzle_archetype:{archetype}"
                lessons_resp = await getattr(self.brain, "recall_relevant_lessons")(query=query, limit=5)
            else:
                query = "lesson_type:action_effect"
                lessons_resp = await getattr(self.brain, "recall_relevant_lessons")(query=query, limit=5)
            lessons = lessons_resp.get("lessons") if isinstance(lessons_resp, dict) else lessons_resp or []
        except Exception:
            lessons = []

        try:
            # Tier 2: current truth (spatial facts)
            spatial_q = self._build_spatial_query(self._solve_context or {}, observation or {})
            truth_resp = await getattr(self.brain, "current_truth")(query=spatial_q)
            truths = truth_resp.get("results") if isinstance(truth_resp, dict) else truth_resp or []
        except Exception:
            truths = []

        try:
            # Tier 3: recall procedures for archetype (or global if archetype missing)
            if archetype:
                proc_resp = await getattr(self.brain, "recall_procedures")(archetype=archetype)
            else:
                proc_resp = await getattr(self.brain, "recall_procedures")()
            procs = proc_resp.get("procedures") if isinstance(proc_resp, dict) else proc_resp or []
        except Exception:
            procs = []

        # Distill patterns from lessons into simple grounded hypotheses
        patterns: dict[tuple, int] = {}
        for l in lessons or []:
            text = None
            if isinstance(l, dict):
                text = l.get("text")
            else:
                text = l
            if not text:
                continue
            try:
                data = json.loads(text)
            except Exception:
                # Best-effort: skip non-json lesson text
                continue

            action = data.get("action") or data.get("action_id") or data.get("verb")
            entity = data.get("entity_type") or data.get("entity") or data.get("target")
            effect = data.get("effect") or data.get("meaning") or data.get("outcome")
            if not action or not entity or not effect:
                continue
            key = (str(action), str(entity), str(effect))
            patterns[key] = patterns.get(key, 0) + 1

        grounded_hypotheses = [
            {
                "action": a,
                "entity_type": e,
                "expected_effect": ef,
                "evidence_count": c,
            }
            for (a, e, ef), c in patterns.items()
        ]

        graph_evidence = {
            "grounded_hypotheses": grounded_hypotheses,
            "action_effect_patterns": lessons or [],
            "spatial_victory_hints": truths or [],
            "matching_procedures": procs or [],
            "lessons_considered": len(lessons or []),
            "truths_considered": len(truths or []),
            "procedures_considered": len(procs or []),
        }

        # Trace for observability
        try:
            self._emit_trace_event(
                "operation",
                "graph_hypothesize_complete",
                {"step": step},
                {"lessons": len(lessons or []), "grounded": len(grounded_hypotheses)},
            )
        except Exception:
            pass

        return {"graph_evidence": graph_evidence}

    # ── Prompt Construction ──────────────────────────────────────────

    def build_action_packet(
        self,
        observation: ARC3Observation,
        memory_context: dict,
        step_history: List[dict],
        available_actions: List[str],
    ) -> PromptPacket:
        """Construct a structured PromptPacket for the current decision. B117/B153/B164"""
        is_first_step = not step_history
        if is_first_step:
            self._ensure_bootstrap_grid_analysis(observation, step=0)

        # B164: compact prompts for smaller models unless we already have a verified execution grid.
        if self._compact_mode and self._phase2_mode != "execution":
            packet = self._build_compact_packet(observation, memory_context, step_history, available_actions)
            mode = "compact"

        # Mode 1: EXECUTION — High-confidence verified rule
        elif hasattr(self, '_phase2_mode') and self._phase2_mode == "execution" and self._verified_output_grid:
            packet = self._build_execution_packet(observation, available_actions)
            mode = "execution"

        # Mode 2: RULE APPLICATION — Prior levels solved, hypothesis available
        elif self._current_level > 1 and self._solved_levels and hasattr(self.solve_engine, '_game_rule_hypotheses') and self.solve_engine._game_rule_hypotheses:
            packet = self._build_rule_application_packet(observation, memory_context, available_actions)
            mode = "rule_application"

        # Mode 3: EXPLORATION — Level 1, no prior knowledge
        elif self._current_level == 1 and not self._solved_levels:
            packet = self._build_exploration_packet(observation, available_actions)
            mode = "exploration"

        # Mode 4: NAVIGATION (Fallback) — Low confidence or complex state
        else:
            packet = self._build_navigation_packet(observation, memory_context, step_history, available_actions)
            mode = "navigation"

        if mode != "compact" and is_first_step and self._bootstrap_grid_summary and packet.get_block("GRID_ANALYSIS") is None:
            packet.blocks.append(
                ContentBlock(
                    type="GRID_ANALYSIS",
                    content=str(self._bootstrap_grid_summary.get("text_summary", "")),
                    header="GRID ANALYSIS",
                )
            )

        # B153/B164: Token budget tracking and tracing
        prompt_text = packet.render()
        token_estimate = self.serializer._estimate_tokens(prompt_text)
        if self._compact_mode and mode in {"compact", "navigation"}:
            budget = 1800
        else:
            budget = {"execution": 200, "rule_application": 500, "exploration": 400, "navigation": 1200}.get(mode, 1200)

        if token_estimate > budget:
            logger.warning("B153: %s prompt exceeds %d token target (%d tokens)", mode, budget, token_estimate)

        self._emit_trace_event(
            "operation",
            "prompt_budget",
            {"tokens": token_estimate, "mode": mode, "budget": budget},
        )

        # B183: Meta-Supervisor Nudge
        if hasattr(self, '_supervisor_nudge') and self._supervisor_nudge:
            packet.blocks.append(
                ContentBlock(
                    type="SUPERVISOR_NUDGE",
                    content=self._supervisor_nudge,
                    header="STRATEGIC NUDGE",
                )
            )
            # Clear nudge after injecting into prompt
            self._supervisor_nudge = None

        return packet

    def _build_exploration_packet(self, observation: ARC3Observation, available_actions: List[str]) -> PromptPacket:
        """Mode 3: Exploration mode for Level 1. B153."""
        packet = PromptPacket()
        packet.blocks.append(ContentBlock(type="SYSTEM", content=SYSTEM_PROMPT))

        from agents.arc3.prompts import ARC_EXPLORATION_TEMPLATE
        packet.blocks.append(ContentBlock(
            type="INSTRUCTION",
            content=ARC_EXPLORATION_TEMPLATE
        ))

        grid = observation.get("grid") or []
        packet.blocks.append(ContentBlock(
            type="GRID",
            content=self.render_grid_compact(grid),
            header="CURRENT GRID"
        ))

        packet.blocks.append(ContentBlock(
            type="ACTION_INVOCATION",
            content=f"Available actions: {available_actions}\nReturn JSON: {{\"action_id\": \"...\", \"rationale\": \"...\"}}",
            header="CHOOSE ACTION"
        ))
        # B212: Surface graph evidence in rule-application prompts as well
        graph_lines = self._format_graph_evidence_section(self._hypothesis_context)
        if graph_lines:
            packet.blocks.append(ContentBlock(type="GRAPH_EVIDENCE", content="\n".join(graph_lines), header="GRAPH EVIDENCE"))
        return packet

    def _build_rule_application_packet(self, observation: ARC3Observation, memory_context: dict, available_actions: List[str]) -> PromptPacket:
        """Mode 2: Rule application mode for Level 2+. B153."""
        packet = PromptPacket()
        packet.blocks.append(ContentBlock(type="SYSTEM", content=SYSTEM_PROMPT))

        hyp = self.solve_engine._game_rule_hypotheses[0]
        from agents.arc3.prompts import ARC_LEVEL_INSIGHT_TEMPLATE
        insight = ARC_LEVEL_INSIGHT_TEMPLATE.format(
            current_level=self._current_level,
            total_levels=observation.get("win_levels", 8),
            action_semantics=json.dumps(hyp.action_semantics),
            rule_hypothesis=hyp.rule_description,
            confidence=hyp.confidence
        )
        packet.blocks.append(ContentBlock(type="PRIOR_INSIGHTS", content=insight, header="KNOWLEDGE FROM PRIOR LEVELS"))

        grid = observation.get("grid") or []
        packet.blocks.append(ContentBlock(
            type="GRID",
            content=self.render_grid_compact(grid),
            header="CURRENT GRID"
        ))

        packet.blocks.append(ContentBlock(
            type="ACTION_INVOCATION",
            content=f"Available actions: {available_actions}\nReturn JSON: {{\"action_id\": \"...\", \"rationale\": \"...\"}}",
            header="CHOOSE ACTION"
        ))
        return packet


    def _build_compact_packet(
        self,
        observation: ARC3Observation,
        memory_context: dict,
        step_history: List[dict],
        available_actions: List[str],
    ) -> PromptPacket:
        """B164: Compact prompt packet tuned for smaller local models."""
        from agents.arc3.prompts import COMPACT_INSTRUCTION_TEMPLATE, COMPACT_SYSTEM_PROMPT

        packet = PromptPacket()
        packet.blocks.append(
            ContentBlock(
                type="SYSTEM",
                content=COMPACT_SYSTEM_PROMPT.format(available_actions=", ".join(available_actions)),
            )
        )

        observation_lines = [self._format_observation_section(observation)]
        if self._bootstrap_grid_summary:
            observation_lines.append(
                f"GRID ANALYSIS: {self._bootstrap_grid_summary.get('text_summary', '')}"
            )
        packet.blocks.append(
            ContentBlock(type="OBSERVATION", content="\n".join(observation_lines))
        )

        fact_lines = self._format_action_fact_section(self._hypothesis_context)
        if fact_lines:
            packet.blocks.append(
                ContentBlock(type="ACTION_FACTS", content="\n".join(fact_lines[:4]))
            )

        # B212: Inject structured graph evidence when available
        graph_lines = self._format_graph_evidence_section(self._hypothesis_context)
        if graph_lines:
            packet.blocks.append(ContentBlock(type="GRAPH_EVIDENCE", content="\n".join(graph_lines), header="GRAPH EVIDENCE"))

        history_text = self._format_history_section(step_history) if step_history else "No prior steps yet."
        packet.blocks.append(ContentBlock(type="HISTORY", content=history_text))
        packet.blocks.append(ContentBlock(type="INSTRUCTION", content=COMPACT_INSTRUCTION_TEMPLATE))
        return packet

    def _build_pattern_packet(self, observation: ARC3Observation, memory_context: dict, available_actions: List[str]) -> PromptPacket:
        """Phase 1: Pattern discovery (UNDERSTAND phase) from solved levels. B153/B151."""
        packet = PromptPacket()

        packet.blocks.append(ContentBlock(
            type="SYSTEM",
            content=ARC_PATTERN_SYSTEM_PROMPT,
        ))

        # Solved levels (Training data)
        solved_text = ""
        for i, level in enumerate(self._solved_levels):
            solved_text += f"\nSolved Level {i+1}:\n"
            solved_text += self.render_training_example(level["start_grid"], level["end_grid"])
        
        if solved_text:
            packet.blocks.append(ContentBlock(
                type="SOLVED_LEVELS",
                content=solved_text.strip(),
                header="SOLVED LEVELS (TRAINING DATA)",
            ))

        # Level analysis summary (from B150)
        if self._level_pattern:
            sig = self._level_pattern
            content = (
                f"LEVEL PATTERN CONSENSUS:\n"
                f"- Consistent color map: {json.dumps(sig.consistent_color_map)}\n"
                f"- Spatial pattern: {sig.consistent_spatial_pattern or 'unknown'}\n"
                f"- Game rule summary: {sig.game_rule_summary}\n"
                f"- Confidence: {sig.confidence:.2f}\n"
            )
            packet.blocks.append(ContentBlock(
                type="GRID_ANALYSIS",
                content=content,
                header="DETERMINISTIC LEVEL ANALYSIS",
            ))

        # Game rule hypotheses (from B151)
        if hasattr(self.solve_engine, '_game_rule_hypotheses') and self.solve_engine._game_rule_hypotheses:
            top = self.solve_engine._game_rule_hypotheses[0]
            content = (
                f"RULE: {top.rule_description}\n"
                f"OBJECTIVE: {top.objective_description}\n"
                f"STRATEGY: {top.level_strategy}\n"
                f"CONFIDENCE: {top.confidence:.0%}\n"
            )
            packet.blocks.append(ContentBlock(
                type="PATTERN_HYPOTHESIS",
                content=content,
                header="GAME RULE HYPOTHESIS"
            ))

        # Test input (current level)
        test_grid = observation.get("grid")
        if test_grid:
            packet.blocks.append(ContentBlock(
                type="TEST_INPUT",
                content=self.render_grid_compact(test_grid),
                header="CURRENT LEVEL GRID",
            ))

        # Instruction
        packet.blocks.append(ContentBlock(
            type="INSTRUCTION",
            content=ARC_PATTERN_INSTRUCTION_TEMPLATE.format(
                training_examples="See SOLVED LEVELS section above.",
                grid_analysis=self._level_pattern.game_rule_summary if self._level_pattern else "none",
                hypothesis_section="", 
                repl_section="" 
            ),
        ))

        return packet

    def _build_execution_packet(self, observation: ARC3Observation, available_actions: List[str]) -> PromptPacket:
        """Phase 2 execution mode: paint the known solution grid. B153."""
        packet = PromptPacket()

        packet.blocks.append(ContentBlock(
            type="SYSTEM",
            content=ARC_EXECUTION_SYSTEM_PROMPT,
        ))

        # Show target grid and current grid
        target = self._verified_output_grid
        current = observation.get("grid") or []

        # Compute cells that still need painting (diff)
        cells_to_paint = []
        if target:
            for r in range(len(target)):
                for c in range(len(target[0])):
                    target_val = target[r][c]
                    current_val = current[r][c] if r < len(current) and c < len(current[0]) else -1
                    if target_val != current_val:
                        cells_to_paint.append(f"  ({r},{c}): {current_val} -> {target_val}")

        packet.blocks.append(ContentBlock(
            type="INSTRUCTION",
            content=ARC_EXECUTION_INSTRUCTION_TEMPLATE.format(
                target_grid=self.render_grid_compact(target) if target else "unknown",
                current_grid=self.render_grid_compact(current),
                cells_to_paint="\n".join(cells_to_paint[:20]) if cells_to_paint else "None",
                available_actions=", ".join(available_actions),
            ),
        ))

        # B212: Expose graph evidence in execution prompts if present
        graph_lines = self._format_graph_evidence_section(self._hypothesis_context)
        if graph_lines:
            packet.blocks.append(ContentBlock(type="GRAPH_EVIDENCE", content="\n".join(graph_lines), header="GRAPH EVIDENCE"))

        return packet

    def _build_navigation_packet(
        self,
        observation: ARC3Observation,
        memory_context: dict,
        step_history: List[dict],
        available_actions: List[str],
    ) -> PromptPacket:
        """Original 15-block navigation prompt (EXISTING). B117."""
        packet = PromptPacket()

        packet.blocks.append(ContentBlock(
            type="SYSTEM",
            content=SYSTEM_PROMPT.format(available_actions=', '.join(available_actions))
        ))

        state = observation.get("state", "UNKNOWN")
        energy = observation.get("energy_estimate", 1.0)
        packet.blocks.append(ContentBlock(
            type="STATE",
            content=f"STATE: {state}  ENERGY: {energy:.0%}"
        ))

        # B120: Entity context block
        if self._entity_map:
            entity_lines = []
            for cid, info in self._entity_map.items():
                if info["role"] == "unknown":
                    continue
                line = f"Color {cid}: {info['role']} (confidence={info['confidence']:.0%})"
                if info.get("position"):
                    line += f" at row {info['position']['row']:.0f}, col {info['position']['col']:.0f}"
                entity_lines.append(line)
            if entity_lines:
                packet.blocks.append(ContentBlock(
                    type="ENTITY_CONTEXT",
                    content="\n".join(entity_lines),
                    header="ENTITY CONTEXT",
                ))

        if memory_context.get("_triggered"):
            memory_lines = self._format_memory_section(memory_context, observation, is_first_decision=not step_history)
            if memory_lines:
                packet.blocks.append(ContentBlock(type="MEMORY", content="\n".join(memory_lines)))

        fact_lines = self._format_action_fact_section(self._hypothesis_context)
        if fact_lines:
            packet.blocks.append(ContentBlock(type="ACTION_FACTS", content="\n".join(fact_lines)))

        hyp_lines = self._format_path_hypothesis_section(self._hypothesis_context)
        if hyp_lines:
            packet.blocks.append(ContentBlock(type="PATH_HYPOTHESES", content="\n".join(hyp_lines)))

        hypothesis_lines = self._format_hypothesis_section(self._hypothesis_context)
        if hypothesis_lines:
            packet.blocks.append(ContentBlock(type="HYPOTHESIS", content="\n".join(hypothesis_lines)))

        # B212: GRAPH EVIDENCE block (if available) — surface before solve context
        graph_lines = self._format_graph_evidence_section(self._hypothesis_context)
        if graph_lines:
            packet.blocks.append(ContentBlock(type="GRAPH_EVIDENCE", content="\n".join(graph_lines), header="GRAPH EVIDENCE"))

        solve_section = self._build_solve_section()
        if solve_section:
            # Solve section already has a header usually, but packet render adds one.
            # Let's clean it up to avoid double headers.
            content = solve_section.replace("=== SOLVE CONTEXT ===\n", "")
            packet.blocks.append(ContentBlock(type="SOLVE_CONTEXT", content=content))

        # B161: Directional guidance
        if self._player_position and self._goal_position:
            pr, pc = self._player_position
            gr, gc = self._goal_position
            dr = "up" if gr < pr else "down" if gr > pr else "aligned"
            dc = "left" if gc < pc else "right" if gc > pc else "aligned"
            nav_text = (
                f"Player is at approximately row {pr:.0f}, col {pc:.0f}. "
                f"Goal appears near row {gr:.0f}, col {gc:.0f}. "
                f"You need to move {dr} and {dc} to reach it."
            )
            packet.blocks.append(ContentBlock(type="NAVIGATION", content=nav_text, header="NAVIGATION GUIDANCE"))

        effect_lines = self._format_effect_section(self._hypothesis_context)
        
        # B161: ACTION5 effect logging
        if self._last_interact_effect:
            le = self._last_interact_effect
            effect_lines.insert(0, f"ACTION5 (interact) caused a major change: {le['pixels_changed']} pixels, new colors: {le['new_colors']}")

        if effect_lines:
            packet.blocks.append(ContentBlock(type="OBSERVED_EFFECTS", content="\n".join(effect_lines)))

        # EXPLORATION_SUMMARY from B116
        compaction_text = self._format_compaction_section()
        if compaction_text:
            packet.blocks.append(ContentBlock(type="EXPLORATION_SUMMARY", content=compaction_text))

        reflex_lines = self._format_reflex_section()
        if reflex_lines:
            packet.blocks.append(ContentBlock(type="REFLEX", content="\n".join(reflex_lines)))

        plan_lines = self._format_plan_section()
        packet.blocks.append(ContentBlock(type="PLAN", content="\n".join(plan_lines)))

        history_text = self._format_history_section(step_history)
        
        # B161: Movement history summary
        movement_summary = self._build_movement_summary()
        if movement_summary:
            history_text = f"MOVEMENT SUMMARY:\n{movement_summary}\n\nSTEP HISTORY:\n{history_text}"

        packet.blocks.append(ContentBlock(type="HISTORY", content=history_text))

        packet.blocks.append(ContentBlock(
            type="OBSERVATION",
            content=self._format_observation_section(observation)
        ))


        # B110: INSTRUCTION should not duplicate effect summary (already in OBSERVED EFFECTS)
        instruction_text = self._format_instruction_section(self._hypothesis_context)
        packet.blocks.append(ContentBlock(
            type="INSTRUCTION",
            content=instruction_text
        ))

        # Apply B110 suppression and other transformations
        self._apply_packet_transformations(packet, observation)

        return packet

    def build_action_prompt(
        self,
        observation: ARC3Observation,
        memory_context: dict,
        step_history: List[dict],
        available_actions: List[str],
    ) -> str:
        """Render the final prompt string from a packet. B117"""
        packet = self.build_action_packet(observation, memory_context, step_history, available_actions)
        return packet.render()

    def _apply_packet_transformations(self, packet: PromptPacket, observation: ARC3Observation) -> None:
        """B110/B117: Apply programmatic transformations like observation suppression and deduplication."""
        obs_block = packet.get_block("OBSERVATION")
        effects_block = packet.get_block("OBSERVED_EFFECTS")
        instruction_block = packet.get_block("INSTRUCTION")

        # B110 logic: suppress OBSERVATION section if OBSERVED EFFECTS provides sufficient board context
        if obs_block and effects_block:
            effects_content = effects_block.content
            # Check if OBSERVED EFFECTS has rich board transition information
            has_board_context = (
                "Before board" in effects_content or
                "After board" in effects_content or
                "Changed region" in effects_content
            )

            if has_board_context:
                # Suppress the OBSERVATION block entirely when OBSERVED EFFECTS provides board context
                obs_block.content = ""
            else:
                # If effects lack board context, keep OBSERVATION but suppress coarse map detail
                obs_lines = obs_block.content.split("\n")
                filtered_obs = [l for l in obs_lines if "Coarse map" not in l and not l.startswith("0 ") and not l.startswith("1 ")]
                if len(filtered_obs) < len(obs_lines):
                    filtered_obs.append("(coarse map suppressed; see OBSERVED EFFECTS for board context)")
                obs_block.content = "\n".join(filtered_obs)

    def set_write_trace_context(self, context: str) -> None:
        self._write_trace_context = context or "bootstrap"

    def consume_write_trace(self) -> List[dict]:
        trace = list(self._write_trace)
        self._write_trace.clear()
        return trace

    def _record_write_event(
        self,
        *,
        kind: str,
        summary: str,
        detail: dict | None = None,
        response_dict: dict | None = None,
        source_step: int | None = None,
    ) -> None:
        # Extract status from response dict, defaulting to "ok"
        status = "ok"
        if response_dict and isinstance(response_dict, dict):
            status = response_dict.get("status", "ok")

        event = {
            "phase": self._write_trace_context,
            "type": kind,
            "kind": kind,
            "status": status,
            "summary": self._compact_text(summary),
        }
        if source_step is not None:
            event["source_step"] = source_step
        if detail:
            event["detail"] = detail
        self._write_trace.append(event)

    @staticmethod
    def _compact_text(text: str, limit: int = 180) -> str:
        text = " ".join(str(text).split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"

    def _compact_fact_trace(self, facts: List[dict], limit: int = 3) -> List[dict]:
        compact: List[dict] = []
        for fact in facts[:limit]:
            compact.append(
                {
                    "id": fact.get("id"),
                    "action": fact.get("action"),
                    "fact_type": fact.get("fact_type"),
                    "value_status": fact.get("value_status"),
                    "consistency": fact.get("consistency"),
                    "evidence_count": fact.get("evidence_count"),
                    "trend": fact.get("trend"),
                    "support_steps": list(fact.get("support_steps") or [])[:4],
                    "description": self._compact_text(fact.get("description") or "", 140),
                }
            )
        return compact

    def _compact_path_trace(self, paths: List[dict], limit: int = 3) -> List[dict]:
        compact: List[dict] = []
        for path in paths[:limit]:
            compact.append(
                {
                    "actions": list(path.get("actions") or []),
                    "value_status": path.get("value_status"),
                    "confidence": path.get("confidence"),
                    "support_steps": list(path.get("support_steps") or [])[:4],
                    "description": self._compact_text(path.get("description") or "", 140),
                }
            )
        return compact

    @staticmethod
    def reward_to_valence(correct: bool, steps: int, max_steps: int) -> float:
        """Map ARC result → valence [-1.0, +1.0]."""
        if not correct:
            if steps >= max_steps:
                return -0.5
            return -0.7
        ratio = 1.0 - (steps - 1) / max(max_steps - 1, 1)
        return 0.3 + 0.7 * ratio

    # ------------------------------------------------------------------

    @staticmethod
    def _parse_llm_response(raw: str, available_actions: List[str]) -> dict | None:
        """Parse an LLM response into a structured result, tolerating any output format.

        Attempts, in order:
        1. Direct JSON parse
        2. Extract JSON object from surrounding text (```json blocks, prose, etc.)
        3. Plain text extraction — look for action references in natural language

        Returns a dict that always includes `parse_method` when parsing succeeds.
        If structured JSON was recovered, `_parsed` is included even when no final
        `action_id` is present yet (for example `sandbox_thought` / `repl_test`).
        Returns None only when nothing usable was found.
        """
        if not raw or not raw.strip():
            return None

        text = raw.strip()

        # --- Tier 1: Direct JSON ---
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                result = {
                    "parse_method": "json_direct",
                    "_parsed": parsed,
                }
                action_id = parsed.get("action_id") or parsed.get("action")
                if action_id is not None:
                    result["action_id"] = str(action_id)
                    result["rationale"] = parsed.get("rationale") or parsed.get("why") or ""
                return result
        except (json.JSONDecodeError, TypeError):
            pass

        # --- Tier 2: Extract JSON from text (markdown blocks, prose wrapping) ---
        # Try ```json ... ``` blocks first
        code_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if code_block:
            try:
                parsed = json.loads(code_block.group(1))
                if isinstance(parsed, dict):
                    result = {
                        "parse_method": "json_code_block",
                        "_parsed": parsed,
                    }
                    action_id = parsed.get("action_id") or parsed.get("action")
                    if action_id is not None:
                        result["action_id"] = str(action_id)
                        result["rationale"] = parsed.get("rationale") or parsed.get("why") or ""
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        # Try any { ... } in the text
        json_match = re.search(r"(\{[^{}]*\})", text, re.DOTALL)
        if json_match:
            try:
                parsed = json.loads(json_match.group(1))
                if isinstance(parsed, dict):
                    result = {
                        "parse_method": "json_extracted",
                        "_parsed": parsed,
                    }
                    action_id = parsed.get("action_id") or parsed.get("action")
                    if action_id is not None:
                        result["action_id"] = str(action_id)
                        result["rationale"] = parsed.get("rationale") or parsed.get("why") or ""
                    return result
            except (json.JSONDecodeError, TypeError):
                pass

        # --- Tier 3: Plain text extraction ---
        text_upper = text.upper()

        # Look for explicit "ACTION<N>" mentions
        action_mentions = re.findall(r"ACTION\s*(\d+)", text_upper)
        if action_mentions:
            # Take the last mentioned action (usually the conclusion after reasoning)
            candidate = f"ACTION{action_mentions[-1]}"
            if candidate in available_actions:
                return {
                    "action_id": candidate,
                    "rationale": text[:200],
                    "parse_method": "plain_text_action_mention",
                }

        # Look for directional words mapping to known actions
        direction_map = {
            "UP": "ACTION1", "MOVE UP": "ACTION1", "GO UP": "ACTION1", "NORTH": "ACTION1",
            "DOWN": "ACTION2", "MOVE DOWN": "ACTION2", "GO DOWN": "ACTION2", "SOUTH": "ACTION2",
            "LEFT": "ACTION3", "MOVE LEFT": "ACTION3", "GO LEFT": "ACTION3", "WEST": "ACTION3",
            "RIGHT": "ACTION4", "MOVE RIGHT": "ACTION4", "GO RIGHT": "ACTION4", "EAST": "ACTION4",
            "INTERACT": "ACTION5", "SELECT": "ACTION5", "USE": "ACTION5", "PRESS": "ACTION5",
            "CLICK": "ACTION6", "PAINT": "ACTION6", "PLACE": "ACTION6", "COORDINATE": "ACTION6",
            "UNDO": "ACTION7", "REVERSE": "ACTION7",
        }
        # Check longest phrases first
        for phrase in sorted(direction_map, key=len, reverse=True):
            if phrase in text_upper:
                candidate = direction_map[phrase]
                if candidate in available_actions:
                    return {
                        "action_id": candidate,
                        "rationale": text[:200],
                        "parse_method": "plain_text_direction",
                    }

        # Look for bare numbers that could be action IDs ("try 3", "I choose 1")
        bare_numbers = re.findall(r"\b(\d)\b", text)
        if bare_numbers:
            candidate = f"ACTION{bare_numbers[-1]}"
            if candidate in available_actions:
                return {
                    "action_id": candidate,
                    "rationale": text[:200],
                    "parse_method": "plain_text_bare_number",
                }

        return None

    @staticmethod
    def _normalize_action_id(action_id: Any) -> str | None:
        if action_id is None:
            return None
        text = str(action_id).strip().upper()
        if not text:
            return None
        if text.isdigit():
            return f"ACTION{text}"
        return text

    @staticmethod
    def _coerce_action6_coordinate(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            coordinate = int(value)
        except (TypeError, ValueError):
            return None
        return max(0, min(63, coordinate))

    @staticmethod
    def _manhattan_dist(c1: tuple[int, int], c2: tuple[int, int]) -> int:
        return abs(c1[0] - c2[0]) + abs(c1[1] - c2[1])

    def _coords_along_vector(self, start: tuple[int, int], end: tuple[int, int], margin: int = 2) -> List[tuple[int, int]]:
        """Generate coordinates on or near the line between start and end (B143)."""
        coords = []
        x0, y0 = start
        x1, y1 = end
        
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy
        
        curr_x, curr_y = x0, y0
        while True:
            # For each point on the line, add a small 2x2 or 3x3 expansion based on margin
            for dx_m in range(-margin, margin + 1):
                for dy_m in range(-margin, margin + 1):
                    mx, my = curr_x + dx_m, curr_y + dy_m
                    if 0 <= mx <= 63 and 0 <= my <= 63:
                        coords.append((mx, my))
            
            if curr_x == x1 and curr_y == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                curr_x += sx
            if e2 < dx:
                err += dx
                curr_y += sy
        
        # Deduplicate and limit
        seen = set()
        unique = []
        for c in coords:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        return unique[:50]

    def _apply_momentum_bias(self, candidates: List[tuple[str, tuple[int, int]]], recent_deltas: List[tuple[float, float]]) -> List[tuple[str, tuple[int, int]]]:
        """Re-sort candidates based on alignment with recent movement direction (B143)."""
        if len(recent_deltas) < 2:
            return candidates
            
        avg_dx = sum(d[0] for d in recent_deltas) / len(recent_deltas)
        avg_dy = sum(d[1] for d in recent_deltas) / len(recent_deltas)
        
        if abs(avg_dx) < 0.3 and abs(avg_dy) < 0.3:
            return candidates
            
        # Re-sort within tiers: dot product with avg_delta
        # We group by tier, sort within tier, then re-flatten
        tiers: Dict[str, List[tuple[int, int]]] = {}
        for tier, coord in candidates:
            if tier not in tiers:
                tiers[tier] = []
            tiers[tier].append(coord)
            
        final = []
        for tier_name in ["goal_vector", "distance_reduce", "fallback"]:
            if tier_name in tiers:
                tier_coords = tiers[tier_name]
                # Sort by alignment with (avg_dx, avg_dy)
                # But wait, coordinate is ABSOLUTE, momentum is RELATIVE.
                # Momentum bias means if we are moving RIGHT, we prefer coords to the RIGHT of current.
                # This requires current player pos. For now, let's skip re-sorting tiers 
                # and just add a "momentum" tier at the very top if we find a strong direction.
                pass
        return candidates

    def _is_cluster_exhausted(self, coord: tuple[int, int], recent_attempts: List[dict], min_count: int = 3) -> bool:
        """B143: Detect if a 3x3 region has already been failed multiple times."""
        if len(recent_attempts) < min_count:
            return False
            
        nearby = []
        for a in recent_attempts[-10:]: # Look back a bit further to catch clusters
            ax = self._coerce_action6_coordinate(a.get("x"))
            ay = self._coerce_action6_coordinate(a.get("y"))
            reward = a.get("reward")
            if ax is not None and ay is not None and reward == 0.0:
                if abs(ax - coord[0]) <= 1 and abs(ay - coord[1]) <= 1:
                    nearby.append(a)
                    
        return len(nearby) >= min_count

    def _candidate_action6_coordinates(self, observation: ARC3Observation) -> List[tuple[str, tuple[int, int]]]:
        """Build prioritized candidate list for ACTION6 (B143)."""
        grid = observation.get("grid") or []
        if not grid or not isinstance(grid, list) or not isinstance(grid[0], list) or not grid[0]:
            return [("fallback", (0, 0))]

        rows = len(grid)
        cols = len(grid[0])
        
        # Check for space/reach_goal geometry
        sc = self._solve_context or {}
        is_space_goal = (
            sc.get("archetype") == "space" 
            and (sc.get("victory_condition") or {}).get("type") == "reach_goal"
        )
        
        player_pos = None
        goal_pos = None
        if is_space_goal:
            roles = sc.get("object_roles") or {}
            best_player: tuple[float, tuple[int, int]] | None = None
            best_goal: tuple[float, tuple[int, int]] | None = None
            for rdata in roles.values():
                pos = rdata.get("estimated_position")
                if not pos:
                    continue
                conf = float(rdata.get("confidence", 0) or 0.0)
                coord = (int(pos["col"]), int(pos["row"]))
                if rdata.get("role") == "player":
                    if best_player is None or conf > best_player[0]:
                        best_player = (conf, coord)
                if rdata.get("role") == "goal":
                    if best_goal is None or conf > best_goal[0]:
                        best_goal = (conf, coord)

            if best_player and best_player[0] >= 0.7:
                player_pos = best_player[1]
            elif best_player and best_goal and min(best_player[0], best_goal[0]) >= 0.35:
                # Bootstrap fallback: keep geometry alive even before motion evidence is strong.
                player_pos = best_player[1]

            if best_goal and best_goal[0] >= 0.7:
                goal_pos = best_goal[1]
            elif best_goal and (player_pos is not None or best_goal[0] >= 0.35):
                goal_pos = best_goal[1]

        candidates: List[tuple[str, tuple[int, int]]] = []
        seen: set[tuple[int, int]] = set()

        # Tier 1: Goal Vector
        if player_pos and goal_pos:
            for c in self._coords_along_vector(player_pos, goal_pos, margin=1):
                if c not in seen:
                    seen.add(c)
                    candidates.append(("goal_vector", c))

        # Tier 2: Distance Reduction (non-background pixels sorted by goal proximity)
        counts: dict[int, int] = {}
        for row in grid:
            for cell in row:
                value = int(cell)
                counts[value] = counts.get(value, 0) + 1
        background = max(counts.items(), key=lambda item: item[1])[0] if counts else 0

        non_background = [
            (x, y)
            for y, row in enumerate(grid)
            for x, cell in enumerate(row)
            if int(cell) != background
        ]
        
        if goal_pos:
            non_background_sorted = sorted(non_background, key=lambda c: self._manhattan_dist(c, goal_pos))
            for c in non_background_sorted:
                if c not in seen:
                    seen.add(c)
                    candidates.append(("distance_reduce", c))
        else:
            for c in non_background:
                if c not in seen:
                    seen.add(c)
                    candidates.append(("non_background", c))

        # Tier 3: Original fallback
        center = (max(0, min(63, cols // 2)), max(0, min(63, rows // 2)))
        corners = [
            (0, 0),
            (max(0, min(63, cols - 1)), 0),
            (0, max(0, min(63, rows - 1))),
            (max(0, min(63, cols - 1)), max(0, min(63, rows - 1))),
        ]
        for c in [center] + corners:
            norm = (max(0, min(63, int(c[0]))), max(0, min(63, int(c[1]))))
            if norm not in seen:
                seen.add(norm)
                candidates.append(("fallback", norm))

        return candidates or [("fallback", (0, 0))]

    def _infer_action6_coordinates(self, observation: ARC3Observation) -> tuple[int, int]:
        """B143: Smarter coordinate selection with anti-clustering."""
        candidates_with_meta = self._candidate_action6_coordinates(observation)
        action6_attempts = [
            step for step in self._step_history
            if self._normalize_action_id(step.get("action_id")) == "ACTION6"
        ]
        # Puzzle-specific rotate-cross heuristic removed (B213): no direct coordinate inference here.
        recent_failed_coords = [
            (x, y)
            for step in action6_attempts[-12:]
            for x, y in [
                (
                    self._coerce_action6_coordinate(step.get("x")),
                    self._coerce_action6_coordinate(step.get("y")),
                )
            ]
            if x is not None and y is not None and float(step.get("reward") or 0.0) <= 0.0
        ]
        used_coords = {
            (x, y)
            for step in action6_attempts
            for x, y in [
                (
                    self._coerce_action6_coordinate(step.get("x")),
                    self._coerce_action6_coordinate(step.get("y")),
                )
            ]
            if x is not None and y is not None
        }

        # If ACTION6 is the only legal action and we are plateauing, prefer a
        # larger jump over local row-by-row sweeps.
        if self._consecutive_no_progress_steps >= 3 and len(recent_failed_coords) >= 3:
            anchor = recent_failed_coords[-1]
            best_candidate: tuple[str, tuple[int, int]] | None = None
            best_score: tuple[int, int, int] | None = None
            for tier, coord in candidates_with_meta:
                if coord in used_coords:
                    continue
                if self._is_cluster_exhausted(coord, action6_attempts):
                    continue
                min_recent_dist = min(
                    self._manhattan_dist(coord, prev) for prev in recent_failed_coords
                )
                dist_from_anchor = self._manhattan_dist(coord, anchor)
                row_change = 1 if coord[1] != anchor[1] else 0
                score = (row_change, min_recent_dist, dist_from_anchor)
                if best_score is None or score > best_score:
                    best_score = score
                    best_candidate = (tier, coord)

            if best_candidate is not None:
                tier, coord = best_candidate
                self._emit_trace_event(
                    "operation",
                    "coordinate_policy",
                    {"policy": "stagnation_escape", "tier": tier, "coord": coord},
                    {
                        "no_progress_steps": self._consecutive_no_progress_steps,
                        "recent_failures": len(recent_failed_coords),
                    },
                )
                return coord

        # B143: Policy tracing and anti-clustering
        policy = "default"
        if any(tier == "goal_vector" for tier, _ in candidates_with_meta):
            policy = "goal_directed"

        for tier, coord in candidates_with_meta:
            if coord in used_coords:
                continue
            
            # Anti-clustering skip
            if self._is_cluster_exhausted(coord, action6_attempts):
                self._emit_trace_event("operation", "coordinate_policy_skip", 
                                      {"coord": coord, "tier": tier}, 
                                      {"reason": "cluster_exhausted"})
                continue
                
            # Found a good one
            self._emit_trace_event("operation", "coordinate_policy", 
                                  {"policy": policy, "tier": tier, "coord": coord},
                                  {"total_candidates": len(candidates_with_meta)})
            return coord

        # Emergency fallback: just use first unused or absolute first
        for _, coord in candidates_with_meta:
            if coord not in used_coords:
                return coord
        return candidates_with_meta[0][1]

    def _ensure_action6_coordinates(self, action: ARC3Action, observation: ARC3Observation) -> ARC3Action:
        normalized_id = self._normalize_action_id(action.get("action_id"))
        if normalized_id != "ACTION6":
            if normalized_id and normalized_id != action.get("action_id"):
                updated = dict(action)
                updated["action_id"] = normalized_id
                return updated
            return action

        updated = dict(action)
        updated["action_id"] = normalized_id
        x = self._coerce_action6_coordinate(updated.get("x"))
        y = self._coerce_action6_coordinate(updated.get("y"))
        inferred = x is None or y is None
        if inferred:
            x, y = self._infer_action6_coordinates(observation)
        updated["x"] = x
        updated["y"] = y

        rationale = str(updated.get("rationale") or "ACTION6 coordinate probe")
        coord_note = f"x={x}, y={y}"
        if coord_note not in rationale:
            prefix = f"{rationale}; " if rationale else ""
            reason = "targeting inferred coord" if inferred else "targeting coord"
            updated["rationale"] = f"{prefix}{reason} ({coord_note})"
        return updated

    async def _query_llm(self, prompt: str, available_actions: List[str]) -> ARC3Action:
        if not self.llm:
            return {"action_id": available_actions[0], "rationale": "system fallback"}
        messages = [
            {"role": "system", "content": QUERY_LLM_SYSTEM_MESSAGE},
            {"role": "user", "content": prompt},
        ]
        try:
            try:
                raw = await asyncio.to_thread(
                    self.llm.chat, messages,
                    response_format={"type": "json_object"},
                )
            except TypeError:
                # Provider doesn't support response_format (e.g. mock LLMs)
                raw = await asyncio.to_thread(self.llm.chat, messages)
                self._record_llm_usage()
            
            # B180: Record usage for successful try block
            self._record_llm_usage()
            payload = getattr(raw, "content", raw)

            # Robust multi-tier parsing: JSON → embedded JSON → plain text
            result = self._parse_llm_response(payload, available_actions)

            if result is None or not result.get("action_id"):
                logger.warning("LLM response unparseable: %s", (payload or "")[:150])
                return {"action_id": available_actions[0], "rationale": f"unparseable LLM response: {(payload or '')[:100]}"}

            action_id = self._normalize_action_id(result["action_id"])
            rationale = result.get("rationale") or "llm response"
            parse_method = result.get("parse_method", "unknown")

            if parse_method != "json_direct":
                logger.info("LLM response parsed via %s: %s", parse_method, action_id)

            if action_id not in available_actions:
                fallback = available_actions[0]
                logger.warning(
                    "LLM selected unavailable action %r; falling back to %r. Available=%s",
                    action_id,
                    fallback,
                    available_actions,
                )
                return {
                    "action_id": fallback,
                    "rationale": f"Invalid LLM action {action_id!r}; fallback to {fallback}. Original rationale: {rationale}",
                }

            action: ARC3Action = {
                "action_id": action_id,
                "rationale": rationale,
            }
            parsed = result.get("_parsed") or {}
            x = self._coerce_action6_coordinate(parsed.get("x"))
            y = self._coerce_action6_coordinate(parsed.get("y"))
            if x is not None:
                action["x"] = x
            if y is not None:
                action["y"] = y
            return action
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("LLM action parse failed: %s", exc)
            return {"action_id": available_actions[0], "rationale": f"parse error: {exc}"}

    @staticmethod
    def _should_skip_chunk_action(effect: dict | None) -> bool:
        """Return True when the evidence says a chunk action has gone stale."""
        if not effect:
            return False

        value_status = str(effect.get("value_status") or "").lower()
        last_label = str(effect.get("last_meaningful_label") or "").lower()
        zero_reward_streak = int(effect.get("zero_reward_streak") or 0)
        no_progress_count = int(effect.get("no_progress_count") or 0)
        avg_change = float(effect.get("avg_meaningful_change") or 0.0)
        rank_score = float(effect.get("rank_score") or 0.0)

        if effect.get("over_retest_budget"):
            return True
        if value_status in {"low_value", "ineffective"}:
            return True
        if last_label in {"low_value", "no_progress"} and zero_reward_streak >= 2:
            return True
        if zero_reward_streak >= 3 and (no_progress_count > 0 or avg_change < 0.45 or rank_score < 0.20):
            return True
        return False

    def _max_exploration_for_level(self) -> int:
        """B154: How many forced exploration steps this level gets."""
        confidence = getattr(self, '_rule_confidence', 0.0)
        level = getattr(self, '_current_level', 0)

        # High-confidence rule: zero exploration
        if confidence > 0.8:
            return 0

        # Level-progressive
        if level <= 1:
            return 5  # Tutorial level — full exploration
        elif level <= 3:
            return 2  # Early levels — verify carryover
        else:
            # Late levels: only if unknown actions remain
            n_known = len(getattr(self, 'observed_action_effects', {}))
            n_total = len(getattr(self, '_available_actions', []))
            return 1 if n_known < n_total else 0

    def _enforce_action_policy(
        self,
        action: ARC3Action,
        available_actions: List[str],
        current_frame_hash: str | None = None,
        observation: ARC3Observation | None = None,
    ) -> ARC3Action:
        """Apply hard exploration guards and chunk enforcement (B109/B112/B133)."""
        hyp_ctx = self._hypothesis_context or {}
        coverage = hyp_ctx.get("action_coverage") or {}
        unexplored = [
            candidate for candidate in coverage.get("untested_actions", [])
            if candidate in available_actions
        ]
        observed_effects = {
            effect.get("action"): effect
            for effect in hyp_ctx.get("observed_action_effects", [])
            if effect.get("action")
        }
        self.observed_action_effects = observed_effects
        self._available_actions = available_actions

        action_id = action.get("action_id")
        rationale = action.get("rationale") or ""
        source = action.get("decision_source", "llm")
        active_chunk = (self._solve_context or {}).get("active_chunk")
        skip_chunk_enforcement = False  # Set True below for fallback decisions

        # B209: Route->Execute adherence contract.
        # If route provided an expected action, enforce it unless we have an explicit override class.
        expected_action = (self._solve_context or {}).get("expected_action")
        explicit_override_sources = {
            "autopilot",
            "policy_override",
            "guard_override",
            "guard_blocked_fallback",
            "fatigue_override",
            "plateau_override",
            "phase2_execution",
        }
        relax_adherence = bool(
            self._consecutive_no_progress_steps >= 2
            or hyp_ctx.get("loop_detected")
            or (coverage.get("top_two_low_value") is True)
        )
        if expected_action and expected_action in available_actions and action_id:
            if action_id != expected_action and source not in explicit_override_sources and not relax_adherence:
                self._emit_trace_event(
                    "operation",
                    "route_execute_adherence_enforced",
                    {"selected_action": action_id, "expected_action": expected_action},
                    {"reason": "missing_explicit_override_reason"},
                )
                action.update(
                    {
                        "action_id": expected_action,
                        "rationale": (
                            f"policy override: route expected {expected_action}; "
                            f"realigning from {action_id}. Original rationale: {rationale}"
                        ),
                        "decision_source": "policy_override",
                        "expected_action": expected_action,
                        "selected_action": action_id,
                        "override_reason": "missing_explicit_override_reason",
                        "adherence_ok": False,
                    }
                )
                return action
            if action_id != expected_action and source not in explicit_override_sources and relax_adherence:
                action["expected_action"] = expected_action
                action["selected_action"] = action_id
                action["override_reason"] = "stagnation_relaxation"
                action["adherence_ok"] = False
            if action_id != expected_action and source in explicit_override_sources:
                action["expected_action"] = expected_action
                action["selected_action"] = action_id
                action["override_reason"] = action.get("override_reason") or source
                action["adherence_ok"] = False
            elif action_id == expected_action:
                action["expected_action"] = expected_action
                action["selected_action"] = action_id
                action["adherence_ok"] = True

        # Rotation-specific heuristic removed (B213): do not inject puzzle-specific overrides here.

        # B166: Autopilot decisions have highest authority. Preserve geometry-
        # driven navigation here so sparse-reward movement puzzles can repeat the
        # same directional action across changing frames without being rotated away
        # by generic stale/low-value guards.
        if source == "autopilot":
            if current_frame_hash:
                self._action_frame_hashes[action_id] = current_frame_hash
            return action

        # Exploration-intent bypass (B213): never override when LLM explicitly intends to explore.
        _exploration_keywords = (
            "haven't tried", "not yet tried", "new action", "unexplored",
            "never tried", "hasn't been tried", "want to see",
        )
        _is_exploration_intent = (
            action_id in unexplored or any(kw in rationale.lower() for kw in _exploration_keywords)
        )
        if _is_exploration_intent:
            # NEW: Still consume if it matches the current chunk head (B112 fix)
            if active_chunk and active_chunk.get("estimated_actions"):
                suggested = active_chunk["estimated_actions"]
                if action_id == suggested[0]:
                    if self.solve_engine._active_chunk and self.solve_engine._active_chunk.estimated_actions:
                        self.solve_engine._active_chunk.estimated_actions.pop(0)
            
            try:
                self._emit_trace_event(
                    "operation",
                    "guard_exploration_bypass",
                    {"action": action_id, "source": source},
                    {"reason": "LLM chose exploration; bypassing decay guard"},
                )
            except Exception:
                pass
            return action

        chosen_effect = observed_effects.get(action_id)
        allow_repeat_probe = self._normalize_action_id(action_id) == "ACTION6"
        if action_id in available_actions and self._should_skip_chunk_action(chosen_effect) and not allow_repeat_probe:
            replacement = None

            if active_chunk and active_chunk.get("estimated_actions"):
                for candidate in active_chunk["estimated_actions"]:
                    if candidate == action_id or candidate not in available_actions:
                        continue
                    if not self._should_skip_chunk_action(observed_effects.get(candidate)):
                        replacement = candidate
                        break

            if replacement is None:
                for candidate in unexplored:
                    if candidate != action_id:
                        replacement = candidate
                        break

            ranked_effects = [
                effect for effect in hyp_ctx.get("observed_action_effects", [])
                if effect.get("action") in available_actions and effect.get("action") != action_id
            ]
            viable_ranked = [
                effect for effect in ranked_effects
                if not self._should_skip_chunk_action(effect)
            ]
            if replacement is None and viable_ranked:
                viable_ranked = sorted(
                    viable_ranked,
                    key=lambda effect: (
                        -float(effect.get("rank_score", 0.0)),
                        effect.get("times_seen", 0),
                        effect.get("action", ""),
                    ),
                )
                replacement = viable_ranked[0].get("action")

            if replacement is None:
                alternatives = [candidate for candidate in available_actions if candidate != action_id]
                if alternatives:
                    replacement = min(
                        alternatives,
                        key=lambda candidate: (self._action_fatigue.get(candidate, 0), candidate),
                    )

            if replacement:
                self._emit_trace_event(
                    "operation",
                    "guard_override_reason",
                    {"original": action_id, "override": replacement},
                    {
                        "reason": "stale low-value/no-progress evidence",
                        "value_status": (chosen_effect or {}).get("value_status"),
                        "zero_reward_streak": (chosen_effect or {}).get("zero_reward_streak"),
                    },
                )
                action.update({
                    "action_id": replacement,
                    "rationale": (
                        f"policy override: stale low-value {action_id} is still decaying; "
                        f"switching to {replacement}. Original rationale: {rationale}"
                    ),
                    "decision_source": "policy_override",
                })
                return action

        # B141: Blocked action enforcement
        if action_id in self._blocked_actions:
            if unexplored:
                forced = unexplored[0]
                self._emit_trace_event("operation", "guard_override_reason", 
                    {"original": action_id, "override": forced},
                    {"reason": "action blocked due to persistent no-progress"}
                )
                action.update({
                    "action_id": forced,
                    "rationale": f"policy override: {action_id} is blocked due to persistent no-progress; forcing exploration of {forced}.",
                    "decision_source": "policy_override",
                })
                return action

        # B133 revised: When LLM failed (fallback), skip chunk enforcement but
        # still apply exploration, ranking, and plateau policies so the agent
        # doesn't blindly default to ACTION1 on every step.
        skip_chunk_enforcement = source == "mental_sandbox_fallback"
        if skip_chunk_enforcement:
            self._emit_trace_event("operation", "guard_fallback_policy_applied", {"reason": "LLM produced fallback; applying exploration/ranking policies"}, {"action_id": action_id})

        # B109/B112: Prioritize guidance-grade chunk actions (bfs, directional)
        # Skip chunk enforcement when LLM produced a fallback — let exploration/ranking take over
        if active_chunk and active_chunk.get("estimated_actions") and not skip_chunk_enforcement:
            chunk_source = active_chunk.get("source", "unknown")
            suggested = active_chunk["estimated_actions"]

            # B112: Only hard-enforce guidance-grade sources (bfs, directional).
            if chunk_source == "bfs":
                first_planned = suggested[0] if suggested else None
                if first_planned and first_planned in available_actions and not self._should_skip_chunk_action(observed_effects.get(first_planned)):
                    chunk_action = first_planned
                    if self.solve_engine._active_chunk and self.solve_engine._active_chunk.estimated_actions:
                        try:
                            self.solve_engine._active_chunk.estimated_actions.remove(chunk_action)
                        except ValueError:
                            pass
                    if action_id != chunk_action:
                        action.update({
                            "action_id": chunk_action,
                            "rationale": f"policy override: enforcing bfs chunk '{active_chunk.get('description', '')}'. Original rationale: {rationale}",
                            "decision_source": "policy_override",
                        })
                        return action
                    return action

            elif chunk_source == "directional":
                valid_suggested = [a for a in suggested if a in available_actions]
                viable_suggested = [
                    candidate for candidate in valid_suggested
                    if not self._should_skip_chunk_action(observed_effects.get(candidate))
                ]
                if viable_suggested:
                    chunk_action = viable_suggested[0]
                    if self.solve_engine._active_chunk and self.solve_engine._active_chunk.estimated_actions:
                        chunk_list = self.solve_engine._active_chunk.estimated_actions
                        try:
                            idx = chunk_list.index(chunk_action)
                            del chunk_list[:idx + 1]
                        except ValueError:
                            pass
                    if action_id != chunk_action:
                        action.update({
                            "action_id": chunk_action,
                            "rationale": f"policy override: enforcing directional chunk '{active_chunk.get('description', '')}'. Original rationale: {rationale}",
                            "decision_source": "policy_override",
                        })
                        return action
                    return action
                elif valid_suggested and self.solve_engine._active_chunk and self.solve_engine._active_chunk.estimated_actions:
                    chunk_list = self.solve_engine._active_chunk.estimated_actions
                    while chunk_list and (
                        chunk_list[0] not in available_actions
                        or self._should_skip_chunk_action(observed_effects.get(chunk_list[0]))
                    ):
                        chunk_list.pop(0)
            else:
                if action_id == suggested[0]:
                    if self.solve_engine._active_chunk and self.solve_engine._active_chunk.estimated_actions:
                        self.solve_engine._active_chunk.estimated_actions.pop(0)

        # B133: Enhanced repetition gate.
        # Only override if same action AND same board state.
        if current_frame_hash and action_id in self._action_frame_hashes:
            if current_frame_hash == self._action_frame_hashes[action_id]:
                # Genuine repetition on identical frame -> override justified.
                if unexplored:
                    forced = unexplored[0]
                    self._emit_trace_event("operation", "guard_override_reason", 
                        {"original": action_id, "override": forced},
                        {"reason": "repeated action on identical frame state", "frame_hash": current_frame_hash[:12]}
                    )
                    action.update({
                        "action_id": forced,
                        "rationale": f"policy override: repeated {action_id} on same frame; forcing exploration of {forced}.",
                        "decision_source": "policy_override",
                    })
                    return action
            else:
                # LLM picked a tested action but frame state CHANGED. 
                # This is a valid 're-test' in a new context. Skip exploration override.
                return action

        # B154: ARC Exploration Policy Relaxation
        base_max = self._max_exploration_for_level()
        mult = getattr(self, "_exploration_budget_multiplier", 1.0) or 1.0
        max_explore = int(base_max * mult)
        if max_explore != base_max:
            try:
                self._emit_trace_event("operation", "gap_aware_budget", {"base_max": base_max, "multiplier": mult}, {"adjusted_max": max_explore})
            except Exception:
                pass
        should_force_explore = (
            unexplored
            and action_id not in unexplored
            and self._forced_exploration_count < max_explore
        )

        # On level 1, always explore if budget allows
        # On later levels, only explore when stuck
        if self._current_level > 1 and should_force_explore:
            should_force_explore = self._consecutive_no_progress_steps >= 1

        # Check if this action was already tested in a prior level
        if action_id in self.observed_action_effects:
            # Already know what this action does — don't force re-exploration
            unexplored = [a for a in unexplored if a not in self.observed_action_effects]
            if not unexplored:
                should_force_explore = False

        if should_force_explore:
            forced = unexplored[0]
            self._forced_exploration_count += 1
            self._total_forced_exploration += 1
            
            # Consume exploration chunk if it matches
            if active_chunk and active_chunk.get("source") == "explore" and active_chunk.get("estimated_actions"):
                if forced == active_chunk["estimated_actions"][0]:
                    if self.solve_engine._active_chunk and self.solve_engine._active_chunk.estimated_actions:
                        self.solve_engine._active_chunk.estimated_actions.pop(0)

            action.update({
                "action_id": forced,
                "rationale": f"exploration step {self._forced_exploration_count}/{max_explore} (level {self._current_level})",
                "decision_source": "policy_override",
            })
            return action

        ranked_effects = [
            effect for effect in hyp_ctx.get("observed_action_effects", [])
            if effect.get("action") in available_actions
        ]

        if coverage.get("initial_exploration_complete") and ranked_effects:
            preferred = self._select_ranked_action(ranked_effects)
            if preferred and action_id != preferred:
                action.update({
                    "action_id": preferred,
                    "rationale": f"policy override: post-exploration ranking prefers {preferred} over {action_id}. Original rationale: {rationale}",
                    "decision_source": "policy_override",
                })
                return action

        # B144/B145/B146: Plateau-aware exploitation policy
        # If the solve engine detected a plateau, we restrict choice to top ranked families.
        sc = self._solve_context or {}
        if sc.get("plateau_mode"):
            # B146: Use authoritative locked family from solve context
            locked_family = sc.get("plateau_locked_family")
            ranked = sc.get("ranked_action_families") or []
            
            # Default to the locked family if we have one
            top_family = locked_family or (ranked[0] if ranked else None)
            secondary = ranked[1] if (ranked and len(ranked) > 1 and ranked[1] != top_family) else None
            
            if top_family:
                # B149: Plateau fatigue escape
                top_fatigue = self._action_fatigue.get(top_family, 0)
                if top_fatigue >= self.ACTION_FATIGUE_THRESHOLD and secondary and secondary in available_actions:
                    secondary_fatigue = self._action_fatigue.get(secondary, 0)
                    if secondary_fatigue < self.ACTION_FATIGUE_THRESHOLD:
                        self._emit_trace_event(
                            "operation",
                            "plateau_fatigue_escape",
                            {
                                "locked_family": top_family,
                                "locked_fatigue": top_fatigue,
                                "escape_to": secondary,
                                "secondary_fatigue": secondary_fatigue,
                            },
                        )
                        action.update({
                            "action_id": secondary,
                            "rationale": (
                                f"plateau fatigue escape: locked family {top_family} has "
                                f"{top_fatigue} zero-reward uses; trying secondary {secondary}."
                            ),
                            "decision_source": "fatigue_override",
                        })
                        return action

                # Check if we are trying to switch away from the authoritative locked family
                # We allow switching to secondary ONLY if switch budget is available.
                is_locked_choice = (action_id == top_family)
                
                if not is_locked_choice:
                    is_secondary = (secondary and action_id == secondary)
                    
                    if is_secondary and self._exploitation_switch_budget > 0:
                        self._exploitation_switch_budget -= 1
                        self._emit_trace_event("operation", "plateau_switch_allowed", 
                                              {"action_id": action_id, "budget_remaining": self._exploitation_switch_budget},
                                              {"locked_family": top_family, "secondary": secondary})
                    elif self._exploitation_switch_budget > 0:
                        # Allow one-time drift if budget exists (e.g. LLM picking something weird but valid)
                        self._exploitation_switch_budget -= 1
                        self._emit_trace_event("operation", "plateau_switch_allowed_drift", 
                                              {"action_id": action_id, "budget_remaining": self._exploitation_switch_budget},
                                              {"locked_family": top_family})
                    else:
                        # Budget exhausted: force back to AUTHORITATIVE top family
                        self._emit_trace_event("operation", "plateau_switch_blocked", 
                                              {"action_id": action_id, "forced": top_family},
                                              {"reason": "exploitation switch budget exhausted", "plateau_family_exhausted": True, "locked_family": top_family})
                        
                        action.update({
                            "action_id": top_family,
                            "rationale": f"plateau lock: exploiting authoritative family {top_family} (switch budget exhausted). Original: {rationale}",
                            "decision_source": "plateau_override",
                        })
                        return action
                else:
                    # Consistent with authoritative lock
                    self._emit_trace_event("operation", "plateau_lock_active", 
                                          {"action_id": action_id}, 
                                          {"locked_family": top_family, "budget_remaining": self._exploitation_switch_budget})

        # B149: General action fatigue override (Exploitation rotation)
        # This catches any fatigued action that wasn't already handled by plateau escape.
        action_id = action.get("action_id")
        fatigue_count = self._action_fatigue.get(action_id, 0)
        if fatigue_count >= self.ACTION_FATIGUE_THRESHOLD:
            alternatives = [
                a for a in available_actions
                if a != action_id and self._action_fatigue.get(a, 0) < self.ACTION_FATIGUE_THRESHOLD
            ]
            if alternatives:
                best_alt = min(alternatives, key=lambda a: self._action_fatigue.get(a, 0))
                self._emit_trace_event(
                    "operation",
                    "action_fatigue_override",
                    {
                        "fatigued_action": action_id,
                        "fatigue_count": fatigue_count,
                        "replacement": best_alt,
                    },
                )
                action.update({
                    "action_id": best_alt,
                    "rationale": (
                        f"fatigue override: {action_id} reached threshold {self.ACTION_FATIGUE_THRESHOLD}; "
                        f"rotating to alternative {best_alt}."
                    ),
                    "decision_source": "fatigue_override",
                })
                return action

        return action

    def _max_exploration_for_level(self) -> int:
        """B154: Calculate forced exploration budget based on level and confidence."""
        # If high confidence rule exists, zero forced exploration
        if hasattr(self.solve_engine, '_game_rule_hypotheses') and self.solve_engine._game_rule_hypotheses:
            if self.solve_engine._game_rule_hypotheses[0].confidence >= 0.8:
                return 0

        if self._current_level == 1:
            return 5
        elif self._current_level <= 3:
            return 2
        else:
            return 1

    def _select_ranked_action(self, ranked_effects: List[dict]) -> str | None:
        allowed = [
            effect for effect in ranked_effects
            if not effect.get("over_retest_budget")
            and not self._should_skip_chunk_action(effect)
        ]
        fallback = [
            effect for effect in ranked_effects
            if not effect.get("over_retest_budget")
        ]
        pool = allowed or fallback or ranked_effects
        if not pool:
            return None
        pool = sorted(
            pool,
            key=lambda effect: (
                -float(effect.get("rank_score", 0.0)),
                effect.get("times_seen", 0),
                effect.get("action", ""),
            ),
        )
        return pool[0].get("action")

    async def _verify_candidate_action(
        self,
        action_id: str,
        rationale: str,
        observation: ARC3Observation,
        step_history: List[dict],
        hypothesis_context: dict,
    ) -> dict:
        """B126: Adversarial verification of candidate action before execution.

        Returns:
            {"approved": bool, "rejection_reason": str or None, "llm_response": str}
        """
        colors = observation.get("colors", [])
        shapes = observation.get("shapes", [])
        state = observation.get("state", "UNKNOWN")

        # Recent history summary
        recent_history_entries = []
        for step in step_history[-3:]:
            recent_history_entries.append(
                f"{step.get('action_id', 'UNKNOWN')}: reward={step.get('reward', '?')}"
            )
        recent_history = " → ".join(recent_history_entries) if recent_history_entries else "No history"

        # Sandbox context (if available from mental sandbox)
        sandbox_result = "Not used"
        thinking_trace = observation.get("_thinking_trace", [])
        if thinking_trace:
            sandbox_entry = next((t for t in thinking_trace if t.get("tool") == "sandbox_thought"), None)
            if sandbox_entry:
                sandbox_result = f"Tested {sandbox_entry.get('test_action')}: {sandbox_entry.get('result')}"

        # Loop detection
        loop_detected = hypothesis_context.get("loop_detected", False)

        # Action facts summary
        action_facts = hypothesis_context.get("action_facts", [])
        facts_for_action = [f for f in action_facts if f.get("action") == action_id]
        facts_summary = ""
        if facts_for_action:
            facts_summary = "; ".join([f"{f.get('action')} = {f.get('description')}" for f in facts_for_action[:2]])
        else:
            facts_summary = "Unknown behavior"

        # Build verifier prompt
        verifier_prompt = VERIFIER_PROMPT_TEMPLATE.format(
            action_id=action_id,
            rationale=rationale,
            state=state,
            colors=colors,
            shapes=shapes,
            recent_history=recent_history,
            sandbox_result=sandbox_result,
            loop_detected=loop_detected,
            action_facts_summary=facts_summary,
        )

        try:
            messages = [
                {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
                {"role": "user", "content": verifier_prompt},
            ]
            raw = await asyncio.to_thread(self.llm.chat, messages)
            self._record_llm_usage()
            parsed = json.loads(raw)

            approval = parsed.get("approved", True)
            rejection_reason = parsed.get("reason") if not approval else None

            return {
                "approved": approval,
                "rejection_reason": rejection_reason,
                "llm_response": raw,
            }
        except Exception as exc:
            logger.warning("Verifier call failed: %s, defaulting to approval", exc)
            return {
                "approved": True,
                "rejection_reason": None,
                "llm_response": "",
            }

    def _extract_run_lessons(
        self,
        solved: bool,
        final_observation: ARC3Observation | None = None,
    ) -> dict:
        """B165: Extract structured lessons from the just-finished run."""
        action_effects: dict[str, dict[str, Any]] = {}
        zero_effect_actions: list[str] = []
        effective_actions: list[str] = []

        for action_id, effect in (self.observed_action_effects or {}).items():
            pixels_changed = float(
                effect.get("avg_pixels_changed", effect.get("avg_meaningful_change", 0.0)) or 0.0
            )
            reward = float(effect.get("avg_reward", 0.0) or 0.0)
            times_seen = int(effect.get("times_seen", 0) or 0)
            label = str(effect.get("value_status", "unknown") or "unknown")
            summary = {
                "pixels_changed": pixels_changed,
                "reward": reward,
                "times_seen": times_seen,
                "label": label,
            }
            action_effects[action_id] = summary
            if pixels_changed <= 0:
                zero_effect_actions.append(action_id)
            else:
                effective_actions.append(action_id)

        grid = (final_observation or {}).get("grid") or []
        fingerprint = grid_characteristic_summary(grid) if grid else {}

        victory = (self._solve_context or {}).get("victory_condition") or (self._solve_context or {}).get("victory") or "unknown"
        if isinstance(victory, dict):
            victory = victory.get("type", "unknown")

        return {
            "puzzle_id": getattr(self, "_task_id", None) or (final_observation or {}).get("task_id"),
            "game_id": getattr(self, "_game_id", None),
            "outcome": "solved" if solved else "failed",
            "steps_used": len(self._step_history),
            "archetype": str((self._solve_context or {}).get("archetype", "unknown")),
            "victory_condition": str(victory),
            "action_effects": action_effects,
            "zero_effect_actions": zero_effect_actions,
            "effective_actions": effective_actions,
            "strategy_attempted": str((self._solve_context or {}).get("strategy_summary", "")),
            "puzzle_fingerprint": {
                "grid_size": f"{fingerprint.get('rows', 0)}x{fingerprint.get('cols', 0)}" if fingerprint else "0x0",
                "n_colors": fingerprint.get("n_colors", 0),
                "n_regions": fingerprint.get("n_regions", 0),
                "region_sizes": fingerprint.get("region_sizes", []),
                "symmetry": fingerprint.get("symmetry", []),
            },
        }

    def _retrieval_needed_for_prompt(self, kind: str) -> bool:
        """A012: Return True if the retrieved content for 'kind' will likely be rendered.
        
        Uses block_trace from prior step if available to know which blocks are active.
        """
        # Mapping kind to block types
        kind_to_blocks = {
            "current_truth": {"MEMORY", "ACTION_FACTS", "EXPLORATION_SUMMARY"},
            "recall_relevant_lessons": {"MEMORY", "PATH_HYPOTHESES", "HYPOTHESIS"},
            "analogical_search": {"MEMORY", "TRAINING_EXAMPLES"},
            "recall_procedures": {"PLAN", "NAVIGATION"},
        }
        
        target_blocks = kind_to_blocks.get(kind, set())
        
        # If no history yet, assume needed (bootstrap)
        if not self._step_history:
            return True
            
        # Check last prompt trace blocks
        try:
            # Look at the most recent snapshot in execution trace for prompt metadata
            for event in reversed(self._execution_trace):
                if event.get("event_type") == "operation" and event.get("operation") == "build_prompt":
                    block_trace = event.get("result", {}).get("block_trace", [])
                    if not block_trace: continue
                    
                    active_blocks = {b.get("type") for b in block_trace if not b.get("pruned")}
                    if target_blocks.intersection(active_blocks):
                        return True
                    else:
                        return False
        except Exception:
            pass
            
        return True # Default to needed if trace check fails

    def _retrieval_dedup_check(self, kind: str, query: str, step: int) -> bool:
        """A012: Return True if the exact same query for 'kind' was just performed."""
        import hashlib
        fingerprint = hashlib.sha256(query.encode()).hexdigest()
        
        last_fp, last_step = self._last_retrieval_kind_fingerprint.get(kind, (None, -1))
        
        if last_fp == fingerprint and (step - last_step) <= 1:
            # Refresh last step so the sliding window moves forward
            self._last_retrieval_kind_fingerprint[kind] = (fingerprint, step)
            return True # Duplicate
            
        self._last_retrieval_kind_fingerprint[kind] = (fingerprint, step)
        return False

    def _memory_query(self, observation: ARC3Observation) -> str:
        """B155: Build memory query from grid structural characteristics."""
        grid = observation.get("grid") or []
        if not grid:
            return "ARC puzzle transformation"

        chars = grid_characteristic_summary(grid)
        available = observation.get("available_actions") or []

        query_parts = [
            "ARC",
            f"{chars['rows']}x{chars['cols']} grid",
            f"{chars['n_colors']} colors",
            f"{len(available)} actions",
        ]
        if chars.get("symmetry"):
            for sym in chars["symmetry"]:
                query_parts.append(f"{sym} symmetry")


        return " ".join(query_parts)

    # _detect_split_map_rotate_cross removed as part of B213: puzzle-specific heuristic reverted

    def _parse_transformation_lessons(self, memories: List[Any]) -> List[GameRuleHypothesis]:
        """B155: Extract game rule hypotheses from retrieved memories."""
        hypotheses = []
        for memory in memories:
            if not isinstance(memory, dict):
                continue
            text = memory.get("text_raw", "") or memory.get("content", "")
            if not text:
                continue
            
            # Support both old and new tags
            if "ARC TRANSFORMATION LESSON" not in text and "ARC GAME STRATEGY" not in text:
                continue

            # Parse rule from the lesson text
            rule_match = re.search(r"Rule: (.+?)(?:\n|$)", text, re.IGNORECASE)
            if not rule_match:
                rule_match = re.search(r"Game rule: (.+?)(?:\n|$)", text, re.IGNORECASE)
                
            outcome_match = re.search(r"Outcome: (\w+)", text, re.IGNORECASE)
            outcome_text = outcome_match.group(1).upper() if outcome_match else "UNKNOWN"
            confidence_base = 0.7 if outcome_text == "SOLVED" else 0.3

            # Parse action semantics JSON if present
            action_semantics = {}
            sem_match = re.search(r"Action semantics: (\{.*?\})(?:\n|$)", text)
            if sem_match:
                try:
                    action_semantics = json.loads(sem_match.group(1))
                except:
                    pass

            hypotheses.append(GameRuleHypothesis(
                rule_description=rule_match.group(1),
                action_semantics=action_semantics,
                objective_description="Match target grid",
                level_strategy="Follow the rule discovered in similar past games",
                confidence=confidence_base * float(memory.get("similarity", 0.5)),
                evidence=[f"Retrieved from memory (similarity={memory.get('similarity', '?')})"],
                contradictions=[],
                source="memory",
            ))

        return hypotheses

    def _draft_plan_steps(
        self,
        observation: ARC3Observation,
        memory_context: dict,
        recall: dict,
        hypothesis_context: dict | None = None,
    ) -> List[str]:
        steps = []
        sc = self._solve_context

        if sc and sc.get("victory_condition"):
            vc = sc["victory_condition"]
            steps.append(f"Win condition: {vc['description']} (confidence={vc['confidence']:.2f})")
        else:
            steps.append("Explore game mechanics: test each action and observe effects")

        if sc and sc.get("active_chunk"):
            ch = sc["active_chunk"]
            steps.append(f"Execute chunk: {ch['description']}")
        else:
            steps.append("Gather more observations to identify game type")

        steps.append("Evaluate: did the action advance the win condition? Adjust if not.")

        for plan in recall.get("plans", [])[:2]:
            steps.append(f"Learn from {plan.get('goal')} (valence {plan.get('valence')})")
        return steps[:self.MAX_PROMPT_PLAN_STEPS]

    def _build_solve_section(self) -> str:
        sc = self._solve_context
        if not sc:
            return ""
        lines = ["=== SOLVE CONTEXT ==="]
        lines.append(f"ARCHETYPE: {sc['archetype']} (confidence={sc['archetype_confidence']:.2f})")

        roles = sc.get("object_roles") or {}
        if roles:
            lines.append("OBJECT ROLES:")
            for color_id, role_info in list(roles.items())[:5]:
                lines.append(f"  color_{color_id}: {role_info['role']} (conf={role_info['confidence']:.2f})")

        vc = sc.get("victory_condition")
        if vc:
            lines.append(f"VICTORY: {vc['type'].upper()} — {vc['description']} (conf={vc['confidence']:.2f})")

        chunk = sc.get("active_chunk")
        if chunk:
            lines.append(f"ACTIVE CHUNK: {chunk['description']} [{chunk['source']}]")
            if chunk.get("estimated_actions"):
                lines.append(f"  Suggested actions: {chunk['estimated_actions'][:6]}")
            lines.append(f"  Progress: {chunk['progress']:.2f}")

        # B124: Render chunk ledger as compact table
        ledger = sc.get("chunk_ledger") or []
        if ledger:
            lines.append("CHUNK LEDGER:")
            for entry in ledger[-8:]:  # Show last 8 entries
                status_sym = {
                    "completed": "✓",
                    "active": "→",
                    "pending": " ",
                    "failed": "✗",
                }.get(entry.get("status", "?"), "?")
                desc = entry.get("description", "")[:40]
                outcome = entry.get("outcome_summary", "")
                if outcome:
                    lines.append(f"  [{status_sym}] {desc} ({outcome})")
                else:
                    lines.append(f"  [{status_sym}] {desc}")

        if sc.get("dissonance"):
            lines.append(f"⚠ DISSONANCE: {sc['dissonance_reason']}")

        return "\n".join(lines)

    def _should_suppress_observation(self, effect_lines: List[str]) -> bool:
        """B110: Suppress OBSERVATION section if OBSERVED EFFECTS already provides board context.

        Returns True if we should skip the OBSERVATION section because OBSERVED EFFECTS
        contains sufficient board-state information (before/after snapshots, changed regions).
        """
        if not effect_lines:
            return False

        # Check if effect_lines contains board transition information
        effect_text = "\n".join(effect_lines)
        has_board_transition = (
            "Board transition:" in effect_text or
            "Before board" in effect_text or
            "Changed region" in effect_text or
            "before_snapshot" in effect_text or
            "after_snapshot" in effect_text
        )

        return has_board_transition

    def _format_instruction_section(self, hyp_ctx: dict | None) -> str:
        """B110: Instruction that refers to earlier sections instead of re-dumping effects.

        Avoids repeating the effect summary already in OBSERVED EFFECTS, but keeps
        the complete decision policy rules.
        """
        # B110: Skip effect summary since OBSERVED EFFECTS provides detailed context
        instruction = (
            "INSTRUCTION: What should you try next? "
            "Choose the next valid action based on observed effects. "
            "Start in an exploration phase: until each available action has at least one observed effect, prefer untested actions. "
            "Prefer actions with strong_progress or tentative_progress evidence. "
            "Treat no_progress evidence as a reason to switch actions unless reward improved. "
            "Use an UNTESTED action when repeated actions are low-value or looped. "
            "If the top tested actions both decay into low_value or no_progress, broaden exploration instead of bouncing between them. "
            "After 2 consecutive zero-reward tentative steps on the same action, require stronger evidence than before or switch. "
            "Do not let a memory-only first move override the current observation unless the memory clearly matches this puzzle. "
            "Do not invent human labels for actions beyond the observed effects. Treat action ids as opaque operators. "
            "Respond with JSON {\"action_id\":..., \"rationale\":...}, and make the rationale cite one observed effect label or say UNTESTED."
        )
        return instruction

    def _compose_final_prompt(self, sections: dict, observation: dict, step_history) -> str:
        """Render a pre-built sections dict into a single final prompt string (B110).

        Sections are ordered canonically. Headers are added for all sections
        except SYSTEM. Applies coarse-map suppression when OBSERVED_EFFECTS
        content is rich (>= 400 chars).
        """
        ordered_keys = [
            "SYSTEM", "STATE", "ENTITY_CONTEXT", "MEMORY", "SOLVE_CONTEXT", "PLAN",
            "OBSERVED_EFFECTS", "OBSERVATION", "ACTION_FACTS", "PATH_HYPOTHESIS", "INSTRUCTION",
        ]

        # Coarse-map suppression: when OBSERVED_EFFECTS is substantial, strip the
        # low-value coarse grid representation from OBSERVATION.
        observation_content = sections.get("OBSERVATION", "")
        if "OBSERVED_EFFECTS" in sections and len(sections["OBSERVED_EFFECTS"]) >= 400:
            coarse_idx = observation_content.find("Coarse map")
            if coarse_idx < 0:
                coarse_idx = observation_content.lower().find("coarse map")
            if coarse_idx >= 0:
                prefix = observation_content[:coarse_idx].rstrip()
                observation_content = prefix + "\n[coarse map suppressed: effects context is rich]"

        parts: list[str] = []
        seen = set()
        for key in ordered_keys:
            content = observation_content if key == "OBSERVATION" else sections.get(key, "")
            if not content:
                continue
            seen.add(key)
            if key == "SYSTEM":
                parts.append(content)
            else:
                parts.append(f"=== {key} ===")
                parts.append(content)

        # Any sections not in the canonical list, append at end
        for key, content in sections.items():
            if key not in seen and content:
                parts.append(f"=== {key} ===")
                parts.append(content)

        return "\n".join(parts)

    def _format_action_fact_section(self, hyp_ctx: dict | None) -> List[str]:
        if not hyp_ctx:
            return []
        
        # A013: Aggregate and prioritize facts
        facts = hyp_ctx.get("action_facts", [])
        if not facts:
            return []

        # Sort facts: deterministic first, then by consistency, then by evidence count
        def fact_sort_key(f):
            priority = 0
            if f.get("fact_type") == "deterministic_effect": priority = 2
            elif f.get("fact_type") == "localized_change": priority = 1
            return (priority, f.get("consistency", 0.0), f.get("evidence_count", 0))

        sorted_facts = sorted(facts, key=fact_sort_key, reverse=True)
        
        lines = []
        for fact in sorted_facts[: self.MAX_PROMPT_ACTIONS]:
            # Compact summary of the fact
            lines.append(
                f"{fact.get('action')}: {fact.get('fact_type', 'unknown').upper()} "
                f"(consistency {fact.get('consistency', 0.0):.2f}, "
                f"evidence {fact.get('evidence_count', 0)}, "
                f"value {fact.get('value_status', 'unknown')}): {fact.get('description')}"
            )
        return lines

    def _format_graph_evidence_section(self, hyp_ctx: dict | None) -> List[str]:
        """Format structured graph evidence into human-readable lines for prompts.

        Only include grounded hypotheses with sufficient evidence (>=2) so the
        LLM sees reproducible patterns rather than one-off noise.
        """
        if not hyp_ctx:
            return []
        ge = hyp_ctx.get("graph_evidence") or {}
        grounded = ge.get("grounded_hypotheses") or []
        lines: List[str] = []
        for g in grounded:
            action = g.get("action") or g.get("action_id")
            entity = g.get("entity_type") or g.get("entity")
            expected = g.get("expected_effect") or g.get("effect") or "unknown"
            count = int(g.get("evidence_count") or g.get("count") or 0)
            if count < 2:
                continue
            lines.append(f"{action} -> {entity} => {expected} (evidence={count})")
        return lines

    def _format_path_hypothesis_section(self, hyp_ctx: dict | None) -> List[str]:
        if not hyp_ctx:
            return []
        lines = []
        if hyp_ctx.get("loop_detected"):
            lines.append(f"⚠ LOOP DETECTED — revisited state {hyp_ctx.get('loop_hash', '')[:8]}. Change strategy.")
        for h in hyp_ctx.get("path_hypotheses", [])[: self.MAX_PROMPT_HYPOTHESES]:
            lines.append(
                f"PATH {h.get('value_status', 'unknown').upper()} ({h.get('confidence', 0.0):.0%}): {h.get('description')}"
            )
        coverage = hyp_ctx.get("action_coverage") or {}
        if coverage:
            untested = coverage.get("untested_actions") or []
            if untested:
                lines.append(
                    "Currently available but unobserved actions: "
                    + ", ".join(untested[: self.MAX_PROMPT_ACTIONS])
                )
            lines.append(
                f"COVERAGE: Exploration coverage: "
                f"tested {coverage.get('tested_count', 0)}, "
                f"untested {coverage.get('untested_count', 0)}"
            )
            if coverage.get("top_two_low_value"):
                lines.append("Top tested actions have decayed to low_value; broaden exploration.")
        bottleneck = hyp_ctx.get("environment_bottleneck")
        if bottleneck:
            lines.append(f"⚠ {bottleneck.get('message')}")
        lines.append(f"Policy: {hyp_ctx.get('explore_vs_exploit', 'explore').upper()}")
        return lines

    def _format_hypothesis_section(self, hyp_ctx: dict | None) -> List[str]:
        if not hyp_ctx:
            return []
        lines: List[str] = []
        for key, label in (
            ("confirmed_hypotheses", "CONFIRMED"),
            ("active_hypotheses", "ACTIVE"),
        ):
            for hyp in hyp_ctx.get(key, [])[: self.MAX_PROMPT_HYPOTHESES]:
                lines.append(
                    f"{label}: {hyp.get('description', 'unknown')} "
                    f"(conf {hyp.get('confidence', 0.0):.2f})"
                )
        for hyp in hyp_ctx.get("pruned_hypotheses", [])[: self.MAX_PROMPT_HYPOTHESES]:
            lines.append(
                f"PRUNED: {hyp.get('description', 'unknown')} "
                f"(conf {hyp.get('confidence', 0.0):.2f})"
            )
        return lines

    def _format_effect_section(self, hyp_ctx: dict | None) -> List[str]:
        if not hyp_ctx:
            return []
        lines: List[str] = []
        last_effect = hyp_ctx.get("last_transition_effect")
        if last_effect:
            before_snapshot = last_effect.get("before_snapshot")
            after_snapshot = last_effect.get("after_snapshot")
            if before_snapshot and after_snapshot:
                lines.append(
                    f"Board transition: {str(last_effect.get('before_frame_hash', 'unknown'))[:8]} -> "
                    f"{str(last_effect.get('after_frame_hash', 'unknown'))[:8]}"
                )
                lines.append(
                    "Before board 4x4:\n"
                    + str(before_snapshot.get("coarse_map", "(empty)"))
                )
                lines.append(
                    "After board 4x4:\n"
                    + str(after_snapshot.get("coarse_map", "(empty)"))
                )
            changed_region = last_effect.get("changed_region") or {}
            if changed_region.get("row_range") and changed_region.get("col_range"):
                lines.append(
                    f"Changed region rows {changed_region['row_range'][0]}-{changed_region['row_range'][1]}, "
                    f"cols {changed_region['col_range'][0]}-{changed_region['col_range'][1]}"
                )
                lines.append(
                    "Changed region before:\n"
                    + str(changed_region.get("before_crop", "(empty)"))
                )
                lines.append(
                    "Changed region after:\n"
                    + str(changed_region.get("after_crop", "(empty)"))
                )
            lines.append(
                f"Last action {last_effect.get('action')}: "
                f"{last_effect.get('meaningful_change_label', 'unknown')} "
                f"(score {last_effect.get('meaningful_change_score', 0.0):.2f}, "
                f"reasons: {', '.join(last_effect.get('meaningful_change_reasons', [])) or 'none'}, "
                f"zero_reward_streak: {last_effect.get('zero_reward_streak', 0)}) :: "
                f"{last_effect.get('summary')}"
            )
        for effect in hyp_ctx.get("observed_action_effects", [])[: self.MAX_PROMPT_ACTIONS]:
            if effect.get("times_seen", 0) <= 0:
                lines.append(f"{effect.get('action')}: UNTESTED")
                continue
            lines.append(
                f"{effect.get('action')}: avg_score {effect.get('avg_meaningful_change', 0.0):.2f}, "
                f"rank {effect.get('rank_score', 0.0):.2f}, "
                f"last {effect.get('last_meaningful_label', 'unknown')}, "
                f"novel {effect.get('novel_state_count', 0)}/{effect.get('times_seen')}, "
                f"reward {effect.get('reward_hits', 0)}/{effect.get('times_seen')}, "
                f"zero_reward_streak {effect.get('zero_reward_streak', 0)}, "
                f"budget {effect.get('retest_budget', 0)}, "
                f"no_progress {effect.get('no_progress_count', 0)}/{effect.get('times_seen')}, "
                f"last {effect.get('recent_diff')}"
            )
        return lines

    def _format_memory_section(
        self,
        memory_context: dict,
        observation: ARC3Observation,
        is_first_decision: bool,
    ) -> List[str]:
        lines: List[str] = []
        for lesson in self._select_prompt_memories(memory_context.get("lessons", []), self.MAX_PROMPT_LESSONS):
            lines.append(f"Lesson: {self._truncate_text(lesson.get('text', ''), 180)}")
        for memory in self._select_prompt_memories(memory_context.get("memories", []), self.MAX_PROMPT_MEMORIES):
            match_score, match_tags = self._score_memory_match(memory, observation)
            if is_first_decision and match_score < 2:
                continue
            prefix = "Matched memory" if match_score >= 2 else "Weak memory"
            lines.append(
                f"{prefix}: {self._truncate_text(self._memory_text(memory), 180)}"
                + (f" [match: {', '.join(match_tags)}]" if match_tags else "")
            )
        for analogy in self._select_prompt_memories(memory_context.get("analogies", []), self.MAX_PROMPT_ANALOGIES):
            lines.append(f"Analogy: {self._truncate_text(self._memory_text(analogy), 180)}")
        return lines

    def _format_reflex_section(self) -> List[str]:
        lines: List[str] = []
        if not self._reflex_context:
            return lines
        for warning in self._reflex_context.get("warnings", [])[:1]:
            lines.append(f"WARNING: {warning}")
        for suggestion in self._reflex_context.get("suggestions", [])[:1]:
            lines.append(f"GOLDEN PATH: {suggestion}")
        return lines

    def _format_compaction_section(self) -> str:
        """B116: EXPLORATION_SUMMARY - compact knowledge from long exploration runs."""
        if not self._compaction_artifact:
            return ""
        art = self._compaction_artifact
        lines = []
        if art.action_summaries:
            lines.append("KNOWN ACTION EFFECTS:")
            for action, summary in art.action_summaries.items():
                lines.append(f"  {summary}")
        if art.known_loops:
            lines.append("KNOWN LOOPS (sequences to avoid):")
            for loop in art.known_loops[:3]:
                lines.append(f"  {' -> '.join(loop)}")
        if art.confirmed_rules:
            lines.append("CONFIRMED RULES:")
            for rule in art.confirmed_rules[:3]:
                lines.append(f"  {rule}")
        return "\n".join(lines)

    def _format_plan_section(self) -> List[str]:
        if not self._plan_steps:
            return ["Plan: no steps yet."]
        return [
            f"Step {idx + 1}: {step}"
            for idx, step in enumerate(self._plan_steps[: self.MAX_PROMPT_PLAN_STEPS])
        ]

    def _format_history_section(self, history: List[dict]) -> str:
        if not history:
            return "No steps taken yet."
        
        # A013: Collapse adjacent identical rationales
        compact_history = []
        for record in history[-self.MAX_PROMPT_HISTORY :]:
            action_id = record.get("action_id")
            rationale = self._truncate_text(record.get("rationale") or "", 120)
            reward = record.get("reward")
            reward_text = f"{reward:.2f}" if isinstance(reward, (float, int)) else "pending"
            step = record["step"]
            
            entry = {
                "action": action_id,
                "rationale": rationale,
                "reward": reward_text,
                "steps": [step]
            }
            
            if compact_history and compact_history[-1]["action"] == entry["action"] and compact_history[-1]["rationale"] == entry["rationale"] and compact_history[-1]["reward"] == entry["reward"]:
                compact_history[-1]["steps"].append(step)
            else:
                compact_history.append(entry)

        lines = []
        for h in compact_history:
            s_list = h["steps"]
            if len(s_list) > 1:
                step_range = f"Steps {s_list[0]}–{s_list[-1]}"
                action_text = f"{h['action']} ×{len(s_list)}"
            else:
                step_range = f"Step {s_list[0]}"
                action_text = h["action"]
            
            lines.append(f"{step_range} → {action_text} ({h['rationale']}) · reward {h['reward']}")
            
        return "\n".join(lines)

    def _format_observation_section(self, observation: ARC3Observation) -> str:
        grid = observation.get("grid", [])
        rows = len(grid)
        cols = len(grid[0]) if grid else 0
        colors = observation.get("colors", [])
        
        # A013: Fix goal-labeling bug using grounded roles from solve_context
        sc = getattr(self, "_solve_context", {}) or {}
        player_cid = None
        goal_cid = None
        
        roles = sc.get("object_roles", {}) or {}
        for cid, role_obj in roles.items():
            if getattr(role_obj, "role", None) and hasattr(role_obj.role, "value"):
                if role_obj.role.value == "player": player_cid = int(cid)
                elif role_obj.role.value == "goal": goal_cid = int(cid)

        if colors:
            color_parts = []
            for c in colors[:6]:
                cid = int(c["value"])
                part = f"{cid}:{c['count']}"
                
                labels = []
                if cid == player_cid: labels.append("player")
                if cid == goal_cid: labels.append("goal")
                
                # Fallback to entity_map if roles are not yet grounded
                if not labels:
                    entity = self._entity_map.get(cid) if self._entity_map else None
                    if entity and entity["role"] != "unknown":
                        labels.append(entity["role"])
                
                if labels:
                    part += f"({'+'.join(labels)})"
                color_parts.append(part)
            color_summary = ", ".join(color_parts)
        else:
            color_summary = "none"

        # A013: Add grid delta summary
        delta_summary = ""
        if self._last_grid and grid:
            from .grid_analysis import GridDiffEngine
            try:
                diff_engine = GridDiffEngine()
                diff = diff_engine.diff_frames(self._last_grid, grid, "prev_step")
                n_changed = diff.get("n_cells_changed", 0)
                if n_changed == 0:
                    delta_summary = " (no change from last step)"
                else:
                    delta_summary = f" ({n_changed} cells changed from last step)"
            except Exception:
                pass

        coarse_map = self._coarse_grid_summary(grid)
        return (
            f"Grid: {rows}x{cols}{delta_summary}\n"
            f"Top colors (value:count): {color_summary}\n"
            f"Frame hash: {observation.get('frame_hash', 'unknown')[:12]}\n"
            f"Coarse map (8x8 majority colors):\n{coarse_map}"
        )

    def _coarse_grid_summary(self, grid: List[List[int]], block_count: int = 8) -> str:
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

    def _select_prompt_memories(self, items: List[Any], limit: int) -> List[Any]:
        selected: List[Any] = []
        seen: set[str] = set()
        
        # B-93: Prioritize [ACTION FACT] entries
        facts = [item for item in items if "[ACTION FACT]" in self._memory_text(item)]
        others = [item for item in items if "[ACTION FACT]" not in self._memory_text(item)]
        
        for item in facts + others:
            text = self._memory_text(item).strip()
            if not text:
                continue
            if "ARC-AGI-3 API Contract" in text:
                continue
            if text in seen:
                continue
            selected.append(item)
            seen.add(text)
            if len(selected) >= limit:
                break
        return selected

    def _memory_text(self, item: Any) -> str:
        if isinstance(item, dict):
            return str(item.get("text") or item.get("text_raw") or item)
        return str(item)

    def _score_memory_match(self, item: Any, observation: ARC3Observation) -> tuple[int, List[str]]:
        text = self._memory_text(item).lower()
        if not text:
            return 0, []

        tags: List[str] = []
        available_actions = [str(action).lower() for action in observation.get("available_actions", [])]
        matched_actions = [action.upper() for action in available_actions if action in text]
        if matched_actions:
            tags.append(f"actions={','.join(matched_actions[:2])}")

        task_id = str(observation.get("task_id", "")).lower()
        dataset_id = str(observation.get("dataset_id", "")).lower()
        state = str(observation.get("state", "")).lower()
        if task_id and task_id in text:
            tags.append("task")
        if dataset_id and dataset_id in text:
            tags.append("dataset")
        if state and state in text:
            tags.append("state")

        color_hits = []
        for color in observation.get("colors", [])[:4]:
            value = str(color.get("value"))
            if f"color {value}" in text or f"{value}->" in text:
                color_hits.append(value)
        if color_hits:
            tags.append(f"colors={','.join(color_hits[:2])}")

        score = len(tags)
        return score, tags

    def _truncate_text(self, text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 3].rstrip() + "..."

    def _snapshot_for_trace(self, observation: ARC3Observation, block_count: int = 4) -> dict:
        grid = observation.get("grid", [])
        colors = observation.get("colors", [])
        return {
            "frame_hash": str(observation.get("frame_hash", "unknown"))[:12],
            "rows": len(grid),
            "cols": len(grid[0]) if grid else 0,
            "top_colors": colors[:6],
            "coarse_map": self._coarse_grid_summary(grid, block_count=block_count),
        }

    # ------------------------------------------------------------------

    def record_step_result(self, reward: float, done: bool, next_observation: Optional[ARC3Observation] = None) -> None:
        if not self._step_history:
            return
        record = self._step_history[-1]
        record["reward"] = reward
        record["done"] = done

        # B133: Track frame hash for this action to detect genuine repetition
        action_id = record.get("action_id")
        board_before = record.get("board_before")
        if action_id and board_before:
            frame_hash = board_before.get("frame_hash")
            if frame_hash:
                self._action_frame_hashes[action_id] = frame_hash

        # B161: Update player position after step
        old_pos = self._player_position
        if next_observation:
            self._update_player_position(next_observation)
        
        centroid_shift = 0.0
        if old_pos and self._player_position:
            centroid_shift = ((self._player_position[0] - old_pos[0])**2 + 
                             (self._player_position[1] - old_pos[1])**2)**0.5

        # B150: Per-step grid analysis (FrameDelta)
        if next_observation and self._last_grid:
            from agents.arc3.grid_analysis import GridDiffEngine
            try:
                curr_grid = next_observation.get("grid")
                if curr_grid:
                    diff_engine = GridDiffEngine()
                    delta = diff_engine.diff_frames(self._last_grid, curr_grid, action_id or "unknown")
                    self._frame_deltas.append(delta)
                    
                    # Store in step history for trace
                    record["frame_delta"] = {
                        "apparent_effect": delta.apparent_effect,
                        "n_cells_changed": delta.n_cells_changed,
                        "direction": delta.direction
                    }
                    
                    # B161: ACTION5 effect analysis
                    if action_id == "ACTION5" and delta.n_cells_changed > 30:
                        self._last_interact_effect = {
                            "pixels_changed": delta.n_cells_changed,
                            "new_colors": delta.new_colors_introduced if hasattr(delta, "new_colors_introduced") else [],
                            "removed_colors": delta.colors_removed if hasattr(delta, "colors_removed") else [],
                            "step": len(self._step_history)
                        }
                    
                    # Update last_grid for next step
                    self._last_grid = curr_grid
            except Exception as exc:
                logger.warning("B150: per-step grid analysis failed: %s", exc)

        # B135: Track recent frame hashes for loop detection
        frame_hash = record.get("frame_hash") or record.get("board_before", {}).get("frame_hash")
        if frame_hash:
            self._recent_frame_hashes.append(str(frame_hash))
            # Keep only last 5 frames
            self._recent_frame_hashes = self._recent_frame_hashes[-5:]

        # B89: Track no-progress steps (reward = 0)
        if reward == 0.0:
            self._no_progress_step_count += 1
            self._consecutive_no_progress_steps += 1
            # B149: Increment fatigue
            if action_id:
                self._action_fatigue[action_id] = self._action_fatigue.get(action_id, 0) + 1
        else:
            self._consecutive_no_progress_steps = 0
            # B177: Clear blocks on progress
            self._blocked_actions.clear()
            # B149: Productive action — reset its fatigue
            if action_id:
                self._action_fatigue[action_id] = 0

        # B175: Clear blocked axes on significant movement or reward
        if reward > 0 or (centroid_shift > 3.0):
            self._blocked_axes.clear()

    def reset_for_retry(self, attempt: int) -> None:
        """Reset internal state for a retry attempt while preserving history.

        The step_history is kept so the Amygdala Reflex can see what was
        already tried.  The plan is cleared so a new register_plan call
        triggers fresh similarity checks against the now-failed plan.
        """
        self._plan_id = None
        self._reflex_context = None
        self._plan_steps = []
        self._consecutive_no_progress_steps = 0
        self._blocked_actions = set()
        self._exploitation_switch_budget = 2
        self._forced_exploration_count = 0
        self._verified_output_grid = None
        self._phase2_mode = "fallback"
        self._transformation_signature = None
        self._training_diffs = None
        # Append a sentinel so the LLM prompt shows the GAME_OVER boundary
        self._step_history.append({
            "step": len(self._step_history) + 1,
            "action_id": "GAME_OVER",
            "rationale": f"Attempt {attempt} failed — resetting with new strategy",
            "reward": -1.0,
            "done": True,
        })
        self.hypothesis_mgr.reset_graph()
        self.solve_engine.reset_for_retry()

    def get_benchmark_metrics(self) -> dict:
        """B89: Return collected prompt budget and retrieval budget metrics."""
        avg_prompt_tokens = (
            sum(self._prompt_tokens_per_step) / len(self._prompt_tokens_per_step)
            if self._prompt_tokens_per_step
            else 0
        )
        total_retrieval_size = sum(
            payload.get("total_size", 0) for payload in self._retrieval_payloads
        )
        return {
            "prompt_budget": {
                "total_steps": len(self._prompt_tokens_per_step),
                "avg_tokens_per_step": round(avg_prompt_tokens, 1),
                "max_tokens_per_step": max(self._prompt_tokens_per_step) if self._prompt_tokens_per_step else 0,
                "min_tokens_per_step": min(self._prompt_tokens_per_step) if self._prompt_tokens_per_step else 0,
                "first_prompt_detail_level": self._first_prompt_detail_level,
                "asked_for_decision_from_effects": self._asked_for_decision_from_effects,
                "invalid_action_count": self._invalid_action_count,
                "no_progress_step_count": self._no_progress_step_count,
            },
            "retrieval_budget": {
                "retrieval_count": len(self._retrieval_payloads),
                "total_retrieval_size_bytes": total_retrieval_size,
                "avg_retrieval_size_bytes": (
                    total_retrieval_size / len(self._retrieval_payloads)
                    if self._retrieval_payloads
                    else 0
                ),
            },
            "token_cost": self.cost_tracker.summary() if self.cost_tracker else {},
            "pruning_decisions": list(self._pruning_decisions),
        }

    def _summarize_puzzle_structure(self, observation: ARC3Observation) -> str:
        """Build a rich structural summary for SideQuests ingestion."""
        grid = observation.get("grid", [])
        rows = len(grid)
        cols = len(grid[0]) if grid else 0
        colors = observation.get("colors", [])
        shapes = observation.get("shapes", [])
        state = observation.get("state", "NOT_STARTED")
        available = observation.get("available_actions", [])
        energy = observation.get("energy_estimate", 1.0)
        frame_hash = str(observation.get("frame_hash", "unknown"))[:12]
        spatial_sketch = self._coarse_grid_summary(grid, block_count=4).replace("\n", " / ")
        color_desc = ", ".join(
            f"color {c['value']} ({c['count']} cells)" for c in colors[:6]
        ) if colors else "none detected"
        shape_desc = ", ".join(
            f"{s.get('type', 'unknown')} size {s.get('size', '?')}" for s in shapes[:6]
        ) if shapes else "none detected"

        # B120: Entity role annotations
        if self._entity_map:
            entity_annotations = []
            for color_info in colors[:6]:
                cid = color_info["value"] if isinstance(color_info, dict) else color_info
                entity = self._entity_map.get(cid)
                if entity and entity["role"] != "unknown":
                    annotation = f"color {cid} = {entity['role']}"
                    if entity.get("position"):
                        annotation += f" at row {entity['position']['row']:.0f}, col {entity['position']['col']:.0f}"
                    entity_annotations.append(annotation)
            entity_desc = "; ".join(entity_annotations) if entity_annotations else "pending"
        else:
            entity_desc = "pending"

        return (
            f"[PUZZLE STRUCTURE] Task {observation['task_id']} from {observation['dataset_id']}. "
            f"Grid: {rows}x{cols}. State: {state}. Energy: {energy:.0%}. "
            f"Frame hash: {frame_hash}. "
            f"Colors: {color_desc}. "
            f"Entity roles: {entity_desc}. "
            f"Shapes ({len(shapes)}): {shape_desc}. "
            f"Available actions: {', '.join(available) if available else 'pending'}. "
            f"Spatial sketch 4x4: {spatial_sketch or '(empty)'}."
        )
