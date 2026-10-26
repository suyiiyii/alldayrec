---
type: explanation
description: "What alldayrec is: an open-source Plaud for your whole day, a private second brain that works on every platform. Screenshots, features, how it compares."
---

# What alldayrec is

alldayrec is an all-day recording and memory service that runs on your own server. Record from morning to night with your phone, an open-source recorder or a Feishu recorder bean, and within a minute your server turns the audio into transcripts with speakers, then into a timeline of your day: conversation summaries, the people you talked to, to-dos and a daily recap. The original audio is kept, so every line can be played back.

## What it looks like

![Live captions and the live summary](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/live-captions-20261026b.gif)

The Live page: captions come up as people speak, and the AI live summary keeps up. Below, the web UI on a phone, filled with made-up data.

| ![Timeline](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/timeline-20261026b.png) | ![One conversation](https://raw.githubusercontent.com/suyiiyii/alldayrec/assets/img/conversation-20261026b.png) |
|:--:|:--:|
| Timeline: the conversation going on, due to-dos, each day's conversations | One conversation: the summary, to-dos and decisions, each with its audio one tap away |

## Why

- **Every platform, no hardware to buy.** The Android app, the iPhone app and a browser all record directly. For all-day wear, connect a Feishu recorder bean (Android) or build an open-source recorder (`LR-P3`).
- **The original audio stays.** Every line plays back, and old days can be transcribed again when a better model comes along.
- **Your data lives on your server.** Audio goes only to the ASR service you pick and transcripts only to the LLM you pick.
- **Free software, pay by use.** You pay the services you pick for ASR and the LLM by use; the default Qwen transcription costs about ¥0.05 per hour of audio.
- **Your agent can search your days.** Over a remote MCP endpoint (Claude Code, Codex, Cursor).

## What it does

- **Every platform, no hardware to buy.** The Android app, the iPhone app and a browser all record directly. The Android app also connects to a [Feishu recorder bean](../how-to/bind-recorder.md#feishu-recorder-bean).
- **Transcribes in two layers.** The final transcript comes from a cloud ASR engine (default: Alibaba Cloud Bailian Qwen, about ¥0.05 per hour of audio), and drafts show within seconds while a page is open; `ENGINE=local` transcribes on your machine instead.
- **Knows who spoke.** It learns your own voice and remembers the names you give others.
- **Remembers.** Conversation summaries, to-dos with due dates, a daily recap, and full-text search.
- **Opens up.** A web UI, a remote MCP endpoint (Claude Code, Codex, Cursor), a CLI.
- **Imports recordings.** Audio files you already have can be [imported](../how-to/import-recordings.md) from the Me page.

## How it compares

<div class="compare">

| | **alldayrec** | Anker × Feishu AI recorder (录音豆) | OPPO AI 心力球 | DingTalk A1 | Plaud | Bee (Amazon) | Omi |
|---|---|---|---|---|---|---|---|
| **Price / subscription** | Free software; ASR paid by use, Qwen about ¥0.05/h; LLM extra | From ¥899; 6 months of AI credits, then ¥69/month membership[^lrd]; the membership transcribes about 50 h a month[^lrd3] | ¥499 trial price, invitation only, bought with an invite code[^xlq2][^xlq3]; OPPO, OnePlus and realme phones only[^xlq]; software pricing due at the end of November[^xlq4] | ¥499 / ¥799[^a1c]; membership ¥599 or ¥1499/year[^a1] | NotePin S $179[^plaud-dev]; free 300 min/month, Pro $17.99/month[^plaud-plan] | Relaunched 2026-10-15: Frontier $74.99, on sale in the US; Eclipse $99.99, no release date; Pro $11.99/month[^bee] | Pendant $179 ($129 on offer); free 300 cloud min/month, Pro $269/year[^omi-price] |
| **All-day wear** | Yes: the phone app, a Feishu recorder bean (Android), or a home-built recorder that writes to its card | 8 h per charge, 32 h with the case[^lrd] | 24 h of continuous recording[^xlq] | A card on the back of the phone, 45 h of recording[^a1] | NotePin S about 20 h[^plaud-dev] | Frontier on the wrist or clipped on, 7 days[^bee-x] | Pendant |
| **Keeps the original audio** | Yes: every line plays back and can be transcribed again | Deleted from the device after upload, kept in the cloud; download only one by one on the Minutes web page[^lrd] | No: "text only, no original audio"[^xlq] | Unknown | Yes, exports MP3 / WAV[^plaud-exp] | No, deleted after processing[^bee] | Optional, off by default[^omi-audio] |
| **Where the data lives** | Your own server | Feishu cloud[^lrd] | OPPO's PCC private cloud[^xlq] | 64 GB on the device + DingTalk cloud[^a1] | Plaud cloud[^plaud-plan] | Bee's cloud (Bee Private Compute)[^bee] | Omi cloud, or your own backend[^omi-docs] |
| **Speaker recognition** | Local voiceprints: knows your voice, remembers the names you give | Voiceprints tell speakers apart[^lrd2] | Voiceprint tells the wearer apart[^xlq] | Names after a voiceprint is registered[^a1b] | Speakers, renamable with find & replace[^plaud-rn] | Knows the wearer; names other people only after they do a voice training with the wearer[^bee-gw] | Speaker identification[^omi-docs] |
| **Summaries / to-dos / recap** | Summaries, to-dos with due dates, decisions, daily recap | Minutes, to-dos[^lrd2] | Daily summary, to-dos, projects across days[^xlq] | Minutes, chapters, analysis and follow-ups[^a1c] | Template summaries, to-dos[^plaud-app] | Summaries (unlimited with Pro)[^bee] | Summaries, action items, daily recaps[^omi-home] |
| **Ask AI / MCP** | Ask AI with sources; remote MCP | Ask AI about a recording[^lrd2]; MCP unknown | Ask in WeChat or Feishu; MCP[^xlq] | Unknown | Ask Plaud; MCP[^plaud-mcp] | Unknown | Chat; MCP[^omi-mcp] |
| **Open hardware** | Yes (`hardware/lr-p3/`) | No | No | No | No | No | Yes[^omi-docs] |
| **Open source** | Yes (MIT: server, web, app, firmware) | No | No | No | No | No | Yes (MIT)[^omi-docs] |

</div>

As of 2026-10-25. "No" means no published design files or source code; "Unknown" means we found no reliable source.

For a closer look at OPPO's 心力球 and the Feishu recorder bean, and which to pick, see [alldayrec vs 心力球 and the Feishu bean](./vs-xinliqiu.md).

Limitless was bought by Meta on 2025-12-05 and is no longer sold[^limitless].

## Name and license

"LR" / "lr" is the project's internal codename, still used in the hardware and the code: recorder names like `LR-P3`, the firmware tool `lr.sh`, the CLI `lr.py`.

The project is released under the MIT license. Bundled third-party code (the Omi devkit, Opus, FireRedVAD's post-processor and others) keeps its own license: see [THIRD_PARTY_NOTICES.md](https://github.com/suyiiyii/alldayrec/blob/main/THIRD_PARTY_NOTICES.md).

[^lrd]: [igao7, Anker AI recorder review (price, AI credits and membership, battery, audio export, cloud storage), Chinese](https://m.igao7.com/news/202601/M0rs0SQkAdD05EZY.html)
[^lrd2]: [52audio, Anker AI recorder (voiceprints, minutes and to-dos, Q&A), Chinese](https://www.52audio.com/archives/267283.html)
[^lrd3]: [@zengge1984 on X: the Feishu AI membership gives "only 50 hours of transcription a month" (2026-10-19), Chinese](https://x.com/zengge1984/status/2112130096152658009)
[^xlq]: [GeekPark, OPPO AI 心力球 and its 白鲸 AI app (no original audio, PCC private cloud, voiceprint, MCP, supported phones), Chinese](https://www.geekpark.net/news/372428)
[^xlq2]: [Sina Tech, OPPO 心力球 at a ¥499 trial price, Chinese](https://finance.sina.com.cn/tech/digi/2026-09-22/doc-inistexc1962351.shtml)
[^xlq3]: [@chenreason on X: buying a 心力球 "needs an invite code" (2026-10-20), Chinese](https://x.com/chenreason/status/2112516784146575480)
[^xlq4]: [@QiBoden on X, a screenshot of the 心力球 plan notice: formal software pricing "expected at the end of November 2026" (2026-10-20), Chinese](https://x.com/QiBoden/status/2112436871234256981)
[^a1]: [QbitAI, DingTalk A1 launch (price, membership, battery, storage), Chinese](https://www.qbitai.com/2025/09/331938.html)
[^a1b]: [DingTalk AI Minutes (names after voiceprint registration)](https://www.dingtalk.io/zh/blog/ai-minutes-redefining-notes)
[^a1c]: [GeekPark, DingTalk A1 hands-on (transcript, minutes, chapters, analysis), Chinese](https://www.geekpark.net/news/353280)
[^plaud-dev]: [Plaud device comparison (NotePin S price, battery)](https://www.plaud.ai/pages/compare-plaud-devices)
[^plaud-plan]: [Plaud AI membership pricing](https://www.plaud.ai/pages/plaud-ai-plan-pricing)
[^plaud-exp]: [Plaud help center, exporting files (MP3 / WAV)](https://support.plaud.ai/hc/en-us/articles/50835453223705-Export-files)
[^plaud-rn]: [Plaud release notes (find and replace speaker names)](https://www.plaud.ai/pages/plaud-release-notes)
[^plaud-app]: [Plaud App 3.0 (Highlights, Summary, To-dos, Ask)](https://www.plaud.ai/blogs/news/plaud-app-3-design-intelligence)
[^plaud-mcp]: [Plaud MCP docs](https://docs.plaud.ai/plaud-mcp-cli/mcp)
[^limitless]: [TechInformed, Meta acquires Limitless, which stops selling its pendant to new customers (2025-12-09)](https://techinformed.com/meta-acquires-limitless-pendant-users-moved-to-free-unlimited-plan/); [the acquisition news on X (2025-12-05)](https://x.com/LuxAlgo/status/1997019782499987471)
[^bee]: [The Next Web, Amazon relaunches Bee (2026-10-15; prices, subscription, on sale in the US, audio deleted after processing)](https://thenextweb.com/news/amazon-bee-frontier-eclipse-subscription)
[^bee-gw]: [GeekWire, Amazon's Bee devices: they switch off when they don't hear the wearer, and other people are named only after a voice training with the wearer (2026-10-16)](https://www.geekwire.com/2026/amazons-new-bee-ai-devices-have-a-bystander-problem-says-former-aws-security-engineer/)
[^bee-x]: [Bee on X: Frontier is worn on the wrist or clipped on, seven-day battery (2026-10-15)](https://x.com/bee__computer/status/2110709597568897052)
[^omi-price]: [Omi pricing](https://www.omi.me/pages/pricing)
[^omi-docs]: [Omi docs (hardware, firmware, app and backend open source, self-hostable, speaker identification)](https://docs.omi.me/doc/get_started/introduction)
[^omi-audio]: [Omi backend, `store_recording_permission` off by default](https://github.com/BasedHardware/omi/blob/main/backend/database/users.py)
[^omi-home]: [Omi's README (summaries and action items)](https://github.com/BasedHardware/omi); [its home page source (Daily Recaps)](https://github.com/BasedHardware/omi/blob/main/app/lib/pages/home/home_content.dart)
[^omi-mcp]: [Omi MCP docs](https://docs.omi.me/doc/developer/MCP)
