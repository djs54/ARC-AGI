"""Durable ARC run driver tying orchestrator + checkpoints + harness."""

from __future__ import annotations

import inspect
import json
import logging
import re
import time
import uuid
import hashlib
import subprocess
import asyncio
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, List, Mapping, Optional

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
                            )
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
                        result_payload["solve_phase_summary"] = self._build_phase_summary(orchestrator)
                        result_payload["game_id"] = getattr(task, "game_id", "unknown")
                        result_payload["runtime_seconds"] = round(duration, 2)
                        result_payload["benchmark_metrics"] = getattr(task_result, "benchmark_metrics", {})
                        result_payload["entity_gate_status"] = getattr(orchestrator, "_entity_gate_result", {}) or {"status": "pass"}
                        result_payload["bootstrap_write_trace"] = getattr(task_result, "bootstrap_write_trace", [])
                        result_payload["final_write_trace"] = getattr(task_result, "final_write_trace", [])
                        result_payload["debug_steps"] = list(getattr(orchestrator, "_step_history", []))
                        result_payload["sidequests_ledger"] = list(self._ledger)
                        result_payload["arc_event_timeline"] = list(getattr(self.brain, "arc_event_timeline", []))
                        result_payload["agent_execution_trace"] = getattr(orchestrator, "_execution_trace", [])

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
                        )

                        try:
                            task_result, duration = await self._run_puzzle(orchestrator, task, checkpoint, mgr)
                            result_payload = asdict(task_result)
                            result_payload["solve_phase_summary"] = self._build_phase_summary(orchestrator)
                            result_payload["game_id"] = getattr(task, "game_id", "unknown")
                            result_payload["runtime_seconds"] = round(duration, 2)
                            result_payload["benchmark_metrics"] = getattr(task_result, "benchmark_metrics", {})
                            result_payload["entity_gate_status"] = getattr(orchestrator, "_entity_gate_result", {}) or {"status": "pass"}
                            result_payload["bootstrap_write_trace"] = getattr(task_result, "bootstrap_write_trace", [])
                            result_payload["final_write_trace"] = getattr(task_result, "final_write_trace", [])
                            result_payload["debug_steps"] = list(getattr(orchestrator, "_step_history", []))
                            result_payload["sidequests_ledger"] = list(self._ledger)
                            result_payload["arc_event_timeline"] = list(getattr(self.brain, "arc_event_timeline", []))
                            result_payload["agent_execution_trace"] = getattr(orchestrator, "_execution_trace", [])
                            
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
                                loop_detected=bool(
                                    (getattr(orchestrator, "_hypothesis_context", {}) or {}).get("loop_detected")
                                    if orchestrator is not None else False
                                ),
                                graduation_reason=str((getattr(orchestrator, "_solve_context", {}) or {}).get("graduation_reason") or "") if orchestrator is not None else "",
                                coverage_saturated=bool((getattr(orchestrator, "_solve_context", {}) or {}).get("coverage_saturated", False)) if orchestrator is not None else False,
                                plateau_escalation_required=bool((getattr(orchestrator, "_solve_context", {}) or {}).get("plateau_escalation_required", False)) if orchestrator is not None else False,
                            )
                            mgr.mark_failed(checkpoint, task.task_id, str(exc), failure_class.value)
                            logger.error("Task %s failed [%s]: %s", task.task_id, failure_class.value, exc)
                            
                            result_payload = {
                                "task_id": task.task_id,
                                "game_id": getattr(task, "game_id", "unknown"),
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
                            }
                            self._ledger.clear()
                            return result_payload

            batch_results = await scheduler.run_batch(ordered_tasks, _run_single_task)
            results = [self._submission_row_from_result(r) for r in batch_results if r is not None]
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
        last_reward = 0.0
        consecutive_no_progress_steps = 0
        bootstrap_write_trace: list[dict] = []
        final_write_trace: list[dict] = []

        for attempt in range(1, max_retries + 1):
            frame_response, guid = await self._initial_frame(game_id)
            observation = adapter.normalize_observation(frame_response)
            last_grid = observation.get("grid")

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
                budget_exhausted = bool(
                    getattr(orchestrator.cost_tracker, "budget_exhausted", False) is True
                ) if getattr(orchestrator, "cost_tracker", None) else False
                if orchestrator.cost_tracker and budget_exhausted:
                    error_msg = "Budget exhausted"
                    done = True
                    break

                if getattr(orchestrator, "_should_abandon", False):
                    error_msg = "Supervisor abandoned"
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

                await orchestrator.solve(observation, hyp_ctx, total_steps)

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

                frame_response, reward, done, guid = await self._execute_action(game_id, guid, action, total_steps)

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
                observation = adapter.normalize_observation(frame_response)
                orchestrator.record_step_result(reward, done, next_observation=observation)

                if reward > last_reward:
                    consecutive_no_progress_steps = 0
                    last_reward = reward
                else:
                    consecutive_no_progress_steps += 1

                state = observation.get("state", "NOT_FINISHED")
                if getattr(orchestrator, "_step_history", None):
                    orchestrator._step_history[-1].update(
                        {"state_after": state, "reward": reward, "done": done}
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

                    try:
                        # B210: use canonical just-recorded step action id to avoid stale attribution.
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
                        logger.exception("perceive_step_response failed")

            if success:
                break
            if state != "GAME_OVER":
                break

        if not success and total_steps >= max_steps * max_retries:
            error_msg = "Max attempts reached across all retries"
        elif not success and not error_msg:
            error_msg = f"Failed after {attempt} attempt(s)"

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
        except Exception:
            logger.exception("B186: trajectory evaluation failed")

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
                max_steps_reached=(total_steps >= max_steps * max_retries),
                loop_detected=bool((getattr(orchestrator, "_hypothesis_context", {}) or {}).get("loop_detected")),
                graduation_reason=str((getattr(orchestrator, "_solve_context", {}) or {}).get("graduation_reason") or "") if orchestrator else "",
                coverage_saturated=bool((getattr(orchestrator, "_solve_context", {}) or {}).get("coverage_saturated", False)) if orchestrator else False,
                plateau_escalation_required=bool((getattr(orchestrator, "_solve_context", {}) or {}).get("plateau_escalation_required", False)) if orchestrator else False,
            ).value

        cost_usd = None
        invalid_action_count = None
        if isinstance(benchmark_metrics, dict):
            cost_usd = self._safe_float((benchmark_metrics.get("token_cost") or {}).get("cost_usd"))
            invalid_action_count = (benchmark_metrics.get("prompt_budget") or {}).get("invalid_action_count")

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
            dissonance_triggered=bool((getattr(orchestrator, "_solve_context", {}) or {}).get("dissonance")),
            trajectory_score=trajectory_score,
            final_state=state,
            final_observation=observation,
            judge_verdict=judge_verdict,
        )
        setattr(task_result, "bootstrap_write_trace", bootstrap_write_trace)
        setattr(task_result, "final_write_trace", final_write_trace)
        setattr(task_result, "benchmark_metrics", benchmark_metrics)
        setattr(task_result, "sidequests_ledger", list(self._ledger))
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
        last_reward = 0.0
        consecutive_no_progress_steps = 0
        bootstrap_write_trace: list[dict] = []
        final_write_trace: list[dict] = []

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
            reward = 1.0 if frame.get("state") == "WIN" else 0.0
            done = frame.get("state") in ("WIN", "GAME_OVER")
            return frame, reward, done, frame.get("guid", guid)

        for attempt in range(1, max_retries + 1):
            frame_response, guid = await _initial_frame_variant(game_id)
            observation = adapter.normalize_observation(frame_response)
            last_grid = observation.get("grid")

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
                budget_exhausted = bool(
                    getattr(orchestrator.cost_tracker, "budget_exhausted", False) is True
                ) if getattr(orchestrator, "cost_tracker", None) else False
                if orchestrator.cost_tracker and budget_exhausted:
                    error_msg = "Budget exhausted"
                    done = True
                    break

                if getattr(orchestrator, "_should_abandon", False):
                    error_msg = "Supervisor abandoned"
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

                await orchestrator.solve(observation, hyp_ctx, total_steps)

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

                frame_response, reward, done, guid = await _execute_action_variant(game_id, guid, action, total_steps)

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
                observation = adapter.normalize_observation(frame_response)
                orchestrator.record_step_result(reward, done, next_observation=observation)

                if reward > last_reward:
                    consecutive_no_progress_steps = 0
                    last_reward = reward
                else:
                    consecutive_no_progress_steps += 1

                state = observation.get("state", "NOT_FINISHED")
                if getattr(orchestrator, "_step_history", None):
                    orchestrator._step_history[-1].update(
                        {"state_after": state, "reward": reward, "done": done}
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

                    try:
                        # B210: use canonical just-recorded step action id to avoid stale attribution.
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
                        logger.exception("Variant perceive_step_response failed")

            if success:
                break
            if state != "GAME_OVER":
                break

        if not success and total_steps >= max_steps * max_retries:
            error_msg = "Max attempts reached across all retries"
        elif not success and not error_msg:
            error_msg = f"Failed after {attempt} attempt(s)"

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
        except Exception:
            logger.exception("B186: trajectory evaluation failed")

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
                max_steps_reached=(total_steps >= max_steps * max_retries),
                loop_detected=bool((getattr(orchestrator, "_hypothesis_context", {}) or {}).get("loop_detected")),
                graduation_reason=str((getattr(orchestrator, "_solve_context", {}) or {}).get("graduation_reason") or "") if orchestrator else "",
                coverage_saturated=bool((getattr(orchestrator, "_solve_context", {}) or {}).get("coverage_saturated", False)) if orchestrator else False,
                plateau_escalation_required=bool((getattr(orchestrator, "_solve_context", {}) or {}).get("plateau_escalation_required", False)) if orchestrator else False,
            ).value

        cost_usd = None
        invalid_action_count = None
        if isinstance(benchmark_metrics, dict):
            cost_usd = self._safe_float((benchmark_metrics.get("token_cost") or {}).get("cost_usd"))
            invalid_action_count = (benchmark_metrics.get("prompt_budget") or {}).get("invalid_action_count")

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
            dissonance_triggered=bool((getattr(orchestrator, "_solve_context", {}) or {}).get("dissonance")),
            trajectory_score=trajectory_score,
            final_state=state,
            final_observation=observation,
            judge_verdict=judge_verdict,
        )
        setattr(task_result, "bootstrap_write_trace", bootstrap_write_trace)
        setattr(task_result, "final_write_trace", final_write_trace)
        setattr(task_result, "benchmark_metrics", benchmark_metrics)
        setattr(task_result, "sidequests_ledger", list(getattr(brain_client, "ledger", []) or []))
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
        solve_ctx = getattr(orchestrator, "_solve_context", {}) or {}
        hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}
        last_step = (getattr(orchestrator, "_step_history", []) or [{}])[-1] or {}
        active_chunk = solve_ctx.get("active_chunk") or {}
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
            return solve_ctx.get("strategy_summary") or "Building a structural model from the latest observation."
        if phase == SolvePhase.HYPOTHESIZE.value:
            archetype = solve_ctx.get("archetype") or "unknown"
            victory = solve_ctx.get("victory_condition") or {}
            victory_type = victory.get("type") if isinstance(victory, dict) else victory or "unknown"
            return f"Archetype={archetype}; victory_condition={victory_type}."
        if phase == SolvePhase.ROUTE.value:
            return active_chunk.get("description") or solve_ctx.get("strategy_summary") or "Selecting the next strategy chunk."
        if phase == SolvePhase.EXECUTE.value:
            return last_step.get("rationale") or active_chunk.get("description") or "Executing the chosen action."
        if phase == SolvePhase.EVALUATE.value:
            state_after = last_step.get("state_after") or "unknown"
            reward = last_step.get("reward")
            return f"Observed state={state_after}, reward={reward}."
        if phase == SolvePhase.REPLAN.value:
            reason = solve_ctx.get("dissonance_reason") or hyp_ctx.get("dissonance_reason") or "No progress / loop detected."
            return str(reason)
        return solve_ctx.get("strategy_summary") or last_step.get("rationale")

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
        solve_ctx = getattr(orchestrator, "_solve_context", {}) or {}
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

        active_chunk = solve_ctx.get("active_chunk") or {}
        victory = solve_ctx.get("victory_condition")
        return {
            "current_phase": phase_name,
            "phase_question": self._phase_question_for(phase_name),
            "phase_answer": self._phase_answer_for(orchestrator, phase_name),
            "last_transition": history[-1] if history else None,
            "phase_step_count": phase_step_count,
            "replan_count": replan_count,
            "phase_history_tail": history[-8:],
            "archetype": solve_ctx.get("archetype"),
            "archetype_confidence": solve_ctx.get("archetype_confidence"),
            "victory_condition": victory.get("type") if isinstance(victory, dict) else victory,
            "victory_confidence": victory.get("confidence") if isinstance(victory, dict) else None,
            "strategy_summary": solve_ctx.get("strategy_summary"),
            "active_chunk": {
                "description": active_chunk.get("description"),
                "source": active_chunk.get("source"),
                "estimated_actions": active_chunk.get("estimated_actions", []),
                "plan_id": active_chunk.get("plan_id"),
            } if active_chunk else None,
        }

    def _should_replan(self, orchestrator: ARCOrchestrator, no_progress_steps: int) -> bool:
        """Decide whether to enter REPLAN based on loop signals or no-progress counters."""
        try:
            current_step = len(getattr(orchestrator, "_step_history", []) or [])
            backoff = int(getattr(self, "_replan_backoff_steps", 3) or 3)
            if current_step - int(getattr(self, "_last_replan_step", -999) or -999) < backoff:
                return False

            hyp_ctx = getattr(orchestrator, "_hypothesis_context", {}) or {}
            loop_detected = bool(hyp_ctx.get("loop_detected"))
            if loop_detected:
                return True
            if int(no_progress_steps or 0) >= backoff:
                return True
            if int(getattr(orchestrator, "_consecutive_no_progress_steps", 0) or 0) >= backoff:
                return True
        except Exception:
            pass
        return False

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
                await self.brain.upsert_lesson(domain=str(archetype), text=lesson_text, valence=valence, confidence=archetype_confidence, tags=tags)
            except Exception:
                logger.exception("Failed to upsert lesson via brain.upsert_lesson")
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
        snapshot = {
            "snapshot_type": "step",
            "timestamp_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "game_id": getattr(task, "game_id", "unknown"),
            "task_id": task.task_id,
            "step": total_steps,
            "runtime_seconds": round(time.time() - start_time, 2),
            "state_after": observation.get("state", "NOT_FINISHED"),
            "reward": reward,
            "done": done,
            "action_id": last_step.get("action_id"),
            "rationale": last_step.get("rationale"),
            "guard_status": last_step.get("guard_status"),
            "thinking_trace": last_step.get("thinking_trace", []),
            "frame_hash": observation.get("frame_hash"),
            "available_actions": observation.get("available_actions", []),
            "solve_phase_summary": phase_summary,
            "sidequests_ledger_count": len(self._ledger),
        }
        self._progress_callback(snapshot)

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
            reward = 1.0 if frame.get("state") == "WIN" else 0.0
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
            if phase_norm not in rule["allowed_phases"]:
                violations.append(
                    {
                        "type": "phase_violation",
                        "phase": phase_norm,
                        "call_type": call_type,
                        "allowed_phases": list(rule["allowed_phases"]),
                    }
                )
            elif mode is not None and mode not in rule["allowed_modes"]:
                violations.append(
                    {
                        "type": "mode_violation",
                        "phase": phase,
                        "call_type": call_type,
                        "allowed_modes": list(rule["allowed_modes"]),
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

        return {
            "orchestration_owner": "ARC Harness",
            "decision_flow": decision_flow,
            "phase_owner": phase_owner,
            "tool_rules": tool_rules,
            "planner_executor_adherence": adherence,
            "runtime_surfaces": ["progress_log", "prompt_trace", "sidequests_ledger"],
            "entity_gate_status": dict(entity_gate_status) if isinstance(entity_gate_status, dict) else {},
            "violations": violations,
            "status": "ok" if not violations else "violation",
        }

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, "", "N/A"):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

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
            "metadata": metadata,
            "submission_metadata": metadata,
        }
        if isinstance(result.get("quality_dimensions"), dict):
            row["quality_dimensions"] = result.get("quality_dimensions")
        if isinstance(result.get("system_monitoring"), dict):
            row["system_monitoring"] = result.get("system_monitoring")
        return row
