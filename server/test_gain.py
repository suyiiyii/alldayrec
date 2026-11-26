"""Per-device gain (gain.py): rows hold by time, each column on its own, each sample at its own time's offset (a
piece, a cut or a listening poll over a since_ms split there); one offset at every decoded exit, once, for the models
and the people listening alike (a recorder's PLAY_GAIN_DB only where it has none); float32 on to the models, int16
(soft-limited past 0.9 of full scale, counted) only for a WAV and an ASR engine, rounded once; the stored bytes and
levels as they came; reported capture gains kept only when they change, and only gains; each tenant its own; the
schema made once however often it starts; the CLI; the history voices' clips, the infer service and replay_case."""
import datetime as dt
import json
import logging
import math
import os
import subprocess
import time

import anyio
import numpy as np
import pytest
from starlette.testclient import TestClient

from alldayrec import asr
from alldayrec import audio
from alldayrec import compute
from alldayrec import core
from alldayrec import db
from alldayrec import gain
from alldayrec import infer
from alldayrec import infer_server
from alldayrec import late
from alldayrec import routes_audio
from alldayrec import selfvoice
from alldayrec import server
from alldayrec import squeeze
from alldayrec import tenant
from alldayrec.tenant import T
from test_gain_parity import T0, at, clip_rig, pcm, speech, streamed

SR = audio.SR
DB2 = 20 * math.log10(2)  # x2: 1000 comes out 2000
up = lambda x, g: x.astype(np.float32) * np.float32(10 ** (g / 20))  # what the models get: float32, not rounded
flat = lambda s, v=1000: np.full(int(s * SR), v, np.int16)


def client(t) -> TestClient:
    core.TENANTS[:] = [t]  # no token: no auth
    return TestClient(server.app)  # no `with`: the lifespan does not start


@pytest.fixture(autouse=True)
def tenants(monkeypatch):
    monkeypatch.setattr(core, "TENANTS", [])


def test_time_and_columns(tmp_path):
    db.open_db(tmp_path / "t.db")
    d = "LR-G1-TEST"
    assert gain.offset_db(d, T0) is None and gain.capture_db(d, T0) is None, "no row: none (0 dB)"
    gain.put(d, T0, 6.0, note="cal 1")
    gain.put(d, T0 + 60_000, -3.0, capture=4.0)
    assert gain.report(d, T0 + 30_000, 8)
    assert gain.offset_db(d, T0 - 1) is None and gain.offset_db(d, T0) == 6 and gain.offset_db(d, T0 + 59_999) == 6
    assert gain.offset_db(d, T0 + 30_000) == 6, "a reported capture gain hides no offset"
    assert gain.offset_db(d, T0 + 60_000) == -3 and gain.offset_db(d, 1 << 50) == -3
    assert gain.capture_db(d, T0 + 29_999) is None and gain.capture_db(d, T0 + 30_000) == 8 and gain.capture_db(d, T0 + 60_000) == 4
    assert gain.offset_db("LR-OTHER", T0 + 60_000) is None, "each device its own"
    gain.put(d, T0, 7.0)
    assert gain.offset_db(d, T0) == 7 and len(gain.rows(d)) == 3, "set again at the same time: replaced"
    assert gain.segments(d, T0 - 500, 2 * SR) == [(0, 8000, None), (8000, 2 * SR, 7.0)], "split where it starts"


def test_reported(tmp_path):
    c = client(db.open_db(tmp_path / "t.db"))
    d = "LR-G2-TEST"
    for hw in ({"battery": 50}, {"battery": 50, "mic_gain_db": None}, {"mic_gain_db": "6"}, {"mic_gain_db": True},
               {"mic_gain_db": 1e9}):
        assert c.post("/api/device-status", params={"device": d}, json=hw).status_code == 200
    r = c.post("/api/device-status", params={"device": d}, content='{"mic_gain_db": ' + "9" * 400 + "}",
               headers={"content-type": "application/json"})
    assert r.status_code == 200, "a 400-digit JSON integer: no gain, no 500"
    assert gain.rows(d) == [], "absent, null or not a gain: nothing"
    for v in (6.5, 6.5, 0, 0, 6.5):
        assert c.post("/api/device-status", params={"device": d}, json={"battery": 50, "mic_gain_db": v}).status_code == 200
    assert [(r["capture_db"], r["offset_db"], r["source"]) for r in gain.rows(d)] == [(6.5, None, "reported"), (0, None, "reported"),
                                                                                   (6.5, None, "reported")], "a row each change"
    assert not gain.report(d, T0, float("nan")) and not gain.report(d, T0, float("inf")) and not gain.report(d, T0, 10 ** 400)
    phone = "phone-g2"
    for i, v in enumerate((None, 3.0, 3.0, -2.0, "nan", "inf", "1e100")):  # a phone's pieces, the last two with another gain
        q = {"device": phone, "start": T0 + i * 1000} | ({} if v is None else {"capture_gain_db": v})
        r = c.post("/api/chunks", params=q, content=audio.wav(speech(1, i)), headers={"content-type": "audio/wav"})
        assert r.status_code == 200, r.text
    assert [(r["since_ms"], r["capture_db"]) for r in gain.rows(phone)] == [(T0 + 1000, 3.0), (T0 + 3000, -2.0)]
    assert gain.offset_db(phone, T0 + 3000) is None, "a client's report never changes the audio"
    got = c.get("/api/device-gain").json()
    assert got["target_dbov"] == -26 and len(got["rows"]) == 5


def test_same_ms(tmp_path):
    """Two reports in one ms: the second is kept (it was lost); a manual capture gain at the same ms wins."""
    db.open_db(tmp_path / "t.db")
    assert gain.report("LR-MS", T0, 1) and gain.report("LR-MS", T0, 2)
    assert gain.capture_db("LR-MS", T0) == 2 and len(gain.rows("LR-MS")) == 1
    gain.put("LR-MS", T0, None, capture=5.0)
    assert gain.capture_db("LR-MS", T0) == 5, "written down beats reported"
    assert not gain.report("LR-MS", T0, 5), "the same as known: nothing"


def test_every_exit_once(tmp_path, monkeypatch):
    """+6 dB at samples, fresh, the stream, the late read, cut s16le, pcm and wav, listening: each exactly +6 dB, never
    twice; float32 to the models, int16 to be played; a recorder with an offset plays at it alone (no PLAY_GAIN_DB);
    X-Gain-Db says which. Long imports too."""
    db.open_db(tmp_path / "t.db")
    x = {d: speech(12, k) for k, d in enumerate(("phone-g3", "LR-G3-TEST", "import-g3"))}
    monkeypatch.setattr(audio, "WHOLE_S", 10)  # import-g3's 12 s: a long import (piece_window, piece_blocks)
    for d, y in x.items():
        assert audio.save(d, T0, audio.wav(y), "audio/wav") == 12
    for d in x:
        gain.put(d, 0, 6.0)
    a, b = T0 + 1000, T0 + 4000
    for d, y in x.items():
        g = up(y, 6)
        p = db.conn().execute("SELECT * FROM pieces WHERE device = ?", (d,)).fetchone()
        if d != "import-g3":
            assert np.array_equal(audio.samples(p), g) and np.array_equal(audio.fresh(p, T0 + 500)[1], g[SR // 2:]), d
        assert np.array_equal(audio.piece_window(p, SR, 2 * SR), g[SR:3 * SR]), d
        assert np.array_equal(anyio.run(streamed, d, len(y))[:len(y)], g), d
        got = late.read_exact(d, a, b)
        assert got.dtype == np.float32 and np.array_equal(got, g[SR:4 * SR]), d
        window = g[SR - SR // 5:4 * SR + SR // 5]
        assert np.array_equal(audio.cut(at(a), at(b), "pcm", d), window), d
        assert np.array_equal(np.frombuffer(audio.cut(at(a), at(b), "s16le", d), np.int16), gain.to16(window)), d
        assert np.array_equal(pcm(audio.cut(at(a), at(b), "wav", d)), gain.to16(window)), f"{d}: played as the models hear it"
        assert np.array_equal(audio.newer(d, None)[2], gain.to16(g)), d
        wav = anyio.run(routes_audio.audio_wav, at(a).isoformat(), at(b).isoformat(), d)
        assert wav.headers["x-gain-db"] == "6", d
    monkeypatch.setattr(audio, "WHOLE_S", 300)
    assert audio.play_db("LR-NONE", T0) == audio.PLAY_GAIN_DB and audio.play_db("phone-none", T0) == 0, "no offset: as on main"


def test_playback_over_first_offset(tmp_path):
    """A recorder's first second without an offset, the next with one: played, the first PLAY_GAIN_DB louder, the
    second at its offset alone (it was PLAY_GAIN_DB on top of the offset), in a cut and in listening."""
    db.open_db(tmp_path / "t.db")
    d = "LR-MIX-TEST"
    for n in range(2):
        assert audio.save(d, T0 + n * 1000, audio.wav(flat(1)), "audio/wav")
    gain.put(d, T0 + 1000, DB2)
    loud = round(1000 * 10 ** (audio.PLAY_GAIN_DB / 20))
    model = np.frombuffer(audio.cut(at(T0 + 200), at(T0 + 1800), "s16le", d), np.int16)  # from T0
    assert model[8000] == 1000 and model[24000] == 2000
    played = pcm(audio.cut(at(T0 + 200), at(T0 + 1800), "wav", d))
    assert played[8000] == loud and played[24000] == 2000, (played[8000], played[24000])
    listen = audio.newer(d, T0 - 1)[2]
    assert listen[8000] == loud and listen[24000] == 2000, (listen[8000], listen[24000])
    later = anyio.run(routes_audio.audio_wav, at(T0 + 1200).isoformat(), at(T0 + 1600).isoformat(), d)
    assert later.headers["x-gain-db"] == f"{DB2:g}" and np.all(pcm(later.body) == 2000)


def test_offset_inside_a_piece(tmp_path, monkeypatch):
    """since_ms inside an upload: the samples from it on have it, those before do not, in samples(), the late read,
    a long import's window and its blocks (the piece's start decided all of it)."""
    db.open_db(tmp_path / "t.db")
    d = "phone-within"
    assert audio.save(d, T0, audio.wav(flat(4)), "audio/wav")
    gain.put(d, T0 + 1000, DB2)
    p = db.conn().execute("SELECT * FROM pieces").fetchone()
    x = audio.samples(p)
    assert x[SR - 1] == 1000 and x[SR] == 2000 and x[2 * SR] == 2000, "the sample at since_ms on"
    assert np.all(late.read_exact(d, T0 + 2000, T0 + 2500) == 2000)
    y = late.read_exact(d, T0 + 500, T0 + 1500)
    assert np.all(y[:SR // 2] == 1000) and np.all(y[SR // 2:] == 2000)
    monkeypatch.setattr(audio, "WHOLE_S", 2)  # a long import
    assert np.all(audio.piece_window(p, 2 * SR, 100) == 2000)
    w = audio.piece_window(p, SR - 50, 100)
    assert np.all(w[:50] == 1000) and np.all(w[50:] == 2000)
    with audio.piece_blocks(p, T0 + 500) as (blocks, skip):
        z = np.concatenate(list(blocks))
    assert skip == SR // 2 and np.all(z[:SR // 2] == 1000) and np.all(z[SR // 2:] == 2000)
    with audio.piece_blocks(p, T0 + 2000) as (blocks, _):
        assert next(blocks)[0] == 2000


def test_header_first_sample(tmp_path):
    """X-Gain-Db: the gain of the WAV's first sample of audio, not of the padding before it (it said 12 there)."""
    db.open_db(tmp_path / "t.db")
    d = "LR-HEADER-TEST"
    assert audio.save(d, T0, audio.wav(flat(1)), "audio/wav")
    gain.put(d, T0, DB2)
    r = anyio.run(routes_audio.audio_wav, at(T0).isoformat(), at(T0 + 500).isoformat(), d)
    assert r.headers["x-gain-db"] == f"{DB2:g}" and pcm(r.body)[SR // 3] == 2000


def test_models_unlimited_played_limited(tmp_path):
    """Past full scale: the models get the float32 product as it is; int16 (played) is the product rounded up to 0.9
    of full scale, every sample, soft-limited past it, under full scale, monotonic, those counted."""
    t = db.open_db(tmp_path / "t.db")
    d = "LR-G4-TEST"
    y = speech(3, 4, -20)  # loud: +12 dB takes its peaks past full scale
    body = audio.wav(y)
    assert audio.save(d, T0, body, "audio/wav") == 3
    p = db.conn().execute("SELECT * FROM pieces WHERE device = ?", (d,)).fetchone()
    stored = (p["rms"], p["hum"], p["zero_ms"], p["full"], p["env"])
    gain.put(d, 0, 12.0)
    f = up(y, 12)
    z = audio.samples(p)
    assert np.array_equal(z, f) and np.abs(z).max() > 32768, "the models: past full scale, not limited"
    assert not t.gain_limited, "nothing counted: nothing was made int16"
    w = pcm(audio.cut(at(T0 + 200), at(T0 + 2800), "wav", d))
    f = f[:len(w)].astype(np.float64)  # the cut starts at T0 (its 0.2 s before T0 + 200)
    small = np.abs(f) <= gain.KNEE * 32768
    assert np.array_equal(w[small], np.round(f[small]).astype(np.int16)), "up to 0.9 of full scale: every sample only rounded"
    assert t.gain_limited[d] == np.count_nonzero(~small) > 0, "counted: the samples into the limiter"
    assert np.all(np.abs(w[~small].astype(int)) <= np.abs(f[~small]) + 0.5) and np.abs(w.astype(int)).max() <= 32767
    order = np.argsort(f)
    assert np.all(np.diff(w[order].astype(int)) >= 0), "monotonic: louder stays louder"
    assert audio.read(p) == body, "the stored bytes as uploaded"
    q = db.conn().execute("SELECT * FROM pieces WHERE device = ?", (d,)).fetchone()
    assert (q["rms"], q["hum"], q["zero_ms"], q["full"], q["env"]) == stored
    gain.put(d, 0, 0.0)
    x0 = audio.decode(body)
    assert np.array_equal(audio.samples(p), x0) and audio.samples(p).dtype == np.int16, "0 dB: as decoded"
    assert gain.apply(x0, d, T0) is x0, "0 dB: the very array"


def test_to16():
    k = gain.KNEE * 32768
    y = np.array([0.4, -1.6, 1000.5, 29000.0, k, -k, k + 1, 32768.0, 33000.0, -40000.0, 1e9], np.float32)
    z = gain.to16(y)
    assert z.dtype == np.int16 and np.array_equal(z[:6], np.round(y[:6].astype(np.float64)).astype(np.int16)), "<= the knee: rounded"
    assert np.all(np.abs(z[6:].astype(int)) < np.abs(y[6:])) and np.all(np.abs(z[6:].astype(int)) <= 32767)
    assert np.all(np.diff(z[[6, 7, 8, 10]].astype(int)) >= 0) and z[10] == 32767
    x = flat(1)
    assert gain.to16(x) is x, "int16: itself"


def test_negative_offset_lossless(tmp_path):
    """-25 dB (a loud phone): the models get the exact float32 product, the low bits kept (they were rounded away)."""
    db.open_db(tmp_path / "t.db")
    d = "phone-quiet"
    y = speech(2, 9, -45)
    assert audio.save(d, T0, audio.wav(y), "audio/wav")
    gain.put(d, 0, -25.0)
    got = late.read_exact(d, T0, T0 + 2000)
    assert got.dtype == np.float32 and np.array_equal(got, up(y, -25))
    assert np.count_nonzero(got != np.round(got)) > len(got) // 2, "not rounded to int16"


def test_asr_rounded_once(tmp_path, monkeypatch):
    """-25 dB through the late read to an ASR engine: squeeze and lift on float, rounded to int16 once, before the
    engine: what it is sent is, sample for sample, the float product lifted and only then rounded."""
    db.open_db(tmp_path / "t.db")
    d = "phone-asr"
    y = speech(10, 3, -30)
    assert audio.save(d, T0, audio.wav(y), "audio/wav")
    gain.put(d, 0, -25.0)
    sent = []

    async def fake(x, partial=None):
        sent.append(x)
        return []
    monkeypatch.setitem(asr.PROVIDERS, "fake", fake)
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "")
    x = late.read_exact(d, T0, T0 + 10_000)
    anyio.run(lambda: asr.engine_segs(x, [(0.0, 10.0)], engine="fake", device=d))
    lift = squeeze.LIFT_MAX_DB  # -55 dBFS: lifted by all of it, no peak near -1 dBFS
    ref = np.round(up(y, -25).astype(np.float64) * 10 ** (lift / 20)).astype(np.int16)
    assert sent[0].dtype == np.int16 and np.array_equal(sent[0], ref)
    twice = np.round(np.round(up(y, -25)).astype(np.float64) * 10 ** (lift / 20)).astype(np.int16)
    assert np.count_nonzero(twice != ref) > len(ref) // 10, "rounding before the lift would differ: the test sees it"


def test_asr_limited_once(tmp_path, monkeypatch):
    """+12 dB on loud audio to an ASR engine: lift adds nothing, to16 holds what passes 0.9 of full scale (counted),
    every sample under it only rounded."""
    t = db.open_db(tmp_path / "t.db")
    d = "phone-loud"
    y = speech(3, 4, -20)
    assert audio.save(d, T0, audio.wav(y), "audio/wav")
    gain.put(d, 0, 12.0)
    sent = []

    async def fake(x, partial=None):
        sent.append(x)
        return []
    monkeypatch.setitem(asr.PROVIDERS, "fake", fake)
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "")
    anyio.run(lambda: asr.engine_segs(late.read_exact(d, T0, T0 + 3000), [(0.0, 3.0)], engine="fake", device=d))
    f = up(y, 12).astype(np.float64)
    small = np.abs(f) <= gain.KNEE * 32768
    z = sent[0]
    assert z.dtype == np.int16 and np.abs(z.astype(int)).max() <= 32767 and np.count_nonzero(~small) > 0
    assert np.array_equal(z[small], np.round(f[small]).astype(np.int16)) and t.gain_limited[d] == np.count_nonzero(~small)


def test_pipeline_with_an_offset(tmp_path, monkeypatch):
    """The live pipeline (Segmenter, the energy VAD, the final layer) on a device with an offset: float32 in, the
    engine sent int16 at the offset (a tone of 1000 x1.25 is heard as tone 5), finalized."""
    from test_backlog import DEVICE, flac, heard, rig, tone
    from alldayrec import device_pipeline
    got = []

    async def engine(x, partial=None):
        got.append(x.dtype)
        return heard(x)
    rig(tmp_path, monkeypatch, engine)
    gain.put(DEVICE, 0, 20 * math.log10(1.25))
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 900)
    assert audio.save(DEVICE, int(t0.timestamp() * 1000), flac(np.concatenate([tone(0, 2), np.zeros(3 * SR, np.int16)])), "audio/flac")
    db.put_kv(f"end_at:{DEVICE}", core.iso(t0 + dt.timedelta(seconds=5)))
    finals = lambda: [r["text"] for r in db.sentences(core.iso(t0), core.iso(t0 + dt.timedelta(seconds=60)), source=DEVICE)]

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            for _ in range(60):
                if finals():
                    break
                await anyio.sleep(0.5)
            tg.cancel_scope.cancel()
    anyio.run(run)
    assert finals() == ["5"] and got and set(got) == {np.dtype(np.int16)}, (finals(), got)


def test_float_into_models():
    """Float32 past full scale into what the models take it through: no clip, no assertion, finite. The fbank's
    feature extractor (kaldi-native-fbank) and the levels here; the ONNX models where their files are."""
    import kaldi_native_fbank as knf
    x = up(speech(2, 1, -10), 12)
    assert np.abs(x).max() > 32768
    o = knf.FbankOptions()
    o.frame_opts.samp_freq, o.frame_opts.dither, o.frame_opts.snip_edges, o.mel_opts.num_bins = 16000, 0, True, 80
    f = knf.OnlineFbank(o)
    f.accept_waveform(16000, x.tolist())
    feat = np.vstack([f.get_frame(i) for i in range(f.num_frames_ready)])
    assert np.isfinite(feat).all()
    q = knf.OnlineFbank(o)
    q.accept_waveform(16000, (x / 4).tolist())
    assert abs(np.median(feat - np.vstack([q.get_frame(i) for i in range(q.num_frames_ready)])) - 2 * np.log(4)) < 1e-3, \
        "4 times louder: log-mel 12 dB up, not clipped"
    assert np.isfinite(infer.levels(x)).all() and infer.levels(x).max() > 0 and selfvoice.level(x) > -3, "past 0 dBFS: as it is"


@pytest.mark.skipif(not all((infer.DIR / n).is_file() for n in (infer.EMB, infer.YAMNET, infer.SEG, infer.VAD, infer.CMVN)),
                    reason="the model files are not in MODELS_DIR (CI)")
def test_float_into_onnx_models():
    from alldayrec import music, transcript
    x = up(speech(3, 2, -10), 12)
    assert np.isfinite(infer.fbank(x)).all()
    e = infer.embedder()(x)
    assert np.isfinite(e).all() and abs(np.linalg.norm(e) - 1) < 1e-5
    music.heard(x)
    assert np.isfinite(transcript.segment(x[:transcript.SEG_N])).all()


def test_validation(tmp_path, monkeypatch, capsys):
    """Not a gain (not finite, past +-60 dB) or no time: refused with a word, never a row (a capture inf made GET 500)."""
    ts = [{"id": "a", "token": "ta", "data": str(tmp_path / "a"), "recordings": str(tmp_path / "a")}]
    f = tmp_path / "tenants.json"
    f.write_text(json.dumps(ts))
    os.chmod(f, 0o600)
    monkeypatch.setattr(tenant, "FILE", f)
    a = tenant.load(f)[0]
    a.data.mkdir()
    tenant.call(a, db.init)
    for args in (["0", "--capture", "inf"], ["0", "--capture", "nan"], ["0", "--capture", "1e100"], ["1e100"], ["inf"],
                 ["3", "--since", "yesterday"], ["3", "--since", "-5"], ["3", "--since", "99999999999999999999"]):
        with pytest.raises(SystemExit):
            gain.main(["--tenant", "a", "set", "LR-BAD", *args])
    assert tenant.call(a, gain.rows) == []
    for bad in ((float("inf"), None), (None, float("nan")), (61.0, None), (None, 10 ** 400)):
        with pytest.raises(ValueError):
            tenant.call(a, gain.put, "LR-BAD", 0, *bad)
    with pytest.raises(ValueError):
        tenant.call(a, gain.put, "LR-BAD", 10 ** 20, 1.0)
    assert tenant.call(a, gain.rows) == []
    c = client(a)
    r = c.get("/api/device-gain", headers={"authorization": "Bearer ta"})
    assert r.status_code == 200 and r.json()["rows"] == []


def test_voice_clip(tmp_path):
    """A history voice's clip: as stored while its device has no offset (revalidated, not cached for a year), or only
    0 dB ones; with one, decoded at it (WAV, X-Gain-Db)."""
    d = "LR-CLIP-TEST"
    c, body = clip_rig(tmp_path, d)
    r = c.get("/api/voice-clip/made-up.ogg")
    assert r.status_code == 200 and r.content == body and r.headers["cache-control"] == "no-cache" and "x-gain-db" not in r.headers
    gain.put(d, T0, 0.0)
    gain.put(d, T0 + 5000, DB2)  # after the clip
    r = c.get("/api/voice-clip/made-up.ogg")
    assert r.status_code == 200 and r.content == body and r.headers["cache-control"] == "no-cache" and "x-gain-db" not in r.headers
    gain.put(d, T0, DB2)
    r = c.get("/api/voice-clip/made-up.ogg", headers={"if-none-match": r.headers.get("etag", "x")})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.headers["x-gain-db"] == f"{DB2:g}"
    assert np.array_equal(pcm(r.content), gain.to16(up(audio.decode(body), DB2)))


@pytest.mark.parametrize("first", [None, 0.0])
def test_voice_clip_offset_inside(tmp_path, first):
    """An offset that starts inside a clip whose start has none (or 0 dB): decoded, each sample at its own time's;
    its first half as decoded, the rest x2 (X-Gain-Db: the first sample's, 0)."""
    d = "LR-CLIP-TEST"
    c, body = clip_rig(tmp_path, d)
    if first is not None:
        gain.put(d, T0, first)
    gain.put(d, T0 + 500, DB2)
    r = c.get("/api/voice-clip/made-up.ogg")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.headers["x-gain-db"] == "0"
    x, h = audio.decode(body), SR // 2
    assert np.array_equal(pcm(r.content), gain.to16(np.concatenate([x[:h], up(x[h:], DB2)])))


@pytest.mark.parametrize("rows", [[(0, DB2)], [(0, DB2), (T0 + 500, DB2)]])
def test_voice_clip_untimed_one(tmp_path, rows):
    """A clip as the history generator wrote it (no clip_start): its device's one offset for all time, from since 0, on
    every sample (WAV, X-Gain-Db)."""
    d = "LR-CLIP-TEST"
    c, body = clip_rig(tmp_path, d, timed=False)
    for since, g in rows:
        gain.put(d, since, g)
    r = c.get("/api/voice-clip/made-up.ogg")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav" and r.headers["x-gain-db"] == f"{DB2:g}"
    assert np.array_equal(pcm(r.content), gain.to16(up(audio.decode(body), DB2))) and not T().gain_untimed


@pytest.mark.parametrize("rows", [[(0, DB2), (T0 + 500, 0.0)], [(0, 0.0), (T0, DB2)], [(T0, DB2)]])
def test_voice_clip_untimed_several(tmp_path, rows, caplog):
    """No clip_start and the device's offset not one value for all time (several, or its first from after 0): no time to
    pick one by, so served as stored, logged and counted (lr_gain_untimed_clips_total)."""
    d = "LR-CLIP-TEST"
    c, body = clip_rig(tmp_path, d, timed=False)
    for since, g in rows:
        gain.put(d, since, g)
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            r = c.get("/api/voice-clip/made-up.ogg")
            assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and r.content == body
    assert "x-gain-db" not in r.headers and T().gain_untimed == {d: 2}
    assert sum("served as stored" in m for m in caplog.messages) == 2


def test_voice_clip_untimed_none(tmp_path):
    """No clip_start and no row of its device (or only 0 dB ones): as stored, nothing counted, as on main."""
    d = "LR-CLIP-TEST"
    c, body = clip_rig(tmp_path, d, timed=False)
    for _ in range(2):
        r = c.get("/api/voice-clip/made-up.ogg")
        assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and r.content == body
        gain.put(d, 0, 0.0)
    assert not T().gain_untimed


def test_seg_emb_gain(tmp_path):
    """A segment voiceprint's gain_db: its device's offset at the segment's start; one over a since_ms has its start's."""
    from alldayrec import device_pipeline
    db.open_db(tmp_path / "t.db")
    d = "LR-SEG-TEST"
    gain.put(d, T0 + 1000, DB2)
    segs = [{"row": k, "start_ms": a, "end_ms": b} for k, (a, b) in enumerate([(0, 900), (800, 2000), (1000, 2000)])]
    vs = [[(g, np.ones(4))] for g in segs]
    rows = device_pipeline.seg_embs(d, segs, vs, lambda s: at(T0) + dt.timedelta(seconds=s))
    assert [r[7] for r in rows] == [0.0, 0.0, DB2]
    assert device_pipeline.seg_embs("LR-NONE", segs, vs, lambda s: at(T0) + dt.timedelta(seconds=s))[2][7] == 0.0


class Old:
    """An infer service from before f32le: its /health without formats."""

    def __init__(self, http):
        self.http, self.post = http, http.post

    def get(self, *a, **k):
        r = self.http.get(*a, **k)
        h = {k: v for k, v in r.json().items() if k != "formats"}
        return type("R", (), {"raise_for_status": lambda self: None, "json": lambda self: h})()


def test_infer_service_float(monkeypatch, caplog):
    """float32 to a service that takes it: as f32le, the very floats; to an older one: rounded to int16 (it would
    read f32 as int16), warned once; int16 as before (application/octet-stream, its bytes); f32 not finite: 400."""
    import test_compute
    monkeypatch.setitem(infer_server.state, "compute", test_compute.Stub())
    monkeypatch.setitem(infer_server.state, "token", test_compute.TOKEN)
    sock = test_compute.bound()
    srv = test_compute.serve(sock)
    try:
        url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        stub, seen = infer_server.state["compute"], []
        monkeypatch.setattr(stub, "embed", lambda x, e=stub.embed: seen.append(x) or e(x))
        r = compute.Remote(url, test_compute.TOKEN, 5.0)
        assert r.same_models() and r.f32
        x = up(speech(2, 5, -10), 12)
        assert np.array_equal(r.embed(x), stub.embed(x)) and seen[-1].dtype == np.float32 and np.array_equal(seen[-1], x)
        i16 = speech(1, 6)
        r.embed(i16)
        assert seen[-1].dtype == np.int16 and np.array_equal(seen[-1], i16), "int16: as before"
        old = compute.Remote(url, test_compute.TOKEN, 5.0)
        old.http = Old(old.http)
        assert old.same_models() and not old.f32
        with caplog.at_level(logging.WARNING):
            old.embed(x)
            old.embed(x)
        assert seen[-1].dtype == np.int16 and np.array_equal(seen[-1], gain.to16(x))
        assert sum("no float32" in m for m in caplog.messages) == 1
        c = TestClient(infer_server.app)
        auth = {"authorization": f"Bearer {test_compute.TOKEN}"}
        bad = np.array([1.0, np.nan], "<f4").tobytes()
        assert c.post("/embed", content=bad, headers={**auth, "content-type": infer_server.F32}).status_code == 400
        assert c.post("/embed", content=b"\0" * 6, headers={**auth, "content-type": infer_server.F32}).status_code == 400
        assert "f32le" in c.get("/health", headers=auth).json()["formats"]
    finally:
        srv.should_exit = True


def test_replay_case_offset(tmp_path, monkeypatch):
    """replay_case: meta.json's offset_db on the case's audio, float32, its results in their own work@<dB>/; none: as
    decoded, work/."""
    from alldayrec import replay_case
    monkeypatch.setenv("STEPFUN_API_KEY", "made-up")
    y = speech(1, 8)
    for g in (None, DB2):
        d = tmp_path / f"case-{g}"
        d.mkdir()
        (d / "a.wav").write_bytes(audio.wav(y))
        (d / "meta.json").write_text(json.dumps({"audio": "a.wav", "device": "made-up", "pieces": [], **({"offset_db": g} if g else {})}))
        case = replay_case.Case(d)
        if g is None:
            assert case.x.dtype == np.int16 and np.array_equal(case.x, y) and case.work == d / "work"
        else:
            assert np.array_equal(case.x, up(y, g)) and case.work == d / f"work@{g:g}"


def test_tenants_and_init(tmp_path):
    a, b = (tenant.Tenant(i, f"t{i}", tmp_path / i, tmp_path / i) for i in "ab")
    for t in (a, b):
        t.data.mkdir()
        tenant.call(t, db.init)
    tenant.call(a, gain.put, "LR-SAME", 0, 9.0)
    x = speech(1, 5)
    assert tenant.call(b, gain.offset_db, "LR-SAME", T0) is None and tenant.call(b, gain.apply, x, "LR-SAME", T0) is x
    assert np.array_equal(tenant.call(a, gain.apply, x, "LR-SAME", T0), up(x, 9))
    for _ in range(2):
        tenant.call(a, db.init)
    assert tenant.call(a, gain.rows) == [{"device": "LR-SAME", "since_ms": 0, "capture_db": None, "offset_db": 9.0, "source": "manual",
                                          "note": None}], "init again: the table and its rows as they were"


def test_cli(tmp_path, monkeypatch, capsys):
    ts = [{"id": i, "token": f"t{i}", "data": str(tmp_path / i), "recordings": str(tmp_path / i)} for i in "ab"]
    f = tmp_path / "tenants.json"
    f.write_text(json.dumps(ts))
    os.chmod(f, 0o600)
    monkeypatch.setattr(tenant, "FILE", f)
    for t in tenant.load(f):
        t.data.mkdir()
        tenant.call(t, db.init)
    gain.main(["--tenant", "a", "set", "LR-CLI", "7.5", "--since", dt.datetime.fromtimestamp(T0 / 1000).isoformat(), "--note", "made up"])
    gain.main(["list"])
    out = capsys.readouterr().out
    assert "a\tLR-CLI\t" in out and "offset=7.5" in out and "made up" in out and "b\t" not in out, out
    a = tenant.load(f)[0]
    assert tenant.call(a, gain.offset_db, "LR-CLI", T0) == 7.5 and tenant.call(a, gain.offset_db, "LR-CLI", T0 - 1) is None
    with pytest.raises(SystemExit):
        gain.main(["set", "LR-CLI", "3"])  # which tenant
    with pytest.raises(SystemExit):
        gain.main(["--tenant", "a", "set", "LR-CLI", "nan"])
    with pytest.raises(SystemExit):
        gain.main(["--tenant", "zz", "list"])
