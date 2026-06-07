#!/usr/bin/env python3
"""Test submission runner for a small ARC puzzle batch."""


import argparse
import asyncio
import atexit
import datetime
import dataclasses
import enum
import importlib.util
import inspect
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import httpx
from sidequest_mcp_client.mcp_brain_client import MCPBrainClient
from benchmarks.arc3.world_model_eval import WorldModelEvaluator
from benchmarks.arc3.adapter import ARC3Adapter, NoOpBrainClient
from agents.arc3.trace_names import (
    canonical_phase,
    normalize_artifact_payload,
    normalize_failure_payload,
    normalize_orchestration_status,
)
from agents.arc3.runner import DurableARCRunner
from benchmarks.arc3.harness import ARC3Harness, load_tasks_from_manifest
from benchmarks.harness import BenchmarkConfig
from agents.arc4.executor import Executor
from agents.arc4.evaluator import Evaluator
from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.graph_queries import ArcGraphQueryPort
from agents.arc4.perceive import PerceiveAgent
from agents.arc4.plan_generator import PlanGenerator, PlanGeneratorLimits
from agents.arc4.plan_vetter import PlanVetter
from agents.arc4.telemetry import ArcV2Telemetry
from agents.arc4.workflow import WorkflowLimits, WorkflowOrchestrator
from agents.arc4.ports import WorkflowDependencies
from agents.arc4.types import WorkflowState, WorkflowRunResult
from sidequest_mcp_client.observability import build_observability
from arc_runtime.config import load_config
from arc_runtime.llm import create_llm_client, LLMInitializationError
from sidequest_mcp_client.readiness import check_mcp_readiness, ReadinessError

# Guarded import for Temporal (optional dependency)
try:
    from agents.arc4.temporal_client import is_temporal_enabled, start_arc_workflow
    HAS_TEMPORAL = True
except ImportError:
    HAS_TEMPORAL = False

# Configuration paths
REPO_ROOT = Path(__file__).resolve().parents[0]
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

# B204: classification sets for timeline visibility
SIDEQUESTS_CALLS = {
    "notify_turn",
    "current_truth",
    "recall_lessons",
    "recall_plans",
    "analogical_search",
    "register_plan",
    "report_outcome",
    "recall_procedures",
    "get_knowledge_gaps",
    "branch_quest",
    "upsert_lesson",
    "explore_graph",
    "reconstruct_timeline",
}
ARC_API_CALLS = {"arc_api_action", "RESET", "ACTION1", "ACTION2", "ACTION3", "ACTION4", "ACTION5", "ACTION6"}

logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def _json_default(obj):
    """Best-effort JSON adapter for telemetry/result payloads."""
    if dataclasses.is_dataclass(obj):
        return dataclasses.asdict(obj)
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj, key=str)
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return str(obj)


def _json_dumps(obj, **kwargs) -> str:
    return json.dumps(obj, default=_json_default, **kwargs)


def _atomic_dump_json(path: Path, obj) -> None:
    """Write JSON atomically: write to <path>.tmp, fsync, then os.replace into place.

    A022: prevents truncated or partial trace files when the process is killed
    mid-write. os.replace is atomic on POSIX when src and dst are on the same
    filesystem (they are here — both sit under REPO_ROOT).
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Ensure parent exists
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=_json_default)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            # Best-effort fsync; some environments may not support it.
            pass
    os.replace(tmp, path)


def _apply_llm_overrides(config: dict, overrides: dict | None = None) -> dict:
    """Return a config copy with one-shot LLM overrides applied."""
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
    """Delete the local smoke-test database and any SQLite/Kùzu sidecars."""
    import shutil

    candidates = [
        db_path,
        Path(f"{db_path}.wal"),
        Path(f"{db_path}.shm"),
        Path(f"{db_path}-wal"),
        Path(f"{db_path}-shm"),
    ]
    for candidate in candidates:
        if candidate.exists():
            if candidate.is_dir():
                shutil.rmtree(candidate)
            else:
                candidate.unlink()


def _ensure_arc_api_key(arc_key_path: str | Path | None = None) -> str | None:
    """Populate ARC_API_KEY from the repo credential file when the env var is absent."""
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
    """Fail fast when observability is enabled but runtime cannot emit traces.

    A016: May mutate config by setting observability.enabled = True and
    may set os.environ["PHOENIX_ENABLE"] = "1" if auto-enabling. A022 softens
    the auto-enable path so that Phoenix unreachability does not abort a smoke
    run when auto-enabled; an explicit `PHOENIX_ENABLE=1` still makes failures
    fatal.
    """
    obs_cfg = config.get("observability", {}) if isinstance(config, dict) else {}
    explicit_enabled = None
    if isinstance(obs_cfg, dict) and "enabled" in obs_cfg:
        explicit_enabled = bool(obs_cfg["enabled"])

    if explicit_enabled is False:
        # User explicitly disabled; respect it
        return

    auto_enabled = False

    if explicit_enabled is None:
        # Not set — probe dependencies and auto-enable if available
        all_present = (
            importlib.util.find_spec("opentelemetry") is not None
            and importlib.util.find_spec("phoenix") is not None
            and importlib.util.find_spec("phoenix.otel") is not None
        )
        if all_present:
            # Mark that A016 auto-enabled Phoenix so we can soft-fail later if
            # build_observability() fails. This sentinel is internal only.
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
            # Packages missing and user did not opt in — stay off silently
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
            raise RuntimeError(
                "Observability preflight failed: tracing could not be initialized."
            )
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
    """Fail fast when configured LLM provider cannot be initialized."""
    try:
        create_llm_client(config)
    except LLMInitializationError as exc:
        raise RuntimeError(f"LLM preflight failed: {exc}")


@dataclasses.dataclass(slots=True)
class ArcV2Bundle:
    graph_port: ArcGraphQueryPort
    telemetry: ArcV2Telemetry
    dependencies: WorkflowDependencies
    orchestrator: WorkflowOrchestrator
    llm_port: Any | None = None  # _SyncLLMPortAdapter, but defined later


class _SyncLLMPortAdapter:
    def __init__(self, llm_client: Any) -> None:
        self._llm_client = llm_client
        self.total_tokens_in = 0
        self.total_tokens_out = 0

    def chat(self, messages: list[Any]) -> str:
        if self._llm_client is None:
            raise RuntimeError("LLM client is not configured")

        # Convert LLMMessage objects to OpenAI-compatible message dicts
        message_dicts = [
            {"role": message.role, "content": message.content}
            for message in messages
        ]

        # Try async chat with proper message array first (OpenAI-compatible)
        achat = getattr(self._llm_client, "achat", None)
        if achat is not None:
            try:
                result = asyncio.get_event_loop().run_until_complete(
                    achat(message_dicts)
                )
                # Estimate tokens from prompt and response
                prompt_text = " ".join(m.get("content", "") for m in message_dicts)
                self.total_tokens_in += len(prompt_text) // 4
                response_str = str(result.content if hasattr(result, "content") else result)
                self.total_tokens_out += len(response_str) // 4
                if hasattr(result, "content"):
                    return str(result.content)
                return str(result)
            except RuntimeError:
                # Event loop already running — use asyncio.run in a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(asyncio.run, achat(message_dicts))
                    result = future.result(timeout=60)
                    # Estimate tokens from prompt and response
                    prompt_text = " ".join(m.get("content", "") for m in message_dicts)
                    self.total_tokens_in += len(prompt_text) // 4
                    response_str = str(result.content if hasattr(result, "content") else result)
                    self.total_tokens_out += len(response_str) // 4
                    if hasattr(result, "content"):
                        return str(result.content)
                    return str(result)
            except Exception:
                pass

        # Fallback: flatten to prompt string for generate/complete methods
        prompt = "\n".join(f"{m.role}: {m.content}" for m in messages)
        for method_name in ("generate", "complete", "chat"):
            method = getattr(self._llm_client, method_name, None)
            if method is None:
                continue
            try:
                result = method(prompt)
                if inspect.isawaitable(result):
                    result = asyncio.run(result)
                # Estimate tokens from prompt and response
                self.total_tokens_in += len(prompt) // 4
                response_str = str(result)
                self.total_tokens_out += len(response_str) // 4
                return response_str
            except Exception:
                return "{}"

        raise TypeError(
            "configured LLM client does not expose a sync-compatible "
            "generate/complete/chat/achat method"
        )


class _ArcV2GameSession:
    def __init__(self, harness: ARC3Harness, *, game_id: str, card_id: str, real_api: bool) -> None:
        self._harness = harness
        self._game_id = game_id
        self._card_id = card_id
        self._real_api = real_api
        self._client: httpx.Client | None = None
        self._guid: str | None = None

    def open(self) -> Mapping[str, Any]:
        if self._real_api:
            self._client = httpx.Client(
                base_url=self._harness.api_base,
                headers={"X-API-Key": str(self._harness.api_key or "")},
                timeout=30.0,
            )
            scorecard_resp = self._client.post("/api/scorecard/open", json={})
            scorecard_resp.raise_for_status()
            card_id = scorecard_resp.json().get("card_id") or self._card_id
            reset_resp = self._client.post("/api/cmd/RESET", json={"game_id": self._game_id, "card_id": card_id})
            reset_resp.raise_for_status()
            payload = reset_resp.json()
            self._guid = str(payload.get("guid") or "") or None
            return payload

        return self._harness._get_mock_initial_frame(self._game_id)

    def execute_action(self, action_id: str, payload: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._real_api:
            if self._client is None:
                raise RuntimeError("ARC v2 game session not opened")
            request_payload: dict[str, Any] = {"game_id": self._game_id, "guid": self._guid}
            request_payload.update(dict(payload))
            if action_id == "ACTION6":
                request_payload.setdefault("x", payload.get("x", 0))
                request_payload.setdefault("y", payload.get("y", 0))
            response = self._client.post(f"/api/cmd/{action_id}", json=request_payload)
            response.raise_for_status()
            frame_response = response.json()
            reward = 1.0 if frame_response.get("state") == "WIN" else 0.0
            done = frame_response.get("state") in ("WIN", "GAME_OVER")
            return {
                "observation": ARC3Adapter(NoOpBrainClient(), session_id=str(context.get("session_id") or "arc-v2"), task_id=self._game_id).normalize_observation(frame_response),
                "did_progress": reward >= 1.0,
                "actual_effect": frame_response.get("state") or frame_response.get("effect"),
                "reward": reward,
                "done": done,
                "state": frame_response.get("state"),
            }

        step = int(context.get("step") or 0)
        raw_action = {"action_id": action_id, **dict(payload)}
        frame_response, reward, done = self._harness._execute_mock_action(self._game_id, raw_action, step)
        return {
            "observation": ARC3Adapter(NoOpBrainClient(), session_id=str(context.get("session_id") or "arc-v2"), task_id=self._game_id).normalize_observation(frame_response),
            "did_progress": bool(reward),
            "actual_effect": frame_response.get("state") or frame_response.get("effect"),
            "reward": reward,
            "done": done,
            "state": frame_response.get("state"),
        }

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run ARC puzzles (optionally real API)")
    parser.add_argument("--real-api", action="store_true", help="Run against the real ARC-AGI-3 API")
    parser.add_argument(
        "--live-smoke",
        action="store_true",
        help=(
            "Convenience mode for a one-puzzle live smoke: implies --real-api, auto-loads ARC_API_KEY "
            "from the repo credential file when needed, and uses more forgiving local-Ollama timeout/retry defaults."
        ),
    )
    parser.add_argument("--agent-version", choices=("v1", "v2"), default="v2", help="Select the ARC agent implementation (default: v2)")
    parser.add_argument("--num-puzzles", type=int, default=None, help="Number of puzzles to run (default: 1 for real, 5 for mock)")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum steps per puzzle (overrides config)")
    parser.add_argument("--card-id", type=str, default=None, help="Override ARC checkpoint card id")
    parser.add_argument("--config", type=str, default=None, help="Explicit path to the campy.toml file to use for this run")
    parser.add_argument("--model", type=str, default=None, help="Override llm.model for this run only")
    parser.add_argument("--base-url", type=str, default=None, help="Override llm.base_url for this run only")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Override llm.timeout_seconds for this run only")
    parser.add_argument("--max-retries", type=int, default=None, help="Override llm.max_retries for this run only")
    parser.add_argument(
        "--arc-key-path",
        type=str,
        default=None,
        help="Load ARC_API_KEY from this JSON file if the environment variable is not already set",
    )
    parser.add_argument("--world-model-eval", action="store_true", help="Enable World Model architecture evaluation")
    parser.add_argument("--world-model-live-output", type=str, default="submission_results_single.world_model.live.jsonl", help="Path for live world model metrics")
    parser.add_argument("--temporal", action="store_true", help="Enable Temporal.io workflow dispatch (requires running Temporal server)")
    return parser


async def _run_arc_v2_batch(runner: "SingleTaskRunner", brain_client: Any, card_id: str, args: Any = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for task in runner.tasks:
        result = await asyncio.to_thread(_run_arc_v2_task, task, runner, card_id, brain_client, args)
        results.append(result)
    return results


def _build_arc_v2_bundle(
    *,
    task_id: str,
    game_id: str,
    game_title: str,
    game_tags: tuple[str, ...],
    brain_client: Any,
    session_id: str,
    append_snapshot: Callable[[dict[str, Any]], None] | None,
    game_session: _ArcV2GameSession,
    world_model_eval: bool,
    max_cycles: int,
    llm_client: Any | None = None,
) -> ArcV2Bundle:
    graph_port = ArcGraphQueryPort(brain_client, task_id=task_id, session_id=session_id, strict=False)
    telemetry = ArcV2Telemetry(
        task_id=task_id,
        game_id=game_id,
        game_title=game_title,
        game_tags=game_tags,
        append_snapshot=append_snapshot,
        world_model_eval=world_model_eval,
    )
    llm_port = _SyncLLMPortAdapter(llm_client) if llm_client is not None else None

    perceive = telemetry.wrap_phase("perceive", PerceiveAgent(graph_port).perceive)
    resolve_agent = GoalResolver(GoalResolverLimits())
    resolve = telemetry.wrap_phase(
        "resolve",
        lambda state, perception: resolve_agent.resolve(state, perception, graph_port=graph_port, llm_port=llm_port),
    )

    plan_agent = PlanGenerator(PlanGeneratorLimits())
    plan = telemetry.wrap_phase(
        "plan",
        lambda state, perception, goal: plan_agent.generate(state, perception, goal, graph_port=graph_port, llm_port=llm_port),
    )
    vet = telemetry.wrap_phase("vet", PlanVetter().vet)
    execute_agent = Executor(transport=game_session)
    execute = telemetry.wrap_phase(
        "execute",
        lambda state, perception, goal, vet_decision: execute_agent.execute(
            state,
            vet_decision.candidate or vet_decision.alternative,
            {
                "game_id": game_id,
                "guid": getattr(game_session, "_guid", None),
                "step": state.step_index,
                "session_id": session_id,
                "state": perception.observation.get("state") if isinstance(perception.observation, Mapping) else None,
            },
        ) if (vet_decision.candidate or vet_decision.alternative) is not None else PhaseResult(
            phase=WorkflowPhase.EXECUTE,
            status=PhaseStatus.CRASH,
            reason="missing_vetted_candidate",
            payload=None,
        ),
    )
    evaluate = telemetry.wrap_phase("evaluate", Evaluator(graph_query_port=graph_port).evaluate)

    dependencies = WorkflowDependencies(
        perceive=perceive,
        resolve=resolve,
        plan=plan,
        vet=vet,
        execute=execute,
        evaluate=evaluate,
    )
    orchestrator = WorkflowOrchestrator(dependencies, limits=WorkflowLimits(max_cycles=max_cycles))
    return ArcV2Bundle(graph_port=graph_port, telemetry=telemetry, dependencies=dependencies, orchestrator=orchestrator, llm_port=llm_port)


def _run_arc_v2_task(task: Any, runner: "SingleTaskRunner", card_id: str, brain_client: Any, args: Any = None) -> dict[str, Any]:
    session_id = f"arc-v2-{task.task_id}-{int(time.time())}"
    game_id = str(getattr(task, "game_id", "unknown"))
    game_title = str(getattr(task, "arc_game_title", "") or "")
    game_tags = tuple(str(tag) for tag in (getattr(task, "arc_game_tags", []) or []))
    game_session = _ArcV2GameSession(runner.harness, game_id=game_id, card_id=card_id, real_api=runner.real_api)
    initial_frame = game_session.open()
    adapter = ARC3Adapter(NoOpBrainClient(), session_id=session_id, task_id=task.task_id)
    observation = adapter.normalize_observation(initial_frame)

    bundle = _build_arc_v2_bundle(
        task_id=task.task_id,
        game_id=game_id,
        game_title=game_title,
        game_tags=game_tags,
        brain_client=brain_client,
        session_id=session_id,
        append_snapshot=runner.append_live_snapshot,
        game_session=game_session,
        world_model_eval=runner.world_model_eval,
        max_cycles=int(runner.config.get("benchmark", {}).get("max_attempts_per_puzzle", 3) or 3),
        llm_client=create_llm_client(runner.config),
    )

    # Determine whether to use Temporal dispatch
    use_temporal = HAS_TEMPORAL and (is_temporal_enabled() or (args and getattr(args, "temporal", False)))

    if use_temporal:
        # Dispatch through Temporal if enabled and reachable
        try:
            import asyncio as _asyncio
            handle = _asyncio.get_event_loop().run_until_complete(
                start_arc_workflow(task.task_id, observation, WorkflowState().to_dict())
            )
            if handle is not None:
                # Temporal was reachable; wait for result
                result_dict = _asyncio.get_event_loop().run_until_complete(handle.result())
                workflow_result = WorkflowRunResult.from_dict(result_dict)
                logger.info(f"Puzzle {task.task_id} completed via Temporal workflow")
            else:
                # Temporal unavailable; fall back to inline execution
                logger.info(f"Temporal unavailable, falling back to inline execution for {task.task_id}")
                workflow_result = bundle.orchestrator.run(WorkflowState(), observation)
        except Exception as exc:
            # Unexpected error during Temporal dispatch; fall back to inline
            logger.warning(f"Temporal dispatch failed for {task.task_id}: {exc}; falling back to inline execution")
            workflow_result = bundle.orchestrator.run(WorkflowState(), observation)
    else:
        # Inline execution path (default)
        workflow_result = bundle.orchestrator.run(WorkflowState(), observation)

    # Extract token counts from LLM adapter
    if bundle.llm_port is not None:
        bundle.telemetry.tokens_input = bundle.llm_port.total_tokens_in
        bundle.telemetry.tokens_output = bundle.llm_port.total_tokens_out

    result = bundle.telemetry.build_final_result(workflow_result)
    game_session.close()
    return result


class SingleTaskRunner:
    def __init__(self, real_api=False, config_path: str | Path | None = None, llm_overrides: dict | None = None, max_steps: int | None = None, live_smoke: bool = False):
        resolved_config_path = (
            Path(config_path)
            if config_path
            else (CONFIG_PATH if CONFIG_PATH.exists() else (LEGACY_CONFIG_PATH if LEGACY_CONFIG_PATH.exists() else None))
        )
        self.config = _apply_llm_overrides(load_config(resolved_config_path), llm_overrides)
        if max_steps is not None:
            if "benchmark" not in self.config:
                self.config["benchmark"] = {}
            self.config["benchmark"]["max_attempts_per_puzzle"] = max_steps
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
        logger.info("Initializing Single Task Runner...")

        # Production startup: verify the HippoCampy MCP service is ready
        required_tools = [
            "notify_turn",
            "current_truth",
            "register_plan",
            "report_outcome",
            "recall_plans",
            "recall_relevant_lessons",
            "upsert_lesson",
        ]
        try:
            check_mcp_readiness(
                required_tools=required_tools,
                require_brain_socket=True,
                probe_memory_backend=True,
                require_roundtrip_persistence=True,
            )
        except ReadinessError as exc:
            raise RuntimeError(str(exc))

        # Do not bootstrap local Kuzu/schema/loop in production startup. Use
        # MCP-backed brain client instead; keep `self.db` as None.
        self.db = None

        # 6. Initialize Harness
        # A039: Right-size timeout budget for live smokes.
        timeout_budget = 7200 if self.live_smoke else 3600
        benchmark_config = BenchmarkConfig(
            name="ARC-AGI-3",
            description="Single puzzle test",
            timeout=timeout_budget,
            memory_limit_gb=8.0,
            cpu_limit_percent=80.0,
            parameters=self.config.get("benchmark", {})
        )
        self.harness = ARC3Harness(benchmark_config, db=self.db, mock_api=not self.real_api)
        await self.harness.setup()

        # 7. Load all tasks (main() will slice)
        if MANIFEST_PATH.exists():
            self.tasks = load_tasks_from_manifest(str(MANIFEST_PATH))
            logger.info(f"Loaded {len(self.tasks)} task(s) from manifest.")
        else:
            logger.warning(f"Manifest not found at {MANIFEST_PATH}. Running with empty task set.")

        if self.real_api and self.tasks:
            live_games = await self.harness.list_games()
            if not live_games:
                raise RuntimeError("Live ARC API returned no games.")

            usable_count = min(len(self.tasks), len(live_games))
            for task, game in zip(self.tasks[:usable_count], live_games[:usable_count]):
                game_id = game["game_id"]
                setattr(task, "game_id", game_id)
                setattr(task, "arc_game_title", game.get("title"))
                setattr(task, "arc_game_tags", list(game.get("tags") or []))
                task.prompt = f"Solve live ARC puzzle {game_id}"

            logger.info(
                "Mapped %d manifest task(s) onto live ARC game ids. First game: %s",
                usable_count,
                getattr(self.tasks[0], "game_id", "unknown"),
            )

    def reset_live_output(self):
        self.live_output_path.write_text("")
        if self.world_model_eval:
            self.world_model_evaluator.reset()
            self.world_model_live_output_path.write_text("")

    def append_live_snapshot(self, snapshot: dict):
        normalized = dict(snapshot or {})
        normalized.setdefault(
            "timestamp_iso",
            datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        normalized = normalize_artifact_payload(normalized, normalized.get("task_id"))
        with open(self.live_output_path, "a") as f:
            f.write(_json_dumps(normalized) + "\n")

        # A078: World Model Live Stream
        if self.world_model_eval:
            try:
                task_id = normalized.get("task_id", "unknown")
                step = normalized.get("step", normalized.get("total_steps", 0))
                
                if normalized.get("snapshot_type") == "final_result":
                    summary_row = self.world_model_evaluator.build_summary_row(task_id, normalized)
                    with open(self.world_model_live_output_path, "a") as f:
                        f.write(_json_dumps(summary_row.to_dict()) + "\n")
                elif normalized.get("snapshot_type") == "world_model_decision":
                    decision_row = self.world_model_evaluator.build_decision_row(task_id, normalized)
                    with open(self.world_model_live_output_path, "a") as f:
                        f.write(_json_dumps(decision_row.to_dict()) + "\n")
                elif "world_model_node_count" in normalized:
                    step_row = self.world_model_evaluator.build_step_row(task_id, step, normalized)
                    with open(self.world_model_live_output_path, "a") as f:
                        f.write(_json_dumps(step_row.to_dict()) + "\n")
            except Exception:
                logger.debug("A078: Failed to emit world model live metrics", exc_info=True)

    @staticmethod
    def _phase_question_for_export(phase: str | None) -> str | None:
        phase = canonical_phase(str(phase or "setup"))
        mapping = {
            "setup": "What run setup or memory context is being prepared?",
            "perceive": "What am I seeing in the puzzle right now?",
            "model": "What world model or structure explains this board?",
            "plan": "What strategy, chunk, or experiment should I follow next?",
            "act": "What exact action should I take now?",
            "evaluate": "What changed, and did that action help?",
            "replan": "Why am I stuck, and which earlier phase should I return to?",
            "summary": "What game was played, and where can I inspect it?",
        }
        return mapping.get(phase)

    @staticmethod
    def _phase_answer_for_export(phase: str | None, payload: dict | None, fallback: str | None = None) -> str | None:
        if not isinstance(payload, dict):
            return fallback
        phase_name = canonical_phase(str(phase or "setup"))
        if phase_name == "replan":
            return payload.get("result_summary") or payload.get("input_summary") or fallback
        if phase_name == "evaluate":
            return payload.get("result_summary") or fallback or payload.get("input_summary")
        if phase_name == "act":
            return payload.get("input_summary") or payload.get("result_summary") or fallback
        return payload.get("result_summary") or payload.get("input_summary") or fallback

    @staticmethod
    def _make_final_result_compact(result: dict) -> dict:
        """A088: Compact final result by removing large fields and preserving high-signal summaries."""
        run_review = SingleTaskRunner._build_run_review(result)
        failure_payload = normalize_failure_payload(result)
        failure_class = result.get("failure_class")
        existing_evals = result.get("evals") if isinstance(result.get("evals"), dict) else {}
        component_eval = existing_evals.get("component_eval") if isinstance(existing_evals.get("component_eval"), dict) else {}
        orchestration_status = normalize_orchestration_status(
            failure_class,
            component_eval.get("orchestration_status") or result.get("orchestration_status", "ok"),
        )
        compact = {
            "task_id": result.get("task_id"),
            "game_id": result.get("game_id"),
            "game_title": result.get("game_title"),
            "game_tags": result.get("game_tags", []),
            "correct": result.get("correct"),
            "steps": result.get("steps"),
            "runtime_seconds": result.get("runtime_seconds"),
            "failure_class": failure_class,
            "failure_reason": failure_payload.get("failure_reason"),
            "exception_type": failure_payload.get("exception_type"),
            "exception_message": failure_payload.get("exception_message"),
            "orchestration_status": orchestration_status,
            "final_state": result.get("final_state"),
            "puzzle_description": run_review.get("puzzle_description"),
            "arc_game_url": run_review.get("arc_game_url"),
            "test_results_url": run_review.get("test_results_url"),
            "solve_phase_summary": result.get("solve_phase_summary", {}),
            "run_review": run_review,
        }
        
        # A088: Preserve world model summary (not full snapshot)
        world_model_snapshot = result.get("world_model_snapshot", {})
        if world_model_snapshot:
            compact["world_model_summary"] = SingleTaskRunner._summarize_world_model_snapshot(world_model_snapshot)
        
        # A088: Add artifact path references instead of full payloads
        artifacts = {}
        if result.get("has_execution_trace") or result.get("agent_execution_trace"):
            artifacts["agent_execution_trace"] = "agent_execution_trace.json"
        if result.get("has_timeline") or result.get("arc_event_timeline") or result.get("chronological_log"):
            artifacts["master_timeline"] = "master_timeline.json"
        artifacts["world_model_live"] = "submission_results_single.world_model.live.jsonl"
        
        if artifacts:
            compact["artifacts"] = artifacts
        
        # Include metrics if available
        if "evals" in result:
            compact["evals"] = result["evals"]
        if "quality_dimensions" in result:
            compact["quality_dimensions"] = result["quality_dimensions"]
        
        return compact

    @staticmethod
    def _summarize_world_model_snapshot(snapshot: dict) -> dict:
        """Return bounded world-model counts without embedding full node/edge payloads."""
        if not isinstance(snapshot, dict):
            return {}
        return {
            "node_count": snapshot.get("node_count", 0),
            "edge_count": snapshot.get("edge_count", 0),
            "contradiction_count": snapshot.get("contradiction_count", 0),
            "demotion_count": snapshot.get("demotion_count", 0),
        }

    @staticmethod
    def _game_slug(result: dict) -> str:
        title = str(result.get("game_title") or "").strip()
        if title:
            return title.lower()
        game_id = str(result.get("game_id") or "").strip()
        if game_id and game_id != "unknown":
            return game_id.split("-", 1)[0].lower()
        return ""

    @staticmethod
    def _artifact_url(path: str | Path) -> str:
        try:
            return Path(path).resolve().as_uri()
        except Exception:
            return str(path)

    @staticmethod
    def _build_run_review(result: dict, results_path: str | Path | None = None) -> dict:
        game_id = str(result.get("game_id") or "unknown")
        title = str(result.get("game_title") or SingleTaskRunner._game_slug(result) or game_id).upper()
        tags = [str(tag) for tag in (result.get("game_tags") or []) if tag]
        controls = ", ".join(tags) if tags else "unknown controls"
        steps = int(result.get("steps", 0) or 0)
        final_state = str(result.get("final_state") or "unknown")
        failure_class = str(result.get("failure_class") or "none")
        correct = bool(result.get("correct") is True)
        slug = SingleTaskRunner._game_slug(result)
        arc_game_url = f"https://arcprize.org/tasks/{slug}" if slug else "https://arcprize.org/tasks"
        result_file = FINAL_OUTPUT_PATH
        current_artifact = Path(results_path) if results_path else FINAL_OUTPUT_PATH
        sentence = (
            f"Played ARC-AGI-3 task {title} ({game_id}), a {controls} game; "
            f"the smoke test ended {'solved' if correct else 'unsolved'} after {steps} step(s) "
            f"with final_state={final_state} and failure_class={failure_class}."
        )
        return {
            "puzzle_description": sentence,
            "arc_game_url": arc_game_url,
            "test_results_url": SingleTaskRunner._artifact_url(result_file),
            "current_artifact_url": SingleTaskRunner._artifact_url(current_artifact),
            "artifact_urls": {
                "submission_results_single": SingleTaskRunner._artifact_url(FINAL_OUTPUT_PATH),
                "submission_results_single_live": SingleTaskRunner._artifact_url(LIVE_OUTPUT_PATH),
                "submission_results_single_world_model_live": SingleTaskRunner._artifact_url(REPO_ROOT / "submission_results_single.world_model.live.jsonl"),
                "agent_execution_trace": SingleTaskRunner._artifact_url(AGENT_EXECUTION_TRACE_PATH),
                "master_timeline": SingleTaskRunner._artifact_url(MASTER_TIMELINE_PATH),
            },
        }

    def export_results(self):
        output_path = self.final_output_path
        logger.info(f"Exporting results to {output_path}")
        for result in self.results:
            if isinstance(result, dict):
                result.setdefault("run_review", self._build_run_review(result, output_path))
                failure_class = result.get("failure_class")
                result["orchestration_status"] = normalize_orchestration_status(
                    failure_class,
                    result.get("orchestration_status", "ok"),
                )
                if failure_class == "crash":
                    failure_payload = normalize_failure_payload(result)
                    for key, value in failure_payload.items():
                        if value is not None and result.get(key) in (None, ""):
                            result[key] = value
        results_for_export = (
            [self._make_final_result_compact(result) for result in self.results]
            if (self.live_smoke or self.world_model_eval)
            else self.results
        )
        canonical_task_id = self.results[0].get("task_id") if self.results and isinstance(self.results[0], dict) else None
        _atomic_dump_json(output_path, normalize_artifact_payload(results_for_export, canonical_task_id))

        # Chronological timeline of function calls + ARC API request/response events.
        call_timeline = []
        for result in self.results:
            for entry in result.get("sidequests_ledger", []) or []:
                if not isinstance(entry, dict):
                    continue

                call_type = entry.get("call_type")
                timestamp = entry.get("timestamp_iso")
                if not call_type or not timestamp:
                    continue

                # ARC API request/response are emitted from arc_server_responses below.
                if call_type == "arc_api_action":
                    continue

                name = str(call_type)

                # Classify call type for timeline visibility (B204)
                if call_type in SIDEQUESTS_CALLS:
                    event_detail_classified = "SideQuests memory/planning call"
                elif call_type in ARC_API_CALLS:
                    event_detail_classified = "ARC API interaction"
                else:
                    event_detail_classified = "internal orchestration"

                phase = canonical_phase(entry.get("phase") or "setup")
                call_timeline.append(
                    {
                        "name": name,
                        "event": "call",
                        "data": entry,
                        "timestamp_iso": timestamp,
                        "event_detail": event_detail_classified,
                        "what": entry.get("input_summary") or entry.get("result_summary") or name,
                        "phase": phase,
                        "phase_question": self._phase_question_for_export(phase),
                        "phase_answer": self._phase_answer_for_export(
                            phase,
                            entry,
                            entry.get("result_summary") or entry.get("input_summary") or name,
                        ),
                    }
                )

            for response in result.get("arc_server_responses", []) or []:
                if not isinstance(response, dict):
                    continue

                request = response.get("request", {}) if isinstance(response.get("request"), dict) else {}
                reply = response.get("response", {}) if isinstance(response.get("response"), dict) else {}

                endpoint = request.get("endpoint")
                if isinstance(endpoint, str) and endpoint:
                    op_name = endpoint.rsplit("/", 1)[-1].upper().replace("/", "_")
                else:
                    op_name = str(request.get("label") or "ARC_CALL")

                request_ts = request.get("timestamp_iso")
                if isinstance(request_ts, str) and request_ts:
                    method = request.get("method")
                    if isinstance(method, str) and method:
                        what_request = f"{method} {endpoint}" if isinstance(endpoint, str) else method
                    else:
                        what_request = request.get("label") or op_name
                    call_timeline.append(
                        {
                            "name": op_name,
                            "event": "request",
                            "data": request,
                            "timestamp_iso": request_ts,
                            "event_detail": "ARC API request",
                            "what": what_request,
                        }
                    )

                response_ts = reply.get("timestamp_iso")
                if isinstance(response_ts, str) and response_ts:
                    response_summary = reply.get("response_summary")
                    if not response_summary:
                        http_status = reply.get("http_status")
                        response_summary = f"http_status={http_status}" if http_status is not None else "response received"
                    call_timeline.append(
                        {
                            "name": op_name,
                            "event": "response",
                            "data": reply,
                            "timestamp_iso": response_ts,
                            "event_detail": "ARC API response",
                            "what": response_summary,
                        }
                    )

        def _sort_key(item: dict) -> tuple:
            ts = item.get("timestamp_iso")
            if isinstance(ts, str) and ts:
                try:
                    parsed = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    return (0, parsed.timestamp(), str(item.get("name", "")))
                except Exception:
                    pass

            runtime = None
            if isinstance(item.get("data"), dict):
                runtime = item["data"].get("runtime_seconds")
            if runtime is None:
                runtime = item.get("runtime_seconds")
            if isinstance(runtime, (int, float)):
                return (1, float(runtime), str(item.get("name", "")))
            return (2, float("inf"), str(item.get("name", "")))

        call_timeline.sort(key=_sort_key)
        if self.results:
            review = self._build_run_review(self.results[0], self.arc_server_output_path)
            call_timeline.append(
                {
                    "name": "run_review",
                    "event": "summary",
                    "data": review,
                    "timestamp_iso": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                    "event_detail": "run review summary",
                    "what": review["puzzle_description"],
                    "phase": "summary",
                    "phase_question": "What game was played, and where can I inspect it?",
                    "phase_answer": review["puzzle_description"],
                }
            )

        logger.info(f"Exporting ARC-only responses to {self.arc_server_output_path}")
        call_timeline = normalize_artifact_payload(call_timeline, canonical_task_id)
        _atomic_dump_json(self.arc_server_output_path, call_timeline)

        # B131: Export agent execution trace (CloudWatch-style logs)
        agent_execution_trace = []
        for result in self.results:
            trace_events = result.get("agent_execution_trace", []) or []
            agent_execution_trace.extend(trace_events)
        
        # Sort by timestamp
        agent_execution_trace.sort(key=lambda e: e.get("timestamp_iso", ""))
        agent_execution_trace = normalize_artifact_payload(agent_execution_trace, canonical_task_id)
        
        logger.info(f"Exporting agent execution trace to {self.agent_execution_trace_path}")
        if self.results:
            review = self._build_run_review(self.results[0], self.agent_execution_trace_path)
            agent_execution_trace.append(
                {
                    "timestamp_iso": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                    "event_type": "operation",
                    "operation": "run_review",
                    "details": review,
                    "result": {"description": review["puzzle_description"]},
                    "elapsed_ms": None,
                }
            )
        _atomic_dump_json(self.agent_execution_trace_path, agent_execution_trace)

        timeline_base_dt = None
        for candidate in [*call_timeline, *agent_execution_trace]:
            ts = candidate.get("timestamp_iso") if isinstance(candidate, dict) else None
            if not isinstance(ts, str) or not ts:
                continue
            try:
                parsed = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
            if timeline_base_dt is None or parsed < timeline_base_dt:
                timeline_base_dt = parsed
        if timeline_base_dt is None:
            timeline_base_dt = datetime.datetime.now(datetime.timezone.utc)

        # B131: Export master timeline — all events from both streams merged chronologically.
        master_timeline = []
        for event in call_timeline:
            # Determine source based on event type and call_type (B204)
            event_type = event.get("event")
            call_type_for_source = (event.get("data") or {}).get("call_type") or event.get("name", "")
            if event_type in ("request", "response"):
                source = "arc_api"
            elif call_type_for_source in SIDEQUESTS_CALLS:
                source = "sidequests"
            else:
                source = "arc_server"

            master_timeline.append({
                "source": source,
                "timestamp_iso": event.get("timestamp_iso"),
                "name": event.get("name"),
                "event": event.get("event"),
                "what": event.get("what"),
                "phase": canonical_phase(event.get("phase") or ((event.get("data") or {}).get("phase") if isinstance(event.get("data"), dict) else "setup")),
                "phase_question": event.get("phase_question"),
                "phase_answer": event.get("phase_answer"),
                "event_detail": event.get("event_detail"),
                "data": event.get("data"),
            })
        for event in agent_execution_trace:
            details = event.get("details") or {}
            operation = str(event.get("operation") or "")
            phase = details.get("phase")
            if not phase:
                op_map = {
                    "perceive": "perceive",
                    "plan": "model",
                    "hypothesize": "model",
                    "solve": "plan",
                    "act": "act",
                    "ingest": "evaluate",
                    "replan": "replan",
                }
                phase = op_map.get(operation)
            phase = canonical_phase(phase or "setup")
            what = (
                (event.get("result") or {}).get("action_id")
                or str(details.get("action_taken", ""))
                or event.get("operation", "")
            )
            master_timeline.append({
                "source": "agent_trace",
                "timestamp_iso": event.get("timestamp_iso"),
                "name": event.get("operation"),
                "event": event.get("event_type"),
                "what": what,
                "phase": phase,
                "phase_question": self._phase_question_for_export(phase),
                "phase_answer": self._phase_answer_for_export(phase, details if isinstance(details, dict) else {}, what),
                "event_detail": f"{event.get('event_type')} — {event.get('operation')}",
                "details": details,
                "result": event.get("result"),
                "elapsed_ms": event.get("elapsed_ms"),
            })

        if self.live_output_path.exists():
            for raw_line in self.live_output_path.read_text().splitlines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    snapshot = json.loads(raw_line)
                except Exception:
                    continue
                if snapshot.get("snapshot_type") != "phase_transition":
                    continue

                snapshot = normalize_artifact_payload(snapshot, canonical_task_id)
                from_phase = snapshot.get("from_phase")
                to_phase = snapshot.get("to_phase")
                snapshot_ts = snapshot.get("timestamp_iso")
                if not snapshot_ts:
                    runtime_seconds = snapshot.get("runtime_seconds")
                    if isinstance(runtime_seconds, (int, float)):
                        snapshot_ts = (
                            timeline_base_dt + datetime.timedelta(seconds=float(runtime_seconds))
                        ).isoformat().replace("+00:00", "Z")
                    else:
                        snapshot_ts = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
                master_timeline.append({
                    "source": "live_snapshot",
                    "timestamp_iso": snapshot_ts,
                    "name": "phase_transition",
                    "event": "phase_transition",
                    "what": f"{from_phase} -> {to_phase}",
                    "phase": snapshot.get("current_phase") or to_phase,
                    "phase_question": snapshot.get("phase_question"),
                    "phase_answer": snapshot.get("phase_answer"),
                    "event_detail": "standalone phase transition snapshot",
                    "data": snapshot,
                    "runtime_seconds": snapshot.get("runtime_seconds"),
                })

        master_timeline.sort(key=_sort_key)
        if self.results:
            review = self._build_run_review(self.results[0], self.master_timeline_path)
            master_timeline.append(
                {
                    "source": "run_review",
                    "timestamp_iso": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
                    "name": "run_review",
                    "event": "summary",
                    "what": review["puzzle_description"],
                    "phase": "summary",
                    "phase_question": "What game was played, and where can I inspect it?",
                    "phase_answer": review["puzzle_description"],
                    "event_detail": "run review summary",
                    "data": review,
                }
            )

        logger.info(f"Exporting master timeline to {self.master_timeline_path}")
        master_timeline = normalize_artifact_payload(master_timeline, canonical_task_id)
        _atomic_dump_json(self.master_timeline_path, master_timeline)

    async def shutdown(self):
        """Tear down background resources so the runner exits cleanly."""
        from sidequest_mcp_client.observability import build_observability
        build_observability(self.config).shutdown()

        if self.harness is not None:
            await self.harness.teardown()
            self.harness = None

        if self.db is not None:
            self.db.close()
            self.db = None


def _emergency_shutdown(runner: DurableARCRunner):
    """atexit handler to atomically save trace data on unexpected exit."""
    if not runner or not hasattr(runner, "_current_trace_snapshot") or not runner._current_trace_snapshot:
        return

    try:
        path = Path(AGENT_EXECUTION_TRACE_PATH)
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        with open(temp_path, "w") as f:
            json.dump(runner._current_trace_snapshot, f, indent=2, default=_json_default)
        # Check for and create the directory if it doesn't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp_path, path)
        # fsync is not available on all platforms, and might not be needed
        # with modern filesystems, but can be added for extra durability.
        # if hasattr(os, "fsync"):
        #     with open(path, "r") as f:
        #         os.fsync(f.fileno())
        logger.info("Emergency shutdown: successfully saved agent execution trace.")
    except Exception:
        # Avoid crashing inside the crash handler
        logger.exception("Emergency shutdown failed to save trace data.")


async def main():
    parser = build_arg_parser()
    args = parser.parse_args()

    real_api = args.real_api or args.live_smoke

    llm_overrides = {
        key: value
        for key, value in {
            "model": args.model,
            "base_url": args.base_url,
            "timeout_seconds": args.timeout_seconds,
            "max_retries": args.max_retries,
        }.items()
        if value is not None
    }
    if args.live_smoke:
        # A039: increase per-call timeout and max retries for local Ollama smokes.
        llm_overrides.setdefault("timeout_seconds", 600.0)
        llm_overrides.setdefault("max_retries", 5)

    if real_api and not _ensure_arc_api_key(args.arc_key_path):
        logger.warning(
            "ARC_API_KEY was not found in the environment or repo credential files; the live run may fail to authenticate."
        )

    # Determine number of puzzles
    if args.num_puzzles is not None:
        num_puzzles = args.num_puzzles
    else:
        num_puzzles = 1 if real_api else TASK_BATCH_SIZE

    runner = SingleTaskRunner(
        real_api=real_api, 
        config_path=args.config, 
        llm_overrides=llm_overrides, 
        max_steps=args.max_steps, 
        live_smoke=bool(args.live_smoke)
    )
    runner.world_model_eval = bool(args.world_model_eval)
    if args.world_model_live_output:
        runner.world_model_live_output_path = Path(args.world_model_live_output)
        
    try:
        await runner.initialize()

        # Override loaded tasks to first N
        if runner.tasks:
            runner.tasks = runner.tasks[:num_puzzles]

        if not runner.tasks:
            logger.error("No tasks to run!")
            return

        if isinstance(runner.config, dict):
            runner.config["require_submission_artifacts"] = True

        if args.card_id:
            card_id = args.card_id
        elif real_api:
            # Live smoke runs should always produce a fresh artifact set.
            card_id = f"real_test_{int(time.time())}"
        else:
            # Local ad-hoc runs are typically used to refresh observability artifacts,
            # so avoid silently reusing a cached checkpoint unless the caller passed
            # an explicit `--card-id`.
            card_id = f"local_test_{int(time.time())}"
        brain_client = MCPBrainClient(runner.db, runner.config)
        runner.reset_live_output()
        llm_cfg = runner.config.get("llm", {})
        logger.info(
            "Running %d puzzle(s) with %s, starting with: %s | provider=%s model=%s timeout=%s retries=%s",
            len(runner.tasks),
            args.agent_version,
            runner.tasks[0].task_id,
            llm_cfg.get("provider"),
            llm_cfg.get("model"),
            llm_cfg.get("timeout_seconds", "default"),
            llm_cfg.get("max_retries", "default"),
        )
        if args.agent_version == "v2":
            runner.results = await _run_arc_v2_batch(runner, brain_client, card_id, args)
        else:
            durable = DurableARCRunner(
                runner.harness,
                brain_client,
                runner.config,
                progress_callback=runner.append_live_snapshot,
            )
            durable._emit_transition_snapshots = True
            atexit.register(_emergency_shutdown, durable)
            runner.results = await durable.run(runner.tasks, card_id)

        for result in runner.results:
            run_review = SingleTaskRunner._build_run_review(result, runner.final_output_path)
            # A088: Build compact final result for live stream
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
                "world_model_snapshot": SingleTaskRunner._summarize_world_model_snapshot(
                    result.get("world_model_snapshot", {})
                ),
            }
            runner.append_live_snapshot(final_snapshot)

        # Print result summary
        for idx, result in enumerate(runner.results):
            logger.info(f"Task {idx+1}: {result.get('task_id')}")
            result_metadata = result.get('metadata', {}) if isinstance(result, dict) else {}
            logger.info(f"  Correct: {result_metadata.get('correct', result.get('correct') if isinstance(result, dict) else None)}")
            logger.info(f"  Steps: {result_metadata.get('steps', result.get('steps') if isinstance(result, dict) else None)}")

            solve_summary = result.get("solve_phase_summary") or result.get("metadata", {}).get("solve_phase_summary") or {}
            if solve_summary:
                logger.info(f"  [SOLVE] archetype: {solve_summary.get('final_archetype')} ({solve_summary.get('final_archetype_confidence', 0):.0%})")
                logger.info(f"  [SOLVE] victory: {solve_summary.get('final_victory_condition')} ({solve_summary.get('final_victory_confidence', 0):.0%})")
                logger.info(f"  [SOLVE] strategy: {solve_summary.get('final_strategy_summary', '')[:80]}")
                logger.info(f"  [SOLVE] dissonance: {solve_summary.get('dissonance_triggered')}")
                if solve_summary.get("archetype_evolution"):
                    logger.info(f"  [SOLVE] archetype evolution: {' → '.join(solve_summary['archetype_evolution'])}")

            error = result_metadata.get('error')
            if error:
                logger.error(f"  Error: {error}")
            else:
                logger.info("  ✅ No parameter binding error!")

        runner.export_results()
    finally:
        await runner.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
