---
type: how-to
---

# How to troubleshoot common problems

Start with the log: with Docker, run `docker compose logs -f`. The web page **处理进度** (Progress, `/progress`) also shows how far transcription and summaries have got, and the system log.

## The server exits right after it starts

- The log says `not set: ...`: these variables are missing from `.env`. See [Configuration](../reference/configuration.md).
- The log says `ENGINE, DRAFT_ENGINE, ENGINE_FALLBACK must be one of ...`: one of these three has a wrong value, and the log lists the allowed values. If your value is in [Change the ASR engine](./change-asr-engine.md) but not in the log, the image on your machine is older than the docs: run `docker compose pull` first.
- The log says `no tenants file`: there is no `tenants.json`. With Docker it is `data/tenants.json`.
- The log says `others can read it, and it holds every token: chmod 600`: run `chmod 600 data/tenants.json`.
- The log says a model file failed to download, `is missing` or `has another sha256`: delete that file and restart, or download it from the same address the log gives and put it in `MODELS_DIR` (`data/models` with Docker). A ReDimNet2 file you export yourself with `export_onnx.py` differs byte for byte from the released one, and the server refuses it.

## The web page shows only `token required`

This browser isn't signed in yet. Open `http(s)://<your-server>/?token=<your token>` once; after that a cookie keeps you signed in.

## Summaries fail, or "ask AI" reports "模型调用失败" (model call failed)

- When a Bailian key is restricted to specific models in the console, calls to the chat models return 403. Use a key without a model restriction.
- Another LLM service must support tool calls and JSON output (`response_format` of `json_object`).

## Some conversations have no summary

A conversation with under 60 seconds of speech doesn't go to the LLM: its title is its first sentence, and it has no summary or to-dos (`MIN_SPEECH_S` in `server/summary.py`).

## The first transcript is slow with `ENGINE=local`

When the first audio arrives, the server first downloads a model tarball of about 136 MB, so the first transcript takes about a minute. It doesn't download it again after that.

## The last part of imported recordings is never transcribed

After you upload a batch of files, call `POST /api/chunks/end` once. See [Import recordings you already have](./import-recordings.md).

## The phone can't connect after scanning the QR code

- The server address in the QR code is the address in the computer browser's address bar. When you open the web page at `127.0.0.1`, the phone gets `127.0.0.1` too. Open the web page again at an address the phone can reach, then scan again.
- With an `http://` address, the released APK (1.5.0) can't connect. See [Reach the server from a phone](./remote-access.md).

## The app on the iPhone no longer opens

An app installed with a free Apple ID stops working after 7 days. Install it again from Xcode. A paid developer account doesn't have this limit.

## Importing a file returns 413 behind an nginx reverse proxy

By default nginx accepts request bodies of up to 1 MB only. Raise `client_max_body_size`.

## Can older Safari play the recordings?

Yes. The server decodes the audio for playback and live listening on the web page to WAV before the browser gets it (`/api/audio`, `/api/listen`), because Safari before 18.4 can't decode the recordings' Ogg Opus format.

## The card's OMI drive never appears on macOS

The app's **录音设备 › U 盘模式 › 存储卡当 U 盘** (Recorder › USB drive mode › Card as USB drive) gives the recorder's card to the computer as a USB drive. Normally the OMI drive appears after about 20 seconds.

What the maintainer measured on macOS 26: the system allows about 20 seconds to mount a FAT32 volume, so a large FAT32 card that reads slowly (such as 32 GB) may never mount; the same card formatted as exFAT mounts in about 2 seconds. The firmware reads both FAT32 and exFAT. Also, macOS mounts no disks while the Mac is locked.
