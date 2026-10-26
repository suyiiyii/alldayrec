"""Round-3 reviewer probes, with assertions for the corrected behavior."""
import json

import anyio
import numpy as np
import pytest

from alldayrec import audio, core, db, infer, late, late_merge, late_store
from test_backlog import EnergyVad, flac, heard, tone
from test_late_audio import A, D, E1, E2, conv, iso, ms, row, span, store


def test_seam_spanning_utterance_stays_whole(store, monkeypatch):
    monkeypatch.setattr(late_store, "MAX_LATE_AUDIO_MS", 30_000)
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    monkeypatch.setitem(core.state, "embed", lambda x: E1)
    np.savez(store / "owner-redimnet2.npz", owner=E2)
    x = np.zeros(60 * audio.SR, np.int16)
    x[25 * audio.SR:35 * audio.SR] = tone(1, 10)
    audio.save(D, ms(0), flac(x), "audio/flac")
    span(0, 60)
    utterances = []

    async def engine(x, speech, **kw):
        gs = heard(x)
        utterances.extend(g["end_ms"] - g["start_ms"] for g in gs)
        return [{**g, "engine": "test"} for g in gs]

    async def vectors(s):
        return {c["id"]: {"S1": E1} for c in db.conversations()}

    monkeypatch.setattr(late.asr, "engine_segs", engine)
    monkeypatch.setattr(late, "conversation_vectors", vectors)

    async def run():
        while s := late_store.claim():
            await late.process(s)

    anyio.run(run)
    rs = list(db.conn().execute("SELECT start,end,text FROM sentences WHERE late=1 ORDER BY start"))
    assert len(rs) == 1
    assert tuple(rs[0]) == (iso(25), iso(35), "1")
    assert utterances == [10_000]  # the fake engine sees the entire word/utterance once
    assert late_store.union(db.processed(D, ms(0), ms(60))) == [[ms(0), ms(60)]]


@pytest.mark.parametrize("existing", [False, True, "extension"])
def test_orphan_s1_is_not_mapped_by_next_parts_diarist(store, existing):
    with db.tx() as t:
        db.insert_sentences(t, [row(0, 5, "S1")])
        t.execute("UPDATE sentences SET late=1 WHERE id='f0'")
        if existing:
            conv(t, A, -20 if existing == "extension" else 20, -10 if existing == "extension" else 60)
    s = span(10, 40)
    changed, _ = late_merge.attach(s, [row(10, 40, "S1")], {"S1": E2},
                                   {A: {"S1": E1}} if existing else {})
    assert len(changed) == 1
    # Old and new diarists' S1s represent unrelated voices, including interval-based extension.
    speakers = dict(db.conn().execute("SELECT id,speaker FROM sentences WHERE late=1"))
    assert speakers["f0"] != speakers["f10"]
    assert changed[0]["start"] == (iso(-20) if existing == "extension" else iso(0))
    assert db.conn().execute("SELECT count(*) FROM sentences WHERE late=1").fetchone()[0] == 2


@pytest.mark.parametrize("speaker", ["S1", "S8", "Alice", "我"])
def test_carried_labels_stay_together_and_only_colliding_anonymous_labels_change(store, speaker):
    with db.tx() as t:
        db.insert_sentences(t, [row(0, 2, speaker), row(3, 5, speaker)])
        t.execute("UPDATE sentences SET late=1")
    s = span(10, 40)
    changed, _ = late_merge.attach(s, [row(10, 40, "S1")], {"S1": E2}, {})
    speakers = dict(db.conn().execute("SELECT id,speaker FROM sentences WHERE late=1"))
    assert speakers["f0"] == speakers["f3"]
    assert speakers["f0"] != speakers["f10"]
    assert (speakers["f0"] != speaker) == (speaker == "S1")
    assert len(changed) == 1 and changed[0]["start"] == iso(0)


def test_stuck_deferred_claim_is_read_only(store):
    span(5000, 5010)
    db.put_kv(f"finalized_to:{D}", iso(6000))
    db.put_kv(f"open_conversation:{D}", {"start": iso(4990), "end": iso(5005)})
    assert late_store.claim() is None
    stmts = []
    db.conn().set_trace_callback(stmts.append)
    try:
        for _ in range(3):
            assert late_store.claim() is None
    finally:
        db.conn().set_trace_callback(None)
    assert not any(s.startswith(("BEGIN", "UPDATE", "INSERT", "DELETE")) for s in stmts)
    db.put_kv(f"open_conversation:{D}", None)
    assert late_store.claim()["status"] == "processing"


def test_waiting_for_final_claim_is_read_only(store):
    span(0, 20)
    db.put_kv(f"finalized_to:{D}", iso(10))
    stmts = []
    db.conn().set_trace_callback(stmts.append)
    try:
        assert late_store.claim() is None
    finally:
        db.conn().set_trace_callback(None)
    assert not any(s.startswith("BEGIN") for s in stmts)


def test_legacy_split_keeps_retry_budget(store):
    db.conn().execute("INSERT INTO late_spans(device,start_ms,end_ms,first_ms,last_ms,ranges,ready,status,tries,next_ms,note) "
                      "VALUES (?,?,?,?,?,?,1,'waiting',2,0,'TimeoutError')",
                      (D, ms(0), ms(7200), 1, 1, json.dumps([[ms(0), ms(7200)]])))
    db.put_kv(f"finalized_to:{D}", iso(8000))
    s = late_store.claim()
    rs = list(db.conn().execute("SELECT * FROM late_spans"))
    assert len(rs) == 4 and s["tries"] == 3
    assert all(r["tries"] == 2 for r in rs if r["id"] != s["id"])


@pytest.mark.parametrize("silence", [False, True])
def test_seam_lookahead_is_bounded_and_preserves_budget(store, monkeypatch, silence):
    span(0, 3600)
    db.conn().execute("UPDATE late_spans SET tries=2,next_ms=7,note='TimeoutError'")
    s = late_store.claim()
    before = dict(s)
    reads = []
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)

    def read(device, a, b):
        reads.append((a, b))
        x = tone(1, (b-a)/1000)
        if silence:
            x[15 * audio.SR:30 * audio.SR] = 0
        return x

    monkeypatch.setattr(late, "read_exact", read)
    assert anyio.run(late.quiet_seam, s)
    assert reads == [(ms(1740), ms(1800))]
    assert s["tries"] == 3
    if silence:
        assert ms(1755) < s["end_ms"] < ms(1770)
        assert anyio.run(late.quiet_seam, s)  # the chosen seam stays fixed on retry
        assert len(reads) == 1
    else:
        assert s == before  # continuous speech: only the hard cap can bound the job
    pending = list(db.conn().execute("SELECT * FROM late_spans WHERE status='waiting'"))
    assert all(r["tries"] == 2 and r["next_ms"] == 7 and r["note"] == "TimeoutError" for r in pending)
    all_ranges = [r for job in db.conn().execute("SELECT ranges FROM late_spans") for r in json.loads(job[0])]
    assert late_store.union(all_ranges) == [[ms(0), ms(3600)]]
    assert sum(b-a for a,b in all_ranges) == 3600_000
    assert all(b-a >= late_store.MIN_LATE_MS for a,b in all_ranges)
    assert all(sum(b-a for a,b in json.loads(r[0])) <= late_store.MAX_LATE_AUDIO_MS
               for r in db.conn().execute("SELECT ranges FROM late_spans"))


def test_seam_lookahead_cannot_revive_reader_owned_audio(store, monkeypatch):
    span(0, 3600)
    s = late_store.claim()
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)

    def read(device, a, b):
        late_store.reset_reader(D, ms(60))
        return np.zeros(round((b-a)*audio.SR/1000), np.int16)

    monkeypatch.setattr(late, "read_exact", read)
    assert anyio.run(late.transcribe, s) == ([], {})
    rs = list(db.conn().execute("SELECT ranges,status FROM late_spans"))
    assert len(rs) == 1 and json.loads(rs[0][0]) == [[ms(0), ms(60)]]
    assert rs[0][1] == "waiting"
