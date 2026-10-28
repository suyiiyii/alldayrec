"""The people bank as a ledger (bank.py): what a rename teaches is filtered, recorded per sentence and taken back
by the undo either way round; a card that sounds like two people is not named whole without asking, and 拆开 moves a
half out in one transaction with an undo; a NULL row that drifted is learnt again (relearn)."""
import subprocess
import sys

import anyio
import numpy as np
import pytest
from fastapi import HTTPException

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


def test_rename_stays_on_its_device(store):
    """Two devices heard the same minutes, each labelling on its own: a rename of one conversation's S1 and its undo
    change that device's rows only, not the other device's S1 (another person); only its rows teach the bank."""
    c = conv(1, "S1", [A, A, A], device="mic")
    with db.tx() as t:
        db.insert_sentences(t, [dict(r, id=f"p{k}", source="LR-P3") for k, r in enumerate(db.sentences("2026-10-01", "2026-10-02"))])

    def labels(device):
        return [r["speaker"] for r in db.sentences("2026-10-01", "2026-10-02", source=device)]
    r = anyio.run(rename, c, {"from": "S1", "to": "张三"})
    assert r["renamed"] == 3 and labels("mic") == ["张三"] * 3 and labels("LR-P3") == ["S1"] * 3
    assert db.people()["张三"][0] == 3
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert labels("mic") == ["S1"] * 3 and labels("LR-P3") == ["S1"] * 3 and "张三" not in db.people()


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


def test_split_named_card_and_undo(store):
    """拆开 of a named card: the half's labels back to fresh S<n>, kept apart from the name, what they taught gone, in
    one transaction; the undo puts all of it back."""
    convs = [conv(i, "S1", [v] * 3) for i, v in enumerate([A] * 12 + [B] * 9)]
    for c in convs:
        anyio.run(rename, c, {"from": "S1", "to": "张三"})
    dump = lambda: [sorted(map(tuple, db.conn().execute(f"SELECT * FROM {n}"))) for n in ("sentences", "conversations", "voices", "apart", "enrolled", "people")]
    was = dump()
    h = anyio.run(routes_speakers.speaker_halves, "张三")
    assert h["mixed"] and [x["count"] for x in h["halves"]] == [12, 9]
    r = anyio.run(routes_speakers.speaker_split, "张三", {"voices": h["halves"][1]["voices"]})
    assert r["moved"] == 9 and [db.conversation(c)["participants"] for c in convs[12:]] == [["S1"]] * 9
    assert db.apart() == {(f"{c}|S1", "张三") for c in convs[12:]} and db.people()["张三"][0] == 12 * 3
    g = {g["id"]: g for g in routes_speakers.speaker_groups()}
    assert len(g["张三"]["members"]) == 12 and not g["张三"]["suggested"]
    assert anyio.run(speaker_undo, {"undo": r["undo"]}) == {"undone": 9}
    assert dump() == was

    r = anyio.run(routes_speakers.speaker_name, {"group": "张三", "name": "李四"})
    assert r["mixed"] and db.conversation(convs[0])["participants"] == ["张三"], "asked, nothing renamed"
    r = anyio.run(routes_speakers.speaker_name, {"group": "张三", "name": "李四", "voices": h["halves"][0]["voices"]})
    assert r["renamed"] == 12 and db.conversation(convs[0])["participants"] == ["李四"]
    assert db.conversation(convs[-1])["participants"] == ["张三"]
    r = anyio.run(routes_speakers.speaker_name, {"group": "李四", "name": "王五", "all": True})
    assert r["renamed"] == 12


def test_split_unnamed_card_and_undo(store):
    """拆开 of an unnamed card: the half without the card's leader is kept apart from it, and is a card of its own;
    the undo drops those pins only."""
    u = bank.unit(A + 0.9 * B)  # 0.74 from A: one group at link 0.5
    convs = [conv(i, "S1", [v] * 3) for i, v in enumerate([A] * 3 + [u] * 2 + [-A] * 5)]  # -A: the recorder's mean near 0
    db.conn().execute("INSERT INTO apart VALUES (?, 'x')", (f"{convs[0]}|S1",))
    mine = lambda: sorted(len(g["members"]) for g in routes_speakers.speaker_groups() if any(m["conv"] in convs[:5] for m in g["members"]))
    g = next(g for g in routes_speakers.speaker_groups() if any(m["conv"] == convs[3] for m in g["members"]))
    assert len(g["members"]) == 5 and mine() == [5]
    with pytest.raises(HTTPException):
        anyio.run(routes_speakers.speaker_split, g["id"], {"voices": [f"{c}|S1" for c in convs[:5]]})  # all of it
    half = [f"{c}|S1" for c in convs[:3]]
    assert g["id"] in half  # the half with the leader: the other one is what is kept apart
    r = anyio.run(routes_speakers.speaker_split, g["id"], {"voices": half})
    assert r["moved"] == 2, "the half that goes, not the one asked for"
    assert db.apart() == {(f"{convs[0]}|S1", "x")} | {(f"{c}|S1", g["id"]) for c in convs[3:5]}
    assert mine() == [2, 3]
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert db.apart() == {(f"{convs[0]}|S1", "x")} and mine() == [5]


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


def test_relearn(store, tmp_path):
    """A NULL row that drifted is learnt again from the name's live voices: the odd one, a history one and those of a
    conversation learnt since (its ledger rows stay) left out; too few voices: kept. The dry run writes nothing; --apply
    writes, and again changes nothing."""
    with db.tx() as t:
        t.execute("INSERT INTO enrolled VALUES ('张三', NULL, NULL, 50, ?)", (db.blob(bank.unit(A + B)),))  # drifted to B
        t.execute("INSERT INTO enrolled VALUES ('张三', 'c-new', 7, 1, ?)", (db.blob(A),))  # learnt since
        t.execute("INSERT INTO enrolled VALUES ('李四', NULL, NULL, 9, ?)", (db.blob(C),))
        v = lambda conv, name, e, device="live": {"conv": conv, "label": name, "device": device, "k": 4, "e": e}
        db.insert_voices(t, [v(f"c{i}", "张三", A) for i in range(5)] + [v("c-odd", "张三", C), v("c-old", "张三", B, "mic"),
                             v("c-new", "张三", B)] + [v(f"d{i}", "李四", C) for i in range(4)])
        for name in ("张三", "李四"):
            bank.recompute(t, name)
    dump = lambda: [list(map(tuple, db.conn().execute(f"SELECT * FROM {n} ORDER BY {o}"))) for n, o in (("enrolled", "rowid"), ("people", "n DESC"))]
    was = dump()
    run = lambda *a: subprocess.run([sys.executable, "-m", "alldayrec.bank", "relearn", str(tmp_path / "t.db"), *a],
                                    capture_output=True, text=True, check=True).stdout
    out = run()
    assert "张三: n 50 -> 20, voices 5 used / 1 dropped, cosine old/new 0.717" in out and "李四: kept (n 9, 4 voices)" in out
    assert dump() == was, "a dry run"
    run("--apply")
    now = dump()
    assert [r[:4] for r in now[0]] == [("张三", None, None, 20), ("张三", "c-new", 7, 1), ("李四", None, None, 9)]
    assert db.people()["张三"][0] == 21 and np.allclose(bank.unit(db.people()["张三"][1]), A)
    assert now[0][2] == was[0][2] and now[1][1] == was[1][1], "李四 as it was"
    run("--apply")
    assert dump() == now


def test_music_row_given_to_someone(store):
    """A ♪ row (S?, music 1) given to someone is a voice: no music flag, learnt like any fixed sentence; the undo puts
    S? and the flag back, and takes the learning out."""
    from alldayrec.routes_sentences import sentence_speaker
    c = conv(1, "S1", [A, A, A, A])
    db.conn().execute("UPDATE sentences SET speaker = 'S?', music = 1 WHERE id = '1-2'")
    anyio.run(rename, c, {"from": "S1", "to": "张三"})
    was = ledger()
    with pytest.raises(HTTPException):
        apply_rename(c, "S?", "张三")  # every S? row of it at once: no
    r = anyio.run(sentence_speaker, "1-2", {"conv": c, "to": "张三"})
    row = lambda: tuple(db.conn().execute("SELECT speaker, music FROM sentences WHERE id = '1-2'").fetchone())
    assert row() == ("张三", None) and r["voiceprints"] == 1 and len(ledger()) == len(was) + 1
    anyio.run(speaker_undo, {"undo": r["undo"]})
    assert row() == ("S?", 1) and ledger() == was
