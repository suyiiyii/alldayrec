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
| Transcript text | The LLM service that `LLM_BASE_URL` points at | When it writes summaries, name hints, people cards, the daily recap, the live analysis and place guesses (tenant `place` on), and when it answers 「问 AI」 (Ask AI). A conversation with under 60 seconds of speech gets no summary, unless 「立即总结」 (Summarize now) was tapped while it was going on |
| The transcripts and summaries your AI agent looks up | The vendor behind your own AI agent | Once you connect it over [MCP](../how-to/connect-mcp.md), each time the agent calls a tool |
| Metrics (`lr_*`, no content) | `VM_INGEST_URL` | Once a minute, only when that variable is set |

Each of these services has its own terms. To keep both the audio and the text on your own machines, use `ENGINE=local` and point `LLM_BASE_URL` at an OpenAI-compatible server you run yourself (vLLM, llama.cpp, Ollama); the model must handle tool calls and JSON output.

Every ASR and LLM call is logged in the `calls` table with only its duration, result and usage, never the content. The MCP request log records only the method, the tool and the number of characters returned, never the arguments or the content.

An AI connection secret is returned only at creation; the database keeps its hash and a short prefix. Server access logging is disabled, and performance records use `/mcp`. Reverse-proxy access logs contain the full URL: disable or redact those logs. With Cloudflare Tunnel, public TLS terminates at Cloudflare and traffic crosses its network.

## People profiles

The 「AI 档案」 (AI profile) on a person's page is written by the LLM from your conversations with someone: who they are, what is known about them, what they are busy with, what others say about them, and what is still open. Every line cites where it came from. The tenant's `persona_v2` (see [Configuration](../reference/configuration.md#tenants-json)) picks the evidence; the two cases are below.

**By default (`persona_v2` off)**

- **What goes out**: when a profile is made, to the LLM at `LLM_BASE_URL`: the 40 latest conversations with the person, each with its title, summary (first 300 characters) and their points in it; their to-dos and yours to them (at most 30, with text and due date); and the sentences where someone else said their name or an alias (at most 30), each with the two sentences either side as transcribed, with speaker labels.
- **What is kept**: the `profiles` table of `live.db`, one row a person: each line of the profile, the conversations it cites (for to-dos and mentions also the sentence's time), and the model and usage that made it. A cited conversation opens as the whole conversation; a to-do or mention, at that sentence.
- **Rules**: the prompt asks for evidence numbers on each line, and a line citing none that was given is dropped. Nothing else: the prompt asks for facts such as family and habits, does not tell what they said from what others said, forbids no sensitive attributes and needs no number of days.
- **After a rename**: the old profile stays shown; the next one made uses the new labels.

**With `persona_v2` on**

- **What goes out**: **the person's own sentences** instead: at most 8 from each of the 40 latest conversations that give evidence (skipped ones do not count, so this can reach far back); the sentences of your to-dos to them; and the sentences where someone else said their name (at most 30), with the two either side. No summaries or points.
- **Of whom**: only of people you named; an anonymous S label gets none. A conversation whose summary is not done, or was done without a model, gives none; words said to an AI, media played aloud and dictation are no evidence, and a sentence of theirs under 1 second does not count. A conversation gives none of their sentences when: their speech in it is under 15 seconds (a fragment); it has 10 or more speakers and their label holds 70 % or more of the speech (a crowd's voices taken in; labels only in media, dictation or words to an AI not counted); or they have 5 or more sentences and 45 % or more of them are under 1 second (grown by short ones joined to them). 「我」 (me) is exempt from the last two. The sentences around a mention are only of its own conversation and pass the same gates: none under 1 second, and none of theirs from a conversation skipped for them.
- **Who said it**: decided by the code from what a line cites, not by the model. A line is theirs only when every sentence it cites is their own; citing anyone else's (a mention, or yours) at all makes it what others say. The same holds for who they are: one resting on others' words moves to what others say.
- **What is never written**: the prompt forbids inferring or writing health, sexual orientation, religion, political views, ethnicity, criminal records or financial details, even when they said it themselves; this rests on the prompt alone, the code does not filter it. With under 5 days of evidence, no character traits or habits: the prompt asks so, and the code drops lines the model marks as a trait; one it does not mark gets through.
- **What is kept**: as above, plus a hash (not the content) of each conversation it cites, to tell whether the profile still holds. A citation opens at the sentence.
- **After a rename**: after a rename, merge or split of speakers or conversations, once a conversation it used changed, the old profile is void and no longer shown, and the next one is made from the new labels. With the flag turned off again, stored profiles are shown as they are.

**Either way**

- **When**: when you tap 「生成」 (Make) on the person's page; after that, again each time a conversation with them gets its summary (not for 「我」). With `persona_v2` on, such a refresh makes no call when the evidence is exactly as last time.
- **Who sees it**: only the tenant itself (the web and API token).
- **How to delete**: one person's: `sqlite3 live.db "DELETE FROM profiles WHERE name = '<name>'"`; all: `DELETE FROM profiles`. A deleted profile is not made again until you tap 「生成」 (Make).

## Environmental sound

Off by default. Once on ([configuration](../reference/configuration.md#optional-environmental-sound): `SOUND_MODEL`, plus the tenant's `"sound": true`), the server scores all of the audio for environmental sound: one frame every 10 s (or every 0.48 s), how much it sounds like each class (a car, music, cooking, a TV, running water…), and its level, kept in the `sounds` table of `live.db`. It scores all of the audio, the stretches where no one speaks too, so it tells where you were and what you were doing more fully than the transcript does.

- It runs on this machine's CPU, or with `INFER_URL` set on your own inference service, which is the only place the audio goes. The scores go to no third party and not into the metrics (those are seconds and counts only).
- When trimming (`trim.py`) drops the audio no one spoke in, these scores stay: the audio is gone, the record of what sounded then is not. `mn10_as`'s per-frame scores and both models' per-minute summaries are kept, `yamnet`'s per-frame scores for 30 days.
- Sound events (the sound tags on conversations, the timeline's sound bar, the sound filter, the ambient sound in MCP) are made from these scores each time they are asked for, and not stored apart. Only the tenant sees them: the pages and MCP need its token or one of its AI connection links, and an agent connected over MCP reads them (and sends them on to the model provider it uses). They say what was heard (speech, music, in a vehicle, quiet), never where, and they can be wrong, so the pages word each one 「可能」 (maybe).
- A 「不对」 (wrong) tapped on a tag or on the sound bar goes into the `sound_wrong` table of `live.db`: which stretch of which device, which event, the model it came from and its conversation id, no audio and no words. Only the tenant sees it (exported by `GET /api/sounds/wrong`), to improve the thresholds later. 「撤销」 (undo) deletes that row; to delete them all, `sqlite3 live.db "DELETE FROM sound_wrong"`.
- To get rid of them: turn `SOUND_MODEL` or the tenant's `"sound"` off, then delete the rows, for example `sqlite3 live.db "DELETE FROM sounds"` (one device: add `WHERE device = '<device>'`). What you delete can only be scored again from the recordings with the backfill command; what trimming has already dropped cannot. With only the tenant's `"sound"` off and `SOUND_MODEL` still set, that tenant's trims still wait for scores: trim with `--allow-unscened`.

## Where you probably were

Off by default. With the tenant's `"place": true` (see [Configuration](../reference/configuration.md#tenants-json)), after each conversation's summary is written the server makes one more LLM call that guesses where you probably were while recording it (home, lab or office, in a car, metro or train, restaurant, outdoors, shop, elsewhere, plus the more exact place a sentence named), and states its evidence: which sentences, which environmental sound, for how many minutes. This is semi-sensitive: one guess is only a guess, many of them are where you go.

- **What goes out**: the conversation's transcript (with media played aloud and speech to an AI marked), the minutes of each environmental sound and the time, to the LLM service at `LLM_BASE_URL`: the same transcript the summary sends. A conversation with under 60 s of speech, whose summary made no LLM call, gets no guess.
- **Where it is kept**: only in the tenant's `live.db`, table `summaries`, as the summary JSON's `place` field; the evidence holds the sentences' times and words (already in the transcript). Not in the metrics; the `calls` table keeps the call's time and token counts only.
- **Who sees it**: the top of the conversation's summary; the conversation APIs that return summaries (`GET /api/conversations`, `GET /api/conversation/{id}` and the like) carry it too, to clients with the tenant's token only. It is not in people profiles, daily recaps, Ask AI or the MCP tools' output. When the model is unsure, or its evidence does not match the transcript, no place is kept and the page shows nothing.
- **Turning it off**: remove the tenant's `"place"` (or set it to `false`) and restart: new summaries get no place; existing ones keep theirs. Regenerating a summary drops its old place.
- **Deleting it**: with `"place"` off, remove the field from every summary, for example `sqlite3 live.db "UPDATE summaries SET body = json_remove(body, '$.place') WHERE json_extract(body, '$.place') IS NOT NULL"`.

## Why keep the original audio

Some similar products delete the audio once it is transcribed. AllDayRec does the opposite: every line plays back, so a wrong transcript has the original to be checked against, and old days can be transcribed again when a better model comes along. The cost is disk space, and you have to keep these recordings safe yourself.

## Recording other people

All-day recording always records other people. Tell them, ask for consent where it is needed, and follow the law where you live; some places require every party's consent.

When you hand data to an AI agent over MCP, what other people said in the transcripts goes to the agent's vendor too. Words in a recording can also be taken as instructions: in sessions that use this connection, don't auto-approve network and shell tools.
