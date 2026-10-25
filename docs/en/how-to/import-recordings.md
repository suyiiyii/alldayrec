---
type: how-to
description: "Import recordings you already have through POST /api/chunks: MP3, WAV, converted m4a and Plaud exports, plus the rules that get them transcribed."
---

# How to import recordings you already have

The app and the web UI have no import button yet. Importing goes through the HTTP endpoint `POST /api/chunks`: one file per request, the body a whole audio file, decoded by ffmpeg on the server.

## What you can import

| What you have | Can it be imported | How |
|---|---|---|
| A voice recorder or phone recordings (MP3, WAV) | Yes | Upload as is |
| `.m4a` (e.g. iPhone Voice Memos) | Yes, after converting | A long `.m4a` can't be decoded from a pipe; run `ffmpeg -i in.m4a out.mp3` first |
| Plaud | Yes | Export MP3 or WAV in the Plaud app, then upload it |
| Anker × Feishu 录音豆 | Yes, connect it directly | The Android app connects to the bean directly and uploads what it records after the binding, see [How to bind a recorder](./bind-recorder.md#feishu-recorder-bean). For recordings from before the binding, download them one at a time from the Feishu Minutes web page, then upload them |
| OPPO 心力球 | No | It keeps no original audio, so there is no audio to import |

The sources for the third-party products are in [What alldayrec is](../explanation/intro.md#how-it-compares).

## Rules

- **Use a device name of its own**, e.g. `import`. A new name for each batch (e.g. `import-2026-10`) is the least hassle: the next rule holds per device name, so a new batch is transcribed even when it has recordings older than the last batch's.
- **Upload oldest first.** Under one device name, audio earlier than where the server has already processed is kept but not transcribed.
- **After the last file, tell the server the batch is over.** Otherwise the last stretch keeps waiting for more audio and never gets a final transcript.

## Steps

1. Put the token in an environment variable:

   ```bash
   TOKEN=<your token>
   ```

2. Upload a file. `start` is the moment the recording began (Unix ms); it sets the day and time its conversations land on.

   Linux (GNU date):

   ```bash
   curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: audio/mpeg" --data-binary @rec.mp3 \
     "https://<your-server>/api/chunks?device=import&start=$(date -d '2026-10-20 09:30:00' +%s)000"
   ```

   macOS (BSD date):

   ```bash
   curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: audio/mpeg" --data-binary @rec.mp3 \
     "https://<your-server>/api/chunks?device=import&start=$(date -j -f '%Y-%m-%d %H:%M:%S' '2026-10-20 09:30:00' +%s)000"
   ```

   On success, it returns the file's length in seconds, e.g. `{"seconds":14.19}`. A 400 means the server can't decode the file.

3. Upload the batch's other files in time order.

4. End the batch. `t` is the moment the last file ends (Unix ms), that is, its `start` plus its duration:

   ```bash
   curl -X POST -H "Authorization: Bearer $TOKEN" "https://<your-server>/api/chunks/end?device=import&t=<end-time>"
   ```

   It returns `{"ok":true}`. Once the server has processed the remaining audio, the transcripts show up on that day's page, and the batch's last conversation ends: it no longer shows as 「进行中」 (in progress). A conversation with less than 10 seconds of speech is not kept as a conversation; its sentences stay in the transcript (see [Architecture and data flow](../explanation/architecture.md#conversations-summaries-and-the-recap)).

## Content-Type

The Ogg Opus that the app sends uses `audio/ogg`; the server also accepts `audio/webm` and `audio/aac`. It stores other types (e.g. `audio/mpeg` for MP3, `audio/wav` for WAV) as they are, and ffmpeg detects the format itself.

## Reference client

[`firmware/devkit/lr_sync.py`](https://github.com/suyiiyii/alldayrec/blob/main/firmware/devkit/lr_sync.py) is a Python client that reads a recorder's card and uploads it. The endpoint's parameters are in [HTTP API](../reference/api.md#api-chunks).
