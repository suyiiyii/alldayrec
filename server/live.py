"""Live analysis of the open conversation: one multi-turn agent per conversation keeping a timeline of topics.

Its messages are the system prompt, today's earlier conversations by their summaries, then per update a user turn with
the topic list and the sentences finalized since the last turn, and the reply verbatim. The history only grows, so each
call reuses the cached prefix. A reply is operations on the timeline (add, update, remove a topic by its id), not the
whole timeline. When the conversation closes, one more turn asks the same agent for the final summary.
"""
import json

from summary import complete, instructions, lang_line, transcript

FIELDS = ("points", "decisions", "todos", "open_questions")
SYSTEM = """你在一段正在进行的对话现场（开会、讨论、聊天、日常生活都算），持续整理议题时间线，帮在场的我随时回顾刚才讲了什么。
开始时你会先收到今天更早几段对话的总结，只作背景：帮你认出人、事和来龙去脉；时间线只写当前这段对话。
之后每一轮收到当前的议题列表（#编号 [开始时间] 标题）和刚定稿的一段新转写，每行「[时间] 说话人: 文本」。「我」是录音者本人，S1、S2… 是其他人（同一个人可能被拆成多个标签），S? 是太短没认出来的句子。转写有错别字和同音误识别，按意图理解。
「我」对电脑或手机上的 AI 助手（Claude、Codex、ChatGPT 等）做语音输入、下指令的话不是会议内容，跳过：特征是祈使句布置任务、提到代码文件日志命令、一口气一长段没人接话。
外放的视频、电视、直播、播客、音乐也不是在场的人说的，不写成议题、不写成谁的观点；在场的人围绕它聊起来时，才记成「在看/听什么、大家怎么说」。线上会议和电话不算外放。
每一轮只输出对时间线的修改，一个 JSON 对象：
{"ops": [
  {"op": "add", "title": "不超过 16 字", "start": "议题第一句的时间 HH:MM:SS，必须是转写里出现的时间", "points": ["谁提了什么主张或信息，每条一句"], "decisions": ["已经定下来的事"], "todos": [{"who": "谁做，「我」或说话人标签或名字", "what": "做什么"}], "open_questions": ["提出来了但还没定的问题"]},
  {"op": "update", "topic": 编号, "points": [...]},
  {"op": "remove", "topic": 编号}
]}
- 每一轮的新转写都要落到时间线上：延续已有议题就 update 它、把新的要点补进去；聊到了别的事就 add 新议题。闲聊、家常、玩游戏、运动也是议题，照样记
- update 只写要改的字段，列表字段给改完后的完整列表；同一件事别拆成很多个，一两句插话不单开
- 主要改最近的议题；后面的内容改变了对前面的理解（结论变了、待办落实了、问题有了答案）也可以改前面的
- 两个议题其实是一件事：update 其中一个，remove 另一个
- 一个议题的 points 2~4 条；没有的字段给空数组
- 只有新转写全是听不清的碎片、没有任何内容时才输出 {"ops": []}。只写转写里有的，不要编"""
FINAL = "对话已经结束。这一轮不要输出议题操作，改为给整段对话写最终总结，要求如下：\n"


def background(convs: list[dict]) -> str:
    """Today's earlier conversations, each by its summary: who and what this one may refer to."""
    parts = []
    for c in convs:
        s = c.get("summary") or {}
        if s.get("status") != "done" or not s.get("title"):
            continue
        lines = [f"## {c['start'][11:16]}–{c['end'][11:16]} {s['title']}（参与：{'、'.join(c['participants']) or '—'}）", s.get("summary") or ""]
        lines += [f"- {p['who']}：{'；'.join(p.get('points') or [])}" for p in s.get("people") or []]
        lines += [f"- 待办：{t.get('owner') or '未知'} {t.get('text', '')}" for t in s.get("todos") or []]
        lines += [f"- 决定：{d.get('text', '')}" for d in s.get("decisions") or []]
        parts.append("\n".join(x for x in lines if x))
    return "今天在这段对话之前的对话（只作背景）：\n\n" + ("\n\n".join(parts) or "（没有）")


def new(start: str, device: str, earlier: list[dict]) -> dict:
    return {"start": start, "device": device, "topics": [], "next_id": 1, "fed_to": "",
            "messages": [{"role": "system", "content": SYSTEM + "\n" + lang_line()}, {"role": "user", "content": background(earlier)}]}


def public(state: dict) -> dict:
    """What pages get: everything but the message history."""
    return {k: v for k, v in state.items() if k != "messages"}


def turn(state: dict, rows: list[dict]) -> tuple[str, list[dict]]:
    """(the next user turn, the rows it brings): the topic list and the rows after the last turn's."""
    fresh = [r for r in rows if r["start"] > state["fed_to"] and r.get("text")]
    listing = "\n".join(f'#{t["id"]} [{t["start"][11:19]}] {t["title"]}' for t in state["topics"]) or "（还没有）"
    return f"当前议题：\n{listing}\n\n新转写：\n{transcript(fresh) or '（没有）'}", fresh


def apply(state: dict, ops: list, rows: list[dict], fresh: list[dict]) -> tuple[list[dict], int]:
    """(topics in time order, next id). A cited time becomes the first row at or after it; an add without one starts
    at the turn's first row. Malformed operations are skipped."""
    starts = [r["start"] for r in rows]
    def at(hms, default: str) -> str:
        if not (isinstance(hms, str) and len(hms) == 8):
            return default
        return next((s for s in starts if s[11:19] >= hms), default)
    def tid(x):
        try:
            return int(str(x).lstrip("#"))
        except ValueError:
            return None
    lists = lambda op: {k: [x for x in op[k] if x] for k in FIELDS if isinstance(op.get(k), list)}
    by, next_id = {t["id"]: dict(t) for t in state["topics"]}, state["next_id"]
    for op in ops if isinstance(ops, list) else []:
        if not isinstance(op, dict):
            continue
        kind, i = op.get("op"), tid(op.get("topic"))
        if kind == "add" and op.get("title"):
            by[next_id] = {"id": next_id, "title": str(op["title"]), "start": at(op.get("start"), (fresh or rows)[0]["start"]),
                           **{k: [] for k in FIELDS}, **lists(op)}
            next_id += 1
        elif kind == "update" and i in by:
            t = by[i]
            t.update(lists(op))
            if op.get("title"):
                t["title"] = str(op["title"])
            if op.get("start"):
                t["start"] = at(op["start"], t["start"])
        elif kind == "remove":
            by.pop(i, None)
    return sorted(by.values(), key=lambda t: t["start"]), next_id


async def analyze(state: dict, rows: list[dict]) -> dict:
    """One turn: rows are the open conversation's sentences so far. Raises on HTTP and JSON errors."""
    text, fresh = turn(state, rows)
    if not fresh:
        return state
    msg = {"role": "user", "content": text}
    res, raw = await complete(state["messages"] + [msg], timeout=120, kind="live")
    topics, next_id = apply(state, res.get("ops"), rows, fresh)
    reply = {"role": "assistant", "content": raw["choices"][0]["message"]["content"]}
    return {**state, "topics": topics, "next_id": next_id, "fed_to": fresh[-1]["start"],
            "drafted": state.get("drafted") or any(r.get("state") == "draft" for r in fresh),
            "messages": state["messages"] + [msg, reply], "model": raw.get("model"), "usage": raw.get("usage")}


DRAFTED = "这段对话已经全部定稿。你前面看到的转写有一部分是实时初稿，可能有错字、漏句、说话人不对；下面是整段对话的定稿转写，总结以它为准：\n"


def final_turn(state: dict, rows: list[dict]) -> str:
    """The closing turn's transcript: the rows it has not seen; all of them, final, when it saw drafts (the final
    summary is kept and searched, so it must not rest on the draft layer's quick text)."""
    if state.get("drafted"):
        return DRAFTED + transcript(rows)
    return turn(state, rows)[0]


async def final(state: dict, rows: list[dict], known_tags: list[str], start: str) -> tuple[dict, dict]:
    """The closing turn: final_turn, then the final summary in summary.SYSTEM's format. start: the closed conversation's
    (due dates count from it; the draft layer may have started the open one on the other side of midnight)."""
    return await complete(state["messages"] + [{"role": "user", "content": f"{final_turn(state, rows)}\n\n{FINAL}{instructions(known_tags, start)}"}],
                    kind="live")


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    import anyio

    import db
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    rows = [dict(start=f"2026-10-02T10:{m:02d}:00.000", text=f"句{m}", speaker="S1") for m in range(0, 20, 2)]
    s = new("2026-10-02T10:00:00.000", "phone", [
        {"start": "2026-10-02T08:00:00", "end": "2026-10-02T08:30:00", "participants": ["我", "老王"],
         "summary": {"status": "done", "title": "早会", "summary": "定了周五上线。", "todos": [{"owner": "老王", "text": "写文档"}]}},
        {"start": "2026-10-02T09:00:00", "end": "2026-10-02T09:01:00", "participants": [], "summary": {"status": "pending"}}])
    assert "## 08:00–08:30 早会（参与：我、老王）" in s["messages"][1]["content"] and "待办：老王 写文档" in s["messages"][1]["content"]
    assert "09:00" not in s["messages"][1]["content"]
    text, fresh = turn(s, rows[:5])
    assert "（还没有）" in text and "[10:08:00]" in text and len(fresh) == 5
    topics, n = apply(s, [{"op": "add", "title": "A", "start": "10:00:00", "points": ["a", ""]}, {"op": "add", "title": "B", "start": "10:05:30"},
                          {"op": "add", "start": "10:01:00"}, "x", {"op": "update", "topic": 9}], rows[:5], fresh)
    assert [(t["id"], t["title"], t["start"][11:19]) for t in topics] == [(1, "A", "10:00:00"), (2, "B", "10:06:00")] and n == 3
    assert topics[0]["points"] == ["a"] and topics[1]["todos"] == []
    s = {**s, "topics": topics, "next_id": n, "fed_to": fresh[-1]["start"]}
    text, fresh = turn(s, rows)
    assert "#1 [10:00:00] A\n#2 [10:06:00] B" in text and "[10:08:00]" not in text and "[10:10:00]" in text
    topics, n = apply(s, [{"op": "update", "topic": "#1", "points": ["a2"], "title": "A2"}, {"op": "remove", "topic": 2},
                          {"op": "add", "title": "C", "start": "99:99:99"}, {"op": "add", "title": "D"}], rows, fresh)
    assert [(t["id"], t["title"]) for t in topics] == [(1, "A2"), (3, "C"), (4, "D")] and topics[0]["points"] == ["a2"], topics
    assert topics[1]["start"] == "2026-10-02T10:10:00.000"  # a time past every row: the turn's first row
    assert anyio.run(analyze, {**s, "fed_to": rows[-1]["start"]}, rows)["fed_to"] == rows[-1]["start"]  # nothing new: no call
    assert json.dumps(public({**s, "messages": [1]})).find("messages") < 0
    assert "S1: 句0" not in final_turn(s, rows) and "S1: 句10" in final_turn(s, rows)  # only what it has not seen
    assert final_turn({**s, "drafted": True}, rows).startswith(DRAFTED) and "S1: 句0" in final_turn({**s, "drafted": True}, rows)
    print("ok")
