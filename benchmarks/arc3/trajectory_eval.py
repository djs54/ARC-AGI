from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


@dataclass
class TrajectoryScore:
    """Offline quality score for an ARC trajectory."""

    action_diversity: int
    hypothesis_convergence: int
    exploration_efficiency: int
    plan_adherence: int
    escalation_quality: int
    total: int
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class TrajectoryEvaluator:
    """Pure algorithmic evaluator for ARC trajectories and execution traces."""

    MAX_DIMENSION_SCORE = 20

    def evaluate(
        self,
        trace: Sequence[dict] | None = None,
        step_history: Sequence[dict] | None = None,
    ) -> TrajectoryScore:
        trace_list = [item for item in (trace or []) if isinstance(item, dict)]
        normalized_steps = self._normalize_step_history(step_history or [])
        if not normalized_steps and trace_list:
            normalized_steps = self._extract_step_history_from_trace(trace_list)

        action_diversity, action_details = self._score_action_diversity(normalized_steps)
        convergence, convergence_details = self._score_hypothesis_convergence(normalized_steps, trace_list)
        exploration, exploration_details = self._score_exploration_efficiency(normalized_steps, trace_list)
        adherence, adherence_details = self._score_plan_adherence(normalized_steps)
        escalation, escalation_details = self._score_escalation_quality(trace_list, normalized_steps)

        total = action_diversity + convergence + exploration + adherence + escalation
        return TrajectoryScore(
            action_diversity=action_diversity,
            hypothesis_convergence=convergence,
            exploration_efficiency=exploration,
            plan_adherence=adherence,
            escalation_quality=escalation,
            total=total,
            details={
                "step_count": len(normalized_steps),
                "trace_event_count": len(trace_list),
                "action_diversity": action_details,
                "hypothesis_convergence": convergence_details,
                "exploration_efficiency": exploration_details,
                "plan_adherence": adherence_details,
                "escalation_quality": escalation_details,
            },
        )

    def evaluate_file(self, trace_path: str | Path) -> TrajectoryScore:
        trace, step_history = self.load_trace_artifacts(trace_path)
        return self.evaluate(trace=trace, step_history=step_history)

    @classmethod
    def load_trace_artifacts(cls, trace_path: str | Path) -> Tuple[List[dict], List[dict]]:
        """Load either a JSON trace, a result payload, or JSONL step snapshots."""
        path = Path(trace_path)
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return [], []

        if path.suffix.lower() == ".jsonl":
            items = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            items = json.loads(text)

        evaluator = cls()
        return evaluator._split_trace_payload(items)

    def _split_trace_payload(self, payload: Any) -> Tuple[List[dict], List[dict]]:
        trace: List[dict] = []
        step_history: List[dict] = []

        if isinstance(payload, dict):
            trace = [item for item in (payload.get("agent_execution_trace") or payload.get("trace") or []) if isinstance(item, dict)]
            candidate_steps = payload.get("debug_steps") or payload.get("step_history") or payload.get("progress_log") or []
            if isinstance(candidate_steps, list):
                step_history = self._normalize_step_history(candidate_steps)
            if not step_history and payload.get("snapshot_type") == "step":
                step_history = self._normalize_step_history([payload])
        elif isinstance(payload, list):
            trace = [item for item in payload if isinstance(item, dict) and "event_type" in item]
            step_candidates = [
                item for item in payload
                if isinstance(item, dict)
                and (
                    item.get("snapshot_type") == "step"
                    or "action_id" in item
                    or "frame_hash" in item
                    or "solve_context" in item
                    or "solve_phase_summary" in item
                )
            ]
            if step_candidates:
                step_history = self._normalize_step_history(step_candidates)

        if not step_history and trace:
            step_history = self._extract_step_history_from_trace(trace)
        return trace, step_history

    def _normalize_step_history(self, step_history: Sequence[dict]) -> List[dict]:
        normalized: List[dict] = []
        for item in step_history:
            if not isinstance(item, dict):
                continue

            solve_context = item.get("solve_context") or item.get("solve_phase_summary") or {}
            frame_analysis = item.get("frame_analysis") or {}
            normalized.append(
                {
                    "step": self._coerce_int(item.get("step"), default=len(normalized) + 1),
                    "action_id": item.get("action_id"),
                    "available_actions": item.get("available_actions", []),
                    "frame_hash": item.get("frame_hash") or frame_analysis.get("frame_hash"),
                    "reward": item.get("reward"),
                    "solve_context": solve_context if isinstance(solve_context, dict) else {},
                }
            )

        return sorted(normalized, key=lambda entry: entry.get("step", 0))

    def _extract_step_history_from_trace(self, trace: Sequence[dict]) -> List[dict]:
        by_step: Dict[int, dict] = {}

        for event in trace:
            if not isinstance(event, dict):
                continue
            details = event.get("details") or {}
            result = event.get("result") or {}
            step = self._coerce_int(details.get("step"), default=None)
            if step is None:
                step = self._coerce_int(result.get("step"), default=None) if isinstance(result, dict) else None
            if step is None:
                continue

            entry = by_step.setdefault(
                step,
                {
                    "step": step,
                    "action_id": None,
                    "available_actions": details.get("available_actions", []),
                    "frame_hash": None,
                    "reward": None,
                    "solve_context": {},
                },
            )

            if not entry.get("action_id"):
                entry["action_id"] = details.get("action_id") or (result.get("action_id") if isinstance(result, dict) else None)
            if not entry.get("frame_hash"):
                entry["frame_hash"] = details.get("frame_hash") or (result.get("frame_hash") if isinstance(result, dict) else None)
            if entry.get("reward") is None:
                reward = details.get("reward")
                if reward is None and isinstance(result, dict):
                    reward = result.get("reward")
                entry["reward"] = reward

        return [by_step[idx] for idx in sorted(by_step)]

    @staticmethod
    def _coerce_int(value: Any, default: int | None = 0) -> int | None:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _clamp_score(cls, value: float) -> int:
        return max(0, min(cls.MAX_DIMENSION_SCORE, int(round(value))))

    @staticmethod
    def _normalize_victory(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("type") or "unknown")
        if value in (None, ""):
            return "unknown"
        return str(value)

    def _score_action_diversity(self, step_history: Sequence[dict]) -> Tuple[int, Dict[str, Any]]:
        actions = [str(step.get("action_id")) for step in step_history if step.get("action_id")]
        if not actions:
            return 10, {"reason": "no actions recorded"}

        unique_actions = set(actions)
        available_action_names = set()
        available_count_hint = 0
        for step in step_history:
            available_actions = step.get("available_actions")
            if isinstance(available_actions, (list, tuple, set)):
                available_action_names.update(str(action) for action in available_actions if isinstance(action, str))
                available_count_hint = max(available_count_hint, len(available_actions))
            else:
                available_count_hint = max(available_count_hint, self._coerce_int(available_actions, default=0) or 0)
        available_count = len(available_action_names) or available_count_hint or len(unique_actions)

        if len(unique_actions) <= 1:
            return 0, {
                "unique_actions": len(unique_actions),
                "available_actions": available_count,
                "coverage_ratio": round(len(unique_actions) / max(available_count, 1), 4),
            }

        counts = Counter(actions)
        probabilities = [count / len(actions) for count in counts.values()]
        entropy = -sum(p * math.log(p, 2) for p in probabilities if p > 0)
        max_entropy = math.log(len(counts), 2) if len(counts) > 1 else 1.0
        balance_ratio = entropy / max_entropy if max_entropy > 0 else 1.0
        coverage_ratio = min(len(unique_actions) / max(available_count, 1), 1.0)

        score = self._clamp_score(self.MAX_DIMENSION_SCORE * coverage_ratio * balance_ratio)
        return score, {
            "unique_actions": len(unique_actions),
            "available_actions": available_count,
            "coverage_ratio": round(coverage_ratio, 4),
            "balance_ratio": round(balance_ratio, 4),
        }

    def _extract_hypothesis_signatures_from_trace(self, trace: Sequence[dict]) -> List[Tuple[str, str]]:
        signatures: List[Tuple[str, str]] = []
        for event in trace:
            if not isinstance(event, dict):
                continue
            details = event.get("details") or {}
            result = event.get("result") or {}
            text = " ".join(
                str(part)
                for part in [details.get("content"), result.get("content") if isinstance(result, dict) else None, details.get("goal")]
                if part
            )
            archetype = None
            victory = None
            if text:
                archetype_match = re.search(r"Archetype:\s*([A-Za-z_]+)", text, flags=re.IGNORECASE)
                victory_match = re.search(r"Win condition:\s*([A-Za-z_]+)", text, flags=re.IGNORECASE)
                if archetype_match:
                    archetype = archetype_match.group(1).lower()
                if victory_match:
                    victory = victory_match.group(1).lower()
            if archetype or victory:
                signatures.append((archetype or "unknown", victory or "unknown"))
        return signatures

    def _score_hypothesis_convergence(self, step_history: Sequence[dict], trace: Sequence[dict]) -> Tuple[int, Dict[str, Any]]:
        signatures = [
            (
                str((step.get("solve_context") or {}).get("archetype") or "unknown"),
                self._normalize_victory((step.get("solve_context") or {}).get("victory_condition")),
            )
            for step in step_history
            if step.get("solve_context")
        ]
        if not signatures:
            signatures = self._extract_hypothesis_signatures_from_trace(trace)
        if len(signatures) < 2:
            return 10, {"reason": "insufficient hypothesis updates"}

        changes = sum(1 for idx in range(1, len(signatures)) if signatures[idx] != signatures[idx - 1])
        stable_from = None
        for idx in range(len(signatures)):
            if len(set(signatures[idx:])) == 1:
                stable_from = idx
                break

        if changes >= len(signatures) - 1 and len(signatures) >= 5:
            score = 0
        elif stable_from is not None and stable_from <= 4:
            score = self.MAX_DIMENSION_SCORE
        else:
            oscillation_ratio = changes / max(len(signatures) - 1, 1)
            score = self._clamp_score(self.MAX_DIMENSION_SCORE * (1.0 - oscillation_ratio))

        return score, {
            "changes": changes,
            "stable_from_step_index": stable_from,
            "signatures_seen": ["/".join(sig) for sig in signatures],
        }

    def _score_exploration_efficiency(self, step_history: Sequence[dict], trace: Sequence[dict]) -> Tuple[int, Dict[str, Any]]:
        frame_hashes = [str(step.get("frame_hash")) for step in step_history if step.get("frame_hash")]
        if not frame_hashes:
            for event in trace:
                if not isinstance(event, dict):
                    continue
                details = event.get("details") or {}
                result = event.get("result") or {}
                frame_hash = details.get("frame_hash") or (result.get("frame_hash") if isinstance(result, dict) else None)
                if frame_hash:
                    frame_hashes.append(str(frame_hash))

        if len(frame_hashes) < 2:
            return 10, {"reason": "insufficient frame hashes"}

        seen = set()
        novel_transitions = 0
        for frame_hash in frame_hashes:
            if frame_hash not in seen:
                seen.add(frame_hash)
                novel_transitions += 1

        novel_ratio = max(novel_transitions - 1, 0) / max(len(frame_hashes) - 1, 1)
        score = self._clamp_score(self.MAX_DIMENSION_SCORE * novel_ratio)
        return score, {
            "unique_frames": len(seen),
            "visited_frames": len(frame_hashes),
            "novel_ratio": round(novel_ratio, 4),
        }

    def _score_plan_adherence(self, step_history: Sequence[dict]) -> Tuple[int, Dict[str, Any]]:
        planned_steps = 0
        matches = 0

        for step in step_history:
            solve_context = step.get("solve_context") or {}
            active_chunk = solve_context.get("active_chunk") or {}
            estimated_actions = active_chunk.get("estimated_actions") or []
            action_id = step.get("action_id")
            if not estimated_actions or not action_id:
                continue
            planned_steps += 1
            if action_id in estimated_actions:
                matches += 1

        if planned_steps == 0:
            return 10, {"reason": "no active chunk plans recorded"}

        adherence_ratio = matches / planned_steps
        score = self._clamp_score(self.MAX_DIMENSION_SCORE * adherence_ratio)
        return score, {
            "planned_steps": planned_steps,
            "plan_matches": matches,
            "adherence_ratio": round(adherence_ratio, 4),
        }

    def _score_escalation_quality(self, trace: Sequence[dict], step_history: Sequence[dict]) -> Tuple[int, Dict[str, Any]]:
        escalation_points: List[int] = []

        for event in trace:
            if not isinstance(event, dict):
                continue
            operation = str(event.get("operation") or "")
            event_type = str(event.get("event_type") or "")
            if not (
                "escalation" in operation
                or "replan" in operation
                or "plateau" in operation
                or "plateau" in event_type
            ):
                continue

            details = event.get("details") or {}
            marker = self._coerce_int(details.get("steps"), default=None)
            if marker is None:
                marker = self._coerce_int(details.get("step"), default=None)
            if marker is not None:
                escalation_points.append(marker)

        if not escalation_points:
            default_score = 15 if len(step_history) <= 10 else 10
            return default_score, {"reason": "no explicit escalations recorded"}

        quality_samples: List[int] = []
        for point in escalation_points:
            if 5 <= point <= 15:
                quality_samples.append(self.MAX_DIMENSION_SCORE)
            elif 3 <= point < 5 or 16 <= point <= 18:
                quality_samples.append(14)
            elif 1 <= point < 3 or 19 <= point <= 22:
                quality_samples.append(8)
            else:
                quality_samples.append(4)

        score = self._clamp_score(sum(quality_samples) / len(quality_samples))
        return score, {
            "escalation_points": escalation_points,
            "sample_scores": quality_samples,
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline trajectory evaluator for ARC execution traces")
    parser.add_argument("trace_path", help="Path to agent_execution_trace.json or a JSONL step snapshot file")
    args = parser.parse_args(argv)

    evaluator = TrajectoryEvaluator()
    score = evaluator.evaluate_file(args.trace_path)
    print(json.dumps(score.to_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
