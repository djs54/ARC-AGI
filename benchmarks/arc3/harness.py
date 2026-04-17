"""
ARC-AGI-3 A/B Harness (Baseline vs SideQuests-Augmented)

Implements the A/B evaluation for ARC puzzles, measuring memory impact on solve rate,
step count, and token efficiency.
"""

from __future__ import annotations
import asyncio
import inspect
import json
import time
import os
import httpx
import uuid
import random
import logging
from typing import Dict, Any, List, Optional, Tuple

from benchmarks.ab_harness import ABHarness, ABVariant, ABTask, ABTaskResult, ABTaskManifest
from benchmarks.arc3.adapter import ARC3Adapter, BrainClientProtocol, NoOpBrainClient, LocalBrainClient
from sidequests_bridge.mcp_brain_client import MCPBrainClient
from benchmarks.arc3.state_serializer import StateSerializerForARC
from sidequests_bridge.runtime import create_llm_client, load_config

logger = logging.getLogger(__name__)


class ARC3Harness(ABHarness):
    """
    A/B runner for ARC-AGI-3.
    """

    def __init__(self, config, global_seed=42, db=None, mock_api=False):
        super().__init__(config, global_seed)
        self.db = db
        self.mock_api = mock_api
        self.config_data = load_config()
        self.llm_client = None
        self.serializer = StateSerializerForARC()
        self._reflex_context = None
        self.api_key = self._load_arc_api_key()
        self.api_base = "https://three.arcprize.org"
        self._session = None  # httpx.AsyncClient

    async def setup(self) -> None:
        """Initialize LLM client and other resources."""
        self.llm_client = create_llm_client(self.config_data)
        if not self.mock_api:
            if not self.api_key:
                raise RuntimeError(
                    "Missing ARC API key. Set ARC_API_KEY (preferred) or arc_api_key in config. "
                    "Legacy Kaggle key fallback was not found."
                )
            self._session = httpx.AsyncClient(base_url=self.api_base, headers={"X-API-Key": self.api_key}, timeout=30.0)

    async def teardown(self) -> None:
        """Clean up resources."""
        if self._session:
            await self._session.aclose()

    @staticmethod
    async def _maybe_await(value: Any) -> Any:
        if inspect.isawaitable(value):
            return await value
        return value

    async def _safe_raise_for_status(self, response: Any) -> None:
        raise_for_status = getattr(response, "raise_for_status", None)
        if callable(raise_for_status):
            await self._maybe_await(raise_for_status())

    async def _safe_json(self, response: Any) -> Any:
        json_method = getattr(response, "json", None)
        if not callable(json_method):
            return {}
        return await self._maybe_await(json_method())

    async def list_games(self) -> List[Dict[str, Any]]:
        """Return the current live ARC game list from the API."""
        if self.mock_api:
            return []
        if not self._session:
            raise RuntimeError("API session not initialized. Did you call setup()?")
        resp = await self._session.get("/api/games")
        await self._safe_raise_for_status(resp)
        data = await self._safe_json(resp)
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected /api/games response type: {type(data).__name__}")
        return data

    async def _execute_task(
        self,
        task: ABTask,
        variant: ABVariant,
        reflex_context: Dict[str, Any] | None = None,
    ) -> ABTaskResult:
        """
        Execute a single ARC task (game) for the given variant.
        """
        self._reflex_context = reflex_context
        session_id = f"arc-{variant}-{uuid.uuid4().hex[:8]}"
        
        # Determine which brain client to use
        if variant == ABVariant.SIDEQUESTS and self.db:
            brain_client = MCPBrainClient(self.db, self.config_data)
        else:
            brain_client = NoOpBrainClient()

        adapter = ARC3Adapter(
            brain_client=brain_client,
            session_id=session_id,
            task_id=task.task_id
        )

        # Reset counters for the task
        steps = 0
        total_tokens_input = 0
        total_tokens_output = 0
        success = False
        error_msg = None
        
        # Max attempts from config
        max_attempts = self.config.parameters.get("max_attempts_per_puzzle", 10)
        
        # Start game session
        game_id = getattr(task, "game_id", "unknown")
        
        try:
            if self.mock_api:
                frame_response = self._get_mock_initial_frame(game_id)
            else:
                # Real API: open scorecard, reset game, and play
                if not self._session:
                    raise RuntimeError("API session not initialized. Did you call setup()?")
                # 1. Open scorecard
                scorecard_resp = await self._session.post("/api/scorecard/open", json={})
                await self._safe_raise_for_status(scorecard_resp)
                card_id = (await self._safe_json(scorecard_resp))["card_id"]
                # 2. Reset game (start session)
                reset_payload = {"game_id": game_id, "card_id": card_id}
                reset_resp = await self._session.post("/api/cmd/RESET", json=reset_payload)
                await self._safe_raise_for_status(reset_resp)
                frame_response = await self._safe_json(reset_resp)
                guid = frame_response["guid"]
            while steps < max_attempts:
                obs = adapter.normalize_observation(frame_response)
                if self.mock_api:
                    raw_action = self._get_mock_action(obs, variant, steps)
                else:
                    raw_action = await self._get_llm_action(obs, variant)
                total_tokens_input += self.serializer._estimate_tokens(str(obs))
                total_tokens_output += self.serializer._estimate_tokens(str(raw_action))
                if self.mock_api:
                    frame_response, reward, done = self._execute_mock_action(game_id, raw_action, steps)
                else:
                    # Real API: choose endpoint based on action_id
                    action_id = raw_action.get("action_id", "ACTION1")
                    action_payload = {"game_id": game_id, "guid": guid}
                    if action_id == "ACTION6":
                        action_payload["x"] = raw_action.get("x", 0)
                        action_payload["y"] = raw_action.get("y", 0)
                        if "rationale" in raw_action:
                            action_payload["reasoning"] = raw_action["rationale"]
                    else:
                        if "rationale" in raw_action:
                            action_payload["reasoning"] = raw_action["rationale"]
                    action_resp = await self._session.post(f"/api/cmd/{action_id}", json=action_payload)
                    await self._safe_raise_for_status(action_resp)
                    frame_response = await self._safe_json(action_resp)
                    reward = 1.0 if frame_response.get("state") == "WIN" else 0.0
                    done = frame_response.get("state") in ("WIN", "GAME_OVER")
                recall_query = "What did I learn from similar puzzles?" if variant == ABVariant.SIDEQUESTS else None
                await adapter.ingest_step(frame_response, raw_action, reward=reward, recall_query=recall_query)
                steps += 1
                if done:
                    success = (reward >= 1.0)
                    break
            if not success and steps >= max_attempts:
                error_msg = "Max attempts reached"
        except Exception as e:
            error_msg = str(e)
            success = False
        return ABTaskResult(
            task_id=task.task_id,
            variant=variant,
            correct=success,
            steps=steps,
            tokens_input=total_tokens_input,
            tokens_output=total_tokens_output,
            error_message=error_msg,
            response_text=f"Solved: {success} in {steps} steps"
        )

    async def _get_llm_action(self, obs: Dict[str, Any], variant: ABVariant) -> Dict[str, Any]:
        """Call the LLM to choose an ARC action."""
        if not self.llm_client:
            return self._get_mock_action(obs, variant, 0)
            
        # Create prompt with observation
        prefix = ""
        if variant == ABVariant.SIDEQUESTS and self._reflex_context:
            prefix = f"REFLEX CONTEXT: {json.dumps(self._reflex_context)}\n\n"
            
        prompt = f"{prefix}ARC Observation: {json.dumps(obs)}\nChoose next action (ACTION1-ACTION7):"
        
        # In a real implementation, we'd use a more sophisticated prompt and parse JSON
        messages = [{"role": "user", "content": prompt}]
        response = await asyncio.to_thread(self.llm_client.chat, messages)
        
        # Simple parser for demonstration
        try:
            return json.loads(response)
        except:
            return {"action_id": "ACTION1", "rationale": "fallback"}

    # --- Mock Methods for testing/demo ---

    def _get_mock_initial_frame(self, game_id: str) -> Dict[str, Any]:
        return {
            "game_id": game_id,
            "guid": f"guid-{game_id}",
            "frame": [[[0, 0], [0, 0]]],
            "state": "NOT_FINISHED",
            "episode_num": 1,
            "step_num": 1
        }

    def _get_mock_action(self, obs: Dict[str, Any], variant: ABVariant, step: int) -> Dict[str, Any]:
        # If SIDEQUESTS, choose a 'better' action
        if variant == ABVariant.SIDEQUESTS:
            return {"action_id": "ACTION6", "x": 1, "y": 1, "value": 1, "rationale": "informed choice"}
        else:
            # Baseline chooses random or less effective actions
            return {"action_id": f"ACTION{random.randint(1, 5)}", "rationale": "baseline choice"}

    def _execute_mock_action(self, game_id: str, action: Dict[str, Any], step: int) -> Tuple[Dict[str, Any], float, bool]:
        # Simple mock logic: success on step 2 for SIDEQUESTS, step 5 for BASELINE
        action_id = action.get("action_id", "")
        
        if action_id == "ACTION6" and step >= 1:
            frame = self._get_mock_initial_frame(game_id)
            frame["state"] = "WIN"  # Game won
            return (frame, 1.0, True)
        
        if step >= 4:
            frame = self._get_mock_initial_frame(game_id)
            frame["state"] = "WIN"  # Game won
            return (frame, 1.0, True)
        
        # Still in progress
        frame = self._get_mock_initial_frame(game_id)
        frame["state"] = "NOT_FINISHED"
        return (frame, 0.0, False)

    def _load_arc_api_key(self) -> Optional[str]:
        """Load ARC API key, preferring explicit ARC credentials over legacy Kaggle fallback."""
        explicit_key = (
            os.environ.get("ARC_API_KEY")
            or self.config_data.get("arc_api_key")
            or os.environ.get("KAGGLE_API_KEY")
            or self.config_data.get("kaggle_api_key")
        )
        if explicit_key:
            return explicit_key.strip()

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

        # Preferred fallback: repo-local ARC credential file used by live smoke runs.
        arc_json_paths = [
            os.path.join(root_dir, "benchmarks", ".arc", "arc.json"),
            os.path.join(root_dir, "benchmarks", "arc3", ".arc", "arc.json"),
        ]
        for arc_json_path in arc_json_paths:
            try:
                with open(arc_json_path, "r") as f:
                    arc_data = json.load(f)
                key = str(arc_data.get("key") or "").strip()
                if key:
                    logger.info("Loaded ARC API key from %s", arc_json_path)
                    return key
            except Exception:
                continue

        # Legacy fallback: Kaggle credential file. This may not work for the ARC REST API,
        # but we keep it for backward compatibility with older local setups.
        kaggle_json_paths = [
            os.path.join(root_dir, "benchmarks", ".kaggle", "kaggle.json"),
            os.path.join(os.path.dirname(__file__), ".kaggle", "kaggle.json"),
        ]
        for kaggle_json_path in kaggle_json_paths:
            try:
                with open(kaggle_json_path, "r") as f:
                    kaggle_data = json.load(f)
                key = (kaggle_data.get("key") or "").strip()
                if key:
                    logger.warning(
                        "Using legacy key from %s. If ARC returns 401, set ARC_API_KEY from three.arcprize.org instead.",
                        kaggle_json_path,
                    )
                    return key
            except Exception:
                continue

        return None


def load_tasks_from_manifest(manifest_path: str) -> List[ABTask]:
    """Load tasks from a JSON manifest."""
    with open(manifest_path, 'r') as f:
        data = json.load(f)
    
    tasks = []
    for t in data["tasks"]:
        task = ABTask(
            task_id=t["task_id"],
            category=t["category"],
            prompt=t["prompt"]
        )
        # Add extra fields needed for ARC
        setattr(task, "game_id", t.get("game_id", "unknown"))
        tasks.append(task)
    return tasks
