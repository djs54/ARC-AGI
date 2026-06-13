"""Deterministic ARC v2 perception module."""

from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any, Mapping, Sequence

from .ports import GraphQueryPort
from .types import PerceivedEntity, PerceptionSnapshot, PhaseResult, WorkflowPhase, WorkflowState


class PerceiveAgent:
    """Build a stable perception snapshot from a raw observation."""

    def __init__(self, graph_query_port: GraphQueryPort | None = None, *, loop_window: int = 32) -> None:
        self._graph_query_port = graph_query_port
        self._loop_window = max(1, int(loop_window))

    def __call__(self, state: WorkflowState, observation: Mapping[str, Any]) -> PhaseResult[PerceptionSnapshot]:
        return self.perceive(state, observation)

    def perceive(self, state: WorkflowState, observation: Mapping[str, Any]) -> PhaseResult[PerceptionSnapshot]:
        normalized_observation = self._normalize_observation(observation)
        grid = self._extract_grid(normalized_observation)
        normalized_grid = self._normalize_grid(grid)
        grid_hash = self._hash_grid(normalized_grid)
        grid_shape = self._grid_shape(normalized_grid)
        entities = self._extract_entities(normalized_grid)

        state.previous_grid_hash = grid_hash
        state.loop_history.append(grid_hash)
        state.loop_history_pointer = len(state.loop_history) - 1
        if len(state.loop_history) > self._loop_window:
            overflow = len(state.loop_history) - self._loop_window
            del state.loop_history[:overflow]
            state.loop_history_pointer = len(state.loop_history) - 1

        repeated_grid_count = sum(1 for known_hash in state.loop_history if known_hash == grid_hash)
        loop_signal = repeated_grid_count > 1

        snapshot = PerceptionSnapshot(
            observation=normalized_observation,
            grid_hash=grid_hash,
            grid_shape=grid_shape,
            loop_signal=loop_signal,
            repeated_grid_count=repeated_grid_count,
            entities=entities,
            metadata={
                "entity_count": len(entities),
                "observation_keys": tuple(sorted(normalized_observation.keys())),
                "grid_source": self._grid_source_key(normalized_observation),
            },
        )

        snapshot.metadata["graph_ingestion"] = self._ingest_snapshot(snapshot)
        return PhaseResult(phase=WorkflowPhase.PERCEIVE, payload=snapshot)

    def _ingest_snapshot(self, snapshot: PerceptionSnapshot) -> str:
        if self._graph_query_port is None:
            return "skipped"

        ingest = getattr(self._graph_query_port, "ingest_perception", None)
        if ingest is None:
            return "skipped"

        try:
            result = ingest(snapshot)
        except Exception:
            return "failed"

        if result is None:
            return "ok"
        snapshot.metadata["graph_ingestion_result"] = result
        return "ok"

    @staticmethod
    def _normalize_observation(observation: Mapping[str, Any]) -> dict[str, Any]:
        return dict(observation)

    def _extract_grid(self, observation: Mapping[str, Any]) -> list[list[Any]] | None:
        grid_like = self._find_grid_like(observation)
        if grid_like is None:
            return None
        return [list(row) for row in grid_like]

    def _find_grid_like(self, value: Any, _seen: set[int] | None = None) -> Sequence[Sequence[Any]] | None:
        if _seen is None:
            _seen = set()
        if isinstance(value, Mapping):
            marker = id(value)
            if marker in _seen:
                return None
            _seen.add(marker)
            for key in ("grid", "board", "cells"):
                candidate = value.get(key)
                if self._is_grid(candidate):
                    return candidate  # type: ignore[return-value]
            for key in ("state", "observation", "payload", "game_state"):
                nested = value.get(key)
                found = self._find_grid_like(nested, _seen)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _is_grid(value: Any) -> bool:
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and bool(value) and all(
            isinstance(row, Sequence) and not isinstance(row, (str, bytes)) for row in value
        )

    @staticmethod
    def _normalize_grid(grid: Sequence[Sequence[Any]] | None) -> list[list[Any]]:
        if grid is None:
            return []
        return [list(row) for row in grid]

    @staticmethod
    def _hash_grid(grid: Sequence[Sequence[Any]]) -> str:
        payload = json.dumps(grid, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _grid_shape(grid: Sequence[Sequence[Any]]) -> tuple[int, int] | None:
        if not grid:
            return None
        row_count = len(grid)
        col_count = max((len(row) for row in grid), default=0)
        return row_count, col_count

    def _extract_entities(self, grid: Sequence[Sequence[Any]]) -> tuple[PerceivedEntity, ...]:
        if not grid:
            return ()

        rows = len(grid)
        cols = max((len(row) for row in grid), default=0)
        visited: set[tuple[int, int]] = set()
        entities: list[PerceivedEntity] = []

        for row_index in range(rows):
            for col_index in range(len(grid[row_index])):
                if (row_index, col_index) in visited:
                    continue
                value = grid[row_index][col_index]
                if value in (0, None):
                    continue
                component = self._collect_component(grid, row_index, col_index, visited)
                if not component:
                    continue
                entities.append(self._component_to_entity(grid, component, rows, cols))

        return tuple(entities)

    def _collect_component(
        self,
        grid: Sequence[Sequence[Any]],
        start_row: int,
        start_col: int,
        visited: set[tuple[int, int]],
    ) -> list[tuple[int, int]]:
        target = grid[start_row][start_col]
        queue: deque[tuple[int, int]] = deque([(start_row, start_col)])
        visited.add((start_row, start_col))
        component: list[tuple[int, int]] = []

        while queue:
            row_index, col_index = queue.popleft()
            component.append((row_index, col_index))
            for next_row, next_col in (
                (row_index - 1, col_index),
                (row_index + 1, col_index),
                (row_index, col_index - 1),
                (row_index, col_index + 1),
            ):
                if next_row < 0 or next_row >= len(grid):
                    continue
                if next_col < 0 or next_col >= len(grid[next_row]):
                    continue
                if (next_row, next_col) in visited:
                    continue
                if grid[next_row][next_col] != target:
                    continue
                visited.add((next_row, next_col))
                queue.append((next_row, next_col))

        return component

    def _component_to_entity(
        self,
        grid: Sequence[Sequence[Any]],
        component: Sequence[tuple[int, int]],
        total_rows: int,
        total_cols: int,
    ) -> PerceivedEntity:
        row_values = [row for row, _ in component]
        col_values = [col for _, col in component]
        min_row = min(row_values)
        max_row = max(row_values)
        min_col = min(col_values)
        max_col = max(col_values)
        cell_count = len(component)
        width = max_col - min_col + 1
        height = max_row - min_row + 1
        centroid_row = sum(row_values) / cell_count
        centroid_col = sum(col_values) / cell_count
        color = grid[component[0][0]][component[0][1]]
        aspect_ratio = round(width / max(height, 1), 3)
        fill_ratio = round(cell_count / max(width * height, 1), 3)

        if cell_count == 1:
            shape = "point"
        elif width == 1 or height == 1:
            shape = "line"
        elif 0.75 <= aspect_ratio <= 1.25:
            shape = "block"
        else:
            shape = "blob"

        return PerceivedEntity(
            kind=shape,
            value=str(color),
            attributes={
                "color": color,
                "cell_count": cell_count,
                "bbox": (min_row, min_col, max_row, max_col),
                "centroid": (round(centroid_row, 3), round(centroid_col, 3)),
                "width": width,
                "height": height,
                "fill_ratio": fill_ratio,
                "aspect_ratio": aspect_ratio,
                "coverage": round(cell_count / max(total_rows * total_cols, 1), 3),
            },
        )

    @staticmethod
    def _grid_source_key(observation: Mapping[str, Any]) -> str:
        for key in ("grid", "board", "cells"):
            if key in observation:
                return key
        for key in ("state", "observation", "payload", "game_state"):
            nested = observation.get(key)
            if isinstance(nested, Mapping):
                for nested_key in ("grid", "board", "cells"):
                    if nested_key in nested:
                        return f"{key}.{nested_key}"
        return "unknown"