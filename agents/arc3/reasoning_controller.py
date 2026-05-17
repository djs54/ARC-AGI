"""ARC Evidence-Gated Reasoning Controller (A076).

Decides when expensive LLM reasoning is justified based on world-model evidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

class ReasoningMode(str, Enum):
    CHEAP_EXECUTE      = "cheap_execute"
    COMPILE_ONLY       = "compile_only"
    RETRIEVE_PRIORS    = "retrieve_priors"
    LLM_REASON         = "llm_reason"
    CHEAP_PROBE        = "cheap_probe"
    CHEAP_PROBE_BATCH  = "cheap_probe_batch"
    EARLY_STOP         = "early_stop"
    RECLASSIFY_MECHANIC = "reclassify_mechanic"
    MULTI_ACTION_CHURN_PROBE = "multi_action_churn_probe"
    MULTI_ACTION_RECLASSIFY = "multi_action_reclassify"
    MULTI_ACTION_STRATEGY_EXHAUSTED = "multi_action_strategy_exhausted"
    CONFIGURATION_CYCLE_SEARCH = "configuration_cycle_search"  # A104

@dataclass
class ReasoningDecision:
    mode: ReasoningMode
    trigger: str
    skipped_reason: Optional[str] = None
    estimated_tokens_saved: int = 0
    stall_policy: Optional[str] = None
    stall_evidence_count: int = 0
    stall_threshold: int = 5
    multi_action_churn_detected: bool = False
    actions_tested_count: int = 0
    productive_action_count: int = 0
    world_model_decision: Optional[str] = None
    early_stop_suppressed_reason: Optional[str] = None
    # A104: Configuration cycle search fields
    configuration_cycle_mode: bool = False
    configuration_hash: Optional[str] = None
    configuration_repeat_count: int = 0
    goal_alignment_delta: float = 0.0

class ReasoningController:
    """Gates LLM-heavy phases based on world-model delta and budget."""

    def __init__(self, config: Optional[dict] = None):
        self.config = config or {}
        self.skip_count = 0
        self.reason_count = 0
        self.escalation_count = 0
        self._consecutive_stalls = 0
        self._total_probes = 0
        self._min_probes_before_stop = int(self.config.get("reasoning_gate", {}).get("min_probes_before_stop", 3))
        self._stall_threshold = int(self.config.get("reasoning_gate", {}).get("stall_threshold", 5))
        
        # A085: Multi-action churn tracking
        self._per_action_evidence = {}  # action_id -> {tested, effects, last_progress_step, frame_hashes}
        self._multi_action_churn_count = 0
        self._last_meaningful_progress_step = -1
        self._multi_action_no_progress_cooldown = 5  # steps without progress before gating
        self._consecutive_multi_action_churn_probes = 0
        self._max_multi_action_churn_probes = int(
            self.config.get("reasoning_gate", {}).get("max_multi_action_churn_probes", 5)
        )
        self._single_action_tick_probe_count = 0
        self._max_single_action_tick_probes = int(
            self.config.get("reasoning_gate", {}).get("max_single_action_tick_probes", 20)
        )
        self._max_unproductive_single_action_tick_probes = int(
            self.config.get("reasoning_gate", {}).get("max_unproductive_single_action_tick_probes", 8)
        )
        self._contradiction_probe_threshold = int(
            self.config.get("reasoning_gate", {}).get("contradiction_probe_threshold", 4)
        )
        self._prediction_falsification_reclassify_threshold = int(
            self.config.get("reasoning_gate", {}).get("prediction_falsification_reclassify_threshold", 3)
        )
        self._strategy_exhausted_probe_epochs = int(
            self.config.get("reasoning_gate", {}).get("strategy_exhausted_probe_epochs", 3)
        )
        self._consecutive_route_regressions = 0
        self._route_regression_threshold = int(
            self.config.get("reasoning_gate", {}).get("route_regression_threshold", 3)
        )
        
        # A104: Configuration cycle search tracking
        self._cycle_search_active = False
        self._cycle_seen_configurations: Set[str] = set()
        self._cycle_probes_count = 0
        self._max_cycle_probes = int(
            self.config.get("reasoning_gate", {}).get("max_cycle_probes", 12)
        )
        self._last_cycle_goal_alignment = 0.0

    def decide(
        self,
        world_summary: str,
        compiled_delta: Optional[Any],
        budget_state: Dict[str, Any],
        phase: str,
        active_hypotheses: List[Dict[str, Any]],
        available_actions: List[str],
        mechanic_priors: Optional[List[Dict[str, Any]]] = None,
        per_action_evidence: Optional[Dict[str, Any]] = None  # A085: per-action progress tracking
    ) -> ReasoningDecision:
        # Default to LLM reasoning
        decision = ReasoningDecision(
            mode=ReasoningMode.LLM_REASON, 
            trigger="default_escalation",
            stall_threshold=self._stall_threshold
        )
        
        failure_signal = getattr(compiled_delta, "failure_signal", None)
        
        # A085: Initialize churn detection (used in counter updates)
        churn_detected = False
        
        # 1. Update internal stall counters based on compiled evidence
        has_progress = False
        if compiled_delta:
            effect_claim = next((c for c in getattr(compiled_delta, "claims", []) if getattr(c, "kind", "") == "action_effect"), None)
            effect_class = getattr(effect_claim, "effect_class", "unknown") if effect_claim is not None else "unknown"
            terminal_alignment = str(getattr(effect_claim, "terminal_alignment", "") or "")
            effect_props = getattr(effect_claim, "props", {}) if effect_claim is not None else {}
            if not isinstance(effect_props, dict):
                effect_props = {}
            distance_trend = str(effect_props.get("distance_trend") or getattr(effect_claim, "distance_trend", "") or "")
            if effect_class == "terminal_progress":
                 has_progress = True
            elif effect_class in ("object_progress", "meaningful_progress"):
                 has_progress = terminal_alignment in ("", "terminal_aligned", "delayed_effect_pending")
        else:
            effect_class = "unknown"
            distance_trend = ""

        if effect_class == "distance_regressing_move" or distance_trend == "regressing":
            self._consecutive_route_regressions += 1
        elif has_progress or effect_class == "distance_improving_move" or distance_trend == "improving":
            self._consecutive_route_regressions = 0
                 
        if has_progress:
            self._consecutive_stalls = 0
            self._last_meaningful_progress_step = max(getattr(self, "_last_meaningful_progress_step", -1), getattr(compiled_delta, "step", 0))
            self._consecutive_multi_action_churn_probes = 0
            self._single_action_tick_probe_count = 0
        elif failure_signal == "single_action_terminal_stall" or len(available_actions) == 1:
            self._consecutive_stalls += 1
            
        decision.stall_evidence_count = self._consecutive_stalls
        falsification_counts = budget_state.get("prediction_falsification_counts") or {}
        max_prediction_falsifications = max(
            [int(v or 0) for v in falsification_counts.values()],
            default=0,
        )

        # Rule 1: Single action terminal stall -> Early Stop or Cheap Probe
        if failure_signal == "single_action_terminal_stall":
             if len(available_actions) == 1 and effect_class == "pixel_churn":
                  has_delayed_prediction = self._has_delayed_reward_prior(mechanic_priors or [])
                  tick_budget = (
                      self._max_single_action_tick_probes
                      if has_delayed_prediction
                      else min(self._max_single_action_tick_probes, self._max_unproductive_single_action_tick_probes)
                  )
                  if self._single_action_tick_probe_count >= tick_budget:
                       decision.mode = ReasoningMode.EARLY_STOP
                       decision.trigger = "single_action_tick_budget_exhausted"
                       decision.stall_policy = "terminal_stop"
                  else:
                       decision.mode = ReasoningMode.CHEAP_PROBE
                       decision.trigger = "delayed_reward_probe" if has_delayed_prediction else "single_action_tick_probe"
                       decision.stall_policy = "delayed_reward_wait" if has_delayed_prediction else "tick_probe"
                       self._single_action_tick_probe_count += 1
             elif effect_class == "harmful":
                  decision.mode = ReasoningMode.LLM_REASON
                  decision.trigger = "single_action_harmful_reclassify"
                  decision.stall_policy = "harmful_breakout"
             elif self._consecutive_stalls >= self._stall_threshold:
                  if self._total_probes >= self._min_probes_before_stop:
                       # Check if any mechanic prior predicts a delayed effect
                       has_delayed_prediction = False
                       for prior in (mechanic_priors or []):
                            if prior.get("predicts_delayed_reward") and not prior.get("effect_observed"):
                                 has_delayed_prediction = True
                                 break
                       
                       if not has_delayed_prediction:
                            decision.mode = ReasoningMode.EARLY_STOP
                            decision.trigger = "single_action_terminal_stall"
                            decision.stall_policy = "terminal_stop"
                       else:
                            decision.mode = ReasoningMode.CHEAP_PROBE
                            decision.trigger = "delayed_reward_probe"
                            decision.stall_policy = "delayed_reward_wait"
                  else:
                       decision.mode = ReasoningMode.CHEAP_PROBE
                       decision.trigger = "min_probe_requirement"
                       decision.stall_policy = "probing"
             else:
                  decision.mode = ReasoningMode.CHEAP_PROBE
                  decision.trigger = "single_action_stall_mitigation"
                  decision.stall_policy = "probing"
             
        # Rule 2: No-op / Churn on legal action with no legal alternatives
        elif len(available_actions) == 1 and self._consecutive_stalls > 1:
             has_delayed_prediction = self._has_delayed_reward_prior(mechanic_priors or [])
             tick_budget = (
                 self._max_single_action_tick_probes
                 if has_delayed_prediction
                 else min(self._max_single_action_tick_probes, self._max_unproductive_single_action_tick_probes)
             )
             if max_prediction_falsifications >= self._prediction_falsification_reclassify_threshold:
                 decision.mode = ReasoningMode.EARLY_STOP
                 decision.trigger = "single_legal_action_prediction_falsified"
                 decision.stall_policy = "strategy_exhausted"
                 decision.world_model_decision = "single_action_prediction_falsified"
             elif self._single_action_tick_probe_count >= tick_budget:
                 decision.mode = ReasoningMode.EARLY_STOP
                 decision.trigger = "single_legal_action_probe_budget_exhausted"
                 decision.stall_policy = "strategy_exhausted"
                 decision.world_model_decision = "single_action_probe_exhausted"
             else:
                 decision.mode = ReasoningMode.CHEAP_PROBE
                 decision.trigger = "delayed_reward_probe" if has_delayed_prediction else "single_legal_action_stalling"
                 decision.stall_policy = "delayed_reward_wait" if has_delayed_prediction else "probing"
                 self._single_action_tick_probe_count += 1

        # A085 Rule 3: Multi-action churn gate
        elif len(available_actions) > 1 and per_action_evidence:
            actions_tested, productive_actions, churn_detected = self._detect_multi_action_churn(
                per_action_evidence=per_action_evidence,
                available_actions=available_actions,
                current_step=getattr(compiled_delta, "step", 0),
                has_progress=has_progress
            )
            decision.actions_tested_count = actions_tested
            decision.productive_action_count = productive_actions
            decision.multi_action_churn_detected = churn_detected
            contradiction_count = int(budget_state.get("world_model_contradiction_count", 0) or 0)
            all_actions_churn_evidence = budget_state.get("all_actions_churn_evidence") or {}
            all_actions_churn = bool(all_actions_churn_evidence.get("all_actions_churn", False))
            route_transition_evidence = budget_state.get("route_transition_evidence") or {}
            has_route_evidence = bool(route_transition_evidence.get("has_route_evidence", False))
            active_goal_type = str(budget_state.get("active_goal_type") or "")
            active_goal_confidence = float(budget_state.get("active_goal_confidence", 0.0) or 0.0)
            graph_configuration_goal_active = (
                active_goal_confidence >= 0.55
                and active_goal_type in {
                    "color_correspondence",
                    "endpoint_connection",
                    "collect_or_activate",
                    "level_advance",
                }
            )
            route_regression_exhausted = bool(
                has_route_evidence
                and (
                    self._consecutive_route_regressions >= self._route_regression_threshold
                    or route_transition_evidence.get("has_recent_route_regression")
                    or int(route_transition_evidence.get("recent_regression_streak", 0) or 0) >= self._route_regression_threshold
                )
            )
            contradiction_pressure = (
                contradiction_count >= self._contradiction_probe_threshold
                and actions_tested >= max(1, len(available_actions))
                and not has_progress
            )
            try:
                best_route_delta = route_transition_evidence.get("best_distance_delta")
                best_route_delta = float(best_route_delta) if best_route_delta is not None else None
            except (TypeError, ValueError):
                best_route_delta = None
            improving_route_count = int(route_transition_evidence.get("improving_transition_count", 0) or 0)
            route_follow_ready = bool(
                has_route_evidence
                and not graph_configuration_goal_active
                and not route_regression_exhausted
                and improving_route_count > 0
                and (best_route_delta is None or best_route_delta < -0.01)
                and actions_tested >= min(2, max(1, len(available_actions)))
            )
            
            if churn_detected and effect_class == "harmful":
                decision.mode = ReasoningMode.LLM_REASON
                decision.trigger = "harmful_outcome_reclassify"
                decision.stall_policy = "harmful_breakout"
                self._consecutive_multi_action_churn_probes = 0
            elif route_follow_ready:
                decision.mode = ReasoningMode.CHEAP_PROBE
                decision.trigger = "graph_route_follow"
                decision.stall_policy = "route_follow"
                decision.world_model_decision = "follow_graph_route"
                self._consecutive_multi_action_churn_probes = 0
            elif route_regression_exhausted and actions_tested >= max(1, len(available_actions)):
                if graph_configuration_goal_active:
                    decision.mode = ReasoningMode.LLM_REASON
                    decision.trigger = "route_regression_suppressed_for_graph_goal"
                    decision.stall_policy = "configuration_search"
                    decision.early_stop_suppressed_reason = f"{active_goal_type}_active"
                    self._consecutive_route_regressions = 0
                    self._consecutive_multi_action_churn_probes = 0
                else:
                    decision.mode = ReasoningMode.EARLY_STOP
                    decision.trigger = "route_regression_exhausted"
                    decision.stall_policy = "strategy_exhausted"
                    decision.world_model_decision = "route_regression_exhausted"
                    self._consecutive_multi_action_churn_probes = 0
            elif contradiction_pressure and max_prediction_falsifications >= self._prediction_falsification_reclassify_threshold:
                decision.mode = ReasoningMode.LLM_REASON
                decision.trigger = "prediction_falsification_reclassify"
                decision.stall_policy = "reclassify_after_prediction_falsification"
                self._consecutive_multi_action_churn_probes = 0
            elif (churn_detected or contradiction_pressure) and self._consecutive_multi_action_churn_probes >= self._max_multi_action_churn_probes:
                enough_graph_exhaustion = (
                    all_actions_churn
                    and int(all_actions_churn_evidence.get("actions_tested_count", 0) or 0) >= max(1, len(available_actions))
                    and int(all_actions_churn_evidence.get("total_churn_count", 0) or 0) >= max(1, len(available_actions))
                )
                enough_epoch_exhaustion = (
                    all_actions_churn
                    and self._total_probes >= self._max_multi_action_churn_probes * self._strategy_exhausted_probe_epochs
                )
                if has_route_evidence:
                    decision.mode = ReasoningMode.MULTI_ACTION_CHURN_PROBE
                    decision.trigger = "route_transition_probe"
                    decision.stall_policy = "route_search_required"
                    decision.world_model_decision = "route_search_required"
                    self._consecutive_multi_action_churn_probes = max(0, self._consecutive_multi_action_churn_probes - 1)
                elif enough_graph_exhaustion or enough_epoch_exhaustion:
                    decision.mode = ReasoningMode.EARLY_STOP
                    decision.trigger = "all_actions_churn_strategy_exhausted"
                    decision.stall_policy = "strategy_exhausted"
                    decision.world_model_decision = "strategy_exhausted"  # A094
                else:
                    decision.mode = ReasoningMode.LLM_REASON
                    decision.trigger = "multi_action_churn_budget_exhausted" if churn_detected else "contradiction_probe_budget_exhausted"
                    decision.stall_policy = "reclassify_after_churn_budget"
                    self._consecutive_multi_action_churn_probes = 0
            elif churn_detected and not has_progress:
                # Gate LLM reasoning and switch to bounded experiments
                decision.mode = ReasoningMode.MULTI_ACTION_CHURN_PROBE
                decision.trigger = "multi_action_churn"
                decision.stall_policy = "multi_action_churn_mitigation"
                self._consecutive_multi_action_churn_probes += 1
            elif contradiction_pressure:
                decision.mode = ReasoningMode.MULTI_ACTION_CHURN_PROBE
                decision.trigger = "prediction_contradiction_pressure"
                decision.stall_policy = "contradiction_probe"
                decision.multi_action_churn_detected = True
                self._consecutive_multi_action_churn_probes += 1
            else:
                self._consecutive_multi_action_churn_probes = 0

        # Rule 4: Contradiction -> LLM Reason (Force escalation)
        elif compiled_delta and any(getattr(c, "kind", "") == "contradiction" for c in getattr(compiled_delta, "claims", [])):
             decision.mode = ReasoningMode.LLM_REASON
             decision.trigger = "world_model_contradiction"

        # Update counters
        if decision.mode == ReasoningMode.LLM_REASON:
            self.reason_count += 1
            if decision.trigger == "default_escalation":
                 self.escalation_count += 1
        else:
            decision.skipped_reason = decision.trigger
            self.skip_count += 1
            if decision.mode in (ReasoningMode.CHEAP_PROBE, ReasoningMode.MULTI_ACTION_CHURN_PROBE):
                 self._total_probes += 1
            if churn_detected:
                 self._multi_action_churn_count += 1
            
        return decision

    @staticmethod
    def _has_delayed_reward_prior(mechanic_priors: List[Dict[str, Any]]) -> bool:
        for prior in mechanic_priors:
            if not isinstance(prior, dict):
                continue
            if prior.get("predicts_delayed_reward") and not prior.get("effect_observed"):
                return True
            effects = prior.get("effects") or prior.get("effect_patterns") or prior.get("action_effects") or []
            if isinstance(effects, dict):
                effects = [effects]
            for effect in effects:
                if not isinstance(effect, dict):
                    continue
                effect_class = str(effect.get("effect_class") or effect.get("effect") or effect.get("kind") or "")
                if (
                    effect.get("predicts_delayed_reward")
                    or "delayed" in effect_class
                ) and not effect.get("effect_observed"):
                    return True
        return False

    def _detect_multi_action_churn(
        self,
        per_action_evidence: Dict[str, Any],
        available_actions: List[str],
        current_step: int,
        has_progress: bool
    ) -> tuple[int, int, bool]:
        """A085: Detect when most/all available actions produce churn/no-op effects.
        
        Returns: (actions_tested_count, productive_action_count, churn_detected)
        """
        # Count actions tested and productive results
        actions_tested = 0
        productive_actions = 0
        churn_effects = 0
        
        for action_id in available_actions:
            evidence = per_action_evidence.get(action_id, {})
            test_count = int(evidence.get("tested_count", 0) or 0)
            effect_classes = evidence.get("recent_effects", [])
            
            if test_count > 0:
                actions_tested += 1
            
            # Count productive vs churn effects
            productive = sum(1 for e in effect_classes if e in ("object_progress", "terminal_progress", "meaningful_progress"))
            churn = sum(1 for e in effect_classes if e in ("pixel_churn", "none", "no_op", "local_object_progress"))
            
            if productive > 0:
                productive_actions += 1
            if churn > 0:
                churn_effects += 1
        
        # Gate condition: Most/all actions tested with churn effects and no overall progress
        min_actions_for_gate = len(available_actions) if len(available_actions) <= 2 else max(3, len(available_actions))
        churn_threshold = 0.7  # 70% of tested actions showing churn
        
        churn_detected = (
            actions_tested >= min_actions_for_gate and
            churn_effects > 0 and
            (productive_actions == 0 or churn_effects / max(1, actions_tested) >= churn_threshold) and
            not has_progress and
            current_step > self._last_meaningful_progress_step + self._multi_action_no_progress_cooldown
        )
        
        return actions_tested, productive_actions, churn_detected

    def get_metrics(self) -> Dict[str, Any]:
        """A080: Return cumulative metrics for evaluation."""
        return {
            "reasoning_skip_count": self.skip_count,
            "reasoning_escalation_count": self.escalation_count,
            "llm_reason_count": self.reason_count,
            "consecutive_stalls": self._consecutive_stalls,
            "total_probes": self._total_probes,
            "consecutive_multi_action_churn_probes": self._consecutive_multi_action_churn_probes,
            "max_multi_action_churn_probes": self._max_multi_action_churn_probes,
            "single_action_tick_probe_count": self._single_action_tick_probe_count,
            "max_single_action_tick_probes": self._max_single_action_tick_probes,
            "max_unproductive_single_action_tick_probes": self._max_unproductive_single_action_tick_probes,
            "contradiction_probe_threshold": self._contradiction_probe_threshold,
            "prediction_falsification_reclassify_threshold": self._prediction_falsification_reclassify_threshold,
            "strategy_exhausted_probe_epochs": self._strategy_exhausted_probe_epochs,
            "consecutive_route_regressions": self._consecutive_route_regressions,
            "route_regression_threshold": self._route_regression_threshold,
            "multi_action_churn_count": self._multi_action_churn_count  # A085
        }

    # ── A104: Configuration Cycle Search ───────────────────────────────

    def update_cycle_search_state(
        self,
        current_config_hash: str,
        current_goal_alignment: float = 0.0,
    ) -> None:
        """A104: Update configuration cycle search tracking.
        
        Args:
            current_config_hash: Hash of current mechanic configuration.
            current_goal_alignment: How well current state aligns with goals (0-1).
        """
        if current_config_hash:
            self._cycle_seen_configurations.add(current_config_hash)
        
        if current_goal_alignment > self._last_cycle_goal_alignment:
            self._last_cycle_goal_alignment = current_goal_alignment

    def check_cycle_closed(
        self,
        current_config_hash: str,
        current_goal_alignment: float = 0.0,
    ) -> bool:
        """A104: Check if configuration cycle is closed without goal progress.
        
        Returns: True if cycle is closed and no goal improvement.
        """
        # If we've seen this config before and goal alignment didn't improve
        if current_config_hash in self._cycle_seen_configurations:
            if current_goal_alignment <= self._last_cycle_goal_alignment:
                return True
        
        return False

    def enter_cycle_search_mode(self) -> None:
        """A104: Enter configuration cycle search mode."""
        self._cycle_search_active = True
        self._cycle_probes_count = 0
        self._cycle_seen_configurations.clear()
        self._last_cycle_goal_alignment = 0.0

    def exit_cycle_search_mode(self) -> None:
        """A104: Exit configuration cycle search mode."""
        self._cycle_search_active = False
        self._cycle_seen_configurations.clear()

    def is_cycle_search_budget_exhausted(self) -> bool:
        """A104: Check if cycle search probe budget is exhausted."""
        return self._cycle_probes_count >= self._max_cycle_probes

    def increment_cycle_probe(self) -> None:
        """A104: Increment cycle search probe counter."""
        self._cycle_probes_count += 1
