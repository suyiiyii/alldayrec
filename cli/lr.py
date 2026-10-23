#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Read your alldayrec conversations: lr day | search | show | at | todos | names | days.

Output is compact Markdown for agents; --json gives the raw API answer. The server and token come from
LIVE_RECORD_URL / LIVE_RECORD_TOKEN, else from live-record.env next to this script, else from
~/.personal-os/secrets/record-live.env.
"""
import argparse
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ENVS = [Path(__file__).resolve().parent / "live-record.env", Path.home() / ".personal-os/secrets/record-live.env"]


def conf(key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    for env in ENVS:
        for line in env.read_text().splitlines() if env.exists() else []:
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    sys.exit(f"{key} is not set (env, {' or '.join(map(str, ENVS))})")


def get(path: str, **params):
    q = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    req = urllib.request.Request(conf("LIVE_RECORD_URL").rstrip("/") + path + (f"?{q}" if q else ""),
                                 headers={"Authorization": "Bearer " + conf("LIVE_RECORD_TOKEN")})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def day(s: str | None) -> dt.date:
    today = dt.date.today()
    if s in (None, "", "today", "今天"):
        return today
    if s in ("yesterday", "昨天"):
        return today - dt.timedelta(days=1)
    if s.lstrip("-").isdigit() and s.startswith("-"):
        return today + dt.timedelta(days=int(s))
    return dt.date.fromisoformat(s if len(s) == 10 else f"{today.year}-{s}")


def hm(t: str | None) -> str:
    return t[11:16] if t else "--:--"


def summary_of(c: dict) -> dict:
    s = c.get("summary") or {}
    return s if s.get("status") == "done" else {}


def line(c: dict) -> str:
    s = summary_of(c)
    title = s.get("title") or ("（进行中）" if c.get("state") == "open" else "（无摘要）")
    who = ",".join(c.get("participants") or [])
    kind = "" if c.get("kind", "对话") == "对话" else f" {c['kind']}"
    out = f"- `{c.get('id', '进行中')}` {c['start'][5:10]} {hm(c['start'])}-{hm(c.get('end') or c['start'])} [{who}]{kind} **{title}**"
    if s.get("summary"):
        out += f"\n  {s['summary']}"
    return out


def cmd_day(a):
    cs = get("/api/conversations", date=day(a.date).isoformat())
    if a.json:
        return cs
    return f"# {day(a.date)} {len(cs)} 段对话\n" + "\n".join(map(line, cs))


def cmd_search(a):
    after = day(a.since).isoformat() if a.since else None
    before = (day(a.until) + dt.timedelta(days=1)).isoformat() if a.until else None
    cs = get("/api/search", q=a.query, who=a.who, tag=a.tag, after=after, before=before, limit=a.limit)
    if a.json:
        return cs
    more = f"（只列了前 {a.limit} 段，--limit 放宽或 --until 往前翻）" if len(cs) == a.limit else ""
    return f"# 搜到 {len(cs)} 段{more}\n" + "\n".join(map(line, cs))


def row(r: dict) -> str:
    return f"{r['start'][11:19]} {r.get('speaker') or '?'}：{r.get('text', '')}"


def cmd_show(a):
    c = get(f"/api/conversation/{a.id}")
    if a.json:
        return c
    s, out = summary_of(c), [line(c).split("\n")[0][2:]]
    if not s:
        out.append(f"摘要状态：{(c.get('summary') or {}).get('status', '无')}")
    for p in s.get("people") or []:
        out.append(f"\n## {p['who']}\n" + "\n".join(f"- {x}" for x in p.get("points") or []))
    for t in s.get("topics") or []:
        out.append(f"\n## 话题：{t['title']}（{hm(t['at'])}）\n" + "\n".join(f"- {x}" for x in t.get("points") or []))
    if s.get("todos"):
        out.append("\n## 待办\n" + "\n".join(f"- [{t.get('owner')}" + (f" → {t['to']}" if t.get("to") else "") + f"] {t['text']}" + (f"（{t['due']}）" if t.get("due") else "")
                                           + f"  原话 {hm(t['at'])}：{t.get('quote', '')}" for t in s["todos"]))
    if s.get("ai_requests"):
        out.append("\n## 让 AI 做的事\n" + "\n".join(f"- {hm(t.get('at'))} {t['text']}" for t in s["ai_requests"]))
    if s.get("decisions"):
        out.append("\n## 决定\n" + "\n".join(f"- {d['text']}  原话 {hm(d['at'])}：{d.get('quote', '')}" for d in s["decisions"]))
    if a.text:
        out.append("\n## 原文\n" + "\n".join(map(row, c["rows"])))
    else:
        out.append(f"\n（原文 {len(c['rows'])} 句，加 --text 查看）")
    return "\n".join(out)


def cmd_at(a):
    d = day(a.date)
    lo, _, hi = a.span.partition("-")
    lo, hi = f"{d}T{lo:0>5}", f"{d}T{(hi or '23:59'):0>5}:59"
    rows = [r for r in get("/api/sentences", date=d.isoformat()) if lo <= r["start"] <= hi]
    if a.json:
        return rows
    return f"# {d} {a.span} {len(rows)} 句\n" + "\n".join(map(row, rows))


def cmd_todos(a):
    end, start = day(a.until), day(a.since)
    out, n = [], 0
    for i in range((end - start).days, -1, -1):
        d = start + dt.timedelta(days=i)
        items = [(c, t) for c in get("/api/conversations", date=d.isoformat())
                 for t in summary_of(c).get("todos") or [] if not a.owner or t.get("owner") == a.owner]
        if a.json:
            out += [{**t, "conversation": c["id"], "title": summary_of(c).get("title")} for c, t in items]
        elif items:
            n += len(items)
            out.append(f"\n## {d}\n" + "\n".join(
                f"- {hm(t['at'])} [{t.get('owner')}" + (f" → {t['to']}" if t.get("to") else "") + f"] {t['text']}" + (f"（{t['due']}）" if t.get("due") else "")
                + f"  ← {summary_of(c).get('title')} `{c['id']}`" for c, t in items))
    return out if a.json else f"# {start} ~ {end} 待办 {n} 条" + "".join(out)


def cmd_names(a):
    n = get("/api/names")
    return n if a.json else "\n".join(n)


def cmd_days(a):
    d = get("/api/days")
    return d if a.json else "\n".join(d)


def main():
    p = argparse.ArgumentParser(prog="lr", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--json", action="store_true", help="raw API JSON")
    sub = p.add_subparsers(required=True)
    DATE = "YYYY-MM-DD, MM-DD, today, yesterday or -N days"

    s = sub.add_parser("day", help="one day's conversations with summaries")
    s.add_argument("date", nargs="?", help=DATE)
    s.set_defaults(fn=cmd_day)

    s = sub.add_parser("search", help="conversations by words, person, tag, dates; newest first")
    s.add_argument("query", nargs="?", default="", help="words in title/summary/tags/todos, else transcript")
    s.add_argument("--who", help="a participant, e.g. 我, S1 or a real name (see `lr names`)")
    s.add_argument("--tag")
    s.add_argument("--since", help=DATE)
    s.add_argument("--until", help=DATE + " (inclusive)")
    s.add_argument("--limit", type=int, default=30)
    s.set_defaults(fn=cmd_search)

    s = sub.add_parser("show", help="one conversation: people's points, topics, todos, decisions")
    s.add_argument("id")
    s.add_argument("--text", action="store_true", help="also the transcript with speakers")
    s.set_defaults(fn=cmd_show)

    s = sub.add_parser("at", help="transcript of a time span, e.g. `lr at 15:00-15:30 yesterday`")
    s.add_argument("span", help="HH:MM-HH:MM")
    s.add_argument("date", nargs="?", help=DATE)
    s.set_defaults(fn=cmd_at)

    s = sub.add_parser("todos", help="todos from conversation summaries over days, newest first")
    s.add_argument("--since", default="-6", help=DATE + " (default: 7 days ago)")
    s.add_argument("--until", help=DATE + " (default: today)")
    s.add_argument("--owner", help="只看某人的，如 我")
    s.set_defaults(fn=cmd_todos)

    sub.add_parser("names", help="known real names, most conversations first").set_defaults(fn=cmd_names)
    sub.add_parser("days", help="days that have recordings, newest first").set_defaults(fn=cmd_days)

    a = p.parse_args()
    out = a.fn(a)
    print(json.dumps(out, ensure_ascii=False, indent=1) if a.json else out)


if __name__ == "__main__":
    main()
