---
type: reference
---

# 数据目录

每个租户有两个目录，都在 `tenants.json` 里指定：`data`（数据库和派生数据）和 `recordings`（原始录音）。按快速开始部署的 Docker 里，它们是宿主机上的 `data/main` 和 `data/main/recordings`。

## DATA_DIR

`DATA_DIR` 在 Docker 里是宿主机的 `./data`；用 uvx、pip 或从源码运行时默认是用户数据目录（见[配置项](./configuration.md)）。第一次启动生成的租户 `main` 在它下面：

```text
<DATA_DIR>/
├── tenants.json
├── models/                         模型文件（MODELS_DIR）
└── main/                           租户 main 的 data 目录
    ├── live.db                     SQLite 数据库（WAL 模式，旁边有 live.db-wal、live.db-shm）
    ├── <日期>.passes.jsonl          每次转写时引擎的原始输出
    └── recordings/                 租户 main 的 recordings 目录
        └── chunks/<设备名>/<日期>/<HH>.<扩展名>
```

## data 目录

| 路径 | 内容 |
|---|---|
| `live.db` | SQLite 数据库，见下表；结构定义在 [`server/alldayrec/db.py`](https://github.com/suyiiyii/alldayrec/blob/main/server/alldayrec/db.py) |
| `<日期>.passes.jsonl` | 每一遍转写时引擎的原始输出 |
| `owner-redimnet2.npz` | 「我」的声纹 |
| `voices/clips/` | 说话人的声音片段 |
| `firmware/` | 录音设备的固件包（只在第一个租户的目录里） |
| `live-record.apk` | 服务器在 `/app.apk` 提供的安卓 App（只在第一个租户的目录里） |

## recordings 目录

| 路径 | 内容 |
|---|---|
| `chunks/<设备名>/<日期>/<HH>.<扩展名>` | 每台设备每小时一个文件，收到的音频块原样追加在后面；每块在文件里的位置和长度记在 `live.db` 的 `pieces` 表里。扩展名按上传时的 `Content-Type`：`ogg`、`webm`、`aac`，其他是 `bin` |
| `rejected/<设备名>/<开始时刻>.<扩展名>` | 解码失败（接口返回 400）的上传，原样留着供检查 |

## live.db 的表

| 表 | 内容 |
|---|---|
| `sentences` | 定稿的句子：时间、说话人、文字、来源设备、引擎 |
| `drafts` | 初稿层的句子（只在页面开着时产生），定稿覆盖后仍保留 |
| `conversations` | 对话：起止时间、参与者、说话秒数、句数、类型、设备 |
| `summaries` | 每段对话的摘要（JSON） |
| `timelines` | 对话结束时的实时分析 |
| `voices` | 每段对话里每个说话人标签的声纹和声音片段 |
| `apart` | 被标成「不是这个人」的声音 |
| `people` | 认出名字的人的声纹 |
| `enrolled` | 每个人的声纹是从哪些句子学来的，一句一行；撤销和拆开时据此减掉 |
| `aliases` | 一个人的别名 |
| `name_hints` | 每个标签在对话里被怎么称呼（只用于建议名字） |
| `profiles` | 认出名字的人的人物卡片，每一行带出处 |
| `pieces` | 上传的音频块索引，也是处理队列 |
| `late_spans` | 迟到录音的精确区间、处理状态与重试次数（v0.4.0 起有） |
| `processed_audio` | 与定稿句子同事务保存的实际处理覆盖（含停顿）；重启及补转写据此去重（v0.4.0 起有） |
| `conversation_aliases` | 合并前的对话网址和说话人标签到保留对话的映射（v0.4.0 起有） |
| `walls` | 长对话切开的位置（每台设备一个时刻一行）：迟到音频不会把两边重新合并（v0.5.0 起有） |
| `feedback` | 用户对摘要的纠正 |
| `todo_marks` | 待办上的「完成」或「不对」标记（v0.2.0 起有） |
| `stars` | 收藏的句子：句子 id、所在对话、收藏的时刻（v0.3.0 起有） |
| `mcp_links` | AI 连接网址：名称、密钥的 SHA-256 哈希和前 8 个字符、创建和最近使用的时刻，不存密钥本身（v0.4.0 起有） |
| `kv` | 每台设备的处理进度等键值 |
| `device_log` | 手机上传的设备事件，包括录音设备自己的日志 |
| `device_status` | 客户端读到的录音设备状态（电量、存储卡、固件） |
| `calls` | 每次语音识别和大模型调用的耗时、结果和用量，不记内容 |
| `balances` | 预付费账户的余额，每小时一次 |
| `perf_stalls` | 事件循环被卡住的记录：结束时刻、毫秒、卡在哪些函数（只在第一个租户的库里；v0.3.0 起有） |
| `perf_samples` | 进程每分钟一行：CPU 秒、内存、循环延迟、各线程池占用、排队的音频块、打开的网页数（只在第一个租户的库里；v0.3.0 起有） |
| `perf_requests` | HTTP 请求：用时 50 ms 及以上的每个一行，另外每分钟按方法、路由、状态码各一行汇总（v0.3.0 起有） |
| `perf_pieces` | 每个定稿块和初稿轮次各步骤的耗时：解码、VAD、识别、声纹、写库（v0.3.0 起有） |

`perf_` 开头的表只记耗时和资源用量，不记内容，保留 90 天。`GET /api/perf?day=YYYY-MM-DD` 读一天的；从数据库副本导出用 `python -m alldayrec.perf export <live.db> <YYYY-MM-DD>`。

`enrolled` 出现之前学到的声纹，每人只有一行汇总，不对应具体的句子。那时学进了别人声音的，可以用 `python -m alldayrec.bank relearn <live.db>` 从这个人现在名下的声音（实时录音里的）重新算出这一行：只打印每人前后的句数、用上和丢掉的声音数、新旧声纹的余弦，不写库；加 `--apply` 才写，写完重启服务端（声纹在启动时载入）。之后逐句学到的不受影响。

摘要提示词更新后，已有摘要保持原样，`summaries.prompt_version` 记录各条的版本。部署新版本后，在服务端的 `server/` 目录中回填：

```sh
# LIVE_RECORD_URL 指向本次已确认的实例；LIVE_RECORD_TOKEN 从环境读取，也可从 stdin 第一行输入。
uv run python -m alldayrec.resummarize --days 30 --dry-run
uv run python -m alldayrec.resummarize --days 30 --limit 1
uv run python -m alldayrec.resummarize --days 30 --delay 5
```

范围按运行机器的日历日期，含今天共 N 天，只处理在范围内开始、已结束且说话至少 60 秒的对话；更早、仍进行中、pending 和已在当前提示词版本的跳过。先单段检查，再扩大范围；中断后重跑会跳过已完成的。单段不足 60 秒的「立即总结」保留。每段等待完成后再处理下一段，默认间隔 5 秒，`--max-delay` 默认 600 秒；HTTP 429 和模型 429 都退避。`--timeout` 默认 1800 秒，超时或 HTTP 结果不明时停止，先检查服务端是否仍在处理，再重跑。失败退出码为 1；输出完成、失败、跳过数量，已处理对话的待办与已标记数前后对比，以及期间的模型调用数（含其他后台任务）。

重写期间已成功的旧摘要可见，模型调用失败时仍保留；重试此前失败的摘要时先变成 pending，回填等待本轮结果。待办可在摘要中保存固定 `id`：唯一匹配（内容或原句相同、执行人与接收人相同）的新待办继承旧 ID，完成/不对标记行不迁移、不复制；无法唯一匹配的已标记旧待办原样带入新摘要，保留 ID 和标记，不因标记拒绝整条摘要。其他新任务使用新 ID，避免旧 ID 被另一件事复用。重命名/合并同步更新旧待办的执行人与接收人，撤销按任务 ID 恢复实际改过的字段。重复生成、撤销或重新标记始终操作同一 ID。已有未固定 ID 的摘要仍按引用时间/内容计算原 ID，首次重写时固定下来。收藏保存在 `stars`，重写摘要不改句子或收藏。

已有日回顾不会自动更新；其待办优先按固定 ID 读取当前内容和标记，旧格式按原文字段核对，找不到的只展示文字，避免旧回顾标记到另一件事。回填后建议对受影响且已存在回顾的日期逐日调用 `POST /api/recap/{YYYY-MM-DD}`（`recap_redo`），更新要点与待办引用；每个有摘要的日期额外 1 次模型调用，30 天最多 30 次。100 段通常需要 100 次摘要调用；每段最多尝试 4 次，因此最多 400 次。缺少姓名提示时每段可能再有 1 次 hints，已有档案的具名参与人还会各触发 1 次 profile；这些与回顾不包含在 100 次里。实际调用数由脚本的 ledger 统计；金额取决于配置模型和输入长度。
