---
type: reference
---

# HTTP API

<!-- 由 docs/gen-api.py 从服务端的 OpenAPI 生成，不要手改。 -->

这一页由 [`docs/gen-api.py`](https://github.com/suyiiyii/alldayrec/blob/main/docs/gen-api.py) 从服务端的 OpenAPI 生成。
说明是各个接口函数文档字符串的第一句（英文）。

## 认证

- 除了 `GET /api/health`，每个请求都要带一个租户的 token：请求头 `Authorization: Bearer <token>`，或者 cookie `token`。
- 任何一个 GET 页面带上 `?token=<token>` 打开一次，服务器会设置一年有效的 cookie，再 303 跳转到去掉 token 的地址。
- 没有有效 token：HTTP 返回 401，WebSocket 以 4401 关闭。
- `tenants.json` 里只有一个租户、且它的 token 为空时，不做认证。
- 写固件的接口（`POST /api/firmware`、`PATCH /api/firmware/{model}/{version}`）另外要 `X-Firmware-Token: <FIRMWARE_TOKEN>`。

## 不在下表里的

- 请求体是原始字节、不在 OpenAPI 里的：`POST /api/chunks` 的请求体是一个完整的音频文件，`POST /api/firmware` 的请求体是一个固件包。
- 返回的内容：表里只有请求。上传录音时用到的两个：`POST /api/chunks` 返回 `{"seconds": 这块的秒数, "slice": {…}}`，`GET /api/client-config` 返回 `{"slice": {…}}`。`slice` 是 App 现在每块音频该切的秒数（`foreground`、`background`、`screenOff`）；实时页 `/live` 开着时，`background` 和 `screenOff` 不超过 `SLICE_WATCHED_S`（见[配置](./configuration.md)）。
- `GET /ws`：WebSocket，服务器向网页推送这个租户的更新。
- 网页本身的路由（`/`、`/live`、`/conversations` 等）和 `/assets/*`，见[网页与 App](./web-and-app.md)。

## 接口

参数写法：`名字?` 表示可选；括号里是位置（query、path）和类型。

### `/api/firmware`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | One DFU package (`lr.sh pkg`) as the body; the model is the build's own (CONFIG_BT_DIS_MODEL), read by lr_release.py from the build directory the package came from. | `model` (query, string)<br>`version` (query, string)<br>`commit` (query, string)<br>`built` (query, string)<br>`notes` (query, string) |
| `GET` | A model's packages, newest first: the app's firmware page lists them with their notes. | `model` (query, string) |

### `/api/firmware/{model}/{version}`

| 方法 | 说明 | 参数 |
|---|---|---|
| `PATCH` | New notes for a published version (lr_release.py --version); the package stays as it is. | `model` (path, string)<br>`version` (path, string)<br>`notes` (query, string) |

### `/api/firmware/latest`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | The newest package of a model (what the app compares with its recorder's revision), or 404. | `model` (query, string) |

### `/api/firmware/{model}/{version}.zip`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | `model` (path, string)<br>`version` (path, string) |

### `/api/addresses`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | — |

### `/app.apk`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | The Android client, for installing from the phone's browser. | — |

### `/api/app/latest`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | What /app.apk is: the Android app compares versionCode with its own build number and offers the update. | — |

### `/api/chunks`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | One self-contained audio file (the apps' Opus or AAC pieces), `start` its device time in unix ms, `n` the 16 kHz samples the client wrote into it (the iPhone's AAC: the decoder adds some at both ends). | `device` (query, string)<br>`start` (query, integer)<br>`n?` (query, integer)<br>`hold?` (query, integer) |

### `/api/chunks/end`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | The recorder stopped at device time t (unix ms): the open conversation closes once its audio is processed. | `device` (query, string)<br>`t` (query, integer) |

### `/api/hold/release`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | No older audio of the recorder is coming from the phone: it no longer has the recorder (unbound, or another bound in its place), or its backlog is all up and no upload follows to let go (a recorder that stopped). | `device` (query, string) |

### `/api/device-status`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | What a client read from a BLE recorder (battery, card, firmware, model): each one kept, the newest shown with the device's pipeline. | `device` (query, string)<br>body: JSON object |

### `/api/device-log`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | A batch of a device's events, from the phone that read them; one sent again replaces the one kept. | `device` (query, string)<br>body: JSON DeviceEvent[] {`src`, `seq`, `at`, `data`} |
| `GET` | A device's events, status reports and audio by the minute (audio.minutes) in [since, until). | `device` (query, string)<br>`since` (query, string)<br>`until?` (query, string) |

### `/api/calls`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | The ASR and LLM calls since `since`, by kind and engine (db.calls), and the StepFun balance (db.balance). | `since` (query, string) |

### `/api/perf`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | A day's (YYYY-MM-DD) performance rows in this tenant's store, oldest first (perf.day): {stalls, samples, requests, pieces}; the event loop's stalls and the process's minutes are in the first tenant's only. | `day` (query, string) |

### `/api/upgrades`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | A device's firmware updates begun since `since`, oldest first: what its app logged joined with the recorder's DFU and BOOT records (db.upgrades). | `device` (query, string)<br>`since` (query, string) |

### `/api/client-config`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | What the apps ask on start and while recording: piece lengths per app state (the server changes them without a new build; short pieces in front make drafts quick, long ones in the background save requests). | — |

### `/api/app-setup`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | For the web page's QR: the token to hand the app (its tenant's, the one the page sent), so it doesn't need typing in by hand. | — |

### `/api/events`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | — |

### `/api/live`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | Each device's live analysis of its open conversation. | — |

### `/api/live/{device}`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | 立即总结 (the live page's button): the device's live analysis now; 409 with why not. | `device` (path, string) |

### `/api/health`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | No token and no tenant (Tenants lets it through): for reverse proxies and docker-compose.yml's healthcheck. | — |

### `/api/status`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | — |

### `/api/lang`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | What the models write this tenant's summaries, recaps and answers in (tenant.py): {"lang": "zh"} or "en". | — |
| `PUT` | The language from the next model call on; existing summaries keep theirs (重新生成 rewrites one). | body: JSON object |

### `/api/sentences`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | `date?` (query, string) |

### `/api/conversations`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | `date?` (query, string) |

### `/api/days`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | Days that have sentences, newest first. | — |

### `/api/calendar`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | Per day with sentences: closed conversations, their speech minutes and the sentence count, for the day picker and the calendar page. | — |

### `/api/search`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | db.search; `before` is the start of the last one already shown. | `q?` (query, string)<br>`tag?` (query, string)<br>`who?` (query, string)<br>`after?` (query, string)<br>`before?` (query, string)<br>`limit?` (query, integer) |

### `/api/ask`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | A question about my own past answered from this app's data (ask.py), in 10-30 s: {answer, sources, calls, usage}. | body: JSON object |

### `/api/ask/voice`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | A question said aloud (the ask sheet's hold-to-talk: what the browser's MediaRecorder wrote, webm, ogg or mp4) as text, {text}, for the page to show and ask with /api/ask; "" when nothing was heard. | — |

### `/mcp`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | No server-sent stream and no sessions to end (2026-07-28). | — |
| `POST` | Remote MCP, authenticated by a tenant bearer or an MCP-only connection link: stateless, JSON only. | — |
| `DELETE` | No server-sent stream and no sessions to end (2026-07-28). | — |

### `/api/mcp-links`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | List this tenant's AI connection links: metadata only, never the secret or its hash. | — |
| `POST` | Create a revocable MCP-only credential from JSON {label}: returns its path once (201). | — |

### `/api/mcp-links/{link_id}`

| 方法 | 说明 | 参数 |
|---|---|---|
| `DELETE` | Disable this tenant's link: 204 when calls finish, 202 {pending: true} after 2 s; unknown ids return 404. | `link_id` (path, string) |

### `/api/recap`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | A day's recap, the newest one when no day is given; null when there is none. | `date?` (query, string) |

### `/api/todos/due`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | My todos due today or before, not marked done or wrong, with how stale each is (db.due_todos). | — |

### `/api/todos/{todo_id}/mark`

| 方法 | 说明 | 参数 |
|---|---|---|
| `PUT` | 完成 (done) or 不对 (wrong) on one todo, by its id (db.todo_ids); a mark again replaces it. | `todo_id` (path, string)<br>body: JSON object |
| `DELETE` | The undo: the todo is open again. | `todo_id` (path, string) |

### `/api/todos/marks`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | Many todos at once: {ids, mark}, mark done or wrong, or null to drop their marks (the bulk undo). | body: JSON object |

### `/api/recap/{date}`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | Write a day's recap again (15-20 s), e.g. after its summaries were redone. | `date` (path, string) |

### `/api/tags`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | — |

### `/api/conversation/{conv_id}`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | `conv_id` (path, string) |

### `/api/conversation/{conv_id}/feedback`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | A correction in the owner's words (wrongly greyed as media, a wrong speaker, a bad summary); collected and read back in batches to fix the prompt, never acted on one by one. | `conv_id` (path, string)<br>body: JSON object |

### `/api/feedback`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | Every correction, newest first, with the conversation's time and title for the read-back. | — |

### `/api/conversation/{conv_id}/summarize`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | Write a conversation's summary again with the current prompt (重新生成, the backfill script resummarize.py). | `conv_id` (path, string) |

### `/api/names`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | Names already in use, most conversations first: suggestions for a rename. | — |

### `/api/conversation/{conv_id}/rename`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | One label renamed; with a name the conversation has already, a merge; to 我, its voice learnt too (learn_me). | `conv_id` (path, string)<br>body: JSON object |

### `/api/speakers/undo`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | Takes back a rename, merge or 拆开 by the undo id its answer gave: the rows, the participants, the voices, pins and name hints as they were, the summaries redone, what the renamed sentences taught the people bank (the ledger, bank.py) both ways, the centroid a 「这是我」 added to 我's voiceprint dropped. 409 when the id is unknown (taken back already, too old, the server restarted) or a conversation changed since. | body: JSON object |

### `/api/speakers`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | The speakers page's cards of people (named, or OTHERS_MIN_S of speech over OTHERS_MIN_CONVS conversations) and how many other voices there are; ?others=1 the cards of those. | `others?` (query, boolean) |

### `/api/speakers/{gid}`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | `gid` (path, string) |

### `/api/speakers/{gid}/halves`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | The two-half test of a card (bank.halves): mixed, and its two halves when it sounds like two people. | `gid` (path, string) |

### `/api/speakers/name`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | Every member voice of a group (or of one of its suggestions) takes the name, in all of its conversations; with voices (conv\|label), only those. | body: JSON object |

### `/api/speakers/detach`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | A voice that is not this speaker: a named one goes back to a fresh S&lt;n> of its conversation; either way it is kept apart from the group from now on. | body: JSON object |

### `/api/speakers/{gid}/split`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | 拆开: the voices of a card a request picks (chosen: a half from the two-half test) out of it into a card of their own, in one transaction, with an undo. | `gid` (path, string)<br>body: JSON object |

### `/api/conversation/{conv_id}/speakers`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | `conv_id` (path, string) |

### `/api/aliases`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | — |
| `POST` | Another way a person is called (A哥 for A): hints with it suggest the name. | body: JSON object |

### `/api/person/{name}`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | Everything about one named speaker: their conversations newest first with what they said there, the todos they own, and what I promised them. | `name` (path, string) |

### `/api/person/{name}/profile`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | Made (or made again) now: one chat call, some seconds. | `name` (path, string) |

### `/api/voice-clip/{name}`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | （无说明） | `name` (path, string) |

### `/api/self`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | The card while it is up: candidates most likely first (none preselected), each with a tier instead of a %. | — |
| `POST` | The answer to the card: a candidate's index becomes 我's voiceprint (owner-redimnet2.npz), taken by every pipeline from its next conversation on: the conversations before keep their labels and summaries. "none" (都不是) and "later" (以后再说) put the card away until selfvoice.due brings it again. | body: JSON object |

### `/api/audio`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | WAV of [start, end] (+0.2 s each side) cut from the uploaded pieces of `device` (any when not given) covering it. | `start` (query, string)<br>`end` (query, string)<br>`device?` (query, string) |

### `/api/listen`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | Listening in on a device (the live page polls it): its audio after `after` (unix ms, the X-After of the answer before; none: its newest piece) as WAV, X-Start the first piece's start; 204 when there is nothing newer. | `device` (query, string)<br>`after?` (query, integer) |

### `/api/stars`

| 方法 | 说明 | 参数 |
|---|---|---|
| `GET` | The starred sentences as they read now (a rename shows), the newest star first, with their conversation's start and title. ponytail: a star whose row a replay re-cut (another start, another id) drops out of the list. | — |

### `/api/sentences/{sid}/star`

| 方法 | 说明 | 参数 |
|---|---|---|
| `PUT` | （无说明） | `sid` (path, string)<br>body: JSON object |
| `DELETE` | （无说明） | `sid` (path, string) |

### `/api/sentences/{sid}/speaker`

| 方法 | 说明 | 参数 |
|---|---|---|
| `POST` | This sentence was said by `to`. | `sid` (path, string)<br>body: JSON object |
