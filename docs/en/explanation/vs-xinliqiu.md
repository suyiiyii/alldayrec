---
type: explanation
title: "alldayrec: an open-source alternative to OPPO's 心力球 (Xinliqiu) and the Feishu recorder bean"
titleTemplate: false
description: "alldayrec, the open-source 心力球 (开源心力球), compared with OPPO's AI 心力球 (Xinliqiu) and the Feishu recorder bean: price, hardware, audio, data, MCP, self-hosting."
---

# alldayrec vs OPPO's 心力球 (Xinliqiu) and the Feishu recorder bean

All three do the same job: they record what you say during the day and turn it into summaries and to-dos. They differ in hardware, which phones they work with, where the audio and data stay, and whether you can host them yourself.

## What each one is

- **alldayrec (全天录)** is an open-source all-day recording and memory service that runs on your own server. The Android app, the iPhone app and a browser all record directly, and it also connects to a Feishu recorder bean or an open-source recorder. The server turns the audio into transcripts with speakers and a timeline of each day: conversation summaries, the people you talked to, to-dos and a daily recap. The original audio is kept.
- **OPPO AI 心力球 (Xinliqiu)** is OPPO's clip-on AI device, launched on 2026-09-22 as an "AI growth coach"[^xlq-price]. It records all day, gives a daily summary and to-dos in its app, groups conversations on one topic into "projects", and keeps only the text after transcription[^xlq].
- **The Anker × Feishu AI recorder bean (录音豆)** is Feishu's first hardware product, made by Anker's soundcore brand and on sale since 2026-01-19[^lrd]. You press its button to record; recordings sync to Feishu Minutes, which writes minutes and to-dos and answers questions about a recording[^lrd][^lrd2].

## Side by side

<div class="compare">

| | **alldayrec** | OPPO AI 心力球 | Anker × Feishu AI recorder bean |
|---|---|---|---|
| **Price / subscription** | Free software; ASR paid by use to the service you pick, default Qwen about ¥0.05 per hour of audio; LLM extra | ¥499 early-adopter price, bought with an invite code by people picked for the early-adopter program, with 60 days of the Ultra plan[^xlq-buy]; formal software pricing expected at the end of November 2026[^xlq-plan] | ¥899 with 6 months of device AI credits; after that 300 free transcription minutes a month, or the AI Premium membership at ¥69/month with 3000 minutes a month[^lrd] |
| **Hardware** | None to buy: the phone or a browser records; also connects a Feishu recorder bean (Android), or build the open-source recorder `LR-P3` | 9.8 g collar clip, 24 h of continuous recording, 5 m pickup[^xlq] | 10 g button, worn on a magnet or a lanyard; 8 h per charge, 32 h with the charging case[^lrd] |
| **Phones and platforms** | Android app, iPhone app (built from source with Xcode), browser | The current version needs an OPPO, OnePlus or realme phone[^xlq] | Bound in the Feishu app; recordings sync to Feishu Minutes[^lrd2] |
| **Original audio** | Kept: every line plays back and can be transcribed again | Text only, no original audio[^xlq] | Deleted from the device after it reaches the phone; the audio downloads only from the Minutes web page[^lrd] |
| **Where the data lives** | Your own server | OPPO's AI PCC private cloud[^xlq] | Feishu cloud[^lrd] |
| **Speakers** | Local voiceprints: knows your voice, remembers the names you give others | Voiceprint tells the wearer apart[^xlq] | Voiceprints tell speakers apart[^lrd2] |
| **Summaries / to-dos** | Conversation summaries, to-dos with due dates, decisions, daily recap, full-text search | "Today" summary and to-dos; "Projects" link conversations into one timeline; "Awareness" gives insights into your habits[^xlq] | Smart minutes, to-dos, key decisions; transcription and translation in several languages[^lrd2] |
| **Ask AI / MCP** | Ask AI with sources; remote MCP endpoint (Claude Code, Codex, Cursor) | Ask in WeChat or Feishu; MCP gives Claude, Cursor, Codex and others its memory[^xlq] | Ask AI about a recording[^lrd2]; MCP unknown |
| **Self-hosting** | Yes: Docker, uvx / pip, or from source | No | No |
| **Open source** | Yes (MIT: server, web, app, firmware) | No | No |

</div>

As of 2026-10-26. "No" means no published source code or way to host it yourself; "Unknown" means we found no reliable source.

## Which one to pick

- **Pick OPPO's 心力球** if you use an OPPO, OnePlus or realme phone, want a light clip you can forget about, only need the text, want coach-style reminders and insights, and don't want to run a server.
- **Pick the Feishu recorder bean** if your team already works in Feishu, you mostly record meetings, and you need minutes, sharing and collaboration.
- **Pick alldayrec** if you want to keep the original audio and your data on your own server, you use an iPhone or any Android phone, you want your AI agent to search your days over MCP, or you want to change the code and the hardware.
- **Already have a recorder bean?** The Android app connects to it from 1.5.5 on, and the recordings go to your own server. The bean connects to one phone at a time: see [How to bind a recorder](../how-to/bind-recorder.md#feishu-recorder-bean).

## Try alldayrec

- <a href="../../demo/" target="_self">Try the demo</a>: made-up data; the timeline, conversations and the Live page open right away.
- [Your first transcript](../tutorial/first-run.md): run the server with Docker and see the first transcript on the web page.
- [How to install and connect the phone app](../how-to/install-app.md): the Android APK, or an iOS build with Xcode.
- For DingTalk A1, Plaud, Bee and Omi, see [What alldayrec is](./intro.md#how-it-compares).

[^xlq]: [GeekPark, OPPO AI 心力球 hands-on (2026-10-16; weight, battery, pickup, voiceprint, Today / Projects / Awareness, text only, PCC private cloud, WeChat and Feishu Q&A, MCP, supported phones), Chinese](https://www.geekpark.net/news/372428)
[^xlq-price]: [Sina Tech via IT之家, OPPO launches the AI 心力球 at a ¥499 trial price as an "AI growth coach" (2026-09-22), Chinese](https://finance.sina.com.cn/tech/digi/2026-09-22/doc-inistexc1962351.shtml)
[^xlq-buy]: [Sohu, OPPO AI 心力球 on sale with an invite code (2026-10-16; early-adopter program, ¥499, 60 days of the Ultra plan), Chinese](https://www.sohu.com/a/1088638076_115831)
[^xlq-plan]: [@QiBoden on X, a screenshot of the 心力球 plan notice: formal software pricing "expected at the end of November 2026" (2026-10-20), Chinese](https://x.com/QiBoden/status/2112436871234256981)
[^lrd]: [igao7, Anker × Feishu AI recorder bean review (launch date, price, credits and membership, weight, battery, recordings deleted from the device after transfer, audio export), Chinese](https://m.igao7.com/news/202601/M0rs0SQkAdD05EZY.html)
[^lrd2]: [52audio, Anker AI recorder bean (pairing in the Feishu app, voiceprints, minutes and to-dos, Q&A, transcription and translation), Chinese](https://www.52audio.com/archives/267283.html)
