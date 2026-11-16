"""db seg_emb, each final segment's voiceprint (tenant "seg_emb"): made by init on any store, again and again, its rows
kept; gone with its own sentence (a pass's replace_sentences, a joined row's later segments too, an overlapping row's
left) and with its audio (a trim's dropped pieces), another device's left; kept only beside its own row when that went
in, never holding the rows up; each part its own span and vector. Writing it, and the switch, in test_isolation.test_pipelines_side_by_side."""
import datetime as dt
import time

import anyio
import numpy as np
import pytest

from alldayrec import core
from alldayrec import db
from alldayrec import device_pipeline
from alldayrec import trim
from alldayrec.tenant import T
from test_backlog import DEVICE as MIC, PIECE_S as P, SR, flac, hear, rig, save, tone
from test_trim import DEVICE, PIECE_S, hour, vad


def ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


def put(device: str, a: int, b: int, part: int = 0, sentence: str = "f"):
    db.conn().execute("INSERT INTO seg_emb VALUES (?, ?, ?, ?, 'SPEAKER_0', 'm@0', ?, 0, ?)",
                      (device, a, b, part, np.ones(4, np.float16).tobytes(), sentence))


def left() -> list:
    return [tuple(r) for r in db.conn().execute("SELECT device, start_ms, part FROM seg_emb ORDER BY device, start_ms, part")]


def test_init_twice_keeps_rows(tmp_path):
    db.open_db(tmp_path / "live.db")
    put("mic", 1000, 2000)
    db.init()
    db.open_db(tmp_path / "live.db")
    assert left() == [("mic", 1000, 0)]


def test_replace_sentences_drops_the_range(tmp_path):
    """The voiceprints of the rows a pass replaces go: of its device's rows starting in [from, to), late ones kept."""
    db.open_db(tmp_path / "live.db")
    t0 = dt.datetime(2026, 10, 1, 10)
    at = lambda s: t0 + dt.timedelta(seconds=s)
    for s, dev in ((5, "mic"), (10, "mic"), (15, "mic"), (20, "mic"), (12, "phone"), (16, "mic")):
        db.insert_sentences(db.conn(), [{"id": f"f{s}", "start": core.iso(at(s)), "end": core.iso(at(s + 2)), "text": "a", "source": dev}])
    db.conn().execute("UPDATE sentences SET late = 1 WHERE id = 'f16'")
    for s in (5, 10, 15, 20):
        put("mic", ms(at(s)), ms(at(s + 2)), sentence=f"f{s}")
    put("mic", ms(at(10)), ms(at(11)), part=1, sentence="f10")
    put("mic", ms(at(16)), ms(at(17)), sentence="f16")  # a late row's (never written so: kept all the same)
    put("phone", ms(at(12)), ms(at(13)), sentence="f12")  # another device, the same minutes
    with db.tx() as t:
        db.replace_sentences(t, at(10).isoformat(timespec="milliseconds"), at(20).isoformat(timespec="milliseconds"), [], "mic")
    assert left() == [("mic", ms(at(5)), 0), ("mic", ms(at(16)), 0), ("mic", ms(at(20)), 0), ("phone", ms(at(12)), 0)]


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


def test_replace_sentences_drops_a_joined_rows_later_segments(tmp_path):
    """A row of several segments, finalized again by a shorter pass: all its voiceprints go with it, the next row's stay."""
    db.open_db(tmp_path / "live.db")
    t0 = dt.datetime(2026, 10, 1, 10)
    at = lambda s: core.iso(t0 + dt.timedelta(seconds=s))
    db.insert_sentences(db.conn(), [{"id": "f0", "start": at(0), "end": at(20), "speaker": "S1", "text": "a", "source": "mic"},
                                    {"id": "f25", "start": at(25), "end": at(27), "speaker": "S1", "text": "b", "source": "mic"}])
    for a, b, r in ((0, 5, "f0"), (15, 20, "f0"), (25, 27, "f25")):
        put("mic", ms(t0 + dt.timedelta(seconds=a)), ms(t0 + dt.timedelta(seconds=b)), sentence=r)
    with db.tx() as t:
        db.replace_sentences(t, at(0), at(10), [], "mic")
    assert left() == [("mic", ms(t0 + dt.timedelta(seconds=25)), 0)]


def test_rows_of_a_piece():
    """Each part of an engine segment its own span and vector, also when label() joins the parts again (the diarist
    hears one voice), with the row it went in; none for a segment too short to embed."""
    from alldayrec import infer, transcript
    from test_split_speakers import A, B, Dia, audio, embed
    x, g = audio((A, 3), (B, 3))
    short = {"start_ms": 6000, "end_ms": 6100, "text": "嗯", "speaker": "SPEAKER_1"}
    segs = [g, short]
    vs = transcript.voices(embed, segs, x)
    labelled = list(segs)
    transcript.label(Dia(same=True), labelled, vs)
    assert len(labelled) == 2 and "split" not in labelled[0], "joined again"
    t0 = dt.datetime(2026, 10, 1, 10)
    row = transcript.final_rows(labelled, {g["speaker"]: g["speaker"] for g in labelled}, t0, 10, since=0, edge=0)[2][0]
    rows = device_pipeline.seg_embs("mic", labelled, vs, lambda s: t0 + dt.timedelta(seconds=s))
    (p1, e1), (p2, e2) = vs[0]
    assert [r[:5] for r in rows] == [("mic", ms(t0) + p1["start_ms"], ms(t0) + p1["end_ms"], 1, "SPEAKER_0"),
                                     ("mic", ms(t0) + p2["start_ms"], ms(t0) + p2["end_ms"], 2, "SPEAKER_0")]
    assert p1["end_ms"] <= 3000 <= p2["start_ms"]
    assert [np.frombuffer(r[6], np.float16).tolist() for r in rows] == [np.float16(e1).tolist(), np.float16(e2).tolist()]
    assert {r[5] for r in rows} == {f"{infer.EMB}@{infer.MODELS[infer.EMB][:12]}"} and {r[7] for r in rows} == {0.0}
    assert {r[8] for r in rows} == {row["id"]} and row["start"] == core.iso(t0 + dt.timedelta(milliseconds=p1["start_ms"]))


def finalize(tmp_path, monkeypatch, before=lambda t0: None, want=("1", "2")):
    """Tones 1 and 2 in two pieces through a device's pipeline, its tenant keeping voiceprints; its final rows."""
    rig(tmp_path, monkeypatch, hear)
    T().seg_emb = True
    silence = np.zeros(P * SR, np.int16)
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 10 * P)
    for k in range(2):
        save(t0 + dt.timedelta(seconds=P * k), flac(np.concatenate([silence[:10 * SR], tone(k + 1, 3), silence[13 * SR:]])))
    before(t0)
    finals = lambda: [r for r in db.sentences(core.iso(t0), "~", source=MIC) if r["id"].startswith("f")]

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, MIC)
            for _ in range(120):
                if [r["text"] for r in finals()] == list(want):
                    break
                await anyio.sleep(0.5)
            tg.cancel_scope.cancel()
    anyio.run(run)
    rows = finals()
    assert [r["text"] for r in rows] == list(want), rows
    return t0, rows


@pytest.mark.parametrize("how", ["write", "build"])
def test_a_failure_keeps_the_final(tmp_path, monkeypatch, how):
    """The voiceprints' write (here the table refuses it) or their making fails: the rows go in all the same, the
    finalized time moves on, the pass's old voiceprints are gone all the same, the failure counted."""
    def before(t0):
        at = lambda s: core.iso(t0 + dt.timedelta(seconds=s))
        db.insert_sentences(db.conn(), [{"id": "f-old", "start": at(10.2), "end": at(11), "text": "old", "source": MIC}])
        put(MIC, ms(t0 + dt.timedelta(seconds=10.2)), ms(t0 + dt.timedelta(seconds=11)), sentence="f-old")  # an earlier pass's
        if how == "write":
            db.conn().execute("CREATE TRIGGER no_emb BEFORE INSERT ON seg_emb BEGIN SELECT RAISE(ABORT, 'synthetic'); END")
        else:
            monkeypatch.setattr(device_pipeline, "seg_embs", lambda *a: 1 / 0)
    t0, _ = finalize(tmp_path, monkeypatch, before)
    st = T().devices[MIC]
    assert st.done_to >= t0 + dt.timedelta(seconds=P) and db.kv(f"finalized_to:{MIC}")
    assert left() == [] and st.seg_emb_failed >= 2


def test_none_for_a_row_the_late_layer_protected(tmp_path, monkeypatch):
    """A final row the late layer's row overlaps does not go in (replace_sentences): nor do its voiceprints."""
    def before(t0):
        at = lambda s: core.iso(t0 + dt.timedelta(seconds=s))
        db.insert_sentences(db.conn(), [{"id": "l1", "start": at(11), "end": at(12), "speaker": "S1", "text": "late", "source": MIC}])
        db.conn().execute("UPDATE sentences SET late = 1 WHERE id = 'l1'")
    _, rows = finalize(tmp_path, monkeypatch, before, want=("2",))
    assert [(r[1], r[2]) for r in db.conn().execute("SELECT * FROM seg_emb")] == [tuple(ms(dt.datetime.fromisoformat(r[k])) for k in ("start", "end")) for r in rows]


def passes(segs: list[dict], vs: list, lo: float, hi: float, late=()):
    """One final pass over [lo, hi) s of segs (each its own voice, vs as voices() gives) as device_pipeline commits
    it: final_rows, replace_sentences, put_seg_embs; late: (start, end) s of late rows there before."""
    t0 = dt.datetime(2026, 10, 1, 10)
    at = lambda s: t0 + dt.timedelta(seconds=s)
    for a, b in late:
        db.insert_sentences(db.conn(), [{"id": f"l{a}", "start": core.iso(at(a)), "end": core.iso(at(b)), "speaker": "S9", "text": "late", "source": "mic"}])
        db.conn().execute("UPDATE sentences SET late = 1 WHERE id = ?", (f"l{a}",))
    rows = transcript_rows(segs, t0, hi)
    embs = device_pipeline.seg_embs("mic", segs, vs, at)
    with db.tx() as t:
        db.replace_sentences(t, core.iso(at(lo)), core.iso(at(hi)), rows, "mic")
        assert db.put_seg_embs(t, "mic", core.iso(at(lo)), core.iso(at(hi)), embs)
    return lambda s: ms(at(s))


def transcript_rows(segs, t0, hi):
    from alldayrec import transcript
    rows = transcript.final_rows(segs, {g["speaker"]: g["speaker"] for g in segs}, t0, hi, since=0, edge=0)[2]
    for r in rows:
        r["source"] = "mic"
    return rows


def overlapping(*spans):
    """Engine segments of different speakers (S1, S2, ...) that overlap, each unsplit with its voiceprint."""
    segs = [{"start_ms": a * 1000, "end_ms": b * 1000, "speaker": f"S{k}", "confidence": 1.0, "text": f"t{k}"} for k, (a, b) in enumerate(spans, 1)]
    return segs, [[(g, np.ones(4))] for g in segs]


def test_a_shorter_pass_keeps_an_overlapping_rows_voiceprint(tmp_path):
    """A [0, 20) and another speaker's B [15, 17) in; a pass over [0, 10) takes A back: A's voiceprint goes, B's (its row
    stays, its start outside the pass) stays."""
    db.open_db(tmp_path / "live.db")
    segs, vs = overlapping((0, 20), (15, 17))
    s = passes(segs, vs, 0, 40)
    assert left() == [("mic", s(0), 0), ("mic", s(15), 0)]
    passes([], [], 0, 10)
    assert [r["start"][-12:] for r in db.sentences("2026", "~", source="mic")] == ["10:00:15.000"]
    assert left() == [("mic", s(15), 0)]


def test_none_for_a_protected_row_that_starts_in_another(tmp_path):
    """A [0, 20) goes in, another speaker's B [10, 30) does not (a late row [25, 26) in it): B's voiceprint is not kept,
    though it starts in A."""
    db.open_db(tmp_path / "live.db")
    segs, vs = overlapping((0, 20), (10, 30))
    s = passes(segs, vs, 0, 40, late=[(25, 26)])
    assert [r["text"] for r in db.sentences("2026", "~", source="mic")] == ["t1", "late"]
    assert left() == [("mic", s(0), 0)]
