---
type: how-to
description: "Change the ASR engine for final transcripts with ENGINE in .env (default qwen), or use ENGINE=local with no ASR key. Also draft and fallback engines."
---

# How to change the ASR engine

`ENGINE` in `.env` decides which ASR engine makes the final transcript; the default is `qwen` (Alibaba Cloud Bailian). For how the engines differ, see [Architecture and data flow](../explanation/architecture.md#two-transcription-layers) and [ASR benchmark](../explanation/asr-benchmark.md).

## Switch engines

1. In `.env`, set `ENGINE` to one of the values in the table, and fill in the key it needs:

   | `ENGINE` | Needs |
   |---|---|
   | `qwen` | `DASHSCOPE_API_KEY` |
   | `volc` | `VOLC_ASR_KEY` |
   | `stepfun-sse` | `STEPFUN_API_KEY` |
   | `stepfun` | `STEPFUN_API_KEY`, plus your own upload bridge and file-mode ASR service: `ASR_UPLOAD_BRIDGE_URL`, `ASR_UPLOAD_BRIDGE_TOKEN`, `ASR_SERVICE_URL`, `ASR_SERVICE_BASIC_AUTH` |
   | `stepfun-file` | Your own upload bridge and file-mode ASR service: `ASR_UPLOAD_BRIDGE_URL`, `ASR_UPLOAD_BRIDGE_TOKEN`, `ASR_SERVICE_URL`, `ASR_SERVICE_BASIC_AUTH` |
   | `local` | No key |

2. Restart the server. With Docker, run `docker compose up -d`: Compose recreates the container with the new `.env`.

When a key is missing, the server doesn't start, and the log says `not set: <missing variable> (see .env.example)`. When `ENGINE` is misspelled, the log lists the valid values.

Whichever engine you use, you need the three LLM settings (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`).

## No ASR key at all: `ENGINE=local`

`local` runs the X-ASR zh-en model with sherpa-onnx in the server process. It outputs text only, and speakers come from the server's own voiceprints. On far-field audio, it does worse than `qwen`.

It needs the `local` extra: the Docker image has it already; with uvx or pip, install `alldayrec[local]` (see [How to run with uvx or pip](./run-uvx.md)); from source, run `uv sync --extra local`.

When the first audio arrives, the server downloads a model tarball of about 136 MB, so the first transcript takes about a minute. If the download is slow, set `LOCAL_ASR_URL` to the same tarball's address on a mirror; the server checks the file against its sha256.

## Drafts and fallback

- `DRAFT_ENGINE`: the engine that makes drafts while a page is open; empty means the same as `ENGINE`. With `qwen-stream`, the server streams the audio to Qwen while a page is open, so words show as they are said (needs `DASHSCOPE_API_KEY`). `qwen-stream` works only as `DRAFT_ENGINE`.
- `ENGINE_FALLBACK`: the engine to use when the main engine fails on a window (402, 451, 5xx, a timeout); empty means retry later.

All the related settings are in [Configuration](../reference/configuration.md).
