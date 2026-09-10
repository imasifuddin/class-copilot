"""Local web server: the chat page, the settings API, and the live websocket.

Bound to the LAN so your phone can reach it. Every route except the page shell
and the pairing endpoint needs a token, which a device gets once by typing the
PIN printed in the laptop's terminal.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse

from . import context_store, settings_store
from .llm import PROVIDERS, build_system, get_provider

log = logging.getLogger("server")
WEB = Path(__file__).resolve().parent / "web"


def _token(request: Request) -> str | None:
    return request.headers.get("x-cc-token") or request.query_params.get("token")


def build_app(hub, auth) -> FastAPI:
    app = FastAPI(title="class-copilot")

    def require(request: Request):
        if not auth.ok(_token(request), request.client.host if request.client else None):
            raise HTTPException(status_code=401, detail="pair this device first")

    @app.on_event("startup")
    async def _startup():
        hub.loop = asyncio.get_running_loop()
        asyncio.create_task(_status_ticker(hub))
        asyncio.create_task(hub.watchdog())

    # ---------- static shell ----------

    @app.get("/")
    async def index():
        return FileResponse(WEB / "index.html")

    @app.get("/manifest.webmanifest")
    async def manifest():
        return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")

    @app.get("/sw.js")
    async def service_worker():
        return FileResponse(WEB / "sw.js", media_type="application/javascript")

    @app.get("/icon-{size}.png")
    async def icon(size: str):
        path = WEB / f"icon-{size}.png"
        if not path.exists():
            raise HTTPException(status_code=404)
        return FileResponse(path, media_type="image/png")

    # ---------- pairing ----------

    @app.get("/api/auth")
    async def auth_state(request: Request):
        host = request.client.host if request.client else None
        return {
            "required": auth.required and not auth.is_local(host),
            "paired": auth.ok(_token(request), host),
            "devices": len(auth.devices),
        }

    @app.post("/api/pair")
    async def pair(request: Request):
        body = await request.json()
        host = request.client.host if request.client else None
        token = auth.pair(str(body.get("pin", "")), host, str(body.get("name", "phone"))[:40])
        if not token:
            raise HTTPException(status_code=403, detail="wrong PIN (or too many tries)")
        log.info("paired a new device from %s", host)
        return {"token": token}

    # ---------- settings ----------

    @app.get("/api/settings")
    async def get_settings(request: Request):
        require(request)
        return {
            "settings": settings_store.public(hub.cfg),
            "providers": PROVIDERS,
            "restart_pending": hub.restart_pending,
        }

    @app.post("/api/settings")
    async def post_settings(request: Request):
        require(request)
        changes = await request.json()
        saved, restart = settings_store.update(hub.cfg, changes)
        hub.reload_llm()
        if restart:
            hub.restart_pending = sorted(set(hub.restart_pending) | set(restart))
        await hub.broadcast({"type": "settings", "settings": settings_store.public(hub.cfg)})
        return {
            "ok": True,
            "settings": settings_store.public(hub.cfg),
            "restart_pending": hub.restart_pending,
        }

    @app.post("/api/settings/models")
    async def list_models(request: Request):
        """Live model list from whichever provider is configured."""
        require(request)
        try:
            provider = get_provider(hub.cfg.llm)
            models = await provider.list_models()
            hub.provider = None
            return {"ok": True, "models": models}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    @app.post("/api/settings/test")
    async def test_settings(request: Request):
        require(request)
        try:
            provider = get_provider(hub.cfg.llm)
            out = []
            async for piece in provider.stream(
                hub.system_for("what is a variable"),
                [{"role": "user", "content": 'Doubt to answer: "what is a variable"'}],
            ):
                out.append(piece)
            text = "".join(out).strip()
            if not text:
                return JSONResponse({"ok": False, "error": "the model returned nothing"})
            hub.provider = None  # the test client is disposable
            return {"ok": True, "sample": text[:400]}
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"{type(exc).__name__}: {exc}"})

    # ---------- your own material ----------

    @app.get("/api/context")
    async def get_context(request: Request):
        require(request)
        return {"docs": context_store.summary()}

    @app.post("/api/context")
    async def add_context(request: Request):
        """Accepts pasted text, or a file the phone picked."""
        require(request)
        body = await request.json()
        name = str(body.get("name", "note"))
        text = body.get("text")
        if text is None and body.get("data_b64"):
            import base64

            try:
                text = context_store.extract(name, base64.b64decode(body["data_b64"]))
            except Exception as exc:
                return JSONResponse({"ok": False, "error": str(exc)})
        text = (text or "").strip()
        if not text:
            return JSONResponse(
                {"ok": False, "error": "nothing readable in that file"})
        doc = context_store.save(name.rsplit(".", 1)[0], text)
        return {"ok": True, "doc": {"name": doc.name, "chars": doc.chars},
                "docs": context_store.summary()}

    @app.delete("/api/context/{name}")
    async def del_context(name: str, request: Request):
        require(request)
        return {"ok": context_store.delete(name), "docs": context_store.summary()}

    # ---------- audio streamed from the phone ----------

    @app.post("/api/audio")
    async def ingest_audio(request: Request):
        """A short post of 16 kHz mono PCM16 from the Android app.

        Short and complete, rather than one long stream: hosting proxies buffer
        a streaming request body, which delayed the first audio by ~19s on
        Render. Each post is announced by `x-cc-state`.
        """
        require(request)
        state = request.headers.get("x-cc-state", "chunk").lower()
        if state == "start":
            hub.remote.begin(request.headers.get("x-cc-device", "phone"),
                             request.headers.get("x-cc-intent", ""))

        body = await request.body()
        if body:
            hub.remote.feed(body)

        if state == "stop":
            hub.remote.end()

        await hub.broadcast(hub.status())      # move the dot immediately
        return {"ok": True, "held": hub.remote.held, "bytes": len(body)}

    # ---------- live ----------

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        host = sock.client.host if sock.client else None
        if not auth.ok(sock.query_params.get("token"), host):
            await sock.send_json({"type": "unauthorized"})
            await sock.close(code=4401)
            return

        hub.clients.add(sock)
        try:
            await sock.send_json(hub.snapshot())
            while True:
                await _dispatch(hub, await sock.receive_json())
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("websocket error")
        finally:
            hub.clients.discard(sock)

    return app


async def _dispatch(hub, msg: dict):
    kind = msg.get("type")
    if kind == "ask":
        await hub.ask(msg.get("text", ""), source="typed")
    elif kind == "answer_line":
        line = next((l for l in hub.lines if l["id"] == msg.get("line_id")), None)
        if line:
            hub.detector.note_manual_fire()
            await hub.ask(line["text"], source="clicked")
    elif kind == "stop":
        await hub.stop()
    elif kind == "pause":
        if hub.audio:
            hub.audio.paused = bool(msg.get("value"))
        await hub.broadcast(hub.status())
    elif kind == "trigger_mode":
        hub.cfg.trigger.mode = "manual" if msg.get("value") == "manual" else "auto"
        settings_store.update(hub.cfg, {"trigger": {"mode": hub.cfg.trigger.mode}})
        await hub.broadcast(hub.status())
    elif kind == "mic":
        # The phone says it started or stopped listening. This arrives over the
        # already-open websocket, so it does not depend on an audio post making
        # it through -- a dropped stop used to leave the session open until it
        # went stale, blocking every later question.
        if hub.remote is not None:
            if msg.get("on"):
                hub.remote.begin(str(msg.get("device", "phone"))[:40], "ask")
            else:
                hub.remote.end()
        await hub.broadcast(hub.status())
    elif kind == "ping":
        pass          # traffic is the point; a free host counts it as activity
    elif kind == "clear":
        hub.lines.clear()
        hub.cards.clear()
        hub.history.clear()
        await hub.broadcast({"type": "cleared"})


async def _status_ticker(hub):
    while True:
        await asyncio.sleep(0.25)
        if hub.clients:
            await hub.broadcast(hub.status())
