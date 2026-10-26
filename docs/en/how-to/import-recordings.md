---
type: how-to
description: "Import recordings you already have (MP3, WAV, m4a, Plaud exports) into AllDayRec on the web page Import recordings."
---

# How to import recordings you already have

Pick the audio files on the web page, check their start times and upload them; the transcripts land on the timeline on the days they were recorded. To import from a script, see [Import from a script](#import-from-a-script).

Long recordings can be imported directly: the server decodes and processes them in bounded blocks, and playback reads a bounded window, so you do not need to split them first. Leave space in `DATA_DIR` for the original file and in the system temporary directory (`TMPDIR`) for upload copies and decoded audio (about 115 MB per hour of mono PCM). FLAC-in-Ogg playback decodes to a temporary file before reading the window to avoid an ffmpeg seek misalignment.

## What you can import

| What you have | Can it be imported | How |
|---|---|---|
| A voice recorder or phone recordings (MP3, WAV, `.m4a`) | Yes | Upload as is |
| Plaud | Yes | Export MP3 or WAV in the Plaud app, then upload it |
| Anker × Feishu 录音豆 | Yes, connect it directly | The Android app connects to the bean directly and uploads what it records after the binding, see [How to bind a recorder](./bind-recorder.md#feishu-recorder-bean). For recordings from before the binding, download them one at a time from the Feishu Minutes web page, then upload them |
| OPPO 心力球 | No | It keeps no original audio, so there is no audio to import |

The sources for the third-party products are in [What AllDayRec is](../explanation/intro.md#how-it-compares).

## Import on the web page

1. Open the web page's Me page and tap Import recordings. In the app: Me › Device status and more › Import recordings.

   ::: tip Android app
   In Android app 1.5.5 and earlier, Choose audio files does nothing. Import from a browser on the phone or a computer instead.
   :::

2. Tap Choose audio files. You can pick several at once.
3. Check each file's start time: it sets the day and time its conversations land on. The page guesses it in this order, and says under each file which one it used:
   - **Metadata**: the time in an `.m4a` file's header (the page takes it as the end of the recording and subtracts the length), and the recording time in a recorder WAV file's `bext` chunk;
   - **File name**: e.g. `20261020_093000`, `2026-10-20 09.30.00`, `录音 2026-10-20 09-30`;
   - **Modified time**: the file's modified time minus its length. A copied file's modified time is when it was copied, so this guess is often off.

   Change any start that is wrong. The page warns when two files overlap, or when a file ends in the future.
4. Tap Import N files. The files go up oldest first, each with its upload progress. If one fails, tap Retry, or tap × to take it out of the batch.
5. When the upload is done, the page lists the days the conversations fall on; tap a day to open its timeline. Transcription takes a while; Past imports at the bottom of the page shows how long is left.

Each batch has a device name of its own, by default `import-` and the moment of the import (e.g. `import-20261026-093000`); you can change it under Batch name. Batches are listed under Past imports.

A conversation with less than 10 seconds of speech is not kept as a conversation; its sentences stay in the transcript (see [Architecture and data flow](../explanation/architecture.md#conversations-summaries-and-the-recap)).

## Import from a script

Importing goes through the HTTP endpoint `POST /api/chunks`: one file per request, the body a whole audio file, decoded by ffmpeg on the server. A script follows three rules, as the web page does:

- **Use a new device name for each batch**, e.g. `import-2026-10`. The next rule holds per device name. Devices named `import` or starting with `import-` count as import batches and are listed under Past imports on the Import recordings page.
- **Upload oldest first.** Under one device name, audio earlier than where the server has already processed is kept but not transcribed.
- **After the last file, tell the server the batch is over.** Otherwise the last stretch keeps waiting for more audio and never gets a final transcript.

Steps:

1. Upload a file. `start` is the moment the recording began (Unix ms). The example uses GNU `date`; on macOS use `date -j -f '%Y-%m-%d %H:%M:%S' '2026-10-20 09:30:00' +%s` instead.

   ```bash
   TOKEN=<your token>
   curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: audio/mpeg" --data-binary @rec.mp3 \
     "https://<your-server>/api/chunks?device=import-2026-10&start=$(date -d '2026-10-20 09:30:00' +%s)000"
   ```

   On success, `seconds` in the answer is the file's length in seconds. A 400 means the server can't decode the file. With a `Content-Type` other than `audio/ogg`, `audio/webm` or `audio/aac`, the server stores the file as it is and ffmpeg detects the format itself.

2. Upload the batch's other files in time order.
3. End the batch. `t` is the moment the last file ends (Unix ms), that is, its `start` plus the seconds step 1 returned:

   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" "https://<your-server>/api/chunks/end?device=import-2026-10&t=<end-time>"
   ```

   It returns `{"ok":true}`.

[`firmware/devkit/lr_sync.py`](https://github.com/suyiiyii/alldayrec/blob/main/firmware/devkit/lr_sync.py) is a Python client that reads a recorder's card and uploads it, for reference. The endpoint's parameters are in [HTTP API](../reference/api.md#api-chunks).
