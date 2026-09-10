#!/usr/bin/env bash
# Checks that it answers real doubts and ignores ordinary classroom talk.
cd "$(dirname "$0")" || exit 1
PY=".venv/Scripts/python.exe"; [ -x "$PY" ] || PY=".venv/bin/python"
exec "$PY" -m app.gate_check
