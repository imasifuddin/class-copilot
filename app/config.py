"""Configuration loading: config.toml + .env, with defaults for every key."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class AudioCfg:
    # loopback | mic | both | phone  ("phone" = this machine does not listen;
    # the Android app streams its microphone here instead)
    mode: str = "both"
    device: str = ""
    silence_threshold: float = 0.010
    min_speech_ms: int = 600
    max_utterance_ms: int = 20000
    hangover_ms: int = 700


@dataclass
class SttCfg:
    # "local"  = faster-whisper on this machine, offline and free
    # "cloud"  = send each clip to the AI provider (better in noisy rooms, and
    #            lets the server run somewhere with no CPU to spare)
    engine: str = "local"
    cloud_model: str = "whisper-large-v3-turbo"
    model: str = "base.en"
    device: str = "cpu"
    compute_type: str = "int8"
    language: str = "en"


@dataclass
class TriggerCfg:
    mode: str = "auto"
    min_words: int = 3
    cooldown_s: float = 5.0
    end_of_turn_s: float = 1.6   # silence that means "they have finished asking"
    max_turn_words: int = 120    # close a turn anyway if someone never pauses
    smart: bool = True           # let the model spot doubts with no trigger words


@dataclass
class LlmCfg:
    provider: str = "anthropic"
    model: str = "claude-opus-5"
    api_key: str = ""      # blank -> read from .env / environment
    base_url: str = ""     # blank -> the provider preset's default
    effort: str = "low"
    fast_mode: bool = False
    max_tokens: int = 2000
    context_turns: int = 4
    style: str = "speak"          # speak | reference | both
    answer_language: str = "English"
    persona_extra: str = ""


@dataclass
class ServerCfg:
    host: str = "0.0.0.0"
    port: int = 8756
    open_browser: bool = True
    require_pin: bool = True


@dataclass
class Config:
    audio: AudioCfg = field(default_factory=AudioCfg)
    stt: SttCfg = field(default_factory=SttCfg)
    trigger: TriggerCfg = field(default_factory=TriggerCfg)
    llm: LlmCfg = field(default_factory=LlmCfg)
    server: ServerCfg = field(default_factory=ServerCfg)


def _fill(cls, data: dict):
    """Build a dataclass from a dict, ignoring unknown keys."""
    known = {f.name for f in cls.__dataclass_fields__.values()}
    return cls(**{k: v for k, v in (data or {}).items() if k in known})


def load(path: Path | None = None) -> Config:
    path = path or ROOT / "config.toml"
    raw: dict = {}
    if path.exists():
        with open(path, "rb") as fh:
            raw = tomllib.load(fh)

    cfg = Config(
        audio=_fill(AudioCfg, raw.get("audio", {})),
        stt=_fill(SttCfg, raw.get("stt", {})),
        trigger=_fill(TriggerCfg, raw.get("trigger", {})),
        llm=_fill(LlmCfg, raw.get("llm", {})),
        server=_fill(ServerCfg, raw.get("server", {})),
    )

    # Env overrides, handy for one-off runs:
    #   set CC_AUDIO_MODE=mic && python -m app.main
    if os.getenv("CC_AUDIO_MODE"):
        cfg.audio.mode = os.environ["CC_AUDIO_MODE"]
    if os.getenv("CC_STT_MODEL"):
        cfg.stt.model = os.environ["CC_STT_MODEL"]
    if os.getenv("CC_STT_ENGINE"):
        cfg.stt.engine = os.environ["CC_STT_ENGINE"]
    if os.getenv("CC_LLM_PROVIDER"):
        cfg.llm.provider = os.environ["CC_LLM_PROVIDER"]
    if os.getenv("CC_LLM_MODEL"):
        cfg.llm.model = os.environ["CC_LLM_MODEL"]
    return cfg
