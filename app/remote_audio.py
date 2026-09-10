"""Audio streamed in from the phone.

When the class runs on a different computer, the laptop running this server
cannot hear anything useful. The Android app becomes the microphone instead.

It arrives as a series of short POSTs rather than one long streaming one. That
looks wasteful, and locally it is -- but hosting proxies buffer a long request
body before handing it to the app, which delayed the first audio by nearly
twenty seconds on Render. Short posts land immediately everywhere.

Each post carries `x-cc-state`:
    start   the first one, when you tap listen
    chunk   more audio
    stop    you tapped stop; close the turn and answer
"""
from __future__ import annotations

import logging
import time

import numpy as np

log = logging.getLogger("remote-audio")

BYTES_PER_SAMPLE = 2          # 16-bit little-endian
FULL_SCALE = 32768.0
STALE_AFTER = 8.0             # a session with no posts this long is abandoned
FLUSH_SILENCE = 0.9           # seconds of quiet appended when you tap stop


class RemoteAudio:
    """Decodes the incoming audio and tracks whether the mic is held open."""

    def __init__(self, hub):
        self.hub = hub
        self.last_seen = 0.0
        self.device = ""
        self.bytes_in = 0
        self.force = False        # "answer whatever you hear" (push to listen)
        self.open_session = False
        self.finished = False     # you tapped stop, so there is nothing to wait for
        self._tail = b""

    # ---------- state ----------

    @property
    def streaming(self) -> bool:
        """Is the phone sending right now?"""
        return self.open_session and (time.time() - self.last_seen) < STALE_AFTER

    @property
    def held(self) -> bool:
        """Are you still holding the button down?

        Deliberately not based on how recently audio arrived: a slow network or
        a buffering proxy can leave gaps of several seconds, and treating those
        as "they stopped talking" is what caused answers to appear mid-question.
        Only an explicit stop, or an abandoned session, ends it.
        """
        return self.force and self.streaming

    def begin(self, device: str, intent: str = ""):
        self.open_session = True
        self.finished = False
        self.device = device or "phone"
        self.force = intent == "ask"
        self.last_seen = time.time()
        self._tail = b""
        log.info("phone microphone opened (%s, intent=%s)",
                 self.device, intent or "listen")

    def end(self):
        if not self.open_session:
            return          # already closed; a duplicate stop is harmless
        self.open_session = False
        self.finished = True
        self.last_seen = time.time()
        # Close off a half-finished sentence rather than leaving it hanging.
        sink = getattr(self.hub, "audio_sink", None)
        if sink is not None:
            sink(np.zeros(int(FLUSH_SILENCE * 16000), dtype=np.float32))
        log.info("phone microphone closed")

    # kept so older callers and tests keep working
    def open(self, device: str, intent: str = ""):
        self.begin(device, intent)

    def close(self):
        self.end()

    # ---------- audio ----------

    def feed(self, chunk: bytes):
        """One post's worth of PCM."""
        if not chunk:
            self.last_seen = time.time()
            return
        self.bytes_in += len(chunk)
        self.last_seen = time.time()

        data = self._tail + chunk
        # A post boundary can split a sample in half; carry the odd byte over.
        usable = len(data) - (len(data) % BYTES_PER_SAMPLE)
        self._tail = data[usable:]
        if not usable:
            return

        pcm = np.frombuffer(data[:usable], dtype="<i2")
        block = (pcm.astype(np.float32) / FULL_SCALE).copy()
        sink = getattr(self.hub, "audio_sink", None)
        if sink is not None:
            sink(block)
