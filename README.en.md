# alldayrec（全天录）

[中文](README.md) | **English**

**alldayrec is a self-hosted, all-day recorder and personal memory layer: wear a recorder (or just carry your phone) from morning to night, and your own server turns the audio into speech-to-text transcripts with speaker diarization, conversation summaries, to-dos and a daily recap, within a minute.** It keeps the original audio, so every line can be played back and checked, and old days can be transcribed again by a better model later. It is device-agnostic (the iPhone and Android app, or an open-source BLE wearable you can build from a few parts), and it speaks MCP, so your own agent can search your days. Think of it as a lifelog you host yourself: an open-source alternative to Limitless, Plaud, Bee or Omi, without a subscription and without your conversations living on someone else's cloud.


"LR" / "lr" is the project's internal codename, still used in the hardware and the code: recorder names like `LR-P3`, the firmware tool `lr.sh`, the CLI `lr.py`.

The git history was rewritten before publishing to remove the author's private deployment details, so it is short on purpose, and references like `lr#123` in commit messages point at issues of the earlier private repository, not this one.

## Screenshots

The web UI on a phone, filled with made-up data (the UI is in Chinese for now).

| ![Today](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/today-20261024.png) | ![Conversations](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversations-20261024.png) | ![One conversation](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversation-20261024.png) | ![Transcript](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/transcript-20261024.png) |
|:--:|:--:|:--:|:--:|
| Today: due to-dos, yesterday's recap, the live analysis of the conversation going on, today's conversations, and a box to ask about today | Conversations: titles, summaries, tags, due to-dos | One conversation's summary: to-dos, decisions, per-person points | Transcript with speakers: tap a line to play from there; the line playing is highlighted |
| ![Live page](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-20261024.png) | ![Ask AI](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/ask-20261024.png) | ![Daily recap](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/recap-20261024.png) | ![Me](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/me-20261024.png) |
| Live page: the live analysis of the conversation going on, drafts as words come in | Ask AI: an answer with links to the conversations it came from | Daily recap: highlights, your to-dos, the people you talked to | Me: each recorder's battery, free card space and lag, plus links to connect a phone, see progress and hook up AI agents |

Live captions while the page is open (the draft layer), and the conversations page on a desktop:

![Live captions](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-20261024.gif)

![Conversations on a desktop](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversations-desktop-20261024.png)

## What it does

- **Records all day.** The phone app uploads short Opus pieces; a BLE recorder writes everything to its SD card and streams it to the phone, which fills gaps after a disconnection. Nothing is lost while offline: pieces queue on the device and go up later.
- **Takes any audio source.** Anything that can POST audio pieces can feed the server: `POST /api/chunks?device=<name>&start=<unix ms of its first sample>`, `Authorization: Bearer <token>`, the body one self-contained audio file (`Content-Type: audio/ogg` for Ogg Opus as the apps send, or `audio/webm`, `audio/aac`). See `chunk()` in `server/server.py`; `firmware/devkit/lr_sync.py` is a small Python client.
- **Transcribes in two layers.** The final layer cuts the audio in pauses and sends pieces of up to 5 minutes to a cloud ASR engine (default: Alibaba Cloud Bailian Qwen, about ¥0.05 per hour of audio); while a page is open, a draft layer shows words within seconds.
- **Knows who spoke.** Speech detection (FireRedVAD) and voiceprints (ReDimNet2) run locally on onnxruntime, no GPU: it learns your own voice, keeps speakers apart within a conversation and remembers the names you give them.
- **Remembers.** Conversations are closed after a silence and summarized by an LLM of your choice; to-dos with due dates, a daily recap, full-text search and "ask AI" over your history. A conversation with under 60 s of speech gets its first sentence as the title and no summary or to-dos (`MIN_SPEECH_S` in `server/summary.py`).
- **Opens up.** A web UI, a remote MCP endpoint (`POST /mcp`) for Claude Code, Codex, Cursor and other agents, a small CLI (`cli/lr.py`), and SQLite storage you can read yourself.
- **Stays yours.** One server, your disk, several people per server if you like (one token each). Audio goes only to the ASR engine you pick, transcripts only to the LLM you pick.

```
BLE recorder ──BLE──▶ phone app ──HTTPS──▶ server (FastAPI) ──▶ web UI, MCP, CLI
                      (or the phone's mic)    │ local: VAD, voiceprints (onnxruntime)
                                              │ cloud: ASR engine, OpenAI-compatible LLM
                                              ▼
                                       SQLite + original audio
```

| Directory | What |
|---|---|
| `server/` | The server: upload queue, VAD and voiceprints, ASR engines, conversations, summaries, MCP (Python 3.11, uv) |
| `web/` | The web UI: live transcript, conversations, speakers, calendar, device pages (Vue, pnpm) |
| `app/` | The phone app for iOS and Android: records the mic or the BLE recorder, uploads, firmware updates (Flutter) |
| `firmware/devkit/` | The BLE recorder firmware for nRF52840 boards (Zephyr / nRF Connect SDK), based on the Omi devkit |
| `firmware/selftest/`, `firmware/blerelay/` | Board self-test firmware; an ESP32 BLE relay used for testing |
| `cli/` | `lr.py`: read your conversations from a terminal or an agent |

## Quickstart (Docker)

You need Docker on an amd64 or arm64 machine and an [Alibaba Cloud Bailian (DashScope)](https://bailian.console.aliyun.com/) API key, Beijing region. One key runs the ASR, and it can run the LLM too (`qwen-plus` in `.env.example`), as long as the key is not restricted to specific models in the Bailian console: a model-restricted key gets 403 on the chat models.
No ASR key: `ENGINE=local` runs the speech-to-text in the server (sherpa-onnx), text only, with speakers from the built-in voiceprints and worse on far-field audio; the LLM key is still required.

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
cp .env.example .env    # fill in DASHSCOPE_API_KEY and LLM_API_KEY (the same key works with the example LLM settings)
mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
docker compose up -d
docker compose logs -f  # the first start downloads the model files (about 55 MB), sha256-checked
```

With `ENGINE=local` the server downloads another ~136 MB (the sherpa-onnx model tarball) when the first audio arrives, so the first transcript takes about a minute.

The container runs in UTC unless `.env` sets `TZ` (`.env.example` has `TZ=Asia/Shanghai`): conversations are dated, and `RECAP_AT` fires, in that time zone.

A prebuilt image is published to `ghcr.io/suyiiyii/alldayrec` (amd64/arm64), and `docker-compose.yml` uses it. To build it locally instead, uncomment `build: .` in `docker-compose.yml` and run `docker compose up -d --build`.

Open `http://127.0.0.1:8770/?token=<your token>` (the token is in `data/tenants.json`; on a remote machine: `ssh -L 8770:127.0.0.1:8770 host` first); a cookie keeps you signed in. Everything lives in `./data`: `tenants.json`, the model files, and per person a SQLite database and the original audio.

Phones need HTTPS: put a reverse proxy with TLS in front of port 8770 (Caddy, nginx, a Cloudflare Tunnel, Tailscale Serve); `GET /api/health` answers `{"ok": true}` without a token, for its health checks. The compose file publishes the port on 127.0.0.1 only; don't expose a server whose token is empty.

## Quickstart (without Docker)

Python 3.11 with [uv](https://docs.astral.sh/uv/), ffmpeg, Node 22 with pnpm.

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
cp .env.example .env    # fill in the keys, and MODELS_DIR=<an absolute, writable directory>
(cd web && pnpm install && pnpm build)    # -> server/static/dist
echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "'$PWD/data/main'", "recordings": "'$PWD/data/main/recordings'"}]' > tenants.json && chmod 600 tenants.json
cd server && uv sync && set -a && . ../.env && set +a && uv run python server.py
```

`tenants.json` lists who the server serves: one entry per person, each with its own token and its own data and recordings directories (`server/tenant.py`). A single entry with an empty token runs without auth: keep such a server on 127.0.0.1.

### Model files

Two models run on the server, on CPU: [FireRedVAD](https://github.com/FireRedTeam/FireRedVAD) (speech detection, Apache-2.0) and [ReDimNet2](https://github.com/PalabraAI/redimnet2) B6 (voiceprints, MIT). You don't need to do anything: at the first start the server downloads the missing files into `MODELS_DIR` (`data/models` in Docker), FireRedVAD from its upstream repository and ReDimNet2 from this repository's `models-v1` release (with `ENGINE=local` also the sherpa-onnx model tarball, at its first use), and accepts only the exact files whose sha256 is pinned in `server/infer.py` and `server/local_asr.py`. A file that is already there is never downloaded again.

If a download fails or a file is refused, delete it and restart, or put the same file from the same URL into `MODELS_DIR` yourself. `server/export_onnx.py` is the maintainer's way to make a new release file from the upstream weights, not a way to get these: its ReDimNet2 export differs byte for byte between machines, so the server refuses one you export yourself.

Note on the voiceprint model: ReDimNet2's code and weights are MIT, but its training data includes VoxBlink2, licensed CC BY-NC-SA 4.0. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and decide whether that fits your use.

## Configuration

All settings are environment variables; [`.env.example`](.env.example) lists every one with a comment. The required ones:

| Setting | What |
|---|---|
| `ENGINE` | The ASR engine of the final transcript. `qwen` (default) needs only `DASHSCOPE_API_KEY` |
| `DASHSCOPE_API_KEY` | Alibaba Cloud Bailian key, Beijing region |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | Any OpenAI-compatible `/chat/completions` endpoint with tool calls and JSON mode: summaries, to-dos, "ask AI", the recap |

ASR engines:

| `ENGINE` | Provider | Needs | Speakers |
|---|---|---|---|
| `qwen` | Alibaba Cloud Bailian, Qwen-Audio file transcription | `DASHSCOPE_API_KEY` | from the engine, refined by local voiceprints |
| `volc` | Volcengine Doubao Seed-ASR 2.0 | `VOLC_ASR_KEY` | local voiceprints |
| `stepfun-sse` | StepFun streaming ASR | `STEPFUN_API_KEY` | local voiceprints |
| `stepfun`, `stepfun-file` | StepFun file mode | `STEPFUN_API_KEY` plus your own upload bridge and file-mode ASR service (`ASR_*`) | from the engine, refined by local voiceprints |
| `local` | sherpa-onnx X-ASR zh-en in the server process, text only; worse than `qwen` on far-field audio | nothing (the model, ~136 MB, is downloaded at its first use) | local voiceprints |

`DRAFT_ENGINE=qwen-stream` streams the audio to Qwen while a page is open, so words show as they are said. `ENGINE_FALLBACK` takes the windows the main engine fails on.

The LLM is always an external service: the project doesn't bundle or run one. To keep everything on your own hardware, point `LLM_BASE_URL` at a local OpenAI-compatible server yourself (vLLM, llama.cpp, Ollama); the model must handle tool calls and JSON output.

## Phone app

- **Android:** build it with `cd app && flutter build apk --release`, or install the APK from the [Releases page](https://github.com/suyiiyii/alldayrec/releases) (`apk-v*` tags). The server can also offer updates at `/app.apk`.
- **iOS:** there is no App Store build; you build and install it yourself from Xcode. Copy `app/ios/Flutter/Signing.local.xcconfig.example` to `Signing.local.xcconfig` and put in your Team ID (and a bundle id suffix of your own). With a free Apple ID, an app you install this way stops opening after 7 days and has to be installed again; a paid Apple Developer account removes that limit.

Connect the app: in the web UI open **连接手机** (`/connect`) and scan its QR code from the app's settings (**扫码登录**), or type the server address and token by hand. The app's UI is in Chinese for now.

## Hardware (optional)

The phone alone is enough. For all-day wear there is an open BLE recorder: an nRF52840 board, a PDM microphone, a microSD card and a small battery. It records to the card all the time and syncs to the phone whenever it's connected, so nothing is lost when the phone is away.

| Model | Board |
|---|---|
| `LR-X1` | Seeed XIAO nRF52840 Sense (built-in mic) + SD module + battery |
| `LR-P3` | ProMicro nRF52840 (nice!nano compatible) + PDM mic + SD module + switch, in a printed case |

Firmware, wiring, build (nRF Connect SDK v2.7.0) and over-the-air updates: [`firmware/devkit/README.md`](firmware/devkit/README.md). New boards can be checked with [`firmware/selftest`](firmware/selftest). LR-P3's parts, wiring, soldering order and printed case (STL, STEP and source) are in [`hardware/lr-p3/README.md`](hardware/lr-p3/README.md) (Chinese, with an English summary). The firmware started from the [Omi](https://github.com/BasedHardware/omi) devkit.

## Privacy and the law

You are recording other people. Tell them, ask for consent where it is needed, and follow the law where you live; some places require every party's consent. All data stays on your server; the audio goes to the ASR provider you choose and transcripts go to the LLM you choose, under their terms.

## Development

```bash
# server/
uv run --with pytest pytest -q
# web/
pnpm install --frozen-lockfile && pnpm build
for f in *.check.ts; do node --experimental-strip-types "$f" || exit 1; done
# app/ (use the pub host recorded in pubspec.lock)
PUB_HOSTED_URL=https://pub.flutter-io.cn flutter pub get --enforce-lockfile
flutter analyze --no-pub && flutter test --no-pub -j 4
# Android JVM and Swift checks: .github/workflows/app.yml; firmware host checks: .github/workflows/firmware-check.yml
```

Code boundaries, the CI workflows, release builds and their secrets: [CONTRIBUTING.md](CONTRIBUTING.md).

## Deploying with deploy.sh (maintainers)

Besides Docker, the server can run as systemd units on a Linux host, deployed over ssh from a clean checkout of `origin/main`. `server/deploy.sh` builds the web app, copies the release, backs up each instance's code and every tenant's `live.db`, installs it (`uv sync`), checks that the model files are there, restarts each instance and checks it; `server/deploy.sh check` only reads what runs, `server/deploy.sh rollback` puts back what the last deploy replaced.

```bash
export LR_DEPLOY_HOST=user@host                  # required: the ssh / scp target (your ssh config and agent)
export LR_DEPLOY_SHELL='sudo bash -s'            # default `bash -s`: the remote shell the script is piped into, as root
export LR_INSTANCES='/opt/alldayrec:alldayrec'   # the default: <dir>:<systemd unit> pairs, space separated, test first
server/deploy.sh                                 # or: server/deploy.sh check | rollback
server/apk_release.sh app/build/app/outputs/flutter-apk/app-release.apk   # the APK phones are offered at /app.apk
server/tenants.sh add <id>                       # a new tenant (its own token and dirs) of the last instance
```

Each instance is a directory with its `.env` and `tenants.json`, run by its systemd unit. scp puts the release and the APK in the login's home on the host; when the remote shell's `$HOME` is another one, `LR_TGZ` and `LR_APK` give their paths there. The details are in the comments at the top of each script.

## License

MIT, see [LICENSE](LICENSE). Bundled third-party code (the Omi devkit, Opus, FireRedVAD's post-processor and others) keeps its own license: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
