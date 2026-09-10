"""Decides whether a transcript line is a doubt that deserves an answer.

Deliberately generous: a false positive costs a few cents and a card you ignore,
a false negative costs you a silence in front of your students. Anything it
misses is still one click away in the transcript pane.
"""
from __future__ import annotations

import re
import time

OPENERS = {
    "what", "why", "how", "when", "where", "which", "who", "whom", "whose",
    "can", "could", "would", "should", "shall", "will", "is", "are", "am",
    "was", "were", "do", "does", "did", "may", "might", "if", "explain",
    "tell", "show", "give", "define", "describe", "compare", "difference",
    "kya", "kyun", "kaise", "kaisa", "matlab",
}

PHRASES = (
    "explain", "difference between", "how does", "how do", "how can", "how to",
    "what is", "what are", "what does", "why is", "why does", "why do",
    "can you", "could you", "i have a doubt", "i had a doubt", "doubt",
    "not clear", "didn't get", "did not get", "didnt get", "i don't understand",
    "i dont understand", "confused", "confusing", "one question", "a question",
    "my question", "example", "for example", "give an example", "use case",
    "error", "not working", "throwing", "exception", "output", "means",
    "meaning of", "samajh nahi", "samajh nhi", "doubt hai", "sir ek",
)

WORD = re.compile(r"[A-Za-z0-9_+#.']+")


class QuestionDetector:
    def __init__(self, cfg):
        self.cfg = cfg
        self._last_fire = 0.0

    def reason(self, text: str) -> str | None:
        """Return why this line looks like a question, or None."""
        clean = text.strip()
        words = WORD.findall(clean)
        if len(words) < self.cfg.min_words:
            return None

        low = clean.lower()
        if clean.endswith("?"):
            return "question mark"
        if words[0].lower() in OPENERS:
            return f"opens with '{words[0].lower()}'"
        if len(words) > 1 and words[0].lower() == "sir" and words[1].lower() in OPENERS:
            return "addressed to you"
        for phrase in PHRASES:
            if phrase in low:
                return f"contains '{phrase}'"
        return None

    def should_fire(self, text: str) -> str | None:
        """Same as `reason`, but also enforces the cooldown."""
        if self.cfg.mode != "auto":
            return None
        why = self.reason(text)
        if not why:
            return None
        now = time.monotonic()
        if now - self._last_fire < self.cfg.cooldown_s:
            return None
        self._last_fire = now
        return why

    def note_manual_fire(self):
        self._last_fire = time.monotonic()
