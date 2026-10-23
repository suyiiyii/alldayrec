# alldayrec（全天录）

**alldayrec is a self-hosted, all-day recorder and personal memory layer: wear a recorder (or just carry your phone) from morning to night, and your own server turns the audio into speech-to-text transcripts with speaker diarization, conversation summaries, to-dos and a daily recap, within a minute.** It keeps the original audio, so every line can be played back and checked, and old days can be transcribed again by a better model later. It is device-agnostic (the iPhone and Android app, or an open-source BLE wearable you can build from a few parts), and it speaks MCP, so your own agent can search your days. Think of it as a lifelog you host yourself: an open-source alternative to Limitless, Plaud, Bee or Omi, without a subscription and without your conversations living on someone else's cloud.

[中文说明](#中文说明)

"LR" / "lr" is the project's internal codename, still used in the hardware and the code: recorder names like `LR-P3`, the firmware tool `lr.sh`, the CLI `lr.py`.

## Screenshots

The web UI on a phone, filled with made-up data (the UI is in Chinese for now).

| ![Conversations](docs/img/conversations.png) | ![One conversation](docs/img/conversation.png) | ![Live page](docs/img/live.png) |
|:--:|:--:|:--:|
| Conversations: titles, summaries, tags, due to-dos | One conversation: summary, per-person points, to-dos, decisions, transcript with speakers | Live page: the live analysis of the conversation going on, drafts as words come in |
| ![Ask AI](docs/img/ask.png) | ![Daily recap](docs/img/recap.png) | ![Progress](docs/img/progress.png) |
| Ask AI: an answer with links to the conversations it came from | Daily recap: highlights, your to-dos, the people you talked to | Progress: each device's queue, recorder battery and card, the phone, today's ASR and LLM calls |

Live captions while the page is open (the draft layer), and the conversations page on a desktop:

![Live captions](docs/img/live-captions.gif)

![Conversations on a desktop](docs/img/conversations-desktop.png)

## What it does

- **Records all day.** The phone app uploads short Opus pieces; a BLE recorder writes everything to its SD card and streams it to the phone, which fills gaps after a disconnection. Nothing is lost while offline: pieces queue on the device and go up later.
- **Transcribes in two layers.** The final layer cuts the audio in pauses and sends pieces of up to 5 minutes to a cloud ASR engine (default: Alibaba Cloud Bailian Qwen, about ¥0.05 per hour of audio); while a page is open, a draft layer shows words within seconds.
- **Knows who spoke.** Speech detection (FireRedVAD) and voiceprints (ReDimNet2) run locally on onnxruntime, no GPU: it learns your own voice, keeps speakers apart within a conversation and remembers the names you give them.
- **Remembers.** Conversations are closed after a silence and summarized by an LLM of your choice; to-dos with due dates, a daily recap, full-text search and "ask AI" over your history.
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

You need Docker on an amd64 or arm64 machine and an [Alibaba Cloud Bailian (DashScope)](https://bailian.console.aliyun.com/) API key, Beijing region. One key runs the ASR, and it can run the LLM too.

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
cp .env.example .env    # fill in DASHSCOPE_API_KEY and LLM_API_KEY (the same key works with the example LLM settings)
mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
docker compose build    # the image is built here from this checkout, amd64 or arm64 (prebuilt image: TODO)
docker compose up -d
docker compose logs -f  # the first start downloads the model files (about 55 MB), sha256-checked
```

Open `http://127.0.0.1:8770/?token=<your token>` (the token is in `data/tenants.json`); a cookie keeps you signed in. Everything lives in `./data`: `tenants.json`, the model files, and per person a SQLite database and the original audio.

Phones need HTTPS: put a reverse proxy with TLS in front of port 8770 (Caddy, nginx, a Cloudflare Tunnel, Tailscale Serve). The compose file publishes the port on 127.0.0.1 only; don't expose a server whose token is empty.

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

Two models run on the server, on CPU: [FireRedVAD](https://github.com/FireRedTeam/FireRedVAD) (speech detection, Apache-2.0) and [ReDimNet2](https://github.com/PalabraAI/redimnet2) B6 (voiceprints, MIT). At the first start the server downloads the missing ones into `MODELS_DIR`, FireRedVAD from its upstream repository and ReDimNet2 from this repository's `models-v1` release, and checks each against the sha256 pinned in `server/infer.py`. A file that is already there is never downloaded again.

If a download fails, put the files into `MODELS_DIR` yourself (`data/models` in Docker), from the same places. `server/export_onnx.py` re-creates all three from the upstream weights (it installs torch for this step only):

```bash
cd server && uv run --group export python export_onnx.py /path/to/models                        # without Docker
docker compose run --rm alldayrec uv run --group export python export_onnx.py                   # Docker: into ./data/models
```

The FireRedVAD files come out identical, but the ReDimNet2 export is not byte-for-byte reproducible across machines yet: its sha256 may differ from the pinned one, and then the server refuses it. The release file is the reliable way.

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

`DRAFT_ENGINE=qwen-stream` streams the audio to Qwen while a page is open, so words show as they are said. `ENGINE_FALLBACK` takes the windows the main engine fails on.

The LLM is always an external service: the project doesn't bundle or run one. To keep everything on your own hardware, point `LLM_BASE_URL` at a local OpenAI-compatible server yourself (vLLM, llama.cpp, Ollama); the model must handle tool calls and JSON output.

## Phone app

- **Android:** build it with `cd app && flutter build apk --release`, or install the APK from the Releases page when there is one. The server can also offer updates at `/app.apk`.
- **iOS:** there is no App Store build; you build and install it yourself from Xcode. Copy `app/ios/Flutter/Signing.local.xcconfig.example` to `Signing.local.xcconfig` and put in your Team ID (and a bundle id suffix of your own). With a free Apple ID, an app you install this way stops opening after 7 days and has to be installed again; a paid Apple Developer account removes that limit.

Connect the app: in the web UI open **连接手机** (`/connect`) and scan its QR code from the app's settings (**扫码登录**), or type the server address and token by hand. The app's UI is in Chinese for now.

## Hardware (optional)

The phone alone is enough. For all-day wear there is an open BLE recorder: an nRF52840 board, a PDM microphone, a microSD card and a small battery. It records to the card all the time and syncs to the phone whenever it's connected, so nothing is lost when the phone is away.

| Model | Board |
|---|---|
| `LR-X1` | Seeed XIAO nRF52840 Sense (built-in mic) + SD module + battery |
| `LR-P3` | ProMicro nRF52840 (nice!nano compatible) + PDM mic + SD module + switch, in a printed case |

Firmware, wiring, build (nRF Connect SDK v2.7.0) and over-the-air updates: [`firmware/devkit/README.md`](firmware/devkit/README.md). New boards can be checked with [`firmware/selftest`](firmware/selftest). The firmware started from the [Omi](https://github.com/BasedHardware/omi) devkit.

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

---

## 中文说明

**全天录（alldayrec）是一个自己部署的全天录音和个人记忆层。** 戴一个录音设备（或者只带手机）从早录到晚，自己的服务器在一分钟内把音频变成带说话人的转写、对话摘要、待办和每日回顾。它保留原始录音，每句话都能回放核对，以后有了更好的模型还能把过去重新转写一遍。它不挑设备（iPhone、安卓 App，或者几十块钱就能自己做的开源蓝牙录音硬件），支持 MCP，自己的 Agent 可以直接查你的每一天。可以把它当成自己托管的 lifelog：Limitless、Plaud、Bee、Omi 的开源替代，没有订阅，对话也不放在别人的云上。

「LR」「lr」是项目的内部代号，硬件和代码里还在用：录音设备型号 `LR-P3`、固件工具 `lr.sh`、命令行 `lr.py`。

网页截图见上面的 [Screenshots](#screenshots)（示例数据都是编的）。

**能做什么**

- 全天录音：手机 App 上传 Opus 分片；蓝牙录音设备一直写 SD 卡，同时推给手机，断开期间的部分回来后补齐。
- 两层转写：定稿层在停顿处切开，每段最长 5 分钟交给云端识别（默认阿里云百炼千问，约 ¥0.05/小时音频）；页面开着时，初稿层几秒内出字。
- 认人：语音检测（FireRedVAD）和声纹（ReDimNet2）在本机 CPU 上跑（onnxruntime，不需要显卡），认得出你自己，分得清一段对话里的不同人，记得你起的名字。
- 记忆：一段安静之后对话结束，用你配置的大模型写摘要；待办和截止日期、每日回顾、全文搜索、「问 AI」。
- 开放：网页、远程 MCP（`POST /mcp`）、命令行 `cli/lr.py`，数据就是 SQLite。

**快速开始**：命令见上面的 [Quickstart (Docker)](#quickstart-docker) 和 [Quickstart (without Docker)](#quickstart-without-docker)；Docker 镜像目前用 `docker compose build` 在本机构建（预构建镜像：TODO）。最少只要一把阿里云百炼（DashScope）北京区的 key：`ENGINE=qwen` 的语音识别用它，`.env.example` 里示例的大模型设置也用同一把。手机连服务器需要 HTTPS，请在 8770 端口前面放一个带 TLS 的反向代理。

**模型文件**：首次启动自动下载到 `MODELS_DIR` 并校验 sha256：FireRedVAD 从上游仓库下载，ReDimNet2 从本仓库的 `models-v1` Release 下载；已经有的文件不会重复下载。下载失败时，把文件自己放进 `MODELS_DIR`（Docker 里是 `data/models`）；`export_onnx.py` 也能从上游权重重新导出，但 ReDimNet2 的导出在不同机器上逐字节不一致，sha256 可能对不上钉住的值，服务会拒绝它，所以还是以 Release 里的文件为准。注意：ReDimNet2 的代码和权重是 MIT，但训练数据包含 VoxBlink2（CC BY-NC-SA 4.0），是否适合你的用途请自己判断，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

**配置**：全部是环境变量，见 [`.env.example`](.env.example)。大模型一律是外部服务，项目不打包、不运行大模型；想全在自己机器上跑，把 `LLM_BASE_URL` 指向你自己的 OpenAI 兼容服务即可，模型需要支持工具调用和 JSON 输出。

**手机 App**：安卓自己 `flutter build apk`，或者从 Release 下载 APK。iOS 不上架、不分发，需要自己用 Xcode 构建安装：把 `app/ios/Flutter/Signing.local.xcconfig.example` 复制成 `Signing.local.xcconfig`，填自己的 Team ID。用免费 Apple ID 装的应用 7 天后会打不开，要重新安装一次；付费开发者账号没有这个限制。网页的「连接手机」页面有二维码，App 设置里「扫码登录」即可。

**硬件（可选）**：只用手机也可以。全天佩戴可以用开源的蓝牙录音设备：nRF52840 板子 + PDM 麦克风 + microSD 卡 + 小电池，见 [`firmware/devkit/README.md`](firmware/devkit/README.md)。

**隐私和法律**：录音会录到别人。请告知对方，需要时取得同意，遵守当地法律。数据都在你自己的服务器上；音频会发给你选择的语音识别服务，转写会发给你选择的大模型。

**许可**：MIT，见 [LICENSE](LICENSE)；第三方代码保留各自的许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
