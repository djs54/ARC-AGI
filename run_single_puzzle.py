#!/usr/bin/env python3
"""Test submission runner for a small ARC puzzle batch."""


import argparse
import asyncio
import datetime
import importlib.util
import json
import logging
import os
import sys
import time
from pathlib import Path

from sidequest_mcp_client.mcp_brain_client import MCPBrainClient
from agents.arc3.runner import DurableARCRunner
from benchmarks.arc3.harness import ARC3Harness, load_tasks_from_manifest
from benchmarks.harness import BenchmarkConfig
from sidequest_mcp_client.observability import build_observability
from arc_runtime.config import load_config
from arc_runtime.llm import create_llm_client, LLMInitializationError
from sidequest_mcp_client.readiness import check_mcp_readiness, ReadinessError

# Configuration paths
REPO_ROOT = Path(__file__).resolve().parents[0]
CONFIG_PATH = REPO_ROOT / "sidequests.toml"
MANIFEST_PATH = REPO_ROOT / "benchmarks/arc3/tasks_manifest.json"
DB_PATH = Path.home() / ".sidequests" / "brain_single_test.db"
SEED_PATH = REPO_ROOT / "sidequests/data/GistSeedExamples.md"
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
    may set os.environ["PHOENIX_ENABLE"] = "1" if auto-enabling.
    """
    obs_cfg = config.get("observability", {}) if isinstance(config, dict) else {}
    explicit_enabled = None
    if isinstance(obs_cfg, dict) and "enabled" in obs_cfg:
        explicit_enabled = bool(obs_cfg["enabled"])

    if explicit_enabled is False:
        # User explicitly disabled; respect it
        return

    if explicit_enabled is None:
        # Not set — probe dependencies and auto-enable if available
        all_present = (
            importlib.util.find_spec("opentelemetry") is not None
            and importlib.util.find_spec("phoenix") is not None
            and importlib.util.find_spec("phoenix.otel") is not None
        )
        if all_present:
            os.environ["PHOENIX_ENABLE"] = "1"
            if isinstance(config, dict):
                config.setdefault("observability", {})["enabled"] = True
            obs_cfg = config.get("observability", {})
            logger.info(
                "Phoenix observability auto-enabled (PHOENIX_ENABLE=1, project=%s, endpoint=%s)",
                os.environ.get("PHOENIX_PROJECT", "arc-agi-sidequests"),
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
            "Fix: run the smoke test with /Users/djshelton/Desktop/GitProjects/sidequests-brain/.venv/bin/python "
            "or install tracing deps into the current interpreter."
        )

    obs = build_observability(config)
    if not obs.enabled:
        endpoint = str(obs_cfg.get("endpoint", "http://127.0.0.1:6006/v1/traces"))
        raise RuntimeError(
            "Observability preflight failed: tracing could not be initialized.\n"
            f"python_executable={sys.executable}\n"
            f"endpoint={endpoint}\n"
            "Fix: verify dependencies are installed in this interpreter and that Phoenix is reachable."
        )


def _enforce_llm_preflight(config: dict) -> None:
    """Fail fast when configured LLM provider cannot be initialized."""
    try:
        create_llm_client(config)
    except LLMInitializationError as exc:
        raise RuntimeError(f"LLM preflight failed: {exc}")


class SingleTaskRunner:
    def __init__(self, real_api=False, config_path: str | Path | None = None, llm_overrides: dict | None = None, max_steps: int | None = None):
        resolved_config_path = Path(config_path) if config_path else (CONFIG_PATH if CONFIG_PATH.exists() else None)
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
        self.live_output_path = LIVE_OUTPUT_PATH
        self.final_output_path = FINAL_OUTPUT_PATH
        self.arc_server_output_path = ARC_SERVER_OUTPUT_PATH
        self.agent_execution_trace_path = AGENT_EXECUTION_TRACE_PATH
        self.master_timeline_path = MASTER_TIMELINE_PATH

    async def initialize(self):
        logger.info("Initializing Single Task Runner...")

        # Clean up old database and stale sidecars from prior smoke runs.
        _remove_db_artifacts(DB_PATH)

        # Production startup: verify the SideQuests MCP service is ready
        required_tools = [
            "notify_turn",
            "current_truth",
            "register_plan",
            "report_outcome",
            "recall_plans",
        ]
        try:
            check_mcp_readiness(required_tools=required_tools)
        except ReadinessError as exc:
            raise RuntimeError(str(exc))

        # Do not bootstrap local Kuzu/schema/loop in production startup. Use
        # MCP-backed brain client instead; keep `self.db` as None.
        self.db = None

        # 6. Initialize Harness
        benchmark_config = BenchmarkConfig(
            name="ARC-AGI-3",
            description="Single puzzle test",
            timeout=3600,
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
                task.prompt = f"Solve live ARC puzzle {game_id}"

            logger.info(
                "Mapped %d manifest task(s) onto live ARC game ids. First game: %s",
                usable_count,
                getattr(self.tasks[0], "game_id", "unknown"),
            )

    def reset_live_output(self):
        self.live_output_path.write_text("")

    def append_live_snapshot(self, snapshot: dict):
        normalized = dict(snapshot or {})
        normalized.setdefault(
            "timestamp_iso",
            datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        with open(self.live_output_path, "a") as f:
            f.write(json.dumps(normalized) + "\n")

    @staticmethod
    def _phase_question_for_export(phase: str | None) -> str | None:
        mapping = {
            "perceive": "What am I seeing in the puzzle right now?",
            "model": "What world model or structure explains this board?",
            "hypothesize": "What kind of puzzle is this and what is the likely win condition?",
            "route": "What strategy or chunk should I follow next?",
            "execute": "What exact action should I take now?",
            "evaluate": "What changed, and did that action help?",
            "replan": "Why am I stuck, and which earlier phase should I return to?",
        }
        return mapping.get(str(phase or "").lower())

    @staticmethod
    def _phase_answer_for_export(phase: str | None, payload: dict | None, fallback: str | None = None) -> str | None:
        if not isinstance(payload, dict):
            return fallback
        phase_name = str(phase or "").lower()
        if phase_name == "replan":
            return payload.get("result_summary") or payload.get("input_summary") or fallback
        if phase_name == "evaluate":
            return payload.get("result_summary") or fallback or payload.get("input_summary")
        if phase_name == "execute":
            return payload.get("input_summary") or payload.get("result_summary") or fallback
        return payload.get("result_summary") or payload.get("input_summary") or fallback

    def export_results(self):
        output_path = self.final_output_path
        logger.info(f"Exporting results to {output_path}")
        with open(output_path, 'w') as f:
            json.dump(self.results, f, indent=2)

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

                call_timeline.append(
                    {
                        "name": name,
                        "event": "call",
                        "data": entry,
                        "timestamp_iso": timestamp,
                        "event_detail": event_detail_classified,
                        "what": entry.get("input_summary") or entry.get("result_summary") or name,
                        "phase": entry.get("phase"),
                        "phase_question": self._phase_question_for_export(entry.get("phase")),
                        "phase_answer": self._phase_answer_for_export(
                            entry.get("phase"),
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

        logger.info(f"Exporting ARC-only responses to {self.arc_server_output_path}")
        with open(self.arc_server_output_path, 'w') as f:
            json.dump(call_timeline, f, indent=2)

        # B131: Export agent execution trace (CloudWatch-style logs)
        agent_execution_trace = []
        for result in self.results:
            trace_events = result.get("agent_execution_trace", []) or []
            agent_execution_trace.extend(trace_events)
        
        # Sort by timestamp
        agent_execution_trace.sort(key=lambda e: e.get("timestamp_iso", ""))
        
        logger.info(f"Exporting agent execution trace to {self.agent_execution_trace_path}")
        with open(self.agent_execution_trace_path, 'w') as f:
            json.dump(agent_execution_trace, f, indent=2)

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
                "phase": event.get("phase") or ((event.get("data") or {}).get("phase") if isinstance(event.get("data"), dict) else None),
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
                    "hypothesize": "hypothesize",
                    "solve": "route",
                    "act": "execute",
                    "ingest": "evaluate",
                    "replan": "replan",
                }
                phase = op_map.get(operation)
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

        logger.info(f"Exporting master timeline to {self.master_timeline_path}")
        with open(self.master_timeline_path, 'w') as f:
            json.dump(master_timeline, f, indent=2)

    async def shutdown(self):
        """Tear down background resources so the runner exits cleanly."""
        if self.harness is not None:
            await self.harness.teardown()
            self.harness = None

        if self.db is not None:
            self.db.close()
            self.db = None


async def main():
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
    parser.add_argument("--num-puzzles", type=int, default=None, help="Number of puzzles to run (default: 1 for real, 5 for mock)")
    parser.add_argument("--max-steps", type=int, default=None, help="Maximum steps per puzzle (overrides config)")
    parser.add_argument("--card-id", type=str, default=None, help="Override ARC checkpoint card id")
    parser.add_argument("--config", type=str, default=None, help="Explicit path to the sidequests.toml file to use for this run")
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
        llm_overrides.setdefault("timeout_seconds", 300.0)
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

    runner = SingleTaskRunner(real_api=real_api, config_path=args.config, llm_overrides=llm_overrides, max_steps=args.max_steps)
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
        durable = DurableARCRunner(
            runner.harness,
            brain_client,
            runner.config,
            progress_callback=runner.append_live_snapshot,
        )
        durable._emit_transition_snapshots = True

        llm_cfg = runner.config.get("llm", {})
        logger.info(
            "Running %d puzzle(s), starting with: %s | provider=%s model=%s timeout=%s retries=%s",
            len(runner.tasks),
            runner.tasks[0].task_id,
            llm_cfg.get("provider"),
            llm_cfg.get("model"),
            llm_cfg.get("timeout_seconds", "default"),
            llm_cfg.get("max_retries", "default"),
        )
        runner.results = await durable.run(runner.tasks, card_id)

        for result in runner.results:
            runner.append_live_snapshot(
                {
                    "snapshot_type": "final_result",
                    "task_id": result.get("task_id"),
                    "game_id": result.get("game_id"),
                    "correct": result.get("correct"),
                    "steps": result.get("steps"),
                    "runtime_seconds": result.get("runtime_seconds"),
                    "failure_class": result.get("failure_class"),
                    "final_state": result.get("final_state"),
                    "solve_phase_summary": result.get("solve_phase_summary", {}),
                    "evals": result.get("evals", {}),
                    "quality_dimensions": result.get("quality_dimensions", {}),
                    "system_monitoring": result.get("system_monitoring", {}),
                }
            )

        # Print result summary
        for idx, result in enumerate(runner.results):
            logger.info(f"Task {idx+1}: {result.get('task_id')}")
            logger.info(f"  Correct: {result['metadata'].get('correct')}")
            logger.info(f"  Steps: {result['metadata'].get('steps')}")

            solve_summary = result.get("solve_phase_summary") or result.get("metadata", {}).get("solve_phase_summary") or {}
            if solve_summary:
                logger.info(f"  [SOLVE] archetype: {solve_summary.get('final_archetype')} ({solve_summary.get('final_archetype_confidence', 0):.0%})")
                logger.info(f"  [SOLVE] victory: {solve_summary.get('final_victory_condition')} ({solve_summary.get('final_victory_confidence', 0):.0%})")
                logger.info(f"  [SOLVE] strategy: {solve_summary.get('final_strategy_summary', '')[:80]}")
                logger.info(f"  [SOLVE] dissonance: {solve_summary.get('dissonance_triggered')}")
                if solve_summary.get("archetype_evolution"):
                    logger.info(f"  [SOLVE] archetype evolution: {' → '.join(solve_summary['archetype_evolution'])}")

            error = result['metadata'].get('error')
            if error:
                logger.error(f"  Error: {error}")
            else:
                logger.info("  ✅ No parameter binding error!")

        runner.export_results()
    finally:
        await runner.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
