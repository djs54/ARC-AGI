"""A174: SyncLLMPortAdapter's sync-method fallback loop called every candidate
method (generate/complete/chat) with a single joined string prompt. This is
wrong for `chat`, which mirrors `achat`'s structured-messages contract --
LLMClient.chat() expects a list of {"role", "content"} dicts, not a string.
When achat ever failed (timeout, network blip), the fallback would send a
malformed request that the real server correctly rejects. See backlog/A174.md.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agents.arc4.ports import LLMMessage
from arc_runtime.bundle import SyncLLMPortAdapter


class _ChatOnlyClient:
    """Client with only a structured chat(messages) method, no achat."""

    def chat(self, messages) -> str:
        assert isinstance(messages, list), f"expected structured messages list, got {type(messages)}"
        assert all(isinstance(m, dict) and "role" in m and "content" in m for m in messages)
        return "structured-ok"


class _FailingAchatThenChatClient:
    """Client whose achat always raises, falling through to the sync loop's chat()."""

    async def achat(self, message_dicts):
        raise ConnectionError("unreachable ollama endpoint")

    def chat(self, messages) -> str:
        assert isinstance(messages, list), f"expected structured messages list, got {type(messages)}"
        return "fallback-ok"


class _GenerateOnlyClient:
    """Client with a generate(prompt: str) method -- regression guard that the
    string-prompt contract for generate/complete is unchanged."""

    def generate(self, prompt: str) -> str:
        assert isinstance(prompt, str), f"expected a string prompt, got {type(prompt)}"
        return "generate-ok"


def test_chat_fallback_receives_structured_messages_not_string():
    adapter = SyncLLMPortAdapter(_ChatOnlyClient())
    result = adapter.chat([LLMMessage(role="user", content="hi")])
    assert result == "structured-ok"


def test_achat_failure_falls_back_to_chat_with_structured_messages():
    adapter = SyncLLMPortAdapter(_FailingAchatThenChatClient())
    result = adapter.chat([LLMMessage(role="user", content="hi")])
    assert result == "fallback-ok"


def test_generate_fallback_still_receives_string_prompt():
    adapter = SyncLLMPortAdapter(_GenerateOnlyClient())
    result = adapter.chat([LLMMessage(role="user", content="hi")])
    assert result == "generate-ok"
