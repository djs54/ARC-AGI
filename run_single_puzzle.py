#!/usr/bin/env python3
"""Thin CLI entrypoint and compatibility re-exports for ARC single-puzzle runs."""

from __future__ import annotations

import argparse
import asyncio
import atexit
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

from agents.common.trace_names import normalize_artifact_payload, normalize_orchestration_status
from arc_runtime import artifacts, dispatch as arc_dispatch, runner_shell as rs
from arc_runtime.bundle import ArcV2Bundle, SyncLLMPortAdapter, build_arc_v2_bundle
from arc_runtime.config import load_config
from arc_runtime.game_session import ArcV2GameSession, _compute_progress, _unwrap_arc_game_payload
from arc_runtime.llm import LLMInitializationError, create_llm_client
from benchmarks.arc3.world_model_eval import WorldModelEvaluator
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient
from sidequest_mcp_client.observability import build_observability

if TYPE_CHECKING:
    from agents.arc3.runner import DurableARCRunner

REPO_ROOT = rs.REPO_ROOT
CONFIG_PATH = rs.CONFIG_PATH
LEGACY_CONFIG_PATH = rs.LEGACY_CONFIG_PATH
MANIFEST_PATH = rs.MANIFEST_PATH
TASK_BATCH_SIZE = rs.TASK_BATCH_SIZE
FINAL_OUTPUT_PATH = rs.FINAL_OUTPUT_PATH
ARC_SERVER_OUTPUT_PATH = rs.ARC_SERVER_OUTPUT_PATH
AGENT_EXECUTION_TRACE_PATH = rs.AGENT_EXECUTION_TRACE_PATH
MASTER_TIMELINE_PATH = rs.MASTER_TIMELINE_PATH
LIVE_OUTPUT_PATH = rs.LIVE_OUTPUT_PATH
ARC_KEY_PATHS = rs.ARC_KEY_PATHS

logger = logging.getLogger(__name__)

_ArcV2GameSession = ArcV2GameSession
_build_arc_v2_bundle = build_arc_v2_bundle
_json_default = artifacts.json_default
_json_dumps = artifacts.json_dumps
_atomic_dump_json = artifacts.atomic_dump_json
HAS_TEMPORAL = arc_dispatch.HAS_TEMPORAL


_apply_llm_overrides = rs._apply_llm_overrides
_remove_db_artifacts = rs._remove_db_artifacts
_ensure_arc_api_key = rs._ensure_arc_api_key


def _enforce_observability_preflight(config: dict) -> None:
    obs_cfg = config.get("observability", {}) if isinstance(config, dict) else {}
    explicit_enabled = None
    if isinstance(obs_cfg, dict) and "enabled" in obs_cfg:
        explicit_enabled = bool(obs_cfg["enabled"])

    if explicit_enabled is False:
        return

    auto_enabled = False

    if explicit_enabled is None:
        all_present = (
            importlib.util.find_spec("opentelemetry") is not None
            and importlib.util.find_spec("phoenix") is not None
            and importlib.util.find_spec("phoenix.otel") is not None
        )
        if all_present:
            os.environ["PHOENIX_ENABLE"] = "1"
            os.environ["_A016_AUTO_ENABLED_PHOENIX"] = "1"
            auto_enabled = True
            if isinstance(config, dict):
                config.setdefault("observability", {})["enabled"] = True
            obs_cfg = config.get("observability", {})
            logger.info(
                "Phoenix observability auto-enabled (PHOENIX_ENABLE=1, project=%s, endpoint=%s)",
                os.environ.get("PHOENIX_PROJECT", "arc-agi-campy"),
                os.environ.get("PHOENIX_ENDPOINT", "http://127.0.0.1:6006/v1/traces"),
            )
        else:
            return

    backend = str(obs_cfg.get("backend", "phoenix")).lower()
    if backend != "phoenix":
        raise RuntimeError(
            f"Observability preflight failed: unsupported backend '{backend}'. "
            "Use backend='phoenix' or disable [observability].enabled."
        )

    missing = []
    if importlib.util.find_spec("opentelemetry") is None:
        missing.append("opentelemetry")
    if importlib.util.find_spec("phoenix") is None:
        missing.append("phoenix")
    if importlib.util.find_spec("phoenix.otel") is None:
        missing.append("phoenix.otel")
    if missing:
        raise RuntimeError(
            "Observability preflight failed: required tracing packages are missing in this interpreter.\n"
            f"python_executable={sys.executable}\n"
            f"missing={', '.join(missing)}\n"
            "Fix: run the smoke test with the HippoCampy/Campy interpreter or install tracing deps into the current interpreter."
        )

    try:
        obs = build_observability(config)
        if not getattr(obs, "enabled", False):
            raise RuntimeError("Observability preflight failed: tracing could not be initialized.")
    except Exception as exc:
        if auto_enabled:
            logger.warning(
                "Phoenix auto-enable failed (%s); falling back to JSON-trace only. "
                "Set PHOENIX_ENABLE=1 explicitly to make this fatal.",
                exc,
            )
            os.environ.pop("PHOENIX_ENABLE", None)
            if isinstance(config.get("observability"), dict):
                config["observability"]["enabled"] = False
            return
        raise
    finally:
        if auto_enabled:
            os.environ.pop("_A016_AUTO_ENABLED_PHOENIX", None)


def _enforce_llm_preflight(config: dict) -> None:
    try:
        create_llm_client(config)
    except LLMInitializationError as exc:
        raise RuntimeError(f"LLM preflight failed: {exc}")


def build_arg_parser() -> argparse.ArgumentParser:
    return rs.build_arg_parser()


def _run_arc_v2_task(task: Any, runner: Any, card_id: str, brain_client: Any, args: Any = None) -> dict[str, Any]:
    previous_factory = arc_dispatch.create_llm_client
    arc_dispatch.create_llm_client = create_llm_client
    try:
        return arc_dispatch.run_arc_v2_task(task, runner, card_id, brain_client, args)
    finally:
        arc_dispatch.create_llm_client = previous_factory


async def _run_arc_v2_batch(runner: Any, brain_client: Any, card_id: str, args: Any = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for task in runner.tasks:
        result = await asyncio.to_thread(_run_arc_v2_task, task, runner, card_id, brain_client, args)
        results.append(result)
    return results


class SingleTaskRunner(rs.SingleTaskRunner):
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
            runner.results = await _run_arc_v2_batch(runner, brain_client, card_id, args)
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
