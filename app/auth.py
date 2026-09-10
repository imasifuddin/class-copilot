"""Pairing PIN for the phone.

Anyone on the same wifi can reach the server, so a device has to pair once with
a 6-digit PIN printed in the laptop's terminal. It then holds a long-lived token
kept in the phone's local storage. Requests from the laptop itself skip all of
this -- you should not have to type a PIN into your own machine.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

from .config import ROOT

TOKENS = ROOT / ".devices.json"
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}
MAX_ATTEMPTS = 6
LOCKOUT_S = 60


class Auth:
    def __init__(self, required: bool = True):
        self.required = required
        # A random PIN each start is right on your own laptop, where you can
        # read it. On a hosted server there is no terminal in front of you, so
        # CC_PIN pins it to something you already know.
        fixed = (os.getenv("CC_PIN") or "").strip()
        if re.fullmatch(r"\d{6}", fixed):
            self.pin = fixed
            self.fixed = True
        else:
            self.pin = f"{secrets.randbelow(1_000_000):06d}"
            self.fixed = False
        self._tokens: dict[str, dict] = self._load()
        self._fails: dict[str, list] = {}

    # ---------- storage ----------

    def _load(self) -> dict:
        if not TOKENS.exists():
            return {}
        try:
            return json.loads(TOKENS.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save(self):
        TOKENS.write_text(json.dumps(self._tokens, indent=2), encoding="utf-8")

    def forget_all(self):
        self._tokens = {}
        self._save()

    @property
    def devices(self) -> list[dict]:
        return [
            {"name": v.get("name", "device"), "paired": v.get("paired", 0)}
            for v in self._tokens.values()
        ]

    # ---------- checks ----------

    def is_local(self, client_host: str | None) -> bool:
        return (client_host or "") in LOCAL_HOSTS

    def ok(self, token: str | None, client_host: str | None) -> bool:
        if not self.required or self.is_local(client_host):
            return True
        return bool(token) and token in self._tokens

    def pair(self, pin: str, client_host: str | None, name: str = "phone") -> str | None:
        """Exchange the PIN for a token, with a crude brute-force brake."""
        now = time.time()
        recent = [t for t in self._fails.get(client_host or "?", []) if now - t < LOCKOUT_S]
        self._fails[client_host or "?"] = recent
        if len(recent) >= MAX_ATTEMPTS:
            return None
        if not secrets.compare_digest(str(pin).strip(), self.pin):
            recent.append(now)
            return None

        token = secrets.token_urlsafe(24)
        self._tokens[token] = {"name": name, "paired": int(now), "host": client_host}
        self._save()
        return token
