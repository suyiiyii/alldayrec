---
type: reference
---

# CLI: lr.py

[`cli/lr.py`](https://github.com/suyiiyii/alldayrec/blob/main/cli/lr.py) reads your conversations in a terminal or an agent. Its output is compact Markdown meant for agents; `--json` gives the raw JSON of the API. It only reads data and uses only the Python standard library.

## Run

```bash
uv run --no-project cli/lr.py <command> [args]
```

The file starts with `#!/usr/bin/env -S uv run --script`, so with uv installed you can also run `cli/lr.py <command>` directly.

## Server and token

`lr.py` reads them in this order:

1. The environment variables `LIVE_RECORD_URL` (the server address) and `LIVE_RECORD_TOKEN` (the tenant token);
2. The lines with the same names in `live-record.env`, in the same directory as `lr.py`.

## Commands

| Command | Arguments | Output |
|---|---|---|
| `day` | `[date]` | A day's conversations and summaries |
| `search` | `[query] [--who WHO] [--tag TAG] [--since SINCE] [--until UNTIL] [--limit LIMIT]` | Finds conversations by word (titles, summaries, tags, to-dos, and the transcript when those have no match), participant, tag and date, newest first; `--who` can be `我` (me), `S1` or a recognized name |
| `show` | `id [--text]` | One conversation: each person's points, topics, to-dos, decisions; `--text` adds the transcript with speakers |
| `at` | `span [date]` | The transcript of a time span, with `span` written as `HH:MM-HH:MM` |
| `todos` | `[--since SINCE] [--until UNTIL] [--owner OWNER]` | To-dos from the summaries of these days' conversations, newest first; the last 7 days by default; `--owner 我` shows only your own |
| `names` | — | Recognized names, by number of conversations, most first |
| `days` | — | Dates with recordings, newest first |

You can write any date argument as `YYYY-MM-DD`, `MM-DD`, `today`, `yesterday` or `-N` (N days ago). Put `--json` before the command: `lr.py --json day today`.

## Example

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
