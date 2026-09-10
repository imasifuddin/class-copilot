"""Entry point: wires audio -> segmenter -> whisper -> hub -> laptop + phone."""
from __future__ import annotations

import argparse
import logging
import os
import queue
import socket
import sys
import threading
import time
import webbrowser

import uvicorn
from dotenv import load_dotenv

from . import config, settings_store
from .audio import AudioSource, list_devices
from .auth import Auth
from .hub import Hub
from .remote_audio import RemoteAudio
from .segmenter import Segmenter
from .server import build_app
from .stt import Transcriber
from .trigger import QuestionDetector

def _utf8_console():
    """Model answers contain arrows and dashes that a cp1252 console refuses."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


log = logging.getLogger("main")


def _pipeline(raw_q: queue.Queue, seg: Segmenter, stt: Transcriber,
              hub: Hub, stop: threading.Event):
    """Resampled audio in, complete utterances out to the STT queue."""
    while not stop.is_set():
        try:
            block = raw_q.get(timeout=0.25)
        except queue.Empty:
            continue
        for clip in seg.push(block):
            if stt.submit(clip):
                hub.note_clip()   # the hub waits for this before answering


def lan_ips() -> list[str]:
    """Addresses this machine is reachable on, best guess first."""
    found: list[str] = []
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))          # no packet is sent
        found.append(sock.getsockname()[0])
    except OSError:
        pass
    finally:
        sock.close()
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def print_devices():
    devs = list_devices()
    print('\nmode = "loopback"   captures what these are playing (the students)')
    for d in devs["loopback"]:
        print(f"   {'*' if d['default'] else ' '} {d['name']}")
    print('\nmode = "mic"        captures the room through these')
    for d in devs["mic"]:
        print(
            f"   {'*' if d['default'] else ' '} {d['name'].strip()}"
            f"   [{d['hostapi']}, {d['rate']} Hz]"
        )
    print("\n   * = used when [audio] device is left empty in config.toml.")
    print("   Otherwise put any part of the name there, e.g. device = \"Realtek\".")


def print_banner(cfg, auth, url_local: str, ips: list[str]):
    port = cfg.server.port
    print()
    print("=" * 62)
    print("  class-copilot is running")
    print("=" * 62)
    print(f"  on this laptop :  {url_local}")
    if cfg.server.host in ("0.0.0.0", "::"):
        if ips:
            for ip in ips:
                print(f"  on your phone  :  http://{ip}:{port}")
        else:
            print("  on your phone  :  no LAN address found -- are you on wifi?")
    else:
        print(f"  phone access   :  off ([server] host = {cfg.server.host!r})")

    if auth.required:
        print()
        note = ("  (fixed by CC_PIN)" if getattr(auth, "fixed", False)
                else "     (type this on the phone, once)")
        print(f"  pairing PIN    :  {auth.pin}{note}")
        if auth.devices:
            print(f"  already paired :  {len(auth.devices)} device(s)")
    on_lan = bool(ips) and cfg.server.host in ("0.0.0.0", "::")
    app_mode = cfg.audio.mode == "phone"
    deep = ""
    if on_lan:
        deep = (f"classcopilot://{ips[0]}:{port}/?pin={auth.pin}" if auth.required
                else f"classcopilot://{ips[0]}:{port}/")
        print(f"  android app    :  {deep}")
    if on_lan and app_mode:
        print()
        print(f"  TYPE THIS IN THE APP -- address:  {ips[0]}:{port}")
        print(f"                             PIN:  {auth.pin}")

    if on_lan:
        try:
            import qrcode

            # In phone mode the browser is useless (it cannot capture audio),
            # so point the QR at the app instead of at the web page.
            qr = qrcode.QRCode(border=1)
            qr.add_data(deep if app_mode else f"http://{ips[0]}:{port}")
            qr.make()
            if app_mode:
                print("\n  or scan this -- it opens the app with both already filled in:\n")
            else:
                print("\n  scan this with your phone camera:\n")
            qr.print_ascii(invert=True)
        except Exception:
            pass
    print("=" * 62)
    if app_mode:
        print("  This machine is not listening. The phone app is the microphone.")
    else:
        print("  Phone tip: open the link, then browser menu -> Add to Home screen.")
    print("  Press Ctrl+C here to stop.")
    print("=" * 62 + "\n")


def main(argv=None):
    _utf8_console()
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(prog="class-copilot")
    parser.add_argument("--list-devices", action="store_true", help="show audio devices and exit")
    parser.add_argument("--mode", choices=["loopback", "mic", "both", "phone"],
                        help="override [audio] mode")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--local-only", action="store_true", help="do not listen on the LAN")
    parser.add_argument("--no-pin", action="store_true", help="disable phone pairing (unsafe wifi)")
    parser.add_argument("--forget-devices", action="store_true", help="unpair every phone")
    parser.add_argument("--reset-settings", action="store_true",
                        help="discard settings.json and go back to config.toml")
    args = parser.parse_args(argv)

    if args.list_devices:
        print_devices()
        return 0

    cfg = config.load()
    if os.getenv("PORT"):          # Render, Fly, Spaces and friends set this
        cfg.server.port = int(os.environ["PORT"])
    if args.reset_settings and settings_store.STORE.exists():
        settings_store.STORE.unlink()
        print(f"Deleted {settings_store.STORE.name}; back to config.toml settings.")
    settings_store.apply(cfg)          # settings.json wins over config.toml
    if args.mode:
        cfg.audio.mode = args.mode
    if args.no_browser:
        cfg.server.open_browser = False
    if args.local_only:
        cfg.server.host = "127.0.0.1"
    if args.no_pin:
        cfg.server.require_pin = False

    auth = Auth(required=cfg.server.require_pin)
    if args.forget_devices:
        auth.forget_all()
        print("All phones unpaired.")

    hub = Hub(cfg)
    hub.detector = QuestionDetector(cfg.trigger)

    raw_q: queue.Queue = queue.Queue(maxsize=400)
    audio = AudioSource(cfg.audio, raw_q)
    stt = Transcriber(cfg.stt, hub.on_transcript, llm=cfg.llm)
    seg = Segmenter(cfg.audio)
    hub.transcriber, hub.segmenter = stt, seg
    hub.remote = RemoteAudio(hub)

    def push_audio(block):
        """Where the phone's microphone lands, same queue as local capture."""
        try:
            raw_q.put_nowait(block)
        except queue.Full:
            pass

    hub.audio_sink = push_audio

    stop = threading.Event()
    stt.start()
    worker = threading.Thread(
        target=_pipeline, args=(raw_q, seg, stt, hub, stop), name="pipeline", daemon=True
    )
    worker.start()

    if cfg.audio.mode == "phone":
        log.info("not listening on this machine; waiting for the phone's microphone")
    else:
        try:
            label = audio.start()
        except Exception as exc:
            log.error("audio capture failed: %s", exc)
            log.error("run `python -m app.main --list-devices` to see what is available")
            return 1
        hub.audio = audio
        log.info("capturing: %s", label)

    url = f"http://127.0.0.1:{cfg.server.port}"
    print_banner(cfg, auth, url, lan_ips())
    if cfg.server.open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    app = build_app(hub, auth)
    try:
        uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        stt.stop()
        audio.stop()
        time.sleep(0.2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
