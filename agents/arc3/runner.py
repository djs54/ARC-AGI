"""Durable ARC run driver tying orchestrator + checkpoints + harness."""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
import atexit
import uuid
import hashlib
import subprocess
import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional, Sequence

from benchmarks.ab_harness import ABHarness, ABTask, ABTaskResult, ABVariant, BenchmarkConfig
from benchmarks.arc3.adapter import ARC3Adapter, BrainClientProtocol, LedgerBrainClient
from benchmarks.arc3.harness import ARC3Harness
from sidequest_mcp_client.observability import build_observability, canonical_span_name
from benchmarks.arc3.outcome_judge import OutcomeJudge
from benchmarks.arc3.regression_monitor import RegressionMonitor, RunRecord
from benchmarks.arc3.trajectory_eval import TrajectoryEvaluator
from arc_runtime.llm import create_llm_client
from agents.arc3.checkpoint import CheckpointManager
from agents.arc3.failure_taxonomy import classify_failure
from agents.arc3.orchestrator import ARCOrchestrator
from agents.arc3.scheduler import PuzzleScheduler
from agents.arc3.strategy_racer import race as strategy_race
from agents.arc3.phase import PhaseController, SolvePhase, IllegalPhaseTransition

logger = logging.getLogger(__name__)

# Bootstrap priors used only when the graph is empty at puzzle start.
_ARCHETYPE_BOOTSTRAP_PRIORS: dict[str, list[str]] = {
    "race": [
        "When target drift per step exceeds player closure velocity, pursuit strategies lose by timeout.",
        "Sustained positive progress from one action should suppress immediate post-replan blacklisting.",
        "If reward collapses after a replan, revert to the last action family with best recent progress.",
        "Route planning should prioritize gap-closing actions over high novelty probes.",
        "If GAME_OVER occurs during pursuit, increase urgency bias and reduce exploratory branching.",
    ],
    "space": [
        "ACTION6 probes are useful only when coordinates are goal-conditioned, not uniform.",
        "When only ACTION6 is available, scorecard diversity penalties should be treated as non-fault noise.",
        "No-op ACTION6 probes should invalidate decision reuse to avoid cache-locked loops.",
        "Goal-color progress should dominate probe selection over frontier novelty when available.",
        "Confidence saturation without progress should rotate hypotheses rather than repeat same target.",
    ],
    "chase": [
        "If pursuer distance trend worsens over multiple steps, switch to evasive or blocking strategy immediately.",
        "Action values should include relative velocity, not just static distance.",
        "Repeated frame changes without reward imply state churn; prefer control actions over random movement.",
        "Short replan intervals can destabilize chase policy; enforce minimum interval unless reward decays.",
        "When one action yields intermittent positive reward, maintain local exploitation window before replanning.",
    ],
    "displace": [
        "Track target-color cell deltas; positive deltas are the strongest local displacement signal.",
        "High novelty without target-color progress should be down-weighted as decorative change.",
        "After successful displacement action, repeat action family before broad exploration.",
        "Use neighborhood probes around recently changed regions to accelerate removal chains.",
        "Plateau logic should key on progress_reward, not raw frame-delta frequency.",
    ],
    "unknown": [
        "At bootstrap, infer archetype from action-effect trends and update once confidence exceeds threshold.",
        "Structured facet queries outperform free-text queries for cross-puzzle retrieval.",
        "Treat missing lesson_id/plan_id as degraded memory writes, not successful persistence.",
        "When memory backend is degraded, throttle recalls and emit periodic health probes.",
        "Persist compact run-summary and top action-outcome lessons at puzzle boundary for transfer.",
    ],
}


class DurableARCRunner:
    """Crash-safe scorecard driver with SideQuests orchestrator."""

    def __init__(
        self,
        harness: ARC3Harness,
        brain_client: BrainClientProtocol,
        config: dict,
        progress_callback: Callable[[dict], None] | None = None,
    ):
        self.harness = harness
        self._raw_brain = brain_client
        self.config = config
        self._ledger: List[dict] = []
        self._current_step = 0
        self._progress_callback = progress_callback
        self._last_replan_step: int = -999
        self._replan_backoff_steps: int = 3
        self._last_replan_signature: dict[str, Any] | None = None
        self._current_trace_snapshot: List[dict] = []
        self.observability = build_observability(config if isinstance(config, dict) else {})
        
        self.brain = LedgerBrainClient(
            inner=brain_client,
            ledger=self._ledger,
            step_provider=lambda: self._current_step,
            cost_tracker=None,
            observability=self.observability,
        )

        # B181: Outcome Judge initialization
        judge_cfg = config.get("judge")
        if judge_cfg:
            judge_llm = create_llm_client({"llm": judge_cfg})
            self.outcome_judge = OutcomeJudge(judge_llm) if judge_llm else None
        else:
            self.outcome_judge = None

        self.trajectory_evaluator = TrajectoryEvaluator()

    def _atexit_flush_trace(self) -> None:
        """A022: write any in-flight execution trace to disk before interpreter exit.

        Fires on both normal termination and unhandled exceptions. Idempotent —
        the normal run() export also rewrites the file, so a subsequent normal
        completion overwrites whatever we dumped here.
        """
        try:
            snapshot = getattr(self, "_current_trace_snapshot", None) or []
            if not snapshot:
                return
            # Import the atomic dumper lazily to avoid import cycles at module
            # import time between agents and the top-level runner entrypoint.
            from run_single_puzzle import _atomic_dump_json

            _atomic_dump_json(getattr(self, "agent_execution_trace_path"), list(snapshot))
            logger.info(
                "A022 atexit: flushed %d trace events to %s",
                len(snapshot),
                getattr(self, "agent_execution_trace_path"),
            )
        except Exception:
            # atexit handlers must never raise
            pass

    async def run(self, tasks: List[ABTask], card_id: str) -> List[dict]:
        run_span = self.observability.span(
            canonical_span_name("run"), 
            {
                "openinference.span.kind": "AGENT",
                "card_id": card_id, 
                "task_count": len(tasks)
            }
        )
        run_span.__enter__()
        # Ensure we flush any in-flight trace on interpreter exit.
        atexit.register(self._atexit_flush_trace)
        
        try:
            mgr = CheckpointManager(card_id)
            checkpoint = mgr.load_or_create(tasks)
            results: List[dict] = []

            # B189: Puzzle Scheduler
            graph_id = None
            try:
                # B190: Register task graph
                try:
                    tasks_meta = [
                        {"task_id": t.task_id, "label": f"ARC puzzle {t.task_id}"}
                        for t in tasks
                    ]
                    reg = await self._raw_brain.register_task_graph(
                        label=f"ARC batch {card_id}",
                        session_id=card_id,
                        owner=(self.config.get("owner") if isinstance(self.config, dict) else "arc-runner"),
                        tasks=tasks_meta,
                    )
                    graph_id = reg.get("graph_id") if isinstance(reg, Mapping) else None
                except Exception:
                    logger.exception("B190: Failed to register task graph for batch %s", card_id)

                concurrency = int(self.config.get("concurrency", 1))
                skip_solved = True
                if isinstance(self.config, dict):
                    skip_solved = bool(self.config.get("skip_solved", True))
                
                scheduler = PuzzleScheduler(concurrency=concurrency, skip_solved=skip_solved, brain_client=self._raw_brain)
                ordered_tasks = await scheduler.prepare(tasks)
            except Exception:
                logger.exception("B189: Failed to prepare puzzle scheduling, falling back to original order")
                ordered_tasks = list(tasks)

            async def _run_single_task(task: ABTask) -> Optional[dict]:
                task_span = self.observability.span(
                    canonical_span_name("task"),
                    {
                        "openinference.span.kind": "AGENT",
                        "task_id": task.task_id, 
                        "game_id": getattr(task, "game_id", "unknown")
                    }
                )
                
                with task_span:
                    orchestrator = None
                    h_list = []
                    t_list = []
                    fail_brain = self.brain
                    tc = checkpoint.tasks.get(task.task_id)
                    if tc and tc.status == "complete":
                        if not self._has_terminal_payload(tc.result):
                            logger.info("Checkpoint for %s is stale. Re-running.", task.task_id)
                            tc.status = "pending"
                            tc.result = None
                            mgr.save(checkpoint)
                        else:
                            return self._submission_row_from_result(tc.result or {})

                    session_id = f"arc-{task.task_id}-{uuid.uuid4().hex[:8]}"
                    self.brain.current_phase = "bootstrap"
                    self._current_step = 0
                    puzzle_start_time = time.time()

                    # B180: Token cost tracking and budget enforcement
                    from agents.arc3.cost_tracker import CostTracker
                    cost_cfg = {}
                    llm_cfg = {}
                    if type(self.config) is dict:
                        cost_cfg = self.config.get("cost", {})
                        llm_cfg = self.config.get("llm", {})
                    
                    model_name = llm_cfg.get("model", "unknown") if isinstance(llm_cfg, dict) else "unknown"
                    pricing = {}
                    if isinstance(cost_cfg, dict):
                        pricing = cost_cfg.get("pricing_per_million_tokens", {}).get(model_name, {"input": 0.0, "output": 0.0})
                    
                    budget = float('inf')
                    if isinstance(cost_cfg, dict):
                        val = cost_cfg.get("budget_per_puzzle_usd")
                        if val is not None:
                            try:
                                budget = float(val)
                            except (TypeError, ValueError):
                                budget = float('inf')

                    cost_tracker = CostTracker(
                        model_name=str(model_name),
                        input_price_per_m=float(pricing.get("input", 0.0) if isinstance(pricing, dict) else 0.0),
                        output_price_per_m=float(pricing.get("output", 0.0) if isinstance(pricing, dict) else 0.0),
                        budget_usd=budget
                    )

                    self.brain = LedgerBrainClient(
                        inner=self._raw_brain,
                        ledger=self._ledger,
                        step_provider=lambda: self._current_step,
                        start_time=puzzle_start_time,
                        cost_tracker=cost_tracker,
                        observability=self.observability,
                    )

                    # B190: Store per-puzzle sidequest
                    branch_result = await self.brain.branch_quest(
                        name=f"ARC puzzle {task.task_id}",
                        purpose=f"Solve ARC-AGI-3 task {task.task_id}",
                        parent_quest_id=card_id,
                    )
                    # Some tests patch `LedgerBrainClient.branch_quest`, which bypasses
                    # its internal ledger recording. Backfill the bootstrap event here.
                    if not any((entry.get("call_type") == "branch_quest") for entry in self._ledger if isinstance(entry, dict)):
                        self._ledger.append({
                            "step": self._current_step,
                            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "elapsed_mmss": "00:00",
                            "phase": "bootstrap",
                            "call_type": "branch_quest",
                            "mode": "write",
                            "input_summary": f"ARC puzzle {task.task_id}",
                            "result_summary": f"side_quest_id={(branch_result or {}).get('side_quest_id') if isinstance(branch_result, Mapping) else None}",
                            "latency_ms": 0.0,
                        })

                    # Create the per-puzzle orchestrator for the default single-run flow,
                    # but if strategy racing is enabled we will launch several variants
                    # via StrategyRacer instead of running a single orchestrator here.
                    if isinstance(self.config, dict) and self.config.get("strategy_racing", False):
                        async def _variant_runner(variant_brain, session_id_v, task_arg, vcfg):
                            # Build per-variant cost tracker and orchestrator, then run it
                            from agents.arc3.cost_tracker import CostTracker

                            llm_cfg_v = vcfg.get("llm", {}) if isinstance(vcfg, dict) else {}
                            model_name_v = llm_cfg_v.get("model", "unknown") if isinstance(llm_cfg_v, dict) else "unknown"
                            pricing_v = {}
                            if isinstance(vcfg.get("cost", {}), dict):
                                pricing_v = vcfg.get("cost", {}).get("pricing_per_million_tokens", {}).get(model_name_v, {"input": 0.0, "output": 0.0})

                            budget_v = float('inf')
                            try:
                                val = (vcfg.get("cost") or {}).get("budget_per_puzzle_usd")
                                if val is not None:
                                    budget_v = float(val)
                            except Exception:
                                budget_v = float('inf')

                            cost_tracker_v = CostTracker(
                                model_name=str(model_name_v),
                                input_price_per_m=float(pricing_v.get("input", 0.0) if isinstance(pricing_v, dict) else 0.0),
                                output_price_per_m=float(pricing_v.get("output", 0.0) if isinstance(pricing_v, dict) else 0.0),
                                budget_usd=budget_v,
                            )

                            # B197: Attempt to load proven procedures for this variant before orchestrator creation
                            procedures = []
                            try:
                                archetype_hint = (getattr(task_arg, 'game_id', None) or 'unknown')
                                proc_resp = await variant_brain.recall_procedures(archetype=archetype_hint, limit=3)
                                if isinstance(proc_resp, Mapping):
                                    procedures = proc_resp.get('procedures') or []
                            except Exception:
                                logger.debug("recall_procedures lookup failed for variant")

                            # B199: Check knowledge gaps to influence exploration budget
                            multiplier = 1.0
                            try:
                                gaps_resp = await variant_brain.get_knowledge_gaps(domain=archetype_hint)
                                if isinstance(gaps_resp, Mapping):
                                    gaps = gaps_resp.get('gaps') or []
                                    # If there are missing-lessons gaps for this archetype, increase exploration
                                    has_gap = any((g.get('gap_type') == 'missing_lessons') for g in gaps)
                                    if has_gap:
                                        multiplier = 2.0
                            except Exception:
                                logger.debug("get_knowledge_gaps lookup failed for variant")

                            vcfg2 = dict(vcfg) if isinstance(vcfg, dict) else {}
                            vcfg2["loaded_procedures"] = procedures
                            vcfg2["exploration_budget_multiplier"] = multiplier

                            orchestrator_v = ARCOrchestrator(
                                brain_client=variant_brain,
                                llm_client=self.harness.llm_client,
                                session_id=session_id_v,
                                serializer=self.harness.serializer,
                                config=vcfg2,
                                cost_tracker=cost_tracker_v,
                                task_id=task.task_id,
                            )                            # A022: expose the orchestrator's in-flight trace so atexit can flush it.
                            self._current_trace_snapshot = getattr(orchestrator_v, "_execution_trace", [])
                            return await self._run_puzzle_with_brain(orchestrator_v, task_arg, variant_brain, vcfg, checkpoint, mgr)

                        winner = await strategy_race(self, task, variants=self.config.get("strategy_racing_variants", ["A", "B", "C"]), variant_runner=_variant_runner)
                        task_result = winner.get("task_result")
                        duration = winner.get("duration")
                        orchestrator = winner.get("orchestrator")
                        # Merge winning ledger into the driver's ledger so subsequent code can use it
                        try:
                            winner_ledger = winner.get("ledger") or []
                            self._ledger.extend(list(winner_ledger))
                        except Exception:
                            logger.exception("Failed merging winner ledger")

                        result_payload = asdict(task_result)
                        # A-038: Ensure authoritative executed step count is preserved
                        # Prefer the ABTaskResult.steps, fall back to orchestrator current step.
                        result_payload["steps"] = int(getattr(task_result, "steps", (getattr(orchestrator, "_current_step", 0) if orchestrator is not None else 0)) or 0)
                        result_payload["solve_phase_summary"] = self._build_phase_summary(orchestrator)
                        result_payload["game_id"] = getattr(task, "game_id", "unknown")
                        result_payload["game_title"] = getattr(task, "arc_game_title", None)
                        result_payload["game_tags"] = list(getattr(task, "arc_game_tags", []) or [])
                        result_payload["runtime_seconds"] = round(duration, 2)
                        result_payload["benchmark_metrics"] = getattr(task_result, "benchmark_metrics", {})
                        result_payload["entity_gate_status"] = getattr(orchestrator, "_entity_gate_result", {}) or {"status": "pass"}
                        result_payload["bootstrap_write_trace"] = getattr(task_result, "bootstrap_write_trace", [])
                        result_payload["final_write_trace"] = getattr(task_result, "final_write_trace", [])
                        result_payload["debug_steps"] = list(getattr(orchestrator, "_step_history", []))
                        result_payload["sidequests_ledger"] = list(self._ledger)
                        result_payload["arc_event_timeline"] = list(getattr(self.brain, "arc_event_timeline", []))
                        result_payload["agent_execution_trace"] = getattr(orchestrator, "_execution_trace", [])
                        result_payload["world_model_snapshot"] = (
                            orchestrator.world_model.to_trace_snapshot()
                            if orchestrator is not None and hasattr(orchestrator, "world_model")
                            else {}
                        )

                        self._ledger.clear()

                        traj = self._build_trajectory_summary(orchestrator)
                        try:
                            await self._report_puzzle_outcome(orchestrator=orchestrator, task=task, task_result=task_result, session_id=session_id)
                            if graph_id:
                                await self.brain.advance_task(graph_id=graph_id, task_id=task.task_id, status="complete", result=task_result.final_state)
                        except Exception:
                            logger.exception("B190: best-effort lesson/advance failed")

                        mgr.mark_complete(checkpoint, task.task_id, getattr(orchestrator, "_plan_id", None), result_payload)
                        
                        if hasattr(task_span, "set_attribute"):
                            task_span.set_attribute("correct", task_result.correct)
                            task_span.set_attribute("steps", task_result.steps)
                        
                        return self._submission_row_from_result(result_payload)
                    else:
                        # B197: Pre-solve procedure lookup
                        procedures = []
                        try:
                            archetype_hint = getattr(task, 'game_id', None) or 'unknown'
                            proc_resp = await self.brain.recall_procedures(archetype=archetype_hint, limit=3)
                            if isinstance(proc_resp, Mapping):
                                procedures = proc_resp.get('procedures') or []
                        except Exception:
                            logger.debug("recall_procedures lookup failed")

                        # B199: Knowledge gap check to influence exploration budget
                        multiplier = 1.0
                        try:
                            gaps_resp = await self.brain.get_knowledge_gaps(domain=archetype_hint)
                            if isinstance(gaps_resp, Mapping):
                                gaps = gaps_resp.get('gaps') or []
                                has_gap = any((g.get('gap_type') == 'missing_lessons') for g in gaps)
                                if has_gap:
                                    multiplier = 2.0
                        except Exception:
                            logger.debug("get_knowledge_gaps lookup failed")

                        cfg2 = dict(self.config) if isinstance(self.config, dict) else {}
                        cfg2["loaded_procedures"] = procedures
                        cfg2["exploration_budget_multiplier"] = multiplier

                        orchestrator = ARCOrchestrator(
                            brain_client=self.brain,
                            llm_client=self.harness.llm_client,
                            session_id=session_id,
                            serializer=self.harness.serializer,
                            config=cfg2,
                            cost_tracker=cost_tracker,
                            task_id=task.task_id,
                        )                        # A022: expose the orchestrator's in-flight trace so atexit can flush it.
                        self._current_trace_snapshot = getattr(orchestrator, "_execution_trace", [])

                        try:
                            task_result, duration = await self._run_puzzle(orchestrator, task, checkpoint, mgr)
                            result_payload = asdict(task_result)
                            # A-038: Ensure authoritative executed step count is preserved
                            # Prefer the ABTaskResult.steps, fall back to orchestrator current step.
                            result_payload["steps"] = int(getattr(task_result, "steps", (getattr(orchestrator, "_current_step", 0) if orchestrator is not None else 0)) or 0)
                            result_payload["solve_phase_summary"] = self._build_phase_summary(orchestrator)
                            result_payload["game_id"] = getattr(task, "game_id", "unknown")
                            result_payload["game_title"] = getattr(task, "arc_game_title", None)
                            result_payload["game_tags"] = list(getattr(task, "arc_game_tags", []) or [])
                            result_payload["runtime_seconds"] = round(duration, 2)
                            result_payload["benchmark_metrics"] = getattr(task_result, "benchmark_metrics", {})
                            result_payload["entity_gate_status"] = getattr(orchestrator, "_entity_gate_result", {}) or {"status": "pass"}
                            result_payload["bootstrap_write_trace"] = getattr(task_result, "bootstrap_write_trace", [])
                            result_payload["final_write_trace"] = getattr(task_result, "final_write_trace", [])
                            result_payload["debug_steps"] = list(getattr(orchestrator, "_step_history", []))
                            result_payload["sidequests_ledger"] = list(self._ledger)
                            result_payload["arc_event_timeline"] = list(getattr(self.brain, "arc_event_timeline", []))
                            result_payload["agent_execution_trace"] = getattr(orchestrator, "_execution_trace", [])
                            result_payload["world_model_snapshot"] = (
                                orchestrator.world_model.to_trace_snapshot()
                                if orchestrator is not None and hasattr(orchestrator, "world_model")
                                else {}
                            )
                            
                            self._ledger.clear()

                            traj = self._build_trajectory_summary(orchestrator)
                            try:
                                await self._report_puzzle_outcome(orchestrator=orchestrator, task=task, task_result=task_result, session_id=session_id)
                                if graph_id:
                                    await self.brain.advance_task(graph_id=graph_id, task_id=task.task_id, status="complete", result=task_result.final_state)
                            except Exception:
                                logger.exception("B190: best-effort lesson/advance failed")

                            mgr.mark_complete(checkpoint, task.task_id, orchestrator._plan_id, result_payload)
                            
                            if hasattr(task_span, "set_attribute"):
                                task_span.set_attribute("correct", task_result.correct)
                                task_span.set_attribute("steps", task_result.steps)

                            return result_payload
                        except Exception as exc:
                            # A014: Ensure we have real lists, not mocks/coroutines
                            if orchestrator is not None:
                                h_attr = getattr(orchestrator, "_step_history", [])
                                if isinstance(h_attr, list):
                                    h_list = h_attr
                                t_attr = getattr(orchestrator, "_execution_trace", [])
                                if isinstance(t_attr, list):
                                    t_list = t_attr

                            final_s = "unknown"
                            if h_list:
                                last_step = h_list[-1]
                                if isinstance(last_step, dict):
                                    final_s = last_step.get("state_after", "unknown")

                            failure_class = classify_failure(
                                exc=exc,
                                final_state=final_s if final_s != "unknown" else None,
                                error_message=str(exc),
                                no_progress_steps=int(getattr(orchestrator, "_consecutive_no_progress_steps", 0) if orchestrator is not None and hasattr(orchestrator, "_consecutive_no_progress_steps") else 0),
                                budget_exhausted=bool(
                                    getattr(orchestrator.cost_tracker, "budget_exhausted", False)
                                    if orchestrator is not None and getattr(orchestrator, "cost_tracker", None)
                                    else False
                                ),
                                wall_clock_timeout=("wall-clock" in str(exc).lower()),
                                loop_detected=self._effective_loop_detected(orchestrator) if orchestrator is not None else False,
                                graduation_reason=str(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "graduation_reason", "")),
                                coverage_saturated=bool(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "coverage_saturated", False)),
                                plateau_escalation_required=bool(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "plateau_escalation_required", False)),
                            )
                            mgr.mark_failed(checkpoint, task.task_id, str(exc), failure_class.value)
                            logger.error("Task %s failed [%s]: %s", task.task_id, failure_class.value, exc)
                            
                            result_payload = {
                                "task_id": task.task_id,
                                "game_id": getattr(task, "game_id", "unknown"),
                                "game_title": getattr(task, "arc_game_title", None),
                                "game_tags": list(getattr(task, "arc_game_tags", []) or []),
                                "correct": False,
                                "steps": int(getattr(orchestrator, "_current_step", 0) if orchestrator is not None and hasattr(orchestrator, "_current_step") else 0),
                                "runtime_seconds": round(time.time() - (puzzle_start_time if 'puzzle_start_time' in locals() else time.time()), 2),
                                "failure_class": failure_class.value,
                                "final_state": final_s if final_s != "unknown" else "error",
                                "final_observation": None,
                                "final_reward": None,
                                "error_message": str(exc),
                                "debug_steps": list(h_list),
                                "sidequests_ledger": list(self._ledger),
                                "arc_event_timeline": list(getattr(fail_brain, "arc_event_timeline", [])),
                                "agent_execution_trace": list(t_list),
                                "world_model_snapshot": (
                                    orchestrator.world_model.to_trace_snapshot()
                                    if orchestrator is not None and hasattr(orchestrator, "world_model")
                                    else {}
                                ),
                            }
                            self._ledger.clear()
                            return result_payload

            batch_results = await scheduler.run_batch(ordered_tasks, _run_single_task)
            results = [self._submission_row_from_result(r) for r in batch_results if r is not None]
            # Clear snapshot on normal completion so atexit handler is a no-op.
            self._current_trace_snapshot = []
            return self._attach_batch_eval_summary(results)
        finally:
            run_span.__exit__(None, None, None)

    def _has_terminal_payload(self, result: dict | None) -> bool:
        if not isinstance(result, dict):
            return False

        grid = (result.get("final_observation") or {}).get("grid")
        has_grid = isinstance(grid, list) and len(grid) > 0
        if not has_grid:
            return False

        if isinstance(self.config, dict) and self.config.get("require_submission_artifacts"):
            has_artifacts = bool(
                result.get("sidequests_ledger")
                or result.get("debug_steps")
                or result.get("arc_event_timeline")
                or result.get("agent_execution_trace")
            )
            if not has_artifacts:
                return False

        return True

    async def _run_puzzle(self, orchestrator: ARCOrchestrator, task: ABTask, checkpoint=None, mgr=None) -> tuple[ABTaskResult, float]:
        # Backwards-compatible: callers may omit checkpoint and mgr (tests use the
        # older two-arg form). If omitted, create an ephemeral CheckpointManager
        # and checkpoint for this invocation so existing call sites keep working.
        if mgr is None or checkpoint is None:
            try:
                local_card = f"local-{getattr(task, 'task_id', 'anon')}-{uuid.uuid4().hex[:8]}"
                mgr = mgr or CheckpointManager(local_card)
                checkpoint = checkpoint or mgr.load_or_create([task])
            except Exception:
                mgr = mgr or CheckpointManager(f"local-{uuid.uuid4().hex[:8]}")
                checkpoint = checkpoint or mgr.load_or_create([task])

        max_steps = self.harness.config.parameters.get("max_attempts_per_puzzle", 10)
        max_retries = self.config.get("max_retries_per_puzzle", 3)
        # A039: overall wall-clock budget for the whole puzzle run.
        wall_clock_budget = float(getattr(self.harness.config, "timeout", 3600))
        # Phase step budgets (force-advance if gate blocks too long)
        MODEL_BUDGET = 4
        HYPOTHESIS_BUDGET = 6
        if getattr(orchestrator, "_supervisor", None) is not None:
            try:
                orchestrator._supervisor.abandon_zero_reward_steps = min(
                    int(getattr(orchestrator._supervisor, "abandon_zero_reward_steps", 30)),
                    max(5, int(max_steps) - 2),
                )
            except Exception:
                logger.debug("Unable to align supervisor threshold with max steps", exc_info=True)
        adapter = ARC3Adapter(
            brain_client=self.brain,
            session_id=orchestrator.session_id,
            task_id=task.task_id,
        )
        game_id = getattr(task, "game_id", "unknown")

        start_time = time.time()
        total_steps = 0
        success = False
        done = False
        error_msg: str | None = None
        total_tokens_in = 0
        total_tokens_out = 0
        last_grid = None
        last_levels_completed: int | None = None
        last_score: float | None = None
        last_reward = 0.0
        consecutive_no_progress_steps = 0
        bootstrap_write_trace: list[dict] = []
        final_write_trace: list[dict] = []
        graph_health = await self._probe_graph_health(orchestrator=orchestrator, task=task)
        await self._seed_bootstrap_lessons_if_empty(task=task, graph_health=graph_health)

        for attempt in range(1, max_retries + 1):
            frame_response, guid = await self._initial_frame(game_id)
            observation = adapter.normalize_observation(frame_response)
            last_grid = observation.get("grid")
            last_levels_completed = self._safe_int(observation.get("levels_completed"))
            last_score = self._safe_float(frame_response.get("score"))

            training_examples = observation.get("training_examples") or []
            if training_examples:
                try:
                    phase1_result = await orchestrator.run_phase1(observation, training_examples)
                    if phase1_result and phase1_result.get("verified"):
                        orchestrator._verified_output_grid = phase1_result["output_grid"]
                        orchestrator._phase2_mode = "execution"
                except Exception:
                    logger.exception("B156: Phase 1 failed")

            # Initialize or restore PhaseController for this attempt
            tc = None
            try:
                tc = (checkpoint.tasks.get(task.task_id) if checkpoint and getattr(checkpoint, 'tasks', None) else None)
            except Exception:
                tc = None

            phase_ctrl = None
            try:
                if tc and getattr(tc, "phase_state", None):
                    phase_ctrl = PhaseController.from_checkpoint(tc.phase_state)
                else:
                    phase_ctrl = PhaseController()
            except Exception:
                logger.exception("Failed to restore PhaseController from checkpoint; creating fresh controller")
                phase_ctrl = PhaseController()

            # Register cheap gates from solve engine where possible
            try:
                engine = getattr(orchestrator, "solve_engine", None)
                if engine:
                    phase_ctrl.register_gate(SolvePhase.MODEL, SolvePhase.HYPOTHESIZE, engine.is_exploration_complete)
                    phase_ctrl.register_gate(SolvePhase.HYPOTHESIZE, SolvePhase.ROUTE, engine.has_hypothesis)
                    phase_ctrl.register_gate(SolvePhase.ROUTE, SolvePhase.EXECUTE, engine.has_active_chunk)
            except Exception:
                logger.exception("Failed registering phase gates")

            # Expose phase controller to orchestrator for read-only use
            try:
                orchestrator._phase_controller = phase_ctrl
            except Exception:
                pass

            # Backward-compat: set brain phase string to controller's name
            try:
                self.brain.current_phase = phase_ctrl.phase_name
                if hasattr(orchestrator, "set_write_trace_context"):
                    orchestrator.set_write_trace_context(phase_ctrl.phase_name)
            except Exception:
                logger.exception("Failed to set initial brain phase shim")

            # Persist initial phase state
            try:
                if tc is not None:
                    tc.phase_state = phase_ctrl.to_checkpoint()
                    mgr.save(checkpoint)
            except Exception:
                logger.exception("Failed to persist initial phase state to checkpoint")

            memory_context = await orchestrator.perceive(observation, step=0)
            if isinstance(memory_context, dict):
                memory_context.setdefault("graph_health", dict(graph_health))
                memory_context.setdefault("memory_degraded", getattr(self.brain, "memory_degraded", False) is True)
                if getattr(self.brain, "memory_degraded_reason", None):
                    memory_context.setdefault("memory_degraded_reason", str(getattr(self.brain, "memory_degraded_reason")))
            # B212: inject graph_evidence from orchestrator hypothesis context into memory_context
            try:
                if isinstance(memory_context, dict) and getattr(orchestrator, "_hypothesis_context", None):
                    ge = (orchestrator._hypothesis_context or {}).get("graph_evidence")
                    if ge:
                        memory_context = dict(memory_context)
                        memory_context["graph_evidence"] = ge
            except Exception:
                logger.debug("Failed injecting graph_evidence into memory_context", exc_info=True)

            await orchestrator.plan(observation, memory_context)
            if hasattr(orchestrator, "consume_write_trace"):
                bootstrap_write_trace = list(orchestrator.consume_write_trace())

            # Advance PERCEIVE -> MODEL (bootstrap split)
            try:
                if phase_ctrl.phase == SolvePhase.PERCEIVE:
                    previous_phase = phase_ctrl.phase_name
                    try:
                        if phase_ctrl.can_advance(SolvePhase.MODEL):
                            phase_ctrl.advance(SolvePhase.MODEL)
                        else:
                            phase_ctrl.advance(SolvePhase.MODEL, force=True)
                    except IllegalPhaseTransition:
                        phase_ctrl.advance(SolvePhase.MODEL, force=True)
                    # sync shim + save
                    self.brain.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                        metadata={"reason": "bootstrap_split"},
                    )
            except Exception:
                logger.exception("Failed advancing to MODEL phase")

            state = observation.get("state", "NOT_FINISHED")
            steps_this_attempt = 0

            while steps_this_attempt < max_steps:
                # A039: Check wall-clock budget
                elapsed = time.time() - start_time
                if elapsed > wall_clock_budget:
                    error_msg = f"Wall-clock budget exhausted ({elapsed:.1f}s > {wall_clock_budget}s)"
                    done = True
                    break

                budget_exhausted = bool(
                    getattr(orchestrator.cost_tracker, "budget_exhausted", False) is True
                ) if getattr(orchestrator, "cost_tracker", None) else False
                if orchestrator.cost_tracker and budget_exhausted:
                    error_msg = "Budget exhausted"
                    done = True
                    break

                if getattr(orchestrator, "_should_abandon", False):
                    error_msg = getattr(
                        orchestrator,
                        "_world_model_failure_reason",
                        "Supervisor abandoned",
                    )
                    done = True
                    break

                # Advance to HYPOTHESIZE
                previous_phase = phase_ctrl.phase_name
                try:
                    if phase_ctrl.phase != SolvePhase.HYPOTHESIZE:
                        if phase_ctrl.can_advance(SolvePhase.HYPOTHESIZE):
                            phase_ctrl.advance(SolvePhase.HYPOTHESIZE)
                        elif total_steps >= MODEL_BUDGET:
                            phase_ctrl.advance(SolvePhase.HYPOTHESIZE, force=True)
                except IllegalPhaseTransition:
                    logger.debug("HYPOTHESIZE gate blocked; continuing without advance")
                except Exception:
                    logger.exception("Error advancing to HYPOTHESIZE")

                self._current_step = total_steps + 1
                # sync shim + save
                try:
                    self.brain.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                    )
                except Exception:
                    logger.exception("Failed to sync brain phase during hypothesize")

                prior_step = orchestrator._step_history[-1] if getattr(orchestrator, "_step_history", None) else None
                hyp_ctx = await orchestrator.hypothesize(
                    observation,
                    prior_step.get("action_id") if prior_step else None,
                    total_steps,
                    transition_meta=prior_step,
                )

                # Advance to ROUTE (was: 'solve')
                previous_phase = phase_ctrl.phase_name
                try:
                    if phase_ctrl.phase != SolvePhase.ROUTE:
                        if phase_ctrl.can_advance(SolvePhase.ROUTE):
                            phase_ctrl.advance(SolvePhase.ROUTE)
                        elif total_steps >= HYPOTHESIS_BUDGET:
                            phase_ctrl.advance(SolvePhase.ROUTE, force=True)
                except IllegalPhaseTransition:
                    logger.debug("ROUTE gate blocked; continuing without advance")
                except Exception:
                    logger.exception("Error advancing to ROUTE")

                try:
                    self.brain.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                    )
                except Exception:
                    logger.exception("Failed to sync brain phase during solve/route")

                # A061: Single-Action Macro Executor
                # Check for deterministic progress in one-action environments
                macro_eligibility = orchestrator.check_macro_eligibility(observation)
                if (
                    isinstance(macro_eligibility, tuple)
                    and len(macro_eligibility) == 2
                ):
                    is_eligible, macro_action_id = macro_eligibility
                else:
                    is_eligible, macro_action_id = False, None
                macro_reason = getattr(orchestrator, "_macro_eligibility_reason", None) or "single_action_deterministic_progress"
                if is_eligible:
                    orchestrator.enter_macro_mode(macro_action_id, reason=macro_reason)
                    
                    macro_cfg = self.config.get("macro_executor", {})
                    max_macro_steps = macro_cfg.get("max_macro_steps", 25)
                    self._macro_hashes = {observation.get("frame_hash")} if observation.get("frame_hash") else set()
                    
                    while orchestrator._macro_active and orchestrator._macro_step_count < max_macro_steps:
                        orchestrator._macro_step_count += 1
                        
                        # Prepare macro action
                        macro_action = {
                            "action_id": macro_action_id,
                            "rationale": f"macro execution [{orchestrator._macro_id}] step {orchestrator._macro_step_count}",
                            "decision_source": "macro_executor",
                            "macro_id": orchestrator._macro_id,
                            "macro_step_index": orchestrator._macro_step_count,
                            "macro_reason": macro_reason,
                            "x": 0,
                            "y": 0,
                        }
                        
                        # Execute action directly bypassing heavy reasoning
                        frame_response, env_reward, done, guid = await self._execute_action(game_id, guid, macro_action, total_steps)
                        env_signals = self._extract_env_signals(frame_response)
                        observation = adapter.normalize_observation(frame_response)
                        
                        target_color_id = self._resolve_target_color_id(orchestrator)
                        # A066: pass terminal value score if available
                        prev_tvs = float(getattr(orchestrator._solve_context, "terminal_value_score", 0.0) if orchestrator._solve_context else 0.0)
                        reward, reward_components = self._compute_progress_reward(
                            env_reward=env_reward,
                            prev_grid=last_grid,
                            next_grid=observation.get("grid"),
                            prev_levels_completed=last_levels_completed,
                            next_levels_completed=self._safe_int(observation.get("levels_completed")),
                            prev_score=last_score,
                            next_score=self._safe_float(frame_response.get("score")),
                            target_color_id=target_color_id,
                            prev_terminal_value_score=prev_tvs,
                        )
                        
                        # Stop conditions (A067)
                        stop_reason = None
                        state = observation.get("state", "NOT_FINISHED")
                        if state in ("WIN", "GAME_OVER") or done:
                            stop_reason = f"terminal_state_{state}"
                        elif env_reward != 0.0:
                            stop_reason = "env_reward_detected"
                        elif macro_action_id not in (observation.get("available_actions") or []):
                            stop_reason = "available_actions_changed"
                        elif not reward_components.get("meaningful_progress", False):
                            stop_reason = "meaningful_progress_stalled"
                        
                        curr_hash = observation.get("frame_hash")
                        if curr_hash and curr_hash in getattr(self, "_macro_hashes", set()):
                            stop_reason = "repeated_frame_hash"
                        if curr_hash:
                            if not hasattr(self, "_macro_hashes"): self._macro_hashes = set()
                            self._macro_hashes.add(curr_hash)

                        # Update runner state
                        last_grid = observation.get("grid")
                        last_levels_completed = self._safe_int(observation.get("levels_completed"))
                        last_score = self._safe_float(frame_response.get("score"))
                        
                        # A066: Use meaningful progress gate
                        meaningful = reward_components.get("meaningful_progress", False)
                        if meaningful:
                            consecutive_no_progress_steps = 0
                            last_reward = reward
                            orchestrator._macro_terminal_stall_count = 0
                        else:
                            consecutive_no_progress_steps += 1
                            if not hasattr(orchestrator, "_macro_terminal_stall_count"):
                                orchestrator._macro_terminal_stall_count = 0
                            orchestrator._macro_terminal_stall_count += 1
                        
                        if orchestrator._macro_terminal_stall_count >= 3:
                            stop_reason = "terminal_stall"
                        
                        self._append_macro_step_record(
                            orchestrator=orchestrator,
                            observation=observation,
                            action=macro_action,
                            total_steps=total_steps + 1,
                            available_actions=observation.get("available_actions", []),
                        )
                        orchestrator.record_step_result(reward, done, next_observation=observation)
                        if getattr(orchestrator, "_step_history", None):
                            frame_delta = orchestrator._step_history[-1].get("frame_delta", {}) or {}
                            n_changed = int(
                                frame_delta.get("n_cells_changed")
                                or reward_components.get("pixels_changed")
                                or reward_components.get("n_cells_changed")
                                or 0
                            )
                            if n_changed == 0:
                                stop_reason = "zero_delta"

                            # A063: Check for object progress stagnation
                            last_op = orchestrator._step_history[-1].get("object_progress", {})
                            op_score = float(last_op.get("score", 0.0) or 0.0)
                            if op_score <= 0.0 and n_changed <= 0:
                                stop_reason = "object_stagnation"
                            effect_class = (orchestrator._step_history[-1].get("compiled_world_delta") or {}).get("effect_class")
                            if effect_class == "harmful":
                                stop_reason = "harmful_effect"
                            elif self._macro_prediction_falsified(
                                orchestrator._step_history[-1],
                                getattr(getattr(orchestrator, "_last_planner_selection", None), "selected", None),
                            ):
                                stop_reason = "prediction_falsified"

                            orchestrator._step_history[-1].update({
                                "state_after": state,
                                "reward": reward,
                                "env_reward": env_reward,
                                "progress_reward": reward,
                                "reward_components": reward_components,
                                "env_signals": env_signals,
                                "done": done,
                                "decision_source": "macro_executor",
                                "macro_id": orchestrator._macro_id,
                                "macro_step_index": orchestrator._macro_step_count,
                                "macro_reason": macro_reason,
                                "args_effective": orchestrator.get_args_effective(macro_action_id),
                                "coordinate_relevance": dict(getattr(orchestrator, "_action_coord_relevance", {}).get(macro_action_id, {})),
                            })
                            
                        total_steps += 1
                        steps_this_attempt += 1
                        
                        # Progress snapshots
                        self._emit_progress_snapshot(
                            task=task,
                            orchestrator=orchestrator,
                            observation=observation,
                            total_steps=total_steps,
                            reward=reward,
                            done=done,
                            start_time=start_time,
                        )
                        
                        # Telemetry (non-blocking in sidequest client)
                        await orchestrator.perceive_step_response(observation, step=total_steps, reward=reward, done=done, action_id=macro_action_id)

                        if stop_reason:
                            if getattr(orchestrator, "_step_history", None):
                                orchestrator._step_history[-1]["macro_stop_reason"] = stop_reason
                                orchestrator._step_history[-1]["macro_terminal_stall_count"] = getattr(orchestrator, "_macro_terminal_stall_count", 0)
                            orchestrator.exit_macro_mode(stop_reason)
                            break
                        
                        if steps_this_attempt >= max_steps:
                            if getattr(orchestrator, "_step_history", None):
                                orchestrator._step_history[-1]["macro_stop_reason"] = "max_steps_per_attempt_reached"
                            orchestrator.exit_macro_mode("max_steps_per_attempt_reached")
                            break
                    
                    # Exit conditions check after macro loop
                    state = observation.get("state", "NOT_FINISHED")
                    if state == "WIN":
                        success = True
                        break
                    elif state == "GAME_OVER":
                        if attempt < max_retries and hasattr(orchestrator, "reset_for_retry"):
                            orchestrator.reset_for_retry(attempt)
                        break
                    elif done:
                        success = reward >= 1.0 or state == "WIN"
                        break
                    
                    # Continue main while loop (re-enters reasoning path for new state)
                    continue

                await orchestrator.solve(observation, hyp_ctx, total_steps)

                # A079: Honor EARLY_STOP signal from reasoning controller
                if getattr(orchestrator, "_force_replan", False) is True:
                    logger.warning(f"A079: early_stop triggered at step {total_steps}")
                    self._emit_world_model_decision_snapshot(
                        task=task,
                        orchestrator=orchestrator,
                        observation=observation,
                        executed_step_count=total_steps,
                        start_time=start_time,
                    )
                    # Force transition to REPLAN phase logic
                    if getattr(orchestrator, "_should_abandon", False):
                        error_msg = getattr(orchestrator, "_world_model_failure_reason", "world_model_strategy_exhausted")
                        break
                    if self._should_replan(orchestrator, consecutive_no_progress_steps):
                         # If it's already a replan trigger, let the natural flow handle it
                         pass
                    else:
                         # Force it
                         break

                # Advance to EXECUTE (was: 'act')
                previous_phase = phase_ctrl.phase_name
                try:
                    if phase_ctrl.phase != SolvePhase.EXECUTE:
                        if phase_ctrl.can_advance(SolvePhase.EXECUTE):
                            phase_ctrl.advance(SolvePhase.EXECUTE)
                        else:
                            # No direct budget for EXECUTE; allow normal advance
                            phase_ctrl.advance(SolvePhase.EXECUTE, force=True)
                except IllegalPhaseTransition:
                    logger.debug("EXECUTE gate blocked; forcing execute")
                except Exception:
                    logger.exception("Error advancing to EXECUTE")

                try:
                    self.brain.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                    )
                except Exception:
                    logger.exception("Failed to sync brain phase during act/execute")

                action = await orchestrator.act(observation, memory_context, total_steps + 1)

                total_tokens_in += self.harness.serializer._estimate_tokens(json.dumps(observation))
                total_tokens_out += self.harness.serializer._estimate_tokens(str(action))

                frame_response, env_reward, done, guid = await self._execute_action(game_id, guid, action, total_steps)
                env_signals = self._extract_env_signals(frame_response)
                observation = adapter.normalize_observation(frame_response)
                target_color_id = self._resolve_target_color_id(orchestrator)
                # A066: pass terminal value score if available
                prev_tvs = float(getattr(orchestrator._solve_context, "terminal_value_score", 0.0) if orchestrator._solve_context else 0.0)
                reward, reward_components = self._compute_progress_reward(
                    env_reward=env_reward,
                    prev_grid=last_grid,
                    next_grid=observation.get("grid"),
                    prev_levels_completed=last_levels_completed,
                    next_levels_completed=self._safe_int(observation.get("levels_completed")),
                    prev_score=last_score,
                    next_score=self._safe_float(frame_response.get("score")),
                    target_color_id=target_color_id,
                    prev_terminal_value_score=prev_tvs,
                )
                last_grid = observation.get("grid")
                last_levels_completed = self._safe_int(observation.get("levels_completed"))
                last_score = self._safe_float(frame_response.get("score"))

                recall_query = None
                if total_steps == 0 or consecutive_no_progress_steps >= 2:
                    recall_query = "What did I learn from similar puzzles?"

                # Advance to EVALUATE (was: 'ingest')
                previous_phase = phase_ctrl.phase_name
                try:
                    if phase_ctrl.phase != SolvePhase.EVALUATE:
                        if phase_ctrl.can_advance(SolvePhase.EVALUATE):
                            phase_ctrl.advance(SolvePhase.EVALUATE)
                        else:
                            phase_ctrl.advance(SolvePhase.EVALUATE, force=True)
                except IllegalPhaseTransition:
                    logger.debug("EVALUATE gate blocked; forcing evaluate")
                except Exception:
                    logger.exception("Error advancing to EVALUATE")

                try:
                    self.brain.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                    )
                except Exception:
                    logger.exception("Failed to sync brain phase during ingest/evaluate")

                await adapter.ingest_step(frame_response, action, reward=reward, recall_query=recall_query)
                orchestrator.record_step_result(reward, done, next_observation=observation, reward_components=reward_components)

                # A066: Use meaningful progress gate
                meaningful = reward_components.get("meaningful_progress", False)
                if meaningful:
                    consecutive_no_progress_steps = 0
                    last_reward = reward
                else:
                    consecutive_no_progress_steps += 1

                state = observation.get("state", "NOT_FINISHED")
                if getattr(orchestrator, "_step_history", None):
                    orchestrator._step_history[-1].update(
                        {
                            "state_after": state,
                            "reward": reward,
                            "env_reward": env_reward,
                            "progress_reward": reward,
                            "reward_components": reward_components,
                            "env_signals": env_signals,
                            "done": done,
                        }
                    )

                total_steps += 1
                steps_this_attempt += 1
                self._emit_progress_snapshot(
                    task=task,
                    orchestrator=orchestrator,
                    observation=observation,
                    total_steps=total_steps,
                    reward=reward,
                    done=done,
                    start_time=start_time,
                )

                # A053: Ensure telemetry is ALWAYS emitted before any break condition.
                # Use canonical just-recorded step action id to avoid stale attribution.
                try:
                    action_id_local = None
                    if getattr(orchestrator, "_step_history", None):
                        action_id_local = (orchestrator._step_history[-1] or {}).get("action_id")
                    if not action_id_local:
                        if isinstance(action, dict):
                            action_id_local = action.get("action_id")
                        else:
                            action_id_local = getattr(action, "action_id", None) or (action if isinstance(action, str) else None)
                    await orchestrator.perceive_step_response(observation, step=total_steps, reward=reward, done=done, action_id=action_id_local)
                except Exception:
                    logger.exception("perceive_step_response failed in hot path")

                if state == "WIN":
                    success = True
                    break
                elif state == "GAME_OVER":
                    if attempt < max_retries and hasattr(orchestrator, "reset_for_retry"):
                        orchestrator.reset_for_retry(attempt)
                    break
                elif done:
                    success = reward >= 1.0 or state == "WIN"
                    break

                # REPLAN check: escalate if loop/no-progress
                # B202: ensure REPLAN is checked before per-step PERCEIVE
                did_replan = False
                # B202: ensure REPLAN is checked before per-step PERCEIVE
                did_replan = False
                if not success and not done:
                    try:
                        if self._should_replan(orchestrator, consecutive_no_progress_steps):
                            replan_target, replan_route_reason = self._replan_target(orchestrator)
                            self._last_replan_step = len(getattr(orchestrator, "_step_history", []) or [])
                            try:
                                orchestrator._emit_trace_event(
                                    "orchestration_escalation",
                                    "replan",
                                    {
                                        "step": total_steps,
                                        "no_progress_steps": consecutive_no_progress_steps,
                                        "loop_detected": bool((getattr(orchestrator, "_hypothesis_context", {}) or {}).get("loop_detected")),
                                        "from_phase": phase_ctrl.phase_name,
                                        "target_phase": replan_target.value,
                                        "route_reason": replan_route_reason,
                                    },
                                )
                            except Exception:
                                logger.debug("Unable to emit REPLAN trace event", exc_info=True)
                            try:
                                if hasattr(orchestrator, "_record_write_event"):
                                    orchestrator._record_write_event(
                                        kind="replan",
                                        summary=f"replan triggered after {consecutive_no_progress_steps} no-progress step(s)",
                                        detail={"target_phase": replan_target.value, "step": total_steps, "route_reason": replan_route_reason},
                                        source_step=total_steps,
                                    )
                            except Exception:
                                logger.debug("Unable to record REPLAN write event", exc_info=True)
                            try:
                                setattr(orchestrator, "_force_replan", True)
                                if hasattr(orchestrator, "_mark_active_chunk_failed"):
                                    orchestrator._mark_active_chunk_failed("phase_replan")
                                if getattr(orchestrator, "_solve_context", None):
                                    orchestrator._solve_context["active_chunk"] = None
                                if hasattr(orchestrator, "apply_replan_perturbation"):
                                    orchestrator.apply_replan_perturbation(
                                        observation,
                                        route_reason=replan_route_reason,
                                        no_progress_steps=consecutive_no_progress_steps,
                                    )
                            except Exception:
                                logger.debug("Unable to clear stale chunk during REPLAN", exc_info=True)

                            previous_phase = phase_ctrl.phase_name
                            try:
                                phase_ctrl.advance(SolvePhase.REPLAN, force=True)
                                did_replan = True
                            except Exception:
                                logger.exception("Failed entering REPLAN")
                            try:
                                self.brain.current_phase = phase_ctrl.phase_name
                                if hasattr(orchestrator, "set_write_trace_context"):
                                    orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                                if tc is not None:
                                    tc.phase_state = phase_ctrl.to_checkpoint()
                                    mgr.save(checkpoint)
                                self._record_phase_transition(
                                    task=task,
                                    orchestrator=orchestrator,
                                    from_phase=previous_phase,
                                    to_phase=phase_ctrl.phase_name,
                                    step=total_steps,
                                    start_time=start_time,
                                    metadata={
                                        "reason": "replan_enter",
                                        "target_phase": replan_target.value,
                                        "no_progress_steps": consecutive_no_progress_steps,
                                    },
                                )
                            except Exception:
                                logger.exception("Failed syncing REPLAN shim")

                            try:
                                if replan_target == SolvePhase.MODEL:
                                    memory_context = await orchestrator.perceive(observation, step=total_steps)
                                    try:
                                        if isinstance(memory_context, dict) and getattr(orchestrator, "_hypothesis_context", None):
                                            ge = (orchestrator._hypothesis_context or {}).get("graph_evidence")
                                            if ge:
                                                memory_context = dict(memory_context)
                                                memory_context["graph_evidence"] = ge
                                    except Exception:
                                        logger.debug("Failed injecting graph_evidence into memory_context", exc_info=True)
                                    await orchestrator.plan(observation, memory_context)
                            except Exception:
                                logger.exception("Failed to refresh MODEL phase during replan")

                            previous_phase = phase_ctrl.phase_name
                            try:
                                if phase_ctrl.can_advance(replan_target):
                                    phase_ctrl.advance(replan_target)
                                else:
                                    phase_ctrl.advance(replan_target, force=True)
                            except Exception:
                                logger.exception("Failed advancing from REPLAN to target")
                            try:
                                self.brain.current_phase = phase_ctrl.phase_name
                                if hasattr(orchestrator, "set_write_trace_context"):
                                    orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                                if tc is not None:
                                    tc.phase_state = phase_ctrl.to_checkpoint()
                                    mgr.save(checkpoint)
                                self._record_phase_transition(
                                    task=task,
                                    orchestrator=orchestrator,
                                    from_phase=previous_phase,
                                    to_phase=phase_ctrl.phase_name,
                                    step=total_steps,
                                    start_time=start_time,
                                    metadata={
                                        "reason": "replan_exit", 
                                        "target_phase": replan_target.value,
                                        "route_reason": replan_route_reason,
                                    },
                                )
                            except Exception:
                                logger.exception("Failed persisting phase after replan")
                    except Exception:
                        logger.exception("_should_replan check failed")

                # Per-step PERCEIVE advance (B202): run only when we did not replan
                if not success and not done and not did_replan:
                    previous_phase = phase_ctrl.phase_name
                    try:
                        if phase_ctrl.phase != SolvePhase.PERCEIVE:
                            if phase_ctrl.can_advance(SolvePhase.PERCEIVE):
                                phase_ctrl.advance(SolvePhase.PERCEIVE)
                            else:
                                phase_ctrl.advance(SolvePhase.PERCEIVE, force=True)
                    except IllegalPhaseTransition:
                        logger.debug("PERCEIVE gate blocked; forcing perceive")
                    except Exception:
                        logger.exception("Error advancing to PERCEIVE")

                    try:
                        self.brain.current_phase = phase_ctrl.phase_name
                        if hasattr(orchestrator, "set_write_trace_context"):
                            orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                        if tc is not None:
                            tc.phase_state = phase_ctrl.to_checkpoint()
                            mgr.save(checkpoint)
                        self._record_phase_transition(
                            task=task,
                            orchestrator=orchestrator,
                            from_phase=previous_phase,
                            to_phase=phase_ctrl.phase_name,
                            step=total_steps,
                            start_time=start_time,
                            metadata={"reason": "per_step_perceive"},
                        )
                    except Exception:
                        logger.exception("Failed to sync brain phase during per-step perceive")

            if success:
                break
            if state != "GAME_OVER":
                break

        if not success and total_steps >= max_steps * max_retries:
            error_msg = "Max attempts reached across all retries"
        elif not success and not error_msg:
            error_msg = f"Failed after {attempt} attempt(s)"

        # A064: Flush deferred writes at end of puzzle run
        try:
            if hasattr(self.brain, "flush_deferred_writes"):
                await self.brain.flush_deferred_writes()
        except Exception:
            logger.exception("Failed flushing deferred brain writes")

        duration = time.time() - start_time

        judge_verdict = None
        if self.outcome_judge and task.reference_solution:
            try:
                expected = json.loads(task.reference_solution)
                trajectory = self._build_trajectory_summary(orchestrator)
                archetype = getattr(getattr(orchestrator.solve_engine, "_archetype", None), "value", "unknown")
                verdict = await self.outcome_judge.evaluate(
                    observation.get("grid"), expected, trajectory, archetype
                )
                if verdict:
                    judge_verdict = asdict(verdict)
            except Exception:
                logger.exception("B181 failed")

        benchmark_metrics = {}
        if hasattr(orchestrator, "get_benchmark_metrics"):
            try:
                benchmark_metrics = orchestrator.get_benchmark_metrics()
            except Exception:
                logger.exception("B89: get_benchmark_metrics failed")

        trajectory_score = None
        try:
            trajectory_score = self.trajectory_evaluator.evaluate(
                trace=list(getattr(orchestrator, "_execution_trace", [])),
                step_history=list(getattr(orchestrator, "_step_history", [])),
            ).to_dict()
        except Exception as exc:
            logger.warning("B186: trajectory evaluation failed: %s", exc)

        failure_class = None
        if not success:
            failure_class = classify_failure(
                exc=None,
                final_state=state,
                error_message=error_msg,
                no_progress_steps=max(
                    consecutive_no_progress_steps,
                    int(getattr(orchestrator, "_consecutive_no_progress_steps", 0) or 0),
                ),
                budget_exhausted=bool(
                    getattr(orchestrator.cost_tracker, "budget_exhausted", False) is True
                ) if getattr(orchestrator, "cost_tracker", None) else False,
                wall_clock_timeout=bool(error_msg and "wall-clock" in error_msg.lower()),
                max_steps_reached=(total_steps >= max_steps * max_retries),
                loop_detected=self._effective_loop_detected(orchestrator),
                graduation_reason=str(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "graduation_reason", "")),
                coverage_saturated=bool(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "coverage_saturated", False)),
                plateau_escalation_required=bool(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "plateau_escalation_required", False)),
            ).value

        cost_usd = None
        invalid_action_count = None
        if isinstance(benchmark_metrics, dict):
            cost_usd = self._safe_float((benchmark_metrics.get("token_cost") or {}).get("cost_usd"))
            invalid_action_count = (benchmark_metrics.get("prompt_budget") or {}).get("invalid_action_count")

        sc = getattr(orchestrator, "_solve_context", None)
        task_result = ABTaskResult(
            task_id=task.task_id,
            variant=ABVariant.SIDEQUESTS,
            correct=success,
            steps=total_steps,
            tokens_input=total_tokens_in,
            tokens_output=total_tokens_out,
            error_message=error_msg,
            failure_class=failure_class,
            response_text=f"Solved: {success} in {total_steps} steps ({attempt} attempt(s))",
            attempts=attempt,
            cost_usd=cost_usd,
            invalid_action_count=invalid_action_count,
            dissonance_triggered=bool(self._solve_context_get(sc, "dissonance_detected", self._solve_context_get(sc, "dissonance", False))),
            trajectory_score=trajectory_score,
            final_state=state,
            final_observation=observation,
            judge_verdict=judge_verdict,
            terminal_value_score=float(self._solve_context_get(sc, "terminal_value_score", 0.0)),
            terminal_value_components=dict(self._solve_context_get(sc, "terminal_value_components", {})),
        )
        setattr(task_result, "bootstrap_write_trace", bootstrap_write_trace)
        setattr(task_result, "final_write_trace", final_write_trace)
        setattr(task_result, "benchmark_metrics", benchmark_metrics)
        setattr(task_result, "sidequests_ledger", list(self._ledger))
        # A073: World model snapshot for evaluation
        if hasattr(orchestrator, "world_model"):
             setattr(task_result, "world_model_snapshot", orchestrator.world_model.to_trace_snapshot())
        # A075: Publish learned mechanics to aggregate memory
        if hasattr(orchestrator, "publish_mechanic_memory"):
            publish_result = orchestrator.publish_mechanic_memory()
            if inspect.isawaitable(publish_result):
                await publish_result
            
        return task_result, duration

    async def _run_puzzle_with_brain(self, orchestrator: ARCOrchestrator, task: ABTask, brain_client: BrainClientProtocol, variant_config: dict, checkpoint, mgr) -> tuple[ABTaskResult, float, ARCOrchestrator]:
        """Run a single puzzle using the provided `brain_client` and `variant_config`.

        This variant of `_run_puzzle` avoids mutating the DurableARCRunner instance
        (no changes to `self.brain` or `self._ledger`) so it is safe to run
        concurrently from StrategyRacer.
        Returns `(ABTaskResult, duration, orchestrator)`.
        """
        from agents.arc3.cost_tracker import CostTracker

        max_steps = self.harness.config.parameters.get("max_attempts_per_puzzle", 10)
        max_retries = variant_config.get("max_retries_per_puzzle", self.config.get("max_retries_per_puzzle", 3))
        # A039: overall wall-clock budget for the whole puzzle run.
        wall_clock_budget = float(getattr(self.harness.config, "timeout", 3600))
        if getattr(orchestrator, "_supervisor", None) is not None:
            try:
                orchestrator._supervisor.abandon_zero_reward_steps = min(
                    int(getattr(orchestrator._supervisor, "abandon_zero_reward_steps", 30)),
                    max(5, int(max_steps) - 2),
                )
            except Exception:
                logger.debug("Unable to align supervisor threshold with max steps", exc_info=True)
        adapter = ARC3Adapter(
            brain_client=brain_client,
            session_id=orchestrator.session_id,
            task_id=task.task_id,
        )
        game_id = getattr(task, "game_id", "unknown")

        start_time = time.time()
        total_steps = 0
        success = False
        done = False
        error_msg: str | None = None
        total_tokens_in = 0
        total_tokens_out = 0
        last_grid = None
        last_levels_completed: int | None = None
        last_score: float | None = None
        last_reward = 0.0
        consecutive_no_progress_steps = 0
        bootstrap_write_trace: list[dict] = []
        final_write_trace: list[dict] = []
        graph_health = await self._probe_graph_health(orchestrator=orchestrator, task=task)
        await self._seed_bootstrap_lessons_if_empty(task=task, graph_health=graph_health)

        # B180: Token cost tracking and budget enforcement (variant_config aware)
        cost_cfg = {}
        llm_cfg = {}
        if isinstance(variant_config, dict):
            cost_cfg = variant_config.get("cost", {})
            llm_cfg = variant_config.get("llm", {})

        model_name = llm_cfg.get("model", "unknown") if isinstance(llm_cfg, dict) else "unknown"
        pricing = {}
        if isinstance(cost_cfg, dict):
            pricing = cost_cfg.get("pricing_per_million_tokens", {}).get(model_name, {"input": 0.0, "output": 0.0})

        budget = float('inf')
        if isinstance(cost_cfg, dict):
            val = cost_cfg.get("budget_per_puzzle_usd")
            if val is not None:
                try:
                    budget = float(val)
                except (TypeError, ValueError):
                    budget = float('inf')

        cost_tracker = CostTracker(
            model_name=str(model_name),
            input_price_per_m=float(pricing.get("input", 0.0) if isinstance(pricing, dict) else 0.0),
            output_price_per_m=float(pricing.get("output", 0.0) if isinstance(pricing, dict) else 0.0),
            budget_usd=budget,
        )

        # Ensure the orchestrator sees the same cost tracker used by the driver
        orchestrator.cost_tracker = cost_tracker

        async def _initial_frame_variant(game_id: str) -> tuple[dict, str | None]:
            start_t = time.time()
            if self.harness.mock_api:
                frame = self.harness._get_mock_initial_frame(game_id)
                if hasattr(brain_client, "record_arc_api_call"):
                    brain_client.record_arc_api_call(
                        phase=getattr(brain_client, "current_phase", "bootstrap"),
                        method="GET",
                        endpoint="/api/games/initial",
                        request_payload={"game_id": game_id},
                        response_payload=frame,
                        latency_ms=(time.time() - start_t) * 1000,
                    )
                return frame, frame.get("guid")

            session = getattr(self.harness, "_session", None)
            if session is None:
                raise RuntimeError("ARC API session not initialized. Did you call harness.setup()?")

            sc_start = time.time()
            sc_resp = await session.post("/api/scorecard/open", json={})
            sc_latency = (time.time() - sc_start) * 1000
            await self._safe_raise_for_status(sc_resp)
            sc_json = await self._safe_json(sc_resp)
            card_id = sc_json["card_id"]
            if hasattr(brain_client, "record_arc_api_call"):
                brain_client.record_arc_api_call(
                    phase=getattr(brain_client, "current_phase", "bootstrap"),
                    method="POST",
                    endpoint="/api/scorecard/open",
                    request_payload={},
                    response_payload=sc_json,
                    latency_ms=sc_latency,
                )

            reset_start = time.time()
            reset_payload = {"game_id": game_id, "card_id": card_id}
            reset_resp = await session.post("/api/cmd/RESET", json=reset_payload)
            reset_latency = (time.time() - reset_start) * 1000
            await self._safe_raise_for_status(reset_resp)
            frame = await self._safe_json(reset_resp)
            if hasattr(brain_client, "record_arc_api_call"):
                brain_client.record_arc_api_call(
                    phase=getattr(brain_client, "current_phase", "bootstrap"),
                    method="POST",
                    endpoint="/api/cmd/RESET",
                    request_payload=reset_payload,
                    response_payload=frame,
                    latency_ms=reset_latency,
                )
            return frame, frame.get("guid")

        async def _execute_action_variant(game_id: str, guid: str | None, action: Mapping[str, Any], step: int) -> tuple[dict, float, bool, str | None]:
            start_t = time.time()
            if self.harness.mock_api:
                frame, reward, done = self.harness._execute_mock_action(game_id, action, step)
                if hasattr(brain_client, "record_arc_api_call"):
                    brain_client.record_arc_api_call(
                        phase=getattr(brain_client, "current_phase", "act"),
                        method="POST",
                        endpoint=f"/api/cmd/{action.get('action_id', 'unknown')}",
                        request_payload=action,
                        response_payload=frame,
                        latency_ms=(time.time() - start_t) * 1000,
                    )
                return frame, reward, done, frame.get("guid", guid)

            session = getattr(self.harness, "_session", None)
            if session is None:
                raise RuntimeError("ARC API session not initialized. Did you call harness.setup()?")

            action_id = action.get("action_id", "ACTION1")
            payload = {"game_id": game_id, "guid": guid}
            if action_id == "ACTION6":
                payload["x"] = action.get("x", 0)
                payload["y"] = action.get("y", 0)
            if "rationale" in action:
                payload["reasoning"] = action["rationale"]

            call_start = time.time()
            action_resp = await session.post(f"/api/cmd/{action_id}", json=payload)
            latency = (time.time() - call_start) * 1000
            await self._safe_raise_for_status(action_resp)
            frame = await self._safe_json(action_resp)
            if hasattr(brain_client, "record_arc_api_call"):
                brain_client.record_arc_api_call(
                    phase=getattr(brain_client, "current_phase", "act"),
                    method="POST",
                    endpoint=f"/api/cmd/{action_id}",
                    request_payload=payload,
                    response_payload=frame,
                    latency_ms=latency,
                )
            reward = self._extract_env_reward(frame)
            done = frame.get("state") in ("WIN", "GAME_OVER")
            return frame, reward, done, frame.get("guid", guid)

        for attempt in range(1, max_retries + 1):
            frame_response, guid = await _initial_frame_variant(game_id)
            observation = adapter.normalize_observation(frame_response)
            last_grid = observation.get("grid")
            last_levels_completed = self._safe_int(observation.get("levels_completed"))
            last_score = self._safe_float(frame_response.get("score"))

            training_examples = observation.get("training_examples") or []
            if training_examples:
                try:
                    phase1_result = await orchestrator.run_phase1(observation, training_examples)
                    if phase1_result and phase1_result.get("verified"):
                        orchestrator._verified_output_grid = phase1_result["output_grid"]
                        orchestrator._phase2_mode = "execution"
                except Exception:
                    logger.exception("B156: Phase 1 failed")

            # Initialize or restore PhaseController for this attempt (variant runner)
            tc = None
            try:
                tc = (checkpoint.tasks.get(task.task_id) if checkpoint and getattr(checkpoint, 'tasks', None) else None)
            except Exception:
                tc = None

            phase_ctrl = None
            try:
                if tc and getattr(tc, "phase_state", None):
                    phase_ctrl = PhaseController.from_checkpoint(tc.phase_state)
                else:
                    phase_ctrl = PhaseController()
            except Exception:
                logger.exception("Failed to restore PhaseController for variant from checkpoint; creating fresh controller")
                phase_ctrl = PhaseController()

            try:
                engine = getattr(orchestrator, "solve_engine", None)
                if engine:
                    phase_ctrl.register_gate(SolvePhase.MODEL, SolvePhase.HYPOTHESIZE, engine.is_exploration_complete)
                    phase_ctrl.register_gate(SolvePhase.HYPOTHESIZE, SolvePhase.ROUTE, engine.has_hypothesis)
                    phase_ctrl.register_gate(SolvePhase.ROUTE, SolvePhase.EXECUTE, engine.has_active_chunk)
            except Exception:
                logger.exception("Failed registering phase gates for variant")

            try:
                orchestrator._phase_controller = phase_ctrl
            except Exception:
                pass

            try:
                brain_client.current_phase = phase_ctrl.phase_name
                if hasattr(orchestrator, "set_write_trace_context"):
                    orchestrator.set_write_trace_context(phase_ctrl.phase_name)
            except Exception:
                logger.exception("Failed to set variant initial brain phase shim")

            try:
                if tc is not None:
                    tc.phase_state = phase_ctrl.to_checkpoint()
                    mgr.save(checkpoint)
            except Exception:
                logger.exception("Failed to persist variant initial phase state")

            # Do not modify shared self._current_step here; keep local counters
            memory_context = await orchestrator.perceive(observation, step=0)
            if isinstance(memory_context, dict):
                memory_context.setdefault("graph_health", dict(graph_health))
                memory_context.setdefault("memory_degraded", getattr(brain_client, "memory_degraded", False) is True)
                if getattr(brain_client, "memory_degraded_reason", None):
                    memory_context.setdefault("memory_degraded_reason", str(getattr(brain_client, "memory_degraded_reason")))
            try:
                if isinstance(memory_context, dict) and getattr(orchestrator, "_hypothesis_context", None):
                    ge = (orchestrator._hypothesis_context or {}).get("graph_evidence")
                    if ge:
                        memory_context = dict(memory_context)
                        memory_context["graph_evidence"] = ge
            except Exception:
                logger.debug("Failed injecting graph_evidence into memory_context", exc_info=True)
            await orchestrator.plan(observation, memory_context)
            if hasattr(orchestrator, "consume_write_trace"):
                bootstrap_write_trace = list(orchestrator.consume_write_trace())

            # Advance PERCEIVE -> MODEL (bootstrap split)
            try:
                if phase_ctrl.phase == SolvePhase.PERCEIVE:
                    previous_phase = phase_ctrl.phase_name
                    try:
                        if phase_ctrl.can_advance(SolvePhase.MODEL):
                            phase_ctrl.advance(SolvePhase.MODEL)
                        else:
                            phase_ctrl.advance(SolvePhase.MODEL, force=True)
                    except IllegalPhaseTransition:
                        phase_ctrl.advance(SolvePhase.MODEL, force=True)
                    brain_client.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                        metadata={"reason": "bootstrap_split_variant"},
                    )
            except Exception:
                logger.exception("Failed advancing variant to MODEL phase")

            state = observation.get("state", "NOT_FINISHED")
            steps_this_attempt = 0

            while steps_this_attempt < max_steps:
                # A039: Check wall-clock budget
                elapsed = time.time() - start_time
                if elapsed > wall_clock_budget:
                    error_msg = f"Wall-clock budget exhausted ({elapsed:.1f}s > {wall_clock_budget}s)"
                    done = True
                    break

                budget_exhausted = bool(
                    getattr(orchestrator.cost_tracker, "budget_exhausted", False) is True
                ) if getattr(orchestrator, "cost_tracker", None) else False
                if orchestrator.cost_tracker and budget_exhausted:
                    error_msg = "Budget exhausted"
                    done = True
                    break

                if getattr(orchestrator, "_should_abandon", False):
                    error_msg = getattr(
                        orchestrator,
                        "_world_model_failure_reason",
                        "Supervisor abandoned",
                    )
                    done = True
                    break

                # Advance to HYPOTHESIZE
                previous_phase = phase_ctrl.phase_name
                try:
                    if phase_ctrl.phase != SolvePhase.HYPOTHESIZE:
                        if phase_ctrl.can_advance(SolvePhase.HYPOTHESIZE):
                            phase_ctrl.advance(SolvePhase.HYPOTHESIZE)
                        else:
                            phase_ctrl.advance(SolvePhase.HYPOTHESIZE, force=True)
                except IllegalPhaseTransition:
                    logger.debug("Variant HYPOTHESIZE gate blocked; continuing")
                except Exception:
                    logger.exception("Error advancing variant to HYPOTHESIZE")

                try:
                    brain_client.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                    )
                except Exception:
                    logger.exception("Failed syncing variant brain phase during hypothesize")

                prior_step = orchestrator._step_history[-1] if getattr(orchestrator, "_step_history", None) else None
                hyp_ctx = await orchestrator.hypothesize(
                    observation,
                    prior_step.get("action_id") if prior_step else None,
                    total_steps,
                    transition_meta=prior_step,
                )

                previous_phase = phase_ctrl.phase_name
                try:
                    if phase_ctrl.phase != SolvePhase.ROUTE:
                        if phase_ctrl.can_advance(SolvePhase.ROUTE):
                            phase_ctrl.advance(SolvePhase.ROUTE)
                        else:
                            phase_ctrl.advance(SolvePhase.ROUTE, force=True)
                except IllegalPhaseTransition:
                    logger.debug("Variant ROUTE gate blocked; continuing")
                except Exception:
                    logger.exception("Error advancing variant to ROUTE")

                try:
                    brain_client.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                    )
                except Exception:
                    logger.exception("Failed syncing variant brain phase during solve/route")

                # A061: Single-Action Macro Executor
                macro_eligibility = orchestrator.check_macro_eligibility(observation)
                if (
                    isinstance(macro_eligibility, tuple)
                    and len(macro_eligibility) == 2
                ):
                    is_eligible, macro_action_id = macro_eligibility
                else:
                    is_eligible, macro_action_id = False, None
                macro_reason = getattr(orchestrator, "_macro_eligibility_reason", None) or "single_action_deterministic_progress"
                if is_eligible:
                    orchestrator.enter_macro_mode(macro_action_id, reason=macro_reason)
                    
                    macro_cfg = self.config.get("macro_executor", {})
                    max_macro_steps = macro_cfg.get("max_macro_steps", 25)
                    self._macro_hashes = {observation.get("frame_hash")} if observation.get("frame_hash") else set()
                    
                    while orchestrator._macro_active and orchestrator._macro_step_count < max_macro_steps:
                        orchestrator._macro_step_count += 1
                        
                        macro_action = {
                            "action_id": macro_action_id,
                            "rationale": f"macro execution [{orchestrator._macro_id}] step {orchestrator._macro_step_count}",
                            "decision_source": "macro_executor",
                            "macro_id": orchestrator._macro_id,
                            "macro_step_index": orchestrator._macro_step_count,
                            "macro_reason": macro_reason,
                            "x": 0,
                            "y": 0,
                        }
                        
                        # Use local variant executor
                        frame_response, env_reward, done, guid = await _execute_action_variant(game_id, guid, macro_action, total_steps)
                        env_signals = self._extract_env_signals(frame_response)
                        observation = adapter.normalize_observation(frame_response)
                        
                        target_color_id = self._resolve_target_color_id(orchestrator)
                        # A066: pass terminal value score if available
                        prev_tvs = float(getattr(orchestrator._solve_context, "terminal_value_score", 0.0) if orchestrator._solve_context else 0.0)
                        reward, reward_components = self._compute_progress_reward(
                            env_reward=env_reward,
                            prev_grid=last_grid,
                            next_grid=observation.get("grid"),
                            prev_levels_completed=last_levels_completed,
                            next_levels_completed=self._safe_int(observation.get("levels_completed")),
                            prev_score=last_score,
                            next_score=self._safe_float(frame_response.get("score")),
                            target_color_id=target_color_id,
                            prev_terminal_value_score=prev_tvs,
                        )
                        
                        # Stop conditions (A067)
                        stop_reason = None
                        state = observation.get("state", "NOT_FINISHED")
                        if state in ("WIN", "GAME_OVER") or done:
                            stop_reason = f"terminal_state_{state}"
                        elif env_reward != 0.0:
                            stop_reason = "env_reward_detected"
                        elif macro_action_id not in (observation.get("available_actions") or []):
                            stop_reason = "available_actions_changed"
                        elif not reward_components.get("meaningful_progress", False):
                            stop_reason = "meaningful_progress_stalled"
                        
                        curr_hash = observation.get("frame_hash")
                        if curr_hash and curr_hash in getattr(self, "_macro_hashes", set()):
                            stop_reason = "repeated_frame_hash"
                        if curr_hash:
                            if not hasattr(self, "_macro_hashes"): self._macro_hashes = set()
                            self._macro_hashes.add(curr_hash)

                        last_grid = observation.get("grid")
                        last_levels_completed = self._safe_int(observation.get("levels_completed"))
                        last_score = self._safe_float(frame_response.get("score"))
                        
                        # A066: Use meaningful progress gate
                        meaningful = reward_components.get("meaningful_progress", False)
                        if meaningful:
                            consecutive_no_progress_steps = 0
                            last_reward = reward
                            orchestrator._macro_terminal_stall_count = 0
                        else:
                            consecutive_no_progress_steps += 1
                            if not hasattr(orchestrator, "_macro_terminal_stall_count"):
                                orchestrator._macro_terminal_stall_count = 0
                            orchestrator._macro_terminal_stall_count += 1
                        
                        if orchestrator._macro_terminal_stall_count >= 3:
                            stop_reason = "terminal_stall"
                        
                        self._append_macro_step_record(
                            orchestrator=orchestrator,
                            observation=observation,
                            action=macro_action,
                            total_steps=total_steps + 1,
                            available_actions=observation.get("available_actions", []),
                        )
                        orchestrator.record_step_result(reward, done, next_observation=observation)
                        if getattr(orchestrator, "_step_history", None):
                            frame_delta = orchestrator._step_history[-1].get("frame_delta", {}) or {}
                            n_changed = int(
                                frame_delta.get("n_cells_changed")
                                or reward_components.get("pixels_changed")
                                or reward_components.get("n_cells_changed")
                                or 0
                            )
                            if n_changed == 0:
                                stop_reason = "zero_delta"

                            # A063: Check for object progress stagnation
                            last_op = orchestrator._step_history[-1].get("object_progress", {})
                            op_score = float(last_op.get("score", 0.0) or 0.0)
                            if op_score <= 0.0 and n_changed <= 0:
                                stop_reason = "object_stagnation"
                            effect_class = (orchestrator._step_history[-1].get("compiled_world_delta") or {}).get("effect_class")
                            if effect_class == "harmful":
                                stop_reason = "harmful_effect"
                            elif self._macro_prediction_falsified(
                                orchestrator._step_history[-1],
                                getattr(getattr(orchestrator, "_last_planner_selection", None), "selected", None),
                            ):
                                stop_reason = "prediction_falsified"

                            orchestrator._step_history[-1].update({
                                "state_after": state,
                                "reward": reward,
                                "env_reward": env_reward,
                                "progress_reward": reward,
                                "reward_components": reward_components,
                                "env_signals": env_signals,
                                "done": done,
                                "decision_source": "macro_executor",
                                "macro_id": orchestrator._macro_id,
                                "macro_step_index": orchestrator._macro_step_count,
                                "macro_reason": macro_reason,
                                "args_effective": orchestrator.get_args_effective(macro_action_id),
                                "coordinate_relevance": dict(getattr(orchestrator, "_action_coord_relevance", {}).get(macro_action_id, {})),
                            })
                            
                        total_steps += 1
                        steps_this_attempt += 1
                        
                        self._emit_progress_snapshot(
                            task=task,
                            orchestrator=orchestrator,
                            observation=observation,
                            total_steps=total_steps,
                            reward=reward,
                            done=done,
                            start_time=start_time,
                        )
                        
                        await orchestrator.perceive_step_response(observation, step=total_steps, reward=reward, done=done, action_id=macro_action_id)

                        if stop_reason:
                            if getattr(orchestrator, "_step_history", None):
                                orchestrator._step_history[-1]["macro_stop_reason"] = stop_reason
                                orchestrator._step_history[-1]["macro_terminal_stall_count"] = getattr(orchestrator, "_macro_terminal_stall_count", 0)
                            orchestrator.exit_macro_mode(stop_reason)
                            break
                        
                        if steps_this_attempt >= max_steps:
                            if getattr(orchestrator, "_step_history", None):
                                orchestrator._step_history[-1]["macro_stop_reason"] = "max_steps_per_attempt_reached"
                            orchestrator.exit_macro_mode("max_steps_per_attempt_reached")
                            break
                    
                    state = observation.get("state", "NOT_FINISHED")
                    if state == "WIN":
                        success = True
                        break
                    elif state == "GAME_OVER":
                        if attempt < max_retries and hasattr(orchestrator, "reset_for_retry"):
                            orchestrator.reset_for_retry(attempt)
                        break
                    elif done:
                        success = reward >= 1.0 or state == "WIN"
                        break
                    
                    continue

                await orchestrator.solve(observation, hyp_ctx, total_steps)

                if getattr(orchestrator, "_force_replan", False) is True:
                    logger.warning(f"A079: variant early_stop triggered at step {total_steps}")
                    self._emit_world_model_decision_snapshot(
                        task=task,
                        orchestrator=orchestrator,
                        observation=observation,
                        executed_step_count=total_steps,
                        start_time=start_time,
                    )
                    if getattr(orchestrator, "_should_abandon", False):
                        error_msg = getattr(
                            orchestrator,
                            "_world_model_failure_reason",
                            "world_model_strategy_exhausted",
                        )
                        break
                    if self._should_replan(orchestrator, consecutive_no_progress_steps):
                        pass
                    else:
                        break

                previous_phase = phase_ctrl.phase_name
                try:
                    if phase_ctrl.phase != SolvePhase.EXECUTE:
                        if phase_ctrl.can_advance(SolvePhase.EXECUTE):
                            phase_ctrl.advance(SolvePhase.EXECUTE)
                        else:
                            phase_ctrl.advance(SolvePhase.EXECUTE, force=True)
                except IllegalPhaseTransition:
                    logger.debug("Variant EXECUTE gate blocked; forcing execute")
                except Exception:
                    logger.exception("Error advancing variant to EXECUTE")

                try:
                    brain_client.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                    )
                except Exception:
                    logger.exception("Failed syncing variant brain phase during act")

                action = await orchestrator.act(observation, memory_context, total_steps + 1)

                total_tokens_in += self.harness.serializer._estimate_tokens(json.dumps(observation))
                total_tokens_out += self.harness.serializer._estimate_tokens(str(action))

                frame_response, env_reward, done, guid = await _execute_action_variant(game_id, guid, action, total_steps)
                env_signals = self._extract_env_signals(frame_response)
                observation = adapter.normalize_observation(frame_response)
                target_color_id = self._resolve_target_color_id(orchestrator)
                # A066: pass terminal value score if available
                prev_tvs = float(getattr(orchestrator._solve_context, "terminal_value_score", 0.0) if orchestrator._solve_context else 0.0)
                reward, reward_components = self._compute_progress_reward(
                    env_reward=env_reward,
                    prev_grid=last_grid,
                    next_grid=observation.get("grid"),
                    prev_levels_completed=last_levels_completed,
                    next_levels_completed=self._safe_int(observation.get("levels_completed")),
                    prev_score=last_score,
                    next_score=self._safe_float(frame_response.get("score")),
                    target_color_id=target_color_id,
                    prev_terminal_value_score=prev_tvs,
                )
                last_grid = observation.get("grid")
                last_levels_completed = self._safe_int(observation.get("levels_completed"))
                last_score = self._safe_float(frame_response.get("score"))

                recall_query = None
                if total_steps == 0 or consecutive_no_progress_steps >= 2:
                    recall_query = "What did I learn from similar puzzles?"

                previous_phase = phase_ctrl.phase_name
                try:
                    if phase_ctrl.phase != SolvePhase.EVALUATE:
                        if phase_ctrl.can_advance(SolvePhase.EVALUATE):
                            phase_ctrl.advance(SolvePhase.EVALUATE)
                        else:
                            phase_ctrl.advance(SolvePhase.EVALUATE, force=True)
                except IllegalPhaseTransition:
                    logger.debug("Variant EVALUATE gate blocked; forcing evaluate")
                except Exception:
                    logger.exception("Error advancing variant to EVALUATE")

                try:
                    brain_client.current_phase = phase_ctrl.phase_name
                    if hasattr(orchestrator, "set_write_trace_context"):
                        orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                    if tc is not None:
                        tc.phase_state = phase_ctrl.to_checkpoint()
                        mgr.save(checkpoint)
                    self._record_phase_transition(
                        task=task,
                        orchestrator=orchestrator,
                        from_phase=previous_phase,
                        to_phase=phase_ctrl.phase_name,
                        step=total_steps,
                        start_time=start_time,
                    )
                except Exception:
                    logger.exception("Failed syncing variant brain phase during ingest/evaluate")

                await adapter.ingest_step(frame_response, action, reward=reward, recall_query=recall_query)
                orchestrator.record_step_result(reward, done, next_observation=observation, reward_components=reward_components)

                # A066: Use meaningful progress gate
                meaningful = reward_components.get("meaningful_progress", False)
                if meaningful:
                    consecutive_no_progress_steps = 0
                    last_reward = reward
                else:
                    consecutive_no_progress_steps += 1

                state = observation.get("state", "NOT_FINISHED")
                if getattr(orchestrator, "_step_history", None):
                    orchestrator._step_history[-1].update(
                        {
                            "state_after": state,
                            "reward": reward,
                            "env_reward": env_reward,
                            "progress_reward": reward,
                            "reward_components": reward_components,
                            "env_signals": env_signals,
                            "done": done,
                        }
                    )

                total_steps += 1
                steps_this_attempt += 1
                # Emit a localized progress snapshot if requested (do not mutate shared runner state)
                if self._progress_callback:
                    last_step = orchestrator._step_history[-1] if getattr(orchestrator, "_step_history", None) else {}
                    phase_summary = self._build_phase_summary(orchestrator)
                    snapshot = {
                        "snapshot_type": "step",
                        "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "game_id": getattr(task, "game_id", "unknown"),
                        "task_id": task.task_id,
                        "step": total_steps,
                        "runtime_seconds": round(time.time() - start_time, 2),
                        "state_after": observation.get("state", "NOT_FINISHED"),
                        "reward": reward,
                        "env_reward": last_step.get("env_reward"),
                        "progress_reward": last_step.get("progress_reward", reward),
                        "reward_components": last_step.get("reward_components"),
                        "env_signals": last_step.get("env_signals"),
                        "done": done,
                        "action_id": last_step.get("action_id"),
                        "rationale": last_step.get("rationale"),
                        "guard_status": last_step.get("guard_status"),
                        "thinking_trace": last_step.get("thinking_trace", []),
                        "frame_hash": observation.get("frame_hash"),
                        "available_actions": observation.get("available_actions", []),
                        "solve_phase_summary": phase_summary,
                        "sidequests_ledger_count": len(getattr(brain_client, "ledger", []) or []),
                    }
                    self._progress_callback(snapshot)

                # A053: Ensure telemetry is ALWAYS emitted before any break condition.
                # Use canonical just-recorded step action id to avoid stale attribution.
                try:
                    action_id_local = None
                    if getattr(orchestrator, "_step_history", None):
                        action_id_local = (orchestrator._step_history[-1] or {}).get("action_id")
                    if not action_id_local:
                        if isinstance(action, dict):
                            action_id_local = action.get("action_id")
                        else:
                            action_id_local = getattr(action, "action_id", None) or (action if isinstance(action, str) else None)
                    await orchestrator.perceive_step_response(observation, step=total_steps, reward=reward, done=done, action_id=action_id_local)
                except Exception:
                    logger.exception("Variant perceive_step_response failed in hot path")

                if state == "WIN":
                    success = True
                    break
                elif state == "GAME_OVER":
                    if attempt < max_retries and hasattr(orchestrator, "reset_for_retry"):
                        orchestrator.reset_for_retry(attempt)
                    break
                elif done:
                    success = reward >= 1.0 or state == "WIN"
                    break

                if not success and not done:
                    try:
                        if self._should_replan(orchestrator, consecutive_no_progress_steps):
                            replan_target, replan_route_reason = self._replan_target(orchestrator)
                            self._last_replan_step = len(getattr(orchestrator, "_step_history", []) or [])
                            try:
                                orchestrator._emit_trace_event(
                                    "orchestration_escalation",
                                    "replan",
                                    {
                                        "step": total_steps,
                                        "no_progress_steps": consecutive_no_progress_steps,
                                        "loop_detected": bool((getattr(orchestrator, "_hypothesis_context", {}) or {}).get("loop_detected")),
                                        "from_phase": phase_ctrl.phase_name,
                                        "target_phase": replan_target.value,
                                        "route_reason": replan_route_reason,
                                    },
                                )
                            except Exception:
                                logger.debug("Unable to emit variant REPLAN trace event", exc_info=True)
                            try:
                                setattr(orchestrator, "_force_replan", True)
                                if hasattr(orchestrator, "_mark_active_chunk_failed"):
                                    orchestrator._mark_active_chunk_failed("phase_replan")
                                if getattr(orchestrator, "_solve_context", None):
                                    orchestrator._solve_context["active_chunk"] = None
                                if hasattr(orchestrator, "apply_replan_perturbation"):
                                    orchestrator.apply_replan_perturbation(
                                        observation,
                                        route_reason=replan_route_reason,
                                        no_progress_steps=consecutive_no_progress_steps,
                                    )
                            except Exception:
                                logger.debug("Unable to clear stale chunk during variant REPLAN", exc_info=True)

                            previous_phase = phase_ctrl.phase_name
                            try:
                                phase_ctrl.advance(SolvePhase.REPLAN, force=True)
                                did_replan = True
                            except Exception:
                                logger.exception("Failed entering variant REPLAN")
                            try:
                                brain_client.current_phase = phase_ctrl.phase_name
                                if hasattr(orchestrator, "set_write_trace_context"):
                                    orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                                if tc is not None:
                                    tc.phase_state = phase_ctrl.to_checkpoint()
                                    mgr.save(checkpoint)
                                self._record_phase_transition(
                                    task=task,
                                    orchestrator=orchestrator,
                                    from_phase=previous_phase,
                                    to_phase=phase_ctrl.phase_name,
                                    step=total_steps,
                                    start_time=start_time,
                                    metadata={
                                        "reason": "replan_enter",
                                        "target_phase": replan_target.value,
                                        "no_progress_steps": consecutive_no_progress_steps,
                                    },
                                )
                            except Exception:
                                logger.exception("Failed syncing variant REPLAN shim")

                            try:
                                if replan_target == SolvePhase.MODEL:
                                    memory_context = await orchestrator.perceive(observation, step=total_steps)
                                    try:
                                        if isinstance(memory_context, dict) and getattr(orchestrator, "_hypothesis_context", None):
                                            ge = (orchestrator._hypothesis_context or {}).get("graph_evidence")
                                            if ge:
                                                memory_context = dict(memory_context)
                                                memory_context["graph_evidence"] = ge
                                    except Exception:
                                        logger.debug("Failed injecting graph_evidence into memory_context", exc_info=True)
                                    await orchestrator.plan(observation, memory_context)
                            except Exception:
                                logger.exception("Failed to refresh MODEL phase during variant replan")

                            previous_phase = phase_ctrl.phase_name
                            try:
                                if phase_ctrl.can_advance(replan_target):
                                    phase_ctrl.advance(replan_target)
                                else:
                                    phase_ctrl.advance(replan_target, force=True)
                            except Exception:
                                logger.exception("Failed advancing variant from REPLAN to target")
                            try:
                                brain_client.current_phase = phase_ctrl.phase_name
                                if hasattr(orchestrator, "set_write_trace_context"):
                                    orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                                if tc is not None:
                                    tc.phase_state = phase_ctrl.to_checkpoint()
                                    mgr.save(checkpoint)
                                self._record_phase_transition(
                                    task=task,
                                    orchestrator=orchestrator,
                                    from_phase=previous_phase,
                                    to_phase=phase_ctrl.phase_name,
                                    step=total_steps,
                                    start_time=start_time,
                                    metadata={
                                        "reason": "replan_exit", 
                                        "target_phase": replan_target.value,
                                        "route_reason": replan_route_reason,
                                    },
                                )
                            except Exception:
                                logger.exception("Failed persisting variant phase after replan")
                    except Exception:
                        logger.exception("Variant _should_replan check failed")

                # Per-step PERCEIVE advance for variant runners (B202)
                if not success and not done and not did_replan:
                    previous_phase = phase_ctrl.phase_name
                    try:
                        if phase_ctrl.phase != SolvePhase.PERCEIVE:
                            if phase_ctrl.can_advance(SolvePhase.PERCEIVE):
                                phase_ctrl.advance(SolvePhase.PERCEIVE)
                            else:
                                phase_ctrl.advance(SolvePhase.PERCEIVE, force=True)
                    except IllegalPhaseTransition:
                        logger.debug("Variant PERCEIVE gate blocked; forcing perceive")
                    except Exception:
                        logger.exception("Error advancing variant to PERCEIVE")

                    try:
                        brain_client.current_phase = phase_ctrl.phase_name
                        if hasattr(orchestrator, "set_write_trace_context"):
                            orchestrator.set_write_trace_context(phase_ctrl.phase_name)
                        if tc is not None:
                            tc.phase_state = phase_ctrl.to_checkpoint()
                            mgr.save(checkpoint)
                        self._record_phase_transition(
                            task=task,
                            orchestrator=orchestrator,
                            from_phase=previous_phase,
                            to_phase=phase_ctrl.phase_name,
                            step=total_steps,
                            start_time=start_time,
                            metadata={"reason": "per_step_perceive_variant"},
                        )
                    except Exception:
                        logger.exception("Failed to sync variant brain phase during per-step perceive")

            if success:
                break
            if state != "GAME_OVER":
                break

        if not success and total_steps >= max_steps * max_retries:
            error_msg = "Max attempts reached across all retries"
        elif not success and not error_msg:
            error_msg = f"Failed after {attempt} attempt(s)"

        # A064: Flush deferred writes at end of puzzle run
        try:
            if hasattr(self.brain, "flush_deferred_writes"):
                await self.brain.flush_deferred_writes()
        except Exception:
            logger.exception("Failed flushing deferred brain writes")

        duration = time.time() - start_time

        judge_verdict = None
        if self.outcome_judge and task.reference_solution:
            try:
                expected = json.loads(task.reference_solution)
                trajectory = self._build_trajectory_summary(orchestrator)
                archetype = getattr(getattr(orchestrator.solve_engine, "_archetype", None), "value", "unknown")
                verdict = await self.outcome_judge.evaluate(
                    observation.get("grid"), expected, trajectory, archetype
                )
                if verdict:
                    judge_verdict = asdict(verdict)
            except Exception:
                logger.exception("B181 failed")

        benchmark_metrics = {}
        if hasattr(orchestrator, "get_benchmark_metrics"):
            try:
                benchmark_metrics = orchestrator.get_benchmark_metrics()
            except Exception:
                logger.exception("B89: get_benchmark_metrics failed")

        trajectory_score = None
        try:
            trajectory_score = self.trajectory_evaluator.evaluate(
                trace=list(getattr(orchestrator, "_execution_trace", [])),
                step_history=list(getattr(orchestrator, "_step_history", [])),
            ).to_dict()
        except Exception as exc:
            logger.warning("B186: trajectory evaluation failed: %s", exc)

        failure_class = None
        if not success:
            failure_class = classify_failure(
                exc=None,
                final_state=state,
                error_message=error_msg,
                no_progress_steps=max(
                    consecutive_no_progress_steps,
                    int(getattr(orchestrator, "_consecutive_no_progress_steps", 0) or 0),
                ),
                budget_exhausted=bool(
                    getattr(orchestrator.cost_tracker, "budget_exhausted", False) is True
                ) if getattr(orchestrator, "cost_tracker", None) else False,
                wall_clock_timeout=bool(error_msg and "wall-clock" in error_msg.lower()),
                max_steps_reached=(total_steps >= max_steps * max_retries),
                loop_detected=self._effective_loop_detected(orchestrator),
                graduation_reason=str(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "graduation_reason", "")),
                coverage_saturated=bool(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "coverage_saturated", False)),
                plateau_escalation_required=bool(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "plateau_escalation_required", False)),
            ).value

        cost_usd = None
        invalid_action_count = None
        if isinstance(benchmark_metrics, dict):
            cost_usd = self._safe_float((benchmark_metrics.get("token_cost") or {}).get("cost_usd"))
            invalid_action_count = (benchmark_metrics.get("prompt_budget") or {}).get("invalid_action_count")

        sc = getattr(orchestrator, "_solve_context", None)
        task_result = ABTaskResult(
            task_id=task.task_id,
            variant=ABVariant.SIDEQUESTS,
            correct=success,
            steps=total_steps,
            tokens_input=total_tokens_in,
            tokens_output=total_tokens_out,
            error_message=error_msg,
            failure_class=failure_class,
            response_text=f"Solved: {success} in {total_steps} steps ({attempt} attempt(s))",
            attempts=attempt,
            cost_usd=cost_usd,
            invalid_action_count=invalid_action_count,
            dissonance_triggered=bool(self._solve_context_get(sc, "dissonance_detected", self._solve_context_get(sc, "dissonance", False))),
            trajectory_score=trajectory_score,
            final_state=state,
            final_observation=observation,
            judge_verdict=judge_verdict,
            terminal_value_score=float(self._solve_context_get(sc, "terminal_value_score", 0.0)),
            terminal_value_components=dict(self._solve_context_get(sc, "terminal_value_components", {})),
        )
        setattr(task_result, "bootstrap_write_trace", bootstrap_write_trace)
        setattr(task_result, "final_write_trace", final_write_trace)
        setattr(task_result, "benchmark_metrics", benchmark_metrics)
        setattr(task_result, "sidequests_ledger", list(getattr(brain_client, "ledger", []) or []))
        # A073: World model snapshot for evaluation
        if hasattr(orchestrator, "world_model"):
             setattr(task_result, "world_model_snapshot", orchestrator.world_model.to_trace_snapshot())
        # A075: Publish learned mechanics to aggregate memory
        if hasattr(orchestrator, "publish_mechanic_memory"):
            publish_result = orchestrator.publish_mechanic_memory()
            if inspect.isawaitable(publish_result):
                await publish_result
            
        return task_result, duration, orchestrator

    def _build_trajectory_summary(self, orchestrator: ARCOrchestrator) -> str:
        lines = [
            f"Step {i + 1}: {s.get('action_id')} - {s.get('rationale')} (reward: {s.get('reward', 0.0)})"
            for i, s in enumerate(getattr(orchestrator, "_step_history", []))
        ]
        if getattr(orchestrator.solve_engine, "_victory_condition", None):
            lines.append(f"Inferred Objective: {orchestrator.solve_engine._victory_condition.description}")
        return "\n".join(lines)

    @staticmethod
    def _phase_question_for(phase_name: str | None) -> str | None:
        mapping = {
            SolvePhase.PERCEIVE.value: "What am I seeing in the puzzle right now?",
            SolvePhase.MODEL.value: "What world model or structure explains this board?",
            SolvePhase.HYPOTHESIZE.value: "What kind of puzzle is this and what is the likely win condition?",
            SolvePhase.ROUTE.value: "What strategy or chunk should I follow next?",
            SolvePhase.EXECUTE.value: "What exact action should I take now?",
            SolvePhase.EVALUATE.value: "What changed, and did that action help?",
            SolvePhase.REPLAN.value: "Why am I stuck, and which earlier phase should I return to?",
        }
        return mapping.get(str(phase_name or "").lower())

    def _phase_answer_for(self, orchestrator: ARCOrchestrator, phase_name: str | None) -> str | None:
        solve_ctx = getattr(orchestrator, "_solve_context", None)
        hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}
        last_step = (getattr(orchestrator, "_step_history", []) or [{}])[-1] or {}
        active_chunk = self._solve_context_get(solve_ctx, "active_chunk") or {}
        phase = str(phase_name or "").lower()

        if phase == SolvePhase.PERCEIVE.value:
            perception = getattr(orchestrator, "_last_response_perception", None)
            if perception and (isinstance(perception, dict) and int(perception.get("step", 0) or 0) > 0):
                delta = perception.get("delta", {}) or {}
                n_changed = int(delta.get("n_cells_changed", 0) or 0)
                effect = delta.get("apparent_effect")
                direction = delta.get("direction")
                actions_list = perception.get("available_actions") or []
                if isinstance(actions_list, list):
                    actions = ", ".join(str(a) for a in actions_list)
                else:
                    actions = str(actions_list)
                delta_str = f"{n_changed} cells changed"
                if effect:
                    delta_str += f", {effect}"
                if direction:
                    delta_str += f", direction={direction}"
                base = (
                    f"State={perception.get('state')}, reward={perception.get('reward')}, "
                    f"done={perception.get('done')}. Grid: {delta_str}. "
                    f"Actions: {actions or 'pending'}."
                )
                # Prefer the contextual question stored on the perception when available
                pq = perception.get("phase_question") if isinstance(perception, dict) else None
                if pq:
                    return f"{pq} — {base}"
                return base
            return "Initial observation captured and memory retrieval seeded."
        if phase == SolvePhase.MODEL.value:
            return self._solve_context_get(solve_ctx, "strategy_summary") or "Building a structural model from the latest observation."
        if phase == SolvePhase.HYPOTHESIZE.value:
            archetype = self._solve_context_get(solve_ctx, "archetype") or "unknown"
            victory = self._solve_context_get(solve_ctx, "victory_condition") or {}
            victory_type = victory.get("type") if isinstance(victory, dict) else victory or "unknown"
            return f"Archetype={archetype}; victory_condition={victory_type}."
        if phase == SolvePhase.ROUTE.value:
            return active_chunk.get("description") if isinstance(active_chunk, dict) else getattr(active_chunk, "description", None) or self._solve_context_get(solve_ctx, "strategy_summary") or "Selecting the next strategy chunk."
        if phase == SolvePhase.EXECUTE.value:
            return last_step.get("rationale") or (active_chunk.get("description") if isinstance(active_chunk, dict) else getattr(active_chunk, "description", None)) or "Executing the chosen action."
        if phase == SolvePhase.EVALUATE.value:
            state_after = last_step.get("state_after") or "unknown"
            reward = last_step.get("reward")
            return f"Observed state={state_after}, reward={reward}."
        if phase == SolvePhase.REPLAN.value:
            reason = self._solve_context_get(solve_ctx, "dissonance_reason") or hyp_ctx.get("dissonance_reason") or "No progress / loop detected."
            return str(reason)
        return self._solve_context_get(solve_ctx, "strategy_summary") or last_step.get("rationale")

    def _record_phase_transition(
        self,
        *,
        task: ABTask,
        orchestrator: ARCOrchestrator,
        from_phase: str | None,
        to_phase: str | None,
        step: int,
        start_time: float,
        metadata: dict | None = None,
    ) -> None:
        """Emit a dedicated phase transition record for live output and timeline export."""
        if not from_phase or not to_phase or from_phase == to_phase:
            return

        phase_question = self._phase_question_for(to_phase)
        phase_answer = self._phase_answer_for(orchestrator, to_phase)
        details = {
            "step": step,
            "phase": to_phase,
            "from_phase": from_phase,
            "to_phase": to_phase,
            "phase_question": phase_question,
            "phase_answer": phase_answer,
        }
        if metadata:
            details.update(metadata)

        try:
            if hasattr(orchestrator, "_emit_trace_event"):
                orchestrator._emit_trace_event(
                    "phase_transition",
                    "phase_transition",
                    details,
                    {"current_phase": to_phase},
                )
        except Exception:
            logger.debug("Unable to emit phase transition trace", exc_info=True)

        if self._progress_callback is not None and getattr(self, "_emit_transition_snapshots", False):
            snapshot = {
                "snapshot_type": "phase_transition",
                "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "game_id": getattr(task, "game_id", "unknown"),
                "task_id": task.task_id,
                "step": step,
                "runtime_seconds": round(time.time() - start_time, 2),
                "from_phase": from_phase,
                "to_phase": to_phase,
                "current_phase": to_phase,
                "phase_question": phase_question,
                "phase_answer": phase_answer,
                "solve_phase_summary": self._build_phase_summary(orchestrator),
            }
            if metadata:
                snapshot["metadata"] = metadata
            self._progress_callback(snapshot)

    def _build_phase_summary(self, orchestrator: ARCOrchestrator) -> dict:
        """Return a compact, user-visible summary of the durable phase state."""
        solve_ctx = getattr(orchestrator, "_solve_context", None)
        phase_ctrl = getattr(orchestrator, "_phase_controller", None)
        phase_name = None
        history = []
        phase_step_count = 0
        replan_count = 0

        if phase_ctrl is not None:
            phase_name = getattr(phase_ctrl, "phase_name", None)
            history = list(getattr(phase_ctrl, "history", []) or [])
            phase_step_count = int(getattr(phase_ctrl, "step_count", 0) or 0)
            replan_count = sum(
                1 for item in history
                if isinstance(item, dict) and item.get("to") == SolvePhase.REPLAN.value
            )

        active_chunk = self._solve_context_get(solve_ctx, "active_chunk") or {}
        victory = self._solve_context_get(solve_ctx, "victory_condition")
        return {
            "current_phase": phase_name,
            "phase_question": self._phase_question_for(phase_name),
            "phase_answer": self._phase_answer_for(orchestrator, phase_name),
            "last_transition": history[-1] if history else None,
            "phase_step_count": phase_step_count,
            "replan_count": replan_count,
            "phase_history_tail": history[-8:],
            "archetype": str(self._solve_context_get(solve_ctx, "archetype", "unknown")),
            "archetype_confidence": self._solve_context_get(solve_ctx, "archetype_confidence"),
            "victory_condition": str(victory.get("type") if isinstance(victory, dict) else getattr(victory, "condition_type", "unknown")),
            "victory_confidence": victory.get("confidence") if isinstance(victory, dict) else getattr(victory, "confidence", 0.0),
            "memory_degraded": (getattr(self.brain, "memory_degraded", False) is True),
            "memory_degraded_reason": str(getattr(self.brain, "memory_degraded_reason", "") or ""),
            "strategy_summary": self._solve_context_get(solve_ctx, "strategy_summary"),
            "active_chunk": {
                "description": active_chunk.get("description") if isinstance(active_chunk, dict) else getattr(active_chunk, "description", None),
                "source": active_chunk.get("source") if isinstance(active_chunk, dict) else getattr(active_chunk, "source", None),
                "estimated_actions": active_chunk.get("estimated_actions", []) if isinstance(active_chunk, dict) else getattr(active_chunk, "estimated_actions", []),
                "plan_id": active_chunk.get("plan_id") if isinstance(active_chunk, dict) else getattr(active_chunk, "plan_id", None),
            } if active_chunk else None,
        }

    def _should_replan(self, orchestrator: ARCOrchestrator, no_progress_steps: int) -> bool:
        """Decide whether to enter REPLAN based on loop signals or no-progress counters."""
        try:
            current_step = len(getattr(orchestrator, "_step_history", []) or [])
            backoff = int(getattr(self, "_replan_backoff_steps", 3) or 3)
            min_interval = 5
            since_last_replan = current_step - int(getattr(self, "_last_replan_step", -999) or -999)
            declining_progress = self._is_progress_reward_declining(orchestrator)
            if since_last_replan < min_interval and not declining_progress:
                return False

            hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}
            loop_detected = bool(hyp_ctx.get("loop_detected"))
            if loop_detected:
                return self._effective_loop_detected(orchestrator)
            if int(no_progress_steps or 0) >= backoff:
                return True
            if int(getattr(orchestrator, "_consecutive_no_progress_steps", 0) or 0) >= backoff:
                return True
            if declining_progress and since_last_replan >= backoff:
                return True
        except Exception:
            pass
        return False

    def _effective_loop_detected(self, orchestrator: ARCOrchestrator | None) -> bool:
        """Suppress raw state-loop signals while graph evidence says exploration is live."""
        if orchestrator is None:
            return False
        hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}
        if not bool(hyp_ctx.get("loop_detected")):
            return False
        if self._loop_has_live_graph_escape(orchestrator):
            try:
                orchestrator._emit_trace_event(
                    "operation",
                    "loop_detection_suppressed",
                    {"loop_hash": hyp_ctx.get("loop_hash")},
                    {"reason": "positive_or_unexhausted_graph_evidence"},
                )
            except Exception:
                pass
            return False
        return True

    @staticmethod
    def _loop_has_live_graph_escape(orchestrator: ARCOrchestrator) -> bool:
        history = [
            row for row in (getattr(orchestrator, "_step_history", []) or [])
            if isinstance(row, dict)
        ]
        if not history:
            return False
        last = history[-1]
        if last.get("click_supported") is True:
            return True
        compiled = last.get("compiled_world_delta") or {}
        if compiled.get("effect_class") in {"distance_improving_move", "terminal_progress", "object_progress", "meaningful_progress"}:
            return True
        try:
            if float(compiled.get("goal_distance_delta", 0.0) or 0.0) < 0:
                return True
        except Exception:
            pass
        gating = last.get("reasoning_gating") or {}
        route_evidence = gating.get("route_transition_evidence") or last.get("route_transition_evidence") or {}
        if isinstance(route_evidence, dict):
            if route_evidence.get("improving_transition_count", 0):
                return True
            try:
                if route_evidence.get("has_route_evidence") and float(route_evidence.get("best_distance_delta", 0.0) or 0.0) < 0:
                    return True
            except Exception:
                pass

        available_actions = list(last.get("available_actions") or [])
        hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}
        action_coverage = hyp_ctx.get("action_coverage") or {}
        if action_coverage.get("untested_actions"):
            return True

        if "ACTION6" in available_actions:
            tried = {
                str(row.get("action_identity"))
                for row in history
                if row.get("action_identity")
            }
            try:
                candidates = orchestrator.world_model.get_click_candidates(limit=16)
            except Exception:
                candidates = []
            for candidate in candidates:
                x = candidate.get("x")
                y = candidate.get("y")
                if x is None or y is None:
                    continue
                if f"ACTION6@{int(x)},{int(y)}" not in tried:
                    return True
        return False

    def _is_progress_reward_declining(self, orchestrator: ARCOrchestrator) -> bool:
        """Detect short-horizon degradation in shaped reward to allow faster replans."""
        history = list(getattr(orchestrator, "_step_history", []) or [])
        if len(history) < 8:
            return False
        vals: list[float] = []
        for step in history[-8:]:
            val = step.get("progress_reward")
            if val is None:
                val = step.get("reward")
            try:
                vals.append(float(val or 0.0))
            except (TypeError, ValueError):
                vals.append(0.0)
        if len(vals) < 8:
            return False
        head = sum(vals[:4]) / 4.0
        tail = sum(vals[4:]) / 4.0
        return (head - tail) >= 0.02 and tail < 0.03

    def _replan_target(self, orchestrator: ARCOrchestrator) -> tuple[SolvePhase, str]:
        """Choose (target_phase, route_reason) after REPLAN.

        Evidence-aware decision tree (A017, restoring A011 runner-side).
        First match wins.
        """
        try:
            solve_ctx = getattr(orchestrator, "_solve_context", {}) or {}
            hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}

            # --- Evidence predicates (computed once up front) ---
            action_facts = hyp_ctx.get("action_facts") or []
            action_coverage = hyp_ctx.get("action_coverage") or {}
            tested_count = int(action_coverage.get("tested_count") or 0)
            available_total = int(action_coverage.get("available_total") or 0)
            untested_count = int(action_coverage.get("untested_count") or 0)
            exploration_complete = bool(action_coverage.get("initial_exploration_complete"))

            det_effects = [
                f for f in action_facts
                if str(f.get("fact_type") or "").lower() == "deterministic_effect"
            ]
            all_actions_low_value = (
                len(det_effects) > 0
                and tested_count >= available_total
                and available_total > 0
                and all(
                    str(f.get("value_status") or "").lower() == "low_value"
                    for f in det_effects
                )
            )

            roles = solve_ctx.get("object_roles") or {}
            # roles cid keys are usually ints in sc
            player_cid = next((cid for cid, r in roles.items() if getattr(r, 'role', None) and hasattr(r.role, 'value') and r.role.value == "player"), None)
            goal_cid = next((cid for cid, r in roles.items() if getattr(r, 'role', None) and hasattr(r.role, 'value') and r.role.value == "goal"), None)
            
            player_role = roles.get(player_cid)
            goal_role = roles.get(goal_cid)
            
            player_conf = player_role.confidence if player_role else 0.0
            goal_conf = goal_role.confidence if goal_role else 0.0
            # A017 uses 0.6 threshold aligned with PlanChunker directional bar
            geometry_high_conf = player_conf >= 0.6 and goal_conf >= 0.6

            coverage_saturated = exploration_complete and untested_count == 0

            arch_conf = float(
                getattr(
                    getattr(orchestrator, "solve_engine", None),
                    "_archetype_confidence",
                    0.0,
                )
                or 0.0
            )

            # --- Signature (retains B218 escalation semantics) ---
            signature = {
                "active_chunk_source": (solve_ctx.get("active_chunk") or {}).get("source"),
                "plateau_locked_family": solve_ctx.get("plateau_locked_family"),
                "archetype": solve_ctx.get("archetype"),
                "victory_condition_type": (
                    (solve_ctx.get("victory_condition") or {}).get("type")
                    if isinstance(solve_ctx.get("victory_condition"), dict)
                    else solve_ctx.get("victory_condition")
                ),
            }
            signature_repeated = self._last_replan_signature == signature
            self._last_replan_signature = signature

            # --- Decision tree (first match wins) ---
            if all_actions_low_value and geometry_high_conf:
                return SolvePhase.MODEL, "low_value_but_known_geometry"
            if signature_repeated:
                if hasattr(orchestrator, "_emit_trace_event"):
                    orchestrator._emit_trace_event(
                        "replan_escalation", "escalate", {"signature": signature}
                    )
                return SolvePhase.MODEL, "signature_escalation"
            if not exploration_complete:
                return SolvePhase.MODEL, "exploration_incomplete"
            if arch_conf < 0.3:
                return SolvePhase.HYPOTHESIZE, "low_archetype_conf"
            if coverage_saturated:
                return SolvePhase.ROUTE, "rebuild_route_from_saturation"
        except Exception:
            logger.exception("_replan_target evaluation failed; falling back to ROUTE")
        return SolvePhase.ROUTE, "default"

    def _summarize_strategy(self, orchestrator: ARCOrchestrator) -> str:
        try:
            solve_ctx = getattr(orchestrator, "_solve_context", {}) or {}
            strategy_summary = solve_ctx.get("strategy_summary")
            if strategy_summary:
                return strategy_summary if isinstance(strategy_summary, str) else json.dumps(strategy_summary)
            active_chunk = solve_ctx.get("active_chunk") or {}
            parts = []
            if active_chunk.get("description"):
                parts.append(active_chunk.get("description"))
            if active_chunk.get("plan_id"):
                parts.append(f"plan:{active_chunk.get('plan_id')}")
            return " | ".join(parts) if parts else "No strategy summary"
        except Exception:
            logger.exception("Failed to summarize strategy")
            return "No strategy summary"

    @staticmethod
    def _validate_lesson_upsert_result(payload: Any) -> tuple[bool, str]:
        if not isinstance(payload, dict):
            return False, "non_dict_payload"
        lesson_id = payload.get("lesson_id") or payload.get("id")
        if lesson_id not in (None, "", "None"):
            return True, "ok"
        if payload.get("status") == "queued_offline":
            return False, "queued_offline"
        if payload.get("status") == "error" or payload.get("error"):
            return False, "tool_error"
        return False, "missing_lesson_id"

    def _build_structured_post_solve_lessons(
        self,
        *,
        orchestrator: ARCOrchestrator,
        task: ABTask,
        task_result: ABTaskResult,
        archetype: str,
    ) -> list[dict[str, Any]]:
        """Synthesize durable graph-friendly lessons from a completed puzzle run."""
        final_obs = getattr(task_result, "final_observation", None)
        run_payload = orchestrator._extract_run_lessons(bool(getattr(task_result, "correct", False)), final_obs)
        valence = 1.0 if getattr(task_result, "correct", False) else 0.0
        tags_common = [
            "arc",
            f"archetype:{archetype}",
            f"outcome:{run_payload.get('outcome', 'failed')}",
            f"task:{task.task_id}",
        ]

        lessons: list[dict[str, Any]] = [
            {
                "domain": str(archetype),
                "text": json.dumps(
                    {
                        "lesson_type": "run_summary",
                        "task_id": task.task_id,
                        "game_id": run_payload.get("game_id"),
                        "archetype": archetype,
                        "outcome": run_payload.get("outcome"),
                        "steps_used": run_payload.get("steps_used"),
                        "victory_condition": run_payload.get("victory_condition"),
                        "puzzle_fingerprint": run_payload.get("puzzle_fingerprint", {}),
                    }
                ),
                "valence": valence,
                "confidence": 0.8,
                "tags": tags_common + ["lesson_type:run_summary"],
            }
        ]

        action_effects = run_payload.get("action_effects", {}) or {}
        ranked_actions = sorted(
            action_effects.items(),
            key=lambda item: (
                float((item[1] or {}).get("reward", 0.0) or 0.0),
                float((item[1] or {}).get("pixels_changed", 0.0) or 0.0),
            ),
            reverse=True,
        )[:3]
        for action_id, stats in ranked_actions:
            lessons.append(
                {
                    "domain": "action_effect",
                    "text": json.dumps(
                        {
                            "lesson_type": "action_outcome",
                            "task_id": task.task_id,
                            "archetype": archetype,
                            "action_id": action_id,
                            "reward": float((stats or {}).get("reward", 0.0) or 0.0),
                            "pixels_changed": float((stats or {}).get("pixels_changed", 0.0) or 0.0),
                            "times_seen": int((stats or {}).get("times_seen", 0) or 0),
                            "label": str((stats or {}).get("label", "unknown")),
                        }
                    ),
                    "valence": float((stats or {}).get("reward", valence) or 0.0),
                    "confidence": 0.75,
                    "tags": tags_common + ["lesson_type:action_outcome", f"action:{action_id}"],
                }
            )
        return lessons

    async def _probe_graph_health(self, *, orchestrator: ARCOrchestrator, task: ABTask) -> dict[str, Any]:
        """Run a once-per-puzzle memory health probe and emit counts."""
        summary = {
            "task_id": task.task_id,
            "lessons_count": 0,
            "procedures_count": 0,
            "plans_count": 0,
            "memory_degraded": (getattr(self.brain, "memory_degraded", False) is True),
            "memory_degraded_reason": str(getattr(self.brain, "memory_degraded_reason", "") or ""),
        }
        try:
            lessons_resp = await self.brain.recall_relevant_lessons(
                query="domain:arc kind:memory_recall",
                limit=200,
            )
            summary["lessons_count"] = len((lessons_resp or {}).get("lessons", []) or [])
        except Exception:
            logger.exception("Graph health probe: recall_relevant_lessons failed")
        try:
            proc_resp = await self.brain.recall_procedures(archetype="unknown", limit=200)
            summary["procedures_count"] = len((proc_resp or {}).get("procedures", []) or [])
        except Exception:
            logger.exception("Graph health probe: recall_procedures failed")
        try:
            plans_resp = await self.brain.recall_plans(
                goal_query="domain:arc kind:plan_recall archetype:unknown",
                session_id=orchestrator.session_id,
                min_valence=0.0,
                limit=200,
            )
            summary["plans_count"] = len((plans_resp or {}).get("plans", []) or [])
        except Exception:
            logger.exception("Graph health probe: recall_plans failed")

        summary["memory_degraded"] = (getattr(self.brain, "memory_degraded", False) is True)
        summary["memory_degraded_reason"] = str(getattr(self.brain, "memory_degraded_reason", "") or "")
        logger.info(
            "Graph health probe task=%s lessons=%s procedures=%s plans=%s memory_degraded=%s reason=%s",
            task.task_id,
            summary["lessons_count"],
            summary["procedures_count"],
            summary["plans_count"],
            summary["memory_degraded"],
            summary["memory_degraded_reason"] or "n/a",
        )
        try:
            orchestrator._emit_trace_event("operation", "graph_health_probe", {"task_id": task.task_id}, summary)
        except Exception:
            pass
        return summary

    async def _seed_bootstrap_lessons_if_empty(self, *, task: ABTask, graph_health: Mapping[str, Any]) -> None:
        """Seed minimal archetype priors when the graph is empty and backend is healthy."""
        if bool(graph_health.get("memory_degraded")):
            return
        if int(graph_health.get("lessons_count") or 0) > 0:
            return
        for archetype, priors in _ARCHETYPE_BOOTSTRAP_PRIORS.items():
            for idx, text in enumerate(priors, start=1):
                payload = {
                    "lesson_type": "bootstrap_prior",
                    "archetype": archetype,
                    "ordinal": idx,
                    "text": text,
                }
                tags = [
                    "bootstrap_prior",
                    f"archetype:{archetype}",
                    "domain:arc",
                    f"task:{task.task_id}",
                ]
                try:
                    await self.brain.upsert_lesson(
                        domain=archetype,
                        text=json.dumps(payload),
                        valence=0.3,
                        confidence=0.7,
                        tags=tags,
                    )
                except Exception:
                    logger.exception("Failed seeding bootstrap prior archetype=%s idx=%s", archetype, idx)

    async def _report_puzzle_outcome(self, *, orchestrator: ARCOrchestrator, task: ABTask, task_result: ABTaskResult, session_id: str) -> None:
        try:
            solve_ctx = getattr(orchestrator, "_solve_context", {}) or {}
            archetype_obj = getattr(getattr(orchestrator, "solve_engine", None), "_archetype", None)
            archetype = getattr(archetype_obj, "value", None) or solve_ctx.get("archetype") or "unknown"
            archetype_confidence = float(solve_ctx.get("archetype_confidence") or 0.7)

            outcome = {
                "task_id": task.task_id,
                "archetype": archetype,
                "archetype_confidence": archetype_confidence,
                "steps_taken": int(getattr(task_result, "steps", 0) or 0),
                "strategy_summary": self._summarize_strategy(orchestrator),
                "failure_class": getattr(task_result, "failure_class", None),
                "judge_verdict": getattr(task_result, "judge_verdict", None),
            }

            outcome_text = json.dumps(outcome, default=str)
            valence = 1.0 if getattr(task_result, "correct", False) else 0.0
            plan_id = getattr(orchestrator, "_plan_id", None)

            # Record structured outcome
            try:
                report_kwargs = {
                    "plan_id": plan_id,
                    "outcome": None,
                    "outcome_text": outcome_text,
                    "valence": valence,
                    "session_id": session_id,
                    "evidence": {"task_id": task.task_id},
                    "valence_source": "runner",
                }

                # If a procedure was applied, include procedure metadata so the DB can update stats
                proc_id = None
                proc_success = None
                try:
                    se = getattr(orchestrator, 'solve_engine', None)
                    if se is None:
                        se = getattr(orchestrator, '_solve_engine', None)
                    proc_id = getattr(se, '_applied_procedure_id', None) or getattr(se, '_using_procedure_id', None)
                    proc_failed = getattr(se, '_procedure_failed', None)
                    if proc_id:
                        report_kwargs['procedure_id'] = proc_id
                        # success = True only if puzzle solved and procedure didn't fail earlier
                        proc_success = bool(getattr(task_result, 'correct', False) and not bool(proc_failed))
                        report_kwargs['procedure_success'] = proc_success
                except Exception:
                    pass

                await self.brain.report_outcome(**report_kwargs)
            except Exception:
                logger.exception("Failed to report outcome via brain.report_outcome")

            # Persist a lesson summarizing the run (domain = archetype)
            lesson_text = f"ARC puzzle {task.task_id} outcome: {outcome_text}"
            tags = [str(archetype), ("success" if valence >= 1.0 else "failure"), f"steps_{outcome['steps_taken']}"]
            try:
                upsert_payload = await self.brain.upsert_lesson(
                    domain=str(archetype),
                    text=lesson_text,
                    valence=valence,
                    confidence=archetype_confidence,
                    tags=tags,
                )
                upsert_ok, upsert_reason = self._validate_lesson_upsert_result(upsert_payload)
                if not upsert_ok:
                    logger.warning("B214: outcome lesson write missing lesson_id (reason=%s task=%s)", upsert_reason, task.task_id)
            except Exception:
                logger.exception("Failed to upsert lesson via brain.upsert_lesson")

            for lesson in self._build_structured_post_solve_lessons(
                orchestrator=orchestrator,
                task=task,
                task_result=task_result,
                archetype=str(archetype),
            ):
                try:
                    upsert_payload = await self.brain.upsert_lesson(**lesson)
                    upsert_ok, upsert_reason = self._validate_lesson_upsert_result(upsert_payload)
                    if not upsert_ok:
                        logger.warning(
                            "B214: structured lesson write missing lesson_id (reason=%s task=%s tags=%s)",
                            upsert_reason,
                            task.task_id,
                            lesson.get("tags"),
                        )
                except Exception:
                    logger.exception("Failed to persist structured post-solve lesson")
        except Exception:
            logger.exception("_report_puzzle_outcome failed")

    def _emit_progress_snapshot(
        self,
        task: ABTask,
        orchestrator: ARCOrchestrator,
        observation: Mapping[str, Any],
        total_steps: int,
        reward: float,
        done: bool,
        start_time: float,
    ) -> None:
        if self._progress_callback is None:
            return

        last_step = orchestrator._step_history[-1] if getattr(orchestrator, "_step_history", None) else {}
        phase_summary = self._build_phase_summary(orchestrator)
        planner_selection = getattr(orchestrator, "_last_planner_selection", None) or (
            orchestrator._solve_context_get(getattr(orchestrator, "_solve_context", {}), "planner_selection")
            if getattr(orchestrator, "_solve_context", None)
            else None
        )
        selected_candidate = getattr(planner_selection, "selected", None)
        selected_prediction = getattr(selected_candidate, "predicted_observation", None)
        if not isinstance(selected_prediction, dict):
            selected_prediction = None
        prior_compatibility = float(getattr(selected_candidate, "prior_compatibility_score", 0.0) or 0.0)
        route_actions = list(getattr(selected_candidate, "route_actions", []) or [])
        route_confidence = float(getattr(selected_candidate, "route_confidence", 0.0) or 0.0)
        phase_memory_degraded = bool(phase_summary.get("memory_degraded", False)) if isinstance(phase_summary, dict) else False
        brain = getattr(self, "brain", None)
        brain_inner = getattr(brain, "inner", None)
        brain_memory_degraded = bool(
            getattr(brain, "memory_degraded", False) is True
            or (getattr(brain_inner, "memory_degraded", False) is True if brain_inner is not None else False)
        )
        memory_degraded_reason = str(
            getattr(brain, "memory_degraded_reason", "")
            or (getattr(brain_inner, "memory_degraded_reason", "") if brain_inner is not None else "")
            or (phase_summary.get("memory_degraded_reason", "") if isinstance(phase_summary, dict) else "")
            or ""
        )
        snapshot = {
            "snapshot_type": "step",
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "game_id": getattr(task, "game_id", "unknown"),
            "task_id": task.task_id,
            "step": total_steps,
            "runtime_seconds": round(time.time() - start_time, 2),
            "state_after": observation.get("state", "NOT_FINISHED"),
            "reward": reward,
            "env_reward": last_step.get("env_reward"),
            "progress_reward": last_step.get("progress_reward", reward),
            "reward_components": last_step.get("reward_components"),
            "env_signals": last_step.get("env_signals"),
            "done": done,
            "action_id": last_step.get("action_id"),
            "x": last_step.get("x"),
            "y": last_step.get("y"),
            "action_identity": last_step.get("action_identity"),
            "coordinate_required": last_step.get("coordinate_required"),
            "missing_coordinate_click": last_step.get("missing_coordinate_click"),
            "rationale": last_step.get("rationale"),
            "guard_status": last_step.get("guard_status"),
            "thinking_trace": last_step.get("thinking_trace", []),
            "decision_source": (
                last_step.get("decision_source")
                or (last_step.get("decision_flow") or {}).get("decision_source")
            ),
            "frame_hash": observation.get("frame_hash"),
            "available_actions": observation.get("available_actions", []),
            "solve_phase_summary": phase_summary,
            "sidequests_ledger_count": len(self._ledger),
            # A073: World Model Graph telemetry
            "world_model_node_count": orchestrator.world_model.to_trace_snapshot()["node_count"],
            "world_model_edge_count": orchestrator.world_model.to_trace_snapshot()["edge_count"],
            "world_model_contradiction_count": orchestrator.world_model.to_trace_snapshot().get("contradiction_count", 0),
            "world_model_demotion_count": orchestrator.world_model.to_trace_snapshot().get("demotion_count", 0),
            "world_model_summary": orchestrator.world_model.to_prompt_summary(max_chars=1000),

            # A076/A080: Reasoning Controller telemetry
            "reasoning_skip_count": orchestrator.reasoning_controller.skip_count,
            "reasoning_escalation_count": orchestrator.reasoning_controller.escalation_count,
            "llm_reason_count": orchestrator.reasoning_controller.reason_count,

            # A077/A080: Planner telemetry
            "planner_candidate_count": getattr(planner_selection, "candidate_count", 0),
            "planner_selected_has_prediction": getattr(planner_selection, "selected_has_prediction", False),
            "selected_prediction": selected_prediction,
            "planner_selected_prediction": selected_prediction,
            "planner_selected_prediction_effect_class": (
                selected_prediction.get("effect_class") if selected_prediction else None
            ),
            "planner_selected_prediction_confidence": (
                float(selected_prediction.get("confidence", 0.0) or 0.0) if selected_prediction else 0.0
            ),
            "planner_selected_has_falsification": getattr(planner_selection, "selected_has_falsification", False),
            "mechanic_priors_used_count": getattr(planner_selection, "mechanic_priors_used", 0),
            "mechanic_prior_recall_status": getattr(orchestrator, "_mechanic_prior_recall_status", "not_called"),
            "mechanic_prior_count": int(getattr(orchestrator, "_mechanic_prior_count", 0) or 0),
            "mechanic_prior_error_code": getattr(orchestrator, "_mechanic_prior_error_code", None),
            "planner_selected_prior_id": getattr(selected_candidate, "mechanic_prior_id", None),
            "planner_selected_prior_source": getattr(selected_candidate, "mechanic_prior_source", "none"),
            "planner_selected_prior_compatibility": prior_compatibility,
            "mechanic_prior_compatibility_score": prior_compatibility,
            "route_candidate_count": 1 if route_actions else 0,
            "route_actions": route_actions,
            "route_confidence": route_confidence,
            "route_transition_evidence": (
                (last_step.get("reasoning_gating") or {}).get("route_transition_evidence")
                if isinstance(last_step.get("reasoning_gating"), dict)
                else None
            ),
            "memory_degraded": bool(brain_memory_degraded or phase_memory_degraded),
            "memory_degraded_reason": memory_degraded_reason,
            "mcp_http_timeout_count": int(
                getattr(brain, "mcp_http_timeout_count", 0)
                or (getattr(brain_inner, "mcp_http_timeout_count", 0) if brain_inner is not None else 0)
                or 0
            ),

            # A072: Robustly access solve context fields            "terminal_value_score": float(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "terminal_value_score", 0.0)),            "terminal_value_components": dict(self._solve_context_get(getattr(orchestrator, "_solve_context", None), "terminal_value_components", {})),
        }
        for key in (
            "decision_source",
            "macro_id",
            "macro_reason",
            "macro_step_index",
            "macro_stop_reason",
            "args_effective",
            "coordinate_relevance",
            "object_progress",
            "meaningful_progress",
            "progress_class",
            "progress_gate_reason",
            "terminal_progress_trend",
            "terminal_goal_distance",
            "compiled_world_delta",
            "world_model_failure_signal",
            "reasoning_gating",
            "selected_prediction",
            "planner_selected_prediction",
            "planner_selected_prediction_effect_class",
            "planner_selected_prediction_confidence",
            "planner_candidate_count",
            "planner_selected_prior_id",
            "planner_selected_prior_source",
            "planner_selected_prior_compatibility",
            "mechanic_prior_compatibility_score",
            "evidence_path",
            "cheap_probe_reason",
            "bypassed_llm",
            "route_transition_evidence",
            "route_candidate_count",
            "route_actions",
            "route_confidence",
            "active_goal_hypothesis_id",
            "active_goal_type",
            "active_goal_confidence",
            "active_goal_evidence_count",
            "mechanic_graph_object_count",
            "mechanic_graph_relation_count",
            "mechanic_graph_configuration_hash",
            "graph_transform_class",
            "configuration_hash_after_action",
            "graph_transform_goal_relevance",
            "affected_mechanic_objects_count",
            "configuration_hash_current",
            "click_candidate_count",
            "top_click_candidate_ids",
            "click_candidate_id",
            "selected_click_candidate_id",
            "click_candidate_role",
            "selected_click_candidate_role",
            "click_candidate_rank",
            "selected_click_candidate_rank",
            "clicked_x",
            "clicked_y",
            "clicked_color",
            "clicked_panel_id",
            "frame_delta",
            "config_delta",
            "click_supported",
            "click_falsified",
            "click_summary",
            "click_failure_message",
        ):
            if key in last_step:
                snapshot[key] = last_step.get(key)
        self._progress_callback(snapshot)

    @staticmethod
    def _macro_prediction_falsified(step: Mapping[str, Any], selected_candidate: Any = None) -> bool:
        """Return true when a macro step contradicts the selected planner prediction."""
        compiled = step.get("compiled_world_delta") or {}
        actual = compiled.get("effect_class")
        if actual in ("object_progress", "meaningful_progress") and compiled.get("terminal_alignment") in ("local_only", "regressing", "oscillating"):
            actual = "local_object_progress"
        prediction = step.get("selected_prediction") or step.get("planner_selected_prediction") or {}
        if not prediction and selected_candidate is not None:
            prediction = getattr(selected_candidate, "predicted_observation", {}) or {}
        predicted = prediction.get("effect_class") if isinstance(prediction, Mapping) else None
        if not predicted or not actual or actual == "unknown":
            return False
        if predicted == actual:
            return False
        progress_effects = {"object_progress", "terminal_progress", "meaningful_progress"}
        if predicted in progress_effects and actual not in progress_effects:
            return True
        if predicted == "pixel_churn" and actual == "harmful":
            return True
        if predicted == "harmful" and actual != "harmful":
            return True
        return False

    def _emit_world_model_decision_snapshot(
        self,
        task: ABTask,
        orchestrator: ARCOrchestrator,
        observation: Mapping[str, Any],
        executed_step_count: int,
        start_time: float,
    ) -> None:
        if self._progress_callback is None:
            return
        decision_snapshot = dict(orchestrator.build_reasoning_decision_snapshot(dict(observation), executed_step_count))
        decision_snapshot.setdefault("timestamp_iso", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        decision_snapshot.setdefault("game_id", getattr(task, "game_id", "unknown"))
        decision_snapshot.setdefault("task_id", task.task_id)
        decision_snapshot.setdefault("runtime_seconds", round(time.time() - start_time, 2))
        decision_snapshot.setdefault("mechanic_prior_recall_status", getattr(orchestrator, "_mechanic_prior_recall_status", "not_called"))
        decision_snapshot.setdefault("mechanic_prior_count", int(getattr(orchestrator, "_mechanic_prior_count", 0) or 0))
        decision_snapshot.setdefault("mechanic_prior_error_code", getattr(orchestrator, "_mechanic_prior_error_code", None))
        self._progress_callback(decision_snapshot)

    def _append_macro_step_record(
        self,
        *,
        orchestrator: ARCOrchestrator,
        observation: Mapping[str, Any],
        action: Mapping[str, Any],
        total_steps: int,
        available_actions: Sequence[str],
    ) -> None:
        """Record a macro action before result ingestion so deltas attach to it."""
        board_before = {}
        snapshot = getattr(orchestrator, "_snapshot_for_trace", None)
        if callable(snapshot):
            try:
                board_before = snapshot(observation)
            except Exception:
                board_before = {}
        orchestrator._step_history.append({
            "step": total_steps,
            "state_before": observation.get("state"),
            "board_before": board_before,
            "solve_context": dict(orchestrator._solve_context) if isinstance(getattr(orchestrator, "_solve_context", None), dict) else None,
            "available_actions": list(available_actions or []),
            "prompt": None,
            "decision_flow": {
                "proposed_by": "macro_executor",
                "executed_by": "macro_executor",
                "candidate_action": action.get("action_id"),
                "executed_action": action.get("action_id"),
                "decision_source": "macro_executor",
                "override_reason": action.get("macro_reason"),
                "memory_prior_source": "none",
                "guard_status": "approved",
            },
            "action_id": action.get("action_id"),
            "candidate_action_id": action.get("action_id"),
            "decision_source": "macro_executor",
            "override_reason": action.get("macro_reason"),
            "memory_prior_source": "none",
            "x": action.get("x"),
            "y": action.get("y"),
            "rationale": action.get("rationale"),
            "thinking_trace": [],
            "guard_status": "approved",
            "verifier_status": "not_run",
            "macro_id": action.get("macro_id"),
            "macro_step_index": action.get("macro_step_index"),
            "macro_reason": action.get("macro_reason"),
            "reward": None,
            "done": False,
            "prompt_tokens": 0,
        })

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    async def _safe_raise_for_status(self, response: Any) -> None:
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            await self._maybe_await(raise_for_status())

    async def _safe_json(self, response: Any) -> Any:
        json_method = getattr(response, "json", None)
        if not callable(json_method):
            return {}
        return await self._maybe_await(json_method())

    async def _initial_frame(self, game_id: str) -> tuple[dict, str | None]:
        start_t = time.time()
        if self.harness.mock_api:
            frame = self.harness._get_mock_initial_frame(game_id)
            if hasattr(self.brain, "record_arc_api_call"):
                self.brain.record_arc_api_call(
                    phase=getattr(self.brain, "current_phase", "bootstrap"),
                    method="GET",
                    endpoint="/api/games/initial",
                    request_payload={"game_id": game_id},
                    response_payload=frame,
                    latency_ms=(time.time() - start_t) * 1000,
                )
            return frame, frame.get("guid")

        session = getattr(self.harness, "_session", None)
        if session is None:
            raise RuntimeError("ARC API session not initialized. Did you call harness.setup()?")

        sc_start = time.time()
        try:
            scorecard_resp = await session.post("/api/scorecard/open", json={})
            sc_latency = (time.time() - sc_start) * 1000
            await self._safe_raise_for_status(scorecard_resp)
            sc_json = await self._safe_json(scorecard_resp)
            card_id = sc_json["card_id"]
            if hasattr(self.brain, "record_arc_api_call"):
                self.brain.record_arc_api_call(
                    phase=getattr(self.brain, "current_phase", "bootstrap"),
                    method="POST",
                    endpoint="/api/scorecard/open",
                    request_payload={},
                    response_payload=sc_json,
                    latency_ms=sc_latency,
                )
        except Exception as exc:
            if hasattr(self.brain, "record_arc_api_call"):
                self.brain.record_arc_api_call(
                    phase=getattr(self.brain, "current_phase", "bootstrap"),
                    method="POST",
                    endpoint="/api/scorecard/open",
                    request_payload={},
                    response_payload=None,
                    latency_ms=(time.time() - sc_start) * 1000,
                    received=False,
                    error_details={
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            raise

        reset_start = time.time()
        reset_payload = {"game_id": game_id, "card_id": card_id}
        try:
            reset_resp = await session.post("/api/cmd/RESET", json=reset_payload)
            reset_latency = (time.time() - reset_start) * 1000
            await self._safe_raise_for_status(reset_resp)
            frame = await self._safe_json(reset_resp)
            if hasattr(self.brain, "record_arc_api_call"):
                self.brain.record_arc_api_call(
                    phase=getattr(self.brain, "current_phase", "bootstrap"),
                    method="POST",
                    endpoint="/api/cmd/RESET",
                    request_payload=reset_payload,
                    response_payload=frame,
                    latency_ms=reset_latency,
                )
            return frame, frame.get("guid")
        except Exception as exc:
            if hasattr(self.brain, "record_arc_api_call"):
                self.brain.record_arc_api_call(
                    phase=getattr(self.brain, "current_phase", "bootstrap"),
                    method="POST",
                    endpoint="/api/cmd/RESET",
                    request_payload=reset_payload,
                    response_payload=None,
                    latency_ms=(time.time() - reset_start) * 1000,
                    received=False,
                    error_details={
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            raise

    async def _execute_action(self, game_id: str, guid: str | None, action: Mapping[str, Any], step: int) -> tuple[dict, float, bool, str | None]:
        start_t = time.time()
        if self.harness.mock_api:
            frame, reward, done = self.harness._execute_mock_action(game_id, action, step)
            if hasattr(self.brain, "record_arc_api_call"):
                self.brain.record_arc_api_call(
                    phase=getattr(self.brain, "current_phase", "act"),
                    method="POST",
                    endpoint=f"/api/cmd/{action.get('action_id', 'unknown')}",
                    request_payload=action,
                    response_payload=frame,
                    latency_ms=(time.time() - start_t) * 1000,
                )
            return frame, reward, done, frame.get("guid", guid)

        session = getattr(self.harness, "_session", None)
        if session is None:
            raise RuntimeError("ARC API session not initialized. Did you call harness.setup()?")

        action_id = action.get("action_id", "ACTION1")
        payload = {"game_id": game_id, "guid": guid}
        if action_id == "ACTION6":
            payload["x"] = action.get("x", 0)
            payload["y"] = action.get("y", 0)
        if "rationale" in action:
            payload["reasoning"] = action["rationale"]

        call_start = time.time()
        try:
            action_resp = await session.post(f"/api/cmd/{action_id}", json=payload)
            latency = (time.time() - call_start) * 1000
            await self._safe_raise_for_status(action_resp)
            frame = await self._safe_json(action_resp)
            if hasattr(self.brain, "record_arc_api_call"):
                self.brain.record_arc_api_call(
                    phase=getattr(self.brain, "current_phase", "act"),
                    method="POST",
                    endpoint=f"/api/cmd/{action_id}",
                    request_payload=payload,
                    response_payload=frame,
                    latency_ms=latency,
                )
            reward = self._extract_env_reward(frame)
            done = frame.get("state") in ("WIN", "GAME_OVER")
            return frame, reward, done, frame.get("guid", guid)
        except Exception as exc:
            if hasattr(self.brain, "record_arc_api_call"):
                self.brain.record_arc_api_call(
                    phase=getattr(self.brain, "current_phase", "act"),
                    method="POST",
                    endpoint=f"/api/cmd/{action_id}",
                    request_payload=payload,
                    response_payload=None,
                    latency_ms=(time.time() - call_start) * 1000,
                    received=False,
                    error_details={
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            raise

    def _extract_prompt_block_trace(self, prompt: str | None) -> list[dict]:
        if not isinstance(prompt, str) or not prompt.strip():
            return []

        markers = []
        def add(section: str, needle: str, block: str, tool: str):
            idx = prompt.find(needle)
            if idx != -1:
                markers.append((idx, section, block, tool))

        add("SOLVE CONTEXT", "=== SOLVE CONTEXT ===", "SolveContextBlock", "ARC Agent SolveEngine")
        add("CHUNK", "ACTIVE CHUNK:", "ChunkBlock", "ARC Agent SolveEngine")
        add("ACTION FACTS", "=== ACTION FACTS ===", "ActionFactBlock", "ARC Agent HypothesisManager")
        add("PATH HYPOTHESES", "=== PATH HYPOTHESES ===", "PathHypothesisBlock", "ARC Agent HypothesisManager")
        add("ENTITY CONTEXT", "=== ENTITY CONTEXT ===", "EntityContextBlock", "ARC Agent SolveEngine")
        add("OBSERVATION", "=== OBSERVATION ===", "ObservationBlock", "ARC Harness + ARC Agent")
        add("INSTRUCTION", "INSTRUCTION:", "InstructionBlock", "ARC Agent Prompt Builder")

        markers.sort(key=lambda item: item[0])
        trace = []
        for order, (_, section, block, tool) in enumerate(markers, start=1):
            trace.append(
                {
                    "order": order,
                    "section": section,
                    "block": block,
                    "owner": "ARC agent",
                    "tool": tool,
                }
            )
        return trace

    def _build_orchestration_report(
        self,
        ledger: list[dict],
        entity_gate_status: Mapping[str, Any] | None = None,
        progress_log: list[dict] | None = None,
    ) -> dict:
        phase_owner = {
            "bootstrap": "harness",
            "perceive": "orchestrator",
            "model": "orchestrator",
            "hypothesize": "orchestrator",
            "route": "orchestrator",
            "execute": "LLM",
            "evaluate": "harness",
            "replan": "harness",
        }
        decision_flow = {
            "bootstrap": {"proposer": "harness", "executor": "harness"},
            "perceive": {"proposer": "orchestrator", "executor": "SideQuests"},
            "model": {"proposer": "orchestrator", "executor": "SideQuests"},
            "hypothesize": {"proposer": "orchestrator", "executor": "orchestrator"},
            "route": {"proposer": "orchestrator", "executor": "orchestrator"},
            "execute": {"proposer": "LLM", "executor": "orchestrator"},
            "evaluate": {"proposer": "harness", "executor": "harness"},
            "replan": {"proposer": "harness", "executor": "harness"},
        }
        # Backwards-compatibility: mirror pre-B201 legacy phase names to canonical entries
        legacy_aliases = {
            "solve": "route",
            "act": "execute",
            "ingest": "evaluate",
            "plan": "model",
        }
        for old, canon in legacy_aliases.items():
            if canon in phase_owner and old not in phase_owner:
                phase_owner[old] = phase_owner[canon]
            if canon in decision_flow and old not in decision_flow:
                decision_flow[old] = decision_flow[canon]
        tool_rules = {
            "branch_quest": {
                "owner": "SideQuests",
                "allowed_modes": ["write"],
                "allowed_phases": ["unknown", "bootstrap"],
            },
            "notify_turn": {
                "owner": "SideQuests",
                "allowed_modes": ["write"],
                "allowed_phases": ["bootstrap", "perceive", "model", "execute", "evaluate", "replan", "finalization"],
            },
            "current_truth": {
                "owner": "SideQuests",
                "allowed_modes": ["read"],
                "allowed_phases": ["bootstrap", "perceive", "model", "execute", "evaluate", "route", "replan", "hypothesize"],
            },
            "recall_lessons": {
                "owner": "SideQuests",
                "allowed_modes": ["read"],
                "allowed_phases": ["bootstrap", "perceive", "model", "route", "evaluate", "replan", "hypothesize"],
            },
            "register_plan": {
                "owner": "SideQuests",
                "allowed_modes": ["write"],
                "allowed_phases": ["bootstrap", "perceive", "model", "route", "replan"],
            },
            "report_outcome": {
                "owner": "SideQuests",
                "allowed_modes": ["write"],
                "allowed_phases": ["evaluate", "route", "finalization"],
            },
            "upsert_lesson": {
                "owner": "SideQuests",
                "allowed_modes": ["write"],
                "allowed_phases": ["perceive", "evaluate", "finalization", "model"],
            },
        }

        violations = []
        adherence = {
            "events": 0,
            "mismatches": 0,
            "samples": [],
        }
        adherence_seen: set[tuple[Any, Any, Any, Any]] = set()

        def _record_adherence_event(step: Any, expected: Any, selected: Any, reason: Any, ok: Any) -> None:
            key = (step, expected, selected, reason)
            if key in adherence_seen:
                return
            adherence_seen.add(key)
            adherence["events"] += 1
            if ok is False:
                adherence["mismatches"] += 1
                if len(adherence["samples"]) < 5:
                    adherence["samples"].append(
                        {
                            "step": step,
                            "expected_action": expected,
                            "selected_action": selected,
                            "override_reason": reason,
                        }
                    )

        for entry in ledger or []:
            call_type = entry.get("call_type") or entry.get("kind")
            phase = entry.get("phase")
            mode = entry.get("mode")
            rule = tool_rules.get(call_type)

            # B209: planner/executor adherence diagnostics emitted via optional
            # action_adherence ledger rows.
            if call_type == "action_adherence":
                _record_adherence_event(
                    entry.get("step"),
                    entry.get("expected_action"),
                    entry.get("selected_action"),
                    entry.get("override_reason"),
                    entry.get("adherence_ok"),
                )

            if not rule:
                continue
            # Normalize legacy/pre-B201 phase names to canonical B201 names
            legacy_map = {
                "solve": "route",
                "act": "execute",
                "ingest": "evaluate",
                "plan": "model",
            }
            phase_norm = phase if not isinstance(phase, str) else legacy_map.get(phase, phase)
            
            # A060: Bootstrap phase awareness.
            # unknown-phase calls are permitted during bootstrap (step 0)
            if phase_norm == "unknown" and entry.get("step") == 0:
                phase_norm = "bootstrap"
            
            if phase_norm not in rule["allowed_phases"]:
                violations.append(
                    {
                        "type": "phase_violation",
                        "phase": phase_norm,
                        "call_type": call_type,
                        "allowed_phases": list(rule["allowed_phases"]),
                        "step": entry.get("step"),
                    }
                )
            elif mode is not None and mode not in rule["allowed_modes"]:
                violations.append(
                    {
                        "type": "mode_violation",
                        "phase": phase_norm,
                        "call_type": call_type,
                        "allowed_modes": list(rule["allowed_modes"]),
                        "step": entry.get("step"),
                    }
                )

        # B209 fallback: when ledger has no action_adherence rows, derive adherence
        # diagnostics from per-step decision metadata captured in progress_log.
        for step in progress_log or []:
            if not isinstance(step, dict):
                continue
            expected = step.get("expected_action")
            selected = step.get("selected_action")
            ok = step.get("adherence_ok")
            reason = step.get("override_reason")
            if expected is None and selected is None and ok is None:
                decision_flow = step.get("decision_flow") if isinstance(step.get("decision_flow"), dict) else {}
                expected = decision_flow.get("expected_action")
                selected = decision_flow.get("selected_action")
                ok = decision_flow.get("adherence_ok")
                reason = decision_flow.get("override_reason")
            if expected is None and selected is None and ok is None:
                continue
            _record_adherence_event(step.get("step"), expected, selected, reason, ok)

        available_widths: list[int] = []
        for step in progress_log or []:
            if not isinstance(step, dict):
                continue
            available_actions = step.get("available_actions")
            if isinstance(available_actions, (list, tuple, set)):
                available_widths.append(len(available_actions))
            elif available_actions is not None:
                try:
                    available_widths.append(int(available_actions))
                except (TypeError, ValueError):
                    continue
        single_action_environment = bool(available_widths) and max(available_widths) <= 1
        
        # A042: orchestration violations can trigger on either phase/mode violations
        # (structural) or high planner/executor drift (behavioral).
        # behavioral_violation threshold: >3 mismatches AND >10% error rate.
        behavioral_violation = False
        if adherence["events"] >= 5:
            mismatch_ratio = adherence["mismatches"] / adherence["events"]
            if adherence["mismatches"] > 3 and mismatch_ratio > 0.10:
                behavioral_violation = True
        
        # A042: suppress all violations (structural and behavioral) for small sample sizes
        # where orchestration patterns haven't had a chance to stabilize.
        # Only suppress if we have a log (total_steps > 0) and it is small (<= 5).
        total_steps = len(progress_log or [])
        small_sample_size = 0 < total_steps <= 5
        
        suppressed_violations: list[dict] = []
        is_violation = bool(violations) or behavioral_violation
        
        if (single_action_environment or small_sample_size) and is_violation:
            suppressed_violations = list(violations)
            if behavioral_violation:
                suppressed_violations.append({
                    "type": "adherence_violation",
                    "mismatches": adherence["mismatches"],
                    "events": adherence["events"],
                    "reason": "high_drift_suppressed_by_constraint"
                })
            violations = []
            is_violation = False

        status = "violation" if is_violation else "ok"

        return {
            "orchestration_owner": "ARC Harness",
            "decision_flow": decision_flow,
            "phase_owner": phase_owner,
            "tool_rules": tool_rules,
            "planner_executor_adherence": adherence,
            "runtime_surfaces": ["progress_log", "prompt_trace", "sidequests_ledger"],
            "single_action_environment": single_action_environment,
            "small_sample_size": small_sample_size,
            "entity_gate_status": dict(entity_gate_status) if isinstance(entity_gate_status, dict) else {},
            "violations": violations,
            "suppressed_violations": suppressed_violations,
            "status": status,
        }

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, "", "N/A"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _solve_context_get(solve_context: Any, key: str, default: Any = None) -> Any:
        """A072: Robustly access fields in SolveContext (supports dict, dataclass, or None)."""
        if solve_context is None:
            return default
        if isinstance(solve_context, dict):
            return solve_context.get(key, default)
        # Handle other mapping-like or object shapes
        try:
            if hasattr(solve_context, "get"):
                return solve_context.get(key, default)
        except Exception:
            pass
        return getattr(solve_context, key, default)

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        if value in (None, "", "N/A"):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _extract_env_signals(self, frame: Mapping[str, Any] | None) -> dict:
        """Extract raw environment-side metrics from a frame response."""
        if not isinstance(frame, Mapping):
            return {}
        signals: dict[str, Any] = {}
        for key in (
            "state",
            "reward",
            "score",
            "lives",
            "life",
            "levels_completed",
            "win_levels",
            "full_reset",
        ):
            if key in frame:
                signals[key] = frame.get(key)
        return signals

    def _extract_env_reward(self, frame: Mapping[str, Any] | None) -> float:
        """Read environment reward from explicit server fields only.

        Falls back to terminal WIN semantics if no explicit numeric reward field
        is present in the response payload.
        """
        if not isinstance(frame, Mapping):
            return 0.0
        for key in ("reward", "env_reward", "delta_reward", "step_reward"):
            value = self._safe_float(frame.get(key))
            if value is not None:
                return float(value)
        return 1.0 if frame.get("state") == "WIN" else 0.0

    @staticmethod
    def _count_color_cells(grid: Any, color_id: int) -> int:
        if not isinstance(grid, list):
            return 0
        count = 0
        for row in grid:
            if not isinstance(row, list):
                continue
            for cell in row:
                try:
                    if int(cell) == int(color_id):
                        count += 1
                except (TypeError, ValueError):
                    continue
        return count

    def _resolve_target_color_id(self, orchestrator: ARCOrchestrator) -> int | None:
        try:
            vc_obj = getattr(getattr(orchestrator, "solve_engine", None), "_victory_condition", None)
            if vc_obj is not None and getattr(vc_obj, "target_color_id", None) is not None:
                target = int(vc_obj.target_color_id)
                return target if target != 0 else None
        except Exception:
            pass
        try:
            solve_ctx = getattr(orchestrator, "_solve_context", {}) or {}
            vc = solve_ctx.get("victory_condition")
            if isinstance(vc, dict) and vc.get("target_color_id") is not None:
                target = int(vc.get("target_color_id"))
                return target if target != 0 else None
        except Exception:
            pass
        return None

    def _compute_progress_reward(
        self,
        *,
        env_reward: float,
        prev_grid: Any,
        next_grid: Any,
        prev_levels_completed: int | None,
        next_levels_completed: int | None,
        prev_score: float | None,
        next_score: float | None,
        target_color_id: int | None,
        prev_terminal_value_score: float | None = None,
        next_terminal_value_score: float | None = None,
    ) -> tuple[float, dict]:
        """Compute bounded dense reward while preserving true sparse env reward."""
        env = max(0.0, float(env_reward or 0.0))
        dense = 0.0
        components: dict[str, Any] = {"env_reward": round(env, 6)}

        if next_levels_completed is not None and prev_levels_completed is not None:
            levels_delta = max(0, int(next_levels_completed) - int(prev_levels_completed))
            if levels_delta > 0:
                gain = min(0.6, 0.3 * float(levels_delta))
                dense += gain
                components["levels_progress"] = round(gain, 6)

        if next_score is not None and prev_score is not None:
            score_delta = max(0.0, float(next_score) - float(prev_score))
            if score_delta > 0.0:
                gain = min(0.3, 0.3 * score_delta)
                dense += gain
                components["score_progress"] = round(gain, 6)

        changed_cells = 0
        total_cells = 0
        changed_ratio = 0.0
        if isinstance(prev_grid, list) and isinstance(next_grid, list):
            rows = min(len(prev_grid), len(next_grid))
            for r in range(rows):
                prev_row = prev_grid[r] if isinstance(prev_grid[r], list) else []
                next_row = next_grid[r] if isinstance(next_grid[r], list) else []
                cols = min(len(prev_row), len(next_row))
                total_cells += cols
                for c in range(cols):
                    if prev_row[c] != next_row[c]:
                        changed_cells += 1
            if changed_cells > 0 and total_cells > 0:
                changed_ratio = changed_cells / float(total_cells)
                gain = min(0.08, changed_ratio * 0.2)
                dense += gain
                components["novel_frame_change"] = round(gain, 6)

        if target_color_id is not None and isinstance(prev_grid, list) and isinstance(next_grid, list):
            prev_target = self._count_color_cells(prev_grid, target_color_id)
            next_target = self._count_color_cells(next_grid, target_color_id)
            target_delta = max(0, next_target - prev_target)
            if target_delta > 0:
                gain = min(0.35, 0.02 * float(min(target_delta, 20)))
                dense += gain
                components["target_color_progress"] = round(gain, 6)

        shaped_reward = max(env, min(0.95, dense))
        components["dense_reward"] = round(min(0.95, dense), 6)
        components["final_reward"] = round(shaped_reward, 6)

        # A066: Meaningful progress gate
        meaningful_progress = False
        progress_class = "none"
        progress_gate_reason = "no progress detected"

        if env > 0:
            meaningful_progress = True
            progress_class = "terminal"
            progress_gate_reason = "environment reward detected"
        elif components.get("levels_progress", 0) > 0:
            meaningful_progress = True
            progress_class = "level"
            progress_gate_reason = "levels completed"
        elif components.get("score_progress", 0) > 0:
            meaningful_progress = True
            progress_class = "score"
            progress_gate_reason = "score increased"
        elif next_terminal_value_score is not None and prev_terminal_value_score is not None and next_terminal_value_score > prev_terminal_value_score:
            meaningful_progress = True
            progress_class = "terminal" # Using 'terminal' as per plan
            progress_gate_reason = "terminal value score increased"
        elif components.get("target_color_progress", 0) > 0:
            meaningful_progress = True
            progress_class = "object_monotonic"
            progress_gate_reason = "target color progress"
        elif components.get("novel_frame_change", 0) > 0:
            progress_class = "pixel_churn"
            # Low-ratio cell changes are not meaningful
            if changed_ratio > 0.05 or changed_cells >= 5:
                 progress_gate_reason = "pixel churn (below meaningful threshold)"
            else:
                 progress_gate_reason = "isolated pixel churn"
        
        components["meaningful_progress"] = meaningful_progress
        components["progress_class"] = progress_class
        components["progress_gate_reason"] = progress_gate_reason

        return shaped_reward, components

    def _build_eval_layers(self, result: dict, orchestration_status: str | None = None) -> dict:
        benchmark_metrics = result.get("benchmark_metrics") if isinstance(result.get("benchmark_metrics"), dict) else {}
        token_cost = benchmark_metrics.get("token_cost") if isinstance(benchmark_metrics.get("token_cost"), dict) else {}
        prompt_budget = benchmark_metrics.get("prompt_budget") if isinstance(benchmark_metrics.get("prompt_budget"), dict) else {}

        cost_usd = self._safe_float(result.get("cost_usd"))
        if cost_usd is None:
            cost_usd = self._safe_float(token_cost.get("cost_usd"))

        invalid_action_count = result.get("invalid_action_count")
        if invalid_action_count is None:
            invalid_action_count = prompt_budget.get("invalid_action_count")

        total_tokens = int(result.get("tokens_input") or 0) + int(result.get("tokens_output") or 0)
        correct = bool(result.get("correct", False))
        budget_usd = self._safe_float(token_cost.get("budget_usd"))
        judge_verdict = result.get("judge_verdict")

        finops = {
            "tokens_input": int(result.get("tokens_input") or 0),
            "tokens_output": int(result.get("tokens_output") or 0),
            "total_tokens": total_tokens,
            "cost_usd": cost_usd,
            "cost_per_solve_usd": cost_usd if correct and cost_usd is not None else None,
            "budget_usd": budget_usd,
            "budget_exhausted": bool(token_cost.get("budget_exhausted") is True),
            "model": token_cost.get("model") or (((self.config.get("llm") or {}).get("model") if isinstance(self.config, dict) else "unknown") or "unknown"),
        }
        component_eval = {
            "status": "available",
            "entity_gate_status": ((result.get("entity_gate_status") or {}).get("status") if isinstance(result.get("entity_gate_status"), dict) else None),
            "invalid_action_count": invalid_action_count,
            "dissonance_triggered": result.get("dissonance_triggered"),
            "failure_class": result.get("failure_class"),
            "orchestration_status": orchestration_status or "pending",
        }
        trajectory_eval = result.get("trajectory_score") or {
            "status": "unavailable",
            "reason": "trajectory score not computed",
        }
        outcome_eval = judge_verdict or {
            "status": "unavailable",
            "reason": "reference solution not available",
        }
        system_monitoring = result.get("system_monitoring")
        if not isinstance(system_monitoring, dict):
            system_monitoring = {"status": "pending_batch_eval", "alerts": []}

        evals = {
            "finops": finops,
            "component_eval": component_eval,
            "trajectory_eval": trajectory_eval,
            "outcome_eval": outcome_eval,
            "system_monitoring": system_monitoring,
        }
        if isinstance(result.get("quality_dimensions"), dict):
            evals["quality_dimensions"] = result.get("quality_dimensions")
        return evals

    def _row_to_ab_task_result(self, row: dict) -> ABTaskResult:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        benchmark_metrics = row.get("benchmark_metrics") if isinstance(row.get("benchmark_metrics"), dict) else {}
        if not benchmark_metrics and isinstance(metadata.get("benchmark_metrics"), dict):
            benchmark_metrics = metadata.get("benchmark_metrics") or {}

        cost_usd = self._safe_float(row.get("cost_usd"))
        if cost_usd is None:
            cost_usd = self._safe_float((benchmark_metrics.get("token_cost") or {}).get("cost_usd"))

        invalid_action_count = row.get("invalid_action_count")
        if invalid_action_count is None:
            invalid_action_count = (benchmark_metrics.get("prompt_budget") or {}).get("invalid_action_count")

        judge_verdict = row.get("judge_verdict")
        if judge_verdict is None and isinstance(metadata.get("judge_verdict"), dict):
            judge_verdict = metadata.get("judge_verdict")

        return ABTaskResult(
            task_id=str(row.get("task_id", "unknown")),
            variant=ABVariant.SIDEQUESTS,
            correct=bool(row.get("correct", False)),
            steps=int(row.get("steps", 0) or 0),
            tokens_input=int(row.get("tokens_input", 0) or 0),
            tokens_output=int(row.get("tokens_output", 0) or 0),
            error_message=row.get("error_message"),
            failure_class=row.get("failure_class"),
            attempts=int(row.get("attempts", 1) or 1),
            cost_usd=cost_usd,
            invalid_action_count=invalid_action_count,
            dissonance_triggered=row.get("dissonance_triggered"),
            trajectory_score=row.get("trajectory_score"),
            final_state=row.get("final_state"),
            final_observation=row.get("final_observation"),
            judge_verdict=judge_verdict,
        )

    def _attach_batch_eval_summary(self, rows: List[dict]) -> List[dict]:
        if not rows:
            return rows

        metrics: dict[str, Any] = {}
        try:
            metric_harness = ABHarness(BenchmarkConfig(name="submission-evals", parameters={}))
            metrics = metric_harness._compute_metrics([
                self._row_to_ab_task_result(row)
                for row in rows
                if isinstance(row, dict)
            ])
        except Exception:
            logger.exception("B182: failed to compute submission eval summary")

        quality_dimensions = metrics.get("quality_dimensions", {}) if isinstance(metrics, dict) else {}
        system_monitoring: dict[str, Any] = {"status": "unavailable", "alerts": []}

        if isinstance(metrics, dict) and metrics:
            try:
                config_blob = json.dumps(self.config if isinstance(self.config, dict) else {}, sort_keys=True, default=str)
                config_hash = hashlib.sha256(config_blob.encode("utf-8")).hexdigest()[:12]
            except Exception:
                config_hash = "unknown"

            try:
                repo_root = Path(__file__).resolve().parents[2]
                git_commit = subprocess.check_output(
                    ["git", "rev-parse", "--short", "HEAD"],
                    cwd=str(repo_root),
                    text=True,
                ).strip()
            except Exception:
                git_commit = "unknown"

            run_record = RunRecord(
                run_id=f"submission_{uuid.uuid4().hex[:12]}",
                timestamp=time.time(),
                model=((self.config.get("llm") or {}).get("model") if isinstance(self.config, dict) else "unknown") or "unknown",
                config_hash=config_hash,
                git_commit=git_commit,
                metrics=quality_dimensions,
            )

            try:
                history_dir = "benchmarks/results"
                if isinstance(self.config, dict):
                    eval_cfg = self.config.get("eval") or {}
                    if isinstance(eval_cfg, dict) and eval_cfg.get("history_dir"):
                        history_dir = str(eval_cfg.get("history_dir"))

                monitor = RegressionMonitor(history_dir=history_dir)
                prior_history_count = len(monitor._load_history())
                alerts = [asdict(alert) for alert in monitor.check(run_record)]
                monitor.save_run(run_record)
                system_monitoring = {
                    "status": "alert" if alerts else ("insufficient_history" if prior_history_count < 3 else "ok"),
                    "alerts": alerts,
                    "run_record": asdict(run_record),
                    "history_dir": history_dir,
                    "history_run_count_before_save": prior_history_count,
                }
            except Exception:
                logger.exception("B188: regression monitoring failed")
                system_monitoring = {
                    "status": "error",
                    "alerts": [],
                    "run_record": asdict(run_record),
                }

        for row in rows:
            if not isinstance(row, dict):
                continue

            row["quality_dimensions"] = quality_dimensions
            row["system_monitoring"] = system_monitoring

            benchmark_metrics = row.get("benchmark_metrics")
            if not isinstance(benchmark_metrics, dict):
                benchmark_metrics = {}
                row["benchmark_metrics"] = benchmark_metrics
            if quality_dimensions:
                benchmark_metrics["quality_dimensions"] = quality_dimensions

            evals = row.get("evals")
            if not isinstance(evals, dict):
                evals = {}
                row["evals"] = evals
            if quality_dimensions:
                evals["quality_dimensions"] = quality_dimensions
            evals["system_monitoring"] = system_monitoring

            for meta_key in ("metadata", "submission_metadata"):
                metadata = row.get(meta_key)
                if not isinstance(metadata, dict):
                    continue
                if quality_dimensions:
                    metadata["quality_dimensions"] = quality_dimensions
                metadata["system_monitoring"] = system_monitoring
                meta_evals = metadata.get("evals")
                if not isinstance(meta_evals, dict):
                    meta_evals = {}
                    metadata["evals"] = meta_evals
                if quality_dimensions:
                    meta_evals["quality_dimensions"] = quality_dimensions
                meta_evals["system_monitoring"] = system_monitoring

        return rows

    def _submission_row_from_result(self, result: dict) -> dict:
        if not isinstance(result, dict):
            return {}

        progress_log = list(result.get("debug_steps") or [])
        prompt_trace = [
            {
                "step": step.get("step"),
                "available_actions": step.get("available_actions", []),
                "prompt": step.get("prompt"),
                "block_trace": self._extract_prompt_block_trace(step.get("prompt")),
            }
            for step in progress_log
        ]

        benchmark_metrics = result.get("benchmark_metrics") if isinstance(result.get("benchmark_metrics"), dict) else {}
        confidence = [1.0 if result.get("correct") else 0.0]
        metadata = {
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "submission_id": f"sub_{uuid.uuid4().hex[:12]}",
            "run_duration_seconds": result.get("runtime_seconds", 0),
            "environment": {
                "llm_model": ((self.config.get("llm") or {}).get("model") if isinstance(self.config, dict) else "unknown") or "unknown",
                "llm_endpoint": "unknown",
                "memory_backend": "unknown",
                "arc_api_endpoint": "mock-harness" if bool(getattr(self.harness, "mock_api", False)) else "three.arcprize.org",
            },
            "model": ((self.config.get("llm") or {}).get("model") if isinstance(self.config, dict) else "unknown") or "unknown",
            "memory_enabled": not isinstance(self._raw_brain, type(None)),
            "steps": result.get("steps", 0),
            "correct": result.get("correct", False),
            "tokens_input": result.get("tokens_input", 0),
            "tokens_output": result.get("tokens_output", 0),
            "final_state": result.get("final_state"),
            "benchmark_metrics": benchmark_metrics,
            "solve_phase_summary": result.get("solve_phase_summary", {}),
            "cost_usd": self._safe_float(result.get("cost_usd")) if result.get("cost_usd") is not None else self._safe_float((benchmark_metrics.get("token_cost") or {}).get("cost_usd")),
            "invalid_action_count": result.get("invalid_action_count", (benchmark_metrics.get("prompt_budget") or {}).get("invalid_action_count")),
            "judge_verdict": result.get("judge_verdict"),
        }
        if result.get("failure_class") is not None:
            metadata["failure_class"] = result.get("failure_class")
        if result.get("trajectory_score") is not None:
            metadata["trajectory_score"] = result.get("trajectory_score")

        sidequests_ledger = list(result.get("sidequests_ledger") or [])
        arc_event_timeline = list(result.get("arc_event_timeline") or [])

        chronological_log: list[dict] = []
        for entry in sidequests_ledger:
            if isinstance(entry, dict):
                chronological_log.append(dict(entry))
        for event in arc_event_timeline:
            if not isinstance(event, dict):
                continue
            normalized = dict(event)
            normalized["timestamp_iso"] = (
                event.get("timestamp_iso")
                or event.get("request_started_iso")
                or event.get("response_received_iso")
            )
            chronological_log.append(normalized)
        chronological_log.sort(
            key=lambda entry: (
                str(entry.get("timestamp_iso") or ""),
                int(entry.get("event_seq") or 0),
                int(entry.get("call_seq") or 0),
            )
        )

        arc_pairs_map: dict[int, dict] = {}
        arc_raw_io_map: dict[int, dict] = {}
        for entry in sidequests_ledger:
            if not isinstance(entry, dict):
                continue
            arc_api_io = entry.get("arc_api_io") or {}
            seq = arc_api_io.get("call_seq")
            if seq is None:
                continue
            arc_raw_io_map[int(seq)] = arc_api_io

        for event in arc_event_timeline:
            if not isinstance(event, dict):
                continue
            seq = event.get("call_seq")
            if seq is None:
                continue
            pair = arc_pairs_map.setdefault(
                int(seq),
                {"call_seq": int(seq), "request": None, "response": None, "raw_request": None, "raw_response": None},
            )
            if event.get("kind") == "request_started":
                pair["request"] = event
            elif event.get("kind") == "response_received":
                pair["response"] = event

        for seq, pair in arc_pairs_map.items():
            raw_io = arc_raw_io_map.get(seq) or {}
            if raw_io:
                pair["raw_request"] = raw_io.get("request")
                pair["raw_response"] = raw_io.get("response")
                continue

            request_event = pair.get("request") or {}
            response_event = pair.get("response") or {}
            request_payload = request_event.get("raw_payload")
            response_payload = response_event.get("raw_payload")
            if request_payload is not None:
                pair["raw_request"] = {
                    "method": request_event.get("method"),
                    "endpoint": request_event.get("endpoint"),
                    "payload": request_payload,
                }
            if response_payload is not None:
                pair["raw_response"] = {
                    "received": True,
                    "http_status": response_event.get("http_status"),
                    "payload": response_payload,
                    "error": None,
                }
        arc_server_responses = [arc_pairs_map[k] for k in sorted(arc_pairs_map)]

        orchestration_report = self._build_orchestration_report(
            sidequests_ledger,
            result.get("entity_gate_status", {}),
            progress_log,
        )
        evals = self._build_eval_layers(result, orchestration_status=orchestration_report.get("status"))
        metadata["evals"] = evals
        if isinstance(result.get("quality_dimensions"), dict):
            metadata["quality_dimensions"] = result.get("quality_dimensions")

        row = {
            "game_id": result.get("game_id", "unknown"),
            "game_title": result.get("game_title"),
            "game_tags": result.get("game_tags", []),
            "task_id": result.get("task_id", "unknown"),
            "correct": result.get("correct", False),
            "steps": result.get("steps", 0),
            "tokens_input": result.get("tokens_input", 0),
            "tokens_output": result.get("tokens_output", 0),
            "runtime_seconds": result.get("runtime_seconds", 0),
            "error_message": result.get("error_message"),
            "failure_class": result.get("failure_class"),
            "final_state": result.get("final_state"),
            "final_observation": result.get("final_observation"),
            "trajectory_score": result.get("trajectory_score"),
            "terminal_value_score": result.get("terminal_value_score", 0.0),
            "terminal_value_components": result.get("terminal_value_components", {}),
            "judge_verdict": result.get("judge_verdict"),
            "cost_usd": metadata.get("cost_usd"),
            "invalid_action_count": metadata.get("invalid_action_count"),
            "benchmark_metrics": benchmark_metrics,
            "bootstrap_write_trace": result.get("bootstrap_write_trace", []),
            "final_write_trace": result.get("final_write_trace", []),
            "sidequests_ledger": sidequests_ledger,
            "arc_event_timeline": arc_event_timeline,
            "agent_execution_trace": result.get("agent_execution_trace", []),
            "chronological_log": chronological_log,
            "arc_server_responses": arc_server_responses,
            "progress_log": progress_log,
            "prompt_trace": prompt_trace,
            "orchestration_report": orchestration_report,
            "evals": evals,
            "confidence": confidence,
            "world_model_snapshot": result.get("world_model_snapshot", {}),
            "metadata": metadata,
            "submission_metadata": metadata,
        }
        if isinstance(result.get("quality_dimensions"), dict):
            row["quality_dimensions"] = result.get("quality_dimensions")
        if isinstance(result.get("system_monitoring"), dict):
            row["system_monitoring"] = result.get("system_monitoring")
        return row
