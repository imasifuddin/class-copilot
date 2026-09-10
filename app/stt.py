"""faster-whisper transcription running on its own thread."""
from __future__ import annotations

import io
import logging
import os
import queue
import threading
import time
import wave
from typing import Callable

# Noise on Windows when the HF cache cannot use symlinks -- harmless, and it
# scrolls the real startup messages off the screen.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import numpy as np

log = logging.getLogger("stt")

# Whisper's favourite hallucinations on silence / music / mic hiss.
JUNK = {
    "", ".", "you", "thank you", "thanks", "thank you.", "thanks for watching",
    "thanks for watching!", "bye", "bye.", "okay", "ok", "so", "um", "uh",
    "please subscribe", "subtitles by the amara.org community", "[music]",
    "[blank_audio]", "(upbeat music)", "thank you for watching",
}


def to_wav(clip: np.ndarray, rate: int = 16000) -> bytes:
    """16 kHz mono PCM16 in a WAV container, which every STT API accepts."""
    pcm = np.clip(clip, -1.0, 1.0)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((pcm * 32767).astype("<i2").tobytes())
    return buf.getvalue()


class Transcriber:
    def __init__(self, cfg, on_text: Callable[[str, float], None], llm=None):
        self.cfg = cfg
        self.llm = llm            # provider credentials, for the cloud engine
        self.on_text = on_text
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=32)
        self.ready = False
        self.error: str | None = None
        self._model = None
        self._client = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="stt", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def submit(self, clip: np.ndarray) -> bool:
        """True if the clip was accepted; the caller counts what is in flight."""
        try:
            self.queue.put_nowait(clip)
            return True
        except queue.Full:
            log.warning("STT backlog full, dropping a clip")
            return False

    @property
    def cloud(self) -> bool:
        return self.cfg.engine == "cloud"

    def _load_cloud(self):
        import os

        from openai import OpenAI

        from .llm import PROVIDERS, resolve_base_url

        preset = PROVIDERS.get(self.llm.provider, {})
        key = (self.llm.api_key or os.getenv(preset.get("env_key") or "")
               or os.getenv("OPENAI_API_KEY"))
        base = resolve_base_url(self.llm)
        if not base:
            raise RuntimeError(
                f"{self.llm.provider} has no speech-to-text endpoint. Switch the "
                "speech engine back to local, or pick a provider that has one."
            )
        self._client = OpenAI(api_key=key or "not-needed", base_url=base)
        self.ready = True
        log.info("speech to text: %s via %s", self.cfg.cloud_model, self.llm.provider)

    def _transcribe_cloud(self, clip: np.ndarray) -> str:
        result = self._client.audio.transcriptions.create(
            model=self.cfg.cloud_model,
            file=("clip.wav", to_wav(clip), "audio/wav"),
            language=self.cfg.language or None,
            response_format="text",
        )
        return result if isinstance(result, str) else getattr(result, "text", "")

    def _load(self):
        from faster_whisper import WhisperModel

        log.info("loading whisper model %s (%s/%s)...",
                 self.cfg.model, self.cfg.device, self.cfg.compute_type)
        t0 = time.time()
        self._model = WhisperModel(
            self.cfg.model, device=self.cfg.device, compute_type=self.cfg.compute_type
        )
        self.ready = True
        log.info("whisper ready in %.1fs", time.time() - t0)

    def _run(self):
        try:
            self._load_cloud() if self.cloud else self._load()
        except Exception as exc:
            log.exception("speech to text unavailable")
            self.error = (
                f"cloud speech ({self.cfg.cloud_model}) unavailable: {exc}"
                if self.cloud
                else f"speech model {self.cfg.model!r} failed to load: {exc}"
            )
            return

        while not self._stop.is_set():
            try:
                clip = self.queue.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                t0 = time.time()
                if self.cloud:
                    text = self._transcribe_cloud(clip).strip()
                    if not text or text.lower().strip(" .!?") in JUNK:
                        text = ""
                    self.on_text(text, time.time() - t0)
                    continue
                segments, _ = self._model.transcribe(
                    clip,
                    language=self.cfg.language or None,
                    beam_size=1,
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                text = " ".join(s.text.strip() for s in segments).strip()
                if not text or text.lower().strip(" .!?") in JUNK:
                    text = ""
                # Always report, empty included -- the hub is counting clips.
                self.on_text(text, time.time() - t0)
            except Exception:
                log.exception("transcription failed")
                self.on_text("", 0.0)
