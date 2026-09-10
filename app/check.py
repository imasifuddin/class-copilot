"""Pre-class preflight: audio -> whisper -> LLM, in about 30 seconds.

    .\.venv\Scripts\python.exe -m app.check

Run this once before a class. It tells you which of the three stages is broken
instead of leaving you guessing at a blank chat window.
"""
from __future__ import annotations

import asyncio
import os
import queue
import sys
import time

import numpy as np
from dotenv import load_dotenv

from . import config, context_store, settings_store
from .audio import AudioSource, TARGET_RATE
from .llm import get_provider
from .prompts import build_system

def _utf8_console():
    """Model answers contain arrows and dashes that a cp1252 console refuses."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


OK, BAD = "[ ok ]", "[FAIL]"
LISTEN_SECONDS = 6


def stage_audio(cfg) -> np.ndarray | None:
    print("\n1. audio capture")
    q: queue.Queue = queue.Queue(maxsize=400)
    src = AudioSource(cfg.audio, q)
    try:
        label = src.start()
    except Exception as exc:
        print(f"   {BAD} {exc}")
        print("        run:  .\\devices.ps1   and set [audio] device in config.toml")
        return None

    print(f"   {OK} {label}")
    if cfg.audio.mode == "loopback":
        print(f"        Play any audio with speech for {LISTEN_SECONDS}s (a video works).")
    else:
        print(f"        Talk into the microphone for {LISTEN_SECONDS}s.")

    chunks: list[np.ndarray] = []
    deadline = time.time() + LISTEN_SECONDS
    while time.time() < deadline:
        try:
            chunks.append(q.get(timeout=0.5))
        except queue.Empty:
            pass
        left = deadline - time.time()
        bar = "#" * int(min(40, src.level / 0.15 * 40))
        print(f"\r        {left:4.1f}s  |{bar:<40}| {src.level:.4f}", end="", flush=True)
    src.stop()
    print()

    if not chunks:
        print(f"   {BAD} no audio arrived from the device")
        return None
    audio = np.concatenate(chunks)
    peak = float(np.abs(audio).max())
    secs = len(audio) / TARGET_RATE
    print(f"   {OK} captured {secs:.1f}s, peak {peak:.3f}")
    if peak < cfg.audio.silence_threshold * 2:
        print(f"   {BAD} that is basically silence.")
        if cfg.audio.mode == "loopback":
            print("        Loopback captures the device you selected -- make sure the")
            print("        call/video is actually playing out of THAT device.")
        else:
            print("        Check Windows mic privacy settings and the input volume.")
        return None
    return audio


def stage_stt(cfg, audio: np.ndarray) -> bool:
    print("\n2. speech to text")
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print(f"   {BAD} faster-whisper is not installed")
        return False

    t0 = time.time()
    try:
        model = WhisperModel(
            cfg.stt.model, device=cfg.stt.device, compute_type=cfg.stt.compute_type
        )
    except Exception as exc:
        print(f"   {BAD} could not load model {cfg.stt.model!r}: {exc}")
        return False
    print(f"   {OK} model {cfg.stt.model} loaded in {time.time() - t0:.1f}s")

    t0 = time.time()
    segments, _ = model.transcribe(
        audio, language=cfg.stt.language or None, beam_size=1, vad_filter=True
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    took = time.time() - t0
    speed = (len(audio) / TARGET_RATE) / took if took else 0
    print(f"   {OK} transcribed in {took:.1f}s ({speed:.1f}x realtime)")
    if speed < 1.0:
        print("        Slower than realtime -- use a smaller [stt] model.")
    print(f'        heard: "{text or "(nothing)"}"')
    return bool(text)


async def stage_llm(cfg) -> bool:
    print("\n3. llm")
    if cfg.llm.provider == "anthropic" and not (
        cfg.llm.api_key or os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")
    ):
        print(f"   {BAD} no Anthropic key. Put one in .env, or set it from the")
        print("        phone's settings screen once the app is running.")
        return False
    try:
        provider = get_provider(cfg.llm)
    except Exception as exc:
        print(f"   {BAD} {exc}")
        return False

    print(f"   ..   asking {cfg.llm.model} a sample doubt")
    t0, first, parts = time.time(), None, []
    try:
        async for delta in provider.stream(
            build_system(cfg.llm, context_store.build('hash map')),
            [{"role": "user", "content": 'Doubt to answer: "sir what is a hash map"'}],
        ):
            if first is None:
                first = time.time() - t0
            parts.append(delta)
    except Exception as exc:
        print(f"   {BAD} {type(exc).__name__}: {exc}")
        return False

    print(f"   {OK} first token in {first:.1f}s, done in {time.time() - t0:.1f}s")
    print("        " + "\n        ".join("".join(parts).strip().splitlines()[:6]))
    return True


def main() -> int:
    _utf8_console()
    load_dotenv()
    cfg = config.load()
    settings_store.apply(cfg)
    print(f"class-copilot preflight  --  mode={cfg.audio.mode}  "
          f"stt={cfg.stt.model}  llm={cfg.llm.provider}/{cfg.llm.model}")

    audio = stage_audio(cfg)
    stt_ok = stage_stt(cfg, audio) if audio is not None else False
    llm_ok = asyncio.run(stage_llm(cfg))

    print("\n" + "-" * 60)
    if audio is not None and stt_ok and llm_ok:
        print("All three stages work. Start the app with:  .\\run.ps1")
        return 0
    print("Fix whatever is marked [FAIL] above, then run this again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
