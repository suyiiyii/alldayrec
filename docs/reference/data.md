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
| `aliases` | 一个人的别名 |
| `name_hints` | 每个标签在对话里被怎么称呼（只用于建议名字） |
| `profiles` | 认出名字的人的人物卡片，每一行带出处 |
| `pieces` | 上传的音频块索引，也是处理队列 |
| `feedback` | 用户对摘要的纠正 |
| `todo_marks` | 待办上的「完成」或「不对」标记（main 上新增，v0.1.0 没有） |
| `kv` | 每台设备的处理进度等键值 |
| `device_log` | 手机上传的设备事件，包括录音设备自己的日志 |
| `device_status` | 客户端读到的录音设备状态（电量、存储卡、固件） |
| `calls` | 每次语音识别和大模型调用的耗时、结果和用量，不记内容 |
| `balances` | 预付费账户的余额，每小时一次 |
