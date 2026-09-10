#!/usr/bin/env bash
cd "$(dirname "$0")" || exit 1
PY=".venv/Scripts/python.exe"; [ -x "$PY" ] || PY=".venv/bin/python"
exec "$PY" -m app.check
