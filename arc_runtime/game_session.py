from __future__ import annotations

import re
from typing import Any, Mapping

import httpx

from benchmarks.arc3.adapter import ARC3Adapter, NoOpBrainClient
from benchmarks.arc3.harness import ARC3Harness

# A238 Track B: the only action_ids the real ARC API's /api/cmd/ endpoint
# ever accepts (per this file's own open()/execute_action bare-ACTION6
# usage -- coordinates travel via the JSON payload, never the id itself).
# Defense-in-depth backstop for whatever produced plan_generator.py's
# synthetic "probe-{goal_id}" fallback action_id (fixed at the source in
# that file as part of this same card) -- confirmed live that an invalid
# action_id reaching this method guaranteed a 404 on every single call, 28
# wasted real API calls across this session's saved live-smoke logs. This
# check rejects the request locally, before the network round-trip, with
# the same CRASH-shaped failure the real 404 already produced (Executor.execute
# wraps ANY exception raised here into the same PhaseStatus.CRASH shape), so
# the only behavior change is that a bad id never actually reaches the live
# API or consumes a real request against ARC's rate limit.
_VALID_ACTION_ID = re.compile(r"^ACTION[1-7]$")


class ArcV2GameSession:
    def __init__(self, harness: ARC3Harness, *, game_id: str, card_id: str, real_api: bool) -> None:
        self._harness = harness
        self._game_id = game_id
        self._card_id = card_id
        self._real_api = real_api
        self._client: httpx.Client | None = None
        self._guid: str | None = None
        self._prev_levels_completed: int = 0
        self._prev_grid_hash: str | None = None

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
            payload = _unwrap_arc_game_payload(reset_resp.json())
            self._guid = str(payload.get("guid") or "") or None
            initial_observation = ARC3Adapter(NoOpBrainClient(), session_id="arc-v2-init", task_id=self._game_id).normalize_observation(payload)
            self._prev_levels_completed = int(payload.get("levels_completed") or 0)
            self._prev_grid_hash = str(initial_observation.get("frame_hash") or "") or None
            return payload

        payload = self._harness._get_mock_initial_frame(self._game_id)
        initial_observation = ARC3Adapter(NoOpBrainClient(), session_id="arc-v2-init", task_id=self._game_id).normalize_observation(payload)
        self._prev_levels_completed = int(payload.get("levels_completed") or 0)
        self._prev_grid_hash = str(initial_observation.get("frame_hash") or "") or None
        return payload

    def execute_action(self, action_id: str, payload: Mapping[str, Any], context: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._real_api:
            if self._client is None:
                raise RuntimeError("ARC v2 game session not opened")
            if not _VALID_ACTION_ID.match(action_id):
                # A238 Track B: fail locally, zero network cost, instead of
                # sending a guaranteed-404 request to the live ARC API.
                raise ValueError(
                    f"invalid action_id for live ARC transport: {action_id!r} "
                    "(expected ACTION1-ACTION7)"
                )
            request_payload: dict[str, Any] = {"game_id": self._game_id, "guid": self._guid}
            request_payload.update(dict(payload))
            if action_id == "ACTION6":
                request_payload.setdefault("x", payload.get("x", 0))
                request_payload.setdefault("y", payload.get("y", 0))
            response = self._client.post(f"/api/cmd/{action_id}", json=request_payload)
            response.raise_for_status()
            frame_response = _unwrap_arc_game_payload(response.json())
            observation = ARC3Adapter(NoOpBrainClient(), session_id=str(context.get("session_id") or "arc-v2"), task_id=self._game_id).normalize_observation(frame_response)
            progress = _compute_progress(
                frame_response,
                self._prev_levels_completed,
                self._prev_grid_hash,
                str(observation.get("frame_hash") or "") or None,
            )
            self._prev_levels_completed = int(progress["levels_completed"])
            self._prev_grid_hash = str(observation.get("frame_hash") or "") or None
            done = frame_response.get("state") in ("WIN", "GAME_OVER")
            return {
                "observation": observation,
                "did_progress": bool(progress["did_progress"]),
                "actual_effect": frame_response.get("state") or frame_response.get("effect"),
                "reward": float(progress["reward"]),
                "progress_reward": float(progress["progress_reward"]),
                "levels_completed": int(progress["levels_completed"]),
                "prev_levels_completed": int(progress["prev_levels_completed"]),
                "level_gain": int(progress["level_gain"]),
                "grid_changed": bool(progress["grid_changed"]),
                "done": done,
                "state": frame_response.get("state"),
            }

        step = int(context.get("step") or 0)
        raw_action = {"action_id": action_id, **dict(payload)}
        frame_response, _reward, done = self._harness._execute_mock_action(self._game_id, raw_action, step)
        observation = ARC3Adapter(NoOpBrainClient(), session_id=str(context.get("session_id") or "arc-v2"), task_id=self._game_id).normalize_observation(frame_response)
        progress = _compute_progress(
            frame_response,
            self._prev_levels_completed,
            self._prev_grid_hash,
            str(observation.get("frame_hash") or "") or None,
        )
        self._prev_levels_completed = int(progress["levels_completed"])
        self._prev_grid_hash = str(observation.get("frame_hash") or "") or None
        return {
            "observation": observation,
            "did_progress": bool(progress["did_progress"]),
            "actual_effect": frame_response.get("state") or frame_response.get("effect"),
            "reward": float(progress["reward"]),
            "progress_reward": float(progress["progress_reward"]),
            "levels_completed": int(progress["levels_completed"]),
            "prev_levels_completed": int(progress["prev_levels_completed"]),
            "level_gain": int(progress["level_gain"]),
            "grid_changed": bool(progress["grid_changed"]),
            "done": done,
            "state": frame_response.get("state"),
        }

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


def _unwrap_arc_game_payload(payload: Any) -> Mapping[str, Any]:
    current = payload
    while isinstance(current, Mapping) and isinstance(current.get("payload"), Mapping):
        nested = dict(current["payload"])
        for key in ("game_id", "guid", "task_id", "dataset_id", "episode_num", "step_num", "available_actions", "frame", "state", "training_examples", "levels_completed", "win_levels"):
            if key in current and key not in nested:
                nested[key] = current[key]
        current = nested
    if isinstance(current, Mapping):
        return dict(current)
    raise TypeError("ARC game server response payload must be a mapping")


def _compute_progress(
    frame_response: Mapping[str, Any],
    prev_levels: int,
    prev_grid_hash: str | None,
    new_grid_hash: str | None,
) -> dict[str, Any]:
    state = frame_response.get("state")
    levels = int(frame_response.get("levels_completed") or 0)
    level_gain = max(0, levels - prev_levels)
    win = state == "WIN"
    grid_changed = bool(new_grid_hash and prev_grid_hash and new_grid_hash != prev_grid_hash)
    progress_reward = 1.0 if win else float(level_gain)
    return {
        "reward": progress_reward,
        "progress_reward": progress_reward,
        "did_progress": bool(win or level_gain > 0),
        "levels_completed": levels,
        "prev_levels_completed": prev_levels,
        "level_gain": level_gain,
        "grid_changed": grid_changed,
    }
