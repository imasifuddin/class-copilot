#!/usr/bin/env bash
# class-copilot launcher for Git Bash / WSL.
#   ./run.sh                 start it
#   ./run.sh --mode mic      listen through the laptop microphone instead
#   ./run.sh --local-only    do not expose it to the wifi
cd "$(dirname "$0")" || exit 1

PY=".venv/Scripts/python.exe"
[ -x "$PY" ] || PY=".venv/bin/python"

if [ ! -x "$PY" ]; then
    echo "First run: creating the virtual environment (a few minutes)..."
    python -m venv .venv
    PY=".venv/Scripts/python.exe"
    [ -x "$PY" ] || PY=".venv/bin/python"
    "$PY" -m pip install --upgrade pip
    "$PY" -m pip install -r requirements.txt
fi

if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env for your API key. You can also set the key later from the"
    echo "app's settings screen. Starting now either way..."
    echo
fi

# Tell you up front if the phone app needs reinstalling, so you are never
# guessing whether a change reached it.
if [ -x ./apk-status.sh ]; then
    ./apk-status.sh | head -1 | sed 's/^/[android] /'
fi

exec "$PY" -m app.main "$@"
