from enum import Enum
from dataclasses import dataclass
from typing import List, Dict, Optional, Any, Tuple
import logging

logger = logging.getLogger(__name__)

class SupervisorDecision(str, Enum):
    CONTINUE = "continue"
    NUDGE = "nudge"
    RESET_STRATEGY = "reset_strategy"
    ABANDON = "abandon"

@dataclass
class SupervisorVerdict:
    decision: SupervisorDecision
    reason: str
    nudge_hint: Optional[str] = None  # Only for NUDGE decisions

class PuzzleSupervisor:
    """B183: Meta-Supervisor Agent for trajectory-aware puzzle monitoring."""

    def __init__(self, llm_client=None, check_interval: int = 5, abandon_zero_reward_steps: int = 30):
        self.llm = llm_client
        self.check_interval = check_interval
        self.abandon_zero_reward_steps = max(int(check_interval), int(abandon_zero_reward_steps))

    async def evaluate(
        self,
        step_history: List[dict],
        execution_trace: List[dict],
        cost_tracker: Optional[Any] = None,
    ) -> SupervisorVerdict:
        """Evaluate agent trajectory and return a strategic decision."""
        step = len(step_history)
        if step == 0:
            return SupervisorVerdict(SupervisorDecision.CONTINUE, "initial step")
            
        if step % self.check_interval != 0:
            return SupervisorVerdict(SupervisorDecision.CONTINUE, f"step {step} not in check interval {self.check_interval}")

        # 1. Rule-based checks (fast path, no LLM)
        verdict = self._rule_based_check(step_history, execution_trace, cost_tracker)
        if verdict:
            logger.info(f"B183: Supervisor rule-based verdict: {verdict.decision.value} - {verdict.reason}")
            return verdict

        # 2. Optional LLM escalation for ambiguous cases
        if self.llm and self._is_ambiguous(step_history):
            logger.info(f"B183: Supervisor escalating ambiguous trajectory to LLM at step {step}")
            return await self._llm_evaluate(step_history, execution_trace)

        return SupervisorVerdict(SupervisorDecision.CONTINUE, "no issues detected by rules")

    def _rule_based_check(self, history: List[dict], trace: List[dict], cost: Optional[Any]) -> Optional[SupervisorVerdict]:
        """Perform deterministic trajectory analysis."""
        if not history:
            return None

        # 1. Oscillation detection: same 2-3 states repeating
        # Use last 10 steps or whatever is available
        recent_history = history[-10:]
        recent_states = [s.get("frame_hash") for s in recent_history if s.get("frame_hash")]
        unique_recent = len(set(recent_states))
        if len(recent_states) >= 8 and unique_recent <= 2:
            return SupervisorVerdict(SupervisorDecision.RESET_STRATEGY,
                f"oscillating between {unique_recent} states for {len(recent_states)} steps")

        # 2. Total stagnation: no reward for 30+ steps
        zero_reward_streak = 0
        for s in reversed(history):
            if float(s.get("reward", 0.0)) <= 0.0:
                zero_reward_streak += 1
            else:
                break
        
        if zero_reward_streak >= self.abandon_zero_reward_steps:
            return SupervisorVerdict(SupervisorDecision.ABANDON,
                f"{zero_reward_streak} consecutive zero-reward steps")

        # 3. Action diversity check: only using 1-2 of N available actions
        # Look at last 15 steps
        recent_actions = [s.get("action_id") for s in history[-15:] if s.get("action_id")]
        last_obs = history[-1].get("next_observation") or {}
        available = last_obs.get("available_actions", [])
        used = set(recent_actions)
        
        if len(available) >= 4 and len(used) <= 2 and len(recent_actions) >= 10:
            untried = [a for a in available if a not in used]
            if untried:
                return SupervisorVerdict(SupervisorDecision.NUDGE,
                    f"low action diversity (used {len(used)} of {len(available)} available)",
                    nudge_hint=f"Strategy nudge: You are stuck using only {', '.join(used)}. You haven't tried actions: {', '.join(untried)}. Try exploring them.")

        # 4. Budget warning: > 70% budget consumed
        if cost and hasattr(cost, 'total_cost_usd') and hasattr(cost, 'budget_usd'):
            if cost.total_cost_usd > cost.budget_usd * 0.7:
                return SupervisorVerdict(SupervisorDecision.NUDGE,
                    f"budget high: {cost.total_cost_usd:.4f} USD used",
                    nudge_hint="Budget warning: You have consumed over 70% of the allocated token budget for this puzzle. Focus on your most promising strategy to reach the goal.")

        # 5. Centroid not moving: player position same for 10+ steps
        # autopilot_player_row/col are recorded by _try_autopilot in step_history
        positions = []
        for s in history[-10:]:
            r = s.get("autopilot_player_row")
            c = s.get("autopilot_player_col")
            if r is not None and c is not None:
                positions.append((round(float(r), 2), round(float(c), 2)))
        
        if len(positions) >= 8 and len(set(positions)) == 1:
            return SupervisorVerdict(SupervisorDecision.RESET_STRATEGY,
                "player position unchanged for 10+ consecutive autopilot steps")

        return None

    def _is_ambiguous(self, history: List[dict]) -> bool:
        """Determine if trajectory warrants LLM analysis."""
        # Ambiguous if we have a medium length zero-reward streak 
        # and haven't found a victory condition yet.
        zero_reward_streak = 0
        for s in reversed(history):
            if float(s.get("reward", 0.0)) <= 0.0:
                zero_reward_streak += 1
            else:
                break
        
        if 15 <= zero_reward_streak < self.abandon_zero_reward_steps:
            # Check if any victory condition is present in last step
            last_step = history[-1]
            sc = last_step.get("solve_context", {})
            if not sc.get("victory_condition"):
                return True
        return False

    async def _llm_evaluate(self, history: List[dict], trace: List[dict]) -> SupervisorVerdict:
        """Use LLM to judge ambiguous trajectory."""
        # Summary of last 10 steps for context
        steps_summary = []
        for s in history[-10:]:
            steps_summary.append({
                "step": s.get("step"),
                "action": s.get("action_id"),
                "rationale": s.get("rationale"),
                "reward": s.get("reward"),
                "state": s.get("frame_hash")[:8] if s.get("frame_hash") else "unknown"
            })

        prompt = f"""You are a Meta-Supervisor for an ARC-AGI agent. 
The agent has been stuck without progress for several steps. 
Analyze the recent trajectory and decide if it should continue, be nudged, reset its strategy, or be abandoned.

RECENT STEPS:
{steps_summary}

DECISION RUBRIC:
- continue: Agent is showing signs of novelty or slow progress.
- nudge: Agent is repeating itself but there are obvious alternatives it hasn't tried.
- reset_strategy: Agent is completely stuck or oscillating; wipe its internal state.
- abandon: Puzzle is likely unsolvable by this agent or too expensive.

Respond in JSON format: {{"decision": "continue|nudge|reset_strategy|abandon", "reason": "...", "nudge_hint": "..."}}
"""
        try:
            import json
            # B184 circuit breaker would wrap this call
            response_text = await self.llm.achat([{"role": "user", "content": prompt}])
            
            # Simple JSON extraction
            if "```json" in response_text:
                response_text = response_text.split("```json")[1].split("```")[0]
            elif "```" in response_text:
                response_text = response_text.split("```")[1].split("```")[0]
            
            data = json.loads(response_text.strip())
            decision_str = data.get("decision", "continue").lower()
            
            # Map string to Enum
            decision_map = {
                "continue": SupervisorDecision.CONTINUE,
                "nudge": SupervisorDecision.NUDGE,
                "reset_strategy": SupervisorDecision.RESET_STRATEGY,
                "abandon": SupervisorDecision.ABANDON
            }
            decision = decision_map.get(decision_str, SupervisorDecision.CONTINUE)
            
            return SupervisorVerdict(
                decision=decision,
                reason=f"LLM Judge: {data.get('reason', 'no reason provided')}",
                nudge_hint=data.get("nudge_hint")
            )
        except Exception as exc:
            logger.warning(f"B183: Supervisor LLM evaluation failed: {exc}")
            return SupervisorVerdict(SupervisorDecision.CONTINUE, f"LLM error: {exc}")
