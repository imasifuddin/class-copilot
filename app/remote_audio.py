"""Audio streamed in from the phone.

When the class runs on a different computer, the laptop running this server
cannot hear anything useful. The Android app becomes the microphone instead: it
records 16 kHz mono PCM and streams it here as one long chunked HTTP POST, which
lands in exactly the same pipeline as locally captured audio.

Raw PCM over a plain POST rather than a websocket, deliberately -- it needs no
extra library on the Android side, and a broken connection is just a retry.
"""
from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger("remote-audio")

BYTES_PER_SAMPLE = 2          # 16-bit little-endian
FULL_SCALE = 32768.0


class RemoteAudio:
    """Decodes the incoming byte stream and hands blocks to the pipeline."""

    def __init__(self, hub):
        self.hub = hub
        self.last_seen = 0.0
        self.device = ""
        self.bytes_in = 0
        self.force = False       # "answer whatever you hear" (push to listen)
        self._tail = b""
        self._active = 0

    @property
    def streaming(self) -> bool:
        return self._active > 0 and (time.time() - self.last_seen) < 5.0

    def open(self, device: str, intent: str = ""):
        self._active += 1
        self.last_seen = time.time()
        self.device = device or "phone"
        self.force = intent == "ask"
        self._tail = b""
        log.info("phone microphone connected (%s, intent=%s)",
                 self.device, intent or "listen")

    def close(self):
        self._active = max(0, self._active - 1)
        # Push a moment of silence so a half-finished sentence is closed off
        # rather than left hanging when the user hits mute mid-word.
        sink = getattr(self.hub, "audio_sink", None)
        if sink is not None:
            sink(np.zeros(int(0.9 * 16000), dtype=np.float32))
        log.info("phone microphone disconnected")

    def feed(self, chunk: bytes):
        """One arbitrary-sized piece of the POST body."""
        if not chunk:
            return
        self.bytes_in += len(chunk)
        self.last_seen = time.time()

        data = self._tail + chunk
        # A chunk boundary can split a sample in half; carry the odd byte over.
        usable = len(data) - (len(data) % BYTES_PER_SAMPLE)
        self._tail = data[usable:]
        if not usable:
            return

        pcm = np.frombuffer(data[:usable], dtype="<i2")
        block = (pcm.astype(np.float32) / FULL_SCALE).copy()
        sink = getattr(self.hub, "audio_sink", None)
        if sink is not None:
            sink(block)
