"""A day's recap, for a card on the conversations page the morning after: one call over that day's summaries (ask.digest)
and a numbered list of that day's todos of mine (only that day's: a todo is never repeated on the next days; none marked
done or wrong). The model picks todos by number, so each is one a summary holds, owned by 我 (never 未知), with its
conversation and its due as said that day; its id and text stay the summary's (完成 / 不对 name a todo by its id), short
is the model's wording. Kept in kv recap:<date> by summaries.py. The model is summary.py's; nothing is pushed anywhere.

    uv run python -m alldayrec.recap                          # self-check
    uv run python -m alldayrec.recap <live.db> <date>...      # recaps of those days from a db opened read only, as JSON lines
"""
import datetime as dt

from alldayrec import ask
from alldayrec import db
from alldayrec import summary
from alldayrec.speakers import ANON

SYSTEM = """你在给我写某一天的每日回顾，第二天早上看。输入是那天每段对话的摘要（带 [对话 id]），和一个编了号的待办清单（那天里我要做的事）。
只输出一个 JSON 对象：
{"headline": "一句话：这一天主要在干什么",
 "highlights": [{"text": "值得回看的事（决定、新信息、重要的谈话），一句话", "conv": "对话 id"}],
 "todos": [{"n": 待办编号, "text": "要做的事，可以改写得更短"}],
 "people": [{"who": "有名字的人", "note": "和 TA 聊了什么，一句话"}]}
- highlights 最多 5 条，按重要性排
- todos 只从清单里挑：对人做出的承诺，或者别人明确要我做的事；对 AI 下的指令、随口一说的打算不算；最多 5 条，宁缺毋滥
- people 只列有名字的人，不列「我」和 S1、S2 这类标签
- 只写摘要里有的，不要编"""


def _next(day: str) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=1)).isoformat()


async def make(day: str) -> dict | None:
    """The recap of a day (YYYY-MM-DD); None when no conversation of it has a summary. Raises on HTTP and JSON errors."""
    convs = [c for c in db.conversations(day, _next(day)) if (c["summary"] or {}).get("status") == "done" and c["summary"].get("title")]
    if not convs:
        return None
    closed = db.closed_todos()
    todos = [{"id": i, "conv": c["id"], "text": t.get("text") or "", "to": t.get("to"), "due": t.get("due")}
             for c in convs for ts in [c["summary"].get("todos") or []] for i, t in zip(db.todo_ids(c["id"], ts), ts)
             if t.get("owner") == "我" and i not in closed]
    listing = "\n".join(f"#{i}{' →' + t['to'] if t['to'] else ''} {t['text']}{'（' + t['due'] + '）' if t['due'] else ''}"
                        for i, t in enumerate(todos, 1)) or "（没有）"
    res, raw = await summary.complete([{"role": "system", "content": SYSTEM + "\n" + summary.lang_line()},
                                 {"role": "user", "content": f"{day} 的对话摘要：\n\n{ask.digest(convs)}\n\n待办清单：\n{listing}"}], timeout=120,
                                kind="recap")
    ids = {c["id"] for c in convs}
    picked = {}
    for x in res.get("todos") or []:
        n = x.get("n") if isinstance(x, dict) else None
        if isinstance(n, int) and 1 <= n <= len(todos) and n not in picked:
            picked[n] = {**todos[n - 1], "short": str(x.get("text") or todos[n - 1]["text"])}
    return {"date": day, "created_at": dt.datetime.now().isoformat(timespec="seconds"), "model": raw.get("model"), "usage": raw.get("usage"),
            "conversations": len(convs), "headline": str(res.get("headline") or ""),
            "highlights": [{"text": str(h["text"]), "conv": h.get("conv") if h.get("conv") in ids else None}
                           for h in res.get("highlights") or [] if isinstance(h, dict) and h.get("text")][:5],
            "todos": list(picked.values())[:5],
            "people": [{"who": str(p["who"]), "note": str(p.get("note") or "")} for p in res.get("people") or []
                       if isinstance(p, dict) and p.get("who") and p["who"] != "我" and not ANON.fullmatch(str(p["who"]))][:8]}


if __name__ == "__main__":
    import json
    import sys
    import tempfile
    from pathlib import Path

    import anyio
    if sys.argv[1:]:
        ro = db.read_only(sys.argv[1])
        db.conn = lambda: ro
        for d in sys.argv[2:]:
            print(json.dumps(anyio.run(make, d), ensure_ascii=False), flush=True)
        sys.exit()
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    with db.tx() as c:
        for cid, start, body in [
                ("c20260925090000", "2026-09-25T09:00:00.000", {"todos": [{"owner": "我", "text": "太早了"}]}),
                ("c20260928090000", "2026-09-28T09:00:00.000", {"todos": [{"owner": "我", "to": "老王", "text": "寄设备", "due": "国庆之后"}]}),
                ("c20261004100000", "2026-10-04T10:00:00.000", {"todos": [{"owner": "未知", "text": "买菜"}, {"owner": "我", "text": "改存储"},
                                                                          {"owner": "我", "text": "已做完"}]}),
                ("c20261004120000", "2026-10-04T12:00:00.000", None),
                ("c20261005010000", "2026-10-05T01:00:00.000", {"todos": [{"owner": "我", "text": "第二天的"}]})]:
            db.insert_conversation(c, dict(id=cid, start=start, end=start[:14] + "30:00.000", participants=["我", "老王"], speech_s=600, sentences=1))
            if body:
                db.put_summary({"conversation": cid, "status": "done", "title": "t" + cid[-6:], "summary": "s", **body}, c)
        db.put_summary({"conversation": "c20261004120000", "status": "pending"}, c)
    db.mark_todos([db.todo_ids("c20261004100000", [{"text": "已做完"}])[0]], "done")
    seen = []
    async def fake(msgs, timeout=300, kind=None):
        seen.append(msgs)
        return {"headline": "改存储", "highlights": [{"text": "定了存储", "conv": "c20261004100000"}, {"text": "编的", "conv": "c1"}, "x", {"conv": "c1"}],
                "todos": [{"n": 1, "text": "改存储方案"}, {"n": 1}, {"n": 9}, {"n": "1"}, {"n": 0}],
                "people": [{"who": "老王", "note": "聊存储"}, {"who": "S3"}, {"who": "我"}, "x"]}, {"model": "m"}
    summary.complete = fake
    r = anyio.run(make, "2026-10-04")
    assert "## [c20261004100000]" in seen[0][1]["content"] and "c20261004120000" not in seen[0][1]["content"], "pending summaries left out"
    listing = seen[0][1]["content"].split("待办清单")[1]
    assert listing == "：\n#1 改存储", "only the day's own todos of mine: not 未知, not the days before or after, not marked done"
    assert r["conversations"] == 1 and r["headline"] == "改存储" and r["model"] == "m"
    assert r["highlights"] == [{"text": "定了存储", "conv": "c20261004100000"}, {"text": "编的", "conv": None}], r["highlights"]
    assert r["todos"] == [{"id": db.todo_ids("c20261004100000", [{"text": "改存储"}])[0], "conv": "c20261004100000", "text": "改存储", "to": None,
                           "due": None, "short": "改存储方案"}], r["todos"]
    assert r["people"] == [{"who": "老王", "note": "聊存储"}]
    assert anyio.run(make, "2026-10-03") is None and len(seen) == 1, "a day without summaries: no call"
    print("ok")
