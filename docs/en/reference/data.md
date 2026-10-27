---
type: reference
---

# Data layout

Each tenant has two directories, both set in `tenants.json`: `data` (the database and derived data) and `recordings` (the original audio). With Docker deployed as in the quickstart, they are `data/main` and `data/main/recordings` on the host.

## DATA_DIR

`DATA_DIR` is `./data` on the host with Docker; with uvx, pip or from source it is your user data directory by default (see [Configuration](./configuration.md)). The tenant `main` that a first start makes lives under it:

```text
<DATA_DIR>/
├── tenants.json
├── models/                         model files (MODELS_DIR)
└── main/                           data directory of tenant main
    ├── live.db                     SQLite database (WAL mode, with live.db-wal and live.db-shm beside it)
    ├── <date>.passes.jsonl         the engine's raw output from each transcription pass
    └── recordings/                 recordings directory of tenant main
        └── chunks/<device>/<date>/<HH>.<ext>
```

## The data directory

| Path | Content |
|---|---|
| `live.db` | The SQLite database, see the table below; its schema is defined in [`server/alldayrec/db.py`](https://github.com/suyiiyii/alldayrec/blob/main/server/alldayrec/db.py) |
| `<date>.passes.jsonl` | The engine's raw output from each transcription pass |
| `owner-redimnet2.npz` | The voiceprint of 「我」 (me) |
| `voices/clips/` | Voice clips of the speakers |
| `firmware/` | Recorder firmware packages (only in the first tenant's directory) |
| `live-record.apk` | The Android app the server offers at `/app.apk` (only in the first tenant's directory) |

## The recordings directory

| Path | Content |
|---|---|
| `chunks/<device>/<date>/<HH>.<ext>` | One file per device per hour; each piece that arrives is appended to it as is, and its position and length in the file are recorded in the `pieces` table of `live.db`. The extension follows the upload's `Content-Type`: `ogg`, `webm`, `aac`, otherwise `bin` |
| `rejected/<device>/<start>.<ext>` | Uploads that failed to decode (the endpoint returned 400), kept as they are for inspection |

## The tables of live.db

| Table | Content |
|---|---|
| `sentences` | Final sentences: time, speaker, text, source device, engine |
| `drafts` | Draft-layer sentences (produced only while a page is open), kept after the finals replace them |
| `conversations` | Conversations: start and end time, participants, seconds of speech, number of sentences, type, device |
| `summaries` | Each conversation's summary (JSON) |
| `timelines` | The live analysis as it stood when a conversation ended |
| `voices` | The voiceprint and voice clips of each speaker label in each conversation |
| `apart` | Voices marked 「不是这个人」 (Not this person) |
| `people` | Voiceprints of people with recognized names |
| `enrolled` | The sentences each person's voiceprint was learnt from, one row each; an undo or a split takes them back out |
| `aliases` | Other names of a person |
| `name_hints` | What each label was called in a conversation (used only to suggest names) |
| `profiles` | People cards of people with recognized names, each row with its source |
| `pieces` | The index of uploaded pieces, which is also the processing queue |
| `late_spans` | Exact late-audio intervals, processing state and retry counts (since v0.4.0) |
| `processed_audio` | Actual processed coverage, including pauses, committed with final rows; prevents replay after restart (since v0.4.0) |
| `conversation_aliases` | Old conversation links and speaker labels mapped to the retained conversation (since v0.4.0) |
| `feedback` | Users' corrections to summaries |
| `todo_marks` | 「完成」 (Done) or 「不对」 (Wrong) marks on to-dos (since v0.2.0) |
| `stars` | Starred sentences: the sentence id, its conversation, when it was starred (since v0.3.0) |
| `mcp_links` | AI connection links: the name, the secret's SHA-256 hash and first 8 characters, when it was created and last used; never the secret itself (since v0.4.0) |
| `kv` | Key-value pairs such as each device's processing progress |
| `device_log` | Device events uploaded by the phones, including the recorders' own logs |
| `device_status` | Recorder status read by the clients (battery, card, firmware) |
| `calls` | Duration, result and usage of each ASR and LLM call, without content |
| `balances` | Prepaid account balances, recorded hourly |
| `perf_stalls` | Times the event loop was held up: when it ended, how many milliseconds, in which functions (only in the first tenant's store; since v0.3.0) |
| `perf_samples` | The process, one row a minute: CPU seconds, memory, loop lag, thread pool use, queued pieces, open pages (only in the first tenant's store; since v0.3.0) |
| `perf_requests` | HTTP requests: one row for each that took 50 ms or more, and each minute one row per method, route and status (since v0.3.0) |
| `perf_pieces` | How long each step of each final piece and draft pass took: decode, VAD, ASR, voiceprints, store (since v0.3.0) |

The `perf_` tables hold durations and resource use only, never content, and keep 90 days. `GET /api/perf?day=YYYY-MM-DD` reads one day; `python -m alldayrec.perf export <live.db> <YYYY-MM-DD>` exports one from a copy of the store.

A voiceprint learnt before `enrolled` existed is one summary row per person, not tied to sentences. If it took in someone else's voice back then, `python -m alldayrec.bank relearn <live.db>` recomputes that row from the voices now under the person's name (from live recordings). It only prints, per person, the sentences before and after, the voices used and dropped, and the cosine between the old and the new voiceprint; `--apply` writes it, and then the server needs a restart (it loads the voiceprints at start). What was learnt sentence by sentence since stays.

A prompt change leaves existing summaries intact; `summaries.prompt_version` records each version. After deploying, run from the server checkout's `server/` directory:

```sh
# Set LIVE_RECORD_URL for the intended instance; token from LIVE_RECORD_TOKEN or stdin's first line.
uv run python -m alldayrec.resummarize --days 30 --dry-run
uv run python -m alldayrec.resummarize --days 30 --limit 1
uv run python -m alldayrec.resummarize --days 30 --delay 5
```

The window includes today and the previous N−1 calendar dates in the command's timezone, based on conversation start. Only closed conversations with at least 60 seconds of speech are processed. Older, open, pending and current-version summaries are skipped, as are short conversations with a manually requested summary. Check one before expanding the run. A rerun skips completed upgrades. Each redo finishes before the next starts, with a default 5-second gap; HTTP and model 429s back off, up to `--max-delay` (600 seconds). A timeout (`--timeout`, default 1800 seconds) or uncertain HTTP result stops the run: check whether the server is still processing before restarting. Failures exit with code 1. Output counts completed/failed/skipped, processed conversations' todos and marked todos before/after, and model calls made meanwhile (including other background work).

A successful old summary stays visible until success and survives model failure. Retrying a previously failed summary first sets it pending, so backfill waits for this attempt. Todos can store a pinned `id`: a unique match by text or quote with the same owner/recipient inherits the old ID. Done/wrong rows never move or duplicate. Unmatched or ambiguous marked old todos are carried into the new summary unchanged with the same ID and mark, rather than rejecting the replacement. Other new actions get fresh IDs to avoid reusing an old action's ID. Rename/merge updates todo owner/recipient labels immediately; undo restores the changed fields by action ID, including when merging into an existing person. Regeneration, undo and remark operate on one ID. Older summaries still derive their original IDs from time/text until their first regeneration pins them. Stars live in `stars`; summary regeneration changes neither sentences nor stars.

Existing daily recaps stay unchanged. On read, pinned IDs resolve to the current action and mark; older unpinned entries are matched by text/recipient/due. Missing actions are displayed without a mark control so a stale recap cannot mark another action. After backfill, regenerate affected dates with existing recaps via `POST /api/recap/{YYYY-MM-DD}` (`recap_redo`) to refresh highlights and todo references. This adds one model call per date with summaries, at most 30 calls for 30 days. For 100 conversations, expect 100 summary calls, up to 400 with all four attempts. Missing name hints can add one hints call per conversation, and each named participant with an existing profile triggers a profile call. Recaps, hints and profiles are additional to the 100. The script reports actual ledger counts; money depends on the configured model and input length.
