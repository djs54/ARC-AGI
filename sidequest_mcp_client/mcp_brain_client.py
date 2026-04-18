"""MCP-backed Brain client for ARC runtime.

Provides async method-style wrappers that map ARC runtime calls to SideQuests
MCP tool names. The client is a thin async wrapper around the
`MCPStdIOSession` transport implemented in `sidequest_mcp_client.mcp_session`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from .mcp_session import MCPStdIOSession, MCPToolNotFound
from .readiness import _cmd_from_env


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
            "upsert_lesson": 30.0,  # slow DB write
        }
        # A012: In-memory cache for high-frequency idempotent retrieval
        self._cache: Dict[tuple, tuple[float, Any]] = {}
        self._cache_ttl = 15.0
        self._cache_max_size = 32
        self._cached_methods = {"current_truth", "recall_relevant_lessons"}

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
                    return copy.deepcopy(payload)
                else:
                    del self._cache[cache_key]

        # Use explicit timeout if provided, else fallback to per-tool override, else 5.0
        final_timeout = timeout
        if final_timeout is None:
            final_timeout = self.timeouts.get(name, 5.0)

        resp = await asyncio.to_thread(self._session.call_tool, name, args, final_timeout)
        
        # A012: Diagnostic logging for queued_offline
        if isinstance(resp, dict) and resp.get("status") == "queued_offline":
            import logging
            logging.getLogger("mcp_client").warning(
                "MCP tool %s returned 'queued_offline'. Ingest events may be delayed. "
                "Context: %s", name, resp.get("payload", {}).get("reason", "no reason provided")
            )

        # A012: Cache population
        if name in self._cached_methods and isinstance(resp, dict) and resp.get("status") != "error":
            if len(self._cache) >= self._cache_max_size:
                # Simple FIFO eviction
                oldest_key = next(iter(self._cache))
                del self._cache[oldest_key]
            self._cache[(name, json.dumps(args, sort_keys=True))] = (time.time(), resp)

        return resp

    # --- ARC method-style wrappers ---
    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        args = {"role": role, "content": content, "session_id": session_id}
        if precomputed is not None:
            args["precomputed"] = precomputed
        return await self.call_tool("notify_turn", args)

    async def current_truth(self, *, query: str, session_id: str, scope: str, limit: int, timeout: Optional[float] = None) -> Dict[str, Any]:
        args = {"query": query, "session_id": session_id, "scope": scope, "limit": limit}
        return await self.call_tool("current_truth", args, timeout=timeout)

    async def register_plan(self, *, goal: str, steps: List[str], session_id: str) -> Dict[str, Any]:
        args = {"goal": goal, "steps": steps, "session_id": session_id}
        return await self.call_tool("register_plan", args)

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

    async def analogical_search(self, *, query: str, current_quest_id: str, limit: int, min_similarity: float) -> Dict[str, Any]:
        args = {"query": query, "current_quest_id": current_quest_id, "limit": limit, "min_similarity": min_similarity}
        return await self.call_tool("analogical_search", args)

    async def upsert_lesson(self, *, domain: str, text: str, valence: float, confidence: float = 0.7, tags: Optional[List[str]] = None) -> Dict[str, Any]:
        args: Dict[str, Any] = {"domain": domain, "text": text, "valence": valence, "confidence": confidence}
        if tags is not None:
            args["tags"] = tags
        return await self.call_tool("upsert_lesson", args)

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

    async def close(self) -> None:
        await asyncio.to_thread(self._session.close)

    async def __aenter__(self):
        await self.start()
        await self.initialize_session()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        await self.close()


__all__ = ["MCPBrainClient"]
