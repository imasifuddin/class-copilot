# class-copilot

A silent teaching assistant for live classes.

Your **laptop** listens to the class, notices when someone asks a doubt, and
writes an answer. Your **phone**, lying next to you, shows that answer. It never
speaks — you read it and explain in your own words.

Nothing appears on the screen you are sharing. Nothing is sent to Zoom or Meet.

```
   the class (Zoom, Meet, or a room)
            |
            |  audio
            v
   +----------------------+
   |  laptop              |  transcribes  ->  spots the doubt  ->  asks the AI
   |  class-copilot       |
   +----------------------+
            |  your wifi
            v
   +----------------------+
   |  phone in your hand  |  the answer appears here, silently
   +----------------------+
```

Answers are written as **the exact words to say** — plain spoken English, no
bullet points, no markdown — so you can read them straight out.

---

## Prerequisites

| You need | Why | Notes |
|---|---|---|
| **Windows 10 or 11** | audio capture uses WASAPI | the phone-microphone mode works anywhere |
| **Python 3.11+** | the server | [python.org](https://www.python.org/downloads/) — tick *Add to PATH* |
| **An AI API key** | writing the answers | free options below |
| A phone on the same wifi | to read the answers | Android for the app; any phone for the browser |

Optional:

| For | You need |
|---|---|
| Building the Android app yourself | JDK 17+, Android SDK, Gradle 8.7 |
| Offline transcription | nothing — a model downloads on first run |

**No GPU required.** Everything runs on a normal CPU.

---

## Getting an API key

You need exactly one. These all have a free tier and none need a credit card.

| Provider | Where | Notes |
|---|---|---|
| **Groq** | [console.groq.com](https://console.groq.com) | Recommended. Fast, free, and does the speech-to-text too. |
| Google Gemini | [aistudio.google.com](https://aistudio.google.com) | Free tier, good quality. |
| OpenAI | [platform.openai.com](https://platform.openai.com) | Paid. |
| Anthropic | [console.anthropic.com](https://console.anthropic.com) | Paid, best answers. |
| Ollama | [ollama.com](https://ollama.com) | Free and fully offline, runs on your own machine. |

> A **Claude.ai or ChatGPT subscription is not an API key.** Those are separate
> products. The key must come from the developer console.

---

## Install

```bash
git clone https://github.com/<you>/class-copilot.git
cd class-copilot
cp .env.example .env
```

Open `.env` and uncomment **one** line, replacing the placeholder with your key:

```ini
# Groq (recommended)
GROQ_API_KEY=gsk_your_real_key_here

# or Google Gemini
# GEMINI_API_KEY=...

# or Claude
# ANTHROPIC_API_KEY=sk-ant-...

# or OpenAI
# OPENAI_API_KEY=sk-...
```

Then pick the provider in `config.toml`:

```toml
[llm]
provider = "groq"
model = "openai/gpt-oss-120b"
```

You can also set all of this later from the phone, without editing any file.

**Start it:**

```bash
./run.sh            # Git Bash
.\run.ps1           # PowerShell
```

The first run creates a virtual environment and installs dependencies (a few
minutes), then downloads a speech model (~150 MB). After that startup takes
seconds.

---

## First run

The terminal prints everything you need:

```
==============================================================
  class-copilot is running
==============================================================
  on this laptop :  http://127.0.0.1:8756
  on your phone  :  http://192.168.1.20:8756
  pairing PIN    :  483920
  [ a QR code ]
==============================================================
```

- **On the laptop** a browser tab opens by itself. Type a question in the box at
  the bottom to check your key works.
- **On the phone** scan the QR code or type the address, then enter the PIN once.

If Windows shows a firewall prompt, click **Allow** and tick **Private
networks** — otherwise your phone cannot reach the laptop.

### Check it before a real class

```bash
./check.sh          # records 6s, transcribes it, asks the model one question
./gate.sh           # 28 realistic classroom lines: does it answer the right ones?
./devices.sh        # lists your audio devices
```

`check.sh` tells you which of the three stages is broken instead of leaving you
guessing at a blank screen.

---

## How it hears the class

Set `[audio] mode` in `config.toml`, or pass `--mode` on the command line.

| Mode | Use when | Hears |
|---|---|---|
| `both` *(default)* | unsure | speakers **and** the room microphone |
| `loopback` | the call runs on this laptop | only what your speakers play — the students, never you. Works with headphones. |
| `mic` | the call runs on another device nearby | only the room |
| `phone` | the class is on a **different computer** | nothing locally; the Android app streams its microphone over wifi |

```bash
./run.sh --mode loopback
```

> **Android cannot capture Zoom or Meet audio from the phone itself.** The OS
> refuses to record apps using `USAGE_VOICE_COMMUNICATION`, and a second app
> grabbing the microphone during a call gets silence. That is why the laptop
> listens. In `phone` mode the phone's microphone is free, because the call is
> happening elsewhere.

---

## Using it in class

Two panes on a laptop, two tabs on a phone.

- **Heard** — every line it understood. Question-like lines are highlighted.
  **Tap any line to answer it**, so nothing is ever really missed.
- **Answers** — one card per question, streaming in as it is written.

Top bar: `auto`/`manual`, `pause`, `stop`, **◑** theme, **⛶** fullscreen,
**⚙** settings.

**Tap anywhere on an answer** to hide the header and text box — the reading area
grows by about 45%. Tap again to bring them back.

The Android app adds a **Tap to listen** button. It starts **muted**. Tap when a
doubt begins, tap again when it ends. Pauses in between are fine — nothing is
answered until you stop, and everything said becomes one question.

---

## Making the answers yours

Both settings live behind **⚙**.

**Answer style**

| Style | What you get |
|---|---|
| `speak` *(default)* | The exact words to say. 60–120 words, no bullets, no markdown. |
| `reference` | A one-line answer, bullets, code, and a `Say:` line to paraphrase. |
| `both` | The script first, then the detail. |

**Your material** — add notes, a syllabus, coding conventions, a profile. PDF,
Word, text, markdown, or pasted text. Answers then use *your* terminology and
*your* examples:

> *without:* "Think of it like a dictionary you look up by a name…"
>
> *with:* "…like tracking each user's staked tokens in **our TokenVault
> contract**. When a user stakes, you just write `stakes[user] = amount`…"

Large collections are narrowed to the passages matching the question, so
prompts stay small and fast.

---

## How it decides what to answer

It waits for the speaker to actually finish, the way a voice assistant does.

- Whisper produces fragments. Someone pausing to think produces *"I have a
  doubt"* then *"what's the difference between a hash map and an array"*.
- Those are collected into one **turn**, closed only after `end_of_turn_s`
  (1.6 s) of quiet. Everything said becomes **one** question with **one**
  answer. Nothing is dropped.
- Then it decides whether to answer at all. Obvious question wording matches
  instantly; anything else is put to the model as a one-word judgement, so
  *"my loop keeps running forever and I can't see why"* — no question mark, no
  "doubt" — still gets answered.
- Ordinary chatter is ignored: *"can you hear me"*, *"when is the next class"*,
  *"yes sir understood"*. Measured at **28/28** on a realistic set (`./gate.sh`).
- A doubt raised while another answer is streaming is **queued**, never dropped.

---

## Speech to text

| Engine | Where | Notes |
|---|---|---|
| `local` *(default)* | your CPU | free, offline, nothing leaves the machine |
| `cloud` | your AI provider | more accurate on noisy room audio, needs no CPU |

Measured on 11 s of clean speech, all word-perfect:

| Model | Time |
|---|---|
| `tiny.en` | 0.5 s |
| `base.en` *(default)* | 1.0 s |
| `small.en` | 4.1 s |
| Groq `whisper-large-v3-turbo` (cloud) | 0.75 s |

Room audio is harder than clean speech. If it mishears, try the cloud engine —
`whisper-large-v3` is a much stronger model than `base.en`.

---

## The Android app

The APK is a window onto the page your laptop serves. You do **not** need it — a
browser works — but it adds a real app icon, no address bar, a screen that stays
awake, and the microphone, which browsers block on plain `http://`.

**Build it:**

```bash
.\build-apk.ps1              # needs JDK 17+, Android SDK, Gradle 8.7
.\build-apk.ps1 -Install     # and push it to a USB-connected phone
```

The result is `android/app/build/outputs/apk/debug/app-debug.apk`. Copy it to
your phone and open it; you will have to allow "install from unknown sources".

**When does it need reinstalling?**

| Changed | Reinstall |
|---|---|
| Python, prompts, speech engine, settings | no |
| The page: layout, themes, buttons, settings screen | no |
| `config.toml` | no |
| Anything under `android/` — pairing screen, mic service, icon | **yes** |

`./run.sh` prints the answer at startup; `./apk-status.sh` tells you on demand.

---

## Security

The server listens on your whole network so the phone can reach it, so phones
must pair with a 6-digit PIN. The laptop itself is exempt. API keys live in
`.env` or `settings.json` on your machine and are never sent to the browser —
the settings screen only ever shows a masked hint.

| Flag | Effect |
|---|---|
| `--local-only` | do not listen on the network at all |
| `--no-pin` | drop the PIN (only on a network you trust) |
| `--forget-devices` | unpair every phone |
| `--reset-settings` | discard `settings.json`, go back to `config.toml` |

Set `CC_PIN=123456` to fix the PIN instead of a random one each start — useful
when the server runs somewhere you cannot read the terminal.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Phone cannot connect | Windows firewall. Admin PowerShell: `New-NetFirewallRule -DisplayName "class-copilot" -Direction Inbound -LocalPort 8756 -Protocol TCP -Action Allow -Profile Private` |
| Still cannot connect | Campus wifi blocking device-to-device — use the laptop's hotspot |
| Nothing under **Heard** | Watch the blue level bar. If it never moves, no sound is reaching the laptop |
| Browser says it cannot send audio | Correct — install the APK. Browsers block the microphone on plain `http://` |
| Answers are generic | Add your material under **⚙ → Your material** |
| It mishears a lot | ⚙ → Speech engine → *In the cloud*, or a bigger local model |
| It answers things that were not questions | ⚙ → Answer mode → `manual`, or raise the cooldown |
| It answers before the student finishes | Raise `[trigger] end_of_turn_s` |
| `429` errors | Provider rate limit reached; switch provider in ⚙ |
| A saved setting broke something | `./run.sh --reset-settings` |

---

## How it works

```
speakers / mic / phone  ->  16 kHz mono
                        ->  energy-based utterance segmentation
                        ->  Whisper (local or cloud)
                        ->  turn assembly (wait for them to finish)
                        ->  is this a doubt?  (keywords, else a one-word model call)
                        ->  the AI, streamed
                        ->  websocket  ->  laptop and phone
```

| File | Role |
|---|---|
| `app/audio.py` | Loopback / microphone capture, resampling |
| `app/remote_audio.py` | Audio streamed in from the phone |
| `app/segmenter.py` | Splits the stream into utterances |
| `app/stt.py` | Speech to text, local or cloud |
| `app/trigger.py` | Keyword-level doubt detection |
| `app/prompts.py` | Answer styles |
| `app/context_store.py` | Your uploaded material, and picking what is relevant |
| `app/llm.py`, `llm_anthropic.py`, `llm_openai.py` | Providers |
| `app/hub.py` | Turn assembly, answering, queueing |
| `app/server.py` | FastAPI, websocket, settings and pairing API |
| `app/web/index.html` | The whole interface |
| `android/` | The APK: a WebView shell with pairing, microphone and keep-awake |

---

## One thing worth knowing

Recording a call is regulated in some places, and many institutions have their
own policy. Nothing is written to disk — the transcript lives in memory and dies
with the process — but audio passes through a speech model and the questions go
to whichever AI provider you choose. Worth a one-line mention to your students
or your institution if that applies to you.

---

## Licence

MIT. Use it, change it, share it.
