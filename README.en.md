<div align="center">

# alldayrec（全天录）

### The open-source Limitless: a private second brain.

Works on every platform, no hardware to buy: your phone is the recorder.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/alldayrec)](https://pypi.org/project/alldayrec/)
[![Release](https://img.shields.io/github/v/release/suyiiyii/alldayrec?filter=v*&label=release)](https://github.com/suyiiyii/alldayrec/releases)
[![Android APK](https://img.shields.io/github/v/release/suyiiyii/alldayrec?filter=apk-v*&label=Android%20APK)](https://github.com/suyiiyii/alldayrec/releases)
[![Docker image](https://img.shields.io/badge/ghcr.io-alldayrec-2496ed?logo=docker&logoColor=white)](https://github.com/suyiiyii/alldayrec/pkgs/container/alldayrec)
[![GitHub stars](https://img.shields.io/github/stars/suyiiyii/alldayrec?style=flat)](https://github.com/suyiiyii/alldayrec)

[中文](README.md) | **English**

[Docs](https://suyiiyii.github.io/alldayrec/en/) · [Quickstart](#quickstart) · [How it compares](https://suyiiyii.github.io/alldayrec/en/explanation/intro.html#how-it-compares) · [Hardware](#hardware) · [Roadmap](https://suyiiyii.github.io/alldayrec/en/explanation/roadmap.html)

Let an AI agent deploy it for you: [SKILL.md](https://suyiiyii.github.io/alldayrec/SKILL.md)

`#self-hosted` `#lifelog` `#speaker-diarization` `#MCP` `#open-hardware`

</div>

alldayrec is an all-day recording and memory service that runs on your own server. Record from morning to night with your phone, an open-source recorder or a Feishu recorder bean, and within a minute your server turns the audio into transcripts with speakers, then into a timeline of your day: conversation summaries, the people you talked to, to-dos and a daily recap. The original audio is kept, so every line can be played back, and your agent can search your days over MCP.

<div align="center">

![Live captions and the live summary](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-20261025.gif)

| ![Timeline](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/timeline-20261025.png) | ![One conversation](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversation-20261025.png) |
|:--:|:--:|
| Timeline: the conversation going on, due to-dos, each day's conversations | One conversation: the summary, to-dos and decisions, each with its audio one tap away |

The animation is the Live page: captions come up as people speak, and the AI live summary keeps up. The screenshots show made-up data in the Chinese UI; the web UI switches to English on the 「我的」 (Me) page.

</div>

## What it does

- **Every platform, no hardware to buy**: the Android app, the iPhone app and a browser all record directly; while offline, audio queues on the device and goes up later.
- **Transcribes in two layers**: the final transcript comes from a cloud ASR engine (default: Alibaba Cloud Bailian Qwen, about ¥0.05 per hour of audio), and drafts show within seconds while a page is open; `ENGINE=local` transcribes on your machine instead.
- **Knows who spoke**: speech detection and voiceprints run locally on the CPU; it learns your own voice and remembers the names you give others.
- **Remembers**: conversation summaries, to-dos with due dates, a daily recap, full-text search, and "ask AI" answers with links to the conversations they came from.
- **Opens up**: a web UI, a remote MCP endpoint (Claude Code, Codex, Cursor), a CLI; the data is SQLite plus the original audio.
- **Imports recordings**: audio files you already have can be [imported](https://suyiiyii.github.io/alldayrec/en/how-to/import-recordings.html) over the HTTP API.

## Quickstart

You need an [Alibaba Cloud Bailian (DashScope)](https://bailian.console.aliyun.com/) API key, Beijing region; one key can run both the ASR and the LLM. Other engines: [How to change the ASR engine](https://suyiiyii.github.io/alldayrec/en/how-to/change-asr-engine.html). Pick one of three ways to run it.

### With uvx (the quickest)

You need [uv](https://docs.astral.sh/uv/) (Python 3.11–3.14; uv downloads one if there is none) and ffmpeg. In an empty directory, make a `.env`:

```ini
DASHSCOPE_API_KEY=<your Bailian key>
LLM_API_KEY=<the same Bailian key>
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

Run `uvx --env-file .env alldayrec` and, on this computer, open the link in the log's `first start: ...` line. pip, the data directory and phones: [How to run with uvx or pip](https://suyiiyii.github.io/alldayrec/en/how-to/run-uvx.html).

### With Docker

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
cp .env.example .env    # fill in DASHSCOPE_API_KEY and LLM_API_KEY
mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
docker compose up -d && docker compose logs -f
```

Open `http://<server IP>:8770/?token=<your token>` (the token is in `data/tenants.json`). The port, time zone, image versions and public access: [How to deploy with Docker](https://suyiiyii.github.io/alldayrec/en/how-to/deploy-docker.html).

### From source

You need uv, ffmpeg, Node 22 and pnpm.

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec && cp .env.example .env    # fill in the keys
(cd web && pnpm install && pnpm build)
cd server && uv sync && set -a && . ../.env && set +a && uv run alldayrec
```

Details: [How to run from source](https://suyiiyii.github.io/alldayrec/en/how-to/deploy-without-docker.html).

### Start recording

- Android: download the latest `apk-v*` APK from [Releases](https://github.com/suyiiyii/alldayrec/releases).
- iPhone: build and install the app from source with Xcode.
- Browser: open the web UI's `/record` page.

Installing and connecting the app: [Install and connect the phone app](https://suyiiyii.github.io/alldayrec/en/how-to/install-app.html). Reaching the server from other devices: [Reach the server from a phone](https://suyiiyii.github.io/alldayrec/en/how-to/remote-access.html).

## Hardware

No hardware to buy: your phone records. For all-day wear, connect one of these recorders:

- **Open-source recorder**: build an `LR-X1` (Seeed XIAO nRF52840 Sense) or an `LR-P3` (ProMicro nRF52840 in a 3D-printed case). It records to an SD card all the time and syncs to the phone when connected. Parts, wiring and firmware: [Recorder hardware](https://suyiiyii.github.io/alldayrec/en/reference/hardware.html), [`hardware/lr-p3/`](hardware/lr-p3/README.md), [`firmware/devkit/`](firmware/devkit/README.md).
- **Feishu recorder bean** (飞书录音豆): the Android app (1.5.5 and later) connects to it directly from **Add recorder**. With the phone nearby it uploads as it records; when the phone is away the audio stays on the bean and is fetched when the phone is back. Recordings the server has in full are deleted from the bean.

## Docs

[suyiiyii.github.io/alldayrec](https://suyiiyii.github.io/alldayrec/en/), in English and Chinese:

- [Tutorials](https://suyiiyii.github.io/alldayrec/en/tutorial/first-run.html): run it for the first time and see your first transcript.
- [How-to guides](https://suyiiyii.github.io/alldayrec/en/how-to/run-uvx.html): deploy, reach the server from a phone, bind a recorder, import recordings, connect MCP, add people, upgrade and back up.
- [Reference](https://suyiiyii.github.io/alldayrec/en/reference/configuration.html): configuration, HTTP API, MCP tools, the CLI, data layout, model files, hardware.
- [Explanation](https://suyiiyii.github.io/alldayrec/en/explanation/intro.html): what it is, architecture and data flow, privacy, the ASR benchmark.
- [Limitations and roadmap](https://suyiiyii.github.io/alldayrec/en/explanation/roadmap.html).

## Contributing

Issues and PRs are welcome: [issues](https://github.com/suyiiyii/alldayrec/issues). Local checks and commit messages: [How to contribute](https://suyiiyii.github.io/alldayrec/en/how-to/contribute.html); code boundaries, CI and release builds: [CONTRIBUTING.md](CONTRIBUTING.md).

## Privacy and the law

You are recording other people. Tell them, ask for consent where it is needed, and follow the law where you live; some places require every party's consent. The data stays on your server; audio and text go to the ASR service and the LLM you choose, see [Privacy: where your data goes](https://suyiiyii.github.io/alldayrec/en/explanation/privacy.html).

Feishu, Limitless and other product names belong to their owners; this project is not affiliated with any of them.

## License

MIT, see [LICENSE](LICENSE). Bundled third-party code (the Omi devkit, Opus, FireRedVAD's post-processor and others) keeps its own license: [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=suyiiyii/alldayrec&type=Date)](https://star-history.com/#suyiiyii/alldayrec&Date)
