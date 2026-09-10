# Runs the server somewhere hosted, so the phone works with your laptop off.
#
# No faster-whisper, no audio device libraries: a hosted box has no microphone
# and little CPU, so the phone app is the microphone and transcription goes to
# your provider's Whisper. That keeps this small enough for a free tier.
FROM python:3.11-slim

# A hosted server listens to nothing itself, and must not try to load a local
# speech model. Both are read by app/config.py.
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    CC_AUDIO_MODE=phone \
    CC_STT_ENGINE=cloud \
    HOME=/app

# Which AI answers. config.toml defaults to Claude, but your saved choice lives
# in settings.json, which is deliberately never deployed -- so state it here.
# Override either as a Space variable to use a different provider.
ENV CC_LLM_PROVIDER=groq \
    CC_LLM_MODEL=openai/gpt-oss-120b

WORKDIR /app

COPY requirements-server.txt ./
RUN pip install --no-cache-dir -r requirements-server.txt

COPY app/ ./app/
COPY config.toml ./config.toml

# Hugging Face Spaces runs the container as a non-root user, so anything the
# app writes (settings.json, .devices.json, context/) has to be writable.
RUN mkdir -p /app/context && chmod -R 777 /app

EXPOSE 7860
CMD ["python", "-m", "app.main", "--no-browser"]
