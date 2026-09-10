"""OpenAI-compatible backend.

Serves every provider except Claude: OpenAI, Gemini's compatibility endpoint,
Groq, OpenRouter, Ollama, LM Studio -- they all speak this dialect. The key and
URL come from the settings screen, falling back to the environment.
"""
from __future__ import annotations

import os
from typing import AsyncIterator

from openai import AsyncOpenAI

from .llm import PROVIDERS, resolve_base_url


class OpenAIProvider:
    label = "OpenAI-compatible"

    def __init__(self, cfg):
        self.cfg = cfg
        preset = PROVIDERS.get(cfg.provider, {})
        env_key = preset.get("env_key") or "OPENAI_API_KEY"
        key = cfg.api_key or os.getenv(env_key) or os.getenv("OPENAI_API_KEY")
        if not key and preset.get("needs_key"):
            raise RuntimeError(
                f"{preset.get('label', cfg.provider)} needs an API key -- add one in "
                f"Settings, or set {env_key} in .env"
            )
        base = resolve_base_url(cfg) or os.getenv("OPENAI_BASE_URL")
        if not base:
            raise RuntimeError(
                f"No server URL for provider {cfg.provider!r}. Set one in Settings."
            )
        self.client = AsyncOpenAI(api_key=key or "not-needed", base_url=base)

    async def list_models(self) -> list[str]:
        """Ask the provider what it actually offers today."""
        page = await self.client.models.list()
        return sorted(m.id for m in page.data)

    async def stream(self, system: str, messages: list[dict]) -> AsyncIterator[str]:
        stream = await self.client.chat.completions.create(
            model=self.cfg.model,
            max_tokens=self.cfg.max_tokens,
            stream=True,
            messages=[{"role": "system", "content": system}] + messages,
        )
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
