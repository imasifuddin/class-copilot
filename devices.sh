#!/usr/bin/env bash
# Lists the audio devices available for each capture mode.
cd "$(dirname "$0")" || exit 1
PY=".venv/Scripts/python.exe"; [ -x "$PY" ] || PY=".venv/bin/python"
exec "$PY" -m app.main --list-devices
