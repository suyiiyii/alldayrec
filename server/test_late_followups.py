"""#180: failed late spans can be queued again, one undecodable piece fails only itself, long .aac windows stop at the
registered length."""
import datetime as dt
import json
import logging
import subprocess
import time

import anyio
import numpy as np
import pytest

from alldayrec import audio, core, db, infer, late, late_backfill, late_store
from alldayrec.tenant import T
from test_backlog import EnergyVad, flac, heard, tone
from test_import_memory import client
from test_late_audio import D, ms, span, store  # noqa: F401


def failed_span(device, start_ms, seconds=10):
    with db.tx() as t:
        i = late_store.enqueue(t, device, start_ms, start_ms + seconds * 1000, round(time.time() * 1000), True)
        t.execute("UPDATE late_spans SET status='failed', tries=3, note='ValueError', next_ms=? WHERE id=?", (10**15, i))
    return i


def row(i):
    return dict(db.conn().execute("SELECT * FROM late_spans WHERE id=?", (i,)).fetchone())


def test_requeue_api_gives_failed_spans_new_tries(store):
    now = round(time.time() * 1000)
    mine, other = failed_span(D, now - 600_000), failed_span("other-recorder", now - 600_000)
    r = client(T()).post(f"/api/late-requeue?device={D}")
    assert r.status_code == 200 and r.json() == {"requeued": 1}
    s = row(mine)
    assert (s["status"], s["tries"], s["next_ms"], s["note"], s["ready"]) == ("waiting", 0, 0, None, 1)
    db.put_kv(f"finalized_to:{D}", core.iso(dt.datetime.now()))
    assert late_store.claim(now)["id"] == mine, "claimable again at once"
    assert row(other)["status"] == "failed", "another device's failed span stays"
    assert client(T()).post("/api/late-requeue").json() == {"requeued": 1}
    assert row(other)["status"] == "waiting"


def test_backfill_includes_failed_spans_only_when_asked(store, monkeypatch):
    monkeypatch.setattr(late_backfill, "candidates", lambda days: ({}, {}))
    now = round(time.time() * 1000)
    recent, old = failed_span(D, now - 3600_000), failed_span(D, now - 40 * 86400_000)
    assert "requeued_failed" not in anyio.run(late_backfill.scan, 30, False)
    assert row(recent)["status"] == "failed"
    assert anyio.run(late_backfill.scan, 30, True, True)["requeued_failed"] == 1
    assert row(recent)["status"] == "failed", "a dry run writes nothing"
    assert anyio.run(late_backfill.scan, 30, False, True)["requeued_failed"] == 1
    assert row(recent)["status"] == "waiting" and row(old)["status"] == "failed", "only the days asked for"


def test_one_undecodable_piece_fails_only_itself(store, monkeypatch, caplog):
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    monkeypatch.setitem(core.state, "embed", lambda x: np.eye(3, dtype=np.float32)[0])
    for start, idx in [(0, 1), (8, 3), (13, 2)]:
        audio.save(D, ms(start), flac(np.concatenate([tone(idx, 3), np.zeros(2 * audio.SR, np.int16)])), "audio/flac")
    async def engine(x, speech, **kw):
        return [{**g, "engine": "test"} for g in heard(x)]
    monkeypatch.setattr(late.asr, "engine_segs", engine)
    samples = audio.samples
    broken = True
    monkeypatch.setattr(audio, "samples", lambda p: None if broken and p["start_ms"] == ms(8) else samples(p))
    s = span(0, 18)
    caplog.set_level(logging.WARNING, "live")
    claimed = late_store.claim(s["next_ms"])
    anyio.run(late.process, claimed)
    assert {r["text"] for r in db.sentences("", "z")} == {"1", "2"}, "the rest of the span is transcribed"
    assert db.processed(D, ms(0), ms(18)) == [[ms(0), ms(8)], [ms(13), ms(18)]]
    left = row(s["id"])
    assert (left["status"], left["tries"], left["note"]) == ("waiting", 1, "undecodable audio")
    assert json.loads(left["ranges"]) == [[ms(8), ms(13)]], "only the bad piece stays"
    assert any(str(ms(8)) in r.getMessage() for r in caplog.records), "the log names the piece"
    for attempt in (2, 3):
        anyio.run(late.process, late_store.claim(row(s["id"])["next_ms"]))
        assert (row(s["id"])["tries"], row(s["id"])["status"]) == (attempt, "failed" if attempt == 3 else "waiting")
    assert late_store.requeue(D) == 1
    broken = False
    anyio.run(late.process, late_store.claim(round(time.time() * 1000)))
    assert row(s["id"])["status"] == "done"
    assert {r["text"] for r in db.sentences("", "z")} == {"1", "2", "3"}


@pytest.mark.parametrize("failure", ["none", "timeout"])
def test_without_bad_an_undecodable_piece_still_raises(store, monkeypatch, failure):
    audio.save(D, ms(0), flac(tone(1, 2)), "audio/flac")
    def fail(p):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ffmpeg", 1)
    monkeypatch.setattr(audio, "samples", fail)
    with pytest.raises(ValueError if failure == "none" else subprocess.TimeoutExpired):
        late.read_exact(D, ms(0), ms(2))
    bad = []
    assert not late.read_exact(D, ms(0), ms(2), bad).any() and bad == [[ms(0), ms(2)]]


def test_long_aac_window_stops_at_registered_length(store):
    body = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:r={audio.SR}:d=301",
                           "-c:a", "aac", "-f", "adts", "pipe:1"], capture_output=True, check=True).stdout
    seconds = audio.save(D, ms(0), body, "audio/aac")
    assert seconds > audio.WHOLE_S
    p = db.conn().execute("SELECT * FROM pieces WHERE device=?", (D,)).fetchone()
    registered = round(p["seconds"] * audio.SR)
    assert len(audio.piece_window(p, registered - audio.SR, 2 * audio.SR)) == audio.SR
    end = ms(0) + round(p["seconds"] * 1000)
    audio.save(D, end, flac(np.full(audio.SR, 222, np.int16)), "audio/flac")
    x = late.read_exact(D, end - 1000, end + 1000)
    assert (x[audio.SR:] == 222).all(), "the next piece's first 32 ms are its own"


def test_overlapping_copy_fills_what_a_bad_piece_misses(store, monkeypatch):
    """A [0,10) will not decode, B [5,15) overlaps it and does: only 0-5 s is bad, B's audio fills 5-15 s."""
    audio.save(D, ms(0), flac(np.full(10 * audio.SR, 111, np.int16)), "audio/flac")
    audio.save(D, ms(5), flac(np.full(10 * audio.SR, 222, np.int16)), "audio/flac")
    samples = audio.samples
    monkeypatch.setattr(audio, "samples", lambda p: None if p["start_ms"] == ms(0) else samples(p))
    bad = []
    x = late.read_exact(D, ms(0), ms(15), bad)
    assert bad == [[ms(0), ms(5)]]
    assert not x[:5 * audio.SR].any() and (x[5 * audio.SR:] == 222).all()
