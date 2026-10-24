<div align="center">

# alldayrec（全天录）

### 开源录音豆，私有第二大脑。

开源版 Plaud 与心力球：全天录音私有化，每小时转写只要五分钱。

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/suyiiyii/alldayrec?filter=v*&label=release)](https://github.com/suyiiyii/alldayrec/releases)
[![Android APK](https://img.shields.io/github/v/release/suyiiyii/alldayrec?filter=apk-v*&label=Android%20APK)](https://github.com/suyiiyii/alldayrec/releases)
[![Docker image](https://img.shields.io/badge/ghcr.io-alldayrec-2496ed?logo=docker&logoColor=white)](https://github.com/suyiiyii/alldayrec/pkgs/container/alldayrec)
[![GitHub stars](https://img.shields.io/github/stars/suyiiyii/alldayrec?style=flat)](https://github.com/suyiiyii/alldayrec)

**中文** | [English](README.en.md)

[快速开始](#快速开始docker) · [和市面产品比](#和市面产品比) · [导入已有录音](#已有录音怎么导入) · [硬件](hardware/lr-p3/README.md) · [路线图](#路线图)

**文档：** [suyiiyii.github.io/alldayrec](https://suyiiyii.github.io/alldayrec/) · 让 AI Agent 帮你部署：[SKILL.md](https://suyiiyii.github.io/alldayrec/SKILL.md)

`#自部署` `#全天录音` `#说话人识别` `#MCP` `#Plaud替代` `#开源硬件`

</div>

全天录是一个自己部署的全天录音和个人记忆层。戴一个录音设备（或者只带手机）从早录到晚，自己的服务器在一分钟内把音频变成带说话人的转写、对话摘要、待办和每日回顾。原始录音一直留着，每句话都能点开回放核对；你的 Agent 通过 MCP 直接查你的每一天。可以把它当成自己托管的 lifelog：Plaud、Limitless、Bee、Omi 的开源替代，没有订阅，对话也不放在别人的云上。（录音豆、心力球等产品名称归各自所有者，本项目与它们没有关联。）

<div align="center">

![实时字幕](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-20261024.gif)

页面开着时的实时字幕（初稿层）。下面是网页在手机上的样子，示例数据都是编的（截图是中文界面；main 上的网页也可以在「我的」页换成英文）。

</div>

| ![Today](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/today-20261024.png) | ![Conversations](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversations-20261024.png) | ![One conversation](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversation-20261024.png) | ![Transcript](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/transcript-20261024.png) |
|:--:|:--:|:--:|:--:|
| 今天：到期待办、昨天的回顾、进行中的对话、今天的对话，底部直接问今天的事 | 对话列表：标题、摘要、标签、到期待办 | 一段对话的摘要：待办、决定、每个人的要点 | 原文：带说话人的转写，点一句就从那里播放，正在播的那句高亮 |
| ![Live page](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-20261024.png) | ![Ask AI](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/ask-20261024.png) | ![Daily recap](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/recap-20261024.png) | ![Me](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/me-20261024.png) |
| 实时页：正在进行的对话的实时分析，边说边出初稿 | 问 AI：回答里附带出处对话的链接 | 每日回顾：要点、你的待办、和你说过话的人 | 我的：每台录音设备的电量、存储卡剩余和延迟，以及连接手机、处理进度、接入 AI 的入口 |

电脑上的对话列表页：

![Conversations on a desktop](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversations-desktop-20261024.png)

## 为什么做这个

- **原始录音一直留着。** 心力球和 Bee 转写完就删音频，转写错了没有底稿可对。这里每句话都能点开回放，以后有了更好的模型，还能把过去重新转写一遍。
- **数据在你自己的服务器上。** 一台机器、一个 SQLite 数据库和一堆原始录音。音频只发给你选的语音识别服务，转写只发给你选的大模型，不需要注册任何账号。
- **没有订阅。** 软件免费（MIT）；默认的千问文件转写约 ¥0.05/小时音频，按量付给云厂商，没有每月分钟数上限。大模型费用按你选的服务计。
- **不挑设备，硬件开源。** iPhone 和安卓 App 直接录手机麦克风；想全天戴，就用几十块钱的现成模块焊一个蓝牙录音设备（`LR-P3`），零件表、接线图和 3D 打印外壳都在仓库里。
- **你的 Agent 能查你的每一天。** 远程 MCP 端点给 Claude Code、Codex、Cursor 用，也有命令行；数据就是 SQLite，自己就能读。

## 和市面产品比

| | **alldayrec** | 飞书录音豆 | OPPO 心力球 | 钉钉 A1 | Plaud | Limitless / Bee | Omi |
|---|---|---|---|---|---|---|---|
| **价格 / 订阅** | 软件免费；转写按量付费，千问约 ¥0.05/小时；大模型另计 | ¥899 起；送 6 个月 AI 权益，之后会员 ¥69/月[^lrd] | ¥499 体验价，招募制；只配 OPPO、一加、realme 手机[^xlq][^xlq2] | ¥499 / ¥799[^a1c]；会员 ¥599 或 ¥1499/年[^a1] | NotePin S $179[^plaud-dev]；免费 300 分钟/月，Pro $17.99/月[^plaud-plan] | Limitless 已被 Meta 收购、停售[^limitless]；Bee $74.99 + Pro $11.99/月，仅美国[^bee] | 吊坠 $179（促销 $129）；免费 300 云端分钟/月，Pro $269/年[^omi-price] |
| **全天佩戴** | 是：手机 App，或自制录音设备边录边写卡 | 单次 8 小时，带充电仓 32 小时[^lrd] | 24 小时连续记录[^xlq] | 贴在手机背面的卡片，连续录 45 小时[^a1] | NotePin S 约 20 小时[^plaud-dev] | Bee 手环，7 天续航[^bee] | 吊坠 |
| **保留原音** | 是，每句可回放、可重新转写 | 设备上传后删除，云端保留；只能在妙记网页逐条下载[^lrd] | 否，「只留文字、不留原音」[^xlq] | 未知 | 是，可导出 MP3 / WAV[^plaud-exp] | Bee 否，处理后删除[^bee] | 可选，默认不存[^omi-audio] |
| **数据在哪** | 你自己的服务器 | 飞书云[^lrd] | OPPO PCC 私密云[^xlq] | 设备 64 GB + 钉钉云[^a1] | Plaud 云[^plaud-plan] | Bee 的云（Bee Private Compute）[^bee] | Omi 云，或自己部署后端[^omi-docs] |
| **说话人识别** | 本机声纹：认得出你自己，记得你起的名字 | 声纹区分说话人[^lrd2] | 声纹区分佩戴者本人[^xlq] | 注册声纹后显示姓名[^a1b] | 区分说话人，可查找替换说话人名[^plaud-rn] | 未知 | 区分说话人[^omi-docs] |
| **摘要 / 待办 / 回顾** | 摘要、待办和截止日期、决定、每日回顾 | 纪要、待办[^lrd2] | 今日摘要、待办、项目串联[^xlq] | 纪要、章节、分析与后续跟进[^a1c] | 模板摘要、To-dos[^plaud-app] | Bee 摘要（Pro 不限量）[^bee] | 摘要、待办、日报[^omi-home] |
| **问 AI / MCP** | 问 AI 带出处；远程 MCP | 跨纪要 AI 问答[^lrd2]；MCP 未知 | 在微信、飞书里问；MCP[^xlq] | 未知 | Ask Plaud；MCP[^plaud-mcp] | 未知 | Chat；MCP[^omi-mcp] |
| **开放硬件** | 是（`hardware/lr-p3/`） | 否 | 否 | 否 | 否 | 否 | 是[^omi-docs] |
| **开源** | 是（MIT：服务端、网页、App、固件） | 否 | 否 | 否 | 否 | 否 | 是（MIT）[^omi-docs] |

「否」指没有公开的设计文件或源码，「未知」指没查到可靠资料；价格和功能以各家官网为准，查于 2026-10-24。文中的产品名称和商标归各自所有者所有，本项目与它们没有任何关联。

## 能做什么

- **全天录音。** 手机 App 上传 Opus 短分片；蓝牙录音设备一直写 SD 卡，同时推给手机，断开期间的部分回来后补齐。离线时什么都不丢：分片在设备上排队，之后再上传。
- **什么音源都能接。** 能 POST 音频的东西都能给服务器送数据（`POST /api/chunks`），已有的录音文件也可以，见下面的[导入](#已有录音怎么导入)。
- **两层转写。** 定稿层在停顿处切开，每段最长 5 分钟交给云端语音识别引擎（默认阿里云百炼千问，约 ¥0.05/小时音频）；页面开着时，初稿层几秒内出字。没有语音识别的 key，可以用 `ENGINE=local` 在本机转写（[和云端的对比](docs/asr-benchmark.md)）。
- **认人。** 语音检测（FireRedVAD）和声纹（ReDimNet2）在本机 CPU 上跑（onnxruntime，不需要显卡），认得出你自己，分得清一段对话里的不同人，记得你起的名字。
- **记忆。** 一段安静之后对话结束，用你配置的大模型写摘要；待办和截止日期、每日回顾、全文搜索，以及对历史记录「问 AI」，回答带出处对话的链接。说话不到 60 秒的对话不调大模型，标题取第一句，没有摘要和待办（`server/summary.py` 里的 `MIN_SPEECH_S`）。
- **开放。** 网页、给 Claude Code、Codex、Cursor 等 Agent 用的远程 MCP 端点（`POST /mcp`）、一个小命令行（`cli/lr.py`），数据就是 SQLite，自己就能读。
- **数据归你。** 一台服务器、你自己的硬盘，愿意的话一台服务器可以给好几个人用（每人一个 token）。

### 已有录音怎么导入

App 和网页里目前没有导入按钮，导入走 `POST /api/chunks`：一个文件一个请求，请求体是一个完整的音频文件，服务器用 ffmpeg 解码。

| 你手上的 | 能导入吗 | 怎么做 |
|---|---|---|
| Plaud | 能 | App 里导出 MP3 或 WAV[^plaud-exp]，再按下面的命令上传 |
| 录音笔、手机录音（MP3、WAV） | 能 | 直接上传 |
| `.m4a`（比如 iPhone 语音备忘录） | 能，先转码 | 长的 `.m4a` 从管道解码不了，先 `ffmpeg -i in.m4a out.mp3` |
| 飞书录音豆 | 麻烦 | 音频只能在飞书妙记网页端逐条下载[^lrd]，下载后同上 |
| OPPO 心力球 | 不能 | 不留原音[^xlq]，没有音频可导 |

```bash
# start：这段录音开始的时刻（Unix 毫秒），对话按它落到哪一天、几点；date -d 是 GNU date 的写法
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: audio/mpeg" --data-binary @rec.mp3 \
  "https://<你的服务器>/api/chunks?device=import&start=$(date -d '2026-10-20 09:30' +%s)000"
```

导入用一个单独的 `device` 名（比如 `import`），按时间从早到晚传：早于这台设备已经处理到的位置的音频只保存、不转写。接口细节见 `server/routes_upload.py` 里的 `chunk()`（App 发的 Ogg Opus 用 `Content-Type: audio/ogg`，也收 `audio/webm`、`audio/aac`）；`firmware/devkit/lr_sync.py` 是一个从录音设备读卡再上传的 Python 参考客户端。

## 快速开始（Docker）

需要一台装了 Docker 的 amd64 或 arm64 机器，以及一把[阿里云百炼（DashScope）](https://bailian.console.aliyun.com/)北京区的 API key，语音识别和大模型可以共用这一把。

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
cp .env.example .env    # 填 DASHSCOPE_API_KEY 和 LLM_API_KEY（用示例的大模型设置时两者是同一把 key）
mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
docker compose up -d
docker compose logs -f  # 首次启动下载模型文件（约 55 MB），校验 sha256
```

在电脑上打开 `http://<服务器 IP>:8770/?token=<你的 token>`（token 在 `data/tenants.json` 里），在 **连接手机** 页用 App 扫码。服务器 IP 要用手机连得上的：Tailscale 的 100.x 地址或局域网地址，不要用 127.0.0.1，因为二维码里放的就是网页的地址。已经用 127.0.0.1 打开了，就在 **连接手机** 页的「手机用哪个地址」里选一个，或者自己填。

端口发布在所有网卡上，靠 token 挡住别人；token 为空时服务不会启动。要从公网访问，在前面放一个带 TLS 的反向代理，见 [Docker 部署细节](#docker-部署细节)。

**更多文档**

- [文档站](https://suyiiyii.github.io/alldayrec/)：教程、操作指南、配置和 API 参考，有中英文
- [Docker 部署细节](#docker-部署细节)：key 的限制、`ENGINE=local`、时区、镜像、公网访问的 HTTPS 反向代理
- [不用 Docker](#快速开始不用-docker)
- [配置](#配置)：所有环境变量和语音识别引擎
- [模型文件](#模型文件)
- [手机 App](#手机-app)
- [硬件](#硬件可选)：LR-P3 的零件、接线和外壳见 [`hardware/lr-p3/README.md`](hardware/lr-p3/README.md)，固件见 [`firmware/devkit/README.md`](firmware/devkit/README.md)
- [语音识别评测](docs/asr-benchmark.md)：本地引擎和云端引擎的对比
- [用 deploy.sh 部署](#用-deploysh-部署维护者)
- [开发](#开发)和 [CONTRIBUTING.md](CONTRIBUTING.md)

## 路线图

- **导入**：在 App 或命令行里直接导入已有的录音文件（现在只能用 `POST /api/chunks`）。
- **撤销**：待办的「完成 / 不对」和合并说话人现在不能撤销。
- **桌面端分栏**：宽屏左边列表、右边详情。
- **人物名片**：在已有的人物卡片上加上最近聊了什么。
- **播放器打磨**：对话页的底部播放条和跟读高亮继续改进。
- **实时字幕**：做成「正在进行」卡片加全屏字幕模式。

## 社区与贡献

欢迎提 [issue](https://github.com/suyiiyii/alldayrec/issues) 和 PR。代码边界、CI 流程、发布构建及其密钥见 [CONTRIBUTING.md](CONTRIBUTING.md)（英文），本地检查命令见下面的[开发](#开发)。

## 隐私与法律

录音会录到别人。请告知对方，需要时取得同意，遵守当地法律；有些地方要求所有参与方都同意。数据都在你自己的服务器上；音频会发给你选择的语音识别服务，转写会发给你选择的大模型，适用它们各自的条款。

## 详细说明

### Docker 部署细节

百炼的这一把 key 跑语音识别，也可以跑大模型（`.env.example` 里的 `qwen-plus`），前提是这把 key 没有在百炼控制台里限定可用模型：限定了模型的 key 调对话模型会返回 403。
没有语音识别的 key：`ENGINE=local` 在服务里跑语音识别（sherpa-onnx），只出文字，说话人靠内置声纹区分，远场录音比千问差；大模型的 key 仍然需要。

用 `ENGINE=local` 时，第一段音频到达时服务还要再下载约 136 MB（sherpa-onnx 的模型包），所以第一条转写要等一分钟左右。

容器默认用 UTC，除非 `.env` 里设了 `TZ`（`.env.example` 里是 `TZ=Asia/Shanghai`）：对话的日期和 `RECAP_AT` 的触发时间都按这个时区算。

预构建镜像发布在 `ghcr.io/suyiiyii/alldayrec`（amd64/arm64），`docker-compose.yml` 用的就是它。想在本机构建，把 `docker-compose.yml` 里的 `build: .` 取消注释，再运行 `docker compose up -d --build`。

打开 `http://<服务器 IP>:8770/?token=<your token>`（token 在 `data/tenants.json` 里），之后靠 cookie 保持登录。**连接手机** 页的「手机用哪个地址」列出服务看得到的 Tailscale 和局域网地址；服务在 Docker 里看不到电脑的网卡，这时用「自己填」。所有东西都在 `./data` 里：`tenants.json`、模型文件，以及每个人各自的 SQLite 数据库和原始录音。

App 连 `http://` 的服务器也可以（1.5.1 起），在 Tailscale 或局域网里直接用 IP 和端口就行；要从公网访问，在 8770 端口前面放一个带 TLS 的反向代理（Caddy、nginx、Cloudflare Tunnel、Tailscale Serve）；`GET /api/health` 不需要 token 就返回 `{"ok": true}`，可以给代理做健康检查。compose 文件把 8770 端口发布在所有网卡上，挡住别人的只有 token；token 为空时，除非 HOST 是 127.0.0.1，服务不会启动。只想本机访问，把那一行改成 `127.0.0.1:8770:8770`。

### 快速开始（不用 Docker）

需要 Python 3.11 和 [uv](https://docs.astral.sh/uv/)、ffmpeg、Node 22 和 pnpm。

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
cp .env.example .env    # 填 key，以及 MODELS_DIR=<一个可写目录的绝对路径>
(cd web && pnpm install && pnpm build)    # 网页构建到 server/static/dist
echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "'$PWD/data/main'", "recordings": "'$PWD/data/main/recordings'"}]' > tenants.json && chmod 600 tenants.json
cd server && uv sync && set -a && . ../.env && set +a && uv run python server.py
```

`tenants.json` 列出服务为哪些人服务：每人一条，各有自己的 token、数据目录和录音目录（`server/tenant.py`）。只有一条且 token 为空时不做认证，这时 HOST 必须是 127.0.0.1，否则服务不会启动。手机要直连，在 `.env` 里设 `HOST=0.0.0.0`（要有 token）。

### 模型文件

服务器上跑两个模型，都用 CPU：[FireRedVAD](https://github.com/FireRedTeam/FireRedVAD)（语音检测，Apache-2.0）和 [ReDimNet2](https://github.com/PalabraAI/redimnet2) B6（声纹，MIT）。不需要你做任何事：首次启动时服务会把缺的文件下载到 `MODELS_DIR`（Docker 里是 `data/models`），FireRedVAD 从上游仓库下载，ReDimNet2 从本仓库的 `models-v1` Release 下载（`ENGINE=local` 还会在第一次用到时下载 sherpa-onnx 的模型包），只接受 sha256 与 `server/infer.py`、`server/local_asr.py` 里钉住的值一致的文件。已经有的文件不会重复下载。

下载失败或文件被拒时，删掉它重启，或者从同一个地址把同一个文件放进 `MODELS_DIR`。`server/export_onnx.py` 是维护者从上游权重制作新的 Release 文件用的，不是获取模型的途径：ReDimNet2 的导出在不同机器上逐字节不一致，自己导出的文件会被拒绝。

关于声纹模型：ReDimNet2 的代码和权重是 MIT，但训练数据包含 VoxBlink2（CC BY-NC-SA 4.0）。详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)，是否适合你的用途请自己判断。

### 配置

所有设置都是环境变量；[`.env.example`](.env.example) 列出了每一项并附有注释。必填的几项：

| 设置 | 说明 |
|---|---|
| `ENGINE` | 定稿转写用的语音识别引擎。`qwen`（默认）只需要 `DASHSCOPE_API_KEY` |
| `DASHSCOPE_API_KEY` | 阿里云百炼的 key，北京区 |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | 任何支持工具调用和 JSON 模式的 OpenAI 兼容 `/chat/completions` 接口：用于摘要、待办、「问 AI」、每日回顾 |
| `UI_LANG` | 可选：大模型写摘要、待办、每日回顾和「问 AI」回答用的语言，`zh`（默认）或 `en`；`tenants.json` 里每人的 `lang` 优先 |

语音识别引擎：

| `ENGINE` | 服务商 | 需要 | 说话人 |
|---|---|---|---|
| `qwen` | 阿里云百炼，千问 Qwen-Audio 文件转写 | `DASHSCOPE_API_KEY` | 引擎给出，再用本地声纹修正 |
| `volc` | 火山引擎豆包 Seed-ASR 2.0 | `VOLC_ASR_KEY` | 本地声纹 |
| `stepfun-sse` | 阶跃星辰流式语音识别 | `STEPFUN_API_KEY` | 本地声纹 |
| `stepfun`, `stepfun-file` | 阶跃星辰文件模式 | `STEPFUN_API_KEY`，外加你自己的上传桥和文件模式语音识别服务（`ASR_*`） | 引擎给出，再用本地声纹修正 |
| `local` | 服务进程里的 sherpa-onnx X-ASR 中英模型，只出文字；远场录音比 `qwen` 差 | 什么都不用（模型约 136 MB，第一次用到时下载） | 本地声纹 |

`DRAFT_ENGINE=qwen-stream` 会在页面开着时把音频流式推给千问，边说边出字。`ENGINE_FALLBACK` 接手主引擎失败的窗口。

本地引擎与云端引擎的对比数据见 [docs/asr-benchmark.md](docs/asr-benchmark.md)。

大模型一律是外部服务，项目不打包、不运行大模型；想全在自己机器上跑，把 `LLM_BASE_URL` 指向你自己的 OpenAI 兼容服务（vLLM、llama.cpp、Ollama）即可，模型需要支持工具调用和 JSON 输出。

### 手机 App

- **安卓：** 用 `cd app && flutter build apk --release` 自己构建，或者从 [Releases 页面](https://github.com/suyiiyii/alldayrec/releases)（`apk-v*` 标签）下载 APK。服务器也可以在 `/app.apk` 提供更新。
- **iOS：** 不上架 App Store，需要自己用 Xcode 构建安装：把 `app/ios/Flutter/Signing.local.xcconfig.example` 复制成 `Signing.local.xcconfig`，填自己的 Team ID（以及一个你自己的 bundle id 后缀）。用免费 Apple ID 装的应用 7 天后会打不开，要重新安装一次；付费开发者账号没有这个限制。

连接 App：在网页里打开 **连接手机**（`/connect`），在 App 设置里点 **扫码登录** 扫它的二维码，或者手动输入服务器地址和 token。App 界面目前只有中文。

### 硬件（可选）

只用手机也可以。全天佩戴可以用开源的蓝牙录音设备：nRF52840 板子 + PDM 麦克风 + microSD 卡 + 小电池。它一直往卡里录，连上手机就同步，所以手机不在身边时也不会丢。

| 型号 | 板子 |
|---|---|
| `LR-X1` | Seeed XIAO nRF52840 Sense（自带麦克风）+ SD 模块 + 电池 |
| `LR-P3` | ProMicro nRF52840（兼容 nice!nano）+ PDM 麦克风 + SD 模块 + 开关，装在 3D 打印外壳里 |

固件、接线、构建（nRF Connect SDK v2.7.0）和空中升级：[`firmware/devkit/README.md`](firmware/devkit/README.md)（英文）。新板子可以用 [`firmware/selftest`](firmware/selftest) 检查。LR-P3 的零件、接线、焊接顺序和 3D 打印外壳（STL、STEP 和源码）见 [`hardware/lr-p3/README.md`](hardware/lr-p3/README.md)。固件最初基于 [Omi](https://github.com/BasedHardware/omi) devkit。

### 架构和目录

```
BLE recorder ──BLE──▶ phone app ──HTTP(S)──▶ server (FastAPI) ──▶ web UI, MCP, CLI
                      (or the phone's mic)    │ local: VAD, voiceprints (onnxruntime)
                                              │ cloud: ASR engine, OpenAI-compatible LLM
                                              ▼
                                       SQLite + original audio
```

| 目录 | 内容 |
|---|---|
| `server/` | 服务端：上传队列、VAD 和声纹、语音识别引擎、对话、摘要、MCP（Python 3.11，uv） |
| `web/` | 网页：今天、对话、人物、日历、实时转写、设备页（Vue，pnpm） |
| `app/` | iOS 和安卓手机 App：录手机麦克风或蓝牙录音设备、上传、固件升级（Flutter） |
| `firmware/devkit/` | nRF52840 板子的蓝牙录音设备固件（Zephyr / nRF Connect SDK），基于 Omi devkit |
| `firmware/selftest/`, `firmware/blerelay/` | 板子自检固件；测试用的 ESP32 蓝牙中继 |
| `hardware/lr-p3/` | LR-P3 录音设备的零件、接线和 3D 打印外壳 |
| `cli/` | `lr.py`：在终端或 Agent 里读你的对话 |
| `docs/` | 评测等补充文档 |

### 开发

```bash
# server/
uv run --with pytest pytest -q
# web/
pnpm install --frozen-lockfile && pnpm build
for f in *.check.ts; do node --experimental-strip-types "$f" || exit 1; done
# app/（用 pubspec.lock 里记录的 pub 源）
PUB_HOSTED_URL=https://pub.flutter-io.cn flutter pub get --enforce-lockfile
flutter analyze --no-pub && flutter test --no-pub -j 4
# 安卓 JVM 和 Swift 检查见 .github/workflows/app.yml；固件的主机端检查见 .github/workflows/firmware-check.yml
```

代码边界、CI 流程、发布构建及其密钥：[CONTRIBUTING.md](CONTRIBUTING.md)（英文）。

### 用 deploy.sh 部署（维护者）

除了 Docker，服务也可以作为 systemd 单元跑在 Linux 主机上，从 `origin/main` 的干净检出通过 ssh 部署。`server/deploy.sh` 会构建网页、拷贝发布包、备份每个实例的代码和每个租户的 `live.db`、安装（`uv sync`）、检查模型文件是否就位、逐个重启实例并检查；`server/deploy.sh check` 只读取正在运行的状态，`server/deploy.sh rollback` 把上一次部署替换掉的东西放回去。

```bash
export LR_DEPLOY_HOST=user@host                  # 必填：ssh / scp 的目标（用你自己的 ssh 配置和 agent）
export LR_DEPLOY_SHELL='sudo bash -s'            # 默认 `bash -s`：脚本通过管道送进去、以 root 运行的远程 shell
export LR_INSTANCES='/opt/alldayrec:alldayrec'   # 默认值：<目录>:<systemd 单元>，空格分隔，测试实例在前
server/deploy.sh                                 # 或 server/deploy.sh check | rollback
server/apk_release.sh app/build/app/outputs/flutter-apk/app-release.apk   # 发布手机从 /app.apk 下载的 APK
server/tenants.sh add <id>                       # 给最后一个实例加一个新租户（自己的 token 和目录）
```

每个实例是一个目录，里面有它的 `.env` 和 `tenants.json`，由对应的 systemd 单元运行。scp 会把发布包和 APK 放到主机上登录用户的 home 目录；如果远程 shell 的 `$HOME` 是另一个目录，用 `LR_TGZ` 和 `LR_APK` 指定它们在那里的路径。细节见每个脚本开头的注释。

## 关于项目

「LR」「lr」是项目的内部代号，硬件和代码里还在用：录音设备型号 `LR-P3`、固件工具 `lr.sh`、命令行 `lr.py`。

仓库公开前改写过 git 历史，去掉了作者私人部署的细节，所以历史很短；提交说明里的 `lr#123` 指的是以前私有仓库的 issue，不是这个仓库的。

## 许可

MIT，见 [LICENSE](LICENSE)。打包进来的第三方代码（Omi devkit、Opus、FireRedVAD 的后处理等）保留各自的许可：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=suyiiyii/alldayrec&type=Date)](https://star-history.com/#suyiiyii/alldayrec&Date)

[^lrd]: [爱搞机，安克 AI 录音豆评测（价格、AI 权益与会员、续航、音频导出、云端存储）](https://m.igao7.com/news/202601/M0rs0SQkAdD05EZY.html)
[^lrd2]: [52audio，安克 AI 录音豆（声纹、纪要与待办、AI 问答）](https://www.52audio.com/archives/267283.html)
[^xlq]: [极客公园，OPPO AI 心力球与白鲸 AI（不留原音、PCC 私密云、声纹、MCP、适配机型）](https://www.geekpark.net/news/372428)
[^xlq2]: [新浪科技，OPPO 心力球 499 元体验价](https://finance.sina.com.cn/tech/digi/2026-09-22/doc-inistexc1962351.shtml)
[^a1]: [量子位，DingTalk A1 发布（价格、会员、续航、存储）](https://www.qbitai.com/2025/09/331938.html)
[^a1b]: [钉钉 AI 听记（注册声纹后标注姓名）](https://www.dingtalk.io/zh/blog/ai-minutes-redefining-notes)
[^a1c]: [极客公园，DingTalk A1 体验（转写、纪要、章节、分析）](https://www.geekpark.net/news/353280)
[^plaud-dev]: [Plaud 设备对比（NotePin S 价格、续航）](https://www.plaud.ai/pages/compare-plaud-devices)
[^plaud-plan]: [Plaud AI 会员价格](https://www.plaud.ai/pages/plaud-ai-plan-pricing)
[^plaud-exp]: [Plaud 帮助中心，导出文件（MP3 / WAV）](https://support.plaud.ai/hc/en-us/articles/50835453223705-Export-files)
[^plaud-rn]: [Plaud 更新日志（查找替换说话人名）](https://www.plaud.ai/pages/plaud-release-notes)
[^plaud-app]: [Plaud App 3.0（Highlights、Summary、To-dos、Ask）](https://www.plaud.ai/blogs/news/plaud-app-3-design-intelligence)
[^plaud-mcp]: [Plaud MCP 文档](https://docs.plaud.ai/plaud-mcp-cli/mcp)
[^limitless]: [TechInformed，Meta 收购 Limitless，吊坠停止向新用户销售（2025-12-09）](https://techinformed.com/meta-acquires-limitless-pendant-users-moved-to-free-unlimited-plan/)
[^bee]: [The Next Web，Amazon 重新推出 Bee（价格、订阅、音频处理后删除）](https://thenextweb.com/news/amazon-bee-frontier-eclipse-subscription)
[^omi-price]: [Omi 价格](https://www.omi.me/pages/pricing)
[^omi-docs]: [Omi 文档（硬件、固件、App、后端全部开源，可自己部署，说话人识别）](https://docs.omi.me/doc/get_started/introduction)
[^omi-audio]: [Omi 后端，`store_recording_permission` 默认关闭](https://github.com/BasedHardware/omi/blob/main/backend/database/users.py)
[^omi-home]: [Omi 的 README（摘要和待办）](https://github.com/BasedHardware/omi)；[首页源码（Daily Recaps）](https://github.com/BasedHardware/omi/blob/main/app/lib/pages/home/home_content.dart)
[^omi-mcp]: [Omi MCP 文档](https://docs.omi.me/doc/developer/MCP)
