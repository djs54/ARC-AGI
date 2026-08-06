"""A154: SyncLLMPortAdapter must not launder failed LLM calls into a valid-looking "{}"
patch, and goal_resolver.py must not treat an empty/goal_id-less JSON object as a real
patch. See backlog/A154.md and backlog/plans/A-154-llm-adapter-failure-visibility.md.
"""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.goal_resolver import GoalResolver, GoalResolverLimits
from agents.arc4.ports import LLMMessage
from agents.arc4.types import PerceivedEntity, PerceptionSnapshot, WorkflowState
from arc_runtime.bundle import SyncLLMPortAdapter


@dataclass
class RecordingLLMPort:
    response: str

    def __post_init__(self) -> None:
        self.calls: list[list[LLMMessage]] = []

    def chat(self, messages):
        self.calls.append(list(messages))
        return self.response


def _perception(*, grid_hash: str = "grid-1", grid_shape: tuple[int, int] | None = (2, 2), entities: tuple[PerceivedEntity, ...] = ()) -> PerceptionSnapshot:
    return PerceptionSnapshot(
        observation={"grid": grid_hash},
        grid_hash=grid_hash,
        grid_shape=grid_shape,
        entities=entities,
    )


def _state(*, previous_grid_hash: str | None = None, consecutive_no_progress_count: int = 0) -> WorkflowState:
    return WorkflowState(
        previous_grid_hash=previous_grid_hash,
        active_goal=None,
        consecutive_no_progress_count=consecutive_no_progress_count,
    )


class _FailingAchatClient:
    """Client whose achat raises and has no sync generate/complete/chat methods."""

    async def achat(self, message_dicts):
        raise ConnectionError("unreachable ollama endpoint")


class _FailingSyncMethodClient:
    """Client with no achat, but a sync `generate` method that raises."""

    def generate(self, prompt):
        raise RuntimeError("boom")


class _NoMethodClient:
    """Client with none of achat/generate/complete/chat."""


def test_chat_returns_empty_string_when_achat_fails():
    adapter = SyncLLMPortAdapter(_FailingAchatClient())
    result = adapter.chat([LLMMessage(role="user", content="hi")])
    assert result == ""
    assert result != "{}"


def test_chat_returns_empty_string_when_sync_method_fails():
    adapter = SyncLLMPortAdapter(_FailingSyncMethodClient())
    result = adapter.chat([LLMMessage(role="user", content="hi")])
    assert result == ""
    assert result != "{}"


def test_chat_returns_empty_string_when_no_method_exists():
    adapter = SyncLLMPortAdapter(_NoMethodClient())
    result = adapter.chat([LLMMessage(role="user", content="hi")])
    assert result == ""
    assert result != "{}"


def test_chat_logs_warning_on_failure(caplog):
    adapter = SyncLLMPortAdapter(_FailingAchatClient())
    with caplog.at_level(logging.WARNING):
        adapter.chat([LLMMessage(role="user", content="hi")])
    warnings = [record for record in caplog.records if record.levelno >= logging.WARNING]
    assert warnings, "expected at least one warning record logged on failure"


def test_parse_llm_response_empty_json_returns_none():
    assert GoalResolver._parse_llm_response("{}") is None


def test_parse_llm_response_with_goal_id_still_works():
    response = json.dumps({"goal_id": "g1", "confidence": 0.6})
    parsed = GoalResolver._parse_llm_response(response)
    assert parsed == {"goal_id": "g1", "confidence": 0.6}


def test_resolve_does_not_append_spurious_hypothesis_on_empty_llm_response():
    resolver = GoalResolver(GoalResolverLimits(ambiguity_gap=0.12, low_confidence_threshold=0.7, llm_patience_steps=2))
    perception = _perception(
        entities=(
            PerceivedEntity(kind="block", value="red", attributes={}),
            PerceivedEntity(kind="block", value="blue", attributes={}),
        )
    )
    llm = RecordingLLMPort(response="{}")

    result = resolver.resolve(_state(consecutive_no_progress_count=2), perception, llm_port=llm)

    assert len(llm.calls) == 1
    assert result.payload is not None
    goal_ids = [hypothesis["goal_id"] for hypothesis in result.payload.metadata["hypotheses"]]
    assert "llm-goal" not in goal_ids
    confidences_by_id = {h["goal_id"]: h["confidence"] for h in result.payload.metadata["hypotheses"]}
    assert all(confidence != 0.0 for confidence in confidences_by_id.values())
    assert result.payload.metadata["llm_escalated"] is False
