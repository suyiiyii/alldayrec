"""What the people in a conversation call each other (names, nicknames, kin and job terms), each tied to the label it
lands on: suggestions for naming a label, never applied by themselves. One small chat call per closed conversation,
apart from the summary's: asked inside the summary call the model gave 3 names in 20 runs of 10 conversations, on
its own 48 (10-05), and history can be asked without redoing summaries. A name is kept only when the rows bear it out:
the model often puts a call on the caller instead of on the one who answers.
"""
import datetime as dt

from alldayrec import summary
from alldayrec.conversations import IGNORE

SYSTEM = """你在看一段现场录音的转写，每行「[时间] 说话人: 文本」。说话人标签来自声纹聚类：「我」是录音者本人，S1、S2… 是其他人，同一个人可能被拆成多个标签，S? 是太短没认出来的句子。转写有错别字和同音误识别，按意图理解。
找出对话里的标签被怎么称呼：名字、昵称、亲属或职务称呼（老王、小李、王哥、张老师、妈）。只收能确定落到某个标签上的：
- addressed：有人叫某人，被叫的人紧接着回应。who 填回应的那个标签，不是喊人的那个
- self：自我介绍（我是X、我叫X），who 填说这句话的标签
- referred：别人当面指着他说（这是X、X刚才说的），who 填被指的标签
不收：谈论不在场的人、媒体里的人名、对 AI 说的话里的名字；「我」被怎么叫不用收。
只输出一个 JSON 对象：{"names": [{"who": "S1", "name": "称呼，照原文", "kind": "addressed|self|referred", "quote": "依据原句，照抄", "at": "原句那一行的时间 HH:MM:SS"}]}
没有就给空数组，不编造。"""
PROMPT_VERSION = 1
# pronouns, and what strangers are called (a shop's 老板, a driver's 师傅): not who someone is
NOT_NAMES = {"我", "你", "他", "她", "您", "大家", "你们", "他们", "老板", "老板娘", "师傅", "帅哥", "美女", "靓仔", "小伙子", "小朋友",
             "服务员", "同学", "先生", "女士", "亲", "亲爱的", "宝贝", "宝宝", "领导", "朋友们"}


async def ask(rows: list[dict]) -> tuple[dict, dict]:
    return await summary.complete([{"role": "system", "content": SYSTEM}, {"role": "user", "content": summary.transcript(rows)}], kind="hints")


def check(res: dict, rows: list[dict], skip: set[str] = frozenset()) -> list[dict]:
    """The model's names that the rows bear out: the quoted row (at its time, holding the name) said by someone else
    and the next row with a known speaker the named label's (addressed: one who answers; the caller talking on is no
    answer), said by the label itself (self), or said by someone else with the label speaking within 5 rows
    (referred). skip: labels not to name (media voices)."""
    rows = [r for r in rows if r.get("text")]
    labels = {r.get("speaker") for r in rows} - {"我", None} - IGNORE - set(skip)
    out = []
    for h in res.get("names") or []:
        if not isinstance(h, dict):
            continue
        who, name, kind = h.get("who"), str(h.get("name") or "").strip(), h.get("kind")
        if who not in labels or not name or len(name) > 12 or name in NOT_NAMES:
            continue
        at = [i for i, r in enumerate(rows) if r["start"][11:19] == h.get("at") and name in r["text"]]
        if not at:
            continue
        i = at[0]
        by = rows[i].get("speaker")
        answer = next((r.get("speaker") for r in rows[i + 1:i + 4] if r.get("speaker") != "S?"), None)
        if kind == "addressed" and by != who and answer == who or kind == "self" and by == who or \
                kind == "referred" and by != who and who in {r.get("speaker") for r in rows[max(0, i - 5):i + 6]}:
            x = {"who": who, "name": name, "kind": kind, "at": rows[i]["start"], "by": by, "quote": rows[i]["text"][:80]}
            if x not in out:
                out.append(x)
    return out


async def hints(c: dict, rows: list[dict], skip: set[str]) -> dict:
    """The stored hints of one conversation."""
    res, raw = await ask(rows)
    names = check(res, rows, skip)
    return {"conv": c["id"], "prompt_version": PROMPT_VERSION, "at": dt.datetime.now().isoformat(timespec="seconds"),
            "model": raw.get("model"), "usage": raw.get("usage"), "asked": len(res.get("names") or []), "names": names}


def shown(h: dict | None, s: dict | None) -> list[dict]:
    """The names the pages offer of one conversation's hints (h) with its summary (s): none on a label that is no
    one (外放媒体 of old history, asked before check skipped them) nor said inside media played aloud."""
    spans = (s or {}).get("media_spans") or []
    return [x for x in (h or {}).get("names", []) if x["who"] not in IGNORE and not summary.in_media({"start": x["at"]}, spans)]


def canon(name: str, aliases: dict[str, str]) -> str:
    return aliases.get(name, name)
