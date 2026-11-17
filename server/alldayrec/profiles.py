"""A person's profile from what is already known: the conversations with them (each summary and what they said there),
their todos and mine to them, and what others said about them anywhere (rows holding their name or an alias, with
the rows around). One chat call over numbered evidence; every line cites its evidence, and a line citing none that
was given is dropped. With the tenant's persona_v2 (evidence_v2): the person's own sentences instead of summary
points, from conversations that can tell who said them, and a profile void once those conversations change.
"""
import datetime as dt
import hashlib
import json

from alldayrec import summary

SYSTEM = """你在为录音者（「我」）整理一个人的档案。输入是关于这个人的证据，每条以 [E编号] 开头：
- 对话：一段有他参加的对话，带标题、摘要和他在那段里说的要点
- 待办：他要做的事，或我答应他的事
- 提及：某段对话里有人说到他的名字或别名（他可能不在场），带前后几句原文，>> 标出提到的那句
转写有错别字和同音误识别，按意图理解。名字被说到不一定是在说他（同名、同音的别的词），拿不准就不用。
只输出一个 JSON 对象：
{"who": "一两句话：他是谁、和我什么关系；没有依据就写空字符串",
 "facts": [{"text": "关于他的事实：工作、学校、住处、家庭、爱好、经历、习惯、观点", "src": ["E1"]}],
 "recent": [{"text": "最近和他聊了什么、他在忙什么，新的在前", "src": ["E2"]}],
 "mentions": [{"text": "别人提到的关于他的事：谁说的、说了什么", "src": ["E3"]}],
 "open": [{"text": "还没了结的事：他答应的、我答应他的、约好的", "src": ["E4"]}]}
指这个人时写名字或 TA，不用他、她。每条都写依据的证据编号 src；不写证据里没有的内容。每类不超过 8 条，没有就给空数组。"""
PROMPT_VERSION = 1
LISTS = ("facts", "recent", "mentions", "open")


def evidence(name: str, convs: list[dict], todos: list[dict], mentions: list[dict]) -> tuple[str, dict[str, dict]]:
    """The model's input and what each E-number points at ({conv, at, start, title}). convs: the person's conversations
    newest first, as /api/person lists them ({id, start, title, summary, points}); todos: theirs and mine to them (with
    their conversation's start); mentions: {conv, title, at, rows (the mention with the rows around)}."""
    lines, ev = [f"这个人：{name}"], {}

    def add(text: str, conv: str, at: str | None, start: str, title: str | None):
        e = f"E{len(ev) + 1}"
        ev[e] = {"conv": conv, "at": at, "start": start, "title": title}
        lines.append(f"[{e}] {text}")
    for c in convs:
        add(f"对话 {c['start'][:16].replace('T', ' ')}《{c.get('title') or ''}》：{(c.get('summary') or '')[:300]}"
            + (f" 他说的：{'；'.join(c['points'])}" if c.get("points") else ""), c["id"], None, c["start"], c.get("title"))
    for t in todos:
        who = "他要做" if t.get("owner") == name else "我答应他"
        add(f"待办 {t['start'][:10]}：{who}：{t['text']}" + (f"（{t['due']}）" if t.get("due") else ""),
            t["conversation"], t.get("at"), t["start"], t.get("title"))
    for m in mentions:
        rows = "\n".join(("  >> " if r["start"] == m["at"] else "     ") + f"{r.get('speaker') or 'S?'}: {r['text']}" for r in m["rows"])
        add(f"提及 {m['at'][:16].replace('T', ' ')}《{m.get('title') or ''}》：\n{rows}", m["conv"], m["at"], m["at"], m.get("title"))
    return "\n".join(lines), ev


def check(res: dict, ev: dict[str, dict]) -> dict:
    """Each line with the evidence it cites (unknown numbers dropped; a line left with none dropped)."""
    out = {"who": str(res.get("who") or "")}
    for k in LISTS:
        out[k] = []
        for x in res.get(k) or []:
            if not isinstance(x, dict) or not x.get("text"):
                continue
            src = [{"id": e, **ev[e]} for e in dict.fromkeys(x.get("src") or []) if isinstance(e, str) and e in ev]
            if src:
                out[k].append({"text": str(x["text"]), "src": src})
    return out


async def profile(name: str, convs: list[dict], todos: list[dict], mentions: list[dict]) -> dict:
    text, ev = evidence(name, convs, todos, mentions)
    res, raw = await summary.complete([{"role": "system", "content": SYSTEM + "\n" + summary.lang_line()}, {"role": "user", "content": text}],
                                       kind="profile")
    return {"name": name, "prompt_version": PROMPT_VERSION, "at": dt.datetime.now().isoformat(timespec="seconds"),
            "model": raw.get("model"), "usage": raw.get("usage"), "upto": convs[0]["start"] if convs else None,
            "inputs": {"conversations": len(convs), "todos": len(todos), "mentions": len(mentions)}, **check(res, ev)}


# v2 (tenants.json "persona_v2"): the person's own sentences as evidence, not summary points, which carry a label's
# mistakes along unseen (a few seconds of someone else, labelled as them, become a fact about them). A conversation's
# sentences count only once its summary has looked for AI and media in it (ai_spans, media_spans): a summary not done,
# or done without a model (under 60 s of speech), says nothing either way, so it gives no evidence.
SYSTEM_V2 = """你在为录音者（「我」）整理一个人（下面叫 TA）的档案。输入是原句证据，每条以 [E编号] 开头：
- TA 原句：TA 自己在某段对话里说的一句话（说话人是声纹认的，偶尔认错：和其他原句对不上的单句不要用）
- 我 原句：我对 TA 说的一句话（摘要认为是和 TA 有关的待办）
- 提及：别人说到 TA 的名字或别名（TA 可能不在场），带前后几句原文，>> 标出提到的那句
转写有错别字和同音误识别，按意图理解。名字被说到不一定是在说 TA（同名、同音的别的词），拿不准就不用。
只输出一个 JSON 对象：
{"who": "一两句话：TA 是谁、和我什么关系；没有依据就写空字符串", "who_src": ["E1"],
 "facts": [{"text": "TA 自己说的关于 TA 的事：工作、学校、住处、经历、观点", "kind": "fact", "src": ["E1"]}],
 "recent": [{"text": "TA 自己说的最近在忙什么、要做什么，新的在前", "src": ["E2"]}],
 "mentions": [{"text": "别人说的关于 TA 的事：谁说的、说了什么", "src": ["E3"]}],
 "open": [{"text": "还没了结的事：TA 答应的、我答应 TA 的、约好的", "src": ["E4"]}]}
规则：
- who、facts 和 recent 只能根据 TA 原句写，src 里只放 TA 原句。别人（包括我）说 TA 的话只进 mentions，并写明是谁说的；不得把别人的转述写成 TA 本人的经历或观点。
- facts 里性格、说话风格、作息习惯这类写 "kind": "trait"，其余写 "fact"。证据不到 5 天时不写 trait。
- 不推断、不写敏感属性：健康和疾病、性取向、宗教、政治立场、民族、犯罪记录、收入存款负债等财务细节。TA 自己说到了也不写。
指 TA 时写名字或 TA，不用他、她。每条都写依据的证据编号 src；不写证据里没有的内容。每类不超过 8 条，没有就给空数组。"""
PROMPT_VERSION_V2 = 2
QUOTES = 8          # a conversation's own sentences given, its longest (and those holding a todo)
MIN_ROW_S = 1.0     # shorter rows are not evidence: diarist.relabel joins them to the nearest named voice at only 0.35
MIN_SPEECH_S = 15.0  # a label with less (its rows of MIN_ROW_S on) in a conversation is a fragment, not evidence
CROWD = 10          # labels in a conversation that make it a crowd (a shop, a restaurant): a named label holding
CROWD_SHARE = 0.7   # this much of its speech has taken in the voices around it
SHORT_SHARE = 0.45  # a named label with this many of its rows short (of 5 on) was grown by the short rows (0.35 join): ~23 %
CONVS = 40          # conversations giving evidence, the newest
TRAIT_DAYS = 5      # fewer days of evidence: no traits (1-4 days give who and what's new, not character)


def _s(r: dict) -> float:
    return (dt.datetime.fromisoformat(r["end"]) - dt.datetime.fromisoformat(r["start"])).total_seconds()


def looked(s: dict | None) -> bool:
    """The summary looked for AI and media in its conversation."""
    return bool(s) and s.get("status") == "done" and bool(s.get("model")) and "ai_spans" in s and "media_spans" in s


def sha(rows: list[dict], s: dict | None) -> str:
    """What a conversation's evidence was made of: every row's words and label, its spans. A rename, merge, split or
    new transcript changes it, and with it the profile made from it is void."""
    x = [[r["start"], r["end"], r.get("speaker"), r.get("text"), r.get("music"), r.get("dictation")] for r in rows]
    x.append([(s or {}).get(k) for k in ("status", "model", "ai_spans", "media_spans")])
    return hashlib.sha256(json.dumps(x, ensure_ascii=False).encode()).hexdigest()[:16]


def heard(r: dict, s: dict) -> bool:
    """A row a person said to people: not music, not dictated, not said to an AI (ai_spans: 口述), not played aloud."""
    spans = (s.get("ai_spans") or []) + (s.get("media_spans") or [])
    return not r.get("music") and not r.get("dictation") and not summary.in_media(r, spans)


def segment(name: str, rows: list[dict], s: dict) -> tuple[list[dict], str | None]:
    """The name's rows of one conversation that can be evidence, or why none can: fragment, crowd, short."""
    ok = [r for r in rows if heard(r, s) and r.get("text") and _s(r) >= MIN_ROW_S]
    own = [r for r in ok if r.get("speaker") == name]
    if sum(map(_s, own)) < MIN_SPEECH_S:
        return [], "fragment"
    if name != "我":  # the wearer is near the mic: theirs is most of the speech, rightly
        labels = {r.get("speaker") for r in rows if r.get("speaker") and heard(r, s)}  # not a TV's: their share is of ok
        if len(labels) >= CROWD and sum(map(_s, own)) / sum(map(_s, ok)) >= CROWD_SHARE:
            return [], "crowd"
        mine = [r for r in rows if r.get("speaker") == name]
        if len(mine) >= 5 and sum(_s(r) < MIN_ROW_S for r in mine) / len(mine) >= SHORT_SHARE:
            return [], "short"
    return own, None


def evidence_v2(name: str, convs: list[dict], mentions: list[dict], rows=lambda c: c["rows"]) -> tuple[str, dict[str, dict], dict]:
    """The model's input, what each E-number points at ({conv, at, start, title, kind: self, me or mention}), and what
    went in (used: each conversation's sha; conversations, days, skipped). convs: the person's conversations newest
    first with their stored summary (db.conversations), the first CONVS that give evidence taken; rows(c): its rows.
    mentions: v1's, each with its conversation's summary and rows (summary, conv_rows)."""
    lines, ev, used, days, skipped, n = [f"这个人：{name}"], {}, {}, set(), {}, 0

    def add(kind: str, text: str, conv: str, at: str | None, start: str, title: str | None):
        e = f"E{len(ev) + 1}"
        ev[e] = {"conv": conv, "at": at, "start": start, "title": title, "kind": kind}
        lines.append(f"[{e}] {text}")
    for c in convs:
        if n == CONVS:
            break
        s = c.get("summary") or {}
        if not looked(s):
            skipped["unsummarized"] = skipped.get("unsummarized", 0) + 1
            continue
        rs = rows(c)
        own, why = segment(name, rs, s)
        if why:
            skipped[why] = skipped.get(why, 0) + 1
            continue
        at = {t.get("at") for t in s.get("todos") or [] if t.get("at") and not t.get("unsupported")
              and (t.get("owner") == name or t.get("owner") == "我" and t.get("to") == name)}
        keep = sorted(own, key=lambda r: (r["start"] not in at, -_s(r)))[:QUOTES]
        mine = [r for r in rs if r["start"] in at and r.get("speaker") == "我" != name and r.get("text") and heard(r, s)]
        title = s.get("title")
        for r in sorted(keep + mine, key=lambda r: r["start"]):
            who = "TA" if r.get("speaker") == name else "我"
            add("self" if who == "TA" else "me", f"{who} 原句 {r['start'][:16].replace('T', ' ')}《{title or ''}》：{r['text'][:150]}",
                c["id"], r["start"], c["start"], title)
        used[c["id"]] = sha(rs, s)
        days.add(c["start"][:10])
        n += 1
    for m in mentions:
        s = m.get("summary") or {}
        if not looked(s):
            continue
        theirs = {r["start"] for r in segment(name, m["conv_rows"], s)[0]}  # none where that conversation is skipped
        here = {r["start"] for r in m["conv_rows"]}  # its spans tell nothing of the next conversation's rows
        rs = [r for r in m["rows"] if r["start"] in here and heard(r, s) and _s(r) >= MIN_ROW_S
              and (r.get("speaker") != name or r["start"] in theirs)]
        if m["at"] not in {r["start"] for r in rs}:
            continue
        near = "\n".join(("  >> " if r["start"] == m["at"] else "     ") + f"{r.get('speaker') or 'S?'}: {r['text']}" for r in rs)
        add("mention", f"提及 {m['at'][:16].replace('T', ' ')}《{m.get('title') or ''}》：\n{near}", m["conv"], m["at"], m["at"], m.get("title"))
        used[m["conv"]] = sha(m["conv_rows"], s)
    lines.insert(1, f"证据覆盖 {len(days)} 天")
    return "\n".join(lines), ev, {"used": [{"conv": k, "sha": v} for k, v in used.items()], "conversations": n,
                                   "days": len(days), "skipped": skipped}


def check_v2(res: dict, ev: dict[str, dict], days: int) -> dict:
    """check()'s, and who says it from what is cited, not from the model: by self only when every sentence a line cites
    is the person's own; citing anyone else's at all (a mention, mine) makes it others'. who, facts and recent are
    theirs only: one of others' goes to mentions (hearsay is not their view, nor a sentence of theirs cited beside it);
    mentions are others' only, open anyone's but hearsay alone; a trait goes with fewer than TRAIT_DAYS days."""
    out = check({**res, "who": ""}, ev)
    who = check({"facts": [{"text": res.get("who"), "src": res.get("who_src")}]}, ev)["facts"]  # [] when it cites none given
    by = lambda x: "self" if all(s["kind"] == "self" for s in x["src"]) else "others"
    own = bool(who) and by(who[0]) == "self"
    out.update(who=who[0]["text"] if own else "", who_src=who[0]["src"] if own else [])
    moved = [] if own else who
    kinds = {k: {str(x.get("text")): x.get("kind") for x in res.get(k) or [] if isinstance(x, dict)} for k in LISTS}
    for k in LISTS:
        lines = []
        for x in out[k] + (moved if k == "mentions" else []):
            x["by"] = by(x)
            if k == "facts" and kinds[k].get(x["text"]) == "trait":
                if days < TRAIT_DAYS:
                    continue
                x["kind"] = "trait"
            if k in ("facts", "recent") and x["by"] != "self":
                moved.append(x)
                continue
            if k == "mentions":
                x["by"] = "others"
                if all(s["kind"] == "self" for s in x["src"]):
                    continue
            if k == "open" and all(s["kind"] == "mention" for s in x["src"]):
                continue
            lines.append(x)
        out[k] = lines
    return out


def key(text: str, ev: dict[str, dict]) -> str:
    """What the model is asked: the same again gets no call (an automatic refresh after a conversation that gave nothing)."""
    return hashlib.sha256(json.dumps([SYSTEM_V2, summary.lang_line(), text, ev], ensure_ascii=False).encode()).hexdigest()[:16]


async def profile_v2(name: str, text: str, ev: dict[str, dict], made: dict, upto: str | None) -> dict:
    """evidence_v2's (ev not empty) to the model, its reply checked; a reply that is not an object raises (the stored
    profile stays)."""
    res, raw = await summary.complete([{"role": "system", "content": SYSTEM_V2 + "\n" + summary.lang_line()}, {"role": "user", "content": text}],
                                       kind="profile")
    if not isinstance(res, dict):
        raise ValueError(f"profile reply is a {type(res).__name__}, not an object")
    n = lambda kind: sum(e["kind"] == kind for e in ev.values())
    return {"name": name, "prompt_version": PROMPT_VERSION_V2, "at": dt.datetime.now().isoformat(timespec="seconds"),
            "model": raw.get("model"), "usage": raw.get("usage"), "upto": upto,
            "inputs": {"conversations": made["conversations"], "sentences": n("self"), "todos": n("me"), "mentions": n("mention")},
            "used": made["used"], "days": made["days"], "skipped": made["skipped"], "input": key(text, ev),
            **check_v2(res, ev, made["days"])}


def fresh(p: dict, now: dict[str, str | None]) -> bool:
    """A v2 profile still stands on what it was made from (now: each used conversation's sha today, None: gone)."""
    return p.get("prompt_version") == PROMPT_VERSION_V2 and all(now.get(x["conv"]) == x["sha"] for x in p.get("used") or [])
