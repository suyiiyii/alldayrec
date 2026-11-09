"""「这是你吗？」/「这是 P 吗？」(selfvoice.unsure, routes_voices check_*): which voices are asked about and in what order,
one card at a time, each conversation once, CHECK_DAY a day; yes is a rename by hand, no a pin, nothing learnt by itself."""
import datetime as dt

import anyio
import numpy as np
import pytest
from fastapi import HTTPException

from alldayrec import bank, core, db, routes_audio, routes_speakers, routes_voices, selfvoice

E = np.eye(8)
ME, P = E[0], E[1]
SR = 16000


def near(v, s, other=E[7]):
    """A unit voiceprint s from v (cosine)."""
    return s * v + np.sqrt(1 - s * s) * other


def test_unsure_kinds_and_order():
    T = "2026-11-09T10:00:00"
    v = lambda conv, label, e, n=3, speech_s=12.0, end=T: dict(conv=conv, label=label, n=n, speech_s=speech_s, e=e, end=end)
    vs = [v("c1", "S1", near(ME, 0.68)),                         # a, 0.02 under
          v("c2", "S1", near(ME, 0.695)),                        # a, 0.005 under: the first a
          v("c3", "S2", near(P, 0.66, E[2])),                    # b
          v("c4", "P", near(P, 0.9, E[3]), end="2026-11-09T09:00:00"),  # c
          v("c5", "P", near(P, 0.9, E[4]), end="2026-11-09T11:00:00"),  # c, later: the first
          v("c6", "P", near(P, 0.9, E[5])),                      # named by the user
          v("c7", "S1", near(ME, 0.68), n=2),                    # too few sentences
          v("c8", "S1", near(ME, 0.68)),                         # pinned apart
          v("c9", "S1", near(ME, 0.65)),                         # under NEAR_ME
          v("c10", "S1", near(ME, 0.68), speech_s=9.0),          # too little speech to teach 我
          v("c11", "Q", near(ME, 0.68)),                         # a name the user gave: not asked if 我
          v("c12", "我", near(ME, 0.68))]
    got = selfvoice.unsure(vs, [ME], {"P": np.array([P])}, {("c6", "P"), ("c11", "Q")}, {("c8|S1", "我")})
    assert [(c["kind"], c["conv"], c["name"]) for c in got] == [
        ("c", "c5", "P"), ("c", "c4", "P"), ("a", "c2", "我"), ("a", "c1", "我"), ("b", "c3", "P")]
    assert selfvoice.unsure(vs[:3], [], {"P": np.array([P])}, set(), set())[0]["kind"] == "b", "no 我 voiceprint: no a"
    both = v("c3", "S2", bank.unit(P + 0.95 * ME))  # 0.73 to P, 0.69 to 我: P by too little, and near 我
    assert [c["kind"] for c in selfvoice.unsure([both], [ME], {"P": np.array([P])}, set(), set())] == ["b"]
    assert [c["kind"] for c in selfvoice.unsure([both], [E[6]], {"P": np.array([P])}, set(), set())] == [], "P, clearly"
    two = {**both, "e": bank.unit(P + E[2])}  # 0.71 to P and to Q: past PEOPLE_MIN, no margin
    assert [(c["kind"], c["score"]) for c in selfvoice.unsure([two], [E[6]], {"P": np.array([P]), "Q": np.array([E[2]])}, set(), set())] == [("b", 0.05)]


SAID: dict[str, np.ndarray] = {}


@pytest.fixture
def store(tmp_path, monkeypatch):
    SAID.clear()
    db.open_db(tmp_path / "t.db")

    async def cut(start, end, fmt, device=None):  # the sentence's start, then a second of tone: loud enough for a level
        return start.ljust(24).encode() + (3000 * np.sin(np.arange(SR) / 5)).astype(np.int16).tobytes()
    monkeypatch.setattr(routes_audio, "cut", cut)
    monkeypatch.setitem(core.state, "embed", lambda x: SAID[x[:12].tobytes().decode().strip()])
    routes_speakers.save_owner({"self": ME.astype(np.float32)})
    with db.tx() as t:  # P, taught from a conversation of its own
        db.conn().execute("INSERT INTO enrolled VALUES ('P', 'c-old', 1, 1, ?)", (db.blob(P),))
        bank.recompute(t, "P")


def conv(hour: int, label: str, v: np.ndarray) -> str:
    """A conversation of today: three 3 s sentences of label saying v, its voice and a finished summary."""
    at = dt.datetime.combine(dt.date.today(), dt.time(hour))
    rows = [dict(id=f"{hour}-{k}", start=core.iso(at + dt.timedelta(seconds=4 * k)), end=core.iso(at + dt.timedelta(seconds=4 * k + 3)),
                 speaker=label, text="一句说得够长的话", source="mic") for k in range(3)]
    cid = "c" + at.strftime("%Y%m%d%H%M%S")
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, {"id": cid, "start": rows[0]["start"], "end": rows[-1]["end"], "participants": [label],
                                   "sentences": 3, "speech_s": 9.0, "device": "mic"})
        db.insert_voices(t, [{"conv": cid, "label": label, "device": "mic", "n": 3, "speech_s": 12.0, "e": bank.unit(v)}])
        db.put_summary({"conversation": cid, "status": "done"}, t)
    SAID.update({r["start"]: bank.unit(v) for r in rows})
    return cid


def card():
    return anyio.run(routes_voices.check_get)["card"]


def test_c_then_a(store):
    """c first: yes teaches P a sample from it, once though two pages answer; then a: no pins it apart from 我."""
    ca, cc = conv(1, "S1", near(ME, 0.68)), conv(2, "P", near(P, 0.9))
    with pytest.raises(HTTPException):
        anyio.run(routes_voices.check_answer, {"answer": "no"})
    anyio.run(routes_voices.check_round)
    assert card() | {"clip": None} == {"kind": "c", "conv": cc, "label": "P", "name": "P", "clip": None, "start": db.conversation(cc)["start"]}
    assert card()["clip"]["text"] == "一句说得够长的话"
    anyio.run(routes_voices.check_round)
    assert card()["conv"] == cc, "one card at a time"

    async def two_pages():
        out = []

        async def answer():
            try:
                out.append(await routes_voices.check_answer({"answer": "yes"}))
            except HTTPException as e:
                out.append(e.status_code)
        async with anyio.create_task_group() as tg:
            tg.start_soon(answer)
            tg.start_soon(answer)
        return out
    assert sorted(map(str, anyio.run(two_pages))) == ["409", "{'ok': True}"]
    assert [r[0] for r in db.conn().execute("SELECT conv FROM enrolled")].count(cc) == 3, "learnt once"
    assert db.people()["P"][1].shape == (2, 8), "a sample of its own"
    anyio.run(routes_voices.check_round)
    assert card()["kind"] == "a" and card()["conv"] == ca
    assert anyio.run(routes_voices.check_answer, {"answer": "no"}) == {"ok": True}
    assert (f"{ca}|S1", "我") in db.apart() and db.conversation(ca)["participants"] == ["S1"] and card() is None
    anyio.run(routes_voices.check_round)
    assert card() is None, "both conversations asked"
    assert [x["answer"] for x in db.kv("voicecheck")["log"]] == ["yes", "no"]


def test_later_not_asked_again(store):
    """以后再说 changes nothing, and the conversation is not asked about again."""
    ca = conv(1, "S1", near(P, 0.66, E[2]))  # b: no quota of its own
    anyio.run(routes_voices.check_round)
    assert card()["conv"] == ca
    anyio.run(routes_voices.check_answer, {"answer": "later"})
    anyio.run(routes_voices.check_round)
    assert card() is None and db.apart() == set() and db.conversation(ca)["participants"] == ["S1"]


def test_a_once_a_day(store):
    conv(1, "S1", near(ME, 0.68)), conv(2, "S1", near(ME, 0.69, E[2]))
    anyio.run(routes_voices.check_round)
    anyio.run(routes_voices.check_answer, {"answer": "no"})
    anyio.run(routes_voices.check_round)
    assert card() is None, "CHECK_ME_DAY: the other a waits for tomorrow"


def test_names_the_user_gave_not_asked(store, monkeypatch):
    """A label the user named is not the diarist's to check (c) nor asked as 我 (a), though it taught nothing."""
    cp, cq = conv(1, "S1", near(P, 0.9)), conv(2, "S1", near(ME, 0.68))
    embed = core.state.pop("embed")  # nothing learnt: no recording to learn from
    routes_speakers.apply_rename(cp, "S1", "P")
    routes_speakers.apply_rename(cq, "S1", "Q")
    core.state["embed"] = embed
    assert [r[0] for r in db.conn().execute("SELECT conv FROM enrolled")] == ["c-old"]
    anyio.run(routes_voices.check_round)
    assert card() is None


def test_yes_renames_and_a_day_has_check_day(store, monkeypatch):
    """b: yes renames the label to P as a rename by hand would (and P learns it); past CHECK_DAY a day, no card."""
    monkeypatch.setattr(selfvoice, "CHECK_DAY", 1)
    cb, cb2 = conv(1, "S1", near(P, 0.66, E[2])), conv(2, "S1", near(P, 0.67, E[3]))
    anyio.run(routes_voices.check_round)
    assert (card()["kind"], card()["conv"]) == ("b", cb2), "the closest to PEOPLE_MIN first"
    anyio.run(routes_voices.check_answer, {"answer": "yes"})
    assert db.conversation(cb2)["participants"] == ["P"] and {r[0] for r in db.conn().execute("SELECT conv FROM enrolled")} == {"c-old", cb2}
    anyio.run(routes_voices.check_round)
    assert card() is None, "the day's one card was asked"
    monkeypatch.setattr(selfvoice, "CHECK_DAY", 2)
    anyio.run(routes_voices.check_round)
    assert card()["conv"] == cb
    db.conn().execute("UPDATE sentences SET speaker = 'S9' WHERE speaker = 'S1'")  # renamed since it popped
    assert anyio.run(routes_voices.check_answer, {"answer": "yes"}) == {"ok": False} and card() is None


def test_renamed_since_the_card(store):
    """A voice the user named after its card popped: a yes neither makes it 我 (a) nor says it learnt anything (c)."""
    cr = conv(1, "R", near(ME, 0.68))
    anyio.run(routes_voices.check_round)
    assert card()["kind"] == "a" and card()["conv"] == cr
    with db.tx() as t:  # another voice of theirs merged into R since
        db.put_kv("named", [f"{cr}|R"], t)
    assert anyio.run(routes_voices.check_answer, {"answer": "yes"}) == {"ok": False}
    assert db.conversation(cr)["participants"] == ["R"] and card() is None
    cc = conv(2, "P", near(P, 0.9))
    anyio.run(routes_voices.check_round)
    assert card()["kind"] == "c" and card()["conv"] == cc
    db.conn().execute("UPDATE sentences SET speaker = 'S9' WHERE speaker = 'P'")
    assert anyio.run(routes_voices.check_answer, {"answer": "yes"}) == {"ok": False}
    assert [r[0] for r in db.conn().execute("SELECT conv FROM enrolled")] == ["c-old"]
    assert [x["answer"] for x in db.kv("voicecheck")["log"]] == ["stale", "stale"]
