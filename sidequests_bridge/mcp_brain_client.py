"""MCP-backed Brain client for ARC runtime.

Provides async method-style wrappers that map ARC runtime calls to SideQuests
MCP tool names. The client is a thin async wrapper around the
`MCPStdIOSession` transport implemented in `sidequests_bridge.mcp_session`.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from .mcp_session import MCPStdIOSession, MCPToolNotFound


class MCPBrainClient:
    """Async ARC-facing brain client backed by an MCP stdio session.

    The client lazily starts and initializes the underlying MCP session
    on first use to preserve compatibility with existing runtime wiring.
    """

    def __init__(self, db: Optional[Any] = None, config: Optional[Dict[str, Any]] = None, cmd: Optional[List[str]] = None, session: Optional[MCPStdIOSession] = None):
        self.db = db
        self.config = config
        self._cmd = cmd
        self._session = session or MCPStdIOSession(cmd=self._cmd)
        self._started = False
        self._initialized = False
        self._init_payload: Optional[Dict[str, Any]] = None

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

    async def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Dict[str, Any]:
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
        return await asyncio.to_thread(self._session.call_tool, name, arguments or {}, timeout)

    # --- ARC method-style wrappers ---
    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        args = {"role": role, "content": content, "session_id": session_id}
        if precomputed is not None:
            args["precomputed"] = precomputed
        return await self.call_tool("notify_turn", args)

    async def current_truth(self, *, query: str, session_id: str, scope: str, limit: int) -> Dict[str, Any]:
        args = {"query": query, "session_id": session_id, "scope": scope, "limit": limit}
        return await self.call_tool("current_truth", args)

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
        return await self.call_tool("register_task_graph", args)

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
