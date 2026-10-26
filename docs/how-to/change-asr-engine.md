---
type: how-to
description: "用 .env 里的 ENGINE 更换定稿用的语音识别引擎（默认千问），或者用 ENGINE=local 在本机转写、不需要语音识别 key。也讲初稿和兜底引擎。"
---

# 如何更换语音识别引擎

定稿用哪个语音识别引擎由 `.env` 里的 `ENGINE` 决定，默认是 `qwen`（阿里云百炼）。各引擎的差别见[架构与数据流](../explanation/architecture.md#两层转写)和[语音识别评测](../explanation/asr-benchmark.md)。

## 换引擎

1. 在 `.env` 里把 `ENGINE` 改成下表中的一个，并填上它需要的 key：

   | `ENGINE` | 需要 |
   |---|---|
   | `qwen` | `DASHSCOPE_API_KEY` |
   | `volc` | `VOLC_ASR_KEY` |
   | `stepfun-sse` | `STEPFUN_API_KEY` |
   | `stepfun` | `STEPFUN_API_KEY`，以及你自己的上传桥和文件模式语音识别服务：`ASR_UPLOAD_BRIDGE_URL`、`ASR_UPLOAD_BRIDGE_TOKEN`、`ASR_SERVICE_URL`、`ASR_SERVICE_BASIC_AUTH` |
   | `stepfun-file` | 你自己的上传桥和文件模式语音识别服务：`ASR_UPLOAD_BRIDGE_URL`、`ASR_UPLOAD_BRIDGE_TOKEN`、`ASR_SERVICE_URL`、`ASR_SERVICE_BASIC_AUTH` |
   | `local` | 不需要 key |

2. 重启服务器。Docker 里运行 `docker compose up -d`，Compose 会用新的 `.env` 重建容器。

缺 key 时服务器不会启动，日志里会写 `not set: <缺的变量> (see .env.example)`。`ENGINE` 写错时，日志里会列出可选的值。

大模型的三项（`LLM_BASE_URL`、`LLM_API_KEY`、`LLM_MODEL`）不管用哪个引擎都要填。

## 不用任何语音识别 key：`ENGINE=local`

`local` 在服务器进程里用 sherpa-onnx 跑 X-ASR 中英模型，只出文字，说话人靠服务器自己的声纹区分；远场录音的效果比 `qwen` 差。

它要装 `local` 这一组依赖：Docker 镜像里已经有了；用 uvx 或 pip 运行时装 `alldayrec[local]`（见[如何用 uvx 或 pip 运行](./run-uvx.md)）；从源码运行时用 `uv sync --extra local`。

第一段音频到达时，服务器会下载约 136 MB 的模型包，所以第一条转写要等一分钟左右。下载慢的话，把 `LOCAL_ASR_URL` 设成同一个模型包在镜像站上的地址；文件按 sha256 校验。

## 初稿和兜底

- `DRAFT_ENGINE`：页面开着时出初稿用的引擎，空着就和 `ENGINE` 一样。设成 `qwen-stream` 会在页面开着时把音频流式推给千问，边说边出字（需要 `DASHSCOPE_API_KEY`）。`qwen-stream` 只能用作 `DRAFT_ENGINE`。
- `ENGINE_FALLBACK`：主引擎在某个窗口上失败（402、451、5xx、超时）时改用这个引擎；空着就稍后重试。

所有相关变量见[配置项](../reference/configuration.md)。
