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
| `chunks/<device>/<date>/<HH>.trim.<ext>` | A trimmed hour (`trim.py`): the pieces of the hour file near speech, copied as they are, in order; an object of the same name (`s3:`) when the hour was sealed |
| `staging/chunks/<device>/<date>/<HH>.<ext>.<gen>` | Only with `STORAGE=s3`: late pieces of an hour already sealed, kept here until merged into its object. A row whose `pieces.path` starts with `s3:` is then in the object store (`<S3_PREFIX><tenant id>/<path>`): [Where recordings are kept](../explanation/architecture.md#where-recordings-are-kept) |
| `rejected/<device>/<start>.<ext>` | Uploads that failed to decode (the endpoint returned 400), kept as they are for inspection |

## The tables of live.db

| Table | Content |
|---|---|
| `sentences` | Final sentences: time, speaker, text, source device, engine |
| `drafts` | Draft-layer sentences (produced only while a page is open), kept after the finals replace them |
| `conversations` | Conversations: start and end time, participants, seconds of speech, number of sentences, device |
| `summaries` | Each conversation's summary (JSON) |
| `timelines` | The live analysis as it stood when a conversation ended |
| `voices` | The voiceprint and voice clips of each speaker label in each conversation (a clip's span `clip_start`, `clip_end`; empty for the history clips the early external generator made, which are calibrated only while their device's offset is one value for all time: see [Device gain](configuration.md#device-gain-device-gain)) |
| `apart` | Voices marked 「不是这个人」 (Not this person) |
| `people` | Voiceprints of people with recognized names |
| `enrolled` | The sentences each person's voiceprint was learnt from, one row each; an undo or a split takes them back out |
| `aliases` | Other names of a person |
| `terms` | Terms: each term and when it was added or changed |
| `name_hints` | What each label was called in a conversation (used only to suggest names) |
| `profiles` | People cards of people with recognized names, each row with its source |
| `pieces` | The index of uploaded pieces, which is also the processing queue; `samples` is how many samples (16 kHz) a piece decoded to at its upload, NULL for pieces from before v0.5.0 |
| `trims` | Hour files trimmed (`trim.py`): the original, the copy kept, its state, and every index row as it was before (for a restore from a backup) |
| `cold_starts` | Recorder pieces whose piece right before was dropped by a trim: their start is faded in on reading |
| `late_spans` | Exact late-audio intervals, processing state and retry counts (since v0.4.0) |
| `processed_audio` | Actual processed coverage, including pauses, committed with final rows; prevents replay after restart (since v0.4.0) |
| `seg_emb` | Written only with `seg_emb` on in `tenants.json`: each final recognized segment's voiceprint, one row per segment (a split sentence's parts a row each, with their own span and vector): device, start and end (unix ms), which part of a split sentence (0: not split), the engine's speaker, the model file with its sha256's first 12 characters, the fp16 vector, `gain_db` (the calibration offset, in dB, its audio was given before the model: `device_gain`'s `offset_db`, none registered: 0; a segment over the time an offset starts has its start's), its sentence's `rowid` in `sentences` (`sentence`: segments can overlap and two sentences can start in the same ms, so neither a span nor a sentence id tells whose it is), its engine segment's place in its piece (`seg`, from 0), and that sentence's `start` as it went in (`sentence_start`); one row per `(sentence, seg, part)`, a second write of one is an error, never an overwrite. Kept only with its own sentence when that went in (none for one the late layer protected), deleted with its own sentence (finalized again) and its audio (a `trim.py` trim); a failed write is only logged and counted (the device status's `seg_emb_failed`), the sentences go in all the same. A voiceprint belongs to a sentence only when that row's `rowid` is its `sentence`, its `source` is the `device` and its `start` is the `sentence_start`, all three exactly (a segment's span tells nothing: a row can end before a segment in it starts); otherwise it belongs to none, finalizing again leaves it, and a trim drops it with its audio. A `rowid` is sure to hold only while the whole database file is copied as it is (the stopped-server archive, the SQLite backup API); rebuilding it from a SQL dump or `INSERT … SELECT` renumbers it, which leaves the voiceprints no one's, and only two sentences of one device starting in the same ms can be mixed up. So restore only whole databases, see [Backup and restore](../how-to/backup.md#restore) (since v0.5.0) |
| `conversation_aliases` | Old conversation links and speaker labels mapped to the retained conversation (since v0.4.0) |
| `walls` | Where long conversations were split (one row per device and time): late audio never merges the two sides back (since v0.5.0) |
| `sounds` | Environmental sound scores (only with [environmental sound](./configuration.md#optional-environmental-sound) on): one row per device, model and stretch of decoded audio, see [Environmental sound](#environmental-sound-sounds) below |
| `sound_wrong` | 「不对」 (wrong) marks on sound tags: the device, start and end ms, the event, the model it came from, the conversation id (none for a stretch of the sound bar outside conversations); see [Sound events](#sound-events-sound-events) below |
| `feedback` | Users' corrections to summaries |
| `todo_marks` | 「完成」 (Done) or 「不对」 (Wrong) marks on to-dos (since v0.2.0) |
| `stars` | Starred sentences: the sentence id, device and start, its conversation (only stars from before this change have one; the rest are looked up when read, as for reports), when it was starred (since v0.3.0) |
| `reports` | 「上报问题」 (Report a problem): the note, when it was reported, the page (live or conversation), the sentence id, device and start, (its conversation is not stored: it is looked up when read, as the closed conversation on that device whose span holds the start; none means the conversation is still going on, or the row is in no conversation), and the row as it read then (text, speaker, start and end, draft or final, device, ASR engine) |
| `mcp_links` | AI connection links: the name, the secret's SHA-256 hash and first 8 characters, when it was created and last used; never the secret itself (since v0.4.0) |
| `kv` | Key-value pairs such as each device's processing progress |
| `device_log` | Device events uploaded by the phones, including the recorders' own logs |
| `device_status` | Recorder status read by the clients (battery, card, firmware) |
| `device_gain` | Each device's gains by time: device, from when (unix ms), capture gain `capture_db` (reported by the device or written down), calibration offset `offset_db` (applied by the server after decoding, to −26 dBov; none: 0), source (`manual`, `reported`), note. An offset for the device's sensitivity is set from 0 ([Device gain](configuration.md#device-gain-device-gain)). See [Levels and gains](../explanation/architecture.md#levels-and-gains) (since v0.5.0) |
| `calls` | Duration, result and usage of each ASR and LLM call, without content |
| `balances` | Prepaid account balances, recorded hourly |
| `perf_stalls` | Times the event loop was held up: when it ended, how many milliseconds, in which functions (only in the first tenant's store; since v0.3.0) |
| `perf_samples` | The process, one row a minute: CPU seconds, memory, loop lag, thread pool use, queued pieces, open pages (only in the first tenant's store; since v0.3.0) |
| `perf_requests` | HTTP requests: one row for each that took 50 ms or more, and each minute one row per method, route and status (since v0.3.0) |
| `perf_pieces` | How long each step of each final piece and draft pass took: decode, VAD, ASR, voiceprints, store (since v0.3.0) |

The `perf_` tables hold durations and resource use only, never content, and keep 90 days. `GET /api/perf?day=YYYY-MM-DD` reads one day; `python -m alldayrec.perf export <live.db> <YYYY-MM-DD>` exports one from a copy of the store.

A voiceprint learnt before `enrolled` existed is one summary row per person, not tied to sentences. If it took in someone else's voice back then, `python -m alldayrec.bank relearn <live.db>` recomputes that row from the voices now under the person's name (from live recordings). It only prints, per person, the sentences before and after, the voices used and dropped, and the cosine between the old and the new voiceprint; `--apply` writes it, and then the server needs a restart (it loads the voiceprints at start). What was learnt sentence by sentence since stays. Run as `relearn --tenant <ID>` with `BG_GATE` on, for a tenant with environmental sound on, it only uses voices shown free of music ([Configuration](./configuration.md#optional-environmental-sound)).

A prompt change leaves existing summaries intact; `summaries.prompt_version` records each version. After deploying, run from the server checkout's `server/` directory:

```sh
# Set LIVE_RECORD_URL for the intended instance; token from LIVE_RECORD_TOKEN or stdin's first line.
uv run python -m alldayrec.resummarize --days 30 --dry-run
uv run python -m alldayrec.resummarize --days 30 --limit 1
uv run python -m alldayrec.resummarize --days 30 --delay 5
# a tenant with media_view on: its summaries go to 19
uv run python -m alldayrec.resummarize --days 7 --version 19 --dry-run
```

The window includes today and the previous N−1 calendar dates in the command's timezone, based on conversation start. Only closed conversations with at least 60 seconds of speech are processed. Older, open, pending and current-version summaries are skipped, as are short conversations with a manually requested summary. Check one before expanding the run. A rerun skips completed upgrades. Each redo finishes before the next starts, with a default 5-second gap; HTTP and model 429s back off, up to `--max-delay` (600 seconds). A timeout (`--timeout`, default 1800 seconds) or uncertain HTTP result stops the run: check whether the server is still processing before restarting. Failures exit with code 1. Output counts completed/failed/skipped, processed conversations' todos and marked todos before/after, and model calls made meanwhile (including other background work).

A successful old summary stays visible until success and survives model failure. Retrying a previously failed summary first sets it pending, so backfill waits for this attempt. Todos can store a pinned `id`: a unique match by text or quote with the same owner/recipient inherits the old ID. Done/wrong rows never move or duplicate. Unmatched or ambiguous marked old todos are carried into the new summary unchanged with the same ID and mark, rather than rejecting the replacement. Other new actions get fresh IDs to avoid reusing an old action's ID. Rename/merge updates todo owner/recipient labels immediately; undo restores the changed fields by action ID, including when merging into an existing person. Regeneration, undo and remark operate on one ID. Older summaries still derive their original IDs from time/text until their first regeneration pins them. Stars live in `stars`; summary regeneration changes neither sentences nor stars.

Existing daily recaps stay unchanged. On read, pinned IDs resolve to the current action and mark; older unpinned entries are matched by text/recipient/due. Missing actions are displayed without a mark control so a stale recap cannot mark another action. After backfill, regenerate affected dates with existing recaps via `POST /api/recap/{YYYY-MM-DD}` (`recap_redo`) to refresh highlights and todo references. This adds one model call per date with summaries, at most 30 calls for 30 days. For 100 conversations, expect 100 summary calls, up to 400 with all four attempts. Missing name hints can add one hints call per conversation, and each named participant with an existing profile triggers a profile call. With the tenant's `place` on, each summary written makes one more call for its place (one more request when the main model fails over to the fallback). Recaps, hints, places and profiles are additional to the 100. The script reports actual ledger counts; money depends on the configured model and input length.

Once a tenant turns `place` on (see [Configuration](./configuration.md#tenants-json)), the summaries written before have no place. To add only the place, without rewriting the summaries, run from the server checkout's `server/` directory (with the instance's own `.env` for the LLM):

```sh
uv run python -m alldayrec.place backfill --tenant <ID> --days 7 --dry-run   # only the count and the estimated input tokens
uv run python -m alldayrec.place backfill --tenant <ID> --days 7
```

It takes the conversations started in the last N days (today included) whose summary made an LLM call and has no `place` yet, one at a time (exit code 1 when any failed; a rerun does the rest), and adds that one field to the summary JSON: titles, todos and their marks, and people profiles stay as they are. A summary rewritten meanwhile is skipped; the new one makes its own. It exits when the tenant has `place` off.

With `place_sensitive` turned on later, the places left empty as sensitive can be guessed again on their own (`--dry-run` first for the count):

```sh
uv run python -m alldayrec.place backfill --tenant <ID> --days 7 --redo-sensitive --dry-run
uv run python -m alldayrec.place backfill --tenant <ID> --days 7 --redo-sensitive
```

The store keeps no reason for an empty place, so it picks: an empty place from the default prompt (`prompt_version` 2) whose sentences said to people have a word of the sensitive list. An unsure one that happens to have such a word (卫生间, a restroom) is asked again too; a refusal with no such word in its sentences is not picked. A redone place is version 3 and never picked again. It exits when the tenant has `place_sensitive` off.

## Environmental sound (sounds)

The key is (`device`, `model`, `start_ms`). `model` is the model's name and the first 12 hex digits of its file's sha256 (for example `mn10_as@58d4c290739e`): another model file is another model, and the old rows stay as they are. Each row is one stretch of decoded audio, at most 300 s:

| Column | Content |
|---|---|
| `start_ms`, `end_ms` | Start and end of the stretch (ms timestamps) |
| `hop_ms`, `window_ms`, `n` | The frames' spacing, their length, their count: `mn10_as` one frame per 10 s (a last bit under 2 s goes into the frame before), `yamnet` one 0.96 s frame every 0.48 s |
| `db` | Each frame's level, dBFS rounded, one int8 per frame |
| `top` | Each frame's 10 highest-scoring classes: zlib of `n × 10` (class uint16, score float16). Classes are AudioSet's, in the model's own order (`mn10_as` 527, `yamnet` 521). Cleared for `yamnet` after 30 days (by the hourly scoring loop of a tenant with sound on; the backfill command does not) |
| `minutes` | One entry per minute (a frame belongs to the minute of its middle): zlib of (Unix minute int32, frames uint16, each class's mean score float16, each class's frames scoring 0.2 or more uint8). Kept |
| `gaps` | The time in the row with no piece at all (JSON, `[[from, to], …]` ms): the model got zeros there, it does not count as scored |
| `gain_db` | The calibration offset (dB) this row's input had before the model (`device_gain`'s `offset_db`; none registered: 0). A stretch over the time an offset starts is two rows, each with its own |
| `at` | When the row was written |

Scoring a stretch again replaces its row. A row covers only decoded audio: it ends at a piece that will not decode or decodes short, which gets no row, so no row means not scored yet, or not successfully. A gap of at most 0.5 s with no piece between two pieces goes into the same row and into its `gaps`. What is scored = each row's `start_ms`–`end_ms` less its `gaps` (the trim gate and the scoring loop both go by this, to the millisecond). Whether a piece decoded in full is checked against `pieces.samples`, its sample count at upload, exactly: one sample short and its last millisecond is not covered. Older pieces have no `samples`, only `seconds` (to the millisecond), so a decode up to 1 ms short may go unnoticed there. A piece that comes into a gap later gets a row of its own, the first row never deleted, so a minute's frames may come from two rows. Measured on real recordings, an hour of audio takes about 80 KB for `mn10_as` and 250 KB for `yamnet` (60 KB after 30 days).

## Sound events (sound_events)

For a tenant with environmental sound on (`SOUND_MODEL` plus `"sound": true`), the web pages and MCP make a few sound events from the scores in `sounds`. They are not stored: each is made from the scores when asked (`sound_events.py`), from the model `SOUND_EVENTS_MODEL` names (default: the first of `SOUND_MODEL`). With sound off nothing is made or shown, and every API answers as before.

Only the four the evaluation found reliable, per minute:

| Event | Rule (`mn10_as` / `yamnet`) |
|---|---|
| Speech `speech` | the minute's highest mean score of a speech class (Speech, the speech kinds, Conversation) is 0.3 / 0.2 or more, and the minute is not quiet |
| Music `music` | the highest mean score of a music, singing or instrument class is 0.3 / 0.1 or more |
| In a vehicle `vehicle` | the highest mean score of a vehicle, engine, traffic, rail or aircraft class is 0.3 / 0.2 or more, for at least 3 minutes in a row (a car passing by is not a ride). A car, a bus, a subway and a plane are not told apart |
| Quiet `quiet` | the minute's level (the mean power of its frames) is under −55 dBFS (−60 uncalibrated: `gain_db` 0, no device gain registered or one of exactly 0 dB), with no music |

- The thresholds were set on a set of hand-labelled 10 s windows (84, calibrated, provisional labels left out): speech at 0.3, quiet minutes left out, found 24 of the 26 with speech, and 24 of the 40 it named had speech (videos and calls on a speaker count as speech too); music at 0.3 found 12 of 13, and 12 of the 13 it named had music; vehicle at 0.3 found 3 of 4, 3 of 6 named, hence the 3 minutes in a row; quiet found 13 of 17, 13 of 16 named. The sample is small and `yamnet`'s are for comparison; they are to be set again on more data.
- Media played aloud (TV, videos, calls on a speaker), typing, footsteps, clothes rubbing, wind, crowds, hum, dishes and turn signals are not told reliably and never shown.
- Frames left out: a row's `gaps`, and frames at −127 dBFS (digital silence: the microphone sent nothing). A minute with no frame left is no data, never quiet.
- Confidence `conf` (0–1): the share of the minute's frames where the event's class scored 0.2 or more (quiet: where the level was under the cut). `yamnet`'s music threshold 0.1 is under 0.2, so a minute of its music can have `conf` 0. The pages word every event as 「可能」 (maybe).
- A conversation's sound tags: the minutes of each event over its device and its start to end; music, vehicle and quiet with at least 2 minutes and at least 10% of its minutes with scores, most first, at most 3; quiet over half of them reads 「大半时间安静」 (mostly quiet). The minute at each end counts whole once a frame of it falls in the conversation. Speech is not a tag. An old conversation with no device has none.
- `GET /api/sounds/day?date=` gives each device's minutes of the day as runs (the same events in the same conversation make one run); time with no scores has none. `GET /api/search?sound=music|vehicle|quiet` returns only the conversations with that tag. It looks at 50 conversations a call at most: when that leaves fewer than `limit` found, the header `X-Searched-To` says where it stopped, the `before` to go on with (the web pages go on by themselves); any other value is a 400.

`sound_wrong`: one row for each 「不对」 (wrong) tapped on a tag or on the sound bar, to grow the hand labels later.

| Column | Content |
|---|---|
| `id`, `at` | Its id, when it was tapped |
| `device`, `start_ms`, `end_ms` | Which stretch of which device (a conversation's tag: the conversation's start and end; the bar: that run) |
| `event` | Which event is wrong (`speech`, `music`, `vehicle`, `quiet`) |
| `model` | The model it came from (name and the first 12 hex digits of its file's sha256) |
| `conv` | Its conversation id, empty when there was none |

A 「不对」 holds for a stretch it covers more than half of: a conversation later grown by late audio or merged with another, or the bar's runs cut anew, still shows it. It holds whatever the model: after a new model file (another sha; the tags and the bar then use the new model's scores only, once they are made) too. Tapping 「撤销」 (undo) deletes the row. Export: `GET /api/sounds/wrong` (JSON, by id), or `sqlite3 live.db "SELECT * FROM sound_wrong"`.
