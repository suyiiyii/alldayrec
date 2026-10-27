---
type: explanation
---

# 架构与数据流

AllDayRec 是一个 Python 服务器加几个客户端。音频从录音设备或手机进来，服务器在本机做语音检测和声纹，语音识别用云端或本机引擎，摘要交给你配置的大模型，结果存进 SQLite，原始录音一直留在磁盘上。

<a class="only-light" href="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-wide-zh-light-name-20261026.png">
<picture>
  <source media="(max-width: 600px)" srcset="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-narrow-zh-light-name-20261026.png">
  <img alt="架构图：开源录音设备、飞书录音豆和手机麦克风经手机 App 上传，浏览器的 /record 页直接上传；自部署服务器先存下音频，再做语音检测、语音识别、认人，大模型写摘要；网页、MCP 和命令行读取结果" src="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-wide-zh-light-name-20261026.png">
</picture>
</a>
<a class="only-dark" href="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-wide-zh-dark-name-20261026.png">
<picture>
  <source media="(max-width: 600px)" srcset="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-narrow-zh-dark-name-20261026.png">
  <img alt="架构图：开源录音设备、飞书录音豆和手机麦克风经手机 App 上传，浏览器的 /record 页直接上传；自部署服务器先存下音频，再做语音检测、语音识别、认人，大模型写摘要；网页、MCP 和命令行读取结果" src="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-wide-zh-dark-name-20261026.png">
</picture>
</a>

图的源文件是 [`docs/diagrams/architecture.drawio`](https://github.com/suyiiyii/alldayrec/blob/main/docs/diagrams/architecture.drawio)，用 draw.io 打开编辑。

## 组成部分

| 目录 | 内容 |
|---|---|
| `server/` | 服务端：上传队列、VAD 和声纹、语音识别引擎、对话、摘要、MCP（Python 3.11，uv） |
| `web/` | 网页：时间线、实时、人物、我的，以及对话、日历、待办、设备页（Vue，pnpm） |
| `app/` | iOS 和安卓手机 App：录手机麦克风或蓝牙录音设备、上传、固件升级（Flutter） |
| `firmware/devkit/` | nRF52840 板子的蓝牙录音设备固件（Zephyr / nRF Connect SDK），基于 Omi devkit |
| `firmware/selftest/`、`firmware/blerelay/` | 板子自检固件；测试用的 ESP32 蓝牙中继 |
| `hardware/lr-p3/` | LR-P3 录音设备的零件、接线和 3D 打印外壳 |
| `cli/` | `lr.py`：在终端或 Agent 里读你的对话 |

## 音频怎么进来

录音设备一直往 SD 卡里写，连着手机时把同一份记录同时推给手机；断开期间的部分，手机回来后从卡上读回来补齐。手机 App 把音频切成 Opus 短块上传到 `POST /api/chunks`；离线时块在手机上排队，之后再传。

服务器把每一块先存进磁盘，再按时间顺序交给这台设备的处理流程。所以一个断网很久的客户端回来后可以一次把积压全传上来，别的设备不受影响。能 POST 音频文件的东西都能当音源，这也是[导入已有录音](../how-to/import-recordings.md)的方式。

## 两层转写

服务器在本机用 FireRedVAD 找出哪里有人说话，然后分两层转写：

- **定稿层** 一直在跑。它在停顿处把音频切开，每段最长 5 分钟，每段只送一次给语音识别引擎（`ENGINE`），结果就是最终的转写。
- **初稿层** 只在有网页开着时跑。它在停顿处（一直没有停顿时每隔 `REFINE_EVERY_S` 秒）把新的音频送给 `DRAFT_ENGINE`，几秒内出字；`DRAFT_ENGINE=qwen-stream` 时改为把音频持续流式推给千问，边说边出字。初稿只在定稿还没覆盖的时间段显示，定稿到了就被替换。

文字和分句来自语音识别引擎；本机负责语音检测和说话人身份，各引擎的说话人从哪里来见下表。送给引擎之前，长于 3 秒的静音会被剪到 3 秒（`SQUEEZE_KEEP_S`）。

各引擎的说话人来源不同：

| `ENGINE` | 服务商 | 说话人 |
|---|---|---|
| `qwen` | 阿里云百炼，千问 Qwen-Audio 文件转写 | 引擎给出，再用本地声纹修正 |
| `volc` | 火山引擎豆包 Seed-ASR 2.0 | 本地声纹 |
| `stepfun-sse` | 阶跃星辰流式语音识别 | 本地声纹 |
| `stepfun`、`stepfun-file` | 阶跃星辰文件模式 | 引擎给出，再用本地声纹修正 |
| `local` | 服务器进程里的 sherpa-onnx X-ASR 中英模型，只出文字 | 本地声纹 |

本地引擎和云端引擎的准确率对比见[语音识别评测](./asr-benchmark.md)。

## 认人

声纹模型 ReDimNet2 在本机 CPU 上跑。服务器认得出「我」（录音者本人），在一段对话里把其他人分成 S1、S2…；太短、认不出来的句子标成 S?。你在网页上给某个标签起了名字，之后的对话里同一个声音就会被认出来。

## 对话、摘要和回顾

句子按时间归成对话：5 分钟没有人说话、录音设备停了，或者一段对话满 4 小时，它就结束。说话少于 10 秒的对话不保留成一段对话（句子还在转写里）。

只按时间分，商场、路上周围的人声会把几段互不相干的对话连成一段。所以较长的对话（`SPLIT_MIN_S`，默认 10 分钟）写摘要前，再让大模型按内容看一遍：从某处起换了人、换了事，而且那里确实停顿过，就从那里切成几段，各写各的摘要。切开的地方之后不会被迟到音频重新合并。

对话结束后，服务器用你配置的大模型写摘要：标题、摘要、每个人的要点、待办（带截止日期）、决定和标签。说话不到 60 秒的对话不调大模型，标题取第一句。每天 `RECAP_AT`（默认 10:00）写前一天的回顾。

「问 AI」用同一个大模型回答关于你过去的问题：最近几天的摘要放在提示词里，更早的或者更细的内容通过查询数据库的工具找到，每个说法都带出处对话的 id。

## 谁在读这些数据

网页、App 里的网页、[MCP 端点](../reference/mcp.md)和[命令行](../reference/cli.md)读的都是同一个租户的数据库。数据库是 SQLite，你也可以自己直接读，表结构见[数据目录](../reference/data.md)。

一台服务器可以服务好几个人，每个人一个 token、一个数据库和一个录音目录（`tenants.json`），在同一个进程里互相隔离。
