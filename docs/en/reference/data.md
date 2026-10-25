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
| `feedback` | Users' corrections to summaries |
| `todo_marks` | 「完成」 (Done) or 「不对」 (Wrong) marks on to-dos (new on main, not in v0.1.0) |
| `kv` | Key-value pairs such as each device's processing progress |
| `device_log` | Device events uploaded by the phones, including the recorders' own logs |
| `device_status` | Recorder status read by the clients (battery, card, firmware) |
| `calls` | Duration, result and usage of each ASR and LLM call, without content |
| `balances` | Prepaid account balances, recorded hourly |
| `perf_stalls` | Times the event loop was held up: when it ended, how many milliseconds, in which functions (only in the first tenant's store; new on main) |
| `perf_samples` | The process, one row a minute: CPU seconds, memory, loop lag, thread pool use, queued pieces, open pages (only in the first tenant's store; new on main) |
| `perf_requests` | HTTP requests: one row for each that took 50 ms or more, and each minute one row per method, route and status (new on main) |
| `perf_pieces` | How long each step of each final piece and draft pass took: decode, VAD, ASR, voiceprints, store (new on main) |

The `perf_` tables hold durations and resource use only, never content, and keep 90 days. `GET /api/perf?day=YYYY-MM-DD` reads one day; `python -m alldayrec.perf export <live.db> <YYYY-MM-DD>` exports one from a copy of the store.

A voiceprint learnt before `enrolled` existed is one summary row per person, not tied to sentences. If it took in someone else's voice back then, `python -m alldayrec.bank relearn <live.db>` recomputes that row from the voices now under the person's name (from live recordings). It only prints, per person, the sentences before and after, the voices used and dropped, and the cosine between the old and the new voiceprint; `--apply` writes it, and then the server needs a restart (it loads the voiceprints at start). What was learnt sentence by sentence since stays.
