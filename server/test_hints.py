"""Name hints and speaker suggestions: only names the rows bear out are kept, a merge keeps the target's voice and
carries the hints, a conversation's suggestions come from voices, names and aliases without changing anything, and a
rename answers before its voice is learnt."""
import tempfile
from pathlib import Path

import anyio
import numpy as np
import pytest
from fastapi import HTTPException

from alldayrec import core
from alldayrec import db
from alldayrec import hints
from alldayrec import routes_audio
from alldayrec import routes_speakers
from alldayrec import tenant
from alldayrec.routes_speakers import alias_set, apply_rename, keep_undo, label_suggestions, person, rename, speaker_undo

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

    r, _, _ = apply_rename(c["id"], "S1", "S2")  # the merge, as the page asks it
    assert r["participants"] == ["S2", "S3", "我"]
    assert [v[0] for v in db.conn().execute("SELECT label FROM voices ORDER BY label")] == ["S2", "S3"]
    assert db.vec(db.conn().execute("SELECT e FROM voices WHERE label = 'S2'").fetchone()[0])[0] == 1.0, "S2's own voice stays"
    assert {x["by"] for x in db.name_hints(c["id"])[c["id"]]["names"]} == {"S2", "S3"}, "the caller S1 is S2 now"

    apply_rename(c["id"], "S2", "张三")
    sug = label_suggestions(db.conversation(c["id"]), groups, vec, db.name_hints(c["id"])[c["id"]], db.aliases())
    assert [x["who"] for x in sug["names"]] == ["S3"], "老张 is 张三's alias: nothing left to offer for 张三"
    assert anyio.run(person, "张三")["aliases"] == ["老张"]
    assert anyio.run(alias_set, {"alias": "老张", "name": ""}) == {}


def test_speaker_list_is_what_the_cards_show(tmp_path):
    """GET /api/speakers carries what the speakers page's cards show (it was 706 KB on one instance, 10-24): samples
    with their clip and first point, called with a count; the group's own GET has it whole."""
    db.open_db(tmp_path / "t.db")
    convs = [f"c2026100510000{i}" for i in range(3)]
    with db.tx() as t:
        for i, cid in enumerate(convs):
            db.insert_conversation(t, {"id": cid, "start": f"{T}0{i}.000", "end": f"{T}0{i}.900", "participants": ["S1", "我"],
                                       "sentences": 1, "speech_s": 1.0})
            db.put_summary({"conversation": cid, "status": "done", "title": "早会", "people": [{"who": "S1", "points": ["说了一", "说了二"]}]}, t)
            db.put_name_hints({"conv": cid, "names": [{"who": "S1", "name": "老张", "kind": "addressed", "at": f"{T}0{i}.000", "by": "我"}]}, t)
        db.insert_voices(t, [{"conv": cid, "label": "S1", "device": "mic", "n": 3, "speech_s": 5.0, "clip_start": f"{T}0{i}.000",
                              "clip_end": f"{T}0{i}.500", "e": np.array(e)}
                             for i, (cid, e) in enumerate(zip(convs, ([1.0, 0, 0, 0], [1.0, 0, 0, 0], [0, 1.0, 0, 0])))])
    assert anyio.run(routes_speakers.speaker_list) == {"cards": [], "others": 2}, "10 s and 5 s: folded"
    g = next(g for g in anyio.run(routes_speakers.speaker_list, True)["cards"] if g["count"] == 2)
    assert set(g) == {"id", "name", "count", "speech_s", "last", "co", "samples", "called", "suggested"}
    assert g["samples"][0] == {"conv": convs[1], "label": "S1", "start": "2026-10-05T10:00:01", "clip_start": f"{T}01.000",
                               "clip_end": f"{T}01.500", "title": "早会", "points": ["说了一"]}
    assert g["called"] == [{"name": "老张", "n": 2}] and g["co"] == ["我"]
    whole = anyio.run(routes_speakers.speaker_one, g["id"])
    assert [m["conv"] for m in whole["members"]] == convs[1::-1] and whole["members"][0]["with"] == ["我"]
    assert whole["called"] == [{"name": "老张", "convs": convs[1::-1]}] and whole["samples"][0]["points"] == ["说了一", "说了二"]


def test_rename_answers_before_the_voice_is_learnt(tmp_path, monkeypatch):
    """The answer comes once the store has the name; the diarist learns the voice after it, in the tenant's tasks
    (10-24: a group of 10 conversations took 40 s, and the speakers page sat locked meanwhile)."""
    db.open_db(tmp_path / "t.db")
    rows = [dict(id=str(i), start=f"{T}{i * 3:02}.000", end=f"{T}{i * 3 + 2:02}.000", speaker="S1", text="嗯") for i in range(3)]
    c = {"id": "c20261005100000", "start": rows[0]["start"], "end": rows[-1]["end"], "participants": ["S1"], "sentences": 3, "speech_s": 6.0}
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, c)
    gate, cut = anyio.Event(), []

    async def audio(start, end, fmt, device=None):
        await gate.wait()
        cut.append(start)
        return np.zeros(1600, np.int16).tobytes()
    monkeypatch.setattr(routes_audio, "cut", audio)
    monkeypatch.setitem(core.state, "embed", lambda x: np.array([0.0, 1, 0, 0]))

    async def run():
        async with anyio.create_task_group() as tenant.T().tg:
            with anyio.fail_after(5):  # learning first would wait for the gate for good
                r = await rename(c["id"], {"from": "S1", "to": "张三"})
                assert {**r, "undo": None} == {"participants": ["张三"], "renamed": 3, "voiceprints": 3, "undo": None}
            assert db.conversation(c["id"])["participants"] == ["张三"] and "张三" not in db.people() and not cut, "nothing learnt yet"
            gate.set()
        assert len(cut) == 3
        n, e = db.people()["张三"]
        assert n == 3 and list(e) == [0, 1, 0, 0]
    anyio.run(run)


def test_undo_takes_a_merge_back(tmp_path):
    """A merge taken back by the undo id its answer gave: the rows, the participants, both voices, the pins and the
    hints as they were, once. Two labels of one conversation named in one click (the speakers page) go back the last
    first; a conversation changed since is refused whole."""
    db.open_db(tmp_path / "t.db")
    cid = "c20261005100001"
    with db.tx() as t:
        db.insert_sentences(t, [{**r, "id": str(i)} for i, r in enumerate(ROWS)])
        db.insert_conversation(t, {"id": cid, "start": ROWS[0]["start"], "end": ROWS[-1]["end"], "participants": ["S1", "S2", "S3", "我"],
                                   "sentences": len(ROWS), "speech_s": 70.0})
        db.insert_voices(t, [{"conv": cid, "label": l, "device": "mic", "n": 3, "e": np.ones(4) * i} for i, l in enumerate(("S1", "S2", "S3"))])
        t.executemany("INSERT INTO apart VALUES (?, ?)", [(f"{cid}|S1", "张三"), (f"{cid}|S2", "张三")])  # the merge's OR REPLACE drops one
    db.put_name_hints({"conv": cid, "names": hints.check({"names": [{"who": "S2", "name": "老张", "kind": "addressed", "at": "10:00:01"}]}, ROWS)})
    dump = lambda: [sorted(map(tuple, db.conn().execute(f"SELECT * FROM {n}"))) for n in ("sentences", "conversations", "voices", "apart", "name_hints")]
    was = dump()

    r = anyio.run(rename, cid, {"from": "S1", "to": "S2"})
    assert r["participants"] == ["S2", "S3", "我"] and dump() != was
    assert anyio.run(speaker_undo, {"undo": r["undo"]}) == {"undone": 1}
    assert dump() == was
    with pytest.raises(HTTPException) as e:
        anyio.run(speaker_undo, {"undo": r["undo"]})
    assert e.value.status_code == 409 and e.value.detail.startswith("[undo_gone] ")

    k = keep_undo([apply_rename(cid, "S1", "张三")[2], apply_rename(cid, "S3", "张三")[2]])
    assert db.conversation(cid)["participants"] == ["张三", "S2", "我"]
    assert anyio.run(speaker_undo, {"undo": k}) == {"undone": 2} and dump() == was

    k = keep_undo([apply_rename(cid, "S1", "S2")[2]])
    apply_rename(cid, "S3", "S2")
    changed = dump()
    with pytest.raises(HTTPException):
        anyio.run(speaker_undo, {"undo": k})
    assert dump() == changed


def test_this_is_me_learns_a_centroid(tmp_path):
    """「这是我」 (a rename to 我) relabels like any merge and adds one centroid to 我's voiceprint: the unit mean of the
    voices, weighted by speech. The undo takes both back: the file gone when the click made it, else the centroid
    dropped and the npz before kept as .prev. Under 10 s of voice relabels only. 不是他 out of 我 drops a centroid
    with the last of the voices it was learnt from."""
    db.open_db(tmp_path / "t.db")
    npz, A, B = tmp_path / "owner-redimnet2.npz", np.array([1.0, 0, 0, 0]), np.array([0.8, 0.6, 0, 0])
    convs = [f"c2026100510000{i}" for i in range(2)]
    with db.tx() as t:  # S1 is one voice in two conversations (A 30 s, B 10 s), S2 someone else in each
        for i, cid in enumerate(convs):
            rows = [dict(id=f"{i}{k}", start=f"{T}{i}{k}.000", end=f"{T}{i}{k}.900", speaker=f"S{k % 2 + 1}", text="嗯") for k in range(4)]
            db.insert_sentences(t, rows)
            db.insert_conversation(t, {"id": cid, "start": rows[0]["start"], "end": rows[-1]["end"], "participants": ["S1", "S2"],
                                       "sentences": 4, "speech_s": 3.6})
        db.insert_voices(t, [{"conv": c, "label": l, "device": "mic", "n": 3, "speech_s": s, "e": e} for c, l, s, e in (
            (convs[0], "S1", 30.0, A), (convs[1], "S1", 10.0, B), (convs[0], "S2", 20.0, np.array([0, 0, 1.0, 0])),
            (convs[1], "S2", 4.0, np.array([0, 0, 0, 1.0])))])
    dump = lambda: [sorted(map(tuple, db.conn().execute(f"SELECT * FROM {n}"))) for n in ("sentences", "conversations", "voices")]
    was = dump()

    g = next(g for g in anyio.run(routes_speakers.speaker_list, True)["cards"] if g["count"] == 2)
    r = anyio.run(routes_speakers.speaker_name, {"group": g["id"], "name": "我"})
    assert r["renamed"] == 2 and r["learned"] and [c["participants"] for c in map(db.conversation, convs)] == [["我", "S2"]] * 2
    bank = dict(np.load(npz))
    (k, v), = bank.items()
    assert k.startswith("me-") and np.allclose(v, np.array([38.0, 6, 0, 0]) / np.hypot(38, 6)), "30 A + 10 B, unit"
    assert np.allclose(tenant.T().owner[k], v) and not (tmp_path / "owner-redimnet2.npz.prev").exists()
    assert anyio.run(speaker_undo, {"undo": r["undo"]}) == {"undone": 2}
    assert dump() == was and not npz.exists() and tenant.T().owner == {}, "no voiceprint before: none after the undo"

    np.savez(npz, self=np.array([0, 1.0, 0, 0]))  # the card's (or a hand-made one)
    r = anyio.run(rename, convs[0], {"from": "S1", "to": "我"})
    assert r["learned"] and r["participants"] == ["我", "S2"]
    bank = dict(np.load(npz))
    (s, me) = bank
    assert s == "self" and me.startswith("me-") and np.allclose(bank[me], A)
    assert list(np.load(tmp_path / "owner-redimnet2.npz.prev")) == ["self"]
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert dump() == was and list(np.load(npz)) == ["self"] and list(tenant.T().owner) == ["self"]
    assert db.kv("me_centroids") == {}, "the undos took their voices' lists too"

    r = anyio.run(rename, convs[1], {"from": "S2", "to": "我"})  # 4 s of voice
    assert not r["learned"] and r["participants"] == ["S1", "我"] and list(np.load(npz)) == ["self"]

    g = next(g for g in anyio.run(routes_speakers.speaker_list, True)["cards"] if g["count"] == 2)
    assert anyio.run(routes_speakers.speaker_name, {"group": g["id"], "name": "我"})["learned"]
    me = next(k for k in np.load(npz) if k != "self")
    assert db.kv("me_centroids") == {me: [f"{c}|我" for c in convs[::-1]]}  # the group's members, newest first
    anyio.run(routes_speakers.speaker_detach, {"conv": convs[0], "label": "我", "group": "我"})
    assert db.conversation(convs[0])["participants"] == ["S3", "S2"] and list(np.load(npz)) == ["self", me], "one voice left"
    assert db.kv("me_centroids") == {me: [f"{convs[1]}|我"]}
    anyio.run(routes_speakers.speaker_detach, {"conv": convs[1], "label": "我", "group": "我"})
    assert list(np.load(npz)) == ["self"] and list(tenant.T().owner) == ["self"] and db.kv("me_centroids") == {}
