---
type: explanation
---

# Architecture and data flow

AllDayRec is a Python server plus a few clients. Audio comes in from a recorder or a phone. The server runs speech detection and voiceprints locally, transcribes with a cloud or local ASR engine, summarizes with the LLM you configure, stores the results in SQLite, and keeps the original audio on disk.

<a class="only-light" href="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-wide-en-light-name-20261026.png">
<picture>
  <source media="(max-width: 600px)" srcset="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-narrow-en-light-name-20261026.png">
  <img alt="Architecture: the open recorder, the Feishu bean and the phone mic upload through the phone app, the browser's /record page uploads directly; the self-hosted server stores the audio, then runs VAD, ASR and speakers, and an LLM writes summaries; the web UI, MCP and CLI read the results" src="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-wide-en-light-name-20261026.png">
</picture>
</a>
<a class="only-dark" href="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-wide-en-dark-name-20261026.png">
<picture>
  <source media="(max-width: 600px)" srcset="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-narrow-en-dark-name-20261026.png">
  <img alt="Architecture: the open recorder, the Feishu bean and the phone mic upload through the phone app, the browser's /record page uploads directly; the self-hosted server stores the audio, then runs VAD, ASR and speakers, and an LLM writes summaries; the web UI, MCP and CLI read the results" src="https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/architecture-wide-en-dark-name-20261026.png">
</picture>
</a>

The diagram's source is [`docs/diagrams/architecture.drawio`](https://github.com/suyiiyii/alldayrec/blob/main/docs/diagrams/architecture.drawio); edit it in draw.io.

## The parts

| Directory | What |
|---|---|
| `server/` | The server: upload queue, VAD and voiceprints, ASR engines, conversations, summaries, MCP (Python 3.11, uv) |
| `web/` | The web UI: timeline, live, people and me, plus the conversation, calendar, to-do and device pages (Vue, pnpm) |
| `app/` | The phone app for iOS and Android: records the phone's mic or the BLE recorder, uploads, firmware updates (Flutter) |
| `firmware/devkit/` | The BLE recorder firmware for nRF52840 boards (Zephyr / nRF Connect SDK), based on the Omi devkit |
| `firmware/selftest/`, `firmware/blerelay/` | Board self-test firmware; an ESP32 BLE relay used for testing |
| `hardware/lr-p3/` | The LR-P3 recorder's parts, wiring and 3D-printed case |
| `cli/` | `lr.py`: read your conversations from a terminal or an agent |

## How audio comes in

The recorder writes to its SD card all the time. While it is connected to the phone, it streams the same record to the phone too; for the time it was disconnected, the phone reads that part back from the card when it returns and fills the gap. The phone app cuts the audio into short Opus pieces and uploads them to `POST /api/chunks`; while offline, the pieces queue on the phone and go up later.

The server first saves each piece to disk, then hands it to that device's pipeline in time order. So a client that was offline for a long time can upload its whole backlog at once when it comes back, and other devices are not affected. Anything that can POST an audio file can be an audio source; this is also how you [import recordings you already have](../how-to/import-recordings.md).

## Two transcription layers

The server finds where people speak with FireRedVAD, locally, and then transcribes in two layers:

- **The final layer** runs all the time. It cuts the audio at pauses into pieces of up to 5 minutes and sends each piece once to the ASR engine (`ENGINE`); the result is the final transcript.
- **The draft layer** runs only while a web page is open. At each pause (or every `REFINE_EVERY_S` seconds while no pause comes) it sends the new audio to `DRAFT_ENGINE`, so words show within seconds; with `DRAFT_ENGINE=qwen-stream` it streams the audio to Qwen continuously instead, and words show as they are said. Drafts show only for the time the finals don't cover yet, and the finals replace them when they arrive.

The text and the sentence splits come from the ASR engine; the server does speech detection and speaker identity locally, and the table below shows where each engine's speakers come from. Before audio goes to the engine, silences longer than 3 seconds are cut to 3 seconds (`SQUEEZE_KEEP_S`).

The engines differ in where the speakers come from:

| `ENGINE` | Provider | Speakers |
|---|---|---|
| `qwen` | Alibaba Cloud Bailian, Qwen-Audio file transcription | from the engine, refined by local voiceprints |
| `volc` | Volcengine Doubao Seed-ASR 2.0 | local voiceprints |
| `stepfun-sse` | StepFun streaming ASR | local voiceprints |
| `stepfun`, `stepfun-file` | StepFun file mode | from the engine, refined by local voiceprints |
| `local` | sherpa-onnx X-ASR zh-en in the server process, text only | local voiceprints |

For the accuracy of the local engine against the cloud engines, see [ASR benchmark](./asr-benchmark.md).

## Knowing who spoke

The voiceprint model ReDimNet2 runs on the local CPU. The server recognizes 「我」 (me: the person recording), and within a conversation it labels the other people S1, S2 and so on; sentences too short to recognize are labeled S?. Once you give a label a name in the web UI, the same voice is recognized in later conversations.

## The compute interface

Voiceprints, music detection and speaker-change detection (pyannote, which finds where a sentence changes speaker) are the three heaviest computations in the server, and all sit behind one compute interface (`Compute` in `server/alldayrec/compute.py`). It takes 16 kHz int16 samples and returns a voiceprint, whether they are music, or the speaker probabilities of each frame of a 10 s chunk. It reads no database and no audio files, and it does not know whose data it is computing.

- **In the process by default**: without `INFER_URL`, the server computes them itself on onnxruntime, exactly as before.
- **As a separate service**: `alldayrec --infer`, the same command with one flag, offers only these computations, and can run on another machine, one with spare CPU or a GPU. With `INFER_URL` and `INFER_TOKEN` set, the server POSTs the samples to it and never loads or downloads these three models itself, so the server's machine needs no CPU or memory for them. When the service is down, hung, gives a 401 or 403, or answers with something that is not a voiceprint or frames, the call waits and tries again, 2 s later, then 4 s, 8 s and up to every 60 s, until it is answered. Meanwhile uploads are taken and stored as usual and the web pages open; only that device's transcripts and speakers wait, and they catch up once the service is back, without losing a sentence. A late span (older audio uploaded afterwards) is not claimed while the service is failing, nor after a restart whose first `/health` check failed: the server pays no ASR for it and spends none of its tries, and only asks `/health` as the backoff allows, starting once it answers. A span already being processed waits for the service, and the wait is not counted against its timeout; a stop meanwhile gives its claim and that try back. A span gets this at most 3 times (each claim pays its ASR again), which only a service failing again and again during that span, with a stop each time, uses up; after that a stop is an interrupted claim as any other, and 3 tries make it `failed`. Learning voiceprints (after a speaker is renamed, the own-voice card) waits for the service too. An error is logged at most once a minute, and `lr_infer_down_seconds` says how long the service has been failing, for an alert. At start the server asks the service's `/health` (with the token) for its model files and voiceprint size; when they are not the server's, every call waits the same way and the log says why. Samples are sent as 16-bit integers (`application/octet-stream`), a calibrated device's as 32-bit floats (`application/x-f32le`, [Levels and gains](#levels-and-gains)) once `/health` lists `f32le` among its `formats`; an older service gets them rounded to 16 bits, with one warning in the log. Upgrade the service before the server.

Speech detection (VAD), ffmpeg decoding and the clustering that tells speakers apart stay in the server process. The VAD runs one stream per device and carries its state from one piece to the next, and one call costs less than a network round trip. Decoding reads the audio on disk, and clustering reads the tenant's voiceprints, so these stay with the database and the audio. To deploy it:

```sh
# On the inference machine: the same model files as the server's, the missing ones downloaded to MODELS_DIR at the first start
INFER_TOKEN=<a random token> INFER_HOST=0.0.0.0 INFER_PORT=8771 alldayrec --infer
# In the server's .env: reach it only over a private network, Tailscale or an HTTPS reverse proxy (never through a proxy from the environment)
INFER_URL=http://infer.example.com:8771
INFER_TOKEN=<the same token>
```

`GET /health` needs no token; `GET /metrics` needs it and returns each computation's calls, compute seconds and audio seconds. For the variables, see [Configuration](../reference/configuration.md#other-environment-variables).

A request takes at most 600 s of audio, and speaker-change frames (`/segment`) at most 10 s; over that the service answers 413. Audio the model will not take (onnxruntime calls the input invalid) gets a 422. Both mean the request itself is wrong and no retry can help, so the server neither waits nor tries again: it logs an error, the call fails, and the calls after it go on.

Upgrade the inference service and the server together, to the same version. The server wants exactly its own model files in `/health`, so when a version changes the models (adding speaker-change detection, for one), neither side upgraded first matches the other: every call waits until the other side is upgraded too. Uploads are taken meanwhile, and they catch up after the upgrade.

## Levels and gains

Recording devices differ in level: the same words through a phone, a recorder and an imported file can be tens of dB apart. Every device is brought to one standard level: the wearer's own speech at an active speech level (ITU-T P.56) of **−26 dBov**. In two steps:

1. **Capture gain**, set on the device: a recorder's PDM gain, a phone's gain before its encoder in the app. As high as it can be without the loudest real sound clipping. When a device's reported value changes (`mic_gain_db` in a recorder's status, `capture_gain_db` with a phone's uploaded piece), the server writes a row to the `device_gain` table (source `reported`); it is a record only and never changes the audio.
2. **Calibration offset**, applied by the server: after decoding, a fixed number of dB (`offset_db`) multiplied in floating point maps the device's level to −26 dBov, losing nothing. It is only set by hand: `server/gain.sh set <tenant> <device> <dB>` (or `python -m alldayrec.gain` locally); `server/gain.sh list` lists it. An offset for the device's own sensitivity is set from `--since 0` (the default), so history voice clips that have no time get it too ([Device gain](../reference/configuration.md#device-gain-device-gain)).

Each `device_gain` row holds from its `since_ms` on: for a device and a time, each of the two columns is its newest non-empty value with `since_ms` no later than that time. Each sample takes the offset of its own time: a piece, a cut or a listening poll over a `since_ms` is split there. With no row the offset is 0 dB and the audio is exactly as before, byte for byte.

The offset is applied where `audio.py` decodes each piece (`samples`, and `piece_window` and `piece_blocks` for long imports), once, so every reader of the audio gets the same gain: transcription and the VAD, voiceprints, music detection, late audio, `trim.py`'s VAD, playback on the web and in the app (`/api/audio`), listening in (`/api/listen`) and the history voices' samples (`/api/voice-clip`). With an offset the audio goes on as 32-bit floats, not rounded and not limited (past full scale too), all the way into the models (the VAD, voiceprints, music detection, segmentation; to a separate inference service as `application/x-f32le`). It becomes 16-bit integers only where it must, once: a WAV that is played, and what an ASR engine is sent (after squeezing and lifting). There, samples above 0.9 of full scale go through a soft limiter that keeps them under full scale (never a hard clip; below 0.9 every sample is only rounded), and those samples are counted in the metric `lr_gain_limited_samples_total` (samples into the limiter on the way to 16 bits). The adaptive gains come after the offset: the VAD's `VAD_GAIN_MAX_DB` and `ASR_GAIN_MAX_DB` before the engine work on audio that already has it. The stored bytes and the levels recorded at upload (`pieces`' rms and envelope) stay as they came. `/api/audio` (with a device) and `/api/listen` answer with an `X-Gain-Db` header: how many dB louder their first sample of audio is than the stored audio (a later `since_ms` inside the response changes it from there on).

Not covered: listening in the app itself (the phone plays what it records, the server is not in the way), the offline tools without a device (`speaker_eval.py`, `volc_asr.py`'s command line) and a question asked by voice in the browser (`/api/ask/voice`, the browser's microphone, no device). `replay_case.py` takes the offset from its case's `meta.json` (`offset_db`, none: 0) and keeps its results apart (`work@<dB>/`).

Audio under an offset plays back at that offset alone; a recorder's audio under none (before its first `since_ms`, or not registered yet) still plays `PLAY_GAIN_DB` louder. Once every recorder has an offset, `PLAY_GAIN_DB` can go.

## Where recordings are kept

Every read and write of recording bytes in `server/alldayrec/audio.py` goes through one storage interface (`store.py`): append (local only), read by position and length, write whole, exists, delete. Each piece's row in the `pieces` table holds its path, position and length. By default (`STORAGE=local`) every recording is in the `recordings` directory. With `STORAGE=s3`, an hour file lives like this:

1. **Open (local)**: the hour's pieces are appended to its local file as before; the client is answered only after the fsync and the index row's commit.
2. **Sealed**: once the hour is over and no piece has come for `SEAL_IDLE_S` (default 600) seconds, a background task uploads the whole file as one object, checks its size and SHA-256 (the PUT is signed with the content's SHA-256, so the store refuses bytes that arrive otherwise), points those rows at the object (paths starting with `s3:`) in one transaction, and only then deletes the local file. Local disk therefore holds about the current hour and the one being sealed.
3. **A late piece** (in an hour already sealed): fsynced to a local staging file `staging/<hour file>.<gen>` and indexed, then the client is answered. A merge runs per hour, in batches: once that hour has had no late piece for `MERGE_IDLE_S` (default 90) seconds, or its staging passes `MERGE_MAX_BYTES`, or the oldest staged piece has waited `MERGE_MAX_S` (default 1800) seconds, it downloads the object, appends the staged pieces in order, uploads that to the same key, checks it, updates those rows' path and position in one transaction, and deletes the staging file. The object's bytes before stay as they were, so every earlier row stays valid, and a reader of the old version reads the same prefix. A merge starts the next gen: pieces arriving meanwhile go to the next staging file.
4. **Reads**: a row pointing at a local or staging file reads that file; one pointing at the object sends one GET with a Range for just that piece. A file deleted by a seal or merge while it was being read is read again from the row as it is now. Playback (`/api/audio`) puts each piece at its own time: where no piece has audio (a gap between two, before the first, after the last) the WAV is silence, so a second of it is a second of the recording and the conversation player stays on the sentence it highlights.
5. **A longer copy of a piece sent again** (today's rule keeps the longer one): it is appended the same way (local, or the staging file when the hour is sealed) and the row is replaced.
6. **Imports** (a piece longer than 5 minutes): a local file of its own, uploaded whole as its own object at once; never merged.

Crash-safe: before each upload the `store_objects` table records the object's expected size, SHA-256 and the rows to update. A crash after the upload and before the commit is found on restart by checking the object: if it matches, only the transaction is applied; if not, the upload is done again. Every upload is a conditional write: a new object only where its key does not exist yet, a merge only over the version that was sealed (by its ETag), so no other instance's object is overwritten. Local and staging bytes are deleted only once the object is verified, the transaction committed, and no row points at them any more; a sealed hour file only when it is exactly the object's bytes (one written again after a spell of `local` stays). One seal or merge per object at a time. With the object store unreachable, uploads are unaffected, sealing and merging back off and retry, and local disk grows; sealed audio that cannot be read answers 503 to playback, and transcription waits at that piece until the store is back.

Each tenant's objects are under `<S3_PREFIX><tenant id>/`, a prefix recorded in the tenant's own store at its first use, so no tenant reads or writes another's, and a tenant merged into another instance keeps its prefix. Metrics: `lr_store_local_bytes` (bytes of hour files not sealed yet and of staging files; recordings from before `s3` not counted), `lr_store_pending_seal` (files due to be sealed and not yet), `lr_store_pending_merge` (hours with staged pieces), `lr_store_errors_total{op,kind}`, and `lr_store_seconds_count{op}` / `lr_store_seconds_sum{op}` (calls, failures and time of each kind of object-store call and of each seal and merge; `kind` tells the store unreachable (`unreachable`) from a write refused because the key holds another object (`conflict`), a store that gives no ETag (`no_etag`) and anything else (`other`); retrying mends neither of the middle two, logged as ERROR). `lr_store_pending_seal` staying above a few, or `lr_store_local_bytes` that keeps rising, means the object store has a problem: alert on them. Variables: [Configuration](../reference/configuration.md#optional-recordings-in-object-storage).

**Keeping only the parts with speech (`trim.py`, an offline tool run by hand).** Recordings without speech can go: an hour file is trimmed once its newest piece arrived `TRIM_AFTER_DAYS` (default 2) days ago (the nightly backup has the original by then), its device has been finalized past it, no late span is still open over it, and, if sealed, nothing of it is staged or being merged. The server's own FireRed stream VAD (with the same gain) finds the speech at `TRIM_VAD` (default 0.1, far wider than the 0.3 transcription is sent by); `TRIM_PAD_S` (default 10) seconds are kept on each side of the speech and of the device's sentences; a piece touching those is kept, so is one the VAD did not hear or was not given (undecodable, or arrived or replaced by a longer resend after the VAD ran), and the other pieces go. With [environmental sound](../reference/configuration.md#optional-environmental-sound) on (`SOUND_MODEL` set) every tenant has a gate too: while any millisecond of the pieces to drop has no scores of the gate's model yet, the hour is left whole (reason `no sound`); backfill first, then trim. Pieces go whole: the kept ones are copied as they are, in order, into `<HH>.trim.<ext>` beside it (a local file, or an object of its own), never re-encoded; once checked, one transaction points the kept rows at it, deletes the dropped rows and records the hour in `trims` with all its rows as they were. The original stays until `purge`, which by default only lists what it would delete and deletes with `--yes`. It deletes an original only when `--confirm-backup` names the time a backup began that was checked to hold the recordings and the hour was trimmed before that time, `TRIM_PURGE_AFTER_H` (default 24) hours have passed since the trim, the copy is still the bytes written (size and SHA-256), no row points at the original and it is still the bytes trimmed from. A sealed original's `store_objects` row goes first, so a late piece afterwards starts a new hour file. A local original is checked and deleted under the lock every append to it takes, so a late piece arriving meanwhile waits and then goes into a new hour file: the service need not be stopped. Only one `apply`, `purge` or `seal adopt` of a tenant runs at a time (a lock file beside its `live.db`; a second one exits), and all three take only `--tenant`. Crash-safe: a row in `trims` comes before the copy is written (a local copy is written under another name and renamed), and a copy whose transaction never committed is deleted or written again on the next run; an hour already trimmed is not written again by an apply with an older plan. Playback places each piece at its own time, so a dropped stretch is silence and the kept audio does not move; the web player skips windows with no recording up to the end of the conversation. What is kept decodes as before, except a recorder's piece right after one a trim dropped (in this hour or at the end of the one before; recorded at the trim): its decoder is no longer primed by the piece before, so its first 40 ms are muted and the next 60 ms faded in (otherwise a click up to 28 dB loud), and the rest of that piece may differ from before by a few least significant bits (around −75 dBFS, inaudible). That start is at least `TRIM_PAD_S` from any speech the VAD heard and any sentence, or the dropped piece would have been kept. A piece after a real gap (a reconnect) is not touched: it plays as it always did, speech in it too. Limits: the VAD runs per device and day over the hours that may be trimmed, so next to midnight or to an hour left whole the VAD's padding is on one side only (a sentence's padding still counts); and sentence times and hour file names are in the instance's time zone, so `trim.py` must run in that zone (nothing checks it: in another zone no sentence is found near a piece and only the VAD keeps it).

```sh
cd server
uv run python -m alldayrec.trim vad --db <copy>/live.db --recordings <copy>/recordings > vad.jsonl   # heavy: run it on a copy
uv run python -m alldayrec.trim plan  --tenant main --vad vad.jsonl   # prints what it would do, changes nothing
uv run python -m alldayrec.trim apply --tenant main --vad vad.jsonl   # trims
uv run python -m alldayrec.trim purge --tenant main --confirm-backup 2026-11-12T03:00   # lists the originals it would delete
uv run python -m alldayrec.trim purge --tenant main --confirm-backup 2026-11-12T03:00 --yes   # deletes them
```

## Conversations, summaries and the recap

Sentences are grouped into conversations by time. A conversation ends after 5 minutes with nobody speaking, when the recorder stops, or when it reaches 4 hours. A conversation with less than 10 seconds of speech is not kept as a conversation (its sentences stay in the transcript).

Time alone lets voices around, in a mall or on the street, chain unrelated conversations into one. So before a longer conversation (`SPLIT_MIN_S`, 10 minutes by default) is summarized, the LLM also reads it for content: where other people and another matter take over, at a real pause, it is split into parts, each with its own summary. Late audio never merges the parts back.

When a conversation ends, the server writes a summary with the LLM you configured: a title, the summary, each person's points, to-dos (with due dates), decisions and tags. A conversation with under 60 seconds of speech doesn't go to the LLM; its first sentence becomes the title. After the summary, one more LLM call checks the open to-dos of the 14 days before: one a sentence says is done or dropped is closed, one that only looks done gets a hint, and the same to-do said several times becomes one. Every day, to-dos over 7 days past due with no mention since move to "probably not needed". Each keeps its reason and the sentence, and can be undone (`todo_auto.py`). Every day at `RECAP_AT` (default 10:00), the server writes the previous day's recap.

「问 AI」 (Ask AI) answers questions about your past with the same LLM: the summaries of the last few days go into the prompt, and older or more detailed content is found with tools that query the database. Every claim carries the id of the conversation it came from.

## Who reads this data

The web UI, the web pages inside the app, the [MCP endpoint](../reference/mcp.md) and the [CLI](../reference/cli.md) all read the same tenant's database. The database is SQLite, so you can also read it directly yourself; the tables are in [Data layout](../reference/data.md), and how they relate and how the processing loop moves on in [Domain model](./domain-model.md).

One server can serve several people, each with a token, a database and a recordings directory (`tenants.json`), isolated from each other in the same process.
