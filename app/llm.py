"""Provider registry, the system prompt, and the factory.

Everything except Claude speaks the OpenAI chat dialect, so one client class
covers OpenAI, Gemini, Groq, OpenRouter, Ollama, LM Studio and anything else
that exposes an OpenAI-compatible URL.
"""
from __future__ import annotations

from .prompts import build_system  # re-exported for callers

# name -> what the settings screen needs to know about it.
PROVIDERS: dict[str, dict] = {
    "anthropic": {
        "label": "Claude (Anthropic)",
        "base_url": "",
        "env_key": "ANTHROPIC_API_KEY",
        "needs_key": True,
        "models": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
        "note": "Best answers. Needs an API key from console.anthropic.com.",
    },
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "env_key": "OPENAI_API_KEY",
        "needs_key": True,
        "models": ["gpt-4o", "gpt-4o-mini", "o4-mini"],
        "note": "Key from platform.openai.com.",
    },
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "env_key": "GEMINI_API_KEY",
        "needs_key": True,
        "models": ["gemini-2.0-flash", "gemini-2.5-pro"],
        "note": "Key from aistudio.google.com. Has a free tier.",
    },
    "groq": {
        "label": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "env_key": "GROQ_API_KEY",
        "needs_key": True,
        "models": ["llama-3.3-70b-versatile", "qwen-2.5-coder-32b"],
        "note": "Very fast, generous free tier. Key from console.groq.com.",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "env_key": "OPENROUTER_API_KEY",
        "needs_key": True,
        "models": ["anthropic/claude-sonnet-4.5", "deepseek/deepseek-chat"],
        "note": "One key, many models. Some are free.",
    },
    "ollama": {
        "label": "Ollama (local, free)",
        "base_url": "http://localhost:11434/v1",
        "env_key": "",
        "needs_key": False,
        "models": ["qwen2.5-coder:7b", "llama3.1:8b"],
        "note": "Runs on this laptop. Free and offline; weaker on hard doubts.",
    },
    "custom": {
        "label": "Custom (OpenAI-compatible)",
        "base_url": "",
        "env_key": "OPENAI_API_KEY",
        "needs_key": False,
        "models": [],
        "note": "LM Studio, vLLM, Together, or any OpenAI-compatible URL.",
    },
}


def resolve_base_url(cfg) -> str:
    """Explicit base_url wins; otherwise the provider's preset."""
    if cfg.base_url:
        return cfg.base_url
    return PROVIDERS.get(cfg.provider, {}).get("base_url", "")


def get_provider(cfg):
    provider = (cfg.provider or "anthropic").lower()
    if provider == "anthropic":
        from .llm_anthropic import AnthropicProvider

        return AnthropicProvider(cfg)
    if provider in PROVIDERS or provider in ("openai-compatible",):
        from .llm_openai import OpenAIProvider

        return OpenAIProvider(cfg)
    raise ValueError(
        f"Unknown provider {cfg.provider!r}. Known: {', '.join(sorted(PROVIDERS))}"
    )
