---
type: reference
---

# Configuration

The server's settings live in two places: `tenants.json` lists the people it serves, and environment variables (usually in `.env`) set everything else. When a required setting is missing, the server refuses to start and writes the missing ones to its log.

::: info Version
This page follows the main branch. v0.1.0 has no `UI_LANG` and no `lang` field in `tenants.json`; up to v0.2.0, `LOOP_STALL_MS` is off by default. Everything else is the same.
:::

## tenants.json

A JSON array with one entry per person (tenant). Its permissions must be 600.

```json
[{"id": "main", "token": "<token>", "data": "/data/main", "recordings": "/data/main/recordings"}]
```

| Field | Description |
|---|---|
| `id` | The tenant name, used in logs and metrics |
| `token` | The token the app and the web pages send (`Authorization: Bearer` or a cookie) |
| `data` | The data directory: `live.db` and more, see [Data layout](./data.md) |
| `recordings` | The recordings directory |
| `lang` | Optional. The language the LLM writes this person's content in (summaries, to-dos, live analysis, the daily recap, "ask AI" answers and more): `zh` or `en`. Without it, `UI_LANG` applies, and without `UI_LANG`, `zh`. Once the language is switched on the web page 「我的」 (Me) (`PUT /api/lang`), that choice wins: it is kept in this person's database and survives a restart |

- Location: `DATA_DIR/tenants.json` (see `DATA_DIR` below). In the Docker image `DATA_DIR` is `/data`, which makes it `data/tenants.json` on the host.
- When the file is missing, the server's first start makes one: one tenant `main` with a random token and the directories `DATA_DIR/main` and `DATA_DIR/main/recordings`, and logs the link that signs you in, `http://127.0.0.1:<PORT>/connect?token=<token>`.
- `id`, `token`, `data` and `recordings` must each be unique across tenants.
- With a single tenant the token can be empty. The server then skips authentication and must stay on `127.0.0.1`. With several tenants, each one needs a token.
- The server reads this file only at startup.

## .env

[`.env.example`](https://github.com/suyiiyii/alldayrec/blob/main/.env.example) lists every variable below, each with a comment. "Example value" is the value in `.env.example`, your starting point once you copy it to `.env`; "When unset" is the default in the code when the variable doesn't exist at all.

Put each comment in `.env` on a line of its own: Docker Compose reads `VAR=   # comment` as the value `# comment`.

### Required: ASR

| Variable | Example value | When unset | Description |
|---|---|---|---|
| `ENGINE` | `qwen` | `qwen` | The ASR engine of the final layer: `qwen`, `volc`, `stepfun-sse`, `stepfun`, `stepfun-file`, `local`. See [Change the ASR engine](../how-to/change-asr-engine.md) |
| `DASHSCOPE_API_KEY` | empty | — | Alibaba Cloud Bailian (DashScope) key, Beijing region. Used by `qwen` and `qwen-stream` |

### Required: LLM

Used for conversation summaries, to-dos, "ask AI" and the daily recap. Any OpenAI-compatible `POST /chat/completions` endpoint that supports tool calls and `response_format` `json_object` works: a cloud service, or one you run yourself (vLLM, llama.cpp, Ollama).

| Variable | Example value | When unset | Description |
|---|---|---|---|
| `LLM_BASE_URL` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | required | The endpoint; the server appends `/chat/completions` |
| `LLM_API_KEY` | empty | required | The endpoint's key; with the example's Bailian compatible mode it is the same key as `DASHSCOPE_API_KEY` |
| `LLM_MODEL` | `qwen-plus` | required | The model name |

### Optional

| Variable | Example value | When unset | Description |
|---|---|---|---|
| `DATA_DIR` | empty | the user data directory: `~/.local/share/alldayrec` on Linux (`alldayrec` under `XDG_DATA_HOME` when that is set), `~/Library/Application Support/alldayrec` on macOS | The directory `tenants.json` is in; without `MODELS_DIR` the model files go in its `models`, and the tenant a first start makes in its `main`. Fixed at `/data` in Docker |
| `MODELS_DIR` | empty | `DATA_DIR/models` | The model files directory, an absolute, writable path; missing files are downloaded here at startup. Fixed at `/data/models` in Docker |
| `PORT` | `8770` | `8770` | The port to listen on. Docker Compose fixes it at `8770` |
| `HOST` | `127.0.0.1` | `127.0.0.1` | The address to listen on; set `0.0.0.0` when the reverse proxy runs on another machine. Docker Compose fixes it at `0.0.0.0` |
| `DRAFT_ENGINE` | empty | same as `ENGINE` | The engine of the draft layer (while a page is open); it can also be `qwen-stream`: while a page is open, the audio streams to Qwen (Bailian qwen-audio-3.1-asr-flash-streaming, with `DASHSCOPE_API_KEY`), so words show as they are said |
| `ENGINE_FALLBACK` | empty | none | The engine used when `ENGINE` or `DRAFT_ENGINE` fails on a window (402, 451, 5xx, timeout); empty means a retry later |
| `VOLC_ASR_KEY` | empty | — | The API key of a Volcengine Ark Agent Plan, used by `volc` |
| `STEPFUN_API_KEY` | empty | — | StepFun key, used by `stepfun` and `stepfun-sse` |
| `STEPFUN_SSE_URL` | empty | `https://api.stepfun.com/v1/audio/asr/sse` | The StepFun SSE endpoint; the default is the pay-as-you-go address; for Step Plan use `.../step_plan/v1/audio/asr/sse` |
| `LOCAL_ASR_URL` | empty | the k2-fsa/sherpa-onnx GitHub Release | Where `local` downloads its model tarball (about 136 MB) from; the same tarball on a mirror works too (sha256-checked) |
| `UI_LANG` | `zh` | `zh` | The language of what the LLM writes (summaries, to-dos, live analysis, the daily recap, "ask AI" answers and more): `zh` or `en`. A `lang` in `tenants.json` wins. The web pages use it too in a browser that hasn't picked a language; the app's UI doesn't change |
| `RECAP_AT` | `10:00` | `10:00` | The time of day the server writes yesterday's recap; set it empty for no recap |
| `TZ` | `Asia/Shanghai` | the system time zone | The time zone for conversation dates and `RECAP_AT`. A Docker container's system time zone is UTC; an empty `TZ=` means UTC too |
| `REFINE_EVERY_S` | `20` | `30` | Draft layer: run once after this many seconds without a pause |
| `TURN_GAP_S` | `0` | `1.0` | Above 0, joins adjacent sentences of the same speaker less than this many seconds apart into one line; 0 keeps the engine's own sentence split |
| `FIRMWARE_TOKEN` | empty | none | The token needed, besides the tenant token, to upload recorder firmware (`firmware/devkit/lr_release.py`); empty means no uploads |
| `VM_INGEST_URL` | empty | none | A VictoriaMetrics import URL (e.g. `https://example.com/api/v1/import/prometheus`); the server pushes `lr_*` metrics to it every minute; empty means no push |
| `VM_INGEST_USER`, `VM_INGEST_PASSWORD` | empty | empty | Basic auth for pushing metrics |

### Advanced: only for `stepfun` and `stepfun-file`

When `stepfun` or `stepfun-file` appears in `ENGINE`, `DRAFT_ENGINE` or `ENGINE_FALLBACK`, all four `ASR_*` variables below are required.

| Variable | Example value | When unset | Description |
|---|---|---|---|
| `ASR_UPLOAD_BRIDGE_URL` | empty | — | Your own upload bridge: `POST {filename, content_type}`, returns `{upload_url, download_url}` |
| `ASR_UPLOAD_BRIDGE_TOKEN` | empty | — | The upload bridge's token |
| `ASR_SERVICE_URL` | empty | — | Your own file-mode ASR service (with speakers and word times): `POST /audio/transcriptions {audio_url, model}`, `GET /jobs/{id}` |
| `ASR_SERVICE_BASIC_AUTH` | empty | — | That service's basic auth, written as `user:password` |
| `FILE_MODELS` | `stepaudio-2.5-asr-8006,doubao-seedasr-2.0` | same as the example value | The file-mode engines, in order; a window the first one rejects goes to the second |

### Only for offline scripts

| Variable | Used in | Description |
|---|---|---|
| `LIVE_DATA`, `RECORDINGS_DIR` | `owner.py`, `replay_case.py` | A tenant's data directory and recordings directory |
| `OWNER_LABELS` | `owner.py` | Windows known to be your own voice (jsonl) |
| `CASES_DIR` | `eval_all.sh` | The directory of labeled evaluation cases |
| `EMB_MODEL`, `SEG_MODEL`, `OWNER_SURE`, `LINK_MIN`, `OWNER_DEFAULT_DEVICE` | `replay_case.py`, `service_case.py` | See those two scripts |

## Other environment variables

The server also reads the variables below, which `.env.example` doesn't list. They are for tuning, and their defaults are the values the author uses.

| Variable | When unset | Description | Source |
|---|---|---|---|
| `SLICE_FOREGROUND_S`, `SLICE_BACKGROUND_S`, `SLICE_SCREEN_OFF_S` | `1`, `20`, `60` | Seconds of audio per piece while the app is in the foreground, in the background, or with the screen off (Android); the app reads them from `/api/client-config` | `routes_upload.py` |
| `REFINE_WINDOW_S` | `240` | The most seconds of audio the draft layer handles at once | `device_pipeline.py` |
| `LIVE_EVERY_S` | `120` | The live analysis is redone each time an ongoing conversation gains this many seconds of speech | `device_pipeline.py` |
| `SQUEEZE_KEEP_S` | `3` | Before audio goes to the engine, silences longer than this many seconds are cut to this length; 0 sends the audio whole | `asr.py` |
| `SQUEEZE_VAD` | `0.3` | The VAD threshold that finds those silences | `segmenter.py` |
| `SUMMARY_WORKERS` | `4` | How many summaries are written at once | `summaries.py` |
| `QWEN_ASR_MODEL` | `qwen-audio-3.1-asr-flash-filetrans` | The Bailian model the `qwen` engine uses | `qwen_asr.py` |
| `ASR_CALLS`, `INFER_CALLS`, `DB_CALLS`, `MEDIA_CALLS` | `1`, `2`, `8`, `16` | How many local ASR runs, voiceprint computations, whole-database queries and audio encodes or decodes run at once | `threads.py` |
| `INFER_THREADS` | `4` | onnxruntime threads per voiceprint computation | `infer.py` |
| `VAD_GAIN_MAX_DB` | `16` | The most dB the VAD's features are raised (this affects only speech detection, not the audio sent to the engine or played back); 0 turns it off | `infer.py` |
| `ASR_GAIN_MAX_DB` | `16` | The most dB the audio sent to the engine is raised; 0 sends it as is | `squeeze.py` |
| `DEHUM` | `1` | Removes the recorder's 10 ms periodic hum when its audio is read; 0 turns it off | `audio.py` |
| `SPEAKER_LINK`, `SPEAKER_SUGGEST`, `MERGE_SUGGEST` | `0.5`, `0.6`, `0.45` | Voiceprint similarity thresholds of the 人物 (People) page: join an anonymous group, suggest a name, suggest merging two labels in the same conversation | `routes_speakers.py` |
| `DIAR_TH`, `DIAR_JOIN`, `DIAR_GUARD`, `PEOPLE_MIN` | `0.5`, `0.35`, `0.6`, `0.7` | Thresholds for telling speakers apart within a conversation | `diarist.py` |
| `SELFVOICE_POP_P` | `0.95` | The probability the top candidate needs before the 「哪个是你的声音」 (Which one is your voice) card pops up by itself | `selfvoice.py` |
| `LOOP_STALL_MS` | `100` | When the event loop is held up longer than this many milliseconds, the log gets one line with how long and in which function (at most a line a second, the ones in between counted), and the `perf_stalls` table a row; it also measures the loop's lag and the thread pools' use each minute. About 0.6% of a core; `0` turns it off | `monitor.py` |
