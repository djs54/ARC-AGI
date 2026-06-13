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
from benchmarks.arc3.state_serializer import StateSerializerForARC
from arc_runtime.config import load_config
from arc_runtime.llm import create_llm_client

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
        # A141: deterministic scripted mock game state keyed by guid.
        self._mock_games: Dict[str, Dict[str, Any]] = {}
        self._mock_active_guid_by_game: Dict[str, str] = {}

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
            brain_client = LocalBrainClient(self.db, self.config_data)
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

    @staticmethod
    def _new_mock_game_state(game_id: str, guid: str) -> Dict[str, Any]:
        """A141 scripted mock game:

        - 64x64 board
        - block A (color 3) starts at rows 10-13, cols 10-13
        - block B (color 5, toggles with ACTION4) at rows 10-13, cols 30-33
        - block C (color 7, target for ACTION6 levels) at rows 40-43, cols 20-23
        - win_levels=2, available_actions=[1,2,3,4,6]
        """
        return {
            "game_id": game_id,
            "guid": guid,
            "rows": 64,
            "cols": 64,
            "step_num": 0,
            "episode_num": 1,
            "state": "NOT_STARTED",
            "levels_completed": 0,
            "win_levels": 2,
            "available_actions": [1, 2, 3, 4, 6],
            "block_a_col": 10,
            "block_b_color": 5,
            "block_c_color": 7,
        }

    @staticmethod
    def _render_mock_grid(state: Dict[str, Any]) -> List[List[int]]:
        rows = int(state.get("rows", 64))
        cols = int(state.get("cols", 64))
        grid = [[0 for _ in range(cols)] for _ in range(rows)]

        block_a_col = int(state.get("block_a_col", 10))
        block_b_color = int(state.get("block_b_color", 5))
        block_c_color = int(state.get("block_c_color", 7))

        for r in range(10, 14):
            for c in range(block_a_col, block_a_col + 4):
                if 0 <= r < rows and 0 <= c < cols:
                    grid[r][c] = 3

        for r in range(10, 14):
            for c in range(30, 34):
                if 0 <= r < rows and 0 <= c < cols:
                    grid[r][c] = block_b_color

        for r in range(40, 44):
            for c in range(20, 24):
                if 0 <= r < rows and 0 <= c < cols:
                    grid[r][c] = block_c_color

        return grid

    def _mock_frame_response(self, state: Dict[str, Any], action_input: Dict[str, Any] | None) -> Dict[str, Any]:
        return {
            "game_id": state["game_id"],
            "guid": state["guid"],
            "frame": [self._render_mock_grid(state)],
            "state": state["state"],
            "levels_completed": int(state["levels_completed"]),
            "win_levels": int(state["win_levels"]),
            "available_actions": list(state["available_actions"]),
            "action_input": action_input,
            "episode_num": int(state.get("episode_num", 1)),
            "step_num": int(state.get("step_num", 0)),
        }

    def _get_mock_initial_frame(self, game_id: str) -> Dict[str, Any]:
        guid = f"guid-{game_id}"
        state = self._new_mock_game_state(game_id, guid)
        self._mock_games[guid] = state
        self._mock_active_guid_by_game[game_id] = guid
        return self._mock_frame_response(state, action_input=None)

    def _get_mock_action(self, obs: Dict[str, Any], variant: ABVariant, step: int) -> Dict[str, Any]:
        # A141: deterministic sidequests sequence for scripted mock (wins by two ACTION6 clicks on block C).
        if variant == ABVariant.SIDEQUESTS:
            if step <= 1:
                return {"action_id": "ACTION6", "x": 21, "y": 41, "rationale": "target block C centroid"}
            return {"action_id": "ACTION3", "rationale": "probe no-op"}
        else:
            # Baseline chooses random low-information actions.
            action_num = random.choice([1, 2, 3, 4])
            return {"action_id": f"ACTION{action_num}", "rationale": "baseline choice"}

    def _execute_mock_action(self, game_id: str, action: Dict[str, Any], step: int) -> Tuple[Dict[str, Any], float, bool]:
        # A141: contract-faithful deterministic scripted game. No step-count auto-WIN.
        action_id_raw = str(action.get("action_id", "ACTION3")).upper()
        if action_id_raw.isdigit():
            action_id = f"ACTION{action_id_raw}"
        else:
            action_id = action_id_raw

        guid = str(action.get("guid") or self._mock_active_guid_by_game.get(game_id) or f"guid-{game_id}")
        state = self._mock_games.get(guid)
        if state is None:
            state = self._new_mock_game_state(game_id, guid)
            self._mock_games[guid] = state
            self._mock_active_guid_by_game[game_id] = guid

        level_before = int(state.get("levels_completed", 0))

        if action_id == "ACTION1":
            state["block_a_col"] = min(58, int(state.get("block_a_col", 10)) + 2)
            state["state"] = "NOT_FINISHED"
        elif action_id == "ACTION2":
            state["block_a_col"] = max(0, int(state.get("block_a_col", 10)) - 2)
            state["state"] = "NOT_FINISHED"
        elif action_id == "ACTION3":
            state["state"] = "NOT_FINISHED"
        elif action_id == "ACTION4":
            state["block_b_color"] = 9 if int(state.get("block_b_color", 5)) == 5 else 5
            state["state"] = "NOT_FINISHED"
        elif action_id == "ACTION6":
            x = int(action.get("x", 0) or 0)
            y = int(action.get("y", 0) or 0)
            if 20 <= x <= 23 and 40 <= y <= 43:
                state["levels_completed"] = min(int(state.get("win_levels", 2)), int(state.get("levels_completed", 0)) + 1)
                # Recolor block C after each successful click to make level progression visible.
                levels = int(state.get("levels_completed", 0))
                state["block_c_color"] = 7 if levels == 0 else 8 if levels == 1 else 9
                state["state"] = "WIN" if int(state.get("levels_completed", 0)) >= int(state.get("win_levels", 2)) else "NOT_FINISHED"
            else:
                state["state"] = "NOT_FINISHED"
        else:
            state["state"] = "NOT_FINISHED"

        state["step_num"] = int(step) + 1
        self._mock_games[guid] = state

        level_after = int(state.get("levels_completed", 0))
        level_gain = max(0, level_after - level_before)
        done = str(state.get("state", "NOT_FINISHED")) in ("WIN", "GAME_OVER")
        reward = 1.0 if state.get("state") == "WIN" else float(level_gain)

        frame = self._mock_frame_response(state, action_input={
            "action_id": action_id,
            **({"x": int(action.get("x", 0) or 0), "y": int(action.get("y", 0) or 0)} if action_id == "ACTION6" else {}),
        })
        return (frame, reward, done)

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
