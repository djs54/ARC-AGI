"""ARC-owned LLM client factory."""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


def _resolve_timeout_seconds(provider: str, llm_cfg: Dict[str, Any]) -> float | None:
    raw_timeout = llm_cfg.get("timeout_seconds")
    if raw_timeout in (None, ""):
        return 180.0 if provider == "ollama" else None
    try:
        return float(raw_timeout)
    except (TypeError, ValueError):
        return 180.0 if provider == "ollama" else None


def _resolve_max_retries(provider: str, llm_cfg: Dict[str, Any]) -> int | None:
    raw_retries = llm_cfg.get("max_retries")
    if raw_retries in (None, ""):
        return 3 if provider == "ollama" else None
    try:
        return int(raw_retries)
    except (TypeError, ValueError):
        return 3 if provider == "ollama" else None


class LLMClient:
    """Small wrapper over an OpenAI-compatible chat endpoint."""

    def __init__(self, client: Any, model: str):
        self._client = client
        self._model = model
        self.last_usage: Optional[Dict[str, int]] = None

    def chat(self, messages: list[dict], **kwargs) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            temperature=0.0,
            **kwargs,
        )
        if hasattr(response, "usage") and response.usage:
            self.last_usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", 0),
                "completion_tokens": getattr(response.usage, "completion_tokens", 0),
                "total_tokens": getattr(response.usage, "total_tokens", 0),
            }
        return response.choices[0].message.content or ""

    async def achat(self, messages: list[dict]) -> str:
        import asyncio

        return await asyncio.to_thread(self.chat, messages)


def create_llm_client(config: Dict[str, Any]) -> LLMClient | None:
    """Return an ARC-owned OpenAI-compatible client or None if unavailable."""
    llm_cfg = config.get("llm", {})
    provider = str(llm_cfg.get("provider", "ollama")).lower()
    model = str(llm_cfg.get("model", "llama3.1:8b"))

    client_options: Dict[str, Any] = {}
    timeout_seconds = _resolve_timeout_seconds(provider, llm_cfg)
    max_retries = _resolve_max_retries(provider, llm_cfg)
    if timeout_seconds is not None:
        client_options["timeout"] = timeout_seconds
    if max_retries is not None:
        client_options["max_retries"] = max_retries

    try:
        from openai import OpenAI

        if provider == "ollama":
            base_url = llm_cfg.get("base_url", "http://localhost:11434/v1")
            client = OpenAI(base_url=base_url, api_key="ollama", **client_options)
            return LLMClient(client, model)

        if provider == "openai":
            api_key = llm_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY", "")
            client = OpenAI(api_key=api_key, **client_options)
            return LLMClient(client, model)

        if provider == "anthropic":
            base_url = llm_cfg.get("base_url", "https://api.anthropic.com/v1")
            api_key = llm_cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
            client = OpenAI(base_url=base_url, api_key=api_key, **client_options)
            return LLMClient(client, model)

        if provider == "google":
            base_url = llm_cfg.get("base_url", "https://generativelanguage.googleapis.com/v1beta/openai/")
            api_key = llm_cfg.get("api_key") or os.environ.get("GOOGLE_API_KEY", "")
            client = OpenAI(base_url=base_url, api_key=api_key, **client_options)
            return LLMClient(client, model)

        print(f"[LLM] Unknown provider '{provider}'. Running without an LLM client.")
        return None
    except Exception as exc:
        print(f"[LLM] Could not initialize provider '{provider}': {exc}. Running without an LLM client.")
        return None

