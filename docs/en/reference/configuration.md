---
type: reference
---

# Configuration

The server's settings live in two places: `tenants.json` lists the people it serves, and environment variables (usually in `.env`) set everything else. When a required setting is missing, the server refuses to start and writes the missing ones to its log.

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
| `sound` | Optional, default `false`. `true`: with `SOUND_MODEL` set, this person's recordings get environmental sound scores by themselves (see [Environmental sound](#optional-environmental-sound) below). Either way, with `SOUND_MODEL` set, trims wait for those scores |
| `seg_emb` | Optional, default `false`. `true`: the voiceprint (ReDimNet2, fp16) taken of each recognized segment at finalization is kept in the `seg_emb` table of this person's `live.db`, with no extra computation and no change to any recognition result. These are the voiceprints of everyone near the wearer, see [Privacy](../explanation/privacy.md). About 1.1 MB a day at some 3,000 segments a day (384 bytes a vector) |

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
| `LLM_FALLBACK_BASE_URL`, `LLM_FALLBACK_API_KEY`, `LLM_FALLBACK_MODEL` | `https://api.deepseek.com`, empty, `deepseek-chat` | no fallback | A backup endpoint, used only when all three are set. Any LLM call (summaries, 问 AI, recaps and the rest) that times out, gets a 429 or a 5xx, or cannot connect is tried once more there. A 4xx is not (the request itself is wrong), nor a streamed answer that has started. Each try is its own row in the calls log; the backup's model name is prefixed `fallback:` |

### Optional

| Variable | Example value | When unset | Description |
|---|---|---|---|
| `DATA_DIR` | empty | the user data directory: `~/.local/share/alldayrec` on Linux (`alldayrec` under `XDG_DATA_HOME` when that is set), `~/Library/Application Support/alldayrec` on macOS | The directory `tenants.json` is in; without `MODELS_DIR` the model files go in its `models`, and the tenant a first start makes in its `main`. Fixed at `/data` in Docker |
| `MODELS_DIR` | empty | `DATA_DIR/models` | The model files directory, an absolute, writable path; missing files are downloaded here at startup. Fixed at `/data/models` in Docker |
| `PORT` | `8770` | `8770` | The port to listen on. Docker Compose fixes it at `8770` |
| `HOST` | `127.0.0.1` | `127.0.0.1` | The address to listen on; set `0.0.0.0` when the reverse proxy runs on another machine. Docker Compose fixes it at `0.0.0.0` |
| `HOLD_STALE_S` | `1200` | `1200` | While the phone keeps reporting its backfill hold, resume finals after this many seconds without new audio before it. A silent phone is still waited for up to 12 hours; returning after more than 5 minutes gives fresh grace for its backlog. Older audio that arrives later is transcribed as late audio (see `LATE_SETTLE_S`). Set very high (e.g. `315360000`) to effectively disable the no-progress timeout; `0` expires immediately, it does not disable it |
| `DRAFT_ENGINE` | empty | same as `ENGINE` | The engine of the draft layer (while a page is open); it can also be `qwen-stream`: while a page is open, the audio streams to Qwen (Bailian qwen-audio-3.1-asr-flash-streaming, with `DASHSCOPE_API_KEY`), so words show as they are said |
| `ENGINE_FALLBACK` | empty | none | The engine used when `ENGINE` or `DRAFT_ENGINE` fails on a window (402, 451, 5xx, timeout); empty means a retry later |
| `HOTWORDS` | `1` | `1` | `0` sends no ASR engine the hotwords ([terms](../how-to/add-terms.md)); the prompts for summaries and answers still carry them |
| `VOLC_ASR_KEY` | empty | — | The API key of a Volcengine Ark Agent Plan, used by `volc` |
| `STEPFUN_API_KEY` | empty | — | StepFun key, used by `stepfun` and `stepfun-sse` |
| `STEPFUN_SSE_URL` | empty | `https://api.stepfun.com/v1/audio/asr/sse` | The StepFun SSE endpoint; the default is the pay-as-you-go address; for Step Plan use `.../step_plan/v1/audio/asr/sse` |
| `LOCAL_ASR_URL` | empty | the k2-fsa/sherpa-onnx GitHub Release | Where `local` downloads its model tarball (about 136 MB) from; the same tarball on a mirror works too (sha256-checked) |
| `UI_LANG` | `zh` | `zh` | The language of what the LLM writes (summaries, to-dos, live analysis, the daily recap, "ask AI" answers and more): `zh` or `en`. A `lang` in `tenants.json` wins. The web pages use it too in a browser that hasn't picked a language; the app's UI doesn't change |
| `RECAP_AT` | `10:00` | `10:00` | The time of day the server writes yesterday's recap; set it empty for no recap |
| `SPLIT_MIN_S` | `600` | `600` | Before a conversation at least this many seconds long (start to end) is summarized, one more LLM call checks whether an unrelated conversation began inside it (splitting only on 5 quiet minutes lets voices around, in a mall or on the street, chain several into one) and splits it there, each part with its own summary; `0` never splits. Late audio never merges the parts back. The call sends `reasoning_effort`, so it needs an endpoint that accepts it (a reasoning model); set `0` when yours does not. Existing conversations: `python -m alldayrec.split --since YYYY-MM-DD` (see the top of that file) |
| `LATE_SETTLE_S` | `180` | `180` | Seconds after the last extension before late audio is processed; end/hold release settles it immediately. Spans near an open conversation wait for it to close |
| `LATE_PROCESS_TIMEOUT_S` | `1800` | `1800` | Maximum seconds for one late-audio job; each job contains at most 30 minutes of audio, so processing must be faster than real time (including diarization, RTF < 1); timeouts back off and stop after three attempts, retaining the failure reason |
| `TZ` | `Asia/Shanghai` | the system time zone | The time zone for conversation dates and `RECAP_AT`. A Docker container's system time zone is UTC; an empty `TZ=` means UTC too |
| `REFINE_EVERY_S` | `20` | `30` | Draft layer: run once after this many seconds without a pause |
| `TURN_GAP_S` | `0` | `1.0` | Above 0, joins adjacent sentences of the same speaker less than this many seconds apart into one line; 0 keeps the engine's own sentence split |
| `FIRMWARE_TOKEN` | empty | none | The token needed, besides the tenant token, to upload recorder firmware (`firmware/devkit/lr_release.py`); empty means no uploads |
| `VM_INGEST_URL` | empty | none | A VictoriaMetrics import URL (e.g. `https://example.com/api/v1/import/prometheus`); the server pushes `lr_*` metrics to it every minute; empty means no push |
| `VM_INGEST_USER`, `VM_INGEST_PASSWORD` | empty | empty | Basic auth for pushing metrics |

### Optional: recordings in object storage

Unset, every recording stays in the `recordings` directory (`STORAGE=local`), as before. With `STORAGE=s3`, the hour being recorded is still written to local disk; once that hour is over and no piece has come for `SEAL_IDLE_S` seconds, the whole file is uploaded as one object of an S3-compatible store, checked, and only then deleted locally, so local disk holds about the current hour or two. Pieces that arrive later go to a local staging file first and are merged into that object in batches. The whole process: [Architecture and data flow](../explanation/architecture.md#where-recordings-are-kept).

| Variable | Example value | When unset | Description |
|---|---|---|---|
| `STORAGE` | `s3` | `local` | `local`: recordings on local disk; `s3`: sealed into an S3-compatible object store that takes conditional writes (see `S3_ENDPOINT`) |
| `S3_ENDPOINT` | `https://s3.example.com` | required with `STORAGE=s3` | The endpoint, a port and a path allowed. Path-style requests (`<endpoint>/<bucket>/<key>`), not bucket-named hosts. The store must take conditional writes (`If-None-Match`, `If-Match`) and give an ETag on HEAD: MinIO (tested from 2024-11 releases on), AWS S3 and Cloudflare R2 do; Alibaba Cloud OSS and Volcengine TOS are untested |
| `S3_BUCKET` | `alldayrec` | required with `STORAGE=s3` | The bucket; create it first |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY` | empty | required with `STORAGE=s3` | The access key, allowed to read, write, delete and HEAD in that bucket |
| `S3_REGION` | `us-east-1` | `us-east-1` | The region requests are signed for; for MinIO usually `us-east-1` |
| `S3_PREFIX` | `alldayrec/` | empty | The keys' prefix. Each tenant's objects are under `<S3_PREFIX><tenant id>/`. One prefix per server instance; instances sharing a bucket each use their own. A tenant records the prefix in its own store the first time it uses the object store and keeps it, so a tenant moved into another instance (`tenants.sh merge`) still reads its own objects; the instance it moves into must use the same `S3_ENDPOINT` and `S3_BUCKET` (the store records only the prefix) |
| `SEAL_IDLE_S` | `600` | `600` | Once an hour is over, it is sealed this many seconds after its last piece came |
| `MERGE_IDLE_S` | `90` | `90` | A sealed hour's staged late pieces are merged into its object once none has come for this many seconds |
| `MERGE_MAX_BYTES` | `33554432` | 32 MiB | ... or as soon as they add up to this many bytes |
| `MERGE_MAX_S` | `1800` | `1800` | ... or as soon as the first of them has waited this many seconds |

- One object is at most 5 GB: that is one PUT's limit, and the server does no multipart upload. A larger file (only a very large import can be one) stays on local disk with an error in the log, and reads as usual.
- With the object store unreachable, uploads are written to local disk as ever; sealing and merging retry after 30 s, 60 s, ... up to 10 minutes, and local disk grows. Sealed audio cannot be read meanwhile: playback answers 503 and transcription waits at that piece until the store is back.
- Recordings already on local disk before the switch to `s3` stay there and read as usual; they are not moved by themselves. To move them into the object store, run `seal adopt` with the instance's environment and time zone: it only marks those hour files as due for sealing, and the running instance seals them as any other (a stopped one once started): upload, check, rows pointed at the object, local file deleted. The hour now, a file a piece came to within `SEAL_IDLE_S`, and a file over 5 GB stay local. It takes the same lock as `trim.py`'s `apply` and `purge`, so they never run together. First `--dry-run` to see which files it would take and their total bytes, then `--limit` in batches, each once `lr_store_pending_seal` is back to 0:

  ```sh
  cd server
  uv run python -m alldayrec.seal adopt --tenant main --dry-run    # only lists the files and their total bytes
  uv run python -m alldayrec.seal adopt --tenant main --limit 200
  ```
- The server never overwrites another's object: a new object is written only if its key does not exist yet (`If-None-Match: *`), and a merge replaces only the version that was sealed (`If-Match`). Two instances given the same prefix by mistake: the later seal is refused with an error in the log, and that hour stays on local disk.
- `s3` switched back to `local` for a while, then to `s3` again: pieces that came meanwhile for hours already sealed are in new local hour files; after the switch back these files and their pieces stay on local disk and read as usual, with an error in the log, and are not deleted.

### Optional: environmental sound

Scores every recording (with speech or without) on AudioSet's classes and keeps the scores as they came in the `sounds` table of `live.db` (format: [Data directory](./data.md#environmental-sound-sounds); privacy: [Privacy](../explanation/privacy.md#environmental-sound)). For now it only lays the data down for summaries later; nothing shows it on the web pages yet. Off by default.

| Variable | Default | Description |
|---|---|---|
| `SOUND_MODEL` | empty (off) | The models to run, comma-separated: `mn10_as` (EfficientAT, one result per 10 s) and `yamnet` (a frame every 0.48 s). The first is the main model the trim gate looks at. Both can run at once, each stored on its own. `mn10_as.onnx` is downloaded and checked only when named here (`yamnet.onnx` is always, for hearing music) |
| `SOUND_SINCE` | none | Required with `SOUND_MODEL` (the server refuses to start without it): the server scores the recordings from this time on (ISO, the instance's time zone, e.g. `2026-11-17`) by itself; the history before it only through the backfill command below, never by itself. A new model file (another sha256: a new model) also starts from this time |
| `SOUND_EVERY_S`, `SOUND_SETTLE_S` | `60`, `120` | How often to look for recordings not scored yet; how many seconds after a piece arrived it is scored |

- A tenant also needs `"sound": true` in `tenants.json` to be scored by itself. One without it is not scored, but the trim gate still holds it (below): backfill first, or trim with `--allow-unscened`.
- What gets scored is every uploaded audio piece, transcribed or not, whether its transcription worked or not. A piece that will not decode, or decodes shorter than its recorded length, gets no result and is tried again after a backoff. The models run on one thread in the server process (one stretch at a time in the whole process, the tenants taking turns); with `INFER_URL` set, on the inference service's `POST /scene/<model>`, which needs the same `SOUND_MODEL`. Before the first result from the service is used, the sha256 of its scene models in `/health` is checked; another one is not used, it is logged as an error and tried again after a backoff. An inference service without the model, or with another one, delays only the sound scores, never the voiceprints.
- Metrics: `lr_scene_audio_seconds_total` (seconds of audio scored), `lr_scene_compute_seconds_total` (time in the model), `lr_scene_failed_total` (stretches that failed, waiting to be tried again), each by `model`; `lr_scene_decode_seconds_total` (time decoding: once for all the models) and `lr_scene_backlog_seconds` (seconds some model has still not scored after the hourly full pass, those waiting to be tried again after a failure too; 0 once all are done).
- The trim gate: with `SOUND_MODEL` set, for every tenant (with `"sound"` or not, a `--db` store too), `trim.py` leaves an hour whole (reason `no sound`) while any millisecond of the pieces it would drop has no result of the main model, the history before `SOUND_SINCE` too. There is no tolerance: an unscored bit at the end of a piece, a piece that will not decode or decodes short, all count as not covered. Backfill first, then trim; when those results are really not wanted, `--allow-unscened` trims anyway and logs how many seconds of unscored audio it dropped. With `SOUND_MODEL` empty, trimming is exactly as before.
- Backfilling history (heavy, on one core, per recorded hour: 45–120 s decoding (once for all the models), about 30 s for EfficientAT, 35–40 s for YAMNet; wall-clock time measured on a busy older CPU):

```sh
uv run python -m alldayrec.sounds backfill --tenant main --since 2026-10-01 --until 2026-11-17   # SOUND_MODEL's models
uv run python -m alldayrec.sounds backfill --db <copy>/live.db --recordings <copy>/recordings --since 2026-10-01 --until 2026-11-17 --model mn10_as
```

Backfilling checks the models it runs first (`--model`'s too): run locally, it downloads missing files and checks their sha256; with `INFER_URL` set, it checks the sha256 of the inference service's scene models in `/health`. Another one stops it with an error, nothing written.

Scored on a copy, only the results can be copied back: `sqlite3 live.db "ATTACH '<copy>/live.db' AS c; INSERT OR IGNORE INTO sounds SELECT * FROM c.sounds"`. Keep `--until` no later than `SOUND_SINCE`, so it does not overlap what the server scores itself.

What turning on the background-music gate (`BG_GATE`, in [Other environment variables](#other-environment-variables)) costs. It only keeps what voiceprints learn from: the People page's groups, picking the 这是你吗 (is this you) card's candidates, and splits and merges still use the conversations' voices as they are, and may still show groups that music got into.

- **Renaming to 我 makes the voice again on the spot.** A conversation's voice not made with the gate on, or with music found under it since, is embedded again from the recording when its label is renamed 我 (on a conversation or on the People page): at most 4 sentences of at most 15 s per label. Naming N labels at once on the People page makes up to N×4, one after the other, sharing `INFER_CALLS` with the other voiceprint work, and the request waits for them. What it makes is not stored, so the next time makes it again. Time it on the real inference service before turning the gate on.
- **An old conversation whose recording is gone cannot teach 我.** A voice that needs making again, as above, cannot be made once its recording is trimmed (or for an imported history conversation): the rename still goes through and answers `learned: false`, and no 我 voiceprint is made. Renaming a label to 我 is not the same as remembering 我's voice. A voice made with the gate on and still with no music found under it teaches as before after its recording is gone.
- **Fewer "sounds like X" hints.** `name_check`'s acoustic hint (`like`) on a rename only looks at voices shown free of music; old ones do not count, so `alike` can be empty. The hint that a person of that name exists (`same`) is as before. What is made again on the spot is not stored, so such an old conversation's hint does not come back from it.
- **The 哪个是你 (which one is you) card waits for new conversations.** Its candidates are only voices made with the gate on and still with no music found under them; conversations from before do not count, so after turning it on the card waits until enough new ones come in. On a pick, the candidate's voices are checked again with the scores there are then, and 我's voiceprint is made from those left. None left (music scored after the card came up, or a card from before the gate): nothing is saved, the answer is `learned: 0` and `reason: background_music`, the page says nothing was saved this time, and the card comes again later. Until there is a 我 voiceprint, each round (5 minutes) looks up at most 4 scores per conversation voice.
- **`bank relearn` only uses voices shown free of music.** Those made before the gate do not count; a person with fewer than 5 is kept as is, and its line says `N not shown free of music (BG_GATE)`. The command reads `BG_GATE` and `SOUND_MODEL` from its own environment, so run it with the instance's `.env`, and the tenant's `"sound"` from `tenants.json` when given `--tenant ID` (instead of the store's path; that tenant's store): `python -m alldayrec.bank relearn --tenant <ID> [--apply]`. Given the store's path, it does not know the tenant and filters nothing, as before the gate.
- **A voice changed by a merge, a split or late audio is not known to be free of music**, and is made again the next time a voiceprint learns from it.
- **What the score lookups cost.** Each sentence is one query of the `sounds` table, and the older the sentence, the more rows it scans (on 100,000 synthetic rows, about 347 days: about 31 ms for the oldest sentence, 16 ms halfway).

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
| `LIVE_DATA` | `replay_case.py` | A tenant's data directory |
| `CASES_DIR` | `eval_all.sh` | The directory of labeled evaluation cases |
| `EMB_MODEL`, `SEG_MODEL`, `OWNER_SURE`, `LINK_MIN`, `OWNER_DEFAULT_DEVICE` | `replay_case.py`, `service_case.py` | See those scripts |
| `TRIM_AFTER_DAYS` (default `2`), `TRIM_PAD_S` (`10`), `TRIM_VAD` (`0.1`), `TRIM_PURGE_AFTER_H` (`24`) | `trim.py` | Trimming: how many days after its newest piece an hour file may be trimmed; the seconds kept on each side of speech and of sentences; the VAD threshold for speech (a VAD file made at another threshold is not used); how many hours after a trim `purge` may delete the original (at least 24: less and `purge` refuses to run). See [Where recordings are kept](../explanation/architecture.md#where-recordings-are-kept). With `SOUND_MODEL` set there is also a gate, for every tenant: see [Environmental sound](#optional-environmental-sound) |

### Device gain (`device_gain`)

Not an environment variable: set with a command, `server/gain.sh set <tenant> <device> <dB> [--since <time>]` (`python -m alldayrec.gain` locally). See [Levels and gains](../explanation/architecture.md#levels-and-gains).

- An offset that calibrates the device's own sensitivity is set from `--since 0` (the default), so all of its audio is calibrated by it, the history voices' clips too.
- Some history voice clips (`/api/voice-clip`) have no time (`voices.clip_start` empty: the early external generator wrote none). Such a clip is calibrated only while its device's offset is one value for all time: when the device's first offset row starts at 0 and every offset set is the same, the whole clip gets that offset, as WAV with `X-Gain-Db`. When the device has different offsets, or its first one starts after 0, there is no telling which one the clip falls under: the stored file is served as it is, logged, and counted in the metric `lr_gain_untimed_clips_total` (by device). A device with no non-zero offset gets the stored file. To calibrate these clips exactly, backfill their `clip_start`.

## Other environment variables

The server also reads the variables below, which `.env.example` doesn't list. They are for tuning, and their defaults are the values the author uses.

| Variable | When unset | Description | Source |
|---|---|---|---|
| `SLICE_FOREGROUND_S`, `SLICE_BACKGROUND_S`, `SLICE_SCREEN_OFF_S` | `1`, `20`, `60` | Seconds of audio per piece while the app is in the foreground, in the background, or with the screen off (Android); the app reads them from `/api/client-config` and from the answer to each upload | `routes_upload.py` |
| `SLICE_WATCHED_S` | `2` | While the 实时 (Live) page `/live` is open, pieces in the background and with the screen off are at most this many seconds, so captions appear within seconds even with the phone in a pocket; the card of the conversation going on, on the 时间线 (Timeline) page, does not count. Back to normal within a minute after the page closes | `routes_upload.py` |
| `REFINE_WINDOW_S` | `240` | The most seconds of audio the draft layer handles at once | `device_pipeline.py` |
| `LIVE_EVERY_S` | `120` | The live analysis is redone each time an ongoing conversation gains this many seconds of speech | `device_pipeline.py` |
| `SQUEEZE_KEEP_S` | `3` | Before audio goes to the engine, silences longer than this many seconds are cut to this length; 0 sends the audio whole | `asr.py` |
| `SQUEEZE_VAD` | `0.3` | The VAD threshold that finds those silences | `segmenter.py` |
| `SUMMARY_WORKERS` | `4` | How many summaries are written at once | `summaries.py` |
| `QWEN_ASR_MODEL` | `qwen-audio-3.1-asr-flash-filetrans` | The Bailian model the `qwen` engine uses | `qwen_asr.py` |
| `ASR_CALLS`, `INFER_CALLS`, `DB_CALLS`, `MEDIA_CALLS` | `1`, `2`, `8`, `16` | How many local ASR runs, voiceprint computations, whole-database queries and audio encodes or decodes run at once | `threads.py` |
| `FINAL_ASR_CONCURRENCY`, `FINAL_EMBED_CONCURRENCY` | `4`, `2` | Per device: during its backlog, how many final pieces go to the engine at once, and how many of them compute voiceprints at once. Two devices with a backlog each send up to twice as many; voiceprints across the process still share the `INFER_CALLS` slots. Pieces are still labelled, split into conversations and stored in time order, with the same result as one at a time. Without a backlog there is only ever one piece, with no added delay; nor while another recorder has audio of the same time or this one stands by for it (co-location) | `device_pipeline.py` |
| `INFER_THREADS` | `4` | onnxruntime threads per voiceprint computation | `infer.py` |
| `INFER_URL` | empty | The address of the separate inference service (started with `alldayrec --infer`), for example `https://infer.example.com`. When set, voiceprints, music detection and speaker-change detection are computed only there, and the server neither downloads nor loads their models; while the service fails, the calls wait and try again (2 s, doubling to 60 s) until it answers. Empty: in the server process | `compute.py` |
| `INFER_TOKEN` | empty | The inference service's token. Required with `INFER_URL`; set the same value on the server and on `alldayrec --infer` | `compute.py`, `infer_server.py` |
| `INFER_TIMEOUT` | `30` | Seconds the inference service may take to answer a try (connecting: 2 s) | `compute.py` |
| `INFER_HOST`, `INFER_PORT` | `127.0.0.1`, `8771` | Where `alldayrec --infer` listens (`--port` sets only its port too; its own variables, so it never takes the server's `HOST` and `PORT` from a shared `.env`) | `infer_server.py` |
| `MUSIC_DETECT` | `1` | At finalization, a sentence heard as music (a song played aloud, the voice about as loud as its accompaniment) gets no voiceprint: its speaker is `S?` and it never reaches the People page. It is still transcribed, with a music-note icon on its row. Fixing its speaker to someone makes it a voice: the icon goes and its voiceprint is learnt as usual. 0 turns it off | `music.py` |
| `BG_GATE`, `BG_MUSIC_MIN` | `0`, `0.23` | Background-music gate: at 1, a sentence with music under its voice (the highest Music score of the `mn10_as` 10 s frames it overlaps reaching `BG_MUSIC_MIN`) teaches no voiceprint: it is not enrolled into a person's voiceprint, not counted into the conversation's voice (so renaming a label to 我 cannot learn it either; a conversation's voice made before the gate was on, or before its scores came in, is checked again with the scores there are then each time 我 learns from it or late audio is matched to it, and made again without such sentences: one made before the gate counts as not known and is always made again), and not used as a clip on the 哪个是你 (which one is you) or 这是你吗 / 这是 P 吗 (is this you / is this P) cards; a yes on 这是 P 吗 for a person already named, whose sentences all have it, is answered with `learned: 0` and `reason: background_music`. It is still transcribed and labelled as usual, with no music note (unlike `MUSIC_DETECT`). It takes effect only for a tenant with environmental sound on (`"sound": true`) and `mn10_as` in `SOUND_MODEL`; a sentence with no recorder (no `source`) is matched against the scores of every recorder of the tenant; otherwise, or where that stretch has no scores yet, it behaves as when off and only counts `lr_bg_gate_unscored_total`; sentences it holds back count in `lr_bg_gate_skipped_total`. Both are by `use`, where the sentence was asked about (`enroll`: a person's voiceprint, `voices`: a conversation's voice, `cached`: one checked again, `late`, `clip`, `check`), and one sentence can be counted in several. 0.23 is tentative, from 74 sample windows with no music in them (about 5 % false alarms): too few, to be checked again on new data. What turning it on costs: [Optional: environmental sound](#optional-environmental-sound) | `sounds.py` |
| `VAD_GAIN_MAX_DB` | `16` | The most dB the VAD's features are raised (this affects only speech detection, not the audio sent to the engine or played back); worked out after the device's calibration offset ([Levels and gains](../explanation/architecture.md#levels-and-gains)); 0 turns it off | `infer.py` |
| `ASR_GAIN_MAX_DB` | `16` | The most dB the audio sent to the engine is raised, after the device's calibration offset; 0 sends it as is | `squeeze.py` |
| `DEHUM` | `1` | Removes the recorder's 10 ms periodic hum when its audio is read; 0 turns it off | `audio.py` |
| `PLAY_GAIN_DB` | `12` | How many dB louder our recorders' audio (devices named `LR-…`) plays on the web and when listening in, held under −1 dBFS (no clipping); phones, beans and imports play as they came. Playback only: storage, transcription and voiceprints are unchanged; 0 turns it off. Not for audio under a calibration offset (`device_gain`, from its `since_ms` on): that plays at the offset alone. Once every recorder has an offset, this can go | `audio.py` |
| `SPEAKER_LINK`, `SPEAKER_SUGGEST`, `MERGE_SUGGEST` | `0.5`, `0.6`, `0.45` | Voiceprint similarity thresholds of the 人物 (People) page: join an anonymous group, suggest a name, suggest merging two labels in the same conversation | `routes_speakers.py` |
| `SPLIT_TURNS` | `1` | When the engine joins two people's words into one sentence (a quick back-and-forth), pyannote segmentation-3.0 first hears where the speaker changes in it; it is cut at the nearest word gap, a cut stays only where the voiceprints of the ≤3 s before and after it differ, and each part gets its own speaker. Only sentences with word times (the `qwen` engine); 0 turns it off and cuts by `SPLIT_COS` alone | `transcript.py` |
| `SPLIT_COS` | `0.35` | With `SPLIT_TURNS=0`, 1.5 s voiceprints every 0.5 s are taken over it; where the windows in the 3 s before and the 3 s after a point are less alike than this, it is cut at the nearest word and each part gets its own speaker. Only sentences of 3 s or more with word times (the `qwen` engine); 0 turns it off | `transcript.py` |
| `DIAR_TH`, `DIAR_JOIN`, `DIAR_GUARD`, `PEOPLE_MIN` | `0.5`, `0.35`, `0.6`, `0.7` | Thresholds for telling speakers apart within a conversation | `diarist.py` |
| `SELFVOICE_POP_P` | `0.95` | The probability the top candidate needs before the 「哪个是你？」 (Which one is you?) card pops up by itself | `selfvoice.py` |
| `LOOP_STALL_MS` | `100` | When the event loop is held up longer than this many milliseconds, the log gets one line with how long and in which function (at most a line a second, the ones in between counted), and the `perf_stalls` table a row; it also measures the loop's lag and the thread pools' use each minute. About 0.6% of a core; `0` turns it off | `monitor.py` |
