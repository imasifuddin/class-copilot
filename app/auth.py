"""Pairing PIN for the phone.

Anyone on the same wifi can reach the server, so a device has to pair once with
a 6-digit PIN printed in the laptop's terminal. It then holds a long-lived token
kept in the phone's local storage. Requests from the laptop itself skip all of
this -- you should not have to type a PIN into your own machine.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
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
        self._secret = self._server_secret()

    def _server_secret(self) -> bytes:
        """Key for signing device tokens.

        A hosted box usually has a throwaway filesystem, so anything written to
        disk vanishes on the next restart and every phone would have to pair
        again. Deriving the key from CC_PIN (or CC_SECRET) makes tokens survive
        restarts. On a laptop the disk is real, so a random key is kept there.
        """
        explicit = os.getenv("CC_SECRET") or ""
        if explicit:
            return hashlib.sha256(explicit.encode()).digest()
        if self.fixed:
            return hashlib.sha256(f"class-copilot:{self.pin}".encode()).digest()
        seed = self._tokens.get("_secret")
        if not seed:
            seed = secrets.token_urlsafe(32)
            self._tokens["_secret"] = seed
            self._save()
        return hashlib.sha256(seed.encode()).digest()

    # ---------- signed, stateless tokens ----------

    def _sign(self, name: str) -> str:
        raw = base64.urlsafe_b64encode(name.encode()).decode().rstrip("=")
        sig = hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()[:32]
        return f"{raw}.{sig}"

    def _signature_ok(self, token: str) -> bool:
        try:
            raw, sig = token.rsplit(".", 1)
        except ValueError:
            return False
        expect = hmac.new(self._secret, raw.encode(), hashlib.sha256).hexdigest()[:32]
        return hmac.compare_digest(sig, expect)

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
        """Drop every device. Also rotates the signing key, so tokens issued
        earlier stop working even though validation is stateless."""
        self._tokens = {"_secret": secrets.token_urlsafe(32)}
        self._save()
        self._secret = self._server_secret()

    @property
    def devices(self) -> list[dict]:
        return [
            {"name": v.get("name", "device"), "paired": v.get("paired", 0)}
            for k, v in self._tokens.items()
            if k != "_secret" and isinstance(v, dict)
        ]

    # ---------- checks ----------

    def is_local(self, client_host: str | None) -> bool:
        return (client_host or "") in LOCAL_HOSTS

    def ok(self, token: str | None, client_host: str | None) -> bool:
        if not self.required or self.is_local(client_host):
            return True
        if not token:
            return False
        return self._signature_ok(token) or token in self._tokens

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

        token = self._sign(f"{name}:{int(now)}")
        self._tokens[token] = {"name": name, "paired": int(now), "host": client_host}
        try:
            self._save()          # only for the device list; validation is stateless
        except OSError:
            pass                  # read-only filesystem on some hosts
        return token
