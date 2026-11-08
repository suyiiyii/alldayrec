"""Questions about my own past, answered from this app's data only: the last DIGEST_DAYS days of summaries sit in the
system prompt, and tools over live.db (search, one day, one conversation, a person, todos, the calendar, grep the
transcript) reach anything older or a detail. Every claim cites its [conversation id]. The model is summary.py's;
nothing else is called.
"""
import collections
import datetime as dt
import json
import logging
import re
import time
import urllib.error

import anyio
import anyio.to_thread

from alldayrec import db
from alldayrec import local_asr
from alldayrec import summary
from alldayrec import threads
from alldayrec import ws
from alldayrec.core import iso
from alldayrec.tenant import T

log = logging.getLogger("live")

ROUNDS = 8          # tool rounds; the next call must answer
DIGEST_DAYS = 7     # today and the 6 days before it, by their summaries
TRANSCRIPT_CHARS = 8000
TOOL_CHARS = 20000  # one tool result sent back, at most
ID = re.compile(r"c\d{14}(?:-[A-Za-z0-9_]+)?")  # device_pipeline.py record(): c<start to the second>[-<device>]
AT = re.compile(f"({ID.pattern}) (\\d\\d:\\d\\d:\\d\\d)")  # [id HH:MM:SS]: a citation of one sentence

SYSTEM = """你在帮我回答关于我自己过去生活的问题。资料来自我随身录音的转写和每段对话的摘要：「我」是录音者本人，S1、S2… 是没认出名字的其他人，转写有错别字和同音误识别。
下面先给你最近 {days} 天每段对话的摘要，近期的问题先看这里；更早的、或者要核对原文细节时，调用工具查。
- 只根据资料回答，不要编；每条结论后面用 [对话 id] 标出处，id 照抄资料里的
- 依据是某一句原话时，出处写到那句的时间：[对话 id HH:MM:SS]，时间照抄工具结果里那句的时间（conversation 原文的行首时间、grep 的 t）；工具结果里没看到的句子不写时间
- 搜不到不等于没有：至少换 2~3 个说法（同义词、同音字、相关的人或事）再搜，也可以用 grep 查原文，之后才能说没找到
- 找不到就直接说没找到，不要拿相关但对不上的对话凑答案
- 待办、约定里的「今天」「明天」「今晚」是那段对话当天说的，按那段对话的日期理解
- 几段、几次、多长时间、哪天最多这类数数和统计的问题，不要自己数上面的摘要（长了会数错）：用 calendar 查每天的段数和时长，用 day 拿某一天的清单和段数，都是查库的准确数字；一段对话算在它开始的那天
- 问某个人的事（最近一次和 TA 聊、聊过什么、答应过 TA 什么），先用 person 按名字查：它按参与者找，比关键词搜全
- 每个回答都要带出处，统计的回答也一样：先给数字，再写「出处：」列出数到的对话 [对话 id]（多于 10 个就列 10 个），并说明数字是查哪一天、哪个月得到的
- 回答简洁，用纯文本，不用 Markdown
{lang}
今天是 {today}（星期{weekday}）。

最近 {days} 天的对话摘要（{lo} 起）：

"""


def digest(convs: list[dict]) -> str:
    """Conversations by their summaries, each headed by its [id] and time (the model cites the ids); none without one."""
    parts = []
    for c in convs:
        s = c.get("summary") or {}
        if s.get("status") != "done" or not s.get("title"):
            continue
        lines = [f"## [{c['id']}] {c['start'][:16].replace('T', ' ')}–{c['end'][11:16]} {s['title']}"
                 f"（参与：{'、'.join(c['participants']) or '—'}）", s.get("summary") or ""]
        if s.get("topics"):  # the summary is one sentence (prompt v11): what else was talked about
            lines.append("- 议题：" + "；".join(f"{x.get('title') or ''}（{'；'.join(x['points'])}）" if x.get("points") else str(x.get("title") or "")
                                              for x in s["topics"]))  # points too: a meal or other chitchat is only there
        lines += [f"- {p['who']}：{'；'.join(p.get('points') or [])}" for p in s.get("people") or []]
        lines += [f"- 待办：{t.get('owner') or '未知'}{'→' + t['to'] if t.get('to') else ''} {t.get('text', '')}"
                  f"{'（' + t['due'] + '）' if t.get('due') else ''}" for t in s.get("todos") or []]
        lines += [f"- 决定：{d.get('text', '')}" for d in s.get("decisions") or []]
        if s.get("tags"):
            lines.append("- 标签：" + "、".join(s["tags"]))
        parts.append("\n".join(x for x in lines if x))
    return "\n\n".join(parts) or "（没有）"


# tools: thin views of db, small enough to send back

def _brief(c: dict) -> dict:
    s = c.get("summary") or {}
    return {"id": c["id"], "start": c["start"][:16], "end": c["end"][11:16],
            "participants": c["participants"], "title": s.get("title"), "summary": s.get("summary")}


def _next_day(date: str) -> str:
    return (dt.date.fromisoformat(date[:10]) + dt.timedelta(days=1)).isoformat()


def t_search(q="", after="", before="", who="", tag="", limit=20):
    return [{**_brief(c), "summary": ((c["summary"] or {}).get("summary") or "")[:160], "tags": (c["summary"] or {}).get("tags")}
            for c in db.search(q, tag, who, after, before, min(int(limit), 40))]


def t_day(date):
    """The day's conversations by their start, and how many: the model miscounts a long list."""
    cs = db.conversations(date[:10], _next_day(date))
    return {"date": date[:10], "count": len(cs),
            "conversations": [{**_brief(c), "todos": [t.get("text") for t in (c["summary"] or {}).get("todos") or []],
                               "decisions": [d.get("text") for d in (c["summary"] or {}).get("decisions") or []]} for c in cs]}


def t_conversation(id, with_transcript=True):
    c = db.conversation(id)
    if not c:
        return {"error": "没有这段对话"}
    s = db.summary(id) or {}
    out = {**_brief(c), "summary": {k: s.get(k) for k in ("summary", "people", "tags", "topics", "todos", "decisions", "ai_requests")},
           "title": s.get("title")}
    if with_transcript:
        t = summary.transcript(db.sentences(c["start"], c["end"], inclusive=True, source=c.get("device")))
        out["transcript"] = t[:TRANSCRIPT_CHARS] + (f"\n…（截断，共 {len(t)} 字）" if len(t) > TRANSCRIPT_CHARS else "")
    return out


def t_person(name):
    cs = [c for c in db.conversations() if name in c["participants"]]
    if not cs:
        return {"error": "没有叫这个名字的说话人"}
    todos = [(c, t) for c in cs for t in (c["summary"] or {}).get("todos") or []]
    return {"name": name, "count": len(cs), "first": cs[-1]["start"][:16], "last": cs[0]["start"][:16],
            "recent": [{"id": c["id"], "start": c["start"][:16], "title": (c["summary"] or {}).get("title"),
                        "points": next((p.get("points") for p in (c["summary"] or {}).get("people") or [] if p.get("who") == name), [])}
                       for c in cs[:12]],
            "their_todos": [{"id": c["id"], "text": t.get("text"), "due": t.get("due")} for c, t in todos if t.get("owner") == name][:20],
            "my_todos_to_them": [{"id": c["id"], "text": t.get("text"), "due": t.get("due")} for c, t in todos
                                 if t.get("owner") == "我" and t.get("to") == name][:20]}


def t_todos(after, before, owner=""):
    return [{"id": c["id"], "date": c["start"][:16], **{k: t.get(k) for k in ("owner", "to", "text", "due", "quote")}, "todo_id": i}
            for c in db.conversations(after, before) for ts in [(c["summary"] or {}).get("todos") or []]
            for i, t in zip(db.todo_ids(c["id"], ts), ts) if not owner or t.get("owner") == owner][:80]


def t_calendar(month):
    """Per day: conversations and speech minutes, as the calendar page counts them."""
    return {d: {"conversations": n, "speech_min": round(s / 60, 1)}
            for d, n, s in db.conn().execute("SELECT substr(start, 1, 10), count(*), coalesce(sum(speech_s), 0) "
                                             "FROM conversations WHERE start LIKE ? GROUP BY 1", (month[:7] + "%",))}


def t_grep(q, after="", before="", limit=30):
    """Each sentence with the conversation it is in (its device's), for the citation."""
    return [dict(r) for r in db.conn().execute(
        """SELECT substr(s.start, 1, 19) t, s.speaker, s.text, (SELECT c.id FROM conversations c WHERE c.start <= s.start AND s.start <= c.end
             AND (c.device IS NULL OR c.device = s.source) ORDER BY c.start DESC LIMIT 1) id
           FROM sentences s WHERE instr(s.text, ?) AND s.start >= ? AND s.start < ? ORDER BY s.start DESC LIMIT ?""",
        (q, after or "0", before or "9", min(int(limit), 60)))]


TOOLS = {"search": t_search, "day": t_day, "conversation": t_conversation, "person": t_person, "todos": t_todos,
         "calendar": t_calendar, "grep": t_grep}
S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}


def _fn(name, desc, props, req=()):
    return {"type": "function", "function": {"name": name, "description": desc,
                                             "parameters": {"type": "object", "properties": props, "required": list(req)}}}


SPECS = [
    _fn("search", "按关键词找对话（最新的在前）：q 是一个子串，原样匹配标题、摘要、标签、待办、决定，摘要没有时再匹配原文；不分词，一次只放一个词。"
        "可按开始时间 after（含）/ before（不含）、参与者 who、标签 tag 过滤，时间写 YYYY-MM-DD。", {"q": S, "after": S, "before": S, "who": S, "tag": S, "limit": I}),
    _fn("day", "某一天（YYYY-MM-DD）开始的全部对话：count 是段数，conversations 是每段的时间、参与者、标题、摘要、待办、决定。", {"date": S}, ["date"]),
    _fn("conversation", f"一段对话的完整摘要（议题、每个人说了什么、待办、决定、对 AI 的请求），可带原文（最多 {TRANSCRIPT_CHARS} 字）。",
        {"id": S, "with_transcript": B}, ["id"]),
    _fn("person", "一个有名字的说话人：和我聊过几次、最早最近、最近 12 段对话里 TA 说了什么、TA 的待办、我答应 TA 的事。", {"name": S}, ["name"]),
    _fn("todos", "开始时间在 [after, before) 里的对话摘要中的待办（谁做、答应给谁、内容、原话里的时间、原句）。owner 填「我」只看我的。",
        {"after": S, "before": S, "owner": S}, ["after", "before"]),
    _fn("calendar", "某月（YYYY-MM）每天开始的对话段数和说话分钟数，查库的准确数字。", {"month": S}, ["month"]),
    _fn("grep", "在原文里找包含某个子串的句子（最新的在前）：时间、说话人、原文、所在对话 id。转写有同音错字，找不到可以换同音或近义的词再试。",
        {"q": S, "after": S, "before": S, "limit": I}, ["q"]),
]


def system(now: dt.datetime) -> str:
    lo = (now.date() - dt.timedelta(days=DIGEST_DAYS - 1)).isoformat()
    return SYSTEM.format(days=DIGEST_DAYS, today=now.date().isoformat(), weekday="一二三四五六日"[now.weekday()], lo=lo,
                         lang=summary.lang_line()) + \
        digest(db.conversations(lo, _next_day(now.date().isoformat())))


def sources(answer: str) -> list[dict]:
    """The conversations the answer cites, in order, those that exist (a device suffix the model added or broke is dropped);
    a citation of one sentence ([id HH:MM:SS], kept by checked) once more with `at`, that sentence's start, and `ref`, the
    citation as written."""
    out = {}
    for m in re.finditer(f"{AT.pattern}|({ID.pattern})", answer):
        x, hms = (m[1], m[2]) if m[1] else (m[3], None)
        c = db.conversation(x) or db.conversation(x.split("-")[0])
        if c and (c["id"], hms) not in out:
            at = next((r["start"] for r in db.sentences(c["start"], c["end"], inclusive=True, source=c.get("device"))
                       if r["start"][11:19] == hms), None) if hms else None
            out[c["id"], hms] = {"id": c["id"], "start": c["start"], "title": (db.summary(c["id"]) or {}).get("title"),
                                 **({"at": at, "ref": m[0]} if at else {})}
    return list(out.values())


def shown(name: str, res, out: str) -> set[tuple[str, str]]:
    """(conversation id, HH:MM:SS) of each sentence a tool result showed the model: the conversation tool's transcript
    lines, grep's rows; out is what was sent (cut at TOOL_CHARS)."""
    if name == "conversation" and isinstance(res, dict) and res.get("id"):
        pairs = {(res["id"], t) for t in re.findall(r"^\[(\d\d:\d\d:\d\d)\]", res.get("transcript") or "", re.M)}
    elif name == "grep" and isinstance(res, list):
        pairs = {(r["id"], r["t"][11:19]) for r in res if isinstance(r, dict) and r.get("id") and r.get("t")}
    else:
        return set()
    return {(i, t) for i, t in pairs if t in out}


def checked(answer: str, seen: set[tuple[str, str]]) -> str:
    """The answer with each [id HH:MM:SS] whose sentence no tool result showed (shown) cut back to [id]."""
    return AT.sub(lambda m: m[0] if (m[1], m[2]) in seen else m[1], answer)


def run_tool(name: str, args: str) -> tuple[str, object]:
    """(what the model is sent, at most TOOL_CHARS of the result's JSON; the result)."""
    try:
        res = TOOLS[name](**json.loads(args or "{}"))
    except Exception as e:  # an unknown tool, bad arguments: the model reads the error and tries again
        res = {"error": repr(e)[:200]}
    return json.dumps(res, ensure_ascii=False)[:TOOL_CHARS], res


async def call(msgs: list[dict], **extra) -> dict:
    """summary.call, once more after a 429 (the relay's accounts rate-limit for a while): Retry-After seconds, at most 30."""
    try:
        return await summary.call(msgs, timeout=120, kind="ask", **extra)
    except urllib.error.HTTPError as e:
        if e.code != 429:
            raise
        wait = (e.headers or {}).get("Retry-After") or ""
        await anyio.sleep(min(float(wait), 30) if wait.replace(".", "", 1).isdigit() else 5)
        return await summary.call(msgs, timeout=120, kind="ask", **extra)


# a question said to a device: its first word is due within 3 s, and a round of tools costs 1-2 s more (11-01: DeepSeek
# without the second line looked things up for 11 of 12 recent questions, the digest had them all)
VOICE = ("\n这是在设备上用语音问的：回答很短，两三句话，要显示在 240×240 的小屏上。"
         "语音问答要快：上面的摘要能回答的就直接回答，不调用工具；摘要里确实没有、或者问的是更早的事时才调用工具。")
CITE = ("上面的回答没有用 [对话 id] 标出处。内容不变，在结论后面标上出处，id 只用前面资料和工具结果里出现过的；"
        "确实没有对应的对话（比如什么都没找到）就照原样回答。")


async def ask(q: str, now: dt.datetime | None = None, on=None) -> dict:
    """{answer, sources, calls: the tool calls made, usage: tokens over all rounds}; raises on HTTP errors. An answer
    citing no conversation is asked once more for its citations: the prompt alone left counts uncited (10-05).
    on: a question said to a device, answered short (VOICE) and streamed: awaited with {"delta": text} as the answer
    comes and {"stage": "searching"} before each round of tools; no second call for citations (it would come late)."""
    msgs = [{"role": "system", "content": system(now or dt.datetime.now()) + (VOICE if on else "")}, {"role": "user", "content": q}]
    calls, usage, seen = [], collections.Counter(), set()
    async def delta(text):
        await on({"delta": text})
    async def reply(**extra) -> dict:
        raw = await call(msgs, tools=SPECS, on_delta=delta if on else None, **extra)
        u = raw.get("usage") or {}
        usage.update(prompt=u.get("prompt_tokens") or 0, completion=u.get("completion_tokens") or 0,
                     cached=(u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        return raw["choices"][0]["message"]
    for i in range(ROUNDS + 1):
        m = await reply(**({"tool_choice": "none"} if i == ROUNDS else {}))
        if not m.get("tool_calls"):
            break
        msgs.append({"role": "assistant", "content": m.get("content"), "tool_calls": m["tool_calls"],
                     **({"reasoning_content": m["reasoning_content"]} if m.get("reasoning_content") else {})})
        if on:
            await on({"stage": "searching"})
        for tc in m["tool_calls"]:
            out, res = await anyio.to_thread.run_sync(run_tool, tc["function"]["name"], tc["function"]["arguments"], limiter=threads.DB)  # grep scans every row
            seen |= shown(tc["function"]["name"], res, out)
            calls.append({"tool": tc["function"]["name"], "args": tc["function"]["arguments"], "chars": len(out)})
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
    answer = m.get("content") or ""
    if not on and not sources(answer):
        msgs += [{"role": "assistant", "content": answer}, {"role": "user", "content": CITE}]
        try:
            answer = (await reply(tool_choice="none")).get("content") or answer
        except Exception as e:  # HTTP errors, timeouts: the answer without citations is still the answer
            log.warning("ask: citations not added: %r", e)
    answer = checked(answer, seen)
    return {"answer": answer, "sources": sources(answer), "calls": calls, "usage": dict(usage)}


HEAR_S = 1.5  # a question said to a device: the most its transcript is waited for, from when it is asked
TAIL_S = 0.5  # ... in by when its last sentence reaches this close to the end (the button let go after the last word)


def said(device: str, lo: dt.datetime, hi: dt.datetime) -> list[dict]:
    """The device's sentences that overlap [lo, hi], in time order: its final rows, its drafts after them and the
    sentence still being said (partial); a standby's (colocate.py) are those of the device it is left to."""
    st = T().devices.get(device)
    if st and st.standby:
        device, st = st.standby, T().devices.get(st.standby)
    a, b = iso(lo), iso(hi)
    rows = [r for r in db.sentences(iso(lo - dt.timedelta(minutes=1)), b, source=device) if r["end"] > a]
    if st:
        done = iso(st.done_to) if st.done_to else ""
        rows += [r for r in st.drafts + [st.partial or {}] if r and r["start"] >= done and r["end"] > a and r["start"] < b]
    return sorted((r for r in rows if r.get("text")), key=lambda r: r["start"])


def trim(rows: list[dict], lo: dt.datetime) -> list[dict]:
    """rows with the words said before lo dropped from a sentence that starts before it (by time, evenly over its text)."""
    out = []
    for r in rows:
        a, b = dt.datetime.fromisoformat(r["start"]), dt.datetime.fromisoformat(r["end"])
        if a < lo < b:
            r = {**r, "text": r["text"][round(len(r["text"]) * (lo - a) / (b - a)):]}
        out.append(r)
    return out


async def heard(device: str, lo: dt.datetime, hi: dt.datetime) -> str:
    """The words of said() once its last sentence reaches TAIL_S before hi (qwen-stream's words come 0.5-1 s after
    the audio), or what is in after HEAR_S."""
    t0, tail = time.monotonic(), iso(hi - dt.timedelta(seconds=TAIL_S))
    while not ((rows := said(device, lo, hi)) and rows[-1]["end"] >= tail) and time.monotonic() - t0 < HEAR_S:
        await anyio.sleep(0.05)
    return local_asr.text([" " + r["text"] for r in trim(rows, lo)])


async def live(device: str, id: str, lo: dt.datetime, hi: dt.datetime):
    """A question said to the device between lo and hi, answered on /ws: {type: ask, device, id} with stage heard (q),
    searching, the answer's deltas, then stage done (answer) or error (error). Nothing kept."""
    t0, first = time.monotonic(), None
    async def send(m: dict):
        nonlocal first
        if "delta" in m and first is None:
            first = time.monotonic() - t0
        await ws.broadcast({"type": "ask", "device": device, "id": id, **m})
    try:
        q = await heard(device, lo, hi)
        t_heard = time.monotonic() - t0
        if not q:
            log.info("voice ask %s: nothing heard in %.1f s", device, t_heard)
            return await send({"stage": "error", "error": "没听清"})
        await send({"stage": "heard", "q": q})
        r = await ask(q, on=send)
        log.info("voice ask %s: heard in %.1f s, first word %.1f s, done %.1f s, %d tool calls", device, t_heard,
                 first or 0, time.monotonic() - t0, len(r["calls"]))
        await send({"stage": "done", "answer": r["answer"]})
    except Exception as e:  # HTTP errors, timeouts
        log.warning("voice ask %s failed: %r", device, e)
        await send({"stage": "error", "error": "模型调用失败"})


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    convs = [dict(id="c20260930100000", start="2026-09-30T10:00:00.000", end="2026-09-30T10:30:00.000", participants=["我", "老王"], speech_s=900, sentences=2, device="phone"),
             dict(id="c20260930100000-LR_X1_567E", start="2026-09-30T10:00:00.000", end="2026-09-30T10:05:00.000", participants=["我"], speech_s=30, sentences=1, device="LR-X1-567E"),
             dict(id="c20260801090000", start="2026-08-01T09:00:00.000", end="2026-08-01T09:20:00.000", participants=["我", "S1"], speech_s=600, sentences=1)]
    with db.tx() as c:
        for x in convs:
            db.insert_conversation(c, x)
        db.insert_sentences(c, [dict(id="f1", start="2026-09-30T10:01:00.000", end="2026-09-30T10:01:05.000", speaker="老王", text="周五把音频按小时存", source="phone"),
                                dict(id="f2", start="2026-09-30T10:02:00.000", end="2026-09-30T10:02:03.000", speaker="我", text="好的我来改", source="phone"),
                                dict(id="f3", start="2026-09-30T10:01:00.000", end="2026-09-30T10:01:04.000", speaker="我", text="按小时存的另一台", source="LR-X1-567E"),
                                dict(id="f4", start="2026-08-01T09:05:00.000", end="2026-08-01T09:05:04.000", speaker="S1", text="去医院复查", source="phone")])
        db.put_summary({"conversation": "c20260930100000", "status": "done", "title": "存储方案", "summary": "定了按小时存。", "tags": ["存储"],
                        "topics": [{"title": "存储格式", "points": ["按小时"]}, {"title": "闲聊", "points": ["猫"]}],
                        "people": [{"who": "老王", "points": ["建议按小时存"]}], "decisions": [{"text": "按小时存"}],
                        "todos": [{"owner": "我", "to": "老王", "text": "改存储", "due": "周五"}, {"owner": "未知", "text": "买菜"}]}, c)
        db.put_summary({"conversation": "c20260801090000", "status": "done", "title": "体检", "summary": "聊复查。", "todos": []}, c)
    now = dt.datetime(2026, 10, 2, 12)
    sp = system(now)
    assert "最近 7 天的对话摘要（2026-09-26 起）" in sp and "星期五" in sp, sp[-300:]
    assert "## [c20260930100000] 2026-09-30 10:00–10:30 存储方案（参与：我、老王）" in sp
    assert "定了按小时存。\n- 议题：存储格式（按小时）；闲聊（猫）\n- 老王：建议按小时存" in sp and "- 待办：我→老王 改存储（周五）" in sp and "- 待办：未知 买菜" in sp and "- 标签：存储" in sp and "体检" not in sp
    assert sorted(x["id"] for x in t_search("按小时")) == ["c20260930100000", "c20260930100000-LR_X1_567E"], "summary words, the other device's transcript"
    assert [x["id"] for x in t_search("复查", before="2026-08-01")] == [] and t_search("复查", after="2026-08-01")[0]["id"] == "c20260801090000"
    d = t_day("2026-09-30")
    assert [x["id"] for x in d["conversations"]] == ["c20260930100000", "c20260930100000-LR_X1_567E"] and d["conversations"][0]["decisions"] == ["按小时存"]
    assert d["count"] == 2 and t_day("2026-08-01")["count"] == 1 and t_day("2026-08-02") == {"date": "2026-08-02", "count": 0, "conversations": []}
    one = t_conversation("c20260930100000")
    assert one["title"] == "存储方案" and "[10:01:00] 老王: 周五把音频按小时存" in one["transcript"] and "另一台" not in one["transcript"], one
    assert "error" in t_conversation("c1") and "transcript" not in t_conversation("c20260930100000", with_transcript=False)
    p = t_person("老王")
    assert p["count"] == 1 and p["recent"][0]["points"] == ["建议按小时存"] and p["my_todos_to_them"] == [{"id": "c20260930100000", "text": "改存储", "due": "周五"}], p
    assert [t["text"] for t in t_todos("2026-09-01", "2026-10-01")] == ["改存储", "买菜"] and len(t_todos("2026-09-01", "2026-10-01", "我")) == 1
    assert t_calendar("2026-09") == {"2026-09-30": {"conversations": 2, "speech_min": 15.5}} and t_calendar("2026-08") == {"2026-08-01": {"conversations": 1, "speech_min": 10.0}}
    g = t_grep("按小时")
    assert sorted((x["id"], x["speaker"]) for x in g) == [("c20260930100000", "老王"), ("c20260930100000-LR_X1_567E", "我")], g
    assert t_grep("复查", after="2026-08-02") == [] and t_grep("复查")[0]["id"] == "c20260801090000"
    assert json.loads(run_tool("nope", "{}")[0])["error"] and json.loads(run_tool("day", '{"date": "x"}')[0])["error"]
    assert len(run_tool("search", "{}")[0]) <= TOOL_CHARS
    src = sources("定了按小时存 [c20260930100000]，另一台也说了 [c20260930100000-LR_X1_567E]，还有 [c20260930100000-x] 和编的 [c20990101000000]")
    assert [x["id"] for x in src] == ["c20260930100000", "c20260930100000-LR_X1_567E"] and src[0]["title"] == "存储方案", src
    # a citation of one sentence: kept when a tool result showed that sentence, else cut back to the conversation
    seen_ = shown("conversation", one, json.dumps(one, ensure_ascii=False)) | shown("grep", g, json.dumps(g, ensure_ascii=False))
    assert ("c20260930100000", "10:01:00") in seen_ and shown("conversation", one, "") == set() and shown("search", [], "") == set()
    a = checked("按小时存 [c20260930100000 10:01:00]，编的 [c20260930100000 10:09:09]", seen_)
    assert a == "按小时存 [c20260930100000 10:01:00]，编的 [c20260930100000]", a
    src = sources(a)
    assert [(x["id"], x.get("at"), x.get("ref")) for x in src] == [("c20260930100000", "2026-09-30T10:01:00.000", "c20260930100000 10:01:00"),
                                                                  ("c20260930100000", None, None)], src

    replies = iter([{"tool_calls": [{"id": "t1", "type": "function", "function": {"name": "grep", "arguments": '{"q": "复查"}'}}]},
                    {"content": "8 月 1 日聊过复查 [c20260801090000]。"}])
    seen = []
    async def fake(msgs, timeout=300, **extra):
        seen.append((msgs, extra))
        return {"choices": [{"message": next(replies)}], "usage": {"prompt_tokens": 100, "completion_tokens": 10}}
    summary.call = fake
    r = anyio.run(ask, "我什么时候说过复查？", now)
    assert r["answer"].startswith("8 月 1 日") and [x["id"] for x in r["sources"]] == ["c20260801090000"], r
    assert r["calls"] == [{"tool": "grep", "args": '{"q": "复查"}', "chars": len(run_tool("grep", '{"q": "复查"}')[0])}] and r["usage"]["prompt"] == 200, r
    assert seen[1][0][-1]["role"] == "tool" and "c20260801090000" in seen[1][0][-1]["content"] and "tool_choice" not in seen[1][1]
    loop = {"tool_calls": [{"id": "t", "type": "function", "function": {"name": "calendar", "arguments": '{"month": "2026-09"}'}}]}
    replies, seen = iter([loop] * ROUNDS + [{"content": "没找到"}, {"content": "没找到"}]), []
    assert anyio.run(ask, "一直查", now)["answer"] == "没找到" and len(seen) == ROUNDS + 2 and seen[ROUNDS][1]["tool_choice"] == "none"
    assert seen[-1][0][-1]["content"] == CITE and seen[-1][0][-2]["content"] == "没找到", "no citation: asked once more, kept when none"
    replies, seen = iter([{"content": "9 月 30 日有 2 段对话。"}, {"content": "9 月 30 日有 2 段对话。出处：[c20260930100000]"}]), []
    r = anyio.run(ask, "9 月 30 日有几段对话", now)
    assert [x["id"] for x in r["sources"]] == ["c20260930100000"] and len(seen) == 2 and seen[1][1]["tool_choice"] == "none" and r["usage"]["prompt"] == 200
    def busy(codes):
        async def f(msgs, timeout=300, **extra):
            seen.append(extra)
            if codes:
                raise urllib.error.HTTPError("u", codes.pop(0), "busy", {"Retry-After": "0"}, None)
            return {"choices": [{"message": {"content": "好了"}}]}
        return f
    summary.call, seen = busy([429]), []
    assert anyio.run(ask, "x", now)["answer"] == "好了" and len(seen) == 3, "one 429: tried again (then once more for citations)"
    replies, seen = iter([{"content": "有 2 段。"}]), []
    async def once(msgs, timeout=300, **extra):
        seen.append(extra)
        if len(seen) == 2:
            raise urllib.error.HTTPError("u", 502, "bad gateway", {}, None)
        return {"choices": [{"message": next(replies)}]}
    summary.call = once
    assert anyio.run(ask, "x", now)["answer"] == "有 2 段。" and len(seen) == 2, "the citation call failing keeps the answer"
    for codes, n in (([429, 429], 2), ([503], 1)):  # a second 429 and any other error: raised, no more tries
        summary.call, seen, code = busy(codes), [], codes[-1]
        try:
            anyio.run(ask, "x", now)
            raise SystemExit(f"{code} must raise")
        except urllib.error.HTTPError as e:
            assert e.code == code and len(seen) == n, (code, len(seen))
    print("ok")
