"""The people bank as a ledger (bank.py, #295): what a rename teaches is filtered, recorded per sentence and taken back
by the undo either way round."""
import anyio
import numpy as np
import pytest

from alldayrec import bank
from alldayrec import core
from alldayrec import db
from alldayrec import routes_audio
from alldayrec import routes_speakers
from alldayrec.routes_speakers import apply_rename, enroll, keep_undo, rename, speaker_undo

A, B, C = np.eye(4)[0], np.eye(4)[1], np.array([0.5, np.sqrt(0.75), 0, 0])  # C: 0.5 from A, a third person


def conv(i: int, label: str, voices: list[np.ndarray], device: str = "mic", t=None) -> str:
    """Conversation i: one sentence of label per voiceprint (2 s each), its voices row (their mean)."""
    cid, at = f"c202610{i // 24 + 1:02}{i % 24:02}0000", f"2026-10-{i // 24 + 1:02}T{i % 24:02}:00:"
    rows = [dict(id=f"{i}-{k}", start=f"{at}{k * 3:02}.000", end=f"{at}{k * 3 + 2:02}.000", speaker=label, text=f"{i}-{k}", source=device)
            for k in range(len(voices))]
    with db.tx() as t:
        db.insert_sentences(t, rows)
        db.insert_conversation(t, {"id": cid, "start": rows[0]["start"], "end": rows[-1]["end"], "participants": [label],
                                   "sentences": len(rows), "speech_s": 2.0 * len(rows), "device": device})
        db.insert_voices(t, [{"conv": cid, "label": label, "device": device, "n": len(rows), "speech_s": 60.0,
                              "e": bank.unit(np.mean(voices, 0))}])
    SAID.update({r["start"]: v for r, v in zip(rows, voices)})
    return cid


SAID: dict[str, np.ndarray] = {}  # sentence start: the voiceprint its audio gives


@pytest.fixture
def store(tmp_path, monkeypatch):
    SAID.clear()
    db.open_db(tmp_path / "t.db")

    async def cut(start, end, fmt, device=None):  # int16 audio that says which sentence it is
        return start.ljust(24).encode()
    monkeypatch.setattr(routes_audio, "cut", cut)
    monkeypatch.setitem(core.state, "embed", lambda x: SAID[x.tobytes().decode().strip()])


def ledger():
    return sorted((r[0], r[1], r[3]) for r in db.conn().execute("SELECT * FROM enrolled"))


def test_pick():
    """Per label: OWN_MIN to its own mean, BANK_MIN to the name's voiceprint when there is one; CAP, in turns."""
    assert bank.pick([[A, A, A, B]], None) == [(0, 0), (0, 1), (0, 2)], "B, a third of one label: out"
    assert bank.pick([[A, A], [C, C, C]], A) == [(0, 0), (0, 1)], "C sounds like itself, not like the name"
    got = bank.pick([[A] * 30, [B] * 30, [C] * 30], None)
    assert len(got) == bank.CAP and [sum(i == k for i, _ in got) for k in range(3)] == [14, 13, 13]


def test_legacy_bank_kept(tmp_path):
    """The bank before the ledger is one row of it: a learnt sentence adds to it as the running mean did."""
    db.open_db(tmp_path / "t.db")
    db.conn().execute("DROP TABLE enrolled")
    db.conn().execute("INSERT INTO people VALUES ('张三', 3, ?)", (db.blob(A),))
    db.init()
    assert ledger() == [("张三", None, 3)]
    with db.tx() as t:
        db.insert_sentences(t, [dict(id="1", start="2026-10-05T10:00:00.000", end="2026-10-05T10:00:02.000", speaker="张三")])
        assert bank.learn(t, "张三", "c1", [({"start": "2026-10-05T10:00:00.000"}, B)]) == 1
    n, e = db.people()["张三"]
    assert n == 4 and np.allclose(e, (3 * A + B) / 4)
    with db.tx() as t:
        bank.forget(t, "张三", [1])
    assert db.people()["张三"][0] == 3 and np.allclose(db.people()["张三"][1], A)


def test_learning_filtered_recorded_undone(store):
    """A label's odd sentence and a voice unlike the name's are not learnt; the undo takes a rename's sentences out of
    the bank, learnt before it or not (an undo before the learning: it learns nothing)."""
    c1 = conv(1, "S1", [A, A, A, B])
    c2 = conv(2, "S1", [C, C, C])
    r1 = anyio.run(rename, c1, {"from": "S1", "to": "张三"})
    assert len(ledger()) == 3 and np.allclose(db.people()["张三"][1], A), "B, a fourth of S1: not 张三's"
    r2 = anyio.run(rename, c2, {"from": "S1", "to": "张三"})
    assert len(ledger()) == 3, "C is itself, 0.5 from what 张三 sounds like"
    assert anyio.run(speaker_undo, {"undo": r2["undo"]}) == {"undone": 1}
    assert anyio.run(speaker_undo, {"undo": r1["undo"]}) == {"undone": 1}
    assert ledger() == [] and "张三" not in db.people()

    _, rows, before = apply_rename(c1, "S1", "张三")  # the click's answer; its undo comes before the learning
    anyio.run(speaker_undo, {"undo": keep_undo([before])})
    anyio.run(enroll, "张三", rows)
    assert ledger() == [] and "张三" not in db.people()


def test_rename_away_forgets_and_undo_restores(store):
    """不是 (a named label back to S<n>) takes what its sentences taught out; the undo of a rename away puts it back."""
    c1, c2 = conv(1, "S1", [A, A, A]), conv(2, "S1", [A, A, A])
    for c in (c1, c2):
        anyio.run(rename, c, {"from": "S1", "to": "张三"})
    assert db.people()["张三"][0] == 6
    was = ledger()
    r = anyio.run(rename, c2, {"from": "张三", "to": "李四"})  # 李四 learns them anew
    assert db.people()["张三"][0] == 3 and db.people()["李四"][0] == 3
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert ledger() == was and db.people()["张三"][0] == 6 and "李四" not in db.people()
    anyio.run(routes_speakers.speaker_detach, {"conv": c1, "label": "张三", "group": "张三"})
    assert db.people()["张三"][0] == 3


def test_one_sentence_fix_forgets_and_learns(store):
    """A sentence's speaker fixed alone (routes_sentences): what it taught its old name goes, the new name learns it
    (filtered and recorded as a rename's), and the undo puts both back."""
    from alldayrec.routes_sentences import sentence_speaker
    c = conv(1, "S1", [A, A, A])
    anyio.run(rename, c, {"from": "S1", "to": "张三"})
    was = ledger()
    r = anyio.run(sentence_speaker, "1-1", {"conv": c, "to": "李四"})
    assert db.people()["张三"][0] == 2 and db.people()["李四"][0] == 1 and ledger() != was
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert ledger() == was and "李四" not in db.people()
