"""db seg_emb, each final segment's voiceprint (tenant "seg_emb"): made by init on any store, again and again, its rows
kept; gone with its sentences (a pass's replace_sentences) and with its audio (a trim's dropped pieces), another
device's left. Writing it, and the switch, in test_isolation.test_pipelines_side_by_side."""
import datetime as dt
import time

import numpy as np

from alldayrec import db
from alldayrec import trim
from test_trim import DEVICE, PIECE_S, hour, vad


def ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


def put(device: str, a: int, b: int, part: int = 0):
    db.conn().execute("INSERT INTO seg_emb VALUES (?, ?, ?, ?, 'SPEAKER_0', 'm@0', ?, 0)",
                      (device, a, b, part, np.ones(4, np.float16).tobytes()))


def left() -> list:
    return [tuple(r) for r in db.conn().execute("SELECT device, start_ms, part FROM seg_emb ORDER BY device, start_ms, part")]


def test_init_twice_keeps_rows(tmp_path):
    db.open_db(tmp_path / "live.db")
    put("mic", 1000, 2000)
    db.init()
    db.open_db(tmp_path / "live.db")
    assert left() == [("mic", 1000, 0)]


def test_replace_sentences_drops_the_range(tmp_path):
    db.open_db(tmp_path / "live.db")
    t0 = dt.datetime(2026, 10, 1, 10)
    at = lambda s: t0 + dt.timedelta(seconds=s)
    for s in (5, 10, 15, 20):
        put("mic", ms(at(s)), ms(at(s + 2)))
    put("mic", ms(at(10)), ms(at(11)), part=1)
    put("phone", ms(at(12)), ms(at(13)))  # another device, the same minutes
    with db.tx() as t:
        db.replace_sentences(t, at(10).isoformat(timespec="milliseconds"), at(20).isoformat(timespec="milliseconds"), [], "mic")
    assert left() == [("mic", ms(at(5)), 0), ("mic", ms(at(20)), 0), ("phone", ms(at(12)), 0)]


def test_trim_drops_with_the_audio(tmp_path, monkeypatch):
    t0, _ = hour(tmp_path, monkeypatch)  # tones in pieces 3 and 20 (of 10 s), kept with a piece around each
    s = lambda k, x=0.0: ms(t0) + int((k * PIECE_S + x) * 1000)
    put(DEVICE, s(3, 2), s(3, 6))       # in the tone: kept
    put(DEVICE, s(10, 1), s(10, 4))     # in a dropped piece
    put(DEVICE, s(14, 8), s(15, 3))     # its end in a dropped piece
    put(DEVICE, s(4, 8), s(5, 3))       # its end in a dropped piece (5), its start in a kept one (4)
    put("other", s(10, 1), s(10, 4))    # another device's
    hours = trim.plan(vad(), int(time.time() * 1000))
    assert trim.apply_one(hours[0])
    kept = {r[0] for r in db.conn().execute("SELECT start_ms FROM pieces")}
    assert {s(3), s(4)} <= kept and not {s(5), s(10), s(15)} & kept, sorted((k - ms(t0)) // 1000 for k in kept)
    assert left() == [(DEVICE, s(3, 2), 0), ("other", s(10, 1), 0)]


def test_rows_of_a_piece():
    """Each labelled segment's own vector from the diarist (by its mark), its part; none for one too short to embed
    (no mark) nor for one starting at hi or later (the next piece's)."""
    from alldayrec import device_pipeline, infer
    t0 = dt.datetime(2026, 10, 1, 10)
    at = lambda s: t0 + dt.timedelta(seconds=s)
    E = [np.eye(4)[i] for i in range(4)]
    segs = [{"start_ms": 0, "end_ms": 2000, "engine_speaker": "A"},
            {"start_ms": 2000, "end_ms": 2200, "engine_speaker": "A"},           # too short: no mark
            {"start_ms": 3000, "end_ms": 5000, "engine_speaker": "B", "split": 1},
            {"start_ms": 5000, "end_ms": 7000, "engine_speaker": "B", "split": 2},
            {"start_ms": 9000, "end_ms": 9500, "engine_speaker": "A"}]           # from hi on
    marks = [(1, 1), None, (3, 2), (4, 3), (4, 3)]
    rows = device_pipeline.seg_embs("mic", segs, marks, E, at, at(0), at(9))
    assert [r[:5] for r in rows] == [("mic", ms(at(0)), ms(at(2)), 0, "A"), ("mic", ms(at(3)), ms(at(5)), 1, "B"),
                                     ("mic", ms(at(5)), ms(at(7)), 2, "B")]
    assert [np.frombuffer(r[6], np.float16).tolist() for r in rows] == [E[0].tolist(), E[2].tolist(), E[3].tolist()]
    assert {r[5] for r in rows} == {f"{infer.EMB}@{infer.MODELS[infer.EMB][:12]}"} and {r[7] for r in rows} == {0.0}
