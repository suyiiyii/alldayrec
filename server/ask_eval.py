"""Fixed questions to ask.py against a live.db opened read only, the right conversations for each found from the
data itself (the people, days and tags in the questions are picked by query, so it runs on any instance). One JSON
line per question, then the totals. Acceptance: cites right >= 8 of Q1-Q10, no made-up citation, median < 20 s;
Q11 (a count, miscounted from the digest when 问 AI first went live) must give the number from the db and cite.
The lines carry the question and the answer: keep them local, report only the numbers.

    uv run python ask_eval.py <live.db> [Q1 Q3 ...]

On the server host against the main instance, read only, nothing left there (the modules go to a temp dir, LLM_* come from
its .env):
    cd server; { echo 'set -e; d=$(mktemp -d); trap "rm -rf $d" EXIT; base64 -d <<"X" | tar -xzC $d'
      tar -cz ask.py ask_eval.py db.py speakers.py summary.py | base64; echo X
      echo 'eval "$(grep -E "^LLM_(BASE_URL|API_KEY|MODEL)=" <install dir>/.env | sed "s/^/export /")"'
      echo 'cd $d && <install dir>/server/.venv/bin/python ask_eval.py <install dir>/data/live.db "$@"'; } > eval.sh
    ssh <user>@<host> 'bash -s' < eval.sh > answers.jsonl
"""
import collections
import datetime as dt
import json
import re
import statistics
import sys
import time

import anyio

import ask
import db
from speakers import ANON

NEVER = ["南极科考", "考潜水证", "买游艇", "火星移民", "冰岛看极光"]  # Q7 asks about the first one no sentence or summary has


def questions(now: dt.datetime) -> dict:
    """Qn: {q, expect: a hit cites one of these, allow: every citation within these (None: any)} or {q, check(answer, cited)}."""
    day = lambda n: (now.date() - dt.timedelta(days=n)).isoformat()
    convs = db.conversations()  # newest first
    S = lambda c: c["summary"] or {}
    done = [c for c in convs if S(c).get("status") == "done" and S(c).get("summary")]
    ids = lambda cs: {c["id"] for c in cs}
    named = lambda c: [p for p in c["participants"] if p != "我" and not ANON.fullmatch(p)]
    has = lambda p: [c for c in convs if p in c["participants"]]
    week = [c for c in convs if c["start"] >= day(6)]
    out = {}

    d1 = next(c["start"][:10] for c in done if c["start"][:10] < day(0))
    out["Q1"] = dict(q=f"{d1} 我都做了些什么？按时间顺序简要列出。", expect=ids(c for c in done if c["start"][:10] == d1),
                     allow=ids(c for c in convs if c["start"][:10] == d1))
    out["Q2"] = dict(q="最近 7 天里，别人让我做的、或者我答应别人要做的事有哪些？哪些有明确时间？",
                     expect=ids(c for c in week if any(t.get("owner") == "我" for t in S(c).get("todos") or [])),
                     allow=ids(c for c in week if S(c).get("todos")))

    p1 = collections.Counter(p for c in done if c["start"] >= day(30) for p in named(c)).most_common(1)[0][0]
    out["Q3"] = dict(q=f"我最近一次和{p1}聊了什么？", expect={has(p1)[0]["id"], next(c["id"] for c in done if p1 in c["participants"])},
                     allow=ids(has(p1)))
    n = collections.Counter(p for c in done for p in named(c))
    p2 = next(p for p, k in n.most_common() if k >= 2 and p != p1 and has(p)[0]["start"] < day(7))
    out["Q4"] = dict(q=f"我上一次和{p2}聊天是什么时候？聊了什么？", expect={has(p2)[0]["id"], next(c["id"] for c in done if p2 in c["participants"])},
                     allow=ids(has(p2)))

    tagged = collections.defaultdict(list)
    for c in done:
        for t in S(c).get("tags") or []:
            tagged[t].append(c)
    old = sorted((t for t, cs in tagged.items() if 2 <= len(cs) <= 6 and cs[0]["start"] < day(30)), key=lambda t: -len(tagged[t]))
    out["Q5"] = dict(q=f"我以前聊过「{old[0]}」相关的事吗？大概是什么时候、说了什么？", expect=ids(tagged[old[0]]), allow=None)

    month = (now.date().replace(day=1) - dt.timedelta(days=1)).isoformat()[:7]
    hours = sum(c.get("speech_s") or 0 for c in convs if c["start"].startswith(month)) / 3600
    out["Q6"] = dict(q=f"{month[:4]} 年 {int(month[5:])} 月我一共录了多少小时的对话？哪 3 天最多？", truth=round(hours, 1),
                     check=lambda a, cited: any(abs(float(x) - hours) <= 0.1 * hours for x in re.findall(r"\d+(?:\.\d+)?", a)))

    never = next(w for w in NEVER if not db.conn().execute(
        "SELECT 1 FROM sentences WHERE instr(text, ?) UNION ALL SELECT 1 FROM summaries WHERE instr(body, ?) LIMIT 1", (w, w)).fetchone())
    out["Q7"] = dict(q=f"我有没有聊过{never}的事？", check=lambda a, cited: not cited and "没" in a)

    mine = collections.Counter(t["to"] for c in done for t in S(c).get("todos") or [] if t.get("owner") == "我" and t.get("to")
                               and not ANON.fullmatch(t["to"]) and t["to"] != "我")
    p3 = mine.most_common(1)[0][0]
    out["Q8"] = dict(q=f"我答应过{p3}要做哪些事？",
                     expect=ids(c for c in done if any(t.get("owner") == "我" and t.get("to") == p3 for t in S(c).get("todos") or [])),
                     allow=ids(has(p3)) | ids(c for c in done if any(t.get("to") == p3 for t in S(c).get("todos") or [])))

    busy = collections.Counter(c["start"][:10] for c in done if day(40) <= c["start"] < day(15))
    d2 = busy.most_common(1)[0][0]
    out["Q9"] = dict(q=f"{d2} 那天我主要在忙什么？", expect=ids(c for c in done if c["start"][:10] == d2),
                     allow=ids(c for c in convs if c["start"][:10] == d2))

    t2 = next(t for t, _ in collections.Counter(t for c in week for t in S(c).get("tags") or [] if S(c).get("decisions")).most_common()
              if len(tagged[t]) <= 20)  # a topic, not the project every other conversation is tagged with
    out["Q10"] = dict(q=f"最近一次聊到「{t2}」是什么时候？定了什么？", expect=ids(tagged[t2][:2]), allow=ids(tagged[t2]))
    day1 = {c["id"] for c in convs if c["start"][:10] == d1}
    n1 = len(day1)
    undated = lambda a: re.sub(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[日号]|\d{1,2}:\d{2}|c\d{14}\S*", "", a)
    out["Q11"] = dict(q=f"{d1} 一共有几段对话？", truth=n1,
                      check=lambda a, cited: bool(re.search(rf"(?<!\d){n1}\s*段", undated(a))) and bool(cited) and set(cited) <= day1)
    return out


def score(x: dict, r: dict) -> dict:
    cited = [s["id"] for s in r["sources"]]
    made_up = [i for i in dict.fromkeys(ask.ID.findall(r["answer"])) if not db.conversation(i) and not db.conversation(i.split("-")[0])]
    if "check" in x:
        ok = x["check"](r["answer"], cited)
        return dict(ok=ok, cited=len(cited), made_up=len(made_up))
    hit, outside = bool(set(cited) & x["expect"]), [i for i in cited if x["allow"] is not None and i not in x["allow"]]
    return dict(ok=hit and not outside and not made_up, hit=hit, cited=len(cited), outside=len(outside), made_up=len(made_up))


if __name__ == "__main__":
    ro = db.read_only(sys.argv[1])
    db.conn = lambda: ro  # ask runs its tools in a thread, one at a time
    qs = questions(dt.datetime.now())
    rows = []
    for k, x in qs.items():
        if sys.argv[2:] and k not in sys.argv[2:]:
            continue
        t0 = time.time()
        try:
            r = anyio.run(ask.ask, x["q"])
        except Exception as e:
            print(json.dumps({"id": k, "error": repr(e)[:300]}, ensure_ascii=False), flush=True)
            continue
        row = {"id": k, **score(x, r), "secs": round(time.time() - t0, 1), "calls": len(r["calls"]), "usage": r["usage"],
               "q": x["q"], "answer": r["answer"], "tools": [c["tool"] for c in r["calls"]],
               "expect": len(x.get("expect") or ()), "truth": x.get("truth")}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    u = collections.Counter()
    for r in rows:
        u.update(r["usage"])
    print(json.dumps({"total": len(rows), "ok": sum(r["ok"] for r in rows), "made_up": sum(r["made_up"] for r in rows),
                      "median_s": statistics.median(r["secs"] for r in rows) if rows else None, "usage": dict(u)}), flush=True)
