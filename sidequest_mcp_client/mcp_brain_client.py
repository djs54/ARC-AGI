"""MCP-backed Brain client for ARC runtime.

Provides async method-style wrappers that map ARC runtime calls to HippoCampy/Campy
MCP tool names. The client is a thin async wrapper around the
`MCPStdIOSession` transport implemented in `sidequest_mcp_client.mcp_session`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .mcp_session import MCPError, MCPStdIOSession, MCPToolNotFound
from .readiness import _cmd_from_env

logger = logging.getLogger(__name__)

class MCPBrainClient:
    """Async ARC-facing brain client backed by an MCP stdio session.

    The client lazily starts and initializes the underlying MCP session
    on first use to preserve compatibility with existing runtime wiring.
    """

    def __init__(self, db: Optional[Any] = None, config: Optional[Dict[str, Any]] = None, cmd: Optional[List[str]] = None, session: Optional[MCPStdIOSession] = None):
        self.db = db
        self.config = config
        self._cmd = cmd or _cmd_from_env()
        self._session = session or MCPStdIOSession(cmd=self._cmd)
        self._started = False
        self._initialized = False
        self._init_payload: Optional[Dict[str, Any]] = None
        # B220: allow per-tool timeout overrides via config or explicit tool settings
        self.timeouts = {
            "current_truth": 20.0,  # expensive retrieval
            "analogical_search": 15.0,
            "recall_relevant_lessons": 15.0,
            "register_plan": 20.0,  # slow DB write
            "notify_turn": 30.0,    # ingestion can be very slow
            "upsert_lesson": 45.0,  # A051: slow DB write, avoid 30s cliff
        }
        # A012: In-memory cache for high-frequency idempotent retrieval
        self._cache: Dict[tuple, tuple[float, Any]] = {}
        self._cache_ttl = 15.0
        self._cache_max_size = 64 # A059: Increased from 32
        self._cached_methods = {
            "current_truth", 
            "recall_relevant_lessons",
            "recall_plans",
            "analogical_search",
            "recall_lessons",
            "recall_scene_graph_priors",
            "recall_procedures"
        }
        # A059: Deduplication cache for repeated writes
        self._dedup_cache: Dict[tuple, tuple[float, Any]] = {}
        self._dedup_ttl = 60.0 # 1 minute dedup window
        # Health signal surfaced to orchestrator/runner when memory backend is degraded.
        self.memory_degraded: bool = False
        self.memory_degraded_reason: str = ""
        # A044: Non-blocking notify_turn queue
        self._notify_queue: Optional[asyncio.Queue] = None
        self._notify_worker_task: Optional[asyncio.Task] = None
        self._notify_queue_limit = 100
        self._notify_dropped_count = 0
        # A064: Memory firewall state
        self.current_phase: str = "unknown"
        self._deferred_writes: List[tuple[str, dict, float]] = []
        self._firewall_enabled: bool = True

    @staticmethod
    def _is_missing_tool_error(exc: Exception, tool_name: str) -> bool:
        text = str(exc).lower()
        return (
            isinstance(exc, MCPToolNotFound)
            or "unknown tool" in text
            or f"unknown method: {tool_name}".lower() in text
            or tool_name.lower() in text and "not found" in text
        )

    @staticmethod
    def _classify_mcp_transport_error(exc: Exception | None, response: Dict[str, Any] | None = None) -> Dict[str, Any]:
        """A091: Classify MCP transport failures into degradation metadata.
        
        Returns degradation metadata dict with:
        - status: "degraded"
        - error_code: specific code (daemon_http_timeout, etc)
        - memory_degraded: true
        - memory_degraded_reason: human-readable reason
        - mcp_transport: "http_bridge" when inferable
        """
        error_text = ""
        if exc:
            error_text = str(exc).lower()
        if response and isinstance(response, dict):
            error_text += " " + str(response.get("error", "")).lower()
        
        # Detect HTTP bridge timeout/offline patterns
        is_http_bridge = (
            "daemon_http_error" in error_text
            or "timed out" in error_text and ("http" in error_text or "127.0.0.1" in error_text)
            or "cannot reach http" in error_text
            or "connection refused" in error_text and ("http" in error_text or "127.0.0.1" in error_text)
        )
        
        if "daemon_http_error" in error_text or "daemon_offline" in error_text:
            reason = "daemon_http_timeout" if "timeout" in error_text else "daemon_offline"
            return {
                "status": "degraded",
                "error_code": reason,
                "memory_degraded": True,
                "memory_degraded_reason": reason,
                "mcp_transport": "http_bridge" if is_http_bridge else None,
            }
        
        if "timed out" in error_text or "timeout" in error_text:
            return {
                "status": "degraded",
                "error_code": "daemon_http_timeout",
                "memory_degraded": True,
                "memory_degraded_reason": "daemon_http_timeout",
                "mcp_transport": "http_bridge" if is_http_bridge else None,
            }
        
        if "connection refused" in error_text or "connection reset" in error_text:
            return {
                "status": "degraded",
                "error_code": "daemon_connection_failed",
                "memory_degraded": True,
                "memory_degraded_reason": "daemon_connection_failed",
                "mcp_transport": "http_bridge" if is_http_bridge else None,
            }
        
        # Fallback for any MCP error
        return {
            "status": "degraded",
            "error_code": "mcp_transport_error",
            "memory_degraded": True,
            "memory_degraded_reason": "mcp_transport_error",
            "mcp_transport": None,
        }

    @staticmethod
    def _degraded_read_payload(
        error_code: str,
        memory_degraded_reason: str,
        mcp_transport: str | None = None
    ) -> Dict[str, Any]:
        """A091: Return a structured degraded empty read payload."""
        return {
            "status": "degraded",
            "items": [],
            "results": [],
            "lessons": [],
            "plans": [],
            "procedures": [],
            "knowledge_gaps": [],
            "memory_degraded": True,
            "memory_degraded_reason": memory_degraded_reason,
            "error_code": error_code,
            "mcp_transport": mcp_transport,
        }

    @staticmethod
    def _degraded_write_payload(
        error_code: str,
        memory_degraded_reason: str,
        mcp_transport: str | None = None,
        deferred: bool = True
    ) -> Dict[str, Any]:
        """A091: Return a structured degraded write payload."""
        return {
            "status": "degraded",
            "accepted": False,
            "deferred": deferred,
            "memory_degraded": True,
            "memory_degraded_reason": memory_degraded_reason,
            "error_code": error_code,
            "mcp_transport": mcp_transport,
        }

    def _ensure_notify_worker(self):
        """A044: Lazily start the background notify worker."""
        if self._notify_queue is None:
            self._notify_queue = asyncio.Queue(maxsize=self._notify_queue_limit)
            self._notify_worker_task = asyncio.create_task(self._notify_worker())
            logger.debug("A044: Started background notify_turn worker")

    async def _notify_worker(self):
        """A044: Process notify_turn calls in the background."""
        while True:
            try:
                args, timeout = await self._notify_queue.get()
                try:
                    # Use synchronous call_tool logic but inside the worker task
                    await self.call_tool("notify_turn", args, timeout=timeout)
                except Exception as exc:
                    logger.warning("A044: Background notify_turn failed: %s", exc)
                finally:
                    self._notify_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("A044: Notify worker encountered unexpected error: %s", exc)
                await asyncio.sleep(1.0)


    def clear_cache(self) -> None:
        """Clear the in-memory retrieval cache."""
        self._cache.clear()

    async def start(self, cmd: Optional[List[str]] = None, startup_timeout: float = 3.0) -> None:
        if cmd is not None:
            self._cmd = cmd
            self._session.cmd = cmd
        await asyncio.to_thread(self._session.start, self._cmd, startup_timeout)
        self._started = True

    async def initialize_session(self, timeout: float = 5.0) -> Dict[str, Any]:
        payload = await asyncio.to_thread(self._session.initialize, timeout)
        self._initialized = True
        self._init_payload = payload
        return payload

    async def list_tools(self, timeout: float = 5.0) -> List[Dict[str, Any]]:
        return await asyncio.to_thread(self._session.list_tools, timeout)

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None) -> Dict[str, Any]:
        # Lazy start/initialize to preserve compatibility with code that
        # constructs clients without explicit startup.
        if not self._started:
            await self.start()
        if not self._initialized:
            try:
                await self.initialize_session()
            except Exception:
                # allow calls to proceed even if initialization payload is not needed
                pass
        
        args = arguments or {}
        
        # A064: Memory Firewall for execute hot path
        is_hot_path = self.current_phase in ("execute", "macro")
        
        # A012: Cache lookup
        import time
        import json
        if name in self._cached_methods:
            cache_key = (name, json.dumps(args, sort_keys=True))
            if cache_key in self._cache:
                ts, payload = self._cache[cache_key]
                if time.time() - ts < self._cache_ttl:
                    # Return a deepcopy to prevent callers from mutating cache
                    import copy
                    cached_resp = copy.deepcopy(payload)
                    if isinstance(cached_resp, dict):
                        cached_resp["source"] = "cache"
                        cached_resp["cache_hit"] = True
                        cached_resp["memory_firewall_action"] = "cached"
                    return cached_resp
                else:
                    del self._cache[cache_key]
            
            # A064/A068: If in hot path and NOT in cache, skip blocking read
            if is_hot_path and self._firewall_enabled:
                return {
                    "status": "skipped",
                    "source": "firewall",
                    "memory_firewall_action": "skipped",
                    "memory_firewall_reason": f"phase_{self.current_phase}_read_blocked",
                    "memory_degraded": False,
                    "results": [],
                    "lessons": [],
                    "plans": [],
                    "procedures": [],
                    "knowledge_gaps": []
                }

        # A059: Deduplication lookup for idempotent writes
        dedup_methods = {"upsert_lesson", "register_plan", "report_outcome"}
        if name in dedup_methods:
            # A064: Defer writes in hot path
            if is_hot_path and self._firewall_enabled:
                if self.current_phase == "macro" and name == "upsert_lesson":
                    coalesce_key = (name, args.get("domain"))
                    for idx, (existing_name, existing_args, _existing_timeout) in enumerate(self._deferred_writes):
                        if (existing_name, existing_args.get("domain")) == coalesce_key:
                            self._deferred_writes[idx] = (name, args, timeout or self.timeouts.get(name, 5.0))
                            return {
                                "status": "deferred",
                                "source": "firewall",
                                "memory_firewall_action": "deferred_coalesced",
                                "memory_firewall_reason": f"phase_{self.current_phase}_write_deferred"
                            }
                self._deferred_writes.append((name, args, timeout or self.timeouts.get(name, 5.0)))
                return {
                    "status": "deferred",
                    "source": "firewall",
                    "memory_firewall_action": "deferred",
                    "memory_firewall_reason": f"phase_{self.current_phase}_write_deferred"
                }

            dedup_key = (name, json.dumps(args, sort_keys=True))
            if dedup_key in self._dedup_cache:
                ts, payload = self._dedup_cache[dedup_key]
                if time.time() - ts < self._dedup_ttl:
                    import copy
                    dedup_resp = copy.deepcopy(payload)
                    if isinstance(dedup_resp, dict):
                        dedup_resp["source"] = "dedup"
                        dedup_resp["dedup_hit"] = True
                    return dedup_resp
                else:
                    del self._dedup_cache[dedup_key]

        # Use explicit timeout if provided, else fallback to per-tool override, else 5.0
        final_timeout = timeout
        if final_timeout is None:
            final_timeout = self.timeouts.get(name, 5.0)

        try:
            resp = await asyncio.to_thread(self._session.call_tool, name, args, final_timeout)
        except Exception as exc:
            # A091: Classify HTTP bridge timeouts as degraded reads/writes instead of crashing
            degradation_info = self._classify_mcp_transport_error(exc)
            if degradation_info["status"] == "degraded":
                self.memory_degraded = True
                self.memory_degraded_reason = degradation_info["memory_degraded_reason"]
                
                # Return appropriate degraded payload based on method type
                if name in {"current_truth", "recall_lessons", "recall_plans", "recall_relevant_lessons",
                           "analogical_search", "recall_scene_graph_priors", "recall_mechanic_priors",
                           "recall_procedures", "get_knowledge_gaps"}:
                    return self._degraded_read_payload(
                        degradation_info["error_code"],
                        degradation_info["memory_degraded_reason"],
                        degradation_info.get("mcp_transport")
                    )
                elif name in {"notify_turn", "upsert_lesson", "register_plan", "publish_mechanic_summary", "report_outcome"}:
                    return self._degraded_write_payload(
                        degradation_info["error_code"],
                        degradation_info["memory_degraded_reason"],
                        degradation_info.get("mcp_transport"),
                        deferred=True
                    )
            
            # A059: Better failure labeling for timeouts
            if "timeout" in str(exc).lower():
                return {
                    "status": "error",
                    "error": str(exc),
                    "source": "fallback",
                    "fallback_reason": "timeout",
                }
            raise

        if isinstance(resp, dict):
            resp["source"] = "fresh"
        
        # A091: Classify degradation from response metadata
        if isinstance(resp, dict) and resp.get("status") == "degraded":
            self.memory_degraded = True
            self.memory_degraded_reason = resp.get("memory_degraded_reason", "unknown_degradation")
        # A012: Diagnostic logging for queued_offline
        elif isinstance(resp, dict) and resp.get("status") == "queued_offline":
            logger.warning(
                "MCP tool %s returned 'queued_offline'. Ingest events may be delayed. "
                "Context: %s", name, resp.get("payload", {}).get("reason", "no reason provided")
            )
            self.memory_degraded = True
            self.memory_degraded_reason = "queued_offline"
        elif isinstance(resp, dict):
            err_text = str(resp.get("error") or "").lower()
            if "daemon_offline" in err_text:
                self.memory_degraded = True
                self.memory_degraded_reason = "daemon_offline"
            elif name in {"current_truth", "recall_relevant_lessons", "recall_plans", "analogical_search", "recall_procedures"} and resp.get("status") != "error":
                # A successful memory read proves the backend is responsive.
                self.memory_degraded = False
                self.memory_degraded_reason = ""

        # A012: Cache population
        if name in self._cached_methods and isinstance(resp, dict) and resp.get("status") != "error":
            if len(self._cache) >= self._cache_max_size:
                # Simple FIFO eviction
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[(name, json.dumps(args, sort_keys=True))] = (time.time(), resp)

        # A059: Dedup population
        if name in dedup_methods and isinstance(resp, dict) and resp.get("status") != "error":
            self._dedup_cache[(name, json.dumps(args, sort_keys=True))] = (time.time(), resp)

        return resp

    # --- ARC method-style wrappers ---
    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed: Optional[Dict[str, Any]] = None, async_dispatch: bool = True) -> Dict[str, Any]:
        """B111: Log a turn in the conversation history.
        A050/A051: Enforce strict async dispatch to avoid 10s memory stalls.
        """
        args = {"role": role, "content": content, "session_id": session_id}
        if precomputed is not None:
            args["precomputed"] = precomputed

        # A051: Remove mixed behavior. Always use background worker.
        self._ensure_notify_worker()
        try:
            # We ignore the caller's async_dispatch override and always queue.
            self._notify_queue.put_nowait((args, self.timeouts.get("notify_turn", 30.0)))
            return {"status": "queued_async", "mode": "async_background"}
        except asyncio.QueueFull:
            self._notify_dropped_count += 1
            logger.warning("A044/A051: Background notify_turn queue full, dropping event")
            return {"status": "dropped_full", "mode": "async_background"}

    async def current_truth(self, *, query: str, session_id: str, scope: str, limit: int, timeout: Optional[float] = None) -> Dict[str, Any]:
        args = {"query": query, "session_id": session_id, "scope": scope, "limit": limit}
        return await self.call_tool("current_truth", args, timeout=timeout)

    async def register_plan(self, *, goal: str, steps: List[str], session_id: str) -> Dict[str, Any]:
        args = {"goal": goal, "steps": steps, "session_id": session_id}
        payload = await self.call_tool("register_plan", args)
        if isinstance(payload, dict):
            plan_id = payload.get("plan_id") or payload.get("id")
            if plan_id in (None, "", "None"):
                payload = dict(payload)
                payload.setdefault("write_ok", False)
                payload.setdefault("error_code", "missing_plan_id")
                self.memory_degraded = True
                self.memory_degraded_reason = payload.get("status") or payload.get("error_code") or "missing_plan_id"
                logger.warning(
                    "B214: register_plan returned without plan_id (goal=%s status=%s)",
                    goal,
                    payload.get("status"),
                )
            else:
                payload = dict(payload)
                payload.setdefault("write_ok", True)
        return payload

    async def report_outcome(
        self,
        *,
        plan_id: Optional[str] = None,
        outcome: Optional[str] = None,
        outcome_text: Optional[str] = None,
        valence: float,
        session_id: str,
        evidence: Optional[Dict[str, Any]] = None,
        valence_source: Optional[str] = None,
    ) -> Dict[str, Any]:
        args: Dict[str, Any] = {"plan_id": plan_id, "valence": valence, "session_id": session_id}
        if outcome is not None:
            args["outcome"] = outcome
        if outcome_text is not None:
            args["outcome_text"] = outcome_text
        if evidence is not None:
            args["evidence"] = evidence
        if valence_source is not None:
            args["valence_source"] = valence_source
        return await self.call_tool("report_outcome", args)

    async def recall_plans(self, *, goal_query: str, session_id: str, min_valence: float, limit: int) -> Dict[str, Any]:
        args = {"goal_query": goal_query, "session_id": session_id, "min_valence": min_valence, "limit": limit}
        return await self.call_tool("recall_plans", args)

    async def recall_relevant_lessons(self, *, query: str, limit: int) -> Dict[str, Any]:
        args = {"query": query, "limit": limit}
        return await self.call_tool("recall_relevant_lessons", args)

    async def recall_lessons(self, *, lesson_type: str, scene_wl_hash: Optional[str] = None, archetype: Optional[str] = None, limit: int = 5) -> Dict[str, Any]:
        """A050: Retrieve lessons with graph facets."""
        args = {"lesson_type": lesson_type, "limit": limit}
        if scene_wl_hash:
            args["scene_wl_hash"] = scene_wl_hash
        if archetype:
            args["archetype"] = archetype
        return await self.call_tool("recall_lessons", args)

    async def analogical_search(self, *, query: Optional[str] = None, vector: Optional[Dict[str, int]] = None, current_quest_id: str, limit: int = 5, min_similarity: float = 0.5) -> Dict[str, Any]:
        """A050: Support precomputed WL-histogram vector."""
        args = {
            "current_quest_id": current_quest_id,
            "limit": limit,
            "min_similarity": min_similarity
        }
        if vector is not None:
            args["vector"] = vector
        if query is not None:
            args["query"] = query
        return await self.call_tool("analogical_search", args)

    async def recall_scene_graph_priors(self, *, scene_wl_hash: str, archetype: str, min_valence: float = 0.5, limit: int = 5) -> Dict[str, Any]:
        """A050: Retrieve expected progress based on scene structural similarity."""
        args = {
            "scene_wl_hash": scene_wl_hash,
            "archetype": archetype,
            "min_valence": min_valence,
            "limit": limit
        }
        return await self.call_tool("recall_scene_graph_priors", args)

    async def recall_mechanic_priors(
        self,
        *,
        signature: Dict[str, Any],
        limit: int = 5,
        min_confidence: float = 0.0,
    ) -> Dict[str, Any]:
        """A075: Recall reusable structural mechanics from aggregate memory."""
        args = {
            "signature": signature,
            "limit": limit,
            "min_confidence": min_confidence
        }
        try:
            resp = await self.call_tool("recall_mechanic_priors", args)
            if isinstance(resp, dict):
                results = resp.get("results", []) or []
                status = resp.get("status", "ok")
                resp.setdefault("prior_count", len(results))
                resp.setdefault("status", status)
                resp.setdefault("mechanic_prior_recall_status", status)
                resp.setdefault("mechanic_prior_count", len(results))
                resp.setdefault("mechanic_prior_error_code", resp.get("error_code"))
            return resp
        except (MCPError, MCPToolNotFound) as exc:
            if self._is_missing_tool_error(exc, "recall_mechanic_priors"):
                return {
                    "status": "capability_missing",
                    "mechanic_prior_recall_status": "capability_missing",
                    "mechanic_prior_count": 0,
                    "mechanic_prior_error_code": "capability_missing",
                    "prior_count": 0,
                    "source": "fallback",
                    "memory_degraded": False,
                    "memory_degraded_reason": "",
                    "capability_missing": "recall_mechanic_priors",
                    "results": [],
                }
            raise

    async def publish_mechanic_summary(
        self,
        *,
        summary: Dict[str, Any],
        async_dispatch: bool = True,
    ) -> Dict[str, Any]:
        """A075: Persist a learned per-game mechanic to aggregate memory."""
        args = {
            "summary": summary,
            "async_dispatch": async_dispatch
        }
        try:
            resp = await self.call_tool("publish_mechanic_summary", args)
            if isinstance(resp, dict):
                resp.setdefault("status", "ok")
                resp.setdefault("write_ok", True)
            return resp
        except (MCPError, MCPToolNotFound) as exc:
            if self._is_missing_tool_error(exc, "publish_mechanic_summary"):
                return {
                    "status": "capability_missing",
                    "source": "fallback",
                    "memory_degraded": False,
                    "memory_degraded_reason": "",
                    "capability_missing": "publish_mechanic_summary",
                    "write_ok": False,
                }
            raise

    async def upsert_lesson(self, *, domain: str, text: str, valence: float, confidence: float = 0.7, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {"domain": domain, "text": text, "valence": valence, "confidence": confidence}
        if tags is not None:
            args["tags"] = tags
        payload = await self.call_tool("upsert_lesson", args)
        if isinstance(payload, dict):
            lesson_id = payload.get("lesson_id") or payload.get("id")
            if lesson_id in (None, "", "None"):
                payload = dict(payload)
                payload.setdefault("write_ok", False)
                payload.setdefault("error_code", "missing_lesson_id")
                self.memory_degraded = True
                self.memory_degraded_reason = payload.get("status") or payload.get("error_code") or "missing_lesson_id"
                logger.warning(
                    "B214: upsert_lesson returned without lesson_id (domain=%s tags=%s status=%s)",
                    domain,
                    tags,
                    payload.get("status"),
                )
            else:
                payload = dict(payload)
                payload.setdefault("write_ok", True)
                self.memory_degraded = False
                self.memory_degraded_reason = ""
        return payload

    async def recall_procedures(self, *, archetype: str, limit: int = 3) -> Dict[str, Any]:
        args = {"archetype": archetype, "limit": limit}
        return await self.call_tool("recall_procedures", args)

    async def get_knowledge_gaps(self, *, domain: Optional[str] = None, limit: int = 10, unresolved_only: bool = True, min_severity: float = 0.0) -> Dict[str, Any]:
        args = {"domain": domain, "limit": limit, "unresolved_only": unresolved_only, "min_severity": min_severity}
        return await self.call_tool("get_knowledge_gaps", args)

    async def branch_quest(self, *, name: str, purpose: str, parent_quest_id: str) -> Dict[str, Any]:
        args = {"name": name, "purpose": purpose, "parent_quest_id": parent_quest_id}
        return await self.call_tool("branch_quest", args)

    async def register_task_graph(self, *, label: str, session_id: str, owner: str, tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
        args = {"label": label, "session_id": session_id, "owner": owner, "tasks": tasks}
        try:
            return await self.call_tool("register_task_graph", args)
        except Exception as e:
            if "violates the uniqueness constraint of the primary key column" in str(e):
                import logging
                logging.getLogger(__name__).warning(f"A019: Ignoring duplicate primary key on register_task_graph for {label}")
                return {"status": "ignored_duplicate"}
            raise

    async def get_ready_tasks(self, *, graph_id: str) -> Dict[str, Any]:
        args = {"graph_id": graph_id}
        return await self.call_tool("get_ready_tasks", args)

    async def advance_task(self, *, graph_id: str, task_id: str, status: str, result: Optional[str] = None) -> Dict[str, Any]:
        args = {"graph_id": graph_id, "task_id": task_id, "status": status, "result": result}
        return await self.call_tool("advance_task", args)

    async def fail_task(self, *, graph_id: str, task_id: str, reason: str) -> Dict[str, Any]:
        args = {"graph_id": graph_id, "task_id": task_id, "reason": reason}
        return await self.call_tool("fail_task", args)

    async def get_task_graph(self, *, graph_id: str) -> Dict[str, Any]:
        args = {"graph_id": graph_id}
        return await self.call_tool("get_task_graph", args)

    async def flush_deferred_writes(self, timeout: float = 10.0) -> Dict[str, Any]:
        """A064: Flush all deferred writes now."""
        if not self._deferred_writes:
            return {"status": "ok", "count": 0}
            
        count = len(self._deferred_writes)
        logger.info("A064: Flushing %d deferred writes", count)
        
        results = []
        old_phase = self.current_phase
        self.current_phase = "flushing"
        
        try:
            for name, args, t in self._deferred_writes:
                try:
                    res = await self.call_tool(name, args, timeout=t)
                    results.append(res)
                except Exception as exc:
                    logger.warning("A064: Deferred write %s failed during flush: %s", name, exc)
                    results.append({"status": "error", "error": str(exc)})
        finally:
            self.current_phase = old_phase
            self._deferred_writes.clear()
            
        return {"status": "ok", "count": count, "results": results}

    async def close(self) -> None:
        if self._notify_worker_task:
            self._notify_worker_task.cancel()
            try:
                await self._notify_worker_task
            except asyncio.CancelledError:
                pass
            self._notify_worker_task = None
        await asyncio.to_thread(self._session.close)

    # ── A105: Level Solution Template Support ──────────────────────────

    async def publish_level_solution_template(self, template: Dict[str, Any]) -> Dict[str, Any]:
        """A105: Publish a level solution template to aggregate memory.
        
        Args:
            template: LevelSolutionTemplate dict with goal_type, mechanic_signature, etc.
        
        Returns:
            Response dict with status and optional error.
        """
        try:
            result = await self.call_tool("publish_level_solution_template", {"template": template})
            return result or {"status": "ok", "template_id": template.get("id")}
        except MCPToolNotFound:
            # Tool not available, degrade gracefully
            logger.debug("A105: publish_level_solution_template not available in MCP server")
            return {"status": "capability_missing", "capability": "publish_level_solution_template"}
        except Exception as exc:
            logger.warning("A105: publish_level_solution_template failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    async def recall_level_solution_templates(
        self,
        signature: Dict[str, Any],
        limit: int = 5,
    ) -> Dict[str, Any]:
        """A105: Recall level solution templates matching a signature.
        
        Args:
            signature: Dict with goal_type, mechanic_signature, action_transform_signature.
            limit: Max templates to return.
        
        Returns:
            Response dict with templates list or empty list if not available.
        """
        try:
            result = await self.call_tool(
                "recall_level_solution_templates",
                {"signature": signature, "limit": limit},
            )
            return result or {"templates": []}
        except MCPToolNotFound:
            logger.debug("A105: recall_level_solution_templates not available in MCP server")
            return {"status": "capability_missing", "templates": []}
        except Exception as exc:
            logger.warning("A105: recall_level_solution_templates failed: %s", exc)
            return {"status": "error", "templates": []}

    async def __aenter__(self):
        await self.start()
        await self.initialize_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


__all__ = ["MCPBrainClient"]
