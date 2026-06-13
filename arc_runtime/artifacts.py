from __future__ import annotations

import dataclasses
import datetime
import enum
import json
import logging
import os
from pathlib import Path
from typing import Any

from agents.common.trace_names import (
    canonical_phase,
    normalize_artifact_payload,
    normalize_failure_payload,
    normalize_orchestration_status,
)

logger = logging.getLogger(__name__)

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


def json_default(obj):
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


def json_dumps(obj, **kwargs) -> str:
    return json.dumps(obj, default=json_default, **kwargs)


def atomic_dump_json(path: Path, obj) -> None:
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, default=json_default)
        f.flush()
        try:
            os.fsync(f.fileno())
        except Exception:
            pass
    os.replace(tmp, path)


def phase_question_for_export(phase: str | None) -> str | None:
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


def phase_answer_for_export(phase: str | None, payload: dict | None, fallback: str | None = None) -> str | None:
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


def summarize_world_model_snapshot(snapshot: dict) -> dict:
    if not isinstance(snapshot, dict):
        return {}
    return {
        "node_count": snapshot.get("node_count", 0),
        "edge_count": snapshot.get("edge_count", 0),
        "contradiction_count": snapshot.get("contradiction_count", 0),
        "demotion_count": snapshot.get("demotion_count", 0),
    }


def game_slug(result: dict) -> str:
    title = str(result.get("game_title") or "").strip()
    if title:
        return title.lower()
    game_id = str(result.get("game_id") or "").strip()
    if game_id and game_id != "unknown":
        return game_id.split("-", 1)[0].lower()
    return ""


def artifact_url(path: str | Path) -> str:
    try:
        return Path(path).resolve().as_uri()
    except Exception:
        return str(path)


def build_run_review(result: dict, *, results_path: str | Path | None = None, final_output_path: Path, live_output_path: Path, world_model_live_output_path: Path, agent_execution_trace_path: Path, master_timeline_path: Path) -> dict:
    game_id = str(result.get("game_id") or "unknown")
    title = str(result.get("game_title") or game_slug(result) or game_id).upper()
    tags = [str(tag) for tag in (result.get("game_tags") or []) if tag]
    controls = ", ".join(tags) if tags else "unknown controls"
    steps = int(result.get("steps", 0) or 0)
    final_state = str(result.get("final_state") or "unknown")
    failure_class = str(result.get("failure_class") or "none")
    correct = bool(result.get("correct") is True)
    arc_game_url = f"https://arcprize.org/play?task={game_id}" if game_id != "unknown" else "https://arcprize.org"
    result_file = final_output_path
    current_artifact = Path(results_path) if results_path else final_output_path
    sentence = (
        f"Played ARC-AGI-3 task {title} ({game_id}), a {controls} game; "
        f"the smoke test ended {'solved' if correct else 'unsolved'} after {steps} step(s) "
        f"with final_state={final_state} and failure_class={failure_class}."
    )
    return {
        "puzzle_description": sentence,
        "arc_game_url": arc_game_url,
        "test_results_url": artifact_url(result_file),
        "current_artifact_url": artifact_url(current_artifact),
        "artifact_urls": {
            "submission_results_single": artifact_url(final_output_path),
            "submission_results_single_live": artifact_url(live_output_path),
            "submission_results_single_world_model_live": artifact_url(world_model_live_output_path),
            "agent_execution_trace": artifact_url(agent_execution_trace_path),
            "master_timeline": artifact_url(master_timeline_path),
        },
    }


def make_final_result_compact(result: dict, *, final_output_path: Path, live_output_path: Path, world_model_live_output_path: Path, agent_execution_trace_path: Path, master_timeline_path: Path) -> dict:
    run_review = build_run_review(
        result,
        final_output_path=final_output_path,
        live_output_path=live_output_path,
        world_model_live_output_path=world_model_live_output_path,
        agent_execution_trace_path=agent_execution_trace_path,
        master_timeline_path=master_timeline_path,
    )
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

    world_model_snapshot = result.get("world_model_snapshot", {})
    if world_model_snapshot:
        compact["world_model_summary"] = summarize_world_model_snapshot(world_model_snapshot)

    artifacts = {}
    if result.get("has_execution_trace") or result.get("agent_execution_trace"):
        artifacts["agent_execution_trace"] = str(agent_execution_trace_path)
    if result.get("has_timeline") or result.get("arc_event_timeline") or result.get("chronological_log"):
        artifacts["master_timeline"] = str(master_timeline_path)
    artifacts["world_model_live"] = str(world_model_live_output_path)
    if artifacts:
        compact["artifacts"] = artifacts

    if "evals" in result:
        compact["evals"] = result["evals"]
    if "quality_dimensions" in result:
        compact["quality_dimensions"] = result["quality_dimensions"]
    return compact


def append_live_snapshot(runner: Any, snapshot: dict):
    normalized = dict(snapshot or {})
    normalized.setdefault(
        "timestamp_iso",
        datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    normalized = normalize_artifact_payload(normalized, normalized.get("task_id"))
    Path(runner.live_output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(runner.live_output_path, "a") as f:
        f.write(json_dumps(normalized) + "\n")

    if runner.world_model_eval:
        try:
            Path(runner.world_model_live_output_path).parent.mkdir(parents=True, exist_ok=True)
            task_id = normalized.get("task_id", "unknown")
            step = normalized.get("step", normalized.get("total_steps", 0))
            if normalized.get("snapshot_type") == "final_result":
                summary_row = runner.world_model_evaluator.build_summary_row(task_id, normalized)
                with open(runner.world_model_live_output_path, "a") as f:
                    f.write(json_dumps(summary_row.to_dict()) + "\n")
            elif normalized.get("snapshot_type") == "world_model_decision":
                decision_row = runner.world_model_evaluator.build_decision_row(task_id, normalized)
                with open(runner.world_model_live_output_path, "a") as f:
                    f.write(json_dumps(decision_row.to_dict()) + "\n")
            elif "world_model_node_count" in normalized:
                step_row = runner.world_model_evaluator.build_step_row(task_id, step, normalized)
                with open(runner.world_model_live_output_path, "a") as f:
                    f.write(json_dumps(step_row.to_dict()) + "\n")
        except Exception:
            logger.debug("A078: Failed to emit world model live metrics", exc_info=True)


def export_results_from_runner(runner: Any):
    output_path = runner.final_output_path
    logger.info("Exporting results to %s", output_path)
    for result in runner.results:
        if isinstance(result, dict):
            result.setdefault(
                "run_review",
                build_run_review(
                    result,
                    results_path=output_path,
                    final_output_path=runner.final_output_path,
                    live_output_path=runner.live_output_path,
                    world_model_live_output_path=runner.world_model_live_output_path,
                    agent_execution_trace_path=runner.agent_execution_trace_path,
                    master_timeline_path=runner.master_timeline_path,
                ),
            )
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
        [
            make_final_result_compact(
                result,
                final_output_path=runner.final_output_path,
                live_output_path=runner.live_output_path,
                world_model_live_output_path=runner.world_model_live_output_path,
                agent_execution_trace_path=runner.agent_execution_trace_path,
                master_timeline_path=runner.master_timeline_path,
            )
            for result in runner.results
        ]
        if (runner.live_smoke or runner.world_model_eval)
        else runner.results
    )
    canonical_task_id = runner.results[0].get("task_id") if runner.results and isinstance(runner.results[0], dict) else None
    atomic_dump_json(output_path, normalize_artifact_payload(results_for_export, canonical_task_id))

    call_timeline = []
    for result in runner.results:
        for entry in result.get("sidequests_ledger", []) or []:
            if not isinstance(entry, dict):
                continue
            call_type = entry.get("call_type")
            timestamp = entry.get("timestamp_iso")
            if not call_type or not timestamp:
                continue
            if call_type == "arc_api_action":
                continue
            name = str(call_type)
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
                    "phase_question": phase_question_for_export(phase),
                    "phase_answer": phase_answer_for_export(
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
    if runner.results:
        review = build_run_review(
            runner.results[0],
            results_path=runner.arc_server_output_path,
            final_output_path=runner.final_output_path,
            live_output_path=runner.live_output_path,
            world_model_live_output_path=runner.world_model_live_output_path,
            agent_execution_trace_path=runner.agent_execution_trace_path,
            master_timeline_path=runner.master_timeline_path,
        )
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

    logger.info("Exporting ARC-only responses to %s", runner.arc_server_output_path)
    call_timeline = normalize_artifact_payload(call_timeline, canonical_task_id)
    atomic_dump_json(runner.arc_server_output_path, call_timeline)

    agent_execution_trace = []
    for result in runner.results:
        trace_events = result.get("agent_execution_trace", []) or []
        agent_execution_trace.extend(trace_events)

    agent_execution_trace.sort(key=lambda e: e.get("timestamp_iso", ""))
    agent_execution_trace = normalize_artifact_payload(agent_execution_trace, canonical_task_id)

    logger.info("Exporting agent execution trace to %s", runner.agent_execution_trace_path)
    if runner.results:
        review = build_run_review(
            runner.results[0],
            results_path=runner.agent_execution_trace_path,
            final_output_path=runner.final_output_path,
            live_output_path=runner.live_output_path,
            world_model_live_output_path=runner.world_model_live_output_path,
            agent_execution_trace_path=runner.agent_execution_trace_path,
            master_timeline_path=runner.master_timeline_path,
        )
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
    atomic_dump_json(runner.agent_execution_trace_path, agent_execution_trace)

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

    master_timeline = []
    for event in call_timeline:
        event_type = event.get("event")
        call_type_for_source = (event.get("data") or {}).get("call_type") or event.get("name", "")
        if event_type in ("request", "response"):
            source = "arc_api"
        elif call_type_for_source in SIDEQUESTS_CALLS:
            source = "sidequests"
        else:
            source = "arc_server"

        master_timeline.append(
            {
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
            }
        )

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
        what = (event.get("result") or {}).get("action_id") or str(details.get("action_taken", "")) or event.get("operation", "")
        master_timeline.append(
            {
                "source": "agent_trace",
                "timestamp_iso": event.get("timestamp_iso"),
                "name": event.get("operation"),
                "event": event.get("event_type"),
                "what": what,
                "phase": phase,
                "phase_question": phase_question_for_export(phase),
                "phase_answer": phase_answer_for_export(phase, details if isinstance(details, dict) else {}, what),
                "event_detail": f"{event.get('event_type')} - {event.get('operation')}",
                "details": details,
                "result": event.get("result"),
                "elapsed_ms": event.get("elapsed_ms"),
            }
        )

    if runner.live_output_path.exists():
        for raw_line in runner.live_output_path.read_text().splitlines():
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
                    snapshot_ts = (timeline_base_dt + datetime.timedelta(seconds=float(runtime_seconds))).isoformat().replace("+00:00", "Z")
                else:
                    snapshot_ts = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
            master_timeline.append(
                {
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
                }
            )

    master_timeline.sort(key=_sort_key)
    if runner.results:
        review = build_run_review(
            runner.results[0],
            results_path=runner.master_timeline_path,
            final_output_path=runner.final_output_path,
            live_output_path=runner.live_output_path,
            world_model_live_output_path=runner.world_model_live_output_path,
            agent_execution_trace_path=runner.agent_execution_trace_path,
            master_timeline_path=runner.master_timeline_path,
        )
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

    logger.info("Exporting master timeline to %s", runner.master_timeline_path)
    master_timeline = normalize_artifact_payload(master_timeline, canonical_task_id)
    atomic_dump_json(runner.master_timeline_path, master_timeline)
