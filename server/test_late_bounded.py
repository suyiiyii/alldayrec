"""Should-fix regressions from the second late-audio review at 67580e7."""
import datetime as dt
import json

import anyio
import numpy as np
import pytest

from alldayrec import audio, core, db, infer, late, late_backfill, late_merge, late_store, transcript
from alldayrec.tenant import T
from test_backlog import EnergyVad, flac, tone
from test_late_audio import A, B, D, E1, E2, conv, iso, ms, row, span, store


CAP_MS = 30 * 60 * 1000


def jobs():
    return [dict(r) for r in db.conn().execute("SELECT * FROM late_spans ORDER BY start_ms,id")]


def late_rows():
    return [dict(r) for r in db.conn().execute("SELECT * FROM sentences WHERE source=? AND late=1 ORDER BY start", (D,))]


def assert_bounded(expected):
    rs = [x for r in jobs() for x in json.loads(r["ranges"])]
    assert len(jobs()) >= 6
    assert all(sum(b-a for a,b in json.loads(r["ranges"])) <= CAP_MS for r in jobs())
    assert late_store.union(rs) == expected
    assert sum(b-a for a,b in rs) == sum(b-a for a,b in expected)


def test_deferred_open_conversation_does_not_starve_finished_summary(store):
    T().late_queued.clear()
    while T().summaries[1].statistics().current_buffer_used:
        T().summaries[1].receive_nowait()
    with db.tx() as t:
        conv(t, A, 0, 300)
    near_open = span(5000, 5010)
    first = span(100, 120)
    db.put_kv(f"finalized_to:{D}", iso(6000))
    db.put_kv(f"open_conversation:{D}", {"start": iso(4990), "end": iso(5005)})
    s = late_store.claim()
    assert s["id"] == first["id"]
    late_merge.attach(s, [row(100, 120)], {"S1": E1}, {})
    for _ in range(3):
        assert late_store.claim() is None
        late_store.queue_summaries()
    assert db.conn().execute("SELECT status FROM late_spans WHERE id=?", (near_open["id"],)).fetchone()[0] == "deferred"
    assert T().summaries[1].statistics().current_buffer_used == 1
    assert T().summaries[1].receive_nowait()["id"] == A


def test_three_hour_live_stretch_is_bounded(store, monkeypatch):
    monkeypatch.setattr(audio, "decode", lambda body, **kw: np.zeros(60 * audio.SR, np.int16))
    monkeypatch.setattr(audio, "health", lambda x: (0, 0, 0, 0))
    T().read_to[D] = ms(20_000)
    for i in range(180):
        audio.save(D, ms(i*60), f"piece-{i}".encode(), "audio/wav")
    assert_bounded([[ms(0), ms(10800)]])


def test_three_hour_backfill_plan_is_bounded(store, monkeypatch):
    base = round(dt.datetime.combine(dt.date.today(), dt.time(10)).timestamp()*1000)
    # One late 3 h file, followed by a file received earlier.
    for offset, seconds, receipt in [(0, 10800, 200), (10800, 60, 100)]:
        db.conn().execute("INSERT INTO pieces(device,start_ms,seconds,path,received_ms) VALUES (?,?,?,?,?)",
                          (D, base+offset*1000, seconds, "fixture", receipt))
    grouped, _ = late_backfill.candidates(1)
    assert len(grouped[D]) >= 6
    assert all(sum(b-a for a,b in rs) <= CAP_MS for rs in grouped[D])
    # Exercise scan's VAD-checked plan, accounting and enqueue path without allocating 3 h PCM.
    class Speech:
        async def apush(self, at, x):
            pass
    monkeypatch.setattr(late_backfill, "Segmenter", Speech)
    monkeypatch.setattr(late_backfill, "read_exact", lambda device, a, b: np.zeros(round((b-a)*16), np.int16))
    monkeypatch.setattr(late_backfill, "speech_in", lambda seg, start, n: [(0, n/16000)])
    before = list(db.conn().iterdump())
    dry = anyio.run(late_backfill.scan, 1, True)
    assert list(db.conn().iterdump()) == before
    actual = anyio.run(late_backfill.scan, 1, False)
    assert dry["days"] == actual["days"]
    assert actual["enqueued"] == dry["days"][0]["spans"] >= 6
    assert actual["estimated_conversations"] == dry["estimated_conversations"] == 1
    assert_bounded([[base, base+10800_000]])


def test_old_oversized_span_is_split_before_claim(store):
    db.conn().execute("INSERT INTO late_spans(device,start_ms,end_ms,first_ms,last_ms,ranges,ready) VALUES (?,?,?,?,?,?,1)",
                      (D, ms(0), ms(10800), 1, 1, json.dumps([[ms(0), ms(10800)]])))
    db.put_kv(f"finalized_to:{D}", iso(12000))
    s = late_store.claim()
    assert sum(b-a for a,b in json.loads(s["ranges"])) <= CAP_MS
    assert_bounded([[ms(0), ms(10800)]])


def test_part_four_timeout_keeps_commits_and_retry_bridges_seams(store, monkeypatch):
    with db.tx() as t:
        conv(t, A, -30, -10)
        conv(t, B, 10810, 10830)
    span(0, 10800)
    attempts = []
    async def transcribe(s):
        attempts.append(s["start_ms"])
        if len(attempts) == 4:
            # The processor's actual cancellation path, after three successful commits.
            assert sum(r["status"] == "done" for r in jobs()) == 3
            assert len(late_rows()) == 90
            await anyio.sleep_forever()
        rs = [row(i, i+20) for i in range(0, 10800, 60)
              if any(a <= ms(i) < ms(i+20) <= b for a,b in json.loads(s["ranges"]))]
        return rs, {"S1": E1}
    async def vectors(s):
        return {c["id"]: {"S1": E1} for c in db.conversations()}
    monkeypatch.setattr(late, "transcribe", transcribe)
    monkeypatch.setattr(late, "conversation_vectors", vectors)
    monkeypatch.setattr(late, "LATE_PROCESS_TIMEOUT_S", 0.02)
    original_sleep = anyio.sleep
    async def stop_when_idle(seconds):
        if seconds == 2:
            raise RuntimeError("test idle")
        await original_sleep(seconds)
    monkeypatch.setattr(late.anyio, "sleep", stop_when_idle)
    with pytest.raises(RuntimeError, match="test idle"):
        anyio.run(late.processor)
    failed = [r for r in jobs() if r["note"] == "TimeoutError"]
    assert len(failed) == 1 and failed[0]["start_ms"] == ms(5400)
    assert sum(r["status"] == "done" for r in jobs()) == 5
    assert len(late_rows()) == 150
    # Retry the missing middle after the later parts have already formed their own conversation.
    s = late_store.claim(failed[0]["next_ms"])
    anyio.run(late.process, s)
    rs = late_rows()
    assert [r["id"] for r in rs] == [f"f{i}" for i in range(0, 10800, 60)]
    assert len(rs) == len({r["id"] for r in rs}) == 180
    assert late_store.union(db.processed(D, ms(0), ms(10800))) == [[ms(0), ms(10800)]]
    assert len(db.conversations()) == 1
    assert db.resolve_conversation(B) == A
    assert all(r["status"] == "done" for r in jobs())


def test_idle_claim_does_not_take_writer_lock(store):
    statements = []
    db.conn().set_trace_callback(statements.append)
    try:
        for _ in range(3):
            assert late_store.claim() is None
    finally:
        db.conn().set_trace_callback(None)
    assert not any(s.startswith("BEGIN") for s in statements)


def test_regrouping_does_not_create_unrelated_conversation(store):
    # Old parts left two short orphan rows; a live conversation was committed between them.
    with db.tx() as t:
        conv(t, A, 0, 20)
        db.insert_sentences(t, [row(-5, 0), row(20, 25)])
        t.execute("UPDATE sentences SET late=1 WHERE id IN ('f-5','f20')")
    s = span(10000, 10020)
    changed, removed = late_merge.attach(s, [row(10000, 10020)], {"S1": E1}, {})
    assert not removed and len(changed) == 1
    assert len(db.conversations()) == 2
    assert db.conversation(A)["start"] == iso(0)


@pytest.mark.parametrize("ranges", [
    [[0, CAP_MS+1]],
    [[0, CAP_MS-600], [CAP_MS, CAP_MS+700]],
    [[0, CAP_MS-100], [CAP_MS, CAP_MS+1000]],
])
def test_cap_preserves_small_tails_and_covered_holes(store, ranges):
    with db.tx() as t:
        late_store.enqueue_ranges(t, D, ranges, 1, ready=True)
    rs = [x for r in jobs() for x in json.loads(r["ranges"])]
    assert len(jobs()) == 2
    assert late_store.union(rs) == ranges
    assert late_store.substantial(rs) == rs
    assert all(sum(b-a for a,b in json.loads(r["ranges"])) <= CAP_MS for r in jobs())


def test_split_transcription_reads_seam_samples_once(store, monkeypatch):
    # Scale only the cap; exercise real reading, VAD, ASR windows, final rows and attach.
    monkeypatch.setattr(late_store, "MAX_LATE_AUDIO_MS", 30_000, raising=False)
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    monkeypatch.setattr(transcript, "TURN_GAP_S", 0)  # retain each fake engine word as a row
    monkeypatch.setitem(core.state, "embed", lambda x: E1)
    np.savez(store / "owner-redimnet2.npz", owner=E2)
    x = np.zeros(60*audio.SR, np.int16)
    for idx, a, b in [(1, 20, 25), (2, 28, 30), (3, 30, 32), (4, 35, 40)]:
        x[a*audio.SR:b*audio.SR] = tone(idx, b-a)
    audio.save(D, ms(0), flac(x), "audio/flac")
    span(0, 60)
    read, lookahead, exact, choosing = [], [], late.read_exact, False
    def read_exact(device, a, b):
        y = exact(device, a, b)
        if choosing:
            lookahead.append((a, b))
        else:
            read.append(y)
        return y
    choose = late.quiet_seam
    async def quiet_seam(s):
        nonlocal choosing
        choosing = True
        try:
            return await choose(s)
        finally:
            choosing = False
    async def engine(x, speech, **kw):
        # Read amplitude changes as words too: adjacent tones 2 and 3 are one VAD utterance,
        # and a quiet seam must no longer depend on the old hard cut to distinguish them.
        frames = np.abs(x[:len(x) // 160 * 160].astype(np.int32)).reshape(-1, 160)
        words = np.rint((np.median(frames, axis=1) - 1000) / 50).astype(int)
        words[frames.mean(axis=1) < 500] = 0
        edges = np.r_[0, np.flatnonzero(np.diff(words)) + 1, len(words)]
        return [{"start_ms": int(a) * 10, "end_ms": int(b) * 10, "speaker": "SPEAKER_0",
                 "text": str(words[a]), "engine": "test"}
                for a,b in zip(edges, edges[1:]) if words[a] > 0]
    async def vectors(s):
        return {c["id"]: {"S1": E1} for c in db.conversations()}
    monkeypatch.setattr(late, "read_exact", read_exact)
    monkeypatch.setattr(late, "quiet_seam", quiet_seam)
    monkeypatch.setattr(late.asr, "engine_segs", engine)
    monkeypatch.setattr(late, "conversation_vectors", vectors)
    async def run():
        while s := late_store.claim():
            await late.process(s)
    anyio.run(run)
    assert np.array_equal(np.concatenate(read), x)
    assert lookahead and all(0 < b-a <= 60_000 for a,b in lookahead)
    assert len({b for _, b in lookahead}) == len(lookahead)
    rs = late_rows()
    assert [r["text"] for r in rs] == ["1", "2", "3", "4"]
    assert len({r["id"] for r in rs}) == 4
    assert all(a["end"] <= b["start"] for a,b in zip(rs, rs[1:]))
    assert len(db.conversations()) == 1
    assert late_store.union(db.processed(D, ms(0), ms(60))) == [[ms(0), ms(60)]]
