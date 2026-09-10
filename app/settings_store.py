"""Runtime-editable settings, changed from the phone and persisted to disk.

`config.toml` is the baseline you edit by hand. Anything changed from the app's
settings screen lands in `settings.json` and wins over it, so you can swap model
or provider mid-class without touching a text file.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from .config import ROOT

log = logging.getLogger("settings")
STORE = ROOT / "settings.json"

# section -> {field: coercion}. Anything not listed here cannot be set remotely.
EDITABLE: dict[str, dict[str, type]] = {
    "llm": {
        "provider": str,
        "model": str,
        "api_key": str,
        "base_url": str,
        "effort": str,
        "fast_mode": bool,
        "max_tokens": int,
        "context_turns": int,
        "style": str,
        "answer_language": str,
        "persona_extra": str,
    },
    "trigger": {"mode": str, "min_words": int, "cooldown_s": float,
                "end_of_turn_s": float, "max_turn_words": int, "smart": bool},
    "audio": {"silence_threshold": float, "mode": str, "device": str},
    "stt": {"model": str, "engine": str, "cloud_model": str},
}

# Changing these only takes effect after a restart; the UI says so.
NEEDS_RESTART = {("audio", "mode"), ("audio", "device"), ("stt", "model"),
                 ("stt", "engine")}

_lock = threading.Lock()


def load() -> dict:
    if not STORE.exists():
        return {}
    try:
        return json.loads(STORE.read_text(encoding="utf-8"))
    except Exception:
        log.exception("settings.json is unreadable; ignoring it")
        return {}


def apply(cfg) -> dict:
    """Overlay the saved settings onto a freshly loaded Config."""
    saved = load()
    for section, fields in saved.items():
        target = getattr(cfg, section, None)
        if target is None or section not in EDITABLE:
            continue
        for key, value in (fields or {}).items():
            if key in EDITABLE[section]:
                setattr(target, key, value)
    return saved


def update(cfg, changes: dict) -> tuple[dict, list[str]]:
    """Validate, apply to the live config, and persist. Returns (saved, restart)."""
    restart: list[str] = []
    with _lock:
        saved = load()
        for section, fields in changes.items():
            allowed = EDITABLE.get(section)
            if not allowed:
                continue
            target = getattr(cfg, section, None)
            if target is None:
                continue
            bucket = saved.setdefault(section, {})
            for key, value in (fields or {}).items():
                if key not in allowed:
                    continue
                caster = allowed[key]
                try:
                    value = bool(value) if caster is bool else caster(value)
                except (TypeError, ValueError):
                    continue
                if getattr(target, key) == value:
                    continue
                setattr(target, key, value)
                bucket[key] = value
                if (section, key) in NEEDS_RESTART:
                    restart.append(f"{section}.{key}")
        STORE.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    return saved, restart


def env_key_present(cfg) -> bool:
    """Is a usable key already sitting in .env for the chosen provider?"""
    from .llm import PROVIDERS

    provider = cfg.llm.provider
    if provider == "anthropic":
        names = ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"]
    else:
        preset = PROVIDERS.get(provider, {})
        names = [preset.get("env_key") or "", "OPENAI_API_KEY"]
    return any(os.getenv(n) for n in names if n)


def public(cfg) -> dict:
    """Current settings for the UI. The API key is masked, never sent back."""
    key = cfg.llm.api_key or ""
    return {
        "llm": {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model,
            "api_key_set": bool(key),
            "api_key_hint": (key[:6] + "..." + key[-4:]) if len(key) > 12 else "",
            "api_key_env": env_key_present(cfg),
            "base_url": cfg.llm.base_url,
            "effort": cfg.llm.effort,
            "fast_mode": cfg.llm.fast_mode,
            "max_tokens": cfg.llm.max_tokens,
            "context_turns": cfg.llm.context_turns,
            "style": cfg.llm.style,
            "answer_language": cfg.llm.answer_language,
            "persona_extra": cfg.llm.persona_extra,
        },
        "trigger": {
            "mode": cfg.trigger.mode,
            "min_words": cfg.trigger.min_words,
            "cooldown_s": cfg.trigger.cooldown_s,
            "end_of_turn_s": cfg.trigger.end_of_turn_s,
            "max_turn_words": cfg.trigger.max_turn_words,
            "smart": cfg.trigger.smart,
        },
        "audio": {
            "silence_threshold": cfg.audio.silence_threshold,
            "mode": cfg.audio.mode,
            "device": cfg.audio.device,
        },
        "stt": {"model": cfg.stt.model, "engine": cfg.stt.engine,
                "cloud_model": cfg.stt.cloud_model},
    }
