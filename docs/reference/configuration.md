---
type: reference
---

# 配置项

服务器的设置分两处：`tenants.json` 列出它为哪些人服务，环境变量（通常写在 `.env` 里）设置其余一切。服务器缺必填项时拒绝启动，并在日志里写出缺了哪些。

::: info 适用版本
这一页按 main 分支写。v0.1.0 没有 `UI_LANG` 和 `tenants.json` 的 `lang` 字段，其余各项相同。
:::

## tenants.json

一个 JSON 数组，每个人（租户）一条。权限必须是 600。

```json
[{"id": "main", "token": "<token>", "data": "/data/main", "recordings": "/data/main/recordings"}]
```

| 字段 | 说明 |
|---|---|
| `id` | 租户名，用在日志和监控指标里 |
| `token` | App 和网页发来的 token（`Authorization: Bearer` 或 cookie） |
| `data` | 数据目录：`live.db` 等，见[数据目录](./data.md) |
| `recordings` | 录音目录 |
| `lang` | 可选。大模型给这个人写的内容（摘要、待办、实时分析、每日回顾、「问 AI」回答等）用的语言：`zh` 或 `en`。不写时用 `UI_LANG`，`UI_LANG` 也没有时是 `zh`。在网页「我的」页切换语言（`PUT /api/lang`）之后，以切换的结果为准，记在这个人的数据库里，重启后也保留 |

- 位置：`server/` 的上一级目录。Docker 镜像里它链接到 `/data/tenants.json`，也就是宿主机的 `data/tenants.json`。
- `id`、`token`、`data`、`recordings` 在租户之间都不能重复。
- 只有一个租户时 token 可以为空，这时不做认证，服务器只能放在 `127.0.0.1` 上。有多个租户时每个都必须有 token。
- 服务器只在启动时读这个文件。

## .env

[`.env.example`](https://github.com/suyiiyii/alldayrec/blob/main/.env.example) 列出了下面每一项并附有注释。「示例值」是 `.env.example` 里写的值，复制成 `.env` 后就是你的起点；「不设时」是变量完全不存在时代码里的默认值。

`.env` 里的注释要单独占一行：Docker Compose 会把 `VAR=   # 注释` 读成值 `# 注释`。

### 必填：语音识别

| 变量 | 示例值 | 不设时 | 说明 |
|---|---|---|---|
| `ENGINE` | `qwen` | `qwen` | 定稿层的语音识别引擎：`qwen`、`volc`、`stepfun-sse`、`stepfun`、`stepfun-file`、`local`。见[如何更换语音识别引擎](../how-to/change-asr-engine.md) |
| `DASHSCOPE_API_KEY` | 空 | — | 阿里云百炼（DashScope）的 key，北京区。`qwen` 和 `qwen-stream` 用 |

### 必填：大模型

用于对话摘要、待办、「问 AI」和每日回顾。可以是任何支持工具调用和 `response_format` 为 `json_object` 的 OpenAI 兼容 `POST /chat/completions` 接口：云服务，或者你自己运行的服务（vLLM、llama.cpp、Ollama）。

| 变量 | 示例值 | 不设时 | 说明 |
|---|---|---|---|
| `LLM_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 必填 | 接口地址，服务器在后面加 `/chat/completions` |
| `LLM_API_KEY` | 空 | 必填 | 接口的 key；用示例的百炼兼容模式时和 `DASHSCOPE_API_KEY` 是同一把 |
| `LLM_MODEL` | `qwen-plus` | 必填 | 模型名 |

### 可选

| 变量 | 示例值 | 不设时 | 说明 |
|---|---|---|---|
| `MODELS_DIR` | `/opt/alldayrec/models` | `/opt/alldayrec/models` | 模型文件目录，绝对路径、可写；缺的文件启动时下载到这里。Docker Compose 里固定为 `/data/models` |
| `PORT` | `8770` | `8770` | 监听端口。Docker Compose 里固定为 `8770` |
| `HOST` | `127.0.0.1` | `127.0.0.1` | 监听地址；反向代理在另一台机器上时设 `0.0.0.0`。Docker Compose 里固定为 `0.0.0.0` |
| `DRAFT_ENGINE` | 空 | 同 `ENGINE` | 初稿层（页面开着时）的引擎；另可设 `qwen-stream`：页面开着时把音频流式推给千问（百炼 qwen-audio-3.1-asr-flash-streaming，用 `DASHSCOPE_API_KEY`），边说边出字 |
| `ENGINE_FALLBACK` | 空 | 无 | `ENGINE` 或 `DRAFT_ENGINE` 在某个窗口上失败（402、451、5xx、超时）时改用的引擎；空着就稍后重试 |
| `VOLC_ASR_KEY` | 空 | — | 火山方舟 Agent Plan 的 API key，`volc` 用 |
| `STEPFUN_API_KEY` | 空 | — | 阶跃星辰的 key，`stepfun`、`stepfun-sse` 用 |
| `STEPFUN_SSE_URL` | 空 | `https://api.stepfun.com/v1/audio/asr/sse` | 阶跃 SSE 接口；默认是按量付费的地址，Step Plan 用 `.../step_plan/v1/audio/asr/sse` |
| `LOCAL_ASR_URL` | 空 | k2-fsa/sherpa-onnx 的 GitHub Release | `local` 的模型包（约 136 MB）从哪里下载；镜像站上同一个模型包也行（按 sha256 校验） |
| `UI_LANG` | `zh` | `zh` | 大模型写的内容（摘要、待办、实时分析、每日回顾、「问 AI」回答等）用的语言：`zh` 或 `en`。`tenants.json` 里的 `lang` 优先。没选过语言的浏览器打开网页时也用它；App 的界面不受影响 |
| `RECAP_AT` | `10:00` | `10:00` | 每天这个时刻写昨天的回顾；设为空就不写 |
| `TZ` | `Asia/Shanghai` | 系统的时区 | 对话日期和 `RECAP_AT` 用的时区。Docker 容器的系统时区是 UTC；`TZ=` 设为空也是 UTC |
| `REFINE_EVERY_S` | `20` | `30` | 初稿层：这么久没有停顿就做一次 |
| `TURN_GAP_S` | `0` | `1.0` | 大于 0 时，把同一个说话人间隔小于这个秒数的相邻句子合成一行；0 保留引擎原来的分句 |
| `FIRMWARE_TOKEN` | 空 | 无 | 上传录音设备固件（`firmware/devkit/lr_release.py`）时除租户 token 外还需要的 token；空着就不能上传 |
| `VM_INGEST_URL` | 空 | 无 | VictoriaMetrics 的导入地址（比如 `https://example.com/api/v1/import/prometheus`），每分钟推送 `lr_*` 指标；空着就不推 |
| `VM_INGEST_USER`、`VM_INGEST_PASSWORD` | 空 | 空 | 推送指标用的 basic auth |

### 高级：只有 `stepfun` 和 `stepfun-file` 用

`stepfun` 或 `stepfun-file` 出现在 `ENGINE`、`DRAFT_ENGINE` 或 `ENGINE_FALLBACK` 里时，下面四个 `ASR_*` 都必填。

| 变量 | 示例值 | 不设时 | 说明 |
|---|---|---|---|
| `ASR_UPLOAD_BRIDGE_URL` | 空 | — | 你自己的上传桥：`POST {filename, content_type}`，返回 `{upload_url, download_url}` |
| `ASR_UPLOAD_BRIDGE_TOKEN` | 空 | — | 上传桥的 token |
| `ASR_SERVICE_URL` | 空 | — | 你自己的文件模式语音识别服务（带说话人和词时间）：`POST /audio/transcriptions {audio_url, model}`、`GET /jobs/{id}` |
| `ASR_SERVICE_BASIC_AUTH` | 空 | — | 这个服务的 basic auth，写成 `user:password` |
| `FILE_MODELS` | `stepaudio-2.5-asr-8006,doubao-seedasr-2.0` | 同示例值 | 文件模式引擎，按顺序；第一个拒绝的窗口交给第二个 |

### 只有离线脚本用

| 变量 | 用在 | 说明 |
|---|---|---|
| `LIVE_DATA`、`RECORDINGS_DIR` | `owner.py`、`replay_case.py` | 一个租户的数据目录和录音目录 |
| `OWNER_LABELS` | `owner.py` | 已知是本人声音的窗口（jsonl） |
| `CASES_DIR` | `eval_all.sh` | 标注好的评测用例目录 |
| `EMB_MODEL`、`SEG_MODEL`、`OWNER_SURE`、`LINK_MIN`、`OWNER_DEFAULT_DEVICE` | `replay_case.py`、`service_case.py` | 见这两个脚本 |

## 其他环境变量

服务器还读下面这些变量，`.env.example` 里没有列出。它们是调参用的，默认值就是作者在用的值。

| 变量 | 不设时 | 说明 | 源码 |
|---|---|---|---|
| `SLICE_FOREGROUND_S`、`SLICE_BACKGROUND_S`、`SLICE_SCREEN_OFF_S` | `1`、`20`、`60` | App 在前台、后台、熄屏（安卓）时每块音频的秒数，App 通过 `/api/client-config` 读取 | `routes_upload.py` |
| `REFINE_WINDOW_S` | `240` | 初稿层一次最多处理的音频秒数 | `device_pipeline.py` |
| `LIVE_EVERY_S` | `120` | 进行中的对话新增这么多秒语音后，重做实时分析 | `device_pipeline.py` |
| `SQUEEZE_KEEP_S` | `3` | 送给引擎前，长于这个秒数的静音剪到这么长；0 表示整段送 | `asr.py` |
| `SQUEEZE_VAD` | `0.3` | 判断上面那些静音用的 VAD 阈值 | `segmenter.py` |
| `SUMMARY_WORKERS` | `4` | 同时写摘要的数量 | `summaries.py` |
| `QWEN_ASR_MODEL` | `qwen-audio-3.1-asr-flash-filetrans` | `qwen` 引擎用的百炼模型 | `qwen_asr.py` |
| `ASR_CALLS`、`INFER_CALLS`、`DB_CALLS`、`MEDIA_CALLS` | `1`、`2`、`8`、`16` | 同时进行的本地语音识别、声纹计算、全库查询、音频编解码的数量 | `threads.py` |
| `INFER_THREADS` | `4` | 每次声纹计算用的 onnxruntime 线程数 | `infer.py` |
| `VAD_GAIN_MAX_DB` | `16` | 给 VAD 的特征最多提升多少 dB（只影响语音检测，不影响送给引擎和回放的音频）；0 关闭 | `infer.py` |
| `ASR_GAIN_MAX_DB` | `16` | 送给引擎的音频最多提升多少 dB；0 原样送 | `squeeze.py` |
| `DEHUM` | `1` | 读取录音设备的音频时去掉它 10 ms 周期的哼声；0 关闭 | `audio.py` |
| `SPEAKER_LINK`、`SPEAKER_SUGGEST`、`MERGE_SUGGEST` | `0.5`、`0.6`、`0.45` | 人物页的声纹相似度阈值：并入匿名组、建议给名字、建议合并同一段对话里的两个标签 | `routes_speakers.py` |
| `DIAR_TH`、`DIAR_JOIN`、`DIAR_GUARD`、`PEOPLE_MIN` | `0.5`、`0.35`、`0.6`、`0.7` | 一段对话内区分说话人的阈值 | `diarist.py` |
| `SELFVOICE_POP_P` | `0.95` | 「哪个是你的声音」卡片自动弹出前，第一个候选需要达到的概率 | `selfvoice.py` |
| `LOOP_STALL_MS` | 不检测 | 事件循环被卡住超过这么多毫秒时，日志里记一行卡了多久、卡在哪个函数（每秒最多一行，其间的次数一并记上）；排查卡顿时设，比如 `100` | `monitor.py` |
