#!/usr/bin/env python3
"""Test submission runner for a small ARC puzzle batch."""


import argparse
import asyncio
import atexit
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agents.common.trace_names import normalize_artifact_payload, normalize_orchestration_status
from arc_runtime import artifacts
from arc_runtime.bundle import ArcV2Bundle, SyncLLMPortAdapter, build_arc_v2_bundle
from arc_runtime.config import load_config
from arc_runtime.dispatch import run_arc_v2_batch, run_arc_v2_task
from arc_runtime.game_session import ArcV2GameSession, _compute_progress, _unwrap_arc_game_payload
from arc_runtime.llm import LLMInitializationError, create_llm_client
from benchmarks.arc3.harness import ARC3Harness, load_tasks_from_manifest
from benchmarks.arc3.world_model_eval import WorldModelEvaluator
from benchmarks.harness import BenchmarkConfig
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient
from sidequest_mcp_client.observability import build_observability
from sidequest_mcp_client.readiness import ReadinessError, check_mcp_readiness

if TYPE_CHECKING:
    from agents.arc3.runner import DurableARCRunner

# Configuration paths
REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "campy.toml"
LEGACY_CONFIG_PATH = REPO_ROOT / "sidequests.toml"
MANIFEST_PATH = REPO_ROOT / "benchmarks/arc3/tasks_manifest.json"
TASK_BATCH_SIZE = 5
FINAL_OUTPUT_PATH = REPO_ROOT / "submission_results_single.json"
ARC_SERVER_OUTPUT_PATH = REPO_ROOT / "submission_results_arcServer.json"
AGENT_EXECUTION_TRACE_PATH = REPO_ROOT / "agent_execution_trace.json"
MASTER_TIMELINE_PATH = REPO_ROOT / "master_timeline.json"
LIVE_OUTPUT_PATH = REPO_ROOT / "submission_results_single.live.jsonl"
ARC_KEY_PATHS = (
    REPO_ROOT / "benchmarks/.arc/arc.json",
    REPO_ROOT / "benchmarks/arc3/.arc/arc.json",
)

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Back-compat re-exports used by tests.
_ArcV2GameSession = ArcV2GameSession
_run_arc_v2_task = run_arc_v2_task
_run_arc_v2_batch = run_arc_v2_batch
_build_arc_v2_bundle = build_arc_v2_bundle
_json_default = artifacts.json_default
_json_dumps = artifacts.json_dumps
_atomic_dump_json = artifacts.atomic_dump_json


def _apply_llm_overrides(config: dict, overrides: dict | None = None) -> dict:
    if not overrides:
        return config
    merged = dict(config)
    llm_cfg = dict(config.get("llm", {}))
    for key, value in overrides.items():
        if value is not None:
            llm_cfg[key] = value
    merged["llm"] = llm_cfg
    return merged


def _remove_db_artifacts(db_path: Path) -> None:
    import shutil

    candidates = [db_path, Path(f"{db_path}.wal"), Path(f"{db_path}.shm"), Path(f"{db_path}-wal"), Path(f"{db_path}-shm")]
    for candidate in candidates:
        if candidate.exists():
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()


def _ensure_arc_api_key(arc_key_path: str | Path | None = None) -> str | None:
    existing = (os.environ.get("ARC_API_KEY") or "").strip()
    if existing:
        return existing
    candidate_paths = [Path(arc_key_path)] if arc_key_path else list(ARC_KEY_PATHS)
    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            key = str(json.loads(path.read_text()).get("key", "")).strip()
        except Exception as exc:
            logger.warning("Could not read ARC key from %s: %s", path, exc)
            continue
        if key:
            os.environ["ARC_API_KEY"] = key
            logger.info("Loaded ARC_API_KEY from %s", path)
            return key
    return None


def _enforce_observability_preflight(config: dict) -> None:
    obs = build_observability(config)
    if config.get("observability", {}).get("enabled") and not getattr(obs, "enabled", False):
        raise RuntimeError("Observability preflight failed: tracing could not be initialized.")


def _enforce_llm_preflight(config: dict) -> None:
    try:
        create_llm_client(config)
    except LLMInitializationError as exc:
        raise RuntimeError(f"LLM preflight failed: {exc}")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ARC puzzles (optionally real API)")
    parser.add_argument("--real-api", action="store_true", help="Run against the real ARC-AGI-3 API")
    parser.add_argument("--live-smoke", action="store_true", help="Convenience mode for one-puzzle live smoke")
    parser.add_argument("--agent-version", choices=("v1", "v2"), default="v2", help="Select ARC agent implementation")
    parser.add_argument("--num-puzzles", type=int, default=None, help="Number of puzzles to run")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum steps per puzzle")
    parser.add_argument("--card-id", type=str, default=None, help="Override ARC checkpoint card id")
    parser.add_argument("--config", type=str, default=None, help="Explicit campy.toml path")
    parser.add_argument("--model", type=str, default=None, help="Override llm.model")
    parser.add_argument("--base-url", type=str, default=None, help="Override llm.base_url")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Override llm.timeout_seconds")
    parser.add_argument("--max-retries", type=int, default=None, help="Override llm.max_retries")
    parser.add_argument("--arc-key-path", type=str, default=None, help="Load ARC_API_KEY from this JSON file")
    parser.add_argument("--world-model-eval", action="store_true", help="Enable World Model architecture evaluation")
    parser.add_argument("--world-model-live-output", type=str, default="submission_results_single.world_model.live.jsonl", help="Path for live world model metrics")
    parser.add_argument("--temporal", action="store_true", help="Enable Temporal workflow dispatch")
    return parser


class SingleTaskRunner:
    def __init__(self, real_api=False, config_path: str | Path | None = None, llm_overrides: dict | None = None, max_steps: int | None = None, live_smoke: bool = False):
        resolved_config_path = Path(config_path) if config_path else (CONFIG_PATH if CONFIG_PATH.exists() else (LEGACY_CONFIG_PATH if LEGACY_CONFIG_PATH.exists() else None))
        self.config = _apply_llm_overrides(load_config(resolved_config_path), llm_overrides)
        if max_steps is not None:
            self.config.setdefault("benchmark", {})["max_attempts_per_puzzle"] = max_steps
        _enforce_llm_preflight(self.config)
        _enforce_observability_preflight(self.config)
        self.db = None
        self.harness = None
        self.tasks = []
        self.results = []
        self.real_api = real_api
        self.live_smoke = live_smoke
        self.live_output_path = LIVE_OUTPUT_PATH
        self.final_output_path = FINAL_OUTPUT_PATH
        self.arc_server_output_path = ARC_SERVER_OUTPUT_PATH
        self.agent_execution_trace_path = AGENT_EXECUTION_TRACE_PATH
        self.master_timeline_path = MASTER_TIMELINE_PATH
        self.world_model_eval = False
        self.world_model_live_output_path = Path("submission_results_single.world_model.live.jsonl")
        self.world_model_evaluator = WorldModelEvaluator()

    async def initialize(self):
        required_tools = ["notify_turn", "current_truth", "register_plan", "report_outcome", "recall_plans", "recall_relevant_lessons", "upsert_lesson"]
        try:
            check_mcp_readiness(required_tools=required_tools, require_brain_socket=True, probe_memory_backend=True, require_roundtrip_persistence=True, call_timeout=10.0)
        except ReadinessError as exc:
            raise RuntimeError(str(exc))

        timeout_budget = 7200 if self.live_smoke else 3600
        benchmark_config = BenchmarkConfig(
            name="ARC-AGI-3",
            description="Single puzzle test",
            timeout=timeout_budget,
            memory_limit_gb=8.0,
            cpu_limit_percent=80.0,
            parameters=self.config.get("benchmark", {}),
        )
        self.harness = ARC3Harness(benchmark_config, db=self.db, mock_api=not self.real_api)
        await self.harness.setup()

        if MANIFEST_PATH.exists():
            self.tasks = load_tasks_from_manifest(str(MANIFEST_PATH))

    def reset_live_output(self):
        self.live_output_path.write_text("")
        if self.world_model_eval:
            self.world_model_evaluator.reset()
            self.world_model_live_output_path.write_text("")

    def append_live_snapshot(self, snapshot: dict):
        artifacts.append_live_snapshot(self, snapshot)

    @staticmethod
    def _make_final_result_compact(result: dict) -> dict:
        return artifacts.make_final_result_compact(
            result,
            final_output_path=FINAL_OUTPUT_PATH,
            live_output_path=LIVE_OUTPUT_PATH,
            world_model_live_output_path=REPO_ROOT / "submission_results_single.world_model.live.jsonl",
            agent_execution_trace_path=AGENT_EXECUTION_TRACE_PATH,
            master_timeline_path=MASTER_TIMELINE_PATH,
        )

    @staticmethod
    def _summarize_world_model_snapshot(snapshot: dict) -> dict:
        return artifacts.summarize_world_model_snapshot(snapshot)

    @staticmethod
    def _build_run_review(result: dict, results_path: str | Path | None = None) -> dict:
        return artifacts.build_run_review(
            result,
            results_path=results_path,
            final_output_path=FINAL_OUTPUT_PATH,
            live_output_path=LIVE_OUTPUT_PATH,
            world_model_live_output_path=REPO_ROOT / "submission_results_single.world_model.live.jsonl",
            agent_execution_trace_path=AGENT_EXECUTION_TRACE_PATH,
            master_timeline_path=MASTER_TIMELINE_PATH,
        )

    def export_results(self):
        artifacts.export_results_from_runner(self)

    async def shutdown(self):
        build_observability(self.config).shutdown()
        if self.harness is not None:
            await self.harness.teardown()
            self.harness = None


def _emergency_shutdown(runner: Any):
    if not runner or not hasattr(runner, "_current_trace_snapshot") or not runner._current_trace_snapshot:
        return
    try:
        path = Path(AGENT_EXECUTION_TRACE_PATH)
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        with open(temp_path, "w") as f:
            json.dump(runner._current_trace_snapshot, f, indent=2, default=_json_default)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, path)
    except Exception:
        logger.exception("Emergency shutdown failed to save trace data.")


async def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    real_api = args.real_api or args.live_smoke

    llm_overrides = {key: value for key, value in {"model": args.model, "base_url": args.base_url, "timeout_seconds": args.timeout_seconds, "max_retries": args.max_retries}.items() if value is not None}
    if args.live_smoke:
        llm_overrides.setdefault("timeout_seconds", 600.0)
        llm_overrides.setdefault("max_retries", 5)

    if real_api and not _ensure_arc_api_key(args.arc_key_path):
        logger.warning("ARC_API_KEY was not found in the environment or repo credential files; live run may fail.")

    num_puzzles = args.num_puzzles if args.num_puzzles is not None else (1 if real_api else TASK_BATCH_SIZE)
    runner = SingleTaskRunner(real_api=real_api, config_path=args.config, llm_overrides=llm_overrides, max_steps=args.max_steps, live_smoke=bool(args.live_smoke))
    runner.world_model_eval = bool(args.world_model_eval)
    if args.world_model_live_output:
        runner.world_model_live_output_path = Path(args.world_model_live_output)

    try:
        await runner.initialize()
        if runner.tasks:
            runner.tasks = runner.tasks[:num_puzzles]
        if not runner.tasks:
            logger.error("No tasks to run!")
            return

        if isinstance(runner.config, dict):
            runner.config["require_submission_artifacts"] = True

        card_id = args.card_id or (f"real_test_{int(time.time())}" if real_api else f"local_test_{int(time.time())}")
        brain_client = MCPBrainClient(runner.db, runner.config)
        runner.reset_live_output()
        if args.agent_version == "v2":
            runner.results = await run_arc_v2_batch(runner, brain_client, card_id, args)
        else:
            from agents.arc3.runner import DurableARCRunner

            durable = DurableARCRunner(runner.harness, brain_client, runner.config, progress_callback=runner.append_live_snapshot)
            durable._emit_transition_snapshots = True
            atexit.register(_emergency_shutdown, durable)
            runner.results = await durable.run(runner.tasks, card_id)

        for result in runner.results:
            run_review = SingleTaskRunner._build_run_review(result, runner.final_output_path)
            final_snapshot = {
                "snapshot_type": "final_result",
                "task_id": result.get("task_id"),
                "game_id": result.get("game_id"),
                "game_title": result.get("game_title"),
                "game_tags": result.get("game_tags", []),
                "correct": result.get("correct"),
                "steps": result.get("steps"),
                "runtime_seconds": result.get("runtime_seconds"),
                "failure_class": result.get("failure_class"),
                "final_state": result.get("final_state"),
                "run_review": run_review,
                "solve_phase_summary": result.get("solve_phase_summary", {}),
                "evals": result.get("evals", {}),
                "quality_dimensions": result.get("quality_dimensions", {}),
                "system_monitoring": result.get("system_monitoring", {}),
                "world_model_snapshot": SingleTaskRunner._summarize_world_model_snapshot(result.get("world_model_snapshot", {})),
            }
            runner.append_live_snapshot(final_snapshot)

        for result in runner.results:
            if isinstance(result, dict):
                result["orchestration_status"] = normalize_orchestration_status(result.get("failure_class"), result.get("orchestration_status", "ok"))
        runner.results = normalize_artifact_payload(runner.results, runner.results[0].get("task_id") if runner.results else None)
        runner.export_results()
    finally:
        await runner.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
