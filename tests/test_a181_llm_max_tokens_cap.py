"""Tests for A181: the tier-2 LLM escalation call (goal-disambiguation,
action-pick) occasionally never emitted a stop token and ran away past
10,000+ decoded tokens for what should be a short JSON object -- confirmed
live twice via Ollama's own server.log `n_decoded` counter, not assumed.
Root cause: `arc_runtime/llm.py::LLMClient.chat`/`achat` never set
`max_tokens`. See backlog/A181.md.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arc_runtime.llm import LLMClient


class _FakeResponse:
    def __init__(self, content: str) -> None:
        self.choices = [type("Choice", (), {"message": type("Msg", (), {"content": content})()})()]
        self.usage = None


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return _FakeResponse('{"ok": true}')


class _FakeChat:
    def __init__(self) -> None:
        self.completions = _FakeCompletions()


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self.chat = _FakeChat()


class TestChatSetsMaxTokens:
    def test_chat_includes_max_tokens_by_default(self):
        fake_client = _FakeOpenAIClient()
        client = LLMClient(fake_client, model="llama3.1:8b")

        client.chat([{"role": "user", "content": "hi"}])

        assert len(fake_client.chat.completions.calls) == 1
        assert fake_client.chat.completions.calls[0]["max_tokens"] == LLMClient.DEFAULT_MAX_TOKENS

    def test_explicit_max_tokens_kwarg_is_respected_not_overridden(self):
        """A caller that explicitly asks for a different cap should get it --
        the default is a fallback, not a forced override."""
        fake_client = _FakeOpenAIClient()
        client = LLMClient(fake_client, model="llama3.1:8b")

        client.chat([{"role": "user", "content": "hi"}], max_tokens=50)

        assert fake_client.chat.completions.calls[0]["max_tokens"] == 50


class TestAchatSetsMaxTokens:
    def test_achat_json_object_path_includes_max_tokens(self):
        fake_client = _FakeOpenAIClient()
        client = LLMClient(fake_client, model="llama3.1:8b")

        result = asyncio.run(client.achat([{"role": "user", "content": "hi"}]))

        assert result == '{"ok": true}'
        assert len(fake_client.chat.completions.calls) == 1
        call = fake_client.chat.completions.calls[0]
        assert call["max_tokens"] == LLMClient.DEFAULT_MAX_TOKENS
        assert call["response_format"] == {"type": "json_object"}

    def test_achat_fallback_path_also_includes_max_tokens(self):
        """The response_format retry (line ~57-58 of llm.py) must not lose
        the cap just because it dropped response_format."""

        class _RejectsResponseFormatCompletions(_FakeCompletions):
            def create(self, **kwargs: Any) -> _FakeResponse:
                if "response_format" in kwargs:
                    raise TypeError("response_format not supported")
                return super().create(**kwargs)

        fake_client = _FakeOpenAIClient()
        fake_client.chat.completions = _RejectsResponseFormatCompletions()
        client = LLMClient(fake_client, model="llama3.1:8b")

        result = asyncio.run(client.achat([{"role": "user", "content": "hi"}]))

        assert result == '{"ok": true}'
        assert len(fake_client.chat.completions.calls) == 1
        assert fake_client.chat.completions.calls[0]["max_tokens"] == LLMClient.DEFAULT_MAX_TOKENS
        assert "response_format" not in fake_client.chat.completions.calls[0]
