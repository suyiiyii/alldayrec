---
type: explanation
description: "What stays on your server (audio, database, voiceprints), what goes to the ASR, LLM and AI-agent vendors you pick, and how to keep it all local."
---

# Privacy: where your data goes

AllDayRec starts from one idea: a whole day of audio is very private data and belongs on your own machine. But AllDayRec is not fully offline: audio and text go to the services you choose. This page sets out what stays local and what goes to whom.

## What stays on your server

- The original audio: one file per device per hour, kept for good (see [Data layout](../reference/data.md)).
- The database `live.db`: transcripts, conversations, summaries, to-dos, speakers, voiceprints.
- Speech detection and voiceprints: FireRedVAD and ReDimNet2 run on the local CPU, and voice features never leave the server.
- Per-segment voiceprints (`seg_emb` in `tenants.json`, off by default): when on, the voiceprint taken of each recognized segment at finalization is kept in the `seg_emb` table of `live.db`. These are the voiceprints of everyone speaking near the wearer, passers-by who never agreed to it included, kept only to re-cluster and review speakers on the server later. Off, none is kept; when sentences are finalized again or audio is trimmed, their voiceprints go in the same transaction; deleting this person's `live.db` deletes them all.

There is no account to sign up for. Web/API access uses the tokens in `tenants.json`; MCP also accepts separately created, revocable [AI connection links](../how-to/connect-mcp.md#create-an-ai-connection-link).

## What goes out

| What | Sent to | When |
|---|---|---|
| Audio pieces | The ASR service you pick in `ENGINE` (and `DRAFT_ENGINE`, `ENGINE_FALLBACK`) | Every transcription. A layer set to `local` sends nothing |
| A question said aloud (「问 AI」 (Ask AI), hold to talk) | Alibaba Bailian's streaming speech recognition, only when one of `ENGINE`, `DRAFT_ENGINE`, `ENGINE_FALLBACK` is already `qwen` or `qwen-stream`; otherwise the service of the row above | As it is said; when the streaming recognition fails, the whole of it goes to the service of the row above |
| Transcript text | The LLM service that `LLM_BASE_URL` points at | When it writes summaries, name hints, people cards, the daily recap and the live analysis, and when it answers 「问 AI」 (Ask AI). A conversation with under 60 seconds of speech gets no summary, unless 「立即总结」 (Summarize now) was tapped while it was going on |
| The transcripts and summaries your AI agent looks up | The vendor behind your own AI agent | Once you connect it over [MCP](../how-to/connect-mcp.md), each time the agent calls a tool |
| Metrics (`lr_*`, no content) | `VM_INGEST_URL` | Once a minute, only when that variable is set |

Each of these services has its own terms. To keep both the audio and the text on your own machines, use `ENGINE=local` and point `LLM_BASE_URL` at an OpenAI-compatible server you run yourself (vLLM, llama.cpp, Ollama); the model must handle tool calls and JSON output.

Every ASR and LLM call is logged in the `calls` table with only its duration, result and usage, never the content. The MCP request log records only the method, the tool and the number of characters returned, never the arguments or the content.

An AI connection secret is returned only at creation; the database keeps its hash and a short prefix. Server access logging is disabled, and performance records use `/mcp`. Reverse-proxy access logs contain the full URL: disable or redact those logs. With Cloudflare Tunnel, public TLS terminates at Cloudflare and traffic crosses its network.

## People profiles

The 「AI 档案」 (AI profile) on a person's page is written by the LLM from your conversations with someone: who they are, what they said themselves, what they are busy with, what others say about them, and what is still open. Every line cites where it came from, down to the sentence in that conversation.

- **What is kept**: the `profiles` table of `live.db`, one row a person: each line of the profile, the conversations and times it cites, and the model and usage that made it. With `persona_v2` on, it also keeps a hash (not the content) of each conversation it cites, to tell whether the profile still holds.
- **What goes out**: the evidence goes to the LLM at `LLM_BASE_URL` when a profile is made. By default these are the summaries of the conversations and the person's points in them; with `persona_v2` on for the tenant (see [Configuration](../reference/configuration.md#tenants-json)), it is **the person's own sentences** instead (at most 8 from each of the 40 latest conversations), the sentences of your to-dos to them, and the few sentences around each time someone else said their name.
- **When**: when you tap 「生成」 (Make) on the person's page; after that, again each time a conversation with them gets its summary.
- **Of whom**: with `persona_v2` on, only of people you named; an anonymous S label gets none. A conversation where their label is a few seconds of fragments, or a crowded one where their label took most of the speech, gives no evidence, and neither do words said to an AI, media played aloud, or dictation.
- **What is never written**: the prompt forbids inferring or writing health, sexual orientation, religion, political views, ethnicity, criminal records or financial details; what others say about someone is never written as that person's own view; with under 5 days of evidence, no character traits or habits.
- **Who sees it**: only the tenant itself (the web and API token). After a rename, merge or split of speakers, the old profile is void and no longer shown, and the next one is made from the new labels.
- **How to delete**: one person's: `sqlite3 live.db "DELETE FROM profiles WHERE name = '<name>'"`; all: `DELETE FROM profiles`. A deleted profile is not made again until you tap 「生成」 (Make).

## Environmental sound

Off by default. Once on ([configuration](../reference/configuration.md#optional-environmental-sound): `SOUND_MODEL`, plus the tenant's `"sound": true`), the server scores all of the audio for environmental sound: one frame every 10 s (or every 0.48 s), how much it sounds like each class (a car, music, cooking, a TV, running water…), and its level, kept in the `sounds` table of `live.db`. It scores all of the audio, the stretches where no one speaks too, so it tells where you were and what you were doing more fully than the transcript does.

- It runs on this machine's CPU, or with `INFER_URL` set on your own inference service, which is the only place the audio goes. The scores go to no third party and not into the metrics (those are seconds and counts only).
- When trimming (`trim.py`) drops the audio no one spoke in, these scores stay: the audio is gone, the record of what sounded then is not. `mn10_as`'s per-frame scores and both models' per-minute summaries are kept, `yamnet`'s per-frame scores for 30 days.
- To get rid of them: turn `SOUND_MODEL` or the tenant's `"sound"` off, then delete the rows, for example `sqlite3 live.db "DELETE FROM sounds"` (one device: add `WHERE device = '<device>'`). What you delete can only be scored again from the recordings with the backfill command; what trimming has already dropped cannot. With only the tenant's `"sound"` off and `SOUND_MODEL` still set, that tenant's trims still wait for scores: trim with `--allow-unscened`.

## Why keep the original audio

Some similar products delete the audio once it is transcribed. AllDayRec does the opposite: every line plays back, so a wrong transcript has the original to be checked against, and old days can be transcribed again when a better model comes along. The cost is disk space, and you have to keep these recordings safe yourself.

## Recording other people

All-day recording always records other people. Tell them, ask for consent where it is needed, and follow the law where you live; some places require every party's consent.

When you hand data to an AI agent over MCP, what other people said in the transcripts goes to the agent's vendor too. Words in a recording can also be taken as instructions: in sessions that use this connection, don't auto-approve network and shell tools.
