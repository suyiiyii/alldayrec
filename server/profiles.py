"""A person's profile from what is already known: the conversations with them (each summary and what they said there),
their todos and mine to them, and what others said about them anywhere (rows holding their name or an alias, with
the rows around). One chat call over numbered evidence; every line cites its evidence, and a line citing none that
was given is dropped.
"""
import datetime as dt

import summary

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
