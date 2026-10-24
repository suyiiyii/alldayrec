---
type: reference
---

# Web pages and app settings

The web pages come in Chinese and English: pick one under **语言 · Language** on the 「我的」 (Me) page. The choice is kept in that browser, and it also switches the language the LLM writes in (summaries, recaps, "ask AI" answers and more) to match; see `lang` in [Configuration](./configuration.md). A browser that hasn't picked one uses the server's language setting. The web pages and the app both follow the system's dark mode.

## Web pages

The server serves the web pages (`web/` builds into `server/static/dist`). The table lists the pages in `web/src/router.ts` on the main branch. v0.1.0 doesn't have 「今天」 (Today), 「我的」 (Me), 「待办」 (To-dos) or `/live` yet, and there `/` is the live captions page. To use these pages, see the `main` image tag in [Deploy with Docker](../how-to/deploy-docker.md#pin-an-image-version).

| Address | Page | Content |
|---|---|---|
| `/` | 今天 (Today) | While a conversation is going on, a small live captions window sits at the top (tap **全屏** (Full screen) to go to `/live`); then due to-dos, yesterday's recap, today's conversations, and 「问问今天」 (Ask about today) |
| `/live` | 实时字幕 (Live captions) | The live transcript and analysis of the conversation going on |
| `/todos` | 待办 (To-dos) | To-dos due today or earlier that aren't marked yet, in three groups: 「今天」 (Today), 「本周」 (This week), 「更早」 (Earlier). Tap the round check to mark one done (or swipe left on a phone); the **⋯** menu can mark it **不对** (Wrong); tap the text to open the original quote. The 「更早」 group has **全部标为已处理** (Mark all as handled); if you marked one by mistake, tap **撤销** (Undo) in the notice |
| `/conversations` | 对话 (Conversations) | The conversation list: titles, summaries, tags, due to-dos |
| `/c/<id>` | 对话 (Conversation) | One conversation's summary and transcript; tap a line to play from there |
| `/calendar` | 日历 (Calendar) | How many conversations each day has |
| `/speakers` | 人物 (People) | Recognized speakers |
| `/p/<name>` | 人物 (Person) | One person's conversations, points and to-dos |
| `/me` | 我的 (Me) | Each recorder's battery, free card space and lag, links to the to-dos and the pages below, and the page language |
| `/progress` | 处理进度 (Progress) | How far along transcription and summaries are, AI calls and the system log |
| `/device/<name>` | 设备日志 (Device log) | One device's events and status history |
| `/connect` | 连接手机 (Connect a phone) | The Android app download, the QR code to sign in by scanning, and the settings for connecting AI (MCP) |
| `/record` | 网页录音 (Web recording) | Records with the browser's microphone or a Bluetooth recorder when you have no app |

### Web recording (`/record`)

- Source: **本机麦克风** (This device's microphone), or **蓝牙录音硬件** (Bluetooth recorder hardware; the browser must support Web Bluetooth).
- The page cuts the microphone audio into a piece every 20 seconds and uploads it as WebM/Opus to `/api/chunks`; tapping **停止** (Stop) calls `/api/chunks/end`.
- The device name defaults to `web-recorder-<8 random characters>`, and you can change it; a Bluetooth recorder's audio is uploaded under the recorder's own name.
- Uncheck **上传** (Upload) to record on the page only, without uploading.

## App

Of the app's four bottom tabs, **今天** (Today), **对话** (Conversations) and **人物** (People) show the server's web pages (`/`, `/conversations`, `/speakers`); **我的** (Me) is the app's own page:

| Entry | Content |
|---|---|
| 录音设备 (Recorders) | Bound Bluetooth recorders: battery, card, offline catch-up, firmware updates, USB drive mode, unbind |
| 实时字幕 (Live captions) | The web page `/live` |
| 设备状态与更多 (Device status and more) | The web page `/me`: battery, card, progress, connecting AI |
| 上传 (Upload) | The phone's upload queue, and how far the server has processed this device's audio |
| 设置 (Settings) | See below |
| 关于 (About) | The app version |

### App settings

| Setting | Description |
|---|---|
| 地址 (Address) | The server address, e.g. `https://your.server` |
| Token | The tenant token |
| 扫码登录 (Scan to sign in) | Scans the QR code on the web page **连接手机** (Connect a phone), then fills in and saves the address and token |
| 设备名 (Device name) | Read-only, taken from the phone name in the system settings; a Bluetooth recorder's audio is uploaded under the recorder's own name |
| 本地保留录音 (Keep recordings locally) | Keeps a copy on the phone after upload: on iOS in the Files app › On My iPhone › alldayrec › recordings › date; on Android in `Android/data/com.suyiiyii.live_record/files/recordings/<date>/` |
| 切片长度（服务端下发） (Slice length, set by the server) | Read-only: seconds of audio per piece in the foreground and in the background (and with the screen off on Android), set by the server's `SLICE_*` variables |
