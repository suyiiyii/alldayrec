"""A long press on one sentence: a star (listed newest first with its conversation, gone when taken off), and its
speaker fixed alone, which learns a named voice and is taken back whole by the rename undo."""
import anyio
import numpy as np
import pytest
from fastapi import HTTPException

from alldayrec import core
from alldayrec import db
from alldayrec import routes_audio
from alldayrec import tenant
from alldayrec.routes_sentences import sentence_speaker, star, stars, unstar
from alldayrec.routes_speakers import speaker_undo

T = "2026-10-05T10:00:"
CID = "c20261005100001"
ROWS = [dict(id="f1", start=T + "01.000", end=T + "04.000", speaker="S1", text="老张，你来一下"),
        dict(id="f2", start=T + "05.000", end=T + "05.500", speaker="S?", text="嗯"),
        dict(id="f3", start=T + "06.000", end=T + "09.000", speaker="S2", text="来了来了"),
        dict(id="f4", start=T + "10.000", end=T + "13.000", speaker="S1", text="好的老板"),
        dict(id="f5", start=T + "14.000", end=T + "15.000", speaker="我", text="走吧")]


@pytest.fixture
def store(tmp_path, monkeypatch):
    db.open_db(tmp_path / "t.db")
    with db.tx() as t:
        db.insert_sentences(t, [{**r, "source": "mic"} for r in ROWS])
        db.insert_sentences(t, [{**ROWS[0], "source": "other", "text": "另一台录的"}])  # the same id on another device
        db.insert_conversation(t, {"id": CID, "start": ROWS[0]["start"], "end": ROWS[-1]["end"], "participants": ["S1", "S2", "我"],
                                   "sentences": len(ROWS), "speech_s": 12.0, "device": "mic"})
        db.insert_voices(t, [{"conv": CID, "label": l, "device": "mic", "n": 2, "e": np.ones(4) * i} for i, l in enumerate(("S1", "S2"))])
        t.execute("INSERT INTO apart VALUES (?, ?)", (f"{CID}|S2", "g1"))
    db.put_summary({"conversation": CID, "status": "done", "title": "叫老张"})
    cut = []

    async def audio(start, end, fmt, device=None):
        cut.append((start, device))
        return np.zeros(1600, np.int16).tobytes()
    monkeypatch.setattr(routes_audio, "cut", audio)
    monkeypatch.setitem(core.state, "embed", lambda x: np.array([0.0, 1, 0, 0]))
    return cut


def dump():
    return [sorted(map(tuple, db.conn().execute(f"SELECT * FROM {n}"))) for n in ("sentences", "conversations", "voices", "apart", "name_hints")]


def queued():
    return tenant.T().summaries[1].statistics().current_buffer_used


def test_stars(store):
    assert anyio.run(star, "f3", {"conv": CID}) == {"starred": True}
    db.conn().execute("UPDATE stars SET at = '2026-10-05T11:00:00'")  # one second apart, in order
    anyio.run(star, "f1", {"conv": CID})
    anyio.run(star, "f1", {"conv": CID})  # twice: one star
    got = anyio.run(stars)
    assert [(s["id"], s["speaker"], s["text"], s["title"], s["conv"], s["start"]) for s in got] == [
        ("f1", "S1", "老张，你来一下", "叫老张", CID, ROWS[0]["start"]), ("f3", "S2", "来了来了", "叫老张", CID, ROWS[2]["start"])], "the mic's row, newest first"
    assert anyio.run(unstar, "f1") == {"starred": False} and [s["id"] for s in anyio.run(stars)] == ["f3"]
    for sid, conv in (("f9", CID), ("f1", "c20261005090000"), ("f1", None)):
        with pytest.raises(HTTPException) as e:
            anyio.run(star, sid, {"conv": conv})
        assert e.value.status_code == 404


def test_one_sentence_and_undo(store):
    was, n0 = dump(), queued()
    fix = lambda sid, to: anyio.run(sentence_speaker, sid, {"conv": CID, "to": to})

    r = fix("f4", "张三")  # one of S1's two: the row alone, a new participant, its voice learnt
    assert {**r, "undo": None} == {"speaker": "张三", "participants": ["S1", "S2", "我", "张三"], "voiceprints": 1, "undo": None}
    assert [x["speaker"] for x in db.sentences(ROWS[0]["start"], "~", source="mic")] == ["S1", "S?", "S2", "张三", "我"]
    assert db.sentences(ROWS[0]["start"], "~", source="other")[0]["speaker"] == "S1", "the other device's row of that id stays"
    assert dump()[2:] == was[2:] and queued() == n0 + 1, "voices, pins and hints untouched; the summary redone"
    assert db.people()["张三"][0] == 1 and store == [(ROWS[3]["start"], "mic")]
    assert anyio.run(speaker_undo, {"undo": r["undo"]}) == {"undone": 1} and dump() == was

    n1 = queued()
    r = fix("f2", "我")  # S? is no participant, 我 one already: summary redone, nothing learnt
    assert r["participants"] == ["S1", "S2", "我"] and r["voiceprints"] == 0 and queued() == n1 + 1 and len(store) == 1
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert dump() == was

    r = fix("f3", "S1")  # S2's only sentence: S2 merged into S1, its voice and pin with it
    assert r["participants"] == ["S1", "我"] and not db.conn().execute("SELECT 1 FROM voices WHERE label = 'S2'").fetchone()
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert dump() == was

    for to in ("", "S?", "S1", "名" * 21):
        with pytest.raises(HTTPException) as e:
            fix("f1", to)
        assert e.value.status_code == 400
    assert dump() == was
