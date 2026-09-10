#!/usr/bin/env bash
# Publishes the server to a Hugging Face Space so it runs without your laptop.
#
#   ./deploy-hf.sh <your-hf-username> <space-name>
#
# Only the server is pushed. Your .env, settings, paired devices and personal
# material stay on your machine.
set -e
cd "$(dirname "$0")"

USER="$1"
SPACE="$2"
if [ -z "$USER" ] || [ -z "$SPACE" ]; then
    echo "usage: ./deploy-hf.sh <hf-username> <space-name>"
    echo "example: ./deploy-hf.sh imasifuddin class-copilot"
    exit 1
fi

STAGE="$(mktemp -d)"
echo "staging in $STAGE"

cp Dockerfile requirements-server.txt config.toml "$STAGE/"
cp -r app "$STAGE/app"
find "$STAGE" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# A Space is configured by the front matter of its README.
cat > "$STAGE/README.md" <<HEADER
---
title: class-copilot
emoji: 🎤
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 7860
pinned: false
---

# class-copilot server

The listening half of [class-copilot](https://github.com/$USER/class-copilot).
Your phone streams what it hears here; this transcribes it, decides whether it
was a doubt, and streams an answer back.

Set two secrets under **Settings -> Variables and secrets**:

| Name | Value |
|---|---|
| \`GROQ_API_KEY\` | your key from console.groq.com |
| \`CC_PIN\` | any 6 digits, used to pair your phone |
HEADER

cat > "$STAGE/.gitignore" <<'IGN'
__pycache__/
*.pyc
.env
settings.json
.devices.json
context/
IGN

cd "$STAGE"
git init -q
git add -A
git -c user.name="$USER" -c user.email="$USER@users.noreply.huggingface.co" \
    commit -q -m "class-copilot server"
git branch -M main

echo
echo "pushing to https://huggingface.co/spaces/$USER/$SPACE"
echo "when asked, the username is '$USER' and the password is an ACCESS TOKEN"
echo "from https://huggingface.co/settings/tokens (needs 'write' permission)"
echo
git remote add origin "https://huggingface.co/spaces/$USER/$SPACE"
git push -u origin main --force

echo
echo "done. it will build for a few minutes, then be live at:"
echo "   https://$USER-$SPACE.hf.space"
