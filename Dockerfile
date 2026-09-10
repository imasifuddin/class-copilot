# Hosting this somewhere free.
#
# The image deliberately does NOT install faster-whisper: a hosted box has no
# microphone and little CPU, so transcription goes to the provider's Whisper
# instead ([stt] engine = "cloud"). That keeps the image small and lets it run
# in a few hundred megabytes of RAM.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860

WORKDIR /app

# Only what a cloud-transcribing server needs -- no audio device libraries.
RUN pip install --no-cache-dir \
        "fastapi>=0.110" "uvicorn[standard]>=0.29" \
        "openai>=1.30" "anthropic>=0.40" \
        "numpy>=1.24" "python-dotenv>=1.0" \
        "qrcode>=7.4" "pypdf>=4.0" "python-docx>=1.1"

COPY app/ ./app/
COPY config.toml ./config.toml

# A hosted server listens to nothing itself; the phone app is the microphone.
RUN python - <<'PY'
import pathlib, re
p = pathlib.Path("config.toml")
s = p.read_text(encoding="utf-8")
s = re.sub(r'(?m)^mode = "(loopback|mic|both|phone)"', 'mode = "phone"', s, count=1)
s = re.sub(r'(?m)^engine = "(local|cloud)"', 'engine = "cloud"', s, count=1)
p.write_text(s, encoding="utf-8")
print("container config: audio=phone, speech=cloud")
PY

EXPOSE 7860
CMD ["python", "-m", "app.main", "--no-browser"]
