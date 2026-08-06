"""StrategyRacer: run multiple strategy variants in parallel and pick a winner.

This module implements a lightweight racing coordinator used by B187.
It intentionally buffers side-effecting BrainClient writes per-variant and
replays them only for the winning variant, enabling safe concurrent runs.
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from benchmarks.arc3.adapter import LedgerBrainClient, BrainClientProtocol


class BufferedBrainClient:
    """Buffer write calls and optionally replay them later.

    The buffered client returns placeholder responses so orchestrators can run
    without hitting the real DB. Call `commit()` to replay buffered writes
    to the underlying `inner` client, or `discard()` to drop them.
    """

    def __init__(self, inner: BrainClientProtocol):
        self.inner = inner
        self._buffer: List[Tuple[str, dict]] = []

    @property
    def db(self) -> Optional[Any]:
        return getattr(self.inner, "db", None)

    # --- Read passthroughs -------------------------------------------------
    async def current_truth(self, *, query: str, session_id: str, scope: str, limit: int):
        return await self.inner.current_truth(query=query, session_id=session_id, scope=scope, limit=limit)

    async def recall_relevant_lessons(self, *, query: str, limit: int):
        return await self.inner.recall_relevant_lessons(query=query, limit=limit)

    async def recall_plans(self, *, goal_query: str, session_id: str, min_valence: float, limit: int):
        return await self.inner.recall_plans(goal_query=goal_query, session_id=session_id, min_valence=min_valence, limit=limit)

    async def get_ready_tasks(self, *, graph_id: str):
        return await self.inner.get_ready_tasks(graph_id=graph_id)

    async def get_task_graph(self, *, graph_id: str):
        return await self.inner.get_task_graph(graph_id=graph_id)

    # --- Buffered writes --------------------------------------------------
    async def notify_turn(self, *, role: str, content: str, session_id: str, precomputed: Optional[Mapping[str, Any]] = None, async_dispatch: bool = False):
        self._buffer.append(("notify_turn", {"role": role, "content": content, "session_id": session_id, "precomputed": precomputed, "async_dispatch": async_dispatch}))
        return {"status": "buffered"}

    async def register_plan(self, *, goal: str, steps: List[str], session_id: str):
        plan_id = f"buf-plan-{uuid.uuid4().hex[:8]}"
        self._buffer.append(("register_plan", {"goal": goal, "steps": steps, "session_id": session_id, "_plan_id": plan_id}))
        return {"plan_id": plan_id, "warnings": [], "suggestions": []}

    async def report_outcome(self, *, plan_id: Optional[str] = None, outcome: Optional[str] = None, outcome_text: Optional[str] = None, valence: float = 0.0, session_id: str = "", evidence: Optional[Mapping[str, Any]] = None, valence_source: Optional[str] = None):
        self._buffer.append(("report_outcome", {"plan_id": plan_id, "outcome": outcome or outcome_text, "valence": valence, "session_id": session_id, "evidence": evidence, "valence_source": valence_source}))
        return {"updated": True}

    async def branch_quest(self, *, name: str, purpose: str, parent_quest_id: str):
        sid = f"buf-sq-{uuid.uuid4().hex[:8]}"
        self._buffer.append(("branch_quest", {"name": name, "purpose": purpose, "parent_quest_id": parent_quest_id, "_side_quest_id": sid}))
        return {"side_quest_id": sid, "name": name, "parent_quest_id": parent_quest_id}

    async def register_task_graph(self, *, label: str, session_id: str, owner: str, tasks: List[Mapping[str, Any]]):
        gid = f"buf-graph-{uuid.uuid4().hex[:8]}"
        self._buffer.append(("register_task_graph", {"label": label, "session_id": session_id, "owner": owner, "tasks": tasks, "_graph_id": gid}))
        return {"graph_id": gid, "task_ids": [], "ready_tasks": [], "cycle_errors": []}

    async def advance_task(self, *, graph_id: str, task_id: str, status: str, result: Optional[str] = None):
        # advance_task is stateful; buffer it and return a placeholder
        self._buffer.append(("advance_task", {"graph_id": graph_id, "task_id": task_id, "status": status, "result": result}))
        return {"task_id": task_id, "new_status": status, "newly_unblocked": []}

    async def fail_task(self, *, graph_id: str, task_id: str, reason: str):
        self._buffer.append(("fail_task", {"graph_id": graph_id, "task_id": task_id, "reason": reason}))
        return {"task_id": task_id, "status": "failed", "blocked_dependents": []}

    async def store_lesson(self, *, content: str, tags: List[str], session_id: str):
        lid = f"buf-lesson-{uuid.uuid4().hex[:8]}"
        self._buffer.append(("store_lesson", {"content": content, "tags": tags, "session_id": session_id, "_lesson_id": lid}))
        return {"lesson_id": lid}

    # Generic passthrough for other optional methods
    def __getattr__(self, name: str):
        # For any other method calls we haven't explicitly implemented,
        # return a coroutine wrapper that records the call and returns a placeholder.
        async def _wrapped(*args, **kwargs):
            self._buffer.append((name, {"args": args, "kwargs": kwargs}))
            return {"status": "buffered", "method": name}
        return _wrapped

    # --- Buffer management -----------------------------------------------
    async def commit(self) -> List[Any]:
        """Replay buffered calls against the underlying inner client in order."""
        results: List[Any] = []
        for name, params in list(self._buffer):
            try:
                method = getattr(self.inner, name, None)
                if callable(method):
                    # Remove any internal placeholders before calling
                    p = dict(params)
                    p.pop("_plan_id", None)
                    p.pop("_side_quest_id", None)
                    p.pop("_graph_id", None)
                    p.pop("_lesson_id", None)
                    # Await the actual call
                    res = await method(**p)
                    results.append(res)
                else:
                    results.append({"skipped": name})
            except Exception:
                # Best-effort: continue replaying others
                results.append({"error": name})
        # Clear buffer after committing
        self._buffer.clear()
        return results

    def discard(self) -> None:
        self._buffer.clear()


async def race(
    runner: Any,
    task: Any,
    variants: Optional[List[str]] = None,
    variant_runner: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Run multiple strategy variants concurrently and select a winner.

    - `runner` is the DurableARCRunner instance (used for harness, config, raw brain)
    - `task` is the ABTask to solve
    - `variants` is a list of variant identifiers (e.g., ['A','B','C'])
    - `variant_runner` is an optional coroutine function taking `(variant_brain, session_id, task, variant_config)`
      and returning `(task_result, duration, orchestrator)`; when omitted the caller should provide one.

    Returns a dict containing winner metadata and the winning result.
    """

    if variants is None:
        variants = ["A", "B", "C"]

    if variant_runner is None:
        # The caller must supply a runner capable of executing a single variant
        raise RuntimeError("StrategyRacer requires a variant_runner coroutine when used by default")

    num = len(variants)
    tasks_info: List[Tuple[asyncio.Task, str, Any, Any, List[dict], dict]] = []

    for v in variants:
        # Per-variant config copy
        vcfg = copy.deepcopy(runner.config if isinstance(runner.config, dict) else {})
        # Apply simple strategy-modifiers per plan
        strat = vcfg.setdefault("strategy", {})
        if v == "B":
            strat["exploration_multiplier"] = max(int(strat.get("exploration_multiplier", 1)), 1) * 2
        if v == "C":
            strat["pattern_first"] = True

        # Token budget splitting (B180)
        cost_cfg = vcfg.setdefault("cost", {})
        base_budget = None
        try:
            base_budget = float((runner.config.get("cost") or {}).get("budget_per_puzzle_usd"))
        except Exception:
            base_budget = None
        if base_budget and num > 0:
            cost_cfg["budget_per_puzzle_usd"] = base_budget / float(num)

        # Session isolation
        session_id = f"arc-{task.task_id}-{v}-{uuid.uuid4().hex[:8]}"

        # Buffer writes per-variant so we can discard losers
        buffered = BufferedBrainClient(runner._raw_brain)
        ledger: List[dict] = []
        # Use LedgerBrainClient to preserve ledger semantics expected by orchestrator
        variant_brain = LedgerBrainClient(
            inner=buffered, 
            ledger=ledger, 
            step_provider=lambda: 0,
            observability=getattr(runner, "observability", None)
        )

        # variant_runner is expected to be a coroutine that creates the orchestrator
        # and executes the variant, returning (task_result, duration, orchestrator)
        coro = variant_runner(variant_brain, session_id, task, vcfg)

        async def _wrap(vname: str, inner_coro):
            res = await inner_coro
            return (vname, res)

        task_obj = asyncio.create_task(_wrap(v, coro))
        tasks_info.append((task_obj, v, buffered, variant_brain, ledger, vcfg))

    winner_info: Optional[Dict[str, Any]] = None
    best_failed: Optional[Dict[str, Any]] = None

    # Iterate in completion order
    for fut in asyncio.as_completed([t for (t, *_rest) in tasks_info]):
        task_obj = fut
        try:
            v_finished, res = await task_obj
        except asyncio.CancelledError:
            continue
        except Exception as e:
            # record failure
            # locate meta in tasks_info
            meta = next((item for item in tasks_info if item[0] is task_obj), None)
            if meta and not best_failed:
                _, v, buf, vbrain, vledger, vcfg = meta
                best_failed = {"variant": v, "exception": e}
            continue

        # Locate buffer/ledger for this variant from tasks_info
        meta = next((item for item in tasks_info if item[1] == v_finished), None)
        if not meta:
            continue
        _t, v, buf, vbrain, vledger, vcfg = meta

        # Expect variant_runner to return (task_result, duration, orchestrator)
        try:
            task_result, duration, orchestrator = res
        except Exception:
            if not best_failed:
                best_failed = {"variant": v, "result": res}
            continue

        # First-to-solve wins
        if getattr(task_result, "correct", False):
            winner_info = {
                "variant": v,
                "task_result": task_result,
                "duration": duration,
                "orchestrator": orchestrator,
                "buffered": buf,
                "ledger": vledger,
                "config": vcfg,
            }
            break
        else:
            if not best_failed:
                best_failed = {"variant": v, "task_result": task_result, "duration": duration, "orchestrator": orchestrator, "buffered": buf, "ledger": vledger, "config": vcfg}

    # If none succeeded, pick best_failed if present
    if winner_info is None and best_failed:
        winner_info = best_failed

    # Cancel any remaining tasks and discard their buffers
    for t_obj, v, buf, vbrain, vledger, vcfg in list(tasks_info):
        if not t_obj.done():
            t_obj.cancel()
            try:
                await t_obj
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            try:
                buf.discard()
            except Exception:
                pass

    if winner_info is None:
        raise RuntimeError("StrategyRacer: no variant produced a usable result")

    # Commit winner buffered writes and return winner info
    winner_buf: BufferedBrainClient = winner_info.get("buffered")
    try:
        await winner_buf.commit()
    except Exception:
        # best-effort
        pass

    return winner_info
