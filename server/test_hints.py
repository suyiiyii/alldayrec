"""Name hints and speaker suggestions: only names the rows bear out are kept, a merge keeps the target's voice and
carries the hints, and a conversation's suggestions come from voices, names and aliases without changing anything."""
import tempfile
from pathlib import Path

import anyio
import numpy as np

import db
import hints
from routes_speakers import alias_set, apply_rename, label_suggestions, person

T = "2026-10-05T10:00:"
ROWS = [dict(start=T + "01.000", end=T + "02.000", speaker="S1", text="老张，你来一下"),
        dict(start=T + "03.000", end=T + "04.000", speaker="S?", text="嗯"),
        dict(start=T + "05.000", end=T + "06.000", speaker="S2", text="来了来了"),
        dict(start=T + "07.000", end=T + "08.000", speaker="S3", text="大家好，我是小李"),
        dict(start=T + "09.000", end=T + "10.000", speaker="我", text="妈，你吃了吗"),
        dict(start=T + "11.000", end=T + "12.000", speaker="我", text="我先走了"),
        dict(start=T + "13.000", end=T + "14.000", speaker="S1", text="好的老板"),
        dict(start=T + "15.000", end=T + "16.000", speaker="S2", text="嗯"),
        dict(start=T + "17.000", end=T + "18.000", speaker="S2", text="走吧")]


def test_check():
    res = {"names": [
        {"who": "S2", "name": "老张", "kind": "addressed", "at": "10:00:01"},  # S? skipped, S2 answers: kept
        {"who": "S1", "name": "老张", "kind": "addressed", "at": "10:00:01"},  # the caller: dropped
        {"who": "S3", "name": "小李", "kind": "self", "at": "10:00:07"},
        {"who": "S1", "name": "妈", "kind": "addressed", "at": "10:00:09"},  # I talk on: no answer, dropped
        {"who": "S2", "name": "老板", "kind": "addressed", "at": "10:00:13"},  # what strangers are called
        {"who": "S2", "name": "小王", "kind": "addressed", "at": "10:00:01"},  # not in the row
        {"who": "S9", "name": "老张", "kind": "addressed", "at": "10:00:01"},
        "x"]}
    got = hints.check(res, ROWS)
    assert [(h["who"], h["name"], h["kind"], h["by"]) for h in got] == [("S2", "老张", "addressed", "S1"), ("S3", "小李", "self", "S3")]
    assert got[0]["at"] == ROWS[0]["start"]
    assert hints.check(res, ROWS, skip={"S3"})[1:] == []
    old = [dict(r, speaker="外放媒体") if r["speaker"] == "S2" else r for r in ROWS]  # a history label that is no one
    assert [h["who"] for h in hints.check({"names": [{**res["names"][0], "who": "外放媒体"}]}, old)] == []
    got = [dict(h, who="外放媒体") for h in got[:1]] + got
    spans = [{"from": ROWS[3]["start"], "to": ROWS[3]["start"]}]  # 小李's self-introduction was a video
    assert [h["who"] for h in hints.shown({"names": got}, {"media_spans": spans})] == ["S2"]
    assert len(hints.shown({"names": got}, None)) == 3 - 1 and hints.shown(None, None) == []


def test_rename_merges_and_suggestions():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    c = {"id": "c20261005100001", "start": ROWS[0]["start"], "end": ROWS[-1]["end"], "participants": ["S1", "S2", "S3", "我"],
         "sentences": len(ROWS), "speech_s": 70.0}
    with db.tx() as t:
        db.insert_sentences(t, [{**r, "id": str(i)} for i, r in enumerate(ROWS)])
        db.insert_conversation(t, c)
        db.insert_voices(t, [{"conv": c["id"], "label": l, "device": "mic", "n": 3, "e": np.ones(4) * i} for i, l in enumerate(("S1", "S2", "S3"))])
    db.put_name_hints({"conv": c["id"], "names": hints.check({"names": [
        {"who": "S2", "name": "老张", "kind": "addressed", "at": "10:00:01"},
        {"who": "S3", "name": "小李", "kind": "self", "at": "10:00:07"}]}, ROWS)})

    a = np.array([1.0, 0, 0, 0])
    vec = {f"{c['id']}|S1": a, f"{c['id']}|S2": a, f"{c['id']}|S3": np.array([0, 1.0, 0, 0])}
    groups = [{"id": "x", "name": "张三", "members": [], "called": [],
               "suggested": [{"score": 0.7, "members": [{"conv": c["id"], "label": "S3"}],
                              "called": [{"name": "李四", "convs": ["c1", "c2", c["id"]]}, {"name": "王五", "convs": ["c1", c["id"]]}]}]}]
    anyio.run(alias_set, {"alias": "老张", "name": "张三"})
    sug = label_suggestions(db.conversation(c["id"]), groups, vec, db.name_hints(c["id"])[c["id"]], db.aliases())
    assert [(x["who"], x["name"], x["canon"]) for x in sug["names"]] == [("S2", "老张", "张三"), ("S3", "小李", "小李")]
    assert sug["merge"] == [{"from": "S1", "to": "S2", "score": 1.0}]  # alike voices; S2 has more rows
    assert sug["like"] == [{"who": "S3", "name": "张三", "score": 0.7}]
    assert sug["elsewhere"] == [{"who": "S3", "name": "李四", "convs": 2}]

    r = anyio.run(apply_rename, c["id"], "S1", "S2")  # the merge, as the page asks it
    assert r["participants"] == ["S2", "S3", "我"]
    assert [v[0] for v in db.conn().execute("SELECT label FROM voices ORDER BY label")] == ["S2", "S3"]
    assert db.vec(db.conn().execute("SELECT e FROM voices WHERE label = 'S2'").fetchone()[0])[0] == 1.0, "S2's own voice stays"
    assert {x["by"] for x in db.name_hints(c["id"])[c["id"]]["names"]} == {"S2", "S3"}, "the caller S1 is S2 now"

    anyio.run(apply_rename, c["id"], "S2", "张三")
    sug = label_suggestions(db.conversation(c["id"]), groups, vec, db.name_hints(c["id"])[c["id"]], db.aliases())
    assert [x["who"] for x in sug["names"]] == ["S3"], "老张 is 张三's alias: nothing left to offer for 张三"
    assert anyio.run(person, "张三")["aliases"] == ["老张"]
    assert anyio.run(alias_set, {"alias": "老张", "name": ""}) == {}
