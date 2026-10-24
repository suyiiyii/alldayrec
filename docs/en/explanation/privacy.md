---
type: explanation
---

# Privacy: where your data goes

alldayrec starts from one idea: a whole day of audio is very private data and belongs on your own machine. But alldayrec is not fully offline: audio and text go to the services you choose. This page sets out what stays local and what goes to whom.

## What stays on your server

- The original audio: one file per device per hour, kept for good (see [Data layout](../reference/data.md)).
- The database `live.db`: transcripts, conversations, summaries, to-dos, speakers, voiceprints.
- Speech detection and voiceprints: FireRedVAD and ReDimNet2 run on the local CPU, and voice features never leave the server.

There is no account to sign up for; the server only accepts the tokens in `tenants.json`.

## What goes out

| What | Sent to | When |
|---|---|---|
| Audio pieces | The ASR service you pick in `ENGINE` (and `DRAFT_ENGINE`, `ENGINE_FALLBACK`) | Every transcription. A layer set to `local` sends nothing |
| Transcript text | The LLM service that `LLM_BASE_URL` points at | When it writes summaries, name hints, people cards, the daily recap and the live analysis, and when it answers 「问 AI」 (Ask AI). A conversation with under 60 seconds of speech gets no summary |
| The transcripts and summaries your AI agent looks up | The vendor behind your own AI agent | Once you connect it over [MCP](../how-to/connect-mcp.md), each time the agent calls a tool |
| Metrics (`lr_*`, no content) | `VM_INGEST_URL` | Once a minute, only when that variable is set |

Each of these services has its own terms. To keep both the audio and the text on your own machines, use `ENGINE=local` and point `LLM_BASE_URL` at an OpenAI-compatible server you run yourself (vLLM, llama.cpp, Ollama); the model must handle tool calls and JSON output.

Every ASR and LLM call is logged in the `calls` table with only its duration, result and usage, never the content. The MCP request log records only the method, the tool and the number of characters returned, never the arguments or the content.

## Why keep the original audio

Some similar products delete the audio once it is transcribed. alldayrec does the opposite: every line plays back, so a wrong transcript has the original to be checked against, and old days can be transcribed again when a better model comes along. The cost is disk space, and you have to keep these recordings safe yourself.

## Recording other people

All-day recording always records other people. Tell them, ask for consent where it is needed, and follow the law where you live; some places require every party's consent.

When you hand data to an AI agent over MCP, what other people said in the transcripts goes to the agent's vendor too. Words in a recording can also be taken as instructions: in sessions that use this connection, don't auto-approve network and shell tools.
