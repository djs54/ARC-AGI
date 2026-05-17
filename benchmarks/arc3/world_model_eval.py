"""ARC World Model Evaluation (A078).

Evaluates the quality and boundedness of the world model redesign.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class WorldModelStepMetrics:
    kind: str = "world_model_step"
    task_id: str = ""
    step: int = 0
    action_id: Optional[str] = None
    x: Optional[int] = None
    y: Optional[int] = None
    action_identity: Optional[str] = None
    coordinate_required: bool = False
    missing_coordinate_click: bool = False
    decision_source: Optional[str] = None
    world_model_node_count: int = 0
    world_model_edge_count: int = 0
    compiled_claim_count: int = 0
    action_effect_class: str = "unknown"
    contradiction_edge_count: int = 0
    hypothesis_demotion_count: int = 0
    reasoning_mode: str = "llm_reason"
    reasoning_skip_count: int = 0
    reasoning_escalation_count: int = 0
    llm_reason_count: int = 0
    planner_candidate_count: int = 0
    selected_candidate_has_prediction: bool = False
    selected_candidate_prediction_effect_class: Optional[str] = None  # A089: predicted effect
    selected_candidate_prediction_confidence: float = 0.0  # A089: prediction confidence
    selected_candidate_has_falsification: bool = False
    planner_selected_prior_id: Optional[str] = None
    planner_selected_prior_source: str = "none"
    planner_selected_prior_compatibility: float = 0.0  # A090: prior compatibility score
    mechanic_prior_recall_status: str = "not_called"
    mechanic_prior_count: int = 0
    mechanic_prior_error_code: Optional[str] = None
    mechanic_prior_used_count: int = 0
    memory_transfer_state: str = "zero_priors"
    single_action_stall_detected: bool = False
    stall_policy: str = "none"
    early_stop_suppressed_reason: Optional[str] = None
    stall_evidence_count: int = 0
    stall_threshold: int = 0
    multi_action_churn_detected: bool = False  # A085
    actions_tested_count: int = 0  # A085
    productive_action_count: int = 0  # A085
    all_actions_churn_detected: bool = False
    all_actions_churn_count: int = 0
    memory_degraded: bool = False  # A091: MCP backend degraded
    memory_degraded_reason: Optional[str] = None  # A091: degradation reason
    mcp_http_timeout_count: int = 0  # A091: HTTP bridge timeout count
    # A100: live eval parity for graph-control debugging.
    reward: float = 0.0
    progress_reward: float = 0.0
    meaningful_progress: bool = False
    progress_class: str = "unknown"
    progress_gate_reason: Optional[str] = None
    terminal_progress_trend: Optional[str] = None
    terminal_goal_distance: Optional[float] = None
    terminal_value_score: Optional[float] = None
    terminal_alignment: Optional[str] = None
    terminal_aligned: bool = False
    goal_distance_before: Optional[float] = None
    goal_distance_after: Optional[float] = None
    goal_distance_delta: Optional[float] = None
    distance_trend: Optional[str] = None
    all_actions_churn_evidence: Dict[str, Any] = field(default_factory=dict)
    total_local_progress_count: int = 0
    route_transition_evidence: Dict[str, Any] = field(default_factory=dict)
    route_candidate_count: int = 0
    route_actions: List[str] = field(default_factory=list)
    route_confidence: float = 0.0
    # A101: Goal Hypothesis Induction
    active_goal_hypothesis_id: Optional[str] = None
    active_goal_type: Optional[str] = None
    active_goal_confidence: float = 0.0
    active_goal_evidence_count: int = 0
    # A102: Object Mechanic Graph
    mechanic_graph_object_count: int = 0
    mechanic_graph_relation_count: int = 0
    mechanic_graph_configuration_hash: Optional[str] = None
    # A103: Graph Transformation
    graph_transform_class: Optional[str] = None
    configuration_hash_after_action: Optional[str] = None
    graph_transform_goal_relevance: float = 0.0
    affected_mechanic_objects_count: int = 0
    # A104: Configuration Cycle Search
    configuration_cycle_search_active: bool = False
    configuration_hash_current: Optional[str] = None
    configuration_repeat_count: int = 0
    cycle_search_goal_alignment_delta: float = 0.0
    # A105: Level Solution Templates
    level_template_count: int = 0
    level_template_match_score: float = 0.0
    level_template_used: bool = False
    level_template_id: Optional[str] = None
    # A106-A110: coordinate-aware click planning/evaluation.
    click_candidate_count: int = 0
    selected_click_candidate_id: Optional[str] = None
    selected_click_candidate_role: Optional[str] = None
    selected_click_candidate_rank: int = -1
    clicked_x: Optional[int] = None
    clicked_y: Optional[int] = None
    clicked_color: Optional[int] = None
    clicked_panel_id: Optional[str] = None
    click_supported: bool = False
    click_falsified: bool = False
    click_failure_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

@dataclass
class WorldModelSummaryMetrics:
    kind: str = "world_model_summary"
    task_id: str = ""
    game_id: str = ""
    game_title: str = ""
    graph_bounded: bool = True
    compiler_active: bool = True
    falsification_active: bool = True
    reasoning_gated: bool = True
    planner_grounded: bool = True
    memory_transfer_active: bool = False
    memory_transfer_state: str = "zero_priors"
    single_action_stall_detected: bool = False
    full_reasoning_cycles_avoided: int = 0
    early_stop_decision_count: int = 0
    world_model_decision_count: int = 0
    puzzle_description: str = ""
    arc_game_url: str = ""
    test_results_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

@dataclass
class WorldModelDecisionMetrics:
    kind: str = "world_model_decision"
    task_id: str = ""
    decision: str = "unknown"
    trigger: Optional[str] = None
    executed_step_count: int = 0
    decision_step: int = 0
    stall_evidence_count: int = 0
    stall_threshold: int = 0
    action_id: Optional[str] = None
    action_effect_class: str = "unknown"
    repeated_frame_hash_count: int = 0
    world_model_node_count: int = 0
    world_model_edge_count: int = 0
    world_model_decision: Optional[str] = None
    failure_class: Optional[str] = None
    failure_reason: Optional[str] = None
    all_actions_churn_detected: bool = False
    all_actions_churn_evidence: Dict[str, Any] = field(default_factory=dict)
    total_churn_count: int = 0
    total_progress_count: int = 0
    total_local_progress_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in self.__dict__.items()}

class WorldModelEvaluator:
    """Helper to build world model evaluation artifacts."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._single_action_stall_detected = False
        self._full_reasoning_cycles_avoided = 0
        self._memory_transfer_active = False
        self._memory_transfer_state = "zero_priors"
        self._early_stop_decision_count = 0
        self._world_model_decision_count = 0

    @staticmethod
    def _compute_memory_transfer_state(snapshot: Dict[str, Any]) -> str:
        """A090: Distinguish prior_used from priors_recalled_not_used based on selected_prior_id."""
        recall_status = str(snapshot.get("mechanic_prior_recall_status", "not_called") or "not_called")
        prior_count = int(snapshot.get("mechanic_prior_count", 0) or 0)
        # A090: Check if selected prior was actually used (provenance tracking)
        selected_prior_id = snapshot.get("mechanic_prior_id") or snapshot.get("planner_selected_prior_id")
        used_count = int(snapshot.get("mechanic_priors_used_count", snapshot.get("mechanic_prior_used_count", 0)) or 0)
        
        if used_count > 0:
            return "prior_used"
        if recall_status == "capability_missing":
            return "capability_missing"
        if prior_count <= 0:
            return "zero_priors"
        return "priors_recalled_not_used"

    @staticmethod
    def _get_field(source: Any, key: str, default: Any = None) -> Any:
        if isinstance(source, dict):
            return source.get(key, default)
        return getattr(source, key, default)

    def build_step_row(self, task_id: str, step: int, snapshot: Dict[str, Any]) -> WorldModelStepMetrics:
        compiled = snapshot.get("compiled_world_delta", {})
        single_action_stall = compiled.get("failure_signal") == "single_action_terminal_stall"
        if single_action_stall:
            self._single_action_stall_detected = True
            
        gating = snapshot.get("reasoning_gating", {})
        
        memory_transfer_state = self._compute_memory_transfer_state(snapshot)
        if memory_transfer_state == "prior_used":
            self._memory_transfer_active = True
        if memory_transfer_state != "zero_priors" or getattr(self, "_memory_transfer_state", "zero_priors") == "zero_priors":
            self._memory_transfer_state = memory_transfer_state
             
        skip_count = snapshot.get("reasoning_skip_count", 0)
        if skip_count > self._full_reasoning_cycles_avoided:
             self._full_reasoning_cycles_avoided = skip_count
        
        # A089: Extract structured prediction info from runner snapshots or trace-shaped payloads.
        selected_prediction = snapshot.get("selected_prediction") or snapshot.get("planner_selected_prediction") or {}
        pred_effect_class = None
        pred_confidence = 0.0
        if isinstance(selected_prediction, dict) and selected_prediction:
            pred_effect_class = selected_prediction.get("effect_class")
            pred_confidence = float(selected_prediction.get("confidence", 0.0) or 0.0)
        else:
            pred_effect_class = snapshot.get("planner_selected_prediction_effect_class")
            pred_confidence = float(snapshot.get("planner_selected_prediction_confidence", 0.0) or 0.0)

        reward_components = snapshot.get("reward_components") or {}
        if not isinstance(reward_components, dict):
            reward_components = {}
        churn_evidence = gating.get("all_actions_churn_evidence") or snapshot.get("all_actions_churn_evidence") or {}
        if not isinstance(churn_evidence, dict):
            churn_evidence = {}
        terminal_alignment = compiled.get("terminal_alignment") or snapshot.get("terminal_alignment")
        terminal_aligned = bool(compiled.get("terminal_aligned", snapshot.get("terminal_aligned", False)))
        
        return WorldModelStepMetrics(
            task_id=task_id,
            step=step,
            action_id=snapshot.get("action_id"),
            x=snapshot.get("x"),
            y=snapshot.get("y"),
            action_identity=snapshot.get("action_identity"),
            coordinate_required=bool(snapshot.get("coordinate_required", False)),
            missing_coordinate_click=bool(snapshot.get("missing_coordinate_click", False)),
            decision_source=snapshot.get("decision_source"),
            world_model_node_count=snapshot.get("world_model_node_count", 0),
            world_model_edge_count=snapshot.get("world_model_edge_count", 0),
            contradiction_edge_count=snapshot.get("world_model_contradiction_count", 0),
            hypothesis_demotion_count=snapshot.get("world_model_demotion_count", 0),
            compiled_claim_count=compiled.get("claims_count", 0),
            action_effect_class=compiled.get("effect_class", "unknown"),
            reasoning_mode=snapshot.get("reasoning_mode", gating.get("mode", "llm_reason")),
            reasoning_skip_count=snapshot.get("reasoning_skip_count", 0),
            reasoning_escalation_count=snapshot.get("reasoning_escalation_count", 0),
            llm_reason_count=snapshot.get("llm_reason_count", 0),
            planner_candidate_count=snapshot.get("planner_candidate_count", 0),
            selected_candidate_has_prediction=bool(snapshot.get("planner_selected_has_prediction", False)),
            selected_candidate_prediction_effect_class=pred_effect_class,  # A089
            selected_candidate_prediction_confidence=pred_confidence,  # A089
            selected_candidate_has_falsification=bool(snapshot.get("planner_selected_has_falsification", False)),
            planner_selected_prior_id=snapshot.get("planner_selected_prior_id"),
            planner_selected_prior_source=snapshot.get("planner_selected_prior_source", "none"),
            planner_selected_prior_compatibility=float(
                snapshot.get(
                    "planner_selected_prior_compatibility",
                    snapshot.get("mechanic_prior_compatibility_score", 0.0),
                )
                or 0.0
            ),  # A090
            mechanic_prior_recall_status=snapshot.get("mechanic_prior_recall_status", "not_called"),
            mechanic_prior_count=snapshot.get("mechanic_prior_count", 0),
            mechanic_prior_error_code=snapshot.get("mechanic_prior_error_code"),
            mechanic_prior_used_count=snapshot.get("mechanic_priors_used_count", 0),
            memory_transfer_state=memory_transfer_state,
            single_action_stall_detected=single_action_stall,
            stall_policy=gating.get("stall_policy", "none"),
            early_stop_suppressed_reason=gating.get("early_stop_suppressed_reason"),
            stall_evidence_count=gating.get("stall_evidence_count", 0),
            stall_threshold=gating.get("stall_threshold", 0),
            multi_action_churn_detected=bool(gating.get("multi_action_churn_detected", False)),  # A085
            actions_tested_count=int(gating.get("actions_tested_count", 0) or 0),  # A085
            productive_action_count=int(gating.get("productive_action_count", 0) or 0),  # A085
            all_actions_churn_detected=bool(churn_evidence.get("all_actions_churn", False)),
            all_actions_churn_count=int(churn_evidence.get("total_churn_count", 0) or 0),
            memory_degraded=bool(snapshot.get("memory_degraded", False)),  # A091
            memory_degraded_reason=snapshot.get("memory_degraded_reason"),  # A091
            mcp_http_timeout_count=int(snapshot.get("mcp_http_timeout_count", 0) or 0),  # A091
            reward=float(snapshot.get("reward", 0.0) or 0.0),
            progress_reward=float(snapshot.get("progress_reward", snapshot.get("reward", 0.0)) or 0.0),
            meaningful_progress=bool(reward_components.get("meaningful_progress", snapshot.get("meaningful_progress", False))),
            progress_class=str(reward_components.get("progress_class") or snapshot.get("progress_class") or "unknown"),
            progress_gate_reason=reward_components.get("progress_gate_reason") or snapshot.get("progress_gate_reason"),
            terminal_progress_trend=snapshot.get("terminal_progress_trend"),
            terminal_goal_distance=(
                float(snapshot.get("terminal_goal_distance"))
                if snapshot.get("terminal_goal_distance") is not None
                else None
            ),
            terminal_value_score=(
                float(snapshot.get("terminal_value_score"))
                if snapshot.get("terminal_value_score") is not None
                else None
            ),
            terminal_alignment=str(terminal_alignment) if terminal_alignment is not None else None,
            terminal_aligned=terminal_aligned,
            goal_distance_before=(
                float(compiled.get("goal_distance_before"))
                if compiled.get("goal_distance_before") is not None
                else None
            ),
            goal_distance_after=(
                float(compiled.get("goal_distance_after"))
                if compiled.get("goal_distance_after") is not None
                else None
            ),
            goal_distance_delta=(
                float(compiled.get("goal_distance_delta"))
                if compiled.get("goal_distance_delta") is not None
                else None
            ),
            distance_trend=compiled.get("distance_trend") or snapshot.get("distance_trend"),
            all_actions_churn_evidence=dict(churn_evidence),
            total_local_progress_count=int(churn_evidence.get("total_local_progress_count", 0) or 0),
            route_transition_evidence=dict(
                gating.get("route_transition_evidence")
                or snapshot.get("route_transition_evidence")
                or {}
            ),
            route_candidate_count=int(snapshot.get("route_candidate_count", 0) or 0),
            route_actions=list(snapshot.get("route_actions") or []),
            route_confidence=float(snapshot.get("route_confidence", 0.0) or 0.0),
            active_goal_hypothesis_id=snapshot.get("active_goal_hypothesis_id"),
            active_goal_type=snapshot.get("active_goal_type"),
            active_goal_confidence=float(snapshot.get("active_goal_confidence", 0.0) or 0.0),
            active_goal_evidence_count=int(snapshot.get("active_goal_evidence_count", 0) or 0),
            mechanic_graph_object_count=int(snapshot.get("mechanic_graph_object_count", 0) or 0),
            mechanic_graph_relation_count=int(snapshot.get("mechanic_graph_relation_count", 0) or 0),
            mechanic_graph_configuration_hash=snapshot.get("mechanic_graph_configuration_hash"),
            graph_transform_class=snapshot.get("graph_transform_class"),
            configuration_hash_after_action=snapshot.get("configuration_hash_after_action"),
            graph_transform_goal_relevance=float(snapshot.get("graph_transform_goal_relevance", 0.0) or 0.0),
            affected_mechanic_objects_count=int(snapshot.get("affected_mechanic_objects_count", 0) or 0),
            configuration_hash_current=snapshot.get("configuration_hash_current"),
            click_candidate_count=int(snapshot.get("click_candidate_count", 0) or 0),
            selected_click_candidate_id=snapshot.get("selected_click_candidate_id") or snapshot.get("click_candidate_id"),
            selected_click_candidate_role=snapshot.get("selected_click_candidate_role") or snapshot.get("click_candidate_role"),
            selected_click_candidate_rank=int(snapshot.get("selected_click_candidate_rank", snapshot.get("click_candidate_rank", -1)) or -1),
            clicked_x=snapshot.get("clicked_x"),
            clicked_y=snapshot.get("clicked_y"),
            clicked_color=snapshot.get("clicked_color"),
            clicked_panel_id=snapshot.get("clicked_panel_id"),
            click_supported=bool(snapshot.get("click_supported", False)),
            click_falsified=bool(snapshot.get("click_falsified", False)),
            click_failure_message=snapshot.get("click_failure_message"),
        )

    def build_decision_row(self, task_id: str, snapshot: Dict[str, Any]) -> WorldModelDecisionMetrics:
        self._world_model_decision_count += 1
        if snapshot.get("decision") == "early_stop":
            self._early_stop_decision_count += 1
        churn_evidence = snapshot.get("all_actions_churn_evidence") or {}
        if not isinstance(churn_evidence, dict):
            churn_evidence = {}
        return WorldModelDecisionMetrics(
            task_id=task_id,
            decision=str(snapshot.get("decision", "unknown")),
            world_model_decision=snapshot.get("world_model_decision"),
            trigger=snapshot.get("trigger"),
            executed_step_count=int(snapshot.get("executed_step_count", 0) or 0),
            decision_step=int(snapshot.get("decision_step", 0) or 0),
            stall_evidence_count=int(snapshot.get("stall_evidence_count", 0) or 0),
            stall_threshold=int(snapshot.get("stall_threshold", 0) or 0),
            action_id=snapshot.get("action_id"),
            action_effect_class=str(snapshot.get("action_effect_class", "unknown") or "unknown"),
            repeated_frame_hash_count=int(snapshot.get("repeated_frame_hash_count", 0) or 0),
            world_model_node_count=int(snapshot.get("world_model_node_count", 0) or 0),
            world_model_edge_count=int(snapshot.get("world_model_edge_count", 0) or 0),
            failure_class=snapshot.get("failure_class"),
            failure_reason=snapshot.get("failure_reason"),
            all_actions_churn_detected=bool(churn_evidence.get("all_actions_churn", False)),
            all_actions_churn_evidence=dict(churn_evidence),
            total_churn_count=int(churn_evidence.get("total_churn_count", 0) or 0),
            total_progress_count=int(churn_evidence.get("total_progress_count", 0) or 0),
            total_local_progress_count=int(churn_evidence.get("total_local_progress_count", 0) or 0),
        )

    def build_summary_row(self, task_id: str, final_result: Any) -> WorldModelSummaryMetrics:
        # Extract from task_result (ABTaskResult)
        snapshot = self._get_field(final_result, "world_model_snapshot", {})
        if not snapshot and isinstance(final_result, dict):
            snapshot = final_result.get("world_model_snapshot", {})
            
        return WorldModelSummaryMetrics(
            task_id=task_id,
            game_id=str(self._get_field(final_result, "game_id", "") or ""),
            game_title=str(self._get_field(final_result, "game_title", "") or ""),
            graph_bounded=snapshot.get("node_count", 0) <= 200,
            compiler_active=True,
            reasoning_gated=True,
            memory_transfer_active=bool(getattr(self, "_memory_transfer_active", False)),
            memory_transfer_state=str(getattr(self, "_memory_transfer_state", "zero_priors")),
            single_action_stall_detected=self._single_action_stall_detected,
            full_reasoning_cycles_avoided=self._full_reasoning_cycles_avoided,
            early_stop_decision_count=int(getattr(self, "_early_stop_decision_count", 0) or 0),
            world_model_decision_count=int(getattr(self, "_world_model_decision_count", 0) or 0),
            puzzle_description=str((self._get_field(final_result, "run_review", {}) or {}).get("puzzle_description", "") if isinstance(self._get_field(final_result, "run_review", {}), dict) else ""),
            arc_game_url=str((self._get_field(final_result, "run_review", {}) or {}).get("arc_game_url", "") if isinstance(self._get_field(final_result, "run_review", {}), dict) else ""),
            test_results_url=str((self._get_field(final_result, "run_review", {}) or {}).get("test_results_url", "") if isinstance(self._get_field(final_result, "run_review", {}), dict) else ""),
        )
