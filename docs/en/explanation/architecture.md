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

## Conversations, summaries and the recap

Sentences are grouped into conversations by time. A conversation ends after 5 minutes with nobody speaking, when the recorder stops, or when it reaches 4 hours. A conversation with less than 10 seconds of speech is not kept as a conversation (its sentences stay in the transcript).

Time alone lets voices around, in a mall or on the street, chain unrelated conversations into one. So before a longer conversation (`SPLIT_MIN_S`, 10 minutes by default) is summarized, the LLM also reads it for content: where other people and another matter take over, at a real pause, it is split into parts, each with its own summary. Late audio never merges the parts back.

When a conversation ends, the server writes a summary with the LLM you configured: a title, the summary, each person's points, to-dos (with due dates), decisions and tags. A conversation with under 60 seconds of speech doesn't go to the LLM; its first sentence becomes the title. Every day at `RECAP_AT` (default 10:00), the server writes the previous day's recap.

「问 AI」 (Ask AI) answers questions about your past with the same LLM: the summaries of the last few days go into the prompt, and older or more detailed content is found with tools that query the database. Every claim carries the id of the conversation it came from.

## Who reads this data

The web UI, the web pages inside the app, the [MCP endpoint](../reference/mcp.md) and the [CLI](../reference/cli.md) all read the same tenant's database. The database is SQLite, so you can also read it directly yourself; the tables are in [Data layout](../reference/data.md).

One server can serve several people, each with a token, a database and a recordings directory (`tenants.json`), isolated from each other in the same process.
