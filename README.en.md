<div align="center">

# alldayrec（全天录）

### The open-source Plaud & Limitless you can self-host and truly own.

Wear it all day: what you say becomes transcripts, summaries and to-dos, with the original audio and all the data on your own server.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/suyiiyii/alldayrec?filter=v*&label=release)](https://github.com/suyiiyii/alldayrec/releases)
[![Android APK](https://img.shields.io/github/v/release/suyiiyii/alldayrec?filter=apk-v*&label=Android%20APK)](https://github.com/suyiiyii/alldayrec/releases)
[![Docker image](https://img.shields.io/badge/ghcr.io-alldayrec-2496ed?logo=docker&logoColor=white)](https://github.com/suyiiyii/alldayrec/pkgs/container/alldayrec)
[![GitHub stars](https://img.shields.io/github/stars/suyiiyii/alldayrec?style=flat)](https://github.com/suyiiyii/alldayrec)

[中文](README.md) | **English**

[Quickstart](#quickstart-docker) · [How it compares](#how-it-compares) · [Import recordings](#importing-recordings-you-already-have) · [Hardware](hardware/lr-p3/README.md) · [Roadmap](#roadmap)

`#self-hosted` `#lifelog` `#speaker-diarization` `#MCP` `#plaud-alternative` `#open-hardware`

</div>

alldayrec is a self-hosted, all-day recorder and personal memory layer. Wear a recorder (or just carry your phone) from morning to night, and your own server turns the audio into speech-to-text transcripts with speaker diarization, conversation summaries, to-dos and a daily recap, within a minute. The original audio is kept, so every line can be played back and checked, and your own agent can search your days over MCP. Think of it as a lifelog you host yourself: an open-source alternative to Plaud, Limitless, Bee or Omi, without a subscription and without your conversations living on someone else's cloud. (Product names belong to their owners; this project is not affiliated with any of them.)

<div align="center">

![Live captions](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-20261024.gif)

Live captions while the page is open (the draft layer). Below, the web UI on a phone, filled with made-up data (the UI is in Chinese for now).

</div>

| ![Today](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/today-20261024.png) | ![Conversations](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversations-20261024.png) | ![One conversation](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversation-20261024.png) | ![Transcript](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/transcript-20261024.png) |
|:--:|:--:|:--:|:--:|
| Today: due to-dos, yesterday's recap, the conversation going on, today's conversations, and a box to ask about today | Conversations: titles, summaries, tags, due to-dos | One conversation's summary: to-dos, decisions, per-person points | Transcript with speakers: tap a line to play from there; the line playing is highlighted |
| ![Live page](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-20261024.png) | ![Ask AI](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/ask-20261024.png) | ![Daily recap](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/recap-20261024.png) | ![Me](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/me-20261024.png) |
| Live page: the live analysis of the conversation going on, drafts as words come in | Ask AI: an answer with links to the conversations it came from | Daily recap: highlights, your to-dos, the people you talked to | Me: each recorder's battery, free card space and lag, plus links to connect a phone, see progress and hook up AI agents |

The conversations page on a desktop:

![Conversations on a desktop](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversations-desktop-20261024.png)

## Why

- **The original audio stays.** OPPO's 心力球 and Bee delete the audio once it is transcribed, so a wrong transcript has nothing to be checked against. Here every line plays back, and old days can be transcribed again when a better model comes along.
- **Your data lives on your server.** One machine, one SQLite database and the original recordings. Audio goes only to the ASR service you pick and transcripts only to the LLM you pick; there is no account to sign up for.
- **No subscription.** The software is free (MIT). The default Qwen file transcription costs about ¥0.05 per hour of audio, paid to the cloud provider by use, with no monthly minute cap. The LLM costs whatever the service you pick charges.
- **Any device, open hardware.** The iPhone and Android app records the phone's mic; for all-day wear, solder a BLE recorder (`LR-P3`) from a few off-the-shelf modules, with the parts list, wiring diagram and 3D-printed case in this repository.
- **Your agent can search your days.** A remote MCP endpoint for Claude Code, Codex and Cursor, a CLI, and SQLite storage you can read yourself.

## How it compares

| | **alldayrec** | Anker × Feishu AI recorder (录音豆) | OPPO AI 心力球 | DingTalk A1 | Plaud | Limitless / Bee | Omi |
|---|---|---|---|---|---|---|---|
| **Price / subscription** | Free software; ASR paid by use, Qwen about ¥0.05/h; LLM extra | From ¥899; 6 months of AI credits, then ¥69/month membership[^lrd] | ¥499 trial price, by invitation; OPPO, OnePlus and realme phones only[^xlq][^xlq2] | ¥499 / ¥799[^a1c]; membership ¥599 or ¥1499/year[^a1] | NotePin S $179[^plaud-dev]; free 300 min/month, Pro $17.99/month[^plaud-plan] | Limitless bought by Meta, no longer sold[^limitless]; Bee $74.99 + Pro $11.99/month, US only[^bee] | Pendant $179 ($129 on offer); free 300 cloud min/month, Pro $269/year[^omi-price] |
| **All-day wear** | Yes: the phone app, or a home-built recorder that writes to its card | 8 h per charge, 32 h with the case[^lrd] | 24 h of continuous recording[^xlq] | A card on the back of the phone, 45 h of recording[^a1] | NotePin S about 20 h[^plaud-dev] | Bee wristband, 7 days[^bee] | Pendant |
| **Keeps the original audio** | Yes: every line plays back and can be transcribed again | Deleted from the device after upload, kept in the cloud; download only one by one on the Minutes web page[^lrd] | No: "text only, no original audio"[^xlq] | Unknown | Yes, exports MP3 / WAV[^plaud-exp] | Bee: no, deleted after processing[^bee] | Optional, off by default[^omi-audio] |
| **Where the data lives** | Your own server | Feishu cloud[^lrd] | OPPO's PCC private cloud[^xlq] | 64 GB on the device + DingTalk cloud[^a1] | Plaud cloud[^plaud-plan] | Bee's cloud (Bee Private Compute)[^bee] | Omi cloud, or your own backend[^omi-docs] |
| **Speaker recognition** | Local voiceprints: knows your voice, remembers the names you give | Voiceprints tell speakers apart[^lrd2] | Voiceprint tells the wearer apart[^xlq] | Names after a voiceprint is registered[^a1b] | Speakers, renamable with find & replace[^plaud-rn] | Unknown | Speaker identification[^omi-docs] |
| **Summaries / to-dos / recap** | Summaries, to-dos with due dates, decisions, daily recap | Minutes, to-dos[^lrd2] | Daily summary, to-dos, projects across days[^xlq] | Minutes, chapters, analysis and follow-ups[^a1c] | Template summaries, to-dos[^plaud-app] | Bee summaries (unlimited with Pro)[^bee] | Summaries, action items, daily recaps[^omi-home] |
| **Ask AI / MCP** | Ask AI with sources; remote MCP | Ask across minutes[^lrd2]; MCP unknown | Ask in WeChat or Feishu; MCP[^xlq] | Unknown | Ask Plaud; MCP[^plaud-mcp] | Unknown | Chat; MCP[^omi-mcp] |
| **Open hardware** | Yes (`hardware/lr-p3/`) | No | No | No | No | No | Yes[^omi-docs] |
| **Open source** | Yes (MIT: server, web, app, firmware) | No | No | No | No | No | Yes (MIT)[^omi-docs] |

"No" means no published design files or source code; "Unknown" means we found no reliable source. Prices and features are as of 2026-10-24; check each vendor's own site. Product names and trademarks belong to their owners; this project is not affiliated with any of them.

## What it does

- **Records all day.** The phone app uploads short Opus pieces; a BLE recorder writes everything to its SD card and streams it to the phone, which fills gaps after a disconnection. Nothing is lost while offline: pieces queue on the device and go up later.
- **Takes any audio source.** Anything that can POST audio can feed the server (`POST /api/chunks`), recordings you already have too: see [importing](#importing-recordings-you-already-have).
- **Transcribes in two layers.** The final layer cuts the audio in pauses and sends pieces of up to 5 minutes to a cloud ASR engine (default: Alibaba Cloud Bailian Qwen, about ¥0.05 per hour of audio); while a page is open, a draft layer shows words within seconds. Without an ASR key, `ENGINE=local` transcribes on your machine ([compared with the cloud engines](docs/asr-benchmark.md)).
- **Knows who spoke.** Speech detection (FireRedVAD) and voiceprints (ReDimNet2) run locally on onnxruntime, no GPU: it learns your own voice, keeps speakers apart within a conversation and remembers the names you give them.
- **Remembers.** Conversations are closed after a silence and summarized by an LLM of your choice; to-dos with due dates, a daily recap, full-text search and "ask AI" over your history, with links to the conversations an answer came from. A conversation with under 60 s of speech gets its first sentence as the title and no summary or to-dos (`MIN_SPEECH_S` in `server/summary.py`).
- **Opens up.** A web UI, a remote MCP endpoint (`POST /mcp`) for Claude Code, Codex, Cursor and other agents, a small CLI (`cli/lr.py`), and SQLite storage you can read yourself.
- **Stays yours.** One server, your disk, several people per server if you like (one token each).

### Importing recordings you already have

The app and the web UI have no import button yet; importing goes through `POST /api/chunks`: one file per request, the body a whole audio file, decoded by ffmpeg on the server.

| What you have | Can it be imported | How |
|---|---|---|
| Plaud | Yes | Export MP3 or WAV from the app[^plaud-exp], then upload it with the command below |
| A voice recorder or phone recordings (MP3, WAV) | Yes | Upload as is |
| `.m4a` (e.g. iPhone Voice Memos) | Yes, after converting | A long `.m4a` can't be decoded from a pipe; run `ffmpeg -i in.m4a out.mp3` first |
| Anker × Feishu 录音豆 | Tedious | The audio downloads only one recording at a time from the Feishu Minutes web page[^lrd]; then as above |
| OPPO 心力球 | No | It keeps no audio[^xlq] |

```bash
# start: when the recording began (unix ms); it sets the day and time of its conversations. `date -d` is GNU date
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: audio/mpeg" --data-binary @rec.mp3 \
  "https://<your server>/api/chunks?device=import&start=$(date -d '2026-10-20 09:30' +%s)000"
```

Use a device name of its own (e.g. `import`) and upload oldest first: audio earlier than where that device has already been processed is kept but not transcribed. The endpoint is `chunk()` in `server/server.py` (Ogg Opus as the apps send it is `Content-Type: audio/ogg`; `audio/webm` and `audio/aac` work too); `firmware/devkit/lr_sync.py` is a reference Python client that reads a recorder's card and uploads it.

## Quickstart (Docker)

You need Docker on an amd64 or arm64 machine and an [Alibaba Cloud Bailian (DashScope)](https://bailian.console.aliyun.com/) API key, Beijing region; one key can run both the ASR and the LLM.

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
cp .env.example .env    # fill in DASHSCOPE_API_KEY and LLM_API_KEY (the same key works with the example LLM settings)
mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
docker compose up -d
docker compose logs -f  # the first start downloads the model files (about 55 MB), sha256-checked
```

Open `http://127.0.0.1:8770/?token=<your token>` (the token is in `data/tenants.json`) and scan the QR code on the **连接手机** page from the app. Phones can connect to `http://<server IP>:8770` directly, e.g. a Tailscale 100.x or LAN address; add HTTPS only to reach it over the public internet, see [Docker details](#docker-details).

**More docs**

- [Docker details](#docker-details): key restrictions, `ENGINE=local`, time zone, the image, an HTTPS reverse proxy for public access
- [Without Docker](#quickstart-without-docker)
- [Configuration](#configuration): every environment variable and the ASR engines
- [Model files](#model-files)
- [Phone app](#phone-app)
- [Hardware](#hardware-optional): LR-P3's parts, wiring and case in [`hardware/lr-p3/README.md`](hardware/lr-p3/README.md), the firmware in [`firmware/devkit/README.md`](firmware/devkit/README.md)
- [ASR benchmark](docs/asr-benchmark.md): the local engine against the cloud engines
- [Deploying with deploy.sh](#deploying-with-deploysh-maintainers)
- [Development](#development) and [CONTRIBUTING.md](CONTRIBUTING.md)

## Roadmap

- **Import**: import recording files from the app or the CLI (today only through `POST /api/chunks`).
- **Undo**: a to-do's 完成 / 不对 marks and a speaker merge can't be undone yet.
- **Desktop split view**: the list on the left, the details on the right on wide screens.
- **People cards**: add what you talked about lately to the existing people cards.
- **Player polish**: keep improving the conversation page's bottom player and follow-along highlight.
- **Live captions**: a "going on now" card plus a full-screen caption mode.

## Community and contributing

Issues and PRs are welcome: [issues](https://github.com/suyiiyii/alldayrec/issues). Code boundaries, the CI workflows, release builds and their secrets are in [CONTRIBUTING.md](CONTRIBUTING.md); the local check commands are under [Development](#development).

## Privacy and the law

You are recording other people. Tell them, ask for consent where it is needed, and follow the law where you live; some places require every party's consent. All data stays on your server; the audio goes to the ASR provider you choose and transcripts go to the LLM you choose, under their terms.

## Details

### Docker details

The Bailian key runs the ASR, and it can run the LLM too (`qwen-plus` in `.env.example`), as long as the key is not restricted to specific models in the Bailian console: a model-restricted key gets 403 on the chat models.
No ASR key: `ENGINE=local` runs the speech-to-text in the server (sherpa-onnx), text only, with speakers from the built-in voiceprints and worse on far-field audio; the LLM key is still required.

With `ENGINE=local` the server downloads another ~136 MB (the sherpa-onnx model tarball) when the first audio arrives, so the first transcript takes about a minute.

The container runs in UTC unless `.env` sets `TZ` (`.env.example` has `TZ=Asia/Shanghai`): conversations are dated, and `RECAP_AT` fires, in that time zone.

A prebuilt image is published to `ghcr.io/suyiiyii/alldayrec` (amd64/arm64), and `docker-compose.yml` uses it. To build it locally instead, uncomment `build: .` in `docker-compose.yml` and run `docker compose up -d --build`.

Open `http://127.0.0.1:8770/?token=<your token>` (the token is in `data/tenants.json`; on a remote machine: `ssh -L 8770:127.0.0.1:8770 host` first); a cookie keeps you signed in. Everything lives in `./data`: `tenants.json`, the model files, and per person a SQLite database and the original audio.

The app accepts plain `http://` servers (since 1.5.1): inside Tailscale or a LAN, use the IP and port directly. To reach the server over the public internet, put a reverse proxy with TLS in front of port 8770 (Caddy, nginx, a Cloudflare Tunnel, Tailscale Serve); `GET /api/health` answers `{"ok": true}` without a token, for its health checks. The compose file publishes the port on 127.0.0.1 only; don't expose a server whose token is empty.

### Quickstart (without Docker)

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

### Configuration

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

The comparison of the local engine with the cloud engines is in [docs/asr-benchmark.md](docs/asr-benchmark.md).

The LLM is always an external service: the project doesn't bundle or run one. To keep everything on your own hardware, point `LLM_BASE_URL` at a local OpenAI-compatible server yourself (vLLM, llama.cpp, Ollama); the model must handle tool calls and JSON output.

### Phone app

- **Android:** build it with `cd app && flutter build apk --release`, or install the APK from the [Releases page](https://github.com/suyiiyii/alldayrec/releases) (`apk-v*` tags). The server can also offer updates at `/app.apk`.
- **iOS:** there is no App Store build; you build and install it yourself from Xcode. Copy `app/ios/Flutter/Signing.local.xcconfig.example` to `Signing.local.xcconfig` and put in your Team ID (and a bundle id suffix of your own). With a free Apple ID, an app you install this way stops opening after 7 days and has to be installed again; a paid Apple Developer account removes that limit.

Connect the app: in the web UI open **连接手机** (`/connect`) and scan its QR code from the app's settings (**扫码登录**), or type the server address and token by hand. The app's UI is in Chinese for now.

### Hardware (optional)

The phone alone is enough. For all-day wear there is an open BLE recorder: an nRF52840 board, a PDM microphone, a microSD card and a small battery. It records to the card all the time and syncs to the phone whenever it's connected, so nothing is lost when the phone is away.

| Model | Board |
|---|---|
| `LR-X1` | Seeed XIAO nRF52840 Sense (built-in mic) + SD module + battery |
| `LR-P3` | ProMicro nRF52840 (nice!nano compatible) + PDM mic + SD module + switch, in a printed case |

Firmware, wiring, build (nRF Connect SDK v2.7.0) and over-the-air updates: [`firmware/devkit/README.md`](firmware/devkit/README.md). New boards can be checked with [`firmware/selftest`](firmware/selftest). LR-P3's parts, wiring, soldering order and printed case (STL, STEP and source) are in [`hardware/lr-p3/README.md`](hardware/lr-p3/README.md) (Chinese, with an English summary). The firmware started from the [Omi](https://github.com/BasedHardware/omi) devkit.

### Architecture and layout

```
BLE recorder ──BLE──▶ phone app ──HTTP(S)──▶ server (FastAPI) ──▶ web UI, MCP, CLI
                      (or the phone's mic)    │ local: VAD, voiceprints (onnxruntime)
                                              │ cloud: ASR engine, OpenAI-compatible LLM
                                              ▼
                                       SQLite + original audio
```

| Directory | What |
|---|---|
| `server/` | The server: upload queue, VAD and voiceprints, ASR engines, conversations, summaries, MCP (Python 3.11, uv) |
| `web/` | The web UI: today, conversations, people, calendar, live transcript, device pages (Vue, pnpm) |
| `app/` | The phone app for iOS and Android: records the mic or the BLE recorder, uploads, firmware updates (Flutter) |
| `firmware/devkit/` | The BLE recorder firmware for nRF52840 boards (Zephyr / nRF Connect SDK), based on the Omi devkit |
| `firmware/selftest/`, `firmware/blerelay/` | Board self-test firmware; an ESP32 BLE relay used for testing |
| `hardware/lr-p3/` | The LR-P3 recorder: parts, wiring and the 3D-printed case |
| `cli/` | `lr.py`: read your conversations from a terminal or an agent |
| `docs/` | Further docs such as the ASR benchmark |

### Development

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

### Deploying with deploy.sh (maintainers)

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

## About the project

"LR" / "lr" is the project's internal codename, still used in the hardware and the code: recorder names like `LR-P3`, the firmware tool `lr.sh`, the CLI `lr.py`.

The git history was rewritten before publishing to remove the author's private deployment details, so it is short on purpose, and references like `lr#123` in commit messages point at issues of the earlier private repository, not this one.

## License

MIT, see [LICENSE](LICENSE). Bundled third-party code (the Omi devkit, Opus, FireRedVAD's post-processor and others) keeps its own license: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=suyiiyii/alldayrec&type=Date)](https://star-history.com/#suyiiyii/alldayrec&Date)

[^lrd]: [igao7, Anker AI recorder review (price, AI credits and membership, battery, audio export, cloud storage), Chinese](https://m.igao7.com/news/202601/M0rs0SQkAdD05EZY.html)
[^lrd2]: [52audio, Anker AI recorder (voiceprints, minutes and to-dos, Q&A), Chinese](https://www.52audio.com/archives/267283.html)
[^xlq]: [GeekPark, OPPO AI 心力球 and its 白鲸 AI app (no original audio, PCC private cloud, voiceprint, MCP, supported phones), Chinese](https://www.geekpark.net/news/372428)
[^xlq2]: [Sina Tech, OPPO 心力球 at a ¥499 trial price, Chinese](https://finance.sina.com.cn/tech/digi/2026-09-22/doc-inistexc1962351.shtml)
[^a1]: [QbitAI, DingTalk A1 launch (price, membership, battery, storage), Chinese](https://www.qbitai.com/2025/09/331938.html)
[^a1b]: [DingTalk AI Minutes (names after voiceprint registration)](https://www.dingtalk.io/zh/blog/ai-minutes-redefining-notes)
[^a1c]: [GeekPark, DingTalk A1 hands-on (transcript, minutes, chapters, analysis), Chinese](https://www.geekpark.net/news/353280)
[^plaud-dev]: [Plaud device comparison (NotePin S price, battery)](https://www.plaud.ai/pages/compare-plaud-devices)
[^plaud-plan]: [Plaud AI membership pricing](https://www.plaud.ai/pages/plaud-ai-plan-pricing)
[^plaud-exp]: [Plaud help center, exporting files (MP3 / WAV)](https://support.plaud.ai/hc/en-us/articles/50835453223705-Export-files)
[^plaud-rn]: [Plaud release notes (find and replace speaker names)](https://www.plaud.ai/pages/plaud-release-notes)
[^plaud-app]: [Plaud App 3.0 (Highlights, Summary, To-dos, Ask)](https://www.plaud.ai/blogs/news/plaud-app-3-design-intelligence)
[^plaud-mcp]: [Plaud MCP docs](https://docs.plaud.ai/plaud-mcp-cli/mcp)
[^limitless]: [TechInformed, Meta acquires Limitless, which stops selling its pendant to new customers (2025-12-09)](https://techinformed.com/meta-acquires-limitless-pendant-users-moved-to-free-unlimited-plan/)
[^bee]: [The Next Web, Amazon relaunches Bee (prices, subscription, audio deleted after processing)](https://thenextweb.com/news/amazon-bee-frontier-eclipse-subscription)
[^omi-price]: [Omi pricing](https://www.omi.me/pages/pricing)
[^omi-docs]: [Omi docs (hardware, firmware, app and backend open source, self-hostable, speaker identification)](https://docs.omi.me/doc/get_started/introduction)
[^omi-audio]: [Omi backend, `store_recording_permission` off by default](https://github.com/BasedHardware/omi/blob/main/backend/database/users.py)
[^omi-home]: [Omi's README (summaries and action items)](https://github.com/BasedHardware/omi); [its home page source (Daily Recaps)](https://github.com/BasedHardware/omi/blob/main/app/lib/pages/home/home_content.dart)
[^omi-mcp]: [Omi MCP docs](https://docs.omi.me/doc/developer/MCP)
