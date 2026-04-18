"""
A/B Evaluation Harness for SideQuests vs. Baseline

Implements the protocol defined in benchmarks/ab_contract.md
"""

import asyncio
import json
import hashlib
import math
import time
import random
import numpy as np
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum
import uuid

from benchmarks.harness import BenchmarkHarness, BenchmarkConfig, BenchmarkResult


class ABVariant(str, Enum):
    """A/B test variant."""
    BASELINE = "baseline"
    SIDEQUESTS = "sidequests"


@dataclass
class ABTask:
    """Represents a single task in A/B evaluation."""
    task_id: str
    category: str
    prompt: str
    expected_output: Optional[str] = None
    reference_solution: Optional[str] = None

    @property
    def prompt_hash(self) -> str:
        """SHA256 hash of task prompt."""
        return hashlib.sha256(self.prompt.encode()).hexdigest()


@dataclass
class ABTaskResult:
    """Result of executing a single task."""
    task_id: str
    variant: ABVariant
    correct: bool
    steps: int
    tokens_input: int
    tokens_output: int
    error_message: Optional[str] = None
    failure_class: Optional[str] = None
    response_text: Optional[str] = None
    attempts: int = 1
    cost_usd: Optional[float] = None
    invalid_action_count: Optional[int] = None
    dissonance_triggered: Optional[bool] = None
    trajectory_score: Optional[dict] = None
    timestamp: float = field(default_factory=time.time)
    final_state: Optional[str] = None  # WIN, GAME_OVER, NOT_FINISHED
    final_observation: Optional[dict] = None  # Full observation with grid, state, etc.
    judge_verdict: Optional[dict] = None  # B181: LLM-as-Judge near-miss grading

    @property
    def total_tokens(self) -> int:
        try:
            return int(self.tokens_input) + int(self.tokens_output)
        except (TypeError, ValueError):
            return 0


@dataclass
class ABTaskManifest:
    """Task manifest with checksums for reproducibility."""
    manifest_version: str = "1.0"
    global_seed: int = 42
    timestamp: str = ""
    tasks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def task_set_hash(self) -> str:
        """SHA256 of concatenated task prompts."""
        task_prompts = "".join(t["prompt"] for t in self.tasks)
        return hashlib.sha256(task_prompts.encode()).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "manifest_version": self.manifest_version,
            "global_seed": self.global_seed,
            "timestamp": self.timestamp,
            "task_set_hash": self.task_set_hash,
            "tasks": self.tasks
        }


@dataclass
class ABRunMetadata:
    """Metadata for a single A/B run."""
    run_id: str
    variant: ABVariant
    timestamp: str
    seed: int
    model: str
    task_set_hash: str
    total_tasks: int
    succeeded: int
    failed: int
    total_tokens: int
    wall_time_seconds: float
    config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)


@dataclass
class ABComparison:
    """Results of comparing baseline vs. sidequests."""
    comparison_id: str
    timestamp: str
    baseline_run_id: str
    sidequests_run_id: str
    metrics: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tasks_where_sidequests_helped: List[Dict[str, Any]] = field(default_factory=list)
    caveats: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)


class ABHarness(BenchmarkHarness):
    """
    A/B evaluation harness for comparing SideQuests vs. baseline.

    Implements the protocol from benchmarks/ab_contract.md:
    - Fixed random seeds for reproducibility
    - Identical task sequences for both variants
    - Automated metrics collection
    - Standardized result format
    """

    def __init__(self, config: BenchmarkConfig, global_seed: int = 42):
        super().__init__(config)
        self.global_seed = global_seed
        self.tasks: List[ABTask] = []
        self.manifest: Optional[ABTaskManifest] = None
        self.baseline_results: List[ABTaskResult] = []
        self.sidequests_results: List[ABTaskResult] = []
        self._set_global_seed(global_seed)

    def _set_global_seed(self, seed: int) -> None:
        """
        Set global random seed for reproducibility.

        Applies seed to all RNG sources used in the harness.
        """
        random.seed(seed)
        np.random.seed(seed)
        # Note: torch seed would be set here if torch is used
        # torch.manual_seed(seed)

    def create_task_manifest(self, tasks: List[ABTask]) -> ABTaskManifest:
        """Create task manifest with checksums."""
        self.tasks = tasks
        manifest = ABTaskManifest(
            global_seed=self.global_seed,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            tasks=[
                {
                    "task_id": t.task_id,
                    "category": t.category,
                    "prompt": t.prompt,
                    "prompt_hash": t.prompt_hash,
                    "expected_output": t.expected_output,
                    "reference_solution": t.reference_solution,
                }
                for t in tasks
            ]
        )
        self.manifest = manifest
        return manifest

    async def setup(self) -> None:
        """Initialize benchmark-specific resources."""
        # Subclasses can override to set up task-specific resources
        pass

    async def run(self) -> BenchmarkResult:
        """
        Execute A/B comparison (not used directly; use run_variant instead).
        """
        return BenchmarkResult(
            benchmark_name=self.config.name,
            success=True,
            duration=0.0,
            metrics={}
        )

    async def teardown(self) -> None:
        """Clean up benchmark-specific resources."""
        pass

    async def run_variant(self, variant: ABVariant) -> Tuple[List[ABTaskResult], float]:
        """
        Run all tasks for a specific variant (baseline or sidequests).

        Returns:
            (task_results, wall_time_seconds)
        """
        if not self.tasks:
            raise ValueError("No tasks loaded. Call create_task_manifest first.")

        start_time = time.perf_counter()
        results = []

        for task in self.tasks:
            result = await self._execute_task(task, variant)
            results.append(result)

        wall_time = time.perf_counter() - start_time

        if variant == ABVariant.BASELINE:
            self.baseline_results = results
        else:
            self.sidequests_results = results

        return results, wall_time

    async def _execute_task(self, task: ABTask, variant: ABVariant) -> ABTaskResult:
        """
        Execute a single task and record results.

        Subclasses should override this to implement task execution logic.
        """
        # Placeholder: subclasses implement actual task execution
        await asyncio.sleep(0.1)
        return ABTaskResult(
            task_id=task.task_id,
            variant=variant,
            correct=True,
            steps=1,
            tokens_input=100,
            tokens_output=50,
            error_message=None,
            response_text="placeholder response"
        )

    @staticmethod
    def _metric_from_result(
        result: ABTaskResult,
        attr_name: str,
        benchmark_path: Tuple[str, ...] = (),
    ) -> Any:
        """Read an optional metric directly from the task result or benchmark_metrics."""
        value = getattr(result, attr_name, None)
        if value is not None:
            return value

        current = getattr(result, "benchmark_metrics", {}) or {}
        for key in benchmark_path:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return current

    @staticmethod
    def _is_numeric_metric(value: Any) -> bool:
        """Return True when the value can participate in delta math."""
        return isinstance(value, (int, float, np.number)) and not isinstance(value, bool)

    @classmethod
    def _format_delta(cls, baseline_val: float, sidequests_val: float) -> str:
        """Format percent deltas consistently, including zero and infinity edges."""
        if not math.isfinite(baseline_val) or not math.isfinite(sidequests_val):
            if baseline_val == sidequests_val:
                return "+0.0%"
            if math.isfinite(baseline_val) and math.isinf(sidequests_val):
                return "+inf%" if sidequests_val > 0 else "-inf%"
            return "N/A"

        if baseline_val != 0:
            delta_pct = ((sidequests_val - baseline_val) / abs(baseline_val)) * 100
        elif sidequests_val != 0:
            delta_pct = float("inf") if sidequests_val > 0 else float("-inf")
        else:
            delta_pct = 0.0

        if delta_pct == float("inf"):
            return "+inf%"
        if delta_pct == float("-inf"):
            return "-inf%"
        return f"{delta_pct:+.1f}%"

    @classmethod
    def _build_metric_comparison(cls, baseline_val: Any, sidequests_val: Any) -> Dict[str, Any]:
        """Compare numeric or dict-valued metrics between baseline and SideQuests."""
        if isinstance(baseline_val, dict) or isinstance(sidequests_val, dict):
            baseline_dict = baseline_val if isinstance(baseline_val, dict) else {}
            sidequests_dict = sidequests_val if isinstance(sidequests_val, dict) else {}
            delta_raw: Dict[str, Any] = {}
            delta_fmt: Dict[str, Any] = {}

            for key in sorted(set(baseline_dict) | set(sidequests_dict)):
                base_item = baseline_dict.get(key)
                side_item = sidequests_dict.get(key)
                if cls._is_numeric_metric(base_item) and cls._is_numeric_metric(side_item):
                    delta_raw[key] = round(float(side_item) - float(base_item), 4)
                    delta_fmt[key] = cls._format_delta(float(base_item), float(side_item))
                else:
                    delta_raw[key] = None
                    delta_fmt[key] = "N/A"

            return {
                "baseline": baseline_val,
                "sidequests": sidequests_val,
                "delta": delta_fmt,
                "delta_raw": delta_raw,
            }

        if not cls._is_numeric_metric(baseline_val) or not cls._is_numeric_metric(sidequests_val):
            return {
                "baseline": baseline_val,
                "sidequests": sidequests_val,
                "delta": "N/A",
                "delta_raw": None,
            }

        baseline_num = float(baseline_val)
        sidequests_num = float(sidequests_val)
        delta_raw = (
            round(sidequests_num - baseline_num, 4)
            if math.isfinite(baseline_num) and math.isfinite(sidequests_num)
            else None
        )
        return {
            "baseline": baseline_val,
            "sidequests": sidequests_val,
            "delta": cls._format_delta(baseline_num, sidequests_num),
            "delta_raw": delta_raw,
        }

    def _compute_metrics(self, results: List[ABTaskResult]) -> Dict[str, Any]:
        """Compute legacy metrics plus B182's four quality dimensions."""
        if not results:
            return {}

        total = len(results)
        correct_results = [r for r in results if r.correct]
        failed_results = [r for r in results if not r.correct]
        correct_count = len(correct_results)
        total_steps = sum(max(int(getattr(r, "steps", 0) or 0), 0) for r in results)
        total_tokens = sum(r.total_tokens for r in results)

        solve_rate = correct_count / total if total else 0.0
        avg_steps = (sum(r.steps for r in correct_results) / correct_count) if correct_count else 0.0
        token_efficiency = total_tokens / correct_count if correct_count > 0 else float("inf")

        judge_scores: List[float] = []
        hallucination_count = 0
        archetype_scores: Dict[str, List[float]] = {}
        for result in results:
            verdict = result.judge_verdict
            if not isinstance(verdict, dict):
                continue
            score = verdict.get("composite_score")
            if score is None:
                continue
            score_value = float(score)
            judge_scores.append(score_value)
            archetype = str(verdict.get("archetype") or "unknown")
            archetype_scores.setdefault(archetype, []).append(score_value)
            reasoning_score = verdict.get("reasoning_score")
            explanation = str(verdict.get("explanation") or "").lower()
            if (reasoning_score is not None and float(reasoning_score) <= 1) or "hallucin" in explanation:
                hallucination_count += 1

        avg_judge_score = sum(judge_scores) / len(judge_scores) if judge_scores else 0.0
        near_miss_rate: Any = "N/A"
        if judge_scores:
            near_misses = sum(
                1
                for result in failed_results
                if isinstance(result.judge_verdict, dict)
                and float(result.judge_verdict.get("composite_score", 0) or 0) >= 3.0
            )
            near_miss_rate = round(near_misses / total, 4) if total else 0.0

        judge_score_by_archetype: Any = "N/A"
        if archetype_scores:
            judge_score_by_archetype = {
                archetype: round(sum(scores) / len(scores), 2)
                for archetype, scores in sorted(archetype_scores.items())
            }

        cost_values = [
            float(value)
            for result in results
            if (value := self._metric_from_result(result, "cost_usd", ("token_cost", "cost_usd"))) is not None
        ]
        solved_cost_values = [
            float(value)
            for result in correct_results
            if (value := self._metric_from_result(result, "cost_usd", ("token_cost", "cost_usd"))) is not None
        ]
        solved_tokens = [result.total_tokens for result in correct_results]

        avg_steps_per_solve: Any = round(avg_steps, 2) if correct_count else "N/A"
        avg_tokens_per_solve: Any = (
            round(sum(solved_tokens) / len(solved_tokens), 2) if solved_tokens else "N/A"
        )
        avg_cost_per_solve: Any = (
            round(sum(solved_cost_values) / len(solved_cost_values), 5)
            if solved_cost_values
            else "N/A"
        )
        tokens_per_step: Any = round(total_tokens / total_steps, 2) if total_steps else "N/A"
        cost_per_step: Any = (
            round(sum(cost_values) / total_steps, 4)
            if cost_values and total_steps
            else "N/A"
        )

        error_counts: Dict[str, int] = {}
        for result in results:
            if result.error_message:
                error_counts[result.error_message] = error_counts.get(result.error_message, 0) + 1
        repeated_mistakes = sum(1 for count in error_counts.values() if count > 1) / total if total else 0.0

        retry_rate = round(
            sum(1 for result in results if int(getattr(result, "attempts", 1) or 1) > 1) / total,
            4,
        ) if total else 0.0

        failure_classes = [str(result.failure_class) for result in failed_results if result.failure_class]
        has_failure_taxonomy = bool(failure_classes) or not failed_results
        if has_failure_taxonomy:
            crash_rate: Any = round(failure_classes.count("crash") / total, 4) if total else 0.0
            timeout_rate: Any = round(failure_classes.count("llm_timeout") / total, 4) if total else 0.0
            budget_exceeded_rate: Any = round(failure_classes.count("budget_exceeded") / total, 4) if total else 0.0
            strategy_exhausted_rate: Any = round(failure_classes.count("strategy_exhausted") / total, 4) if total else 0.0
            coverage_saturated_rate: Any = round(failure_classes.count("coverage_saturated_abort") / total, 4) if total else 0.0
            loop_stuck_rate: Any = round(failure_classes.count("stuck_in_loop") / total, 4) if total else 0.0
        else:
            crash_rate = "N/A"
            timeout_rate = "N/A"
            budget_exceeded_rate = "N/A"
            strategy_exhausted_rate = "N/A"
            coverage_saturated_rate = "N/A"
            loop_stuck_rate = "N/A"

        invalid_action_values = [
            int(value)
            for result in results
            if (value := self._metric_from_result(result, "invalid_action_count", ("prompt_budget", "invalid_action_count"))) is not None
        ]
        invalid_action_rate: Any = (
            round(sum(invalid_action_values) / total_steps, 4)
            if invalid_action_values and total_steps
            else "N/A"
        )

        dissonance_values = [
            bool(result.dissonance_triggered)
            for result in results
            if getattr(result, "dissonance_triggered", None) is not None
        ]
        dissonance_trigger_rate: Any = (
            round(sum(1 for triggered in dissonance_values if triggered) / total, 4)
            if dissonance_values and total
            else "N/A"
        )

        hallucination_rate: Any = (
            round(hallucination_count / len(judge_scores), 4)
            if judge_scores
            else "N/A"
        )

        effectiveness = {
            "solve_rate": round(solve_rate, 4),
            "judge_score_avg": round(avg_judge_score, 2) if judge_scores else "N/A",
            "near_miss_rate": near_miss_rate,
            "judge_score_by_archetype": judge_score_by_archetype,
        }
        efficiency = {
            "avg_steps_per_solve": avg_steps_per_solve,
            "avg_tokens_per_solve": avg_tokens_per_solve,
            "avg_cost_per_solve": avg_cost_per_solve,
            "tokens_per_step": tokens_per_step,
            "cost_per_step": cost_per_step,
        }
        robustness = {
            "crash_rate": crash_rate,
            "timeout_rate": timeout_rate,
            "budget_exceeded_rate": budget_exceeded_rate,
            "strategy_exhausted_rate": strategy_exhausted_rate,
            "coverage_saturated_rate": coverage_saturated_rate,
            "loop_stuck_rate": loop_stuck_rate,
            "retry_rate": retry_rate,
        }
        safety_alignment = {
            "invalid_action_rate": invalid_action_rate,
            "dissonance_trigger_rate": dissonance_trigger_rate,
            "hallucination_rate": hallucination_rate,
        }

        return {
            "solve_rate": round(solve_rate, 4),
            "steps_to_solve": round(avg_steps, 2),
            "token_efficiency": round(token_efficiency, 2) if math.isfinite(token_efficiency) else float("inf"),
            "avg_judge_score": round(avg_judge_score, 2),
            "repeated_mistakes": round(repeated_mistakes, 4),
            "judge_score_avg": effectiveness["judge_score_avg"],
            "near_miss_rate": near_miss_rate,
            "judge_score_by_archetype": judge_score_by_archetype,
            "avg_steps_per_solve": avg_steps_per_solve,
            "avg_tokens_per_solve": avg_tokens_per_solve,
            "avg_cost_per_solve": avg_cost_per_solve,
            "tokens_per_step": tokens_per_step,
            "cost_per_step": cost_per_step,
            "crash_rate": crash_rate,
            "timeout_rate": timeout_rate,
            "budget_exceeded_rate": budget_exceeded_rate,
            "strategy_exhausted_rate": strategy_exhausted_rate,
            "coverage_saturated_rate": coverage_saturated_rate,
            "loop_stuck_rate": loop_stuck_rate,
            "retry_rate": retry_rate,
            "invalid_action_rate": invalid_action_rate,
            "dissonance_trigger_rate": dissonance_trigger_rate,
            "hallucination_rate": hallucination_rate,
            "total_tokens": total_tokens,
            "succeeded": correct_count,
            "failed": len(results) - correct_count,
            "quality_dimensions": {
                "effectiveness": effectiveness,
                "efficiency": efficiency,
                "robustness": robustness,
                "safety_alignment": safety_alignment,
            },
        }

    def generate_run_metadata(self, variant: ABVariant, wall_time: float) -> ABRunMetadata:
        """Generate metadata for a completed run."""
        results = self.baseline_results if variant == ABVariant.BASELINE else self.sidequests_results

        metrics = self._compute_metrics(results)

        return ABRunMetadata(
            run_id=str(uuid.uuid4()),
            variant=variant,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            seed=self.global_seed,
            model=self.config.parameters.get("model", "unknown"),
            task_set_hash=self.manifest.task_set_hash if self.manifest else "unknown",
            total_tasks=len(results),
            succeeded=metrics.get("succeeded", 0),
            failed=metrics.get("failed", 0),
            total_tokens=metrics.get("total_tokens", 0),
            wall_time_seconds=wall_time,
            config={k: v for k, v in self.config.parameters.items() if k != "tasks"}
        )

    async def run_ab_comparison(self) -> Tuple[ABComparison, ABRunMetadata, ABRunMetadata]:
        """
        Run full A/B comparison: baseline followed by sidequests.

        Returns:
            (comparison, baseline_metadata, sidequests_metadata)
        """
        # Reset seed before each variant to ensure reproducibility
        self._set_global_seed(self.global_seed)

        await self.setup()

        try:
            # Run baseline
            baseline_results, baseline_wall_time = await self.run_variant(ABVariant.BASELINE)
            baseline_metadata = self.generate_run_metadata(ABVariant.BASELINE, baseline_wall_time)

            # Reset seed before sidequests to ensure same initial state
            self._set_global_seed(self.global_seed)

            # Run sidequests
            sidequests_results, sidequests_wall_time = await self.run_variant(ABVariant.SIDEQUESTS)
            sidequests_metadata = self.generate_run_metadata(ABVariant.SIDEQUESTS, sidequests_wall_time)

            # Compare results
            comparison = self._compare_results(baseline_results, sidequests_results, baseline_metadata, sidequests_metadata)

            return comparison, baseline_metadata, sidequests_metadata

        finally:
            await self.teardown()

    def _compare_results(
        self,
        baseline: List[ABTaskResult],
        sidequests: List[ABTaskResult],
        baseline_meta: ABRunMetadata,
        sidequests_meta: ABRunMetadata
    ) -> ABComparison:
        """Generate comparison between baseline and sidequests."""
        baseline_metrics = self._compute_metrics(baseline)
        sidequests_metrics = self._compute_metrics(sidequests)

        metrics_dict: Dict[str, Dict[str, Any]] = {}
        metric_keys = sorted(set(baseline_metrics) | set(sidequests_metrics))
        for key in metric_keys:
            if key == "quality_dimensions":
                continue
            metrics_dict[key] = self._build_metric_comparison(
                baseline_metrics.get(key, "N/A"),
                sidequests_metrics.get(key, "N/A"),
            )

        # Identify tasks where sidequests helped
        helped_tasks = []
        for b, s in zip(baseline, sidequests):
            if not b.correct and s.correct:
                helped_tasks.append({
                    "task_id": b.task_id,
                    "reason": "Baseline failed, SideQuests succeeded"
                })
            elif b.correct and s.correct and s.steps < b.steps:
                helped_tasks.append({
                    "task_id": b.task_id,
                    "reason": f"Fewer steps: {b.steps} → {s.steps}"
                })

        return ABComparison(
            comparison_id=str(uuid.uuid4()),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            baseline_run_id=baseline_meta.run_id,
            sidequests_run_id=sidequests_meta.run_id,
            metrics=metrics_dict,
            tasks_where_sidequests_helped=helped_tasks,
            caveats="A/B comparison completed. See task logs for detailed analysis."
        )

    def save_results(self, comparison: ABComparison, baseline_meta: ABRunMetadata, sidequests_meta: ABRunMetadata, output_dir: str = "benchmarks/results") -> None:
        """Save all A/B results and metadata."""
        import os
        os.makedirs(output_dir, exist_ok=True)

        # Save comparison
        comparison_path = os.path.join(output_dir, f"ab_comparison_{comparison.comparison_id}.json")
        with open(comparison_path, 'w') as f:
            json.dump(comparison.to_dict(), f, indent=2, default=str)

        # Save baseline metadata
        baseline_path = os.path.join(output_dir, f"baseline_{baseline_meta.run_id}.json")
        with open(baseline_path, 'w') as f:
            json.dump(baseline_meta.to_dict(), f, indent=2, default=str)

        # Save sidequests metadata
        sidequests_path = os.path.join(output_dir, f"sidequests_{sidequests_meta.run_id}.json")
        with open(sidequests_path, 'w') as f:
            json.dump(sidequests_meta.to_dict(), f, indent=2, default=str)

        # Save task manifest
        manifest_path = os.path.join(output_dir, f"manifest_{self.manifest.task_set_hash[:8]}.json")
        with open(manifest_path, 'w') as f:
            json.dump(self.manifest.to_dict(), f, indent=2, default=str)

        # Save detailed task results
        task_results_path = os.path.join(output_dir, f"task_results_{comparison.comparison_id}.json")
        task_results = []
        for b, s in zip(self.baseline_results, self.sidequests_results):
            task_results.append({
                "task_id": b.task_id,
                "baseline_correct": b.correct,
                "baseline_steps": b.steps,
                "baseline_tokens": {"input": b.tokens_input, "output": b.tokens_output},
                "sidequests_correct": s.correct,
                "sidequests_steps": s.steps,
                "sidequests_tokens": {"input": s.tokens_input, "output": s.tokens_output},
                "error_message": b.error_message or "none",
            })

        with open(task_results_path, 'w') as f:
            json.dump(task_results, f, indent=2, default=str)
