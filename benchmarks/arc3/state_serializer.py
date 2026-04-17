"""State-to-text serializer for ARC spatial transitions with causal preservation."""

from __future__ import annotations

import json
from typing import Any, List, Mapping, Optional, Tuple


class StateSerializerForARC:
    """Normalize ARC state transitions into reversible, token-efficient forms."""

    def __init__(self, max_tokens_per_step: int = 256) -> None:
        """Initialize the serializer with token budget limits."""
        self.max_tokens_per_step = max_tokens_per_step
        self._step_logs: List[Mapping[str, Any]] = []

    def serialize_transition(
        self,
        before_state: List[List[int]],
        after_state: List[List[int]],
        action: Mapping[str, Any],
        reward: Optional[float] = None,
        done: bool = False,
    ) -> Mapping[str, Any]:
        """Convert a state transition into machine and human-readable forms.
        
        Args:
            before_state: Grid before action
            after_state: Grid after action
            action: Normalized ARC3Action object
            reward: Reward signal from environment
            done: Whether episode is terminal
        
        Returns:
            Dict with 'machine_delta', 'narrative', 'tokens_used', and validation fields
        """
        # Compute pixel-level delta
        delta = self._compute_delta(before_state, after_state)
        
        # Generate human-readable narrative
        narrative = self._narrative_from_delta(delta, action, reward, done)
        
        # Estimate token cost
        machine_str = json.dumps(delta, separators=(",", ":"))
        tokens_used = self._estimate_tokens(machine_str + " " + narrative)
        
        # Validate round-trip reconstruction
        reconstructed = self._reconstruct_state(before_state, delta)
        is_valid = self._states_equal(reconstructed, after_state)
        
        result = {
            "machine_delta": delta,
            "narrative": narrative,
            "tokens_used": tokens_used,
            "is_valid": is_valid,
            "action": action.get("action_type"),
            "reward": reward,
            "done": done,
        }
        self._step_logs.append(result)
        return result

    def get_step_logs(self) -> List[Mapping[str, Any]]:
        """Return all recorded step transitions."""
        return [dict(log) for log in self._step_logs]

    def get_fidelity_score(self) -> float:
        """Return round-trip accuracy percentage (0-100)."""
        if not self._step_logs:
            return 100.0
        valid_count = sum(1 for log in self._step_logs if log["is_valid"])
        return (valid_count / len(self._step_logs)) * 100.0

    def get_token_statistics(self) -> Mapping[str, float]:
        """Return token usage stats across all steps."""
        if not self._step_logs:
            return {"total": 0, "avg": 0, "max": 0}
        tokens = [log["tokens_used"] for log in self._step_logs]
        return {
            "total": sum(tokens),
            "avg": sum(tokens) / len(tokens),
            "max": max(tokens),
        }

    # --- Helper methods ---

    def _compute_delta(
        self, before: List[List[int]], after: List[List[int]]
    ) -> Mapping[str, Any]:
        """Compute exact pixel changes between two grids."""
        changes = []
        
        # Handle grid size mismatch gracefully
        rows_before = len(before)
        rows_after = len(after)
        cols_before = len(before[0]) if before else 0
        cols_after = len(after[0]) if after else 0
        
        max_rows = max(rows_before, rows_after)
        max_cols = max(cols_before, cols_after)
        
        for r in range(max_rows):
            for c in range(max_cols):
                before_val = before[r][c] if r < rows_before and c < cols_before else None
                after_val = after[r][c] if r < rows_after and c < cols_after else None
                
                if before_val != after_val:
                    changes.append({
                        "coords": [r, c],
                        "before": before_val,
                        "after": after_val,
                    })
        
        return {
            "num_changes": len(changes),
            "changes": changes,
            "grid_shape_before": [rows_before, cols_before],
            "grid_shape_after": [rows_after, cols_after],
        }

    def _reconstruct_state(
        self, before_state: List[List[int]], delta: Mapping[str, Any]
    ) -> List[List[int]]:
        """Apply delta to before_state and reconstruct after_state."""
        # Start with a deep copy of before_state
        rows, cols = delta["grid_shape_after"]
        reconstructed: List[List[int]] = [[0] * cols for _ in range(rows)]
        
        # Copy over all before values that are still in the new grid
        rows_before, cols_before = delta["grid_shape_before"]
        for r in range(min(rows_before, rows)):
            for c in range(min(cols_before, cols)):
                reconstructed[r][c] = before_state[r][c]
        
        # Apply changes
        for change in delta["changes"]:
            r, c = change["coords"]
            reconstructed[r][c] = change["after"] if change["after"] is not None else 0
        
        return reconstructed

    def _states_equal(self, state_a: List[List[int]], state_b: List[List[int]]) -> bool:
        """Check if two state grids are identical."""
        if len(state_a) != len(state_b):
            return False
        for row_a, row_b in zip(state_a, state_b):
            if len(row_a) != len(row_b) or row_a != row_b:
                return False
        return True

    def _narrative_from_delta(
        self,
        delta: Mapping[str, Any],
        action: Mapping[str, Any],
        reward: Optional[float],
        done: bool,
    ) -> str:
        """Generate compact human/LLM form of the transition."""
        changes = delta["changes"]
        num_changes = len(changes)
        
        if num_changes == 0:
            summary = "no changes"
        elif num_changes == 1:
            change = changes[0]
            coords = change["coords"]
            before = change["before"]
            after = change["after"]
            summary = f"cell {coords} changed {before}→{after}"
        else:
            first_few = changes[:3]
            first_desc = "; ".join(
                f"[{c['coords']}]={c['before']}→{c['after']}"
                for c in first_few
            )
            extra = f" +{num_changes - 3} more" if num_changes > 3 else ""
            summary = f"{first_desc}{extra}"
        
        action_type = action.get("action_type", "UNKNOWN")
        reward_str = f" | reward={reward:.2f}" if reward is not None else ""
        done_str = " | done" if done else ""
        
        return f"{action_type}: {summary}{reward_str}{done_str}"

    def _estimate_tokens(self, text: str) -> int:
        """Rough estimate of token count (4 chars ≈ 1 token)."""
        return max(1, len(text) // 4)
