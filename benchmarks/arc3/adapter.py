"""ARC-AGI-3 adapter that bridges episodes to SideQuests tools."""

from __future__ import annotations

import copy
import logging
import time
from collections import Counter, deque
from contextlib import nullcontext
from typing import Any, Callable, List, Mapping, Optional, Protocol, Sequence

from sidequest_mcp_client.observability import Observability, canonical_span_name
from .schema import (
    ARC3Action,
    ARC3ColorSummary,
    ARC3Observation,
    ARC3ShapeSummary,
)

logger = logging.getLogger(__name__)


class BrainClientProtocol(Protocol):
    """Very small protocol covering the MCP tools we actually invoke."""

    @property
    def db(self) -> Optional[Any]:
        ...

    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        ...

    async def current_truth(
        self, *, query: str, session_id: str, scope: str, limit: int
    ) -> Mapping[str, Any]:
        ...

    # NEW — Active planning (B67)
    async def register_plan(self, *, goal: str, steps: List[str], session_id: str) -> Mapping[str, Any]:
        ...

    async def report_outcome(
        self,
        *,
        plan_id: Optional[str] = None,
        outcome: Optional[str] = None,
        outcome_text: Optional[str] = None,
        valence: float,
        session_id: str,
        evidence: Optional[Mapping[str, Any]] = None,
        valence_source: Optional[str] = None,
    ) -> Mapping[str, Any]:
        ...

    # NEW — Lesson persistence (B200)
    async def upsert_lesson(self, *, domain: str, text: str, valence: float, confidence: float = 0.7, tags: Optional[List[str]] = None) -> Mapping[str, Any]:
        ...

    # NEW — Procedure recall (B197)
    async def recall_procedures(self, *, archetype: str, limit: int = 3) -> Mapping[str, Any]:
        ...

    # NEW — Retrieval tools the agent needs
    async def recall_plans(self, *, goal_query: str, session_id: str, min_valence: float, limit: int) -> Mapping[str, Any]:
        ...

    async def recall_relevant_lessons(self, *, query: str, limit: int) -> Mapping[str, Any]:
        ...

    async def analogical_search(self, *, query: str, current_quest_id: str, limit: int, min_similarity: float) -> Mapping[str, Any]:
        ...

    # NEW — Knowledge gap inspection (B193/B199)
    async def get_knowledge_gaps(self, *, domain: Optional[str] = None, limit: int = 10, unresolved_only: bool = True, min_severity: float = 0.0) -> Mapping[str, Any]:
        ...

    async def branch_quest(self, *, name: str, purpose: str, parent_quest_id: str) -> Mapping[str, Any]:
        ...

    # NEW — Task Graph (B128)
    async def register_task_graph(self, *, label: str, session_id: str, owner: str, tasks: List[Mapping[str, Any]]) -> Mapping[str, Any]:
        ...

    async def get_ready_tasks(self, *, graph_id: str) -> Mapping[str, Any]:
        ...

    async def advance_task(self, *, graph_id: str, task_id: str, status: str, result: Optional[str] = None) -> Mapping[str, Any]:
        ...

    async def fail_task(self, *, graph_id: str, task_id: str, reason: str) -> Mapping[str, Any]:
        ...

    async def get_task_graph(self, *, graph_id: str) -> Mapping[str, Any]:
        ...


class NoOpBrainClient(BrainClientProtocol):
    """Brain client that does nothing (for baseline mode)."""
    def __init__(self):
        # simple in-memory lesson store for testing cross-puzzle learning
        self._lessons_store: List[Mapping[str, Any]] = []

    @property
    def db(self) -> Optional[Any]:
        return None

    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        return {"status": "skipped"}

    async def current_truth(self, *, query: str, session_id: str, scope: str, limit: int) -> Mapping[str, Any]:
        return {"results": []}

    async def register_plan(self, *, goal: str, steps: List[str], session_id: str) -> Mapping[str, Any]:
        return {"plan_id": None, "warnings": [], "suggestions": []}

    async def report_outcome(
        self,
        *,
        plan_id: Optional[str] = None,
        outcome: Optional[str] = None,
        outcome_text: Optional[str] = None,
        valence: float,
        session_id: str,
        evidence: Optional[Mapping[str, Any]] = None,
        valence_source: Optional[str] = None,
        procedure_id: Optional[str] = None,
        procedure_success: Optional[bool] = None,
    ) -> Mapping[str, Any]:
        return {"updated": False}

    async def recall_procedures(self, *, archetype: str, limit: int = 3) -> Mapping[str, Any]:
        return {"procedures": []}

    async def upsert_lesson(self, *, domain: str, text: str, valence: float, confidence: float = 0.7, tags: Optional[List[str]] = None) -> Mapping[str, Any]:
        # No-op: return a noop lesson id for testing
        return {"lesson_id": "noop", "created": False}

    async def recall_plans(self, *, goal_query: str, session_id: str, min_valence: float, limit: int) -> Mapping[str, Any]:
        return {"plans": []}

    async def recall_relevant_lessons(self, *, query: str, limit: int) -> Mapping[str, Any]:
        if not query:
            return {"lessons": []}
        # Simple substring match over stored lessons
        matches = [l for l in self._lessons_store if query in (l.get("content", "") + " " + " ".join(l.get("tags", [])))]
        return {"lessons": matches[:limit]}

    async def get_knowledge_gaps(self, *, domain: Optional[str] = None, limit: int = 10, unresolved_only: bool = True, min_severity: float = 0.0) -> Mapping[str, Any]:
        """No-op knowledge gaps: baseline returns empty gaps list."""
        return {"gaps": []}

    async def store_lesson(self, *, content: str, tags: List[str], session_id: str) -> Mapping[str, Any]:
        lesson = {"lesson_id": f"lesson_{len(self._lessons_store) + 1}", "content": content, "tags": tags, "session_id": session_id}
        self._lessons_store.append(lesson)
        return {"lesson_id": lesson["lesson_id"]}

    async def upsert_lesson(self, *, domain: str, text: str, valence: float, confidence: float = 0.7, tags: Optional[List[str]] = None) -> Mapping[str, Any]:
        # Map upsert_lesson into store_lesson semantics for NoOp client
        tags = tags or [domain]
        return await self.store_lesson(content=text, tags=tags, session_id="noop")

    async def analogical_search(self, *, query: str, current_quest_id: str, limit: int, min_similarity: float) -> Mapping[str, Any]:
        return {"results": []}

    async def branch_quest(self, *, name: str, purpose: str, parent_quest_id: str) -> Mapping[str, Any]:
        return {"side_quest_id": None, "name": name, "parent_quest_id": parent_quest_id}

    async def register_task_graph(self, *, label: str, session_id: str, owner: str, tasks: List[Mapping[str, Any]]) -> Mapping[str, Any]:
        return {"graph_id": "noop", "task_ids": [], "ready_tasks": [], "cycle_errors": []}

    async def get_ready_tasks(self, *, graph_id: str) -> Mapping[str, Any]:
        return {"graph_id": graph_id, "ready": []}

    async def advance_task(self, *, graph_id: str, task_id: str, status: str, result: Optional[str] = None) -> Mapping[str, Any]:
        return {"task_id": task_id, "new_status": status, "newly_unblocked": []}

    async def fail_task(self, *, graph_id: str, task_id: str, reason: str) -> Mapping[str, Any]:
        return {"task_id": task_id, "status": "failed", "blocked_dependents": []}

    async def get_task_graph(self, *, graph_id: str) -> Mapping[str, Any]:
        return {"graph_id": graph_id, "label": "", "status": "active", "version": 0, "tasks": [], "edges": []}


class LocalBrainClient(BrainClientProtocol):
    """Deprecated compatibility shim kept only to fail fast if used."""
    @property
    def db(self):
        return self._db

    def __init__(self, db, config):
        self._db = db
        self.config = config
        raise RuntimeError(
            "LocalBrainClient is no longer supported in production. "
            "Use MCPBrainClient for production paths or add a test-only shim under sidequest_mcp_client/test_compat."
        )

    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        params = {"role": role, "content": content, "session_id": session_id}
        if precomputed:
            params["precomputed"] = precomputed
        return await self._notify_turn_handler(params, self.db, self.config)

    async def current_truth(self, *, query: str, session_id: str, scope: str, limit: int) -> Mapping[str, Any]:
        params = {"query": query, "session_id": session_id, "scope": scope, "limit": limit}
        return await self._current_truth_handler(params, self.db, self.config)

    async def register_plan(self, *, goal: str, steps: List[str], session_id: str) -> Mapping[str, Any]:
        params = {"goal": goal, "steps": steps, "session_id": session_id}
        return await self._register_plan_handler(params, self.db, self.config)

    async def report_outcome(
        self,
        *,
        plan_id: Optional[str] = None,
        outcome: Optional[str] = None,
        outcome_text: Optional[str] = None,
        valence: float,
        session_id: str,
        evidence: Optional[Mapping[str, Any]] = None,
        valence_source: Optional[str] = None,
        procedure_id: Optional[str] = None,
        procedure_success: Optional[bool] = None,
    ) -> Mapping[str, Any]:
        params = {"plan_id": plan_id, "valence": valence, "session_id": session_id}
        if outcome: params["outcome"] = outcome
        if outcome_text: params["outcome_text"] = outcome_text
        if evidence: params["evidence"] = evidence
        if valence_source: params["valence_source"] = valence_source
        if procedure_id is not None: params["procedure_id"] = procedure_id
        if procedure_success is not None: params["procedure_success"] = procedure_success
        return await self._report_outcome_handler(params, self.db, self.config)

    async def recall_procedures(self, *, archetype: str, limit: int = 3) -> Mapping[str, Any]:
        params = {"archetype": archetype, "limit": limit}
        return await self._recall_procedures_handler(params, self.db, self.config)

    async def recall_plans(self, *, goal_query: str, session_id: str, min_valence: float, limit: int) -> Mapping[str, Any]:
        params = {"goal_query": goal_query, "session_id": session_id, "min_valence": min_valence, "limit": limit}
        return await self._recall_plans_handler(params, self.db, self.config)

    async def recall_relevant_lessons(self, *, query: str, limit: int) -> Mapping[str, Any]:
        params = {"query": query, "limit": limit}
        return await self._recall_relevant_lessons_handler(params, self.db, self.config)

    async def recall_procedures(self, *, archetype: str, limit: int = 3) -> Mapping[str, Any]:
        params = {"archetype": archetype, "limit": limit}
        return await self._recall_procedures_handler(params, self.db, self.config)

    async def analogical_search(self, *, query: str, current_quest_id: str, limit: int, min_similarity: float) -> Mapping[str, Any]:
        params = {"query": query, "current_quest_id": current_quest_id, "limit": limit, "min_similarity": min_similarity}
        return await self._analogical_search_handler(params, self.db, self.config)

    async def branch_quest(self, *, name: str, purpose: str, parent_quest_id: str) -> Mapping[str, Any]:
        params = {"name": name, "purpose": purpose, "parent_quest_id": parent_quest_id}
        return await self._branch_quest_handler(params, self.db, self.config)

    async def register_task_graph(self, *, label: str, session_id: str, owner: str, tasks: List[Mapping[str, Any]]) -> Mapping[str, Any]:
        params = {"label": label, "session_id": session_id, "owner": owner, "tasks": tasks}
        return await self._register_task_graph_handler(params, self.db, self.config)

    async def store_lesson(self, *, content: str, tags: List[str], session_id: str) -> Mapping[str, Any]:
        # Map to the tool's expected parameters (upsert_lesson expects 'text')
        params = {"text": content, "domain": (tags[0] if tags else "arc_game"), "lesson_type": "optimization", "session_id": session_id}
        # if the tool handler exists, call it
        handler = getattr(self, "_store_lesson_handler", None)
        if callable(handler):
            return await handler(params, self.db, self.config)
        # fallback
        logger.error("store_lesson: _store_lesson_handler not set on %s", type(self).__name__)
        return {"lesson_id": None}

    async def upsert_lesson(self, *, domain: str, text: str, valence: float, confidence: float = 0.7, tags: Optional[List[str]] = None) -> Mapping[str, Any]:
        # Map to the same tool handler used by store_lesson
        params = {"text": text, "domain": domain, "lesson_type": "optimization", "session_id": "unknown"}
        handler = getattr(self, "_store_lesson_handler", None)
        if callable(handler):
            return await handler(params, self.db, self.config)
        return {"lesson_id": None}

    async def get_ready_tasks(self, *, graph_id: str) -> Mapping[str, Any]:
        params = {"graph_id": graph_id}
        return await self._get_ready_tasks_handler(params, self.db, self.config)

    async def get_knowledge_gaps(self, *, domain: Optional[str] = None, limit: int = 10, unresolved_only: bool = True, min_severity: float = 0.0) -> Mapping[str, Any]:
        params = {"domain": domain, "limit": limit, "unresolved_only": unresolved_only, "min_severity": min_severity}
        handler = getattr(self, "_get_knowledge_gaps_handler", None)
        if callable(handler):
            return await handler(params, self.db, self.config)
        # Fallback: try to call configured tool handler if present
        handler2 = getattr(self, "_recall_relevant_lessons_handler", None)
        if callable(handler2):
            # best-effort fallback returning no gaps
            return {"gaps": []}
        return {"gaps": []}

    async def advance_task(self, *, graph_id: str, task_id: str, status: str, result: Optional[str] = None) -> Mapping[str, Any]:
        params = {"graph_id": graph_id, "task_id": task_id, "status": status, "result": result}
        return await self._advance_task_handler(params, self.db, self.config)

    async def fail_task(self, *, graph_id: str, task_id: str, reason: str) -> Mapping[str, Any]:
        params = {"graph_id": graph_id, "task_id": task_id, "reason": reason}
        return await self._fail_task_handler(params, self.db, self.config)

    async def get_task_graph(self, *, graph_id: str) -> Mapping[str, Any]:
        params = {"graph_id": graph_id}
        return await self._get_task_graph_handler(params, self.db, self.config)


class LedgerBrainClient(BrainClientProtocol):
    """Wrapper that records all calls into a shared ledger."""
    def __init__(
        self,
        inner: BrainClientProtocol,
        ledger: List[Mapping[str, Any]],
        step_provider: Callable[[], int | str],
        start_time: Optional[float] = None,
        cost_tracker: Optional[Any] = None,
        observability: Optional[Observability] = None,
    ):
        self.inner = inner
        self.ledger = ledger
        self.step_provider = step_provider
        self.cost_tracker = cost_tracker
        self.observability = observability
        self.current_phase: str = "unknown"
        self.start_time = start_time or time.time()
        self._arc_call_seq = 0
        self._event_seq = 0
        self.arc_event_timeline: List[dict] = []

    @staticmethod
    def _brain_operation(call_type: str) -> str:
        mapping = {
            "notify_turn": "brain.notify_turn",
            "current_truth": "brain.current_truth",
            "register_plan": "brain.register_plan",
            "recall_plans": "brain.recall_plans",
            "recall_lessons": "brain.recall_relevant_lessons",
            "report_outcome": "brain.report_outcome",
            "branch_quest": "brain.branch_quest",
            "register_task_graph": "brain.task_graph.register",
            "get_ready_tasks": "brain.task_graph.get_ready",
            "advance_task": "brain.task_graph.advance",
            "fail_task": "brain.task_graph.fail",
            "get_task_graph": "brain.task_graph.get",
            "recall_procedures": "brain.recall_procedures",
            "upsert_lesson": "brain.upsert_lesson",
            "analogical_search": "brain.analogical_search",
            "get_knowledge_gaps": "brain.get_knowledge_gaps",
        }
        return mapping.get(call_type, f"brain.{call_type}")

    def _span_attributes(self, *, phase: str, mode: str, latency_ms: float, extra: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
        attrs = {
            "phase": phase if phase != "unknown" else self.current_phase,
            "mode": mode,
            "latency_ms": round(latency_ms, 3),
            "step": self.step_provider(),
            "agent.name": "ledger_brain_client",
            "agent.role": "memory_adapter",
            "emitter.module": "benchmarks.arc3.adapter",
            "emitter.method": "LedgerBrainClient",
            "trace.contract.version": "v1",
        }
        if extra:
            attrs.update(extra)
        return attrs

    @property
    def db(self) -> Optional[Any]:
        return self.inner.db

    def _record(self, phase: str, call_type: str, mode: str, input_summary: str, result_summary: str, latency_ms: float, decision_used: Optional[Any] = None, arc_api_io: Optional[dict] = None):
        import datetime
        now = time.time()
        elapsed_ms = (now - self.start_time) * 1000
        elapsed_mmss = f"{int(elapsed_ms // 60000):02d}:{int((elapsed_ms % 60000) // 1000):02d}"
        
        entry = {
            "step": self.step_provider(),
            "timestamp_iso": datetime.datetime.fromtimestamp(now, datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "elapsed_mmss": elapsed_mmss,
            "phase": phase if phase != "unknown" else self.current_phase,
            "call_type": call_type,
            "mode": mode,
            "input_summary": self._compact_text(input_summary, 120),
            "result_summary": self._compact_text(result_summary, 120),
            "latency_ms": round(latency_ms, 1),
        }
        
        # B180: Capture current token usage/cost if available
        if self.cost_tracker:
            s = self.cost_tracker.summary()
            entry["cumulative_tokens_in"] = s["tokens_in"]
            entry["cumulative_tokens_out"] = s["tokens_out"]
            entry["cumulative_cost_usd"] = round(s["cost_usd"], 5)

        if decision_used is not None:
            entry["decision_used"] = decision_used
        if arc_api_io is not None:
            entry["arc_api_io"] = arc_api_io
            # B130: preserve arc_api_trace as optional compatibility mirror
            entry["arc_api_trace"] = {
                "http_method": arc_api_io.get("request", {}).get("method"),
                "http_endpoint": arc_api_io.get("request", {}).get("endpoint"),
                "http_status": arc_api_io.get("response", {}).get("http_status"),
                "received": arc_api_io.get("response", {}).get("received", True),
            }
        self.ledger.append(entry)
        if self.observability and self.observability.enabled:
            try:
                span_attrs = self._span_attributes(
                    phase=entry.get("phase", "unknown"),
                    mode=mode,
                    latency_ms=latency_ms,
                    extra={
                        "operation": self._brain_operation(call_type),
                        "input_summary": entry.get("input_summary"),
                        "result_summary": entry.get("result_summary"),
                    },
                )
                with self.observability.span(canonical_span_name(self._brain_operation(call_type)), span_attrs):
                    pass
                self.observability.emit_structured_event(
                    event_type="tool_call",
                    operation=self._brain_operation(call_type),
                    details={
                        "phase": entry.get("phase"),
                        "mode": mode,
                        "step": entry.get("step"),
                        "latency_ms": round(latency_ms, 3),
                        "input_summary": entry.get("input_summary"),
                    },
                    result={
                        "result_summary": entry.get("result_summary"),
                    },
                    elapsed_ms=latency_ms,
                )
            except Exception:
                logger.debug("Failed to emit observability event for %s", call_type, exc_info=True)

    @staticmethod
    def _humanize_arc_operation(method: str, endpoint: str, request_payload: Any = None) -> str:
        endpoint = str(endpoint or "").strip()
        action_id = request_payload.get("action_id") if isinstance(request_payload, dict) else None

        if endpoint.startswith("/api/cmd/"):
            return endpoint.rstrip("/").rsplit("/", 1)[-1].upper()
        if action_id:
            return str(action_id).upper()
        if endpoint.startswith("/api/games/initial"):
            return "INITIAL frame"
        if endpoint.startswith("/api/games"):
            return "GAME state"
        if endpoint.startswith("/api/scorecard/open"):
            return "SCORECARD open"
        if endpoint:
            return f"{method.upper()} {endpoint}"
        return method.upper()

    @staticmethod
    def _summarize_arc_payload(payload: Any) -> Any:
        """Return a compact, test-friendly ARC payload summary."""
        if not isinstance(payload, dict):
            return payload

        summarized = dict(payload)
        frame = summarized.pop("frame", None)
        if isinstance(frame, list):
            rows = len(frame)
            cols = len(frame[0]) if rows and isinstance(frame[0], list) else 0
            summarized["frame_summary"] = {
                "elided": True,
                "dimensions": [rows, cols],
            }
        return summarized

    def record_arc_api_call(self, phase: str, method: str, endpoint: str, request_payload: Any, response_payload: Any, latency_ms: float, http_status: Optional[int] = None, received: bool = True, error_details: Optional[dict] = None):
        """B130: Record a raw ARC API call in the ledger and timeline."""
        import datetime
        now_ts = time.time()
        start_ts = now_ts - (latency_ms / 1000.0)
        operation_label = self._humanize_arc_operation(method, endpoint, request_payload)

        self._arc_call_seq += 1

        # 1. Request Started Event
        self._event_seq += 1
        req_start_iso = datetime.datetime.fromtimestamp(start_ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        req_elapsed_ms = (start_ts - self.start_time) * 1000
        req_elapsed_mmss = f"{int(req_elapsed_ms // 60000):02d}:{int((req_elapsed_ms % 60000) // 1000):02d}"

        self.arc_event_timeline.append({
            "event_seq": self._event_seq,
            "call_seq": self._arc_call_seq,
            "kind": "request_started",
            "label": f"{operation_label} request started",
            "request_started_iso": req_start_iso,
            "elapsed_mmss": req_elapsed_mmss,
            "method": method,
            "endpoint": endpoint
        })

        actual_status = http_status or (200 if received else None)
        
        if self.observability and self.observability.enabled:
            try:
                req_attrs = self._span_attributes(
                    phase=phase,
                    mode="write" if method.upper() != "GET" else "read",
                    latency_ms=0.0,
                    extra={
                        "endpoint": endpoint,
                        "method": method.upper(),
                        "call_seq": self._arc_call_seq,
                        "operation_label": operation_label,
                    },
                )
                with self.observability.span(canonical_span_name("arc_api.request"), req_attrs):
                    pass
                resp_attrs = self._span_attributes(
                    phase=phase,
                    mode="read",
                    latency_ms=latency_ms,
                    extra={
                        "endpoint": endpoint,
                        "method": method.upper(),
                        "call_seq": self._arc_call_seq,
                        "http_status": actual_status if actual_status is not None else 0,
                        "received": bool(received),
                    },
                )
                with self.observability.span(canonical_span_name("arc_api.response"), resp_attrs) as span:
                    if not received and error_details:
                        span.add_event(
                            "arc_api.error",
                            {
                                "error_type": error_details.get("error_type"),
                                "error_message": str(error_details.get("error_message") or "")[:120],
                            },
                        )
            except Exception:
                logger.debug("Failed to emit ARC API spans", exc_info=True)
        
        # 2. Response Received Event
        self._event_seq += 1
        resp_received_iso = datetime.datetime.fromtimestamp(now_ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z")
        resp_elapsed_ms = (now_ts - self.start_time) * 1000
        resp_elapsed_mmss = f"{int(resp_elapsed_ms // 60000):02d}:{int((resp_elapsed_ms % 60000) // 1000):02d}"
        
        summary = ""
        timeline_payload = self._summarize_arc_payload(response_payload) if received else (error_details if error_details else None)
        if received:
            summary = f"status {actual_status}"
            if isinstance(response_payload, dict):
                state = response_payload.get("state")
                reward = response_payload.get("reward")
                if state: summary += f"; state {state}"
                if reward is not None: summary += f"; reward {reward}"
        else:
            summary = f"failed: {error_details.get('error_type') if error_details else 'unknown'}"

        self.arc_event_timeline.append({
            "event_seq": self._event_seq,
            "call_seq": self._arc_call_seq,
            "kind": "response_received",
            "label": f"{operation_label} response #{self._arc_call_seq}",
            "response_received_iso": resp_received_iso,
            "elapsed_mmss": resp_elapsed_mmss,
            "duration_ms": int(latency_ms),
            "http_status": actual_status,
            "response_summary": summary,
            "payload": timeline_payload
        })

        arc_api_io = {
            "call_seq": self._arc_call_seq,
            "request": {
                "method": method,
                "endpoint": endpoint,
                "payload": request_payload,
            },
            "response": {
                "received": received,
                "http_status": actual_status,
                "payload": response_payload if received else None,
                "error": error_details,
            }
        }
        input_summary = f"{method} {endpoint}"
        result_summary = f"status={actual_status}" if received else f"failed: {error_details.get('error_type') if error_details else 'unknown'}"
        self._record(
            phase=phase,
            call_type="arc_api_action",
            mode="write" if method == "POST" else "read",
            input_summary=input_summary,
            result_summary=result_summary,
            latency_ms=latency_ms,
            arc_api_io=arc_api_io
        )

    @staticmethod
    def _compact_text(text: str, limit: int = 180) -> str:
        text = " ".join(str(text).split())
        if len(text) <= limit:
            return text
        return text[: limit - 1].rstrip() + "…"

    @staticmethod
    def _safe_get(payload: Any, key: str, default: Any = None) -> Any:
        if isinstance(payload, Mapping):
            return payload.get(key, default)
        return default

    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        import time
        start = time.time()
        
        ctx = (
            self.observability.span(canonical_span_name("brain.notify_turn"), self._span_attributes(phase="unknown", mode="write", latency_ms=0))
            if self.observability and self.observability.enabled
            else nullcontext()
        )
        
        with ctx as span:
            resp = await self.inner.notify_turn(role=role, content=content, session_id=session_id, precomputed=precomputed)
            latency = (time.time() - start) * 1000
            if hasattr(span, "set_attributes"):
                span.set_attributes({"latency_ms": round(latency, 3), "role": role})
            
        self._record("unknown", "notify_turn", "write", content, self._safe_get(resp, "status", "ok"), latency)
        return resp

    async def current_truth(self, *, query: str, session_id: str, scope: str, limit: int) -> Mapping[str, Any]:
        import time
        start = time.time()
        
        ctx = (
            self.observability.span(canonical_span_name("brain.current_truth"), self._span_attributes(phase="unknown", mode="read", latency_ms=0))
            if self.observability and self.observability.enabled
            else nullcontext()
        )
        
        with ctx as span:
            resp = await self.inner.current_truth(query=query, session_id=session_id, scope=scope, limit=limit)
            latency = (time.time() - start) * 1000
            results = self._safe_get(resp, "results", []) or []
            if hasattr(span, "set_attributes"):
                span.set_attributes({"latency_ms": round(latency, 3), "result_count": len(results)})
            
        self._record("unknown", "current_truth", "read", query, f"found {len(results)} items", latency)
        return resp

    async def register_plan(self, *, goal: str, steps: List[str], session_id: str) -> Mapping[str, Any]:
        import time
        start = time.time()
        
        ctx = (
            self.observability.span(canonical_span_name("brain.register_plan"), self._span_attributes(phase="unknown", mode="write", latency_ms=0))
            if self.observability and self.observability.enabled
            else nullcontext()
        )
        
        with ctx as span:
            resp = await self.inner.register_plan(goal=goal, steps=steps, session_id=session_id)
            latency = (time.time() - start) * 1000
            if hasattr(span, "set_attributes"):
                span.set_attributes({"latency_ms": round(latency, 3), "step_count": len(steps)})
            
        self._record("unknown", "register_plan", "write", f"goal={goal}, steps={len(steps)}", f"plan_id={self._safe_get(resp, 'plan_id')}", latency)
        return resp

    async def report_outcome(
        self,
        *,
        plan_id: Optional[str] = None,
        outcome: Optional[str] = None,
        outcome_text: Optional[str] = None,
        valence: float,
        session_id: str,
        evidence: Optional[Mapping[str, Any]] = None,
        valence_source: Optional[str] = None,
        procedure_id: Optional[str] = None,
        procedure_success: Optional[bool] = None,
    ) -> Mapping[str, Any]:
        import time
        start = time.time()
        
        # B181: Map outcome_text to outcome if provided (for B167 compatibility)
        actual_outcome = outcome or outcome_text or "unknown"
        
        kwargs = {
            "plan_id": plan_id,
            "session_id": session_id,
            "valence": valence,
            "outcome": actual_outcome,
        }
        if valence_source: kwargs["valence_source"] = valence_source
        if evidence: kwargs["evidence"] = evidence
        if procedure_id is not None: kwargs["procedure_id"] = procedure_id
        if procedure_success is not None: kwargs["procedure_success"] = procedure_success

        resp = await self.inner.report_outcome(**kwargs)
        latency = (time.time() - start) * 1000
        input_summary = f"plan_id={plan_id}, valence={valence:.2f}"
        if valence_source:
            input_summary += f", source={valence_source}"
        self._record("unknown", "report_outcome", "write", input_summary, self._safe_get(resp, "status", "ok"), latency)
        return resp

    async def recall_plans(self, *, goal_query: str, session_id: str, min_valence: float, limit: int) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.recall_plans(goal_query=goal_query, session_id=session_id, min_valence=min_valence, limit=limit)
        latency = (time.time() - start) * 1000
        plans = self._safe_get(resp, "plans", []) or []
        self._record("unknown", "recall_plans", "read", goal_query, f"found {len(plans)} plans", latency)
        return resp

    async def recall_relevant_lessons(self, *, query: str, limit: int) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.recall_relevant_lessons(query=query, limit=limit)
        latency = (time.time() - start) * 1000
        lessons = self._safe_get(resp, "lessons", []) or []
        self._record("unknown", "recall_lessons", "read", query, f"found {len(lessons)} lessons", latency)
        return resp

    async def recall_procedures(self, *, archetype: str, limit: int = 3) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.recall_procedures(archetype=archetype, limit=limit)
        latency = (time.time() - start) * 1000
        procs = self._safe_get(resp, "procedures", []) or []
        self._record("unknown", "recall_procedures", "read", archetype, f"found {len(procs)} procedures", latency)
        return resp

    async def get_knowledge_gaps(self, *, domain: Optional[str] = None, limit: int = 10, unresolved_only: bool = True, min_severity: float = 0.0) -> Mapping[str, Any]:
        import time
        start = time.time()
        # Delegate to inner client
        resp = await self.inner.get_knowledge_gaps(domain=domain, limit=limit, unresolved_only=unresolved_only, min_severity=min_severity)
        latency = (time.time() - start) * 1000
        gaps = self._safe_get(resp, "gaps", []) or []
        self._record("unknown", "get_knowledge_gaps", "read", str(domain or ""), f"found {len(gaps)} gaps", latency)
        return resp

    async def analogical_search(self, *, query: str, current_quest_id: str, limit: int, min_similarity: float) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.analogical_search(query=query, current_quest_id=current_quest_id, limit=limit, min_similarity=min_similarity)
        latency = (time.time() - start) * 1000
        results = self._safe_get(resp, "results", []) or []
        self._record("unknown", "analogical_search", "read", query, f"found {len(results)} results", latency)
        return resp

    async def branch_quest(self, *, name: str, purpose: str, parent_quest_id: str) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.branch_quest(name=name, purpose=purpose, parent_quest_id=parent_quest_id)
        latency = (time.time() - start) * 1000
        self._record("unknown", "branch_quest", "write", name, f"side_quest_id={self._safe_get(resp, 'side_quest_id')}", latency)
        return resp

    async def register_task_graph(self, *, label: str, session_id: str, owner: str, tasks: List[Mapping[str, Any]]) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.register_task_graph(label=label, session_id=session_id, owner=owner, tasks=tasks)
        latency = (time.time() - start) * 1000
        self._record("unknown", "register_task_graph", "write", f"label={label}, tasks={len(tasks)}", f"graph_id={self._safe_get(resp, 'graph_id')}", latency)
        return resp

    async def get_ready_tasks(self, *, graph_id: str) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.get_ready_tasks(graph_id=graph_id)
        latency = (time.time() - start) * 1000
        ready = self._safe_get(resp, "ready", []) or []
        self._record("unknown", "get_ready_tasks", "read", f"graph_id={graph_id}", f"found {len(ready)} tasks", latency)
        return resp

    async def advance_task(self, *, graph_id: str, task_id: str, status: str, result: Optional[str] = None) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.advance_task(graph_id=graph_id, task_id=task_id, status=status, result=result)
        latency = (time.time() - start) * 1000
        unblocked = len(resp.get('newly_unblocked', [])) if isinstance(resp, dict) else 0
        self._record("unknown", "advance_task", "write", f"task_id={task_id}, status={status}", f"unblocked={unblocked}", latency)
        return resp

    async def store_lesson(self, *, content: str, tags: List[str], session_id: str) -> Mapping[str, Any]:
        import time
        start = time.time()
        # Delegate to inner client's store_lesson when available
        handler = getattr(self.inner, "store_lesson", None)
        if callable(handler):
            resp = await handler(content=content, tags=tags, session_id=session_id)
        else:
            # Best-effort: try upsert_lesson via inner if available
            upsert = getattr(self.inner, "upsert_lesson", None)
            if callable(upsert):
                resp = await upsert(text=content, domain=(tags[0] if tags else "arc_game"), lesson_type="optimization", session_id=session_id)
            else:
                resp = {"lesson_id": None}

        latency = (time.time() - start) * 1000
        lesson_id = resp.get('lesson_id') if isinstance(resp, dict) else None
        self._record("unknown", "store_lesson", "write", f"session_id={session_id}", f"lesson_id={lesson_id}", latency)
        return resp

    async def upsert_lesson(self, *, domain: str, text: str, valence: float, confidence: float = 0.7, tags: Optional[List[str]] = None) -> Mapping[str, Any]:
        import time
        start = time.time()
        # Prefer explicit upsert_lesson on inner client
        handler = getattr(self.inner, "upsert_lesson", None)
        if callable(handler):
            try:
                resp = await handler(domain=domain, text=text, valence=valence, confidence=confidence, tags=tags)
            except Exception:
                logger.exception(
                    "upsert_lesson failed (LedgerBrainClient): domain=%s text_len=%d",
                    domain, len(text),
                )
                resp = {"lesson_id": None}
        else:
            # Fallback to store_lesson if available
            handler2 = getattr(self.inner, "store_lesson", None)
            if callable(handler2):
                try:
                    resp = await handler2(content=text, tags=tags or [domain], session_id="unknown")
                except Exception:
                    logger.exception(
                        "upsert_lesson store_lesson fallback failed: domain=%s", domain
                    )
                    resp = {"lesson_id": None}
            else:
                resp = {"lesson_id": None}

        latency = (time.time() - start) * 1000
        lesson_id = resp.get('lesson_id') if isinstance(resp, dict) else None
        self._record("unknown", "upsert_lesson", "write", f"domain={domain}", f"lesson_id={lesson_id}", latency)
        return resp

    async def fail_task(self, *, graph_id: str, task_id: str, reason: str) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.fail_task(graph_id=graph_id, task_id=task_id, reason=reason)
        latency = (time.time() - start) * 1000
        self._record("unknown", "fail_task", "write", f"task_id={task_id}, reason={reason}", f"blocked={len(resp.get('blocked_dependents', []))}", latency)
        return resp

    async def get_task_graph(self, *, graph_id: str) -> Mapping[str, Any]:
        import time
        start = time.time()
        resp = await self.inner.get_task_graph(graph_id=graph_id)
        latency = (time.time() - start) * 1000
        tasks = self._safe_get(resp, "tasks", []) or []
        self._record("unknown", "get_task_graph", "read", f"graph_id={graph_id}", f"found {len(tasks)} tasks", latency)
        return resp


class ARC3Adapter:
    """Normalize ARC episodes and drive SideQuests notify/current_truth calls."""

    def __init__(
        self,
        brain_client: BrainClientProtocol,
        session_id: str,
        dataset_id: str = "arc-agi-3",
        task_id: str = "unknown",
        telemetry_hook: Optional[Callable[[Mapping[str, Any]], None]] = None,
    ) -> None:
        self.brain_client = brain_client
        self.session_id = session_id
        self.dataset_id = dataset_id
        self.task_id = task_id
        self.episode_num = 1
        self.step_num = 0
        self.telemetry_hook = telemetry_hook
        self._telemetry: List[Mapping[str, Any]] = []
        self.logger = logging.getLogger(__name__)

    # --- public helpers ---------------------------------------------------

    def start_episode(self, episode_num: int = 1) -> None:
        """Reset the internal counters for a new ARC episode."""

        self.episode_num = episode_num
        self.step_num = 0

    def get_ledger(self) -> List[Mapping[str, Any]]:
        """Compatibility accessor for B111 ledger ownership.

        Some call paths may still ask the adapter for the aggregated ledger even
        though the canonical owner now lives on the wrapped brain client.
        """
        ledger = getattr(self.brain_client, "ledger", None)
        if ledger is None:
            return []
        return list(ledger)

    def normalize_observation(self, raw: Mapping[str, Any]) -> ARC3Observation:
        """Convert the raw FrameResponse into a stable normalized snapshot."""

        grid = self._resolve_grid(raw.get("frame"))
        if not grid:
            raise ValueError("Observation payload missing grid data")

        dataset_id = raw.get("dataset_id") or raw.get("game_id") or self.dataset_id
        task_id = raw.get("task_id") or raw.get("guid") or self.task_id
        episode_num = int(raw.get("episode_num") or raw.get("episode") or self.episode_num)
        step_num = int(raw.get("step_num") or (self.step_num + 1))

        self.dataset_id = dataset_id
        self.task_id = task_id
        self.episode_num = episode_num

        available_actions = self._normalize_available_actions(raw.get("available_actions") or [])
        state = str(raw.get("state") or "NOT_STARTED")
        energy_estimate = self._estimate_energy(grid)

        # B88: Add frame_hash and invariant_regions (populated later by orchestrator)
        from agents.arc3.hypothesis import StateNode
        frame_hash = StateNode.hash_grid(grid)

        return {
            "dataset_id": dataset_id,
            "task_id": task_id,
            "episode_num": episode_num,
            "step_num": step_num,
            "grid": grid,
            "colors": self._summarize_colors(grid),
            "shapes": self._detect_shapes(grid),
            "available_actions": available_actions,
            "state": state,
            "energy_estimate": energy_estimate,
            "frame_hash": frame_hash,
            "invariant_regions": [],
            "training_examples": raw.get("training_examples") or [], # B156
            "levels_completed": raw.get("levels_completed"),         # B157
            "win_levels": raw.get("win_levels"),                     # B157
        }

    def normalize_action(
        self, raw_action: Mapping[str, Any]
    ) -> ARC3Action:
        """Turn an ARC action payload into a deterministic change descriptor."""

        action_type = (
            raw_action.get("action_id")
            or raw_action.get("action_type")
            or raw_action.get("type")
            or raw_action.get("name")
        )
        if not action_type:
            raise ValueError("ARC action missing action_type")
        normalized_type = str(action_type).upper()

        coords = self._coords_from_action(raw_action)
        grid_change = self._build_grid_change(raw_action, coords)
        rationale = (
            raw_action.get("rationale")
            or raw_action.get("reasoning")
            or raw_action.get("comment")
            or "ARC action"
        )

        deterministic_id = self._build_action_id(normalized_type, grid_change)

        metadata = dict(raw_action.get("metadata") or {})

        return {
            "action_type": normalized_type,
            "grid_change": grid_change,
            "rationale": str(rationale),
            "deterministic_id": deterministic_id,
            "metadata": metadata,
        }

    def to_turn_narrative(
        self,
        obs: ARC3Observation,
        action: ARC3Action,
        reward: Optional[float] = None,
    ) -> str:
        """Summarize a step for passive ingestion."""

        coords = action["grid_change"].get("coords")
        coords_text = f"cell {coords}" if coords else "the grid"
        change_value = action["grid_change"].get("value")
        rationale = action["rationale"]
        reward_text = f" reward {reward:.2f}" if reward is not None else ""

        return (
            f"[{obs['dataset_id']}:{obs['task_id']}] "
            f"Episode {obs['episode_num']} · Step {obs['step_num']}: "
            f"{action['action_type']} at {coords_text} sets {change_value} · {rationale}.{reward_text}"
        )

    async def ingest_step(
        self,
        raw_observation: Mapping[str, Any],
        raw_action: Mapping[str, Any],
        reward: Optional[float] = None,
        recall_query: Optional[str] = None,
    ) -> Mapping[str, Any]:
        """Normalize, log, ingest, and optionally recall memory for one ARC turn."""

        memory = None
        if recall_query:
            memory = await self.current_truth(recall_query)

        normalized_obs = self.normalize_observation(raw_observation)
        normalized_action = self.normalize_action(raw_action)
        narrative = self.to_turn_narrative(normalized_obs, normalized_action, reward)
        await self.notify_turn(narrative)

        entry = {
            "observation": normalized_obs,
            "action": normalized_action,
            "reward": reward,
            "memory": memory,
        }
        self._telemetry.append(copy.deepcopy(entry))
        if self.telemetry_hook:
            self.telemetry_hook(entry)

        self.step_num += 1
        return {"narrative": narrative, "memory": memory}

    async def notify_turn(self, content: str, role: str = "assistant", precomputed: Optional[Mapping[str, Any]] = None) -> Mapping[str, Any]:
        """Forward the turn narrative to SideQuests."""

        return await self.brain_client.notify_turn(
            role=role, content=content, session_id=self.session_id, precomputed=precomputed
        )

    async def current_truth(
        self,
        query: str,
        scope: str = "branch",
        limit: int = 5,
    ) -> Mapping[str, Any]:
        """Recall relevant memory for the current session."""

        return await self.brain_client.current_truth(
            query=query, session_id=self.session_id, scope=scope, limit=limit
        )

    def get_telemetry_trace(self) -> List[Mapping[str, Any]]:
        """Return a deterministic replay trace of every logged step."""

        return [copy.deepcopy(entry) for entry in self._telemetry]

    # --- helper utilities ----------------------------------------------

    def _resolve_grid(self, frame_obj: Any) -> List[List[int]]:
        if not frame_obj or not isinstance(frame_obj, list):
            return []

        first = frame_obj[0]
        if isinstance(first, list) and first and isinstance(first[0], list):
            candidate = first
        elif isinstance(first, list):
            candidate = frame_obj
        else:
            return []

        if not candidate or not isinstance(candidate[0], list):
            return []

        return [self._to_row(row) for row in candidate]

    def _normalize_available_actions(self, actions: Sequence[Any]) -> List[str]:
        normalized: List[str] = []
        for action in actions:
            if isinstance(action, int):
                normalized.append(f"ACTION{action}")
                continue

            text = str(action).strip()
            if not text:
                continue
            if text.isdigit():
                normalized.append(f"ACTION{text}")
            else:
                normalized.append(text.upper())
        return normalized

    def _to_row(self, row: Sequence[Any]) -> List[int]:
        return [int(cell) for cell in row]

    def _summarize_colors(self, grid: List[List[int]]) -> List[ARC3ColorSummary]:
        counts: Counter[int] = Counter()
        for row in grid:
            for pixel in row:
                counts[int(pixel)] += 1
        return [
            {"value": value, "count": count}
            for value, count in sorted(counts.items(), key=lambda item: item[0])
        ]

    def _detect_shapes(self, grid: List[List[int]]) -> List[ARC3ShapeSummary]:
        if not grid or not grid[0]:
            return []

        rows, cols = len(grid), len(grid[0])
        visited = [[False] * cols for _ in range(rows)]
        shapes: List[ARC3ShapeSummary] = []

        for r in range(rows):
            for c in range(cols):
                if visited[r][c]:
                    continue
                target_value = grid[r][c]
                coords: List[tuple[int, int]] = []
                queue = deque([(r, c)])
                visited[r][c] = True
                while queue:
                    pr, pc = queue.popleft()
                    coords.append((pr, pc))
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nr, nc = pr + dr, pc + dc
                        if (
                            0 <= nr < rows
                            and 0 <= nc < cols
                            and not visited[nr][nc]
                            and grid[nr][nc] == target_value
                        ):
                            visited[nr][nc] = True
                            queue.append((nr, nc))
                coords_sorted = sorted(coords)
                shapes.append(
                    {
                        "color": int(target_value),
                        "size": len(coords_sorted),
                        "coords": coords_sorted,
                    }
                )
        shapes.sort(key=lambda shape: (shape["color"], shape["size"], shape["coords"]))
        return shapes

    def _estimate_energy(self, grid: List[List[int]]) -> float:
        """Estimate energy/life bar from the bottom rows of the 64x64 grid."""
        return 1.0

    def _coords_from_action(self, raw_action: Mapping[str, Any]) -> Optional[List[int]]:
        if "coords" in raw_action:
            coords = raw_action["coords"]
            if isinstance(coords, Sequence) and len(coords) >= 2:
                return [int(coords[0]), int(coords[1])]
        row = raw_action.get("row")
        col = raw_action.get("col")
        if row is not None and col is not None:
            return [int(row), int(col)]
        x = raw_action.get("x")
        y = raw_action.get("y")
        if x is not None and y is not None:
            return [int(y), int(x)]
        return None

    def _build_grid_change(
        self, raw_action: Mapping[str, Any], coords: Optional[List[int]]
    ) -> Mapping[str, Any]:
        change: dict[str, Any] = {}
        if coords is not None:
            change["coords"] = coords
        for field in ("value", "target_value", "prev_value", "direction"):
            if field in raw_action:
                change[field] = raw_action[field]
        return change

    def _build_action_id(self, action_type: str, grid_change: Mapping[str, Any]) -> str:
        parts = [action_type]
        coords = grid_change.get("coords")
        if coords:
            parts.append(f"coords={coords[0]}:{coords[1]}")
        if "value" in grid_change:
            parts.append(f"new={grid_change['value']}")
        if "prev_value" in grid_change:
            parts.append(f"prev={grid_change['prev_value']}")
        return "|".join(parts)
