---
type: explanation
---

# What alldayrec is

alldayrec is a self-hosted, all-day recorder and personal memory layer. Wear a recorder (or just carry your phone) from morning to night, and your own server turns the audio into speech-to-text transcripts with speaker diarization, conversation summaries, to-dos and a daily recap, within a minute. The original audio is kept, so every line can be played back and checked, and your own agent can search your days over MCP. Think of it as a lifelog you host yourself: an open-source alternative to Plaud, Limitless, Bee or Omi, without a subscription and without your conversations living on someone else's cloud. (Product names belong to their owners; this project is not affiliated with any of them.)

## What it looks like

![Live captions](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-20261024.gif)

Live captions while the page is open (the draft layer). Below, the web UI on a phone, filled with made-up data (the screenshots show the Chinese UI; the web UI can be switched to English on the 「我的」 (Me) page).

| ![Today](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/today-20261024.png) | ![Conversations](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversations-20261024.png) | ![One conversation](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversation-20261024.png) | ![Transcript](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/transcript-20261024.png) |
|:--:|:--:|:--:|:--:|
| Today: due to-dos, yesterday's recap, the conversation going on, today's conversations, and a box to ask about today | Conversations: titles, summaries, tags, due to-dos | One conversation's summary: to-dos, decisions, per-person points | Transcript with speakers: tap a line to play from there; the line playing is highlighted |
| ![Live page](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-20261024.png) | ![Ask AI](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/ask-20261024.png) | ![Daily recap](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/recap-20261024.png) | ![Me](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/me-20261024.png) |
| Live page: the live analysis of the conversation going on, drafts as words come in | Ask AI: an answer with links to the conversations it came from | Daily recap: highlights, your to-dos, the people you talked to | Me: each recorder's battery, free card space and lag, plus links to connect a phone, see progress and hook up AI agents |

The conversations page on a desktop:

![Conversations on a desktop](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversations-desktop-20261024.png)

## Why

- **The original audio stays.** OPPO's 心力球 and Bee delete the audio once it is transcribed, so a wrong transcript has nothing to be checked against. Here every line plays back, and old days can be transcribed again when a better model comes along.
- **Your data lives on your server.** One machine, one SQLite database and the original recordings. Audio goes only to the ASR service you pick and transcripts only to the LLM you pick; there is no account to sign up for.
- **No subscription.** The software is free (MIT). The default Qwen file transcription costs about ¥0.05 per hour of audio, paid to the cloud provider by use, with no monthly minute cap. The LLM costs whatever the service you pick charges.
- **Any device, open hardware.** The iPhone and Android app records the phone's mic; for all-day wear, solder a BLE recorder (`LR-P3`) from a few off-the-shelf modules, with the parts list, wiring diagram and 3D-printed case in this repository.
- **Your agent can search your days.** A remote MCP endpoint for Claude Code, Codex and Cursor, a CLI, and SQLite storage you can read yourself.

## What it does

- **Records all day.** The phone app uploads short Opus pieces; a BLE recorder writes everything to its SD card and streams it to the phone, which fills gaps after a disconnection. Nothing is lost while offline: pieces queue on the device and go up later.
- **Takes any audio source.** Anything that can POST audio can feed the server (`POST /api/chunks`), recordings you already have too: see [importing](../how-to/import-recordings.md).
- **Transcribes in two layers.** The final layer cuts the audio in pauses and sends pieces of up to 5 minutes to a cloud ASR engine (default: Alibaba Cloud Bailian Qwen, about ¥0.05 per hour of audio); while a page is open, a draft layer shows words within seconds. Without an ASR key, `ENGINE=local` transcribes on your machine ([compared with the cloud engines](./asr-benchmark.md)).
- **Knows who spoke.** Speech detection (FireRedVAD) and voiceprints (ReDimNet2) run locally on onnxruntime, no GPU: it learns your own voice, keeps speakers apart within a conversation and remembers the names you give them.
- **Remembers.** Conversations are closed after a silence and summarized by an LLM of your choice; to-dos with due dates, a daily recap, full-text search and "ask AI" over your history, with links to the conversations an answer came from. A conversation with under 60 s of speech gets its first sentence as the title and no summary or to-dos (`MIN_SPEECH_S` in `server/alldayrec/summary.py`).
- **Opens up.** A web UI, a remote MCP endpoint (`POST /mcp`) for Claude Code, Codex, Cursor and other agents, a small CLI (`cli/lr.py`), and SQLite storage you can read yourself.
- **Stays yours.** One server, your disk, several people per server if you like (one token each).

## How it compares

| | **alldayrec** | Anker × Feishu AI recorder (录音豆) | OPPO AI 心力球 | DingTalk A1 | Plaud | Limitless / Bee | Omi |
|---|---|---|---|---|---|---|---|
| **Price / subscription** | Free software; ASR paid by use, Qwen about ¥0.05/h; LLM extra | From ¥899; 6 months of AI credits, then ¥69/month membership[^lrd] | ¥499 trial price, by invitation; OPPO, OnePlus and realme phones only[^xlq][^xlq2] | ¥499 / ¥799[^a1c]; membership ¥599 or ¥1499/year[^a1] | NotePin S $179[^plaud-dev]; free 300 min/month, Pro $17.99/month[^plaud-plan] | Limitless bought by Meta, no longer sold[^limitless]; Bee $74.99 + Pro $11.99/month, US only[^bee] | Pendant $179 ($129 on offer); free 300 cloud min/month, Pro $269/year[^omi-price] |
| **All-day wear** | Yes: the phone app, or a home-built recorder that writes to its card | 8 h per charge, 32 h with the case[^lrd] | 24 h of continuous recording[^xlq] | A card on the back of the phone, 45 h of recording[^a1] | NotePin S about 20 h[^plaud-dev] | Bee wristband, 7 days[^bee] | Pendant |
| **Keeps the original audio** | Yes: every line plays back and can be transcribed again | Deleted from the device after upload, kept in the cloud; download only one by one on the Minutes web page[^lrd] | No: "text only, no original audio"[^xlq] | Unknown | Yes, exports MP3 / WAV[^plaud-exp] | Bee: no, deleted after processing[^bee] | Optional, off by default[^omi-audio] |
| **Where the data lives** | Your own server | Feishu cloud[^lrd] | OPPO's PCC private cloud[^xlq] | 64 GB on the device + DingTalk cloud[^a1] | Plaud cloud[^plaud-plan] | Bee's cloud (Bee Private Compute)[^bee] | Omi cloud, or your own backend[^omi-docs] |
| **Speaker recognition** | Local voiceprints: knows your voice, remembers the names you give | Voiceprints tell speakers apart[^lrd2] | Voiceprint tells the wearer apart[^xlq] | Names after a voiceprint is registered[^a1b] | Speakers, renamable with find & replace[^plaud-rn] | Unknown | Speaker identification[^omi-docs] |
| **Summaries / to-dos / recap** | Summaries, to-dos with due dates, decisions, daily recap | Minutes, to-dos[^lrd2] | Daily summary, to-dos, projects across days[^xlq] | Minutes, chapters, analysis and follow-ups[^a1c] | Template summaries, to-dos[^plaud-app] | Bee summaries (unlimited with Pro)[^bee] | Summaries, action items, daily recaps[^omi-home] |
| **Ask AI / MCP** | Ask AI with sources; remote MCP | Ask across minutes[^lrd2]; MCP unknown | Ask in WeChat or Feishu; MCP[^xlq] | Unknown | Ask Plaud; MCP[^plaud-mcp] | Unknown | Chat; MCP[^omi-mcp] |
| **Open hardware** | Yes (`hardware/lr-p3/`) | No | No | No | No | No | Yes[^omi-docs] |
| **Open source** | Yes (MIT: server, web, app, firmware) | No | No | No | No | No | Yes (MIT)[^omi-docs] |

"No" means no published design files or source code; "Unknown" means we found no reliable source. Prices and features are as of 2026-10-24; check each vendor's own site. Product names and trademarks belong to their owners; this project is not affiliated with any of them.

## Name and history

"LR" / "lr" is the project's internal codename, still used in the hardware and the code: recorder names like `LR-P3`, the firmware tool `lr.sh`, the CLI `lr.py`.

The git history was rewritten before publishing to remove the author's private deployment details, so it is short on purpose, and references like `lr#123` in commit messages point at issues of the earlier private repository, not this one.

The project is released under the MIT license. Bundled third-party code (the Omi devkit, Opus, FireRedVAD's post-processor and others) keeps its own license: see [THIRD_PARTY_NOTICES.md](https://github.com/suyiiyii/alldayrec/blob/main/THIRD_PARTY_NOTICES.md).

[^lrd]: [igao7, Anker AI recorder review (price, AI credits and membership, battery, audio export, cloud storage), Chinese](https://m.igao7.com/news/202601/M0rs0SQkAdD05EZY.html)
[^lrd2]: [52audio, Anker AI recorder (voiceprints, minutes and to-dos, Q&A), Chinese](https://www.52audio.com/archives/267283.html)
[^xlq]: [GeekPark, OPPO AI 心力球 and its 白鲸 AI app (no original audio, PCC private cloud, voiceprint, MCP, supported phones), Chinese](https://www.geekpark.net/news/372428)
[^xlq2]: [Sina Tech, OPPO 心力球 at a ¥499 trial price, Chinese](https://finance.sina.com.cn/tech/digi/2026-09-22/doc-inistexc1962351.shtml)
[^a1]: [QbitAI, DingTalk A1 launch (price, membership, battery, storage), Chinese](https://www.qbitai.com/2025/09/331938.html)
[^a1b]: [DingTalk AI Minutes (names after voiceprint registration)](https://www.dingtalk.io/zh/blog/ai-minutes-redefining-notes)
[^a1c]: [GeekPark, DingTalk A1 hands-on (transcript, minutes, chapters, analysis), Chinese](https://www.geekpark.net/news/353280)
[^plaud-dev]: [Plaud device comparison (NotePin S price, battery)](https://www.plaud.ai/pages/compare-plaud-devices)
[^plaud-plan]: [Plaud AI membership pricing](https://www.plaud.ai/pages/plaud-ai-plan-pricing)
[^plaud-exp]: [Plaud help center, exporting files (MP3 / WAV)](https://support.plaud.ai/hc/en-us/articles/50835453223705-Export-files)
[^plaud-rn]: [Plaud release notes (find and replace speaker names)](https://www.plaud.ai/pages/plaud-release-notes)
[^plaud-app]: [Plaud App 3.0 (Highlights, Summary, To-dos, Ask)](https://www.plaud.ai/blogs/news/plaud-app-3-design-intelligence)
[^plaud-mcp]: [Plaud MCP docs](https://docs.plaud.ai/plaud-mcp-cli/mcp)
[^limitless]: [TechInformed, Meta acquires Limitless, which stops selling its pendant to new customers (2025-12-09)](https://techinformed.com/meta-acquires-limitless-pendant-users-moved-to-free-unlimited-plan/)
[^bee]: [The Next Web, Amazon relaunches Bee (prices, subscription, audio deleted after processing)](https://thenextweb.com/news/amazon-bee-frontier-eclipse-subscription)
[^omi-price]: [Omi pricing](https://www.omi.me/pages/pricing)
[^omi-docs]: [Omi docs (hardware, firmware, app and backend open source, self-hostable, speaker identification)](https://docs.omi.me/doc/get_started/introduction)
[^omi-audio]: [Omi backend, `store_recording_permission` off by default](https://github.com/BasedHardware/omi/blob/main/backend/database/users.py)
[^omi-home]: [Omi's README (summaries and action items)](https://github.com/BasedHardware/omi); [its home page source (Daily Recaps)](https://github.com/BasedHardware/omi/blob/main/app/lib/pages/home/home_content.dart)
[^omi-mcp]: [Omi MCP docs](https://docs.omi.me/doc/developer/MCP)
