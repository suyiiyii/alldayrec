---
type: reference
---

# Web pages and app settings

The web pages come in Chinese and English: pick one under **语言 · Language** on the 「我的」 (Me) page. The choice is kept in that browser, and it also switches the language the LLM writes in (summaries, recaps, "ask AI" answers and more) to match; see `lang` in [Configuration](./configuration.md). A browser that hasn't picked one uses the server's language setting. The web pages and the app both follow the system's dark mode.

## Web pages

The server serves the web pages (`web/` builds into `server/alldayrec/static/dist`). The table lists the pages in `web/src/router.ts` on the main branch; v0.2.0 and later have all of them. v0.1.0 doesn't have 「我的」 (Me), 「待办」 (To-dos) or `/live` yet, and there `/` is the live captions page. Four tabs sit at the top of the pages (at the bottom on a phone): **时间线** (Timeline), **实时** (Live), **人物** (People) and **我的** (Me).

| Address | Page | Content |
|---|---|---|
| `/` (also `/conversations`) | 时间线 (Timeline) | Every conversation by day, newest first. On today with no filter, the conversation going on (tap **全屏** (Full screen) to go to `/live`) and the due to-dos sit at the top; each past day opens with its recap, and short conversations without a summary fold into one line. At the top: a day picker, **日历** (Calendar), the search box, **问 AI** (Ask AI; a round button at the bottom right on a phone) and tags |
| `/live` | 实时 (Live) | The live captions and analysis of the conversation going on; you can also pick a day to read its transcript |
| `/todos` | 待办 (To-dos) | To-dos due today or earlier that aren't marked yet, in three groups: 「今天」 (Today), 「本周」 (This week), 「更早」 (Earlier). Tap the round check to mark one done (or swipe left on a phone); the **⋯** menu can mark it **不对，不是待办** (Wrong: not a to-do); tap the text to open the original quote. The 「更早」 group has **全部标为已处理** (Mark all as handled); if you marked one by mistake, tap **撤销** (Undo) in the notice |
| `/c/<id>` | 对话 (Conversation) | One conversation's summary and transcript; tap a line to play from there |
| `/calendar` | 日历 (Calendar) | How many conversations each day has |
| `/speakers` | 人物 (People) | Recognized speakers |
| `/p/<name>` | 人物 (Person) | One person's conversations, points and to-dos |
| `/me` | 我的 (Me) | Every device that has recorded, with its battery, free card space and lag (those whose last recording is over 7 days old fold under 「更早的设备」 (Earlier devices)), links to the to-dos and the pages below, and the page language |
| `/progress` | 处理进度 (Progress) | How far along transcription and summaries are, AI calls and the system log |
| `/device/<name>` | 设备日志 (Device log) | One device's events and status history |
| `/connect` | 连接手机 (Connect a phone) | The Android app download, the QR code to sign in by scanning, and the settings for connecting AI (MCP) |
| `/record` | 网页录音 (Web recording) | Records with the browser's microphone or a Bluetooth recorder when you have no app |

### Web recording (`/record`)

- Source: **本机麦克风** (This device's microphone), or **蓝牙录音硬件** (Bluetooth recorder; the browser must support Web Bluetooth).
- The page cuts the microphone audio into a piece every 20 seconds and uploads it as WebM/Opus to `/api/chunks`; tapping **停止** (Stop) calls `/api/chunks/end`.
- The device name defaults to `web-recorder-<8 random characters>`, and you can change it; a Bluetooth recorder's audio is uploaded under the recorder's own name.
- Uncheck **上传** (Upload) to record on the page only, without uploading.

## App

Of the app's four bottom tabs, **时间线** (Timeline), **实时** (Live) and **人物** (People) show the server's web pages (`/`, `/live`, `/speakers`); **我的** (Me) is the app's own page:

| Entry | Content |
|---|---|
| 录音设备 (Recorder) | The bound Bluetooth recorder: battery, card, pause, live listening, offline recording sync, USB disk mode, firmware update, unbind (a Feishu recorder bean has no pause, live listening, USB disk mode or firmware update) |
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
| 本地保留录音 (Keep recordings on the phone) | Keeps a copy on the phone after upload: on iOS in the Files app › On My iPhone › alldayrec › recordings › date; on Android in `Android/data/com.suyiiyii.live_record/files/recordings/<date>/` |
| 切片长度（服务端下发） (Piece length, set by the server) | Read-only: seconds of audio per piece in the foreground and in the background (and with the screen off on Android), set by the server's `SLICE_*` variables |
