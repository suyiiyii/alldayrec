"""Person profiles: the evidence numbers the model sees point back at conversations, todos and mentions; a line citing
nothing given is dropped; mentions find the name and its aliases said by others, a few per conversation."""
import datetime as dt
import json
import tempfile
from pathlib import Path

import anyio
import pytest
from fastapi import HTTPException

from alldayrec import db
from alldayrec import profiles
from alldayrec import summary
from alldayrec.routes_speakers import alias_set, make_profile, mentions, person, profile_inputs, rename
from alldayrec import tenant

T = "2026-10-05T10:00:"


def test_profile(monkeypatch):
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    rows = [dict(id="1", start=T + "01.000", end=T + "02.000", speaker="张三", text="我下周去上海出差", source="mic"),
            dict(id="2", start=T + "03.000", end=T + "04.000", speaker="S1", text="老张上次说要换工作", source="mic"),
            dict(id="3", start=T + "05.000", end=T + "06.000", speaker="我", text="张三人呢", source="mic"),
            dict(id="4", start=T + "07.000", end=T + "08.000", speaker="张三", text="张三在这", source="mic")]  # his own: no mention
    rows += [dict(id=f"x{i}", start=f"2026-10-05T11:00:{i:02d}.000", end=f"2026-10-05T11:00:{i:02d}.500", speaker="S2",
                  text="张三张三", source="mic") for i in range(5)]  # one talk saying it over and over
    c1 = {"id": "c1", "start": T + "00.000", "end": T + "09.000", "participants": ["张三", "S1", "我"], "sentences": 4,
          "speech_s": 70.0, "device": "mic"}
    c2 = {"id": "c2", "start": "2026-10-05T11:00:00.000", "end": "2026-10-05T11:00:05.000", "participants": ["S2"],
          "sentences": 5, "speech_s": 5.0, "device": "mic"}
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, c1)
        db.insert_conversation(t, c2)
        db.put_summary({"conversation": "c1", "status": "done", "title": "出差", "summary": "张三说要去上海",
                        "people": [{"who": "张三", "points": ["下周去上海出差"]}],
                        "todos": [{"owner": "我", "to": "张三", "text": "订票", "at": T + "05.000"}]}, t)
    anyio.run(alias_set, {"alias": "老张", "name": "张三"})

    ms = mentions("张三")
    assert [(m["conv"], m["at"][11:19]) for m in ms] == [("c2", "11:00:04"), ("c2", "11:00:03"), ("c2", "11:00:02"),
                                                         ("c1", "10:00:05"), ("c1", "10:00:03")], "3 per talk; his own row is none"
    assert [r["id"] for r in ms[-1]["rows"]] == ["1", "2", "3", "4"]

    asked = []

    async def complete(messages, kind=None):
        asked.append(messages[1]["content"])
        return {"who": "同事", "facts": [{"text": "要去上海出差", "src": ["E1", "E99"]}, {"text": "编的", "src": ["E99"]}],
                "recent": [], "mentions": [{"text": "S1 说他要换工作", "src": ["E7"]}], "open": [{"text": "我要订票", "src": ["E2"]}]}, {"model": "m"}
    monkeypatch.setattr(summary, "complete", complete)
    pf = anyio.run(make_profile, "张三")
    text = asked[0]
    assert "[E1] 对话" in text and "下周去上海出差" in text and "[E2] 待办 2026-10-05：我答应他：订票" in text
    assert "  >> S1: 老张上次说要换工作" in text, text
    assert [x["text"] for x in pf["facts"]] == ["要去上海出差"] and [s["id"] for s in pf["facts"][0]["src"]] == ["E1"]
    assert pf["mentions"][0]["src"][0]["conv"] == "c1" and pf["open"][0]["src"][0]["at"] == T + "05.000"
    assert pf["upto"] == c1["start"] and pf["inputs"] == {"conversations": 1, "todos": 1, "mentions": 5}
    p = anyio.run(person, "张三")
    assert p["profile"]["who"] == "同事" and p["profile_newer"] == 0
    assert json.loads(db.conn().execute("SELECT body FROM profiles").fetchone()[0])["name"] == "张三"


def test_check_drops_unsourced():
    ev = {"E1": {"conv": "c1", "at": None, "start": T, "title": "x"}}
    got = profiles.check({"who": None, "facts": [{"text": "a", "src": ["E1", "E1"]}, {"text": "b"}, "x"], "open": None}, ev)
    assert got == {"who": "", "facts": [{"text": "a", "src": [{"id": "E1", **ev["E1"]}]}], "recent": [], "mentions": [], "open": []}


# v2 (persona_v2): the person's own sentences; made-up people and talk only
def row(conv: str, s: float, dur: float, who: str, text: str, **kw) -> dict:
    t = dt.datetime.fromisoformat(conv) + dt.timedelta(seconds=s)
    return dict(id=f"{conv[11:19]}-{s}", start=t.isoformat(timespec="milliseconds"),
                end=(t + dt.timedelta(seconds=dur)).isoformat(timespec="milliseconds"), speaker=who, text=text, source="mic", **kw)


def span(rs: list[dict]) -> dict:
    return {"from": rs[0]["start"], "to": rs[-1]["start"]}


def v2_store():
    """c1 a talk with 张三 (one line of theirs said to an AI, one under a video); c2 their fragment; c3 a crowd where
    张三's label took most of twelve; c4 a one-on-one where 张三 talks most; c5 李四 about 张三, who is not there; c6 its
    summary not done. Each a conversation of its own day."""
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    tenant.T().persona_v2 = True
    day = lambda d: f"2026-10-0{d}T10:00:00.000"
    c1 = [row(day(1), 0, 7, "张三", "我下周去上海出差"), row(day(1), 7, 3, "S1", "去几天"), row(day(1), 11, 9, "张三", "三天，周五回来，回来再说项目的事"),
          row(day(1), 20, 4, "张三", "你好助手，明早七点叫我"), row(day(1), 25, 6, "S2", "张三是本期节目的嘉宾"),
          row(day(1), 31, 5, "张三", "欢迎收看本期节目"), row(day(1), 37, 0.5, "张三", "嗯"), row(day(1), 38, 3, "我", "那我周六把合同发你")]
    c2 = [row(day(2), 0, 3, "张三", "对对"), row(day(2), 4, 3, "张三", "我去一下"), row(day(2), 8, 20, "我", "好的我先把这个弄完再说")]
    c3 = [row(day(3), i * 10, 9, "张三", f"第{i}号柜子里放的是螺丝") for i in range(8)] + \
         [row(day(3), 100 + i * 3, 2, f"S{i}", "看看") for i in range(11)]
    c4 = [row(day(4), i * 10, 9, "张三", f"我每天早上跑步第{i}圈") for i in range(6)] + [row(day(4), 70, 5, "我", "那挺好的")]
    c5 = [row(day(5), 0, 5, "李四", "张三下个月要搬家"), row(day(5), 6, 5, "我", "真的吗")]
    c6 = [row(day(6), 0, 30, "张三", "我其实很喜欢爬山")]
    talks = {"c1": (c1, {"ai_spans": [span(c1[3:4])], "media_spans": [span(c1[4:6])],
                         "todos": [{"owner": "我", "to": "张三", "text": "发合同", "at": c1[7]["start"]}]}),
             "c2": (c2, {}), "c3": (c3, {}), "c4": (c4, {}), "c5": (c5, {}), "c6": (c6, {"status": "pending"})}
    with db.tx() as t:
        for cid, (rs, s) in talks.items():
            db.insert_sentences(t, rs)
            db.insert_conversation(t, {"id": cid, "start": rs[0]["start"], "end": rs[-1]["end"], "device": "mic",
                                       "participants": sorted({r["speaker"] for r in rs}), "sentences": len(rs), "speech_s": 90.0})
            db.put_summary({"conversation": cid, "status": "done", "model": "m", "title": cid, "summary": "", "people": [],
                            "ai_spans": [], "media_spans": [], "todos": [], **s}, t)


def test_v2_evidence_is_own_reliable_sentences():
    v2_store()
    text, ev, made, upto = profile_inputs("张三")
    said = [e for e in ev.values() if e["kind"] == "self"]
    assert "我下周去上海出差" in text and "三天，周五回来" in text and "我每天早上跑步第0圈" in text
    assert "你好助手" not in text and "欢迎收看本期节目" not in text, "said to an AI, played aloud: no evidence"
    assert "张三是本期节目的嘉宾" not in text, "a video saying the name is no mention"
    assert "对对" not in text and "嗯" not in text, "a fragment, a row under a second"
    assert "螺丝" not in text, "a named label that took a crowd in"
    assert "爬山" not in text, "a summary not done has not looked for media"
    assert "[E" in text and "我 原句" in text and "那我周六把合同发你" in text, "my promise to them, as said"
    assert "提及" in text and ">> 李四: 张三下个月要搬家" in text
    assert {e["conv"] for e in said} == {"c1", "c4"} and made["days"] == 2 and made["conversations"] == 2
    assert made["skipped"] == {"fragment": 1, "crowd": 1, "unsummarized": 1}
    assert {x["conv"] for x in made["used"]} == {"c1", "c4", "c5"} and upto.startswith("2026-10-06")


def test_v2_hearsay_is_never_their_own():
    ev = {"E1": {"conv": "c1", "at": "a", "start": "a", "title": None, "kind": "self"},
          "E2": {"conv": "c5", "at": "b", "start": "b", "title": None, "kind": "mention"},
          "E3": {"conv": "c1", "at": "c", "start": "a", "title": None, "kind": "me"}}
    res = {"who": "同学", "who_src": ["E9"],
           "facts": [{"text": "要去上海", "kind": "fact", "src": ["E1"]}, {"text": "要搬家", "src": ["E2"]},
                     {"text": "做事稳妥", "kind": "trait", "src": ["E1"]}],
           "recent": [{"text": "下个月搬家", "src": ["E2", "E3"]}],
           "mentions": [{"text": "李四说 TA 要搬家", "src": ["E2"]}, {"text": "TA 说自己要去上海", "src": ["E1"]}],
           "open": [{"text": "我要发合同", "src": ["E3"]}, {"text": "TA 周五回来再谈", "src": ["E1"]}, {"text": "TA 答应搬家后请客", "src": ["E2"]}]}
    got = profiles.check_v2(res, ev, days=4)
    assert got["who"] == "", "a who citing nothing given"
    assert [(x["text"], x["by"]) for x in got["facts"]] == [("要去上海", "self")], "hearsay dropped; a trait needs 5 days"
    assert got["recent"] == [] and [x["text"] for x in got["mentions"]] == ["李四说 TA 要搬家"]
    assert [(x["text"], x["by"]) for x in got["open"]] == [("我要发合同", "others"), ("TA 周五回来再谈", "self")], "hearsay is no promise"
    assert [x["kind"] for x in profiles.check_v2(res, ev, days=5)["facts"] if x["text"] == "做事稳妥"] == ["trait"]


def test_v2_profile_void_after_a_relabel(monkeypatch):
    v2_store()
    asked = []

    async def complete(messages, kind=None):
        asked.append(messages)
        e = next((l.split("]")[0][1:] for l in messages[1]["content"].splitlines() if "TA 原句" in l and "上海出差" in l), "E1")
        return {"who": "同事", "who_src": [e], "facts": [{"text": "要去上海出差", "kind": "fact", "src": [e]}]}, {"model": "m"}
    monkeypatch.setattr(summary, "complete", complete)
    pf = anyio.run(make_profile, "张三")
    assert asked[0][0]["content"].startswith(profiles.SYSTEM_V2) and pf["prompt_version"] == profiles.PROMPT_VERSION_V2
    assert pf["facts"][0]["by"] == "self" and pf["inputs"]["conversations"] == 2 and pf["who"] == "同事"
    assert anyio.run(person, "张三")["profile"]["facts"][0]["text"] == "要去上海出差"
    anyio.run(rename, "c4", {"from": "我", "to": "S9"})  # c4's other voice was someone else, not me
    assert anyio.run(person, "张三")["profile"] is None, "made on labels since changed: void"
    with pytest.raises(HTTPException) as e:
        anyio.run(make_profile, "S1")
    assert e.value.status_code == 400, "no profile of an unnamed voice"
    tenant.T().persona_v2 = False  # off: the stored profile is shown as it is, the next one made as before
    assert anyio.run(person, "张三")["profile"]["prompt_version"] == profiles.PROMPT_VERSION_V2
    anyio.run(make_profile, "张三")
    assert asked[-1][0]["content"].startswith(profiles.SYSTEM + "\n") and "[E1] 对话" in asked[-1][1]["content"]


def test_v2_no_call_without_evidence(monkeypatch):
    v2_store()
    monkeypatch.setattr(summary, "complete", lambda *a, **k: pytest.fail("no evidence: no call"))
    with db.tx() as t:
        t.execute("UPDATE summaries SET body = json_set(body, '$.status', 'pending') WHERE conversation IN ('c1', 'c4')")
    with pytest.raises(HTTPException) as e:
        anyio.run(make_profile, "张三")
    assert e.value.status_code == 422
