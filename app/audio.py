"""Audio capture from either WASAPI loopback (speaker output) or a microphone.

Two backends, each on the path it actually works on under Windows:

* loopback -> `soundcard`, which exposes a speaker as a WASAPI loopback input.
  This hears exactly what Zoom/Meet plays through your speakers -- the students --
  and never your own microphone, so the assistant is not distracted by the
  teacher talking.
* mic      -> `sounddevice`, a callback stream on a normal input device.

Both deliver mono float32 blocks at 16 kHz onto the same queue.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import warnings

import numpy as np

log = logging.getLogger("audio")

TARGET_RATE = 16000     # what Whisper wants
CAPTURE_RATE = 48000    # what we ask the loopback device for
BLOCK_SECONDS = 0.05


class Resampler:
    """Streaming linear resampler with a box anti-alias filter."""

    def __init__(self, src_rate: int, dst_rate: int):
        self.step = src_rate / dst_rate
        self._pos = 0.0
        self._tail = np.zeros(0, dtype=np.float32)
        self._taps = max(1, int(round(self.step))) if self.step > 1.5 else 1

    def process(self, block: np.ndarray) -> np.ndarray:
        x = np.concatenate([self._tail, block.astype(np.float32)])
        if len(x) < 2:
            self._tail = x
            return np.zeros(0, dtype=np.float32)

        if self._taps > 1:
            kernel = np.ones(self._taps, dtype=np.float32) / self._taps
            x = np.convolve(x, kernel, mode="same")

        n_out = int(np.floor((len(x) - 1 - self._pos) / self.step)) + 1
        if n_out <= 0:
            self._tail = x
            return np.zeros(0, dtype=np.float32)

        idx = self._pos + self.step * np.arange(n_out, dtype=np.float64)
        out = np.interp(idx, np.arange(len(x)), x).astype(np.float32)

        nxt = idx[-1] + self.step
        keep = min(int(np.floor(nxt)), len(x))
        self._pos = nxt - keep
        self._tail = x[keep:]
        return out


# --------------------------------------------------------------------------
# device discovery
# --------------------------------------------------------------------------

def _speakers():
    import soundcard as sc

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return sc.all_speakers()


def list_devices() -> dict:
    """Everything selectable, split by the mode it belongs to."""
    import sounddevice as sd

    out: dict = {"loopback": [], "mic": []}
    try:
        import soundcard as sc

        default = sc.default_speaker().name
        for s in _speakers():
            out["loopback"].append({"name": s.name, "default": s.name == default})
    except Exception as exc:  # pragma: no cover - non-Windows / no WASAPI
        log.warning("loopback devices unavailable: %s", exc)

    apis = sd.query_hostapis()
    default_in = sd.default.device[0]
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0:
            out["mic"].append(
                {
                    "index": i,
                    "name": d["name"],
                    "hostapi": apis[d["hostapi"]]["name"],
                    "rate": int(d["default_samplerate"]),
                    "default": i == default_in,
                }
            )
    return out


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------

class _Backend:
    """Base: converts whatever the device gives us into 16 kHz mono blocks."""

    rate = CAPTURE_RATE

    def __init__(self, on_block):
        self.on_block = on_block
        self._res: Resampler | None = None

    def _feed(self, block):
        mono = block.mean(axis=1) if block.ndim > 1 else block
        if not len(mono):
            return
        if self._res is None:
            self._res = Resampler(self.rate, TARGET_RATE)
        out = self._res.process(mono)
        if len(out):
            self.on_block(out)


class _LoopbackBackend(_Backend):
    """Pulls from a speaker's WASAPI loopback stream on its own thread."""

    def __init__(self, name_filter: str, on_block):
        super().__init__(on_block)
        self.rate = CAPTURE_RATE
        self._mic = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.label = ""

        import soundcard as sc

        speakers = _speakers()
        if not speakers:
            raise RuntimeError("No playback devices found for loopback capture.")

        chosen = None
        if name_filter:
            needle = name_filter.lower()
            chosen = next((s for s in speakers if needle in s.name.lower()), None)
            if chosen is None:
                raise RuntimeError(
                    f"No speaker matching {name_filter!r}. "
                    "Run: python -m app.main --list-devices"
                )
        else:
            chosen = sc.default_speaker()

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._mic = sc.get_microphone(id=str(chosen.name), include_loopback=True)
        self.label = f"{chosen.name} @ {self.rate} Hz (loopback)"

    def start(self):
        self._thread = threading.Thread(target=self._run, name="loopback", daemon=True)
        self._thread.start()

    @staticmethod
    def _com_init():
        """WASAPI is COM, and COM must be initialised on *this* thread.

        Without it, opening the recorder from a worker thread fails with
        0x800401F0 (CO_E_NOTINITIALIZED) -- intermittently, depending on what
        else touched COM first, which makes it a nasty one to chase.
        """
        try:
            import ctypes

            hr = ctypes.windll.ole32.CoInitializeEx(None, 0)  # multithreaded
            if hr not in (0, 1, 0x80010106):  # S_OK, S_FALSE, RPC_E_CHANGED_MODE
                log.warning("CoInitializeEx returned 0x%08x", hr & 0xFFFFFFFF)
        except Exception:
            pass  # not Windows, or no ole32; the recorder will report it

    def _run(self):
        frames = int(self.rate * BLOCK_SECONDS)
        self._com_init()
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with self._mic.recorder(
                    samplerate=self.rate, channels=2, blocksize=frames
                ) as rec:
                    while not self._stop.is_set():
                        self._feed(rec.record(numframes=frames))
        except Exception:
            if not self._stop.is_set():
                log.exception("loopback capture stopped")

    def stop(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


class _MicBackend(_Backend):
    """Callback stream on a normal input device."""

    def __init__(self, name_filter: str, on_block):
        import sounddevice as sd

        super().__init__(on_block)
        devices = sd.query_devices()
        candidates = [i for i, d in enumerate(devices) if d["max_input_channels"] > 0]
        if name_filter:
            needle = name_filter.lower()
            candidates = [i for i in candidates if needle in devices[i]["name"].lower()]
            if not candidates:
                raise RuntimeError(
                    f"No input device matching {name_filter!r}. "
                    "Run: python -m app.main --list-devices"
                )
        elif sd.default.device[0] in candidates:
            candidates.insert(0, candidates.pop(candidates.index(sd.default.device[0])))
        if not candidates:
            raise RuntimeError("No microphone found.")

        self._index = candidates[0]
        dev = devices[self._index]
        self.rate = int(dev["default_samplerate"])
        self._channels = max(1, min(2, dev["max_input_channels"]))
        self.label = f"{dev['name'].strip()} @ {self.rate} Hz (mic)"
        self._stream = None

    def start(self):
        import sounddevice as sd

        self._stream = sd.InputStream(
            device=self._index,
            channels=self._channels,
            samplerate=self.rate,
            dtype="float32",
            blocksize=int(self.rate * BLOCK_SECONDS),
            callback=lambda indata, frames, t, status: self._feed(indata.copy()),
        )
        self._stream.start()

    def stop(self):
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


class _MixBackend:
    """Both at once: the call audio AND the room microphone, summed.

    Use this when some students are on the call and some are in the room with
    you -- or simply when you want your own voice in the transcript too. The two
    streams arrive on different clocks, so each gets a short buffer and a steady
    20 ms tick sums whatever has landed.
    """

    STEP = 320                        # 20 ms at 16 kHz
    MAX_BUFFER = TARGET_RATE * 2      # bound memory if a device stalls
    STALLED = TARGET_RATE // 2        # 0.5 s ahead means the other side died

    def __init__(self, cfg, on_block):
        self.on_block = on_block
        self.rate = TARGET_RATE
        self._buf = [np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)]
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        # The device filter names the speaker; the microphone uses the default.
        self._subs = [
            _LoopbackBackend(cfg.device, lambda b: self._collect(0, b)),
            _MicBackend("", lambda b: self._collect(1, b)),
        ]
        self.label = (
            f"{self._subs[0].label.split(' @ ')[0]} + "
            f"{self._subs[1].label.split(' @ ')[0]} (both)"
        )

    def _collect(self, index: int, block: np.ndarray):
        with self._lock:
            merged = np.concatenate([self._buf[index], block])
            self._buf[index] = merged[-self.MAX_BUFFER:]

    def _pull(self, index: int, count: int) -> np.ndarray:
        buf = self._buf[index]
        self._buf[index] = buf[count:]
        return buf[:count]

    def _run(self):
        """Clocked by the audio itself, never by the wall clock.

        Emitting on a timer looked right but silently corrupted speech: a tick
        that found a buffer momentarily empty inserted silence, the real samples
        stayed queued, and the backlog grew until the cap threw audio away --
        which ate whole words out of the middle of a sentence. So only mix what
        both sides have actually delivered.
        """
        while not self._stop.is_set():
            with self._lock:
                n0, n1 = len(self._buf[0]), len(self._buf[1])
                take = (min(n0, n1) // self.STEP) * self.STEP
                if take:
                    left = self._pull(0, take)
                    right = self._pull(1, take)
                elif max(n0, n1) >= self.STALLED:
                    # One device has stopped delivering; carry on with the other
                    # rather than blocking the whole class on it.
                    live = 0 if n0 > n1 else 1
                    take = (max(n0, n1) // self.STEP) * self.STEP
                    left = self._pull(live, take)
                    right = np.zeros(take, dtype=np.float32)
                else:
                    left = None

            if left is None:
                time.sleep(0.005)
                continue
            self.on_block(np.clip(left + right, -1.0, 1.0))

    def start(self):
        for sub in self._subs:
            sub.start()
        self._thread = threading.Thread(target=self._run, name="mixer", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        for sub in self._subs:
            sub.stop()
        if self._thread is not None:
            self._thread.join(timeout=1.0)


# --------------------------------------------------------------------------
# facade
# --------------------------------------------------------------------------

class AudioSource:
    """Pushes mono 16 kHz float32 blocks onto `out_queue`."""

    def __init__(self, cfg, out_queue: queue.Queue):
        self.cfg = cfg
        self.out = out_queue
        self.level = 0.0          # smoothed RMS, for the UI meter
        self.paused = False
        self.device_label = ""
        self._backend = None

    def _on_block(self, chunk: np.ndarray):
        """Receives 16 kHz mono straight from the backend."""
        if self.paused or not len(chunk):
            return
        rms = float(np.sqrt(np.mean(np.square(chunk))))
        self.level = 0.7 * self.level + 0.3 * rms
        try:
            self.out.put_nowait(chunk)
        except queue.Full:
            pass

    def start(self) -> str:
        if self.cfg.mode == "loopback":
            self._backend = _LoopbackBackend(self.cfg.device, self._on_block)
        elif self.cfg.mode == "mic":
            self._backend = _MicBackend(self.cfg.device, self._on_block)
        elif self.cfg.mode == "both":
            self._backend = _MixBackend(self.cfg, self._on_block)
        else:
            raise ValueError(
                f'audio.mode must be "loopback", "mic" or "both", not {self.cfg.mode!r}'
            )

        self._backend.start()
        self.device_label = self._backend.label
        return self.device_label

    def stop(self):
        if self._backend is not None:
            self._backend.stop()
            self._backend = None
