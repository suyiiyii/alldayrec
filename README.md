<div align="center">

# alldayrec（全天录）

### 开源录音豆，私有第二大脑。

开源版 Plaud 与心力球：全天录音私有化，每小时转写只要五分钱。

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/alldayrec)](https://pypi.org/project/alldayrec/)
[![Release](https://img.shields.io/github/v/release/suyiiyii/alldayrec?filter=v*&label=release)](https://github.com/suyiiyii/alldayrec/releases)
[![Android APK](https://img.shields.io/github/v/release/suyiiyii/alldayrec?filter=apk-v*&label=Android%20APK)](https://github.com/suyiiyii/alldayrec/releases)
[![Docker image](https://img.shields.io/badge/ghcr.io-alldayrec-2496ed?logo=docker&logoColor=white)](https://github.com/suyiiyii/alldayrec/pkgs/container/alldayrec)
[![GitHub stars](https://img.shields.io/github/stars/suyiiyii/alldayrec?style=flat)](https://github.com/suyiiyii/alldayrec)

**中文** | [English](README.en.md)

[文档](https://suyiiyii.github.io/alldayrec/) · [快速开始](#快速开始) · [和市面产品比](https://suyiiyii.github.io/alldayrec/explanation/intro.html#和市面产品比) · [硬件](#硬件) · [路线图](https://suyiiyii.github.io/alldayrec/explanation/roadmap.html)

让 AI Agent 帮你部署：[SKILL.md](https://suyiiyii.github.io/alldayrec/SKILL.md)

`#自部署` `#全天录音` `#说话人识别` `#MCP` `#Plaud替代` `#开源硬件`

</div>

全天录是一个开源的全天录音设备加服务器，跑在你自己的机器上。戴一个录音设备（或者只带手机）从早录到晚，你的服务器在一分钟内把音频变成带说话人的转写，再整理成一天的时间线：对话摘要、认出的人、待办和每日回顾。原始录音一直留着，每句话都能点开回放核对；你的 Agent 通过 MCP 直接查你的每一天。没有订阅，对话也不放在别人的云上。（录音豆、心力球等产品名称归各自所有者，本项目与它们没有关联。）

<div align="center">

![实时字幕](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-20261024.gif)

| ![Today](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/today-20261024.png) | ![One conversation](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversation-20261024.png) |
|:--:|:--:|
| 今天：到期待办、昨天的回顾、今天的对话 | 一段对话的摘要：待办、决定、每个人的要点 |

页面开着时的实时字幕，以及网页在手机上的样子。示例数据都是编的；[更多截图](https://suyiiyii.github.io/alldayrec/explanation/intro.html#界面一览)。

</div>

## 能做什么

- **全天录音**：手机 App 录手机麦克风，或者连一个蓝牙录音设备；离线时在设备上排队，回来后补传。
- **两层转写**：定稿交给云端语音识别（默认阿里云百炼千问，约 ¥0.05/小时音频），页面开着时几秒内出初稿；也可以 `ENGINE=local` 在本机转写。
- **认人**：语音检测和声纹在本机 CPU 上跑，认得出你自己，记得你给别人起的名字。
- **记忆**：对话摘要、待办和截止日期、每日回顾、全文搜索，「问 AI」的回答带出处对话的链接。
- **开放**：网页、远程 MCP 端点（Claude Code、Codex、Cursor）、命令行；数据就是 SQLite 加原始录音，自己就能读。
- **什么音源都能接**：已有的录音文件可以通过 HTTP 接口[导入](https://suyiiyii.github.io/alldayrec/how-to/import-recordings.html)。

为什么做、和录音豆、心力球、Plaud、Omi 等产品的对比：[简介与定位](https://suyiiyii.github.io/alldayrec/explanation/intro.html)。

## 快速开始

需要一把[阿里云百炼（DashScope）](https://bailian.console.aliyun.com/)北京区的 API key，语音识别和大模型可以共用这一把；用别的引擎见[如何更换语音识别引擎](https://suyiiyii.github.io/alldayrec/how-to/change-asr-engine.html)。三种跑法任选一种。

### 用 uvx（最省事）

需要 [uv](https://docs.astral.sh/uv/)（Python 3.11–3.14，没有的话 uv 自己下载）和 ffmpeg。在一个空目录里建 `.env`：

```ini
DASHSCOPE_API_KEY=<你的百炼 key>
LLM_API_KEY=<同一把百炼 key>
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

运行 `uvx --env-file .env alldayrec`，在这台电脑上打开日志里 `first start: ...` 那一行的链接。pip 安装、数据目录和让手机连上：[如何用 uvx 或 pip 运行](https://suyiiyii.github.io/alldayrec/how-to/run-uvx.html)。

### 用 Docker

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec
cp .env.example .env    # 填 DASHSCOPE_API_KEY 和 LLM_API_KEY
mkdir -p data && echo '[{"id": "main", "token": "'$(openssl rand -hex 16)'", "data": "/data/main", "recordings": "/data/main/recordings"}]' > data/tenants.json && chmod 600 data/tenants.json
docker compose up -d && docker compose logs -f
```

打开 `http://<服务器 IP>:8770/?token=<你的 token>`（token 在 `data/tenants.json` 里）。端口、时区、镜像版本和公网访问：[如何用 Docker 部署](https://suyiiyii.github.io/alldayrec/how-to/deploy-docker.html)。

### 从源码运行

需要 uv、ffmpeg、Node 22 和 pnpm。

```bash
git clone https://github.com/suyiiyii/alldayrec && cd alldayrec && cp .env.example .env    # 填 key
(cd web && pnpm install && pnpm build)
cd server && uv sync && set -a && . ../.env && set +a && uv run alldayrec
```

详见[如何从源码运行](https://suyiiyii.github.io/alldayrec/how-to/deploy-without-docker.html)。服务跑起来以后：[让手机连上服务器](https://suyiiyii.github.io/alldayrec/how-to/remote-access.html)，[安装并连接手机 App](https://suyiiyii.github.io/alldayrec/how-to/install-app.html)。

## 硬件

只用手机也可以。全天佩戴可以自己做开源的蓝牙录音设备：`LR-X1`（Seeed XIAO nRF52840 Sense）或 `LR-P3`（ProMicro nRF52840，装在 3D 打印外壳里），一直往 SD 卡里录，连上手机就同步。
零件、接线和固件：[录音设备硬件](https://suyiiyii.github.io/alldayrec/reference/hardware.html)、[`hardware/lr-p3/`](hardware/lr-p3/README.md)、[`firmware/devkit/`](firmware/devkit/README.md)。

## 文档

[suyiiyii.github.io/alldayrec](https://suyiiyii.github.io/alldayrec/)，中英文：

- [教程](https://suyiiyii.github.io/alldayrec/tutorial/first-run.html)：第一次跑起来，看到第一句转写。
- [操作指南](https://suyiiyii.github.io/alldayrec/how-to/run-uvx.html)：部署、让手机连上、绑定录音设备、导入录音、接入 MCP、多人使用、升级和备份。
- [参考](https://suyiiyii.github.io/alldayrec/reference/configuration.html)：配置项、HTTP API、MCP 工具、命令行、数据目录、模型文件、硬件。
- [说明](https://suyiiyii.github.io/alldayrec/explanation/intro.html)：简介与定位、架构与数据流、隐私、语音识别评测。
- [局限与路线图](https://suyiiyii.github.io/alldayrec/explanation/roadmap.html)。

## 参与贡献

欢迎提 [issue](https://github.com/suyiiyii/alldayrec/issues) 和 PR。本地检查和提交说明见[如何参与开发](https://suyiiyii.github.io/alldayrec/how-to/contribute.html)，代码边界、CI 和发布构建见 [CONTRIBUTING.md](CONTRIBUTING.md)（英文）。

## 隐私与法律

录音会录到别人。请告知对方，需要时取得同意，遵守当地法律；有些地方要求所有参与方都同意。数据在你自己的服务器上，音频和文字会发给你选的语音识别服务和大模型，见[隐私与数据去向](https://suyiiyii.github.io/alldayrec/explanation/privacy.html)。

## 许可

MIT，见 [LICENSE](LICENSE)。打包进来的第三方代码（Omi devkit、Opus、FireRedVAD 的后处理等）保留各自的许可：[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## Star history

[![Star History Chart](https://api.star-history.com/svg?repos=suyiiyii/alldayrec&type=Date)](https://star-history.com/#suyiiyii/alldayrec&Date)
