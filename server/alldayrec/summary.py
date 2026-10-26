"""Summary of a closed conversation: one chat call returning title, summary, topics, todos and decisions.

    LLM_BASE_URL, LLM_API_KEY, LLM_MODEL   any OpenAI-compatible /chat/completions endpoint

A conversation with less than MIN_SPEECH_S of speech gets no call: its title is its first sentence.
"""
import datetime as dt
import json
import logging
import os
import time

from alldayrec import db
from alldayrec import net
from alldayrec.tenant import T

log = logging.getLogger("live")

MIN_SPEECH_S = 60
PROMPT_VERSION = 12  # 2: tags, 3: people, 4: speech to AI told apart from speech to people, 5: media played aloud, 6: no "there was none" lines, fewer people,
                     # 7: a todo's owner is who does it (not who said it), what I tell an AI is never a todo, 8: a todo's due_date (its due on the calendar),
                     # 9: each person's point has its sentence (people[].points_at), 10: shorter: summary 2~4 sentences, points and AI requests ≤ 30 字,
                     # 11: summary one sentence ≤ 40 字, business before chitchat; one 闲聊 topic; todos/decisions extracted independently,
                     # 12: preserve the actor (I only for the recorder); people points cite their own speaker
SYSTEM = """你在整理一段现场录音转写出来的对话。每行是「[时间] 说话人: 文本」。说话人标签来自声纹聚类：「我」是录音者本人，S1、S2… 是其他人，同一个人可能被拆成多个标签，S? 是太短没认出来的句子。转写有错别字和同音误识别，按意图理解。
「我」说的话有两种听众，先分清再整理：
- 对人：说给在场或线上的人听，有来有回，别人会接话、回应、提问
- 对 AI：我在对电脑或手机做语音输入，给 AI 助手（Claude、Codex、Orca、ChatGPT 等）下指令或提问，旁边的人不会接这句话。常见特征：祈使句布置任务（帮我看一下、你去查、跑一下、改成、再确认一下），提到代码、文件、日志、仓库、命令、页面，一口气说一长段没人插话，前后没有别人回应它
- 口吻像聊天也可能是对 AI（我理解一下、你详细讲一下、你这个指的是什么）：关键看有没有人接话。整段只有我在说、没有别人回应，基本就是对 AI
录音里还可能有外放的媒体：视频、电视、直播、播客、广播、音乐，是放给人听的内容，不是在场的人在交流。常见特征：播音腔或讲解腔、一个人长段独白或几个人表演式对话、内容和现场无关、在场的人不接它的话、说话人标签和其他句子对不上。线上会议和电话不算媒体（我在参与，有来有回），当面讲课、念稿也不算。
只输出一个 JSON 对象：
{"title": "不超过 20 字的标题",
 "ai_spans": [{"from": "HH:MM:SS", "to": "HH:MM:SS"}],
 "media_spans": [{"from": "HH:MM:SS", "to": "HH:MM:SS"}],
 "summary": "一句话，不超过 40 字：这段里最重要的一件事（发生了什么、定了什么），按原文保留主体：仅录音者本人所说所做写「我」，他人的行动、请求、观察和讲述写其说话人标签或姓名，不因像对 AI 下指令而改写主体",
 "people": [{"who": "说话人标签，原样照抄", "points": [{"text": "这个人在这段里说了什么：观点、提供的信息、提出的问题、表的态，1~3 条，每条不超过 30 字", "at": "依据的那一行的时间 HH:MM:SS"}]}],
 "tags": ["2~5 个标签：这段对话和什么相关（项目、领域、活动、人际场合），优先沿用已有标签"],
 "topics": [{"title": "议题", "points": ["要点，一行、不超过 30 字，每个议题最多 4 条"], "at": "议题开始那一行的时间 HH:MM:SS"}],
 "ai_requests": [{"text": "我让 AI 做的事，一行、不超过 30 字：做什么，不抄整段指令", "at": "原句那一行的时间 HH:MM:SS"}],
 "todos": [{"owner": "去做这件事的人：我 | S1 | 未知", "to": "答应给谁或替谁做，写说话人标签；不是对人的就写 null", "text": "要做的事", "due": "原话里的时间，没有写 null", "due_date": "due 换算成的日期 YYYY-MM-DD，说不准写 null", "quote": "依据的原句", "at": "原句那一行的时间 HH:MM:SS"}],
 "decisions": [{"text": "定下来的事", "quote": "依据的原句", "at": "原句那一行的时间 HH:MM:SS"}]}
ai_spans 是我对 AI 说话的连续时间段（第一句和最后一句的时间）。段内只能是我对 AI 说的话：中间夹着别人说话、或我对人说的短句（嗯、对、这个、你让他们上去吧），就在那里断开分成两段；没有就给空数组。
media_spans 是外放媒体的连续时间段（第一句和最后一句的时间）：中间夹着在场的人说话（包括我对媒体内容的点评），就在那里断开分成两段；没有就给空数组。
媒体里的声音即使有姓名也不是现场参与人；媒体里的话不是任何在场的人说的：不进 people、todos、decisions、topics，不要写成「我」说的或做的。
对 AI 说的话只进 ai_requests，不进 todos、decisions、people。todos 只收人要做的事：我答应别人的、别人答应的、别人要我做的、我自己说要做的。
todos 的 owner 是要去做这件事的人，不是说这句话的人：别人要我做的事（我答应了，或者没有拒绝），owner 是「我」、to 是对方；我让在场的 S1 去做的事，owner 是 S1、to 是「我」。
due_date 按这段对话的日期换算（最后一行写了这段对话哪天几点开始、星期几）：今天、今晚、待会儿是当天，明天、后天往后数，周五、下周一按星期数；原句的时间比开始时间早，说明过了午夜，那句话是第二天说的。「X 之前」「X 之内」取最后一天，「X 之后」取之后的第一天（国庆之后 = 国庆假期结束后的第一天）。说不出是哪一天（过几天、以后、有空、下周、最近）或者 due 是 null 时写 null，不要猜。
我对 AI 助手的吩咐（帮我查、改、跑、开分支、合并、部署、派人去调研……）说得再像吩咐人，也只进 ai_requests，不是任何人的待办。这种话前后没有人用人的口吻接话，常常是一个人连着说一长段；其他标签也可能在对 AI 说话，仍按该标签或姓名记录主体，不据语气改写成「我」。
people 只列在这段里说了实质内容的标签（观点、信息、经历、提问、表态），「我」在最前；S?、只有附和的、只说了一两句零碎话（问一句、喊人、接一句玩笑、递东西）的都不列。人多的闲聊挑主要说话的几个，一般不超过 8 个。按这个人自己的话写，不要写成别人对他的评价。
summary 只写一句，写这段里最重要的事：正事（工作、安排、决定、要办的事、重要的信息）在前，闲聊、外放的媒体、我对 AI 说的话都不写进去；整段都是闲聊、媒体或对 AI 说话时，才用这一句说聊了什么、在看或听什么、在让 AI 做什么。只写这段里有的事：没有媒体、没有对 AI 说话、没有待办时不要提「没有」。
闲聊（家常、吃喝、宠物、天气、玩笑……）夹在正事里时，在 topics 里并成一个议题，标题「闲聊」，要点只写一行，列出聊了什么（猫、鞋子、吃的）；整段都是闲聊时照常按话题分议题。
todos 和 decisions 独立检查整段对话，按上述规则逐条提取人的待办和已定事项；summary 的一句话取舍、闲聊合并和字数限制都不影响它们，不能因已写进 summary 或 topics 而省略。
写短：要点只写结论和关键信息，不复述过程；次要的议题并进相关的议题，不单列。字数限制只管 summary、要点和 ai_requests：title、todos、decisions 照旧，一条待办、一个决定都不能少。
没有就给空数组，不编造原文里没有的内容。"""


# the last line of every prompt whose reply the owner reads (summary, live, recap, ask, profiles): the tenant's lang.
# 我, S1, 未知 are keys the code matches (recap and due todos keep owner 我), never words to translate.
ZH = "用中文写；名字、说话人标签和原句（quote）照原文。"
EN = ("Write everything in English, though these instructions are in Chinese. Never translate, keep exactly as given: "
      "JSON keys, speaker labels (我, S1, S2, S?, 未知), names, quotes, times (HH:MM:SS) and dates (YYYY-MM-DD). "
      "我 is the recorder: \"I\" in prose, and only for what the recorder said or did; keep other people's actions, requests, observations and stories "
      "attributed to their speaker label or name, even when they sound like instructions to an AI. Named voices in played media are not participants. A title is at most 8 words; the summary (40 字 above) at most 25 words; a point or an AI request (30 字 above) at most 15 words.")


def lang_line() -> str:
    return EN if T().lang == "en" else ZH


def transcript(rows: list[dict]) -> str:
    """Only what any recording has: who spoke is known, who was spoken to is the model's call."""
    return "\n".join(f'[{r["start"][11:19]}] {r.get("speaker") or "S?"}: {r["text"]}' for r in rows if r.get("text"))


async def call(messages: list[dict], timeout: int = 300, kind: str = "summary", **extra) -> dict:
    """One chat call, extra fields as they are (tools, response_format): the raw response; raises on HTTP errors. Kept
    in the calls table as `kind` (summary, hints, profile, recap, live, ask): time, tokens, the error, never the text;
    a call cut off (the task cancelled) too, as CancelledError."""
    req = {"model": os.environ["LLM_MODEL"], "messages": messages, **extra}
    t, usage, err = time.perf_counter(), {}, None
    try:
        r = await net.request("POST", os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions", timeout, content=json.dumps(req).encode(),
                              headers={"Authorization": f'Bearer {os.environ["LLM_API_KEY"]}', "Content-Type": "application/json"})
        out = json.loads(r.content)
        usage = out.get("usage") or {}
        return out
    except BaseException as e:
        err = f"{type(e).__name__}: {e}"[:120]
        raise
    finally:
        db.add_call(kind, req["model"], None, round((time.perf_counter() - t) * 1000), err,
                    prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))


async def complete(messages: list[dict], timeout: int = 300, kind: str = "summary") -> tuple[dict, dict]:
    """One chat call answering a JSON object: (parsed reply, raw response); raises on HTTP errors and on a reply that is not JSON."""
    raw = await call(messages, timeout, kind, response_format={"type": "json_object"})
    return json.loads(raw["choices"][0]["message"]["content"]), raw


def instructions(known_tags: list[str], start: str) -> str:
    """SYSTEM, the tags in use, the day the conversation started (start: its ISO start), which due_date counts from, and
    the language to write in."""
    d = dt.datetime.fromisoformat(start)
    return (SYSTEM + ("\n已有标签：" + "、".join(known_tags) if known_tags else "")
            + f"\n这段对话开始于 {d:%Y-%m-%d}（周{'一二三四五六日'[d.weekday()]}）{d:%H:%M}。\n" + lang_line())


async def chat(text: str, known_tags: list[str], start: str) -> tuple[dict, dict]:
    return await complete([{"role": "system", "content": instructions(known_tags, start)}, {"role": "user", "content": text}])


def day(x) -> str | None:
    """A YYYY-MM-DD the model wrote, as it is; None for anything else."""
    try:
        return dt.date.fromisoformat(x).isoformat() if isinstance(x, str) and len(x) == 10 else None
    except ValueError:
        return None


def anchor(res: dict, rows: list[dict]) -> dict:
    """The model cites HH:MM:SS; turn each into the start of the row it names (null when none matches), topics in time order.
    A todo's due_date stays only when it is a date and the todo has a due: without words for a time it was made up.
    A person's points ({text, at} each) are kept as two lists side by side, points (the texts, what every reader takes)
    and points_at; a plain text point has no time. A cited point must match its speaker as well as its time;
    unsupported citations are cleared without guessing a different line or changing the point text."""
    def at(hms, who=None):
        return next((r["start"] for r in rows if r["start"][11:19] == hms and (who is None or r.get("speaker") == who)), None) if isinstance(hms, str) else None
    for k in ("topics", "todos", "decisions", "ai_requests"):
        res[k] = [{**x, "at": at(x.get("at"))} for x in res.get(k) or []]
    res["todos"] = [{**x, "due_date": day(x.get("due_date")) if x.get("due") else None} for x in res["todos"]]
    for k in ("ai_spans", "media_spans"):
        res[k] = [{"from": at(x.get("from")), "to": at(x.get("to"))} for x in res.get(k) or [] if isinstance(x, dict)]
        res[k] = [x for x in res[k] if x["from"] and x["to"]]
    res["topics"].sort(key=lambda x: x["at"] or "~")
    people = {}  # the model sometimes lists one label twice
    for x in res.get("people") or []:
        if isinstance(x, dict) and x.get("who"):
            p = people.setdefault(x["who"], {"who": x["who"], "points": [], "points_at": []})
            for y in x.get("points") or []:
                text, hms = (y.get("text"), y.get("at")) if isinstance(y, dict) else (y, None)
                if isinstance(text, str) and text:
                    p["points"].append(text)
                    p["points_at"].append(at(hms, x["who"]))
    res["people"] = list(people.values())
    return res


async def summarize(conv: dict, rows: list[dict], known_tags: list[str] = ()) -> dict:
    base = {"conversation": conv["id"], "prompt_version": PROMPT_VERSION,
            "created_at": dt.datetime.now().isoformat(timespec="seconds"), "error": None}
    if conv.get("speech_s", 0) < MIN_SPEECH_S or not rows:
        first = next((r["text"] for r in rows if r.get("text")), "")
        return {**base, "status": "done", "model": None, "title": first[:20], "summary": "", **{k: [] for k in LISTS}}
    return result(conv, rows, *await chat(transcript(rows), list(known_tags), conv["start"]))


def typed(res: dict) -> tuple[dict, list[str]]:
    """The reply with each field of the type the format gives it (title and summary text, tags texts, the other lists
    objects; a todo, decision or AI request with a text, its due, owner, to and quote text or null), and the fields that
    held something else: that part is dropped. A tag that is not a text broke tag_counts, and with it every later summary
    and the tags list; a todo's list as its text broke the due todos."""
    out, bad = dict(res), []
    for k in ("title", "summary"):
        if not isinstance(out.get(k) or "", str):
            out[k], bad = "", [*bad, k]
    for k in LISTS:
        v = out.get(k) or []
        out[k] = [x for x in v if isinstance(x, str if k == "tags" else dict)] if isinstance(v, list) else []
        if k in ("todos", "decisions", "ai_requests"):  # each one a text, its other words text or null (todo_ids, due_todos)
            out[k] = [{**x, **{f: None for f in ("due", "owner", "to", "quote") if x.get(f) is not None and not isinstance(x[f], str)}}
                      for x in out[k] if isinstance(x.get("text"), str)]
        if out[k] != v:
            bad.append(k)
    return out, bad


def result(conv: dict, rows: list[dict], res: dict, raw: dict) -> dict:
    """A model's reply in the SYSTEM format as the stored summary; a reply that is not an object raises (the worker
    tries again), a field of another type is dropped (typed)."""
    if not isinstance(res, dict):
        raise ValueError(f"summary reply is a {type(res).__name__}, not an object")
    res, bad = typed(res)
    if bad:
        log.warning("summary %s: dropped the reply's %s (not in the format)", conv["id"], ", ".join(bad))
    res = anchor(res, rows)
    return {"conversation": conv["id"], "prompt_version": PROMPT_VERSION, "created_at": dt.datetime.now().isoformat(timespec="seconds"),
            "error": None, "status": "done", "model": raw.get("model"), "usage": raw.get("usage"),
            "media_share": media_share(rows, res["media_spans"]),
            **{k: res.get(k) or ("" if k in ("title", "summary") else []) for k in ("title", "summary", *LISTS)}}


LISTS = ("people", "tags", "topics", "todos", "decisions", "ai_requests", "ai_spans", "media_spans")


def _speech(r: dict) -> float:
    return (dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds()


def in_media(r: dict, spans: list[dict]) -> bool:
    """Spans name rows by their start, both ends included."""
    return any(x["from"] <= r["start"] <= x["to"] for x in spans)


def media_share(rows: list[dict], spans: list[dict]) -> float:
    """Of the speech, the part played aloud by media; the page greys a conversation that is mostly media."""
    total = sum(map(_speech, rows))
    return round(sum(_speech(r) for r in rows if in_media(r, spans)) / total, 2) if total and spans else 0.0


def media_labels(rows: list[dict], spans: list[dict]) -> set[str]:
    """Labels that speak mostly inside media: their voice is the media's, not a person's in the room."""
    by = {}
    for r in rows:
        if r.get("speaker"):
            x = by.setdefault(r["speaker"], [0.0, 0.0])
            x[0] += _speech(r)
            x[1] += _speech(r) if in_media(r, spans) else 0
    return {k for k, (t, m) in by.items() if t and m / t >= 0.5}


if __name__ == "__main__":
    import tempfile
    from pathlib import Path

    import anyio
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    rows = [dict(start="2026-09-30T10:00:01.200", text="我们先定一下", speaker="我"), dict(start="2026-09-30T10:02:00.000", text="好", speaker="S1")]
    r = anchor({"topics": [{"title": "b", "at": "10:02:00"}, {"title": "a", "at": "10:00:01"}], "todos": [{"text": "x", "at": "9:99"}]}, rows)
    assert [t["title"] for t in r["topics"]] == ["a", "b"] and r["topics"][0]["at"] == rows[0]["start"]
    assert r["todos"][0]["at"] is None and r["todos"][0]["due_date"] is None and r["decisions"] == []
    todos = anchor({"todos": [{"due": "明天", "due_date": "2026-10-01"}, {"due": "下周", "due_date": "下周"}, {"due": None, "due_date": "2026-10-01"},
                              {"due": "周五", "due_date": "2026-10-1"}, {"due": "月底", "due_date": "2026-09-31"}]}, rows)["todos"]
    assert [t["due_date"] for t in todos] == ["2026-10-01", None, None, None, None], todos
    assert instructions(["工作"], "2026-10-04T23:50:10.000").endswith("\n已有标签：工作\n这段对话开始于 2026-10-04（周日）23:50。\n" + ZH)
    assert anchor({"people": [{"who": "S1", "points": ["a"]}, {"who": "S1", "points": ["b"]}, "x"]}, rows)["people"] == [{"who": "S1", "points": ["a", "b"], "points_at": [None, None]}]
    rows = [dict(rows[0], end="2026-09-30T10:00:09.000"), dict(rows[1], end="2026-09-30T10:02:09.000"), dict(start="2026-09-30T10:03:00.000", end="2026-09-30T10:03:30.000", text="欢迎收看", speaker="S2")]
    spans = anchor({"media_spans": [{"from": "10:03:00", "to": "10:03:00"}, {"from": "10:00:01", "to": "bad"}, "x"]}, rows)["media_spans"]
    assert spans == [{"from": rows[2]["start"], "to": rows[2]["start"]}], spans
    assert media_labels(rows, spans) == {"S2"} and media_share(rows, spans) == round(30 / (7.8 + 9 + 30), 2), media_share(rows, spans)
    assert media_share(rows, []) == 0.0
    # a v9 reply: each point with its time, resolved like the others; a duplicate label keeps the two lists side by side
    reply = {"title": "定存储", "summary": "s", "people": [
        {"who": "我", "points": [{"text": "先定一下", "at": "10:00:01"}, {"text": "没对上", "at": "10:00:02"}, {"at": "10:02:00"}]},
        {"who": "S1", "points": [{"text": "同意", "at": "10:02:00"}]}, {"who": "我", "points": ["旧格式", {"text": "再说", "at": None}]}]}
    s = result({"id": "c1"}, rows, reply, {"model": "m"})
    assert s["prompt_version"] == 12 and s["people"] == [
        {"who": "我", "points": ["先定一下", "没对上", "旧格式", "再说"], "points_at": [rows[0]["start"], None, None, None]},
        {"who": "S1", "points": ["同意"], "points_at": [rows[1]["start"]]}], s["people"]
    # an odd reply: what is not in the format is dropped; one that is not an object raises (the worker tries again)
    s = result({"id": "c1"}, rows, {"title": ["t"], "tags": [{"name": "工作"}, "工作"], "todos": ["买菜", {"text": "a"}], "topics": "t"}, {})
    assert s["title"] == "" and s["tags"] == ["工作"] and [x["text"] for x in s["todos"]] == ["a"] and s["topics"] == [], s
    s = result({"id": "c1"}, rows, {"todos": [{"text": ["买菜"]}, {"text": "b", "due": 1, "owner": "我", "to": None}], "decisions": [{"quote": "q"}]}, {})
    assert s["todos"] == [{"text": "b", "due": None, "owner": "我", "to": None, "at": None, "due_date": None}] and s["decisions"] == [], s
    try:
        result({"id": "c1"}, rows, ["not an object"], {})
        raise AssertionError("a reply that is not an object was taken")
    except ValueError:
        pass
    s = anyio.run(summarize, {"id": "c1", "speech_s": 12}, rows)
    assert s["title"] == "我们先定一下" and s["model"] is None, s
    print("ok")
