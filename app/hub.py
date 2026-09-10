"""The coordinator: audio in, one answer per finished question out.

The important behaviour lives here. Whisper hands us fragments -- a student
pausing to think splits "I have a doubt ... what is the difference between a
hash map and an array" into two pieces. Answering each piece separately is
useless, so fragments are gathered into a *turn* and only answered once the
speaker has actually stopped, the way a voice assistant waits for you to finish.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque

from . import context_store
from .llm import build_system, get_provider

log = logging.getLogger("hub")

MAX_TRANSCRIPT = 400
CONTEXT_LINES = 8          # transcript lines given to the model as context
HISTORY_CHARS = 900        # per remembered answer
TICK = 0.2                 # how often the turn watchdog looks


class Hub:
    def __init__(self, cfg):
        self.cfg = cfg
        self.loop: asyncio.AbstractEventLoop | None = None
        self.clients: set = set()

        self.lines: deque[dict] = deque(maxlen=MAX_TRANSCRIPT)
        self.cards: list[dict] = []
        self.history: list[tuple[str, str]] = []

        self.provider = None
        self._task: asyncio.Task | None = None
        self._queue: deque[tuple[str, str]] = deque()
        self._next_id = 1

        # --- turn assembly ---
        self._turn: list[str] = []          # fragments of the sentence in progress
        self._turn_ids: list[int] = []
        self._pending = 0                   # clips submitted but not yet transcribed
        self._last_audio = 0.0              # when speech last stopped (monotonic)
        self._last_notice = 0.0             # throttle for visible warnings
        self._count_lock = threading.Lock()

        self.restart_pending: list[str] = []

        # Filled in by main.py once the audio stack is up.
        self.audio_sink = None      # callable that pushes 16 kHz mono blocks
        self.remote = None          # RemoteAudio, the phone acting as a mic
        self.audio = None
        self.transcriber = None
        self.segmenter = None
        self.detector = None

    # ---------- plumbing ----------

    def reload_llm(self):
        """Called after the settings screen changes anything about the model."""
        self.provider = None

    def system_for(self, question: str = "") -> str:
        """Persona, answer style, and the slice of your material that fits."""
        return build_system(self.cfg.llm, context_store.build(question))

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    async def broadcast(self, msg: dict):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    def emit(self, msg: dict):
        """Broadcast from a non-async thread."""
        if self.loop is not None:
            asyncio.run_coroutine_threadsafe(self.broadcast(msg), self.loop)

    def snapshot(self) -> dict:
        return {
            "type": "hello",
            "lines": list(self.lines),
            "cards": self.cards[-30:],
            "status": self.status(),
        }

    def status(self) -> dict:
        return {
            "type": "status",
            "listening": bool(
                (self.audio and not self.audio.paused)
                or (self.remote and self.remote.streaming)
            ),
            "stt_ready": bool(self.transcriber and self.transcriber.ready),
            "stt_error": self.transcriber.error if self.transcriber else None,
            "device": self._device_label(),
            "level": round(float(self.audio.level) if self.audio else 0.0, 4),
            "phone_mic": bool(self.remote and self.remote.streaming),
            "audio_mode": self.cfg.audio.mode,
            "busy": bool(self._task and not self._task.done()),
            "collecting": bool(self._turn) or self._pending > 0,
            "holding_mic": self.holding_mic(),
            "queued": len(self._queue),
            "trigger_mode": self.cfg.trigger.mode,
            "provider": self.cfg.llm.provider,
            "model": self.cfg.llm.model,
            "restart_pending": self.restart_pending,
        }

    def _device_label(self) -> str:
        parts = []
        if self.audio and self.audio.device_label:
            parts.append(self.audio.device_label)
        if self.remote and self.remote.streaming:
            parts.append(f"{self.remote.device} (phone mic)")
        return "  +  ".join(parts) or "nothing is listening"

    # ---------- audio-side bookkeeping (called from the pipeline thread) ----------

    def note_clip(self):
        """A finished utterance has been handed to Whisper."""
        with self._count_lock:
            self._pending += 1
            self._last_audio = time.monotonic()

    # ---------- transcript ----------

    def on_transcript(self, text: str, secs: float):
        """Called from the STT thread; text may be empty for a junk clip."""
        if self.loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._handle_line(text, secs), self.loop)

    async def _handle_line(self, text: str, secs: float):
        with self._count_lock:
            self._pending = max(0, self._pending - 1)
            self._last_audio = time.monotonic()

        if not text:
            return

        line = {
            "id": self._id(),
            "ts": time.time(),
            "text": text,
            "question": self.detector.reason(text) if self.detector else None,
            "stt_s": round(secs, 2),
        }
        self.lines.append(line)
        self._turn.append(text)
        self._turn_ids.append(line["id"])
        await self.broadcast({"type": "line", **line})

        # Someone lecturing without a real pause would otherwise grow a turn
        # forever; close it off and let the next words start a fresh one. Not
        # while you are holding the mic open -- there you decide when it ends.
        if (not self.holding_mic()
                and sum(len(t.split()) for t in self._turn)
                >= self.cfg.trigger.max_turn_words):
            log.info("turn hit the word cap; closing it early")
            await self._finish_turn()

    # ---------- turn completion ----------

    def holding_mic(self) -> bool:
        """True while you are deliberately holding the microphone open."""
        return bool(self.remote and self.remote.held)

    def _turn_is_over(self) -> bool:
        """Has the speaker finished, and has everything they said come back?"""
        if not self._turn:
            return False
        if self.holding_mic():
            # You have not tapped stop yet. A pause mid-question is just a
            # pause -- keep collecting until you close the mic yourself.
            return False
        with self._count_lock:
            if self._pending > 0:
                return False
            quiet_for = time.monotonic() - self._last_audio
        if self.segmenter is not None and self.segmenter.speaking:
            return False        # they have already started talking again
        return quiet_for >= self.cfg.trigger.end_of_turn_s

    async def watchdog(self):
        """Closes a turn once the room has gone quiet, and drains the queue."""
        while True:
            await asyncio.sleep(TICK)
            try:
                if self._turn_is_over():
                    await self._finish_turn()
                if self._queue and not (self._task and not self._task.done()):
                    question, source = self._queue.popleft()
                    self._task = asyncio.create_task(self._answer(question, source))
            except Exception:
                log.exception("turn watchdog")

    async def _finish_turn(self):
        text = " ".join(self._turn).strip()
        self._turn, self._turn_ids = [], []
        if not text:
            return

        # Push-to-listen: you unmuted on purpose, so answer it. No gate, no
        # keyword check, no extra classification call.
        if self.remote is not None and self.remote.force:
            self.remote.force = False        # consumed; the next tap sets it again
            await self.ask(text, source="you unmuted")
            return

        if self.cfg.trigger.mode != "auto":
            return
        words = text.split()
        if len(words) < self.cfg.trigger.min_words:
            return

        # The model is the judge when it is available. Keyword matching alone
        # answers far too much: "can you hear me sir" opens with "can", and
        # "when is the next class" opens with "when", but neither is a doubt.
        if self.cfg.trigger.smart:
            why = await self._classify(text)
            if why is None:                      # model unreachable
                why = self.detector.reason(text) if self.detector else None
        else:
            why = self.detector.reason(text) if self.detector else None
        if not why:
            return
        await self.ask(text, source=f"auto - {why}")

    async def _classify(self, text: str) -> str | None:
        """Ask the model whether this turn actually wants an answer.

        This is what lets a student just say "why does my loop run forever"
        without any 'sir I have a doubt' preamble.
        """
        system = (
            "You decide whether a teaching assistant should write an answer for "
            "one turn of classroom speech. Reply with exactly one word, ANSWER "
            "or SKIP.\n\n"
            "ANSWER only when someone wants something explained: a question "
            "about a concept, a doubt, confusion, an error or unexpected "
            "output, or a request for code, an example or a comparison. There "
            "must be real technical substance to answer.\n\n"
            "SKIP everything else, including:\n"
            "- greetings, thanks, goodbyes, agreement (\"yes sir\", \"got it\")\n"
            "- audio and video checks (\"can you hear me\", \"is my screen "
            "visible\", \"I was on mute\")\n"
            "- scheduling and logistics (\"when is the next class\", \"will you "
            "share the notes\", \"my internet is slow\")\n"
            "- the teacher lecturing, or narrating what they are doing\n"
            "- small talk, and anything not about the subject being taught\n"
            "- fragments too garbled to act on\n\n"
            "A sentence can be phrased as a question and still be SKIP if it is "
            "not asking for something to be explained."
        )
        try:
            if self.provider is None:
                self.provider = get_provider(self.cfg.llm)
            got = ""
            async for piece in self.provider.stream(
                system, [{"role": "user", "content": text}]
            ):
                got += piece      # one word; reading it fully avoids a noisy abort
            return "sounds like a doubt" if "ANSWER" in got.upper() else None
        except Exception as exc:
            # Silence here is dangerous: doubts with no keywords would simply
            # never be answered and nothing on screen would say why.
            log.warning("classifier unavailable (%s); using keywords only", exc)
            await self.notice(
                f"Cannot tell whether that was a doubt - {type(exc).__name__}: {exc}"
            )
            return None

    async def notice(self, text: str, gap: float = 60.0):
        """A throttled, visible warning; better than failing quietly."""
        now = time.monotonic()
        if now - self._last_notice < gap:
            return
        self._last_notice = now
        await self.broadcast({"type": "notice", "text": text})

    # ---------- answering ----------

    async def ask(self, question: str, source: str = "manual"):
        question = question.strip()
        if not question:
            return
        if self._task and not self._task.done():
            self._queue.append((question, source))    # never drop a doubt
            await self.broadcast(self.status())
            return
        if self.provider is None:
            self.provider = get_provider(self.cfg.llm)
        self._task = asyncio.create_task(self._answer(question, source))

    async def stop(self):
        self._queue.clear()
        if self._task and not self._task.done():
            self._task.cancel()

    def _messages(self, question: str) -> list[dict]:
        msgs: list[dict] = []
        for q, a in self.history[-self.cfg.llm.context_turns :]:
            msgs.append({"role": "user", "content": q})
            msgs.append({"role": "assistant", "content": a[:HISTORY_CHARS]})

        context = self.recent_context(exclude=question)
        parts = []
        if context:
            parts.append("Recent classroom transcript:\n" + "\n".join(context))
        parts.append(f'Doubt to answer: "{question}"')
        msgs.append({"role": "user", "content": "\n\n".join(parts)})
        return msgs

    def recent_context(self, exclude: str = "") -> list[str]:
        out = [l["text"] for l in list(self.lines)[-CONTEXT_LINES:]]
        if exclude:
            out = [t for t in out if t not in exclude]
        return out

    async def _answer(self, question: str, source: str):
        card = {
            "id": self._id(),
            "ts": time.time(),
            "question": question,
            "source": source,
            "answer": "",
        }
        self.cards.append(card)
        await self.broadcast(
            {"type": "card", "id": card["id"], "question": question, "source": source}
        )

        t0 = time.time()
        chunks: list[str] = []
        try:
            async for delta in self.provider.stream(
                self.system_for(question), self._messages(question)
            ):
                chunks.append(delta)
                await self.broadcast({"type": "delta", "id": card["id"], "text": delta})
        except asyncio.CancelledError:
            card["answer"] = "".join(chunks)
            await self.broadcast({"type": "done", "id": card["id"], "cancelled": True})
            raise
        except Exception as exc:
            log.exception("answer failed")
            await self.broadcast(
                {"type": "error", "id": card["id"], "text": f"{type(exc).__name__}: {exc}"}
            )
            return

        answer = "".join(chunks)
        card["answer"] = answer
        self.history.append((question, answer))
        await self.broadcast(
            {"type": "done", "id": card["id"], "ms": int((time.time() - t0) * 1000)}
        )
