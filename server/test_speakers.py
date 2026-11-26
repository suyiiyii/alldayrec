"""The speakers page's groups: an unnamed group that sounds like a name is offered to it without the voices of
the conversations they share, and the page lists people (named, or enough speech in enough conversations) with the
other voices folded."""
import anyio
import numpy as np

from alldayrec import db
from alldayrec import routes_speakers
from alldayrec import speakers

RNG = np.random.default_rng(0)
A, B, C = (RNG.normal(size=16) for _ in range(3))


def voice(conv, label, base, n=5, speech_s=100.0):
    return {"conv": conv, "label": label, "device": "mic", "n": n, "speech_s": speech_s,
            "e": (base + RNG.normal(scale=0.1, size=16)).tolist()}


def test_twin_suggested_without_the_shared_conversation():
    """张三's twin (S1 in c3, c4, c5) shares c3 with 张三: c4 and c5 are offered as 张三, c3's S1 stays a group of its own
    under the leader's key (what a pin from that card is against). Before, c3 kept the whole group from 张三."""
    vs = [voice("c1", "张三", A), voice("c2", "张三", A), voice("c3", "张三", A),
          voice("c3", "S1", A), voice("c4", "S1", A, n=9), voice("c5", "S1", A),
          voice("c1", "S2", B), voice("c4", "S2", B), voice("c6", "S2", B), voice("c6", "S1", -A), voice("c2", "S2", -B)]
    vec = speakers.centered(vs)
    assert float(vec["c4|S1"] @ vec["c1|张三"]) > 0.8 and float(vec["c4|S1"] @ vec["c1|S2"]) < 0.5  # centring keeps them apart
    sp = speakers.speakers(vs, link=0.5, suggest=0.6, apart=set())
    named = next(g for g in sp if g["name"] == "张三")
    assert [[speakers.key(v) for v in s["members"]] for s in named["suggested"]] == [["c4|S1", "c5|S1"]]
    assert named["suggested"][0]["score"] >= 0.6
    left = next(g for g in sp if any(v["conv"] == "c3" and v["label"] == "S1" for v in g["members"]))
    assert left["name"] is None and left["id"] == "c4|S1" and len(left["members"]) == 1

    pinned = speakers.speakers(vs, link=0.5, suggest=0.6, apart={("c5|S1", "张三")})  # 不是 on one of them: none offered
    assert next(g for g in pinned if g["name"] == "张三")["suggested"] == []
    assert any(len(g["members"]) == 3 and g["id"] == "c4|S1" for g in pinned)


def test_minor_voices_fold(tmp_path):
    """People: named, or OTHERS_MIN_S of speech over OTHERS_MIN_CONVS conversations; the rest only with ?others=1."""
    db.open_db(tmp_path / "t.db")
    vs = [voice("c20261005100000", "张三", A, speech_s=10),  # named: a card however little
          voice("c20261005100001", "S1", B), voice("c20261005100002", "S1", B),  # 200 s over 2: a card
          voice("c20261005100003", "S1", C, speech_s=300),  # one conversation: folded
          voice("c20261005100004", "S2", -B, speech_s=60), voice("c20261005100005", "S1", -B, speech_s=60)]  # 120 s: folded
    with db.tx() as t:
        for v in vs:
            db.insert_conversation(t, {"id": v["conv"], "start": routes_speakers.conv_start(v["conv"]), "end": routes_speakers.conv_start(v["conv"]),
                                       "participants": [v["label"]], "sentences": 1, "speech_s": v["speech_s"]})
        db.insert_voices(t, [{**v, "e": np.array(v["e"])} for v in vs])
    people = anyio.run(routes_speakers.speaker_list)
    assert sorted((g["name"] or "", g["count"], g["speech_s"]) for g in people["cards"]) == [("", 2, 200.0), ("张三", 1, 10.0)]
    assert people["others"] == 2
    others = anyio.run(routes_speakers.speaker_list, True)
    assert sorted((g["count"], g["speech_s"]) for g in others["cards"]) == [(1, 300.0), (2, 120.0)] and others["others"] == 2
    assert anyio.run(routes_speakers.speaker_one, others["cards"][0]["id"])["minor"], "a folded card is still a group to name"


def test_live_voices_keep_their_recorder(tmp_path):
    """#355: the 'live' voices take their conversation's recorder at the start (one with none stays 'live'); centring,
    the halves' pools and the voices relearn learns from (db.LIVE_VOICE) stay what they were, bit for bit."""
    db.open_db(tmp_path / "t.db")
    convs = [("c20261005100000", "rec-a"), ("c20261005100001", "rec-b"), ("c20260930100000", None)]  # None: before 10-02
    with db.tx() as t:
        for cid, dev in convs:
            db.insert_conversation(t, {"id": cid, "start": routes_speakers.conv_start(cid), "end": routes_speakers.conv_start(cid),
                                       "participants": ["S1", "张三"], "sentences": 2, "speech_s": 9.0, "device": dev})
        vs = [v | {"device": "live"} for cid, _ in convs for v in (voice(cid, "S1", A), voice(cid, "张三", B))]
        vs += [voice("h1", "张三", B) | {"device": "mic-x"}, voice("h2", "S1", C) | {"device": "mic-y"}]  # history: no conversation
        db.insert_voices(t, [{**v, "e": np.array(v["e"])} for v in vs])
    was = [{k: x for k, x in v.items() if k != "live"} for v in db.voices()]  # as before: pooled by device
    picked = lambda where: {tuple(r) for r in db.conn().execute(f"SELECT conv, label FROM voices WHERE {where}")}
    learnt = picked("device = 'live'")
    db.init()
    now = db.voices()
    assert [v["device"] for v in now] == ["rec-a"] * 2 + ["rec-b"] * 2 + ["live"] * 2 + ["mic-x", "mic-y"]
    old, new = speakers.centered(was), speakers.centered(now)
    assert old.keys() == new.keys() and all(np.array_equal(old[k], new[k]) for k in old)
    assert [speakers.pool(v) for v in now] == [v["device"] for v in was]
    assert picked(db.LIVE_VOICE) == learnt and len(learnt) == 6


def test_live_writers_take_the_recorder(monkeypatch):
    """#355: both writers of a live voice (voices_loop's label_voice, late_merge's voice_record) store the
    conversation's recorder, not 'live'."""
    from alldayrec import core, late_merge

    async def cut(a, b, fmt, device=None):
        return np.zeros(16000, np.int16)
    monkeypatch.setattr(routes_speakers.routes_audio, "cut", cut)
    monkeypatch.setitem(core.state, "embed", lambda x: np.ones(4) / 2)
    rs = [{"start": "2026-10-05T10:00:00", "end": "2026-10-05T10:00:03", "speaker": "S1", "text": "x"}]
    v = anyio.run(routes_speakers.label_voice, {"id": "c20261005100000", "device": "rec-a"}, "S1", rs)
    assert v["device"] == "rec-a"
    assert late_merge.voice_record("c20261005100000", "S1", np.ones(4) / 2, rs, "rec-b")["device"] == "rec-b"


def test_halves_one_live_pool():
    """bank.halves splits a card within speakers.pool: live voices on three recorders (7 each, under CLASS_MIN apiece)
    are one pool, so two people in it are found; grouped by recorder (device) none of them could be split."""
    from alldayrec import bank
    ms = [{"conv": f"c{i}", "label": "张三", "device": f"rec-{i % 3}", "live": True, "speech_s": 1.0} for i in range(21)]
    rng = np.random.default_rng(1)
    vec = {f"c{i}|张三": (lambda x: x / np.linalg.norm(x))((A if i % 2 else -A) + rng.normal(scale=0.1, size=16)) for i in range(21)}
    got = bank.halves(ms, vec)
    assert got and sorted(len(h) for h in got) == [10, 11]
    assert bank.halves([m | {"live": False} for m in ms], vec) is None  # each recorder its own pool: too few to split
