"""Energy-based utterance segmentation.

Turns a continuous 16 kHz stream into discrete speech clips, so Whisper is only
ever asked to transcribe an actual sentence instead of a rolling window. The
threshold adapts to the room's noise floor, which matters a lot in mic mode.
"""
from __future__ import annotations

import numpy as np

from .audio import TARGET_RATE

FRAME = 320          # 20 ms
PREROLL_FRAMES = 15  # 300 ms of audio kept before speech is confirmed
ONSET_FRAMES = 3     # consecutive loud frames needed to declare speech


class Segmenter:
    def __init__(self, cfg):
        self.cfg = cfg  # read live so the settings screen can retune mid-class
        self.min_len = int(TARGET_RATE * cfg.min_speech_ms / 1000)
        self.max_len = int(TARGET_RATE * cfg.max_utterance_ms / 1000)
        self.hang_frames = max(1, int(cfg.hangover_ms / 20))

        self._buf = np.zeros(0, dtype=np.float32)
        self._preroll: list[np.ndarray] = []
        self._speech: list[np.ndarray] = []
        self._noise = cfg.silence_threshold
        self._loud = 0
        self._quiet = 0
        self._active = False

    @property
    def speaking(self) -> bool:
        """True while someone is part-way through an utterance."""
        return self._active

    @property
    def threshold(self) -> float:
        return max(self.cfg.silence_threshold, self._noise * 3.5)

    def push(self, block: np.ndarray) -> list[np.ndarray]:
        """Feed audio, get back any completed utterances."""
        self._buf = np.concatenate([self._buf, block])
        done: list[np.ndarray] = []

        while len(self._buf) >= FRAME:
            frame, self._buf = self._buf[:FRAME], self._buf[FRAME:]
            rms = float(np.sqrt(np.mean(np.square(frame))))

            if not self._active:
                # Track the noise floor only while nobody is talking.
                self._noise = 0.995 * self._noise + 0.005 * rms
                self._preroll.append(frame)
                if len(self._preroll) > PREROLL_FRAMES:
                    self._preroll.pop(0)

                self._loud = self._loud + 1 if rms > self.threshold else 0
                if self._loud >= ONSET_FRAMES:
                    self._active = True
                    self._quiet = 0
                    self._speech = list(self._preroll)
                    self._preroll = []
            else:
                self._speech.append(frame)
                self._quiet = 0 if rms > self.threshold else self._quiet + 1

                too_long = len(self._speech) * FRAME >= self.max_len
                if self._quiet >= self.hang_frames or too_long:
                    clip = np.concatenate(self._speech)
                    if too_long:
                        # Hard cut mid-sentence: keep listening straight away.
                        self._speech = []
                        self._active = True
                        self._quiet = 0
                    else:
                        # Drop the trailing silence before handing it over.
                        clip = clip[: max(0, len(clip) - self._quiet * FRAME)]
                        self._reset_to_idle()
                    if len(clip) >= self.min_len:
                        done.append(clip)

        return done

    def flush(self) -> list[np.ndarray]:
        """Emit whatever is buffered (used when the user hits Pause)."""
        if self._active and len(self._speech) * FRAME >= self.min_len:
            clip = np.concatenate(self._speech)
            self._reset_to_idle()
            return [clip]
        self._reset_to_idle()
        return []

    def _reset_to_idle(self):
        self._active = False
        self._speech = []
        self._preroll = []
        self._loud = 0
        self._quiet = 0
