---
type: reference
---

# 命令行 lr.py

[`cli/lr.py`](https://github.com/suyiiyii/alldayrec/blob/main/cli/lr.py) 在终端或 Agent 里读你的对话，输出是给 Agent 看的精简 Markdown，`--json` 给出接口的原始 JSON。它只读数据，只用 Python 标准库。

## 运行

```bash
uv run --no-project cli/lr.py <命令> [参数]
```

文件开头有 `#!/usr/bin/env -S uv run --script`，装了 uv 时也可以直接 `cli/lr.py <命令>`。

## 服务器和 token

按顺序读取：

1. 环境变量 `LIVE_RECORD_URL`（服务器地址）和 `LIVE_RECORD_TOKEN`（租户 token）；
2. 和 `lr.py` 同一目录下的 `live-record.env` 里同名的行。

## 命令

| 命令 | 参数 | 输出 |
|---|---|---|
| `day` | `[date]` | 一天的对话和摘要 |
| `search` | `[query] [--who WHO] [--tag TAG] [--since SINCE] [--until UNTIL] [--limit LIMIT]` | 按词（标题、摘要、标签、待办，没有时查原文）、参与者、标签、日期找对话，最新的在前；`--who` 可以是 `我`、`S1` 或认出的名字 |
| `show` | `id [--text]` | 一段对话：每个人的要点、话题、待办、决定；`--text` 同时给出带说话人的原文 |
| `at` | `span [date]` | 一段时间的原文，`span` 写成 `HH:MM-HH:MM` |
| `todos` | `[--since SINCE] [--until UNTIL] [--owner OWNER]` | 这些天对话摘要里的待办，最新的在前；默认最近 7 天；`--owner 我` 只看自己的 |
| `names` | — | 认出的名字，按对话数从多到少 |
| `days` | — | 有录音的日期，最新的在前 |

日期参数都可以写成 `YYYY-MM-DD`、`MM-DD`、`today`、`yesterday` 或 `-N`（N 天前）。`--json` 放在命令前面：`lr.py --json day today`。

## 例子

```bash
export LIVE_RECORD_URL=http://127.0.0.1:8770 LIVE_RECORD_TOKEN=<token>
uv run --no-project cli/lr.py at 09:30-09:32 2026-10-20
```

```text
# 2026-10-20 09:30-09:32 7 句
09:30:59 S?：大家好，
09:31:00 S?：今天下午三点我们在三楼会议室开会，
09:31:04 S1：讨论下个月的发布计划，
...
```
