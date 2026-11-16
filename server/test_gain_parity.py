"""With no device_gain row, every decoded exit hands out what main did: checked against the input itself (lossless WAV
pieces: the samples as made, a recorder's played WAV squeeze.louder(PLAY_GAIN_DB) of them), and, for the Opus and AAC
paths (priming, dehum, AAC trimming), a digest of every exit printed to compare with the same file run on main. This
file imports nothing of gain.py, so it runs unchanged on main."""
import datetime as dt
import hashlib
import io
import subprocess
import wave

import anyio
import numpy as np
import pytest
from starlette.testclient import TestClient

from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import late
from alldayrec import routes_audio
from alldayrec import server
from alldayrec import squeeze

SR = audio.SR
T0 = int(dt.datetime(2026, 10, 2, 10, 0).timestamp() * 1000)
at = lambda ms: dt.datetime.fromtimestamp(ms / 1000)


def speech(seconds: float, seed: int, db_: float = -30) -> np.ndarray:
    """Noise bursts at db_ dBFS RMS, a quarter second on and off: made up, no recording."""
    rng = np.random.default_rng(seed)
    x = rng.normal(size=int(seconds * SR)) * np.repeat(rng.random(int(seconds * 4) + 1) < 0.6, SR // 4)[:int(seconds * SR)]
    return np.clip(x * 32768 * 10 ** (db_ / 20), -32768, 32767).astype(np.int16)


def pcm(wav_bytes: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav_bytes)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), np.int16)


async def streamed(device: str, n: int) -> np.ndarray:
    out, s = [], audio.stream(device, None)
    async for _, x in s:
        out.append(x)
        if sum(map(len, out)) >= n:
            break
    await s.aclose()
    return np.concatenate(out)


def test_lossless_exits_as_on_main(tmp_path):
    db.open_db(tmp_path / "t.db")
    x = {d: speech(6, k) for k, d in enumerate(("phone-test", "LR-T1-TEST"))}
    for d, y in x.items():
        assert audio.save(d, T0, audio.wav(y), "audio/wav") == 6
    a, b = T0 + 1000, T0 + 4000
    for d, y in x.items():
        p = db.conn().execute("SELECT * FROM pieces WHERE device = ?", (d,)).fetchone()
        assert np.array_equal(audio.samples(p), y), "samples: as uploaded"
        assert np.array_equal(audio.fresh(p, T0 + 500)[1], y[SR // 2:]), "fresh"
        assert np.array_equal(anyio.run(streamed, d, len(y))[:len(y)], y), "the pipeline's stream"
        assert np.array_equal(late.read_exact(d, a, b), y[SR:4 * SR]), "the late layer's read"
        raw = np.frombuffer(audio.cut(at(a), at(b), "s16le", d), np.int16)
        assert np.array_equal(raw, y[SR - SR // 5:4 * SR + SR // 5]), "s16le (voiceprints): the samples"
        played = squeeze.louder(raw, audio.PLAY_GAIN_DB) if d.startswith("LR-") else raw
        assert np.array_equal(pcm(audio.cut(at(a), at(b), "wav", d)), played), "wav: a recorder's PLAY_GAIN_DB louder"
        _, _, heard = audio.newer(d, None)
        assert np.array_equal(heard, squeeze.louder(y, audio.PLAY_GAIN_DB) if d.startswith("LR-") else y), "listening"
        wav = anyio.run(routes_audio.audio_wav, at(a).isoformat(), at(b).isoformat(), d)
        assert wav.body == audio.cut(at(a), at(b), "wav", d)


def test_codec_exits_digest(tmp_path, capsys):
    """Opus pieces of a recorder (primed, dehum) and a phone's AAC: each exit's bytes hashed, printed to compare with
    the same test on main (the same ffmpeg)."""
    db.open_db(tmp_path / "t.db")
    y = speech(4, 7, -40)
    enc = lambda args: subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0", *args, "pipe:1"],
                                      input=y.tobytes(), capture_output=True, check=True).stdout
    opus = enc(["-c:a", "libopus", "-frame_duration", "10", "-f", "ogg"])
    for i in range(3):  # three back-to-back 4 s pieces of one recorder: the second and third primed by the one before
        assert audio.save("LR-T2-TEST", T0 + i * 4000, opus, "audio/ogg")
    assert audio.save("phone-aac", T0, enc(["-c:a", "aac", "-f", "adts"]), "audio/aac", n=len(y))
    h = hashlib.sha256()
    for d in ("LR-T2-TEST", "phone-aac"):
        for p in db.conn().execute("SELECT * FROM pieces WHERE device = ? ORDER BY start_ms", (d,)):
            h.update(audio.samples(p).tobytes())
        h.update(audio.cut(at(T0 + 500), at(T0 + 3500), "s16le", d))
        h.update(audio.cut(at(T0 + 500), at(T0 + 3500), "wav", d))
        h.update(audio.newer(d, None)[2].tobytes())
        h.update(late.read_exact(d, T0 + 500, T0 + 3500).tobytes())
        h.update(anyio.run(streamed, d, 3 * SR)[:3 * SR].tobytes())
    with capsys.disabled():
        print(f"\ncodec exits sha256 {h.hexdigest()}")


def test_asr_input_digest(tmp_path, capsys):
    """What an ASR engine is sent of the late read (squeezed, faded at the splices, lifted): its bytes hashed, printed
    to compare with the same test on main; int16 throughout."""
    from alldayrec import asr
    db.open_db(tmp_path / "t.db")
    y = speech(20, 11, -45)  # quiet: lifted
    assert audio.save("phone-asr", T0, audio.wav(y), "audio/wav") == 20
    x = late.read_exact("phone-asr", T0, T0 + 20_000)
    h = hashlib.sha256()
    for speech_ in ([(1.0, 3.0), (9.5, 12.0), (18.0, 19.5)], [(0.0, 20.0)], []):
        xs, spans = asr.sent_audio(x, speech_)
        assert xs.dtype == np.int16
        h.update(xs.tobytes() + repr(spans).encode())
    with capsys.disabled():
        print(f"\nasr input sha256 {h.hexdigest()}")


def clip_rig(tmp_path, d, timed=True):
    """A tenant with one history voice of d, its 1 s clip from T0 (made-up noise, Opus): its client and the clip's bytes.
    Not timed: the row as the history generator wrote it (clip, clip_text, device; no clip_start, no clip_end)."""
    t = db.open_db(tmp_path / "t.db")
    clips = t.data / "voices" / "clips"
    clips.mkdir(parents=True)
    body = subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0", "-c:a", "libopus",
                           "-f", "ogg", "pipe:1"], input=speech(1, 3, -30).tobytes(), capture_output=True, check=True).stdout
    (clips / "made-up.ogg").write_bytes(body)
    v = {"conv": "made-up-conv", "label": "S1", "device": d, "n": 1, "speech_s": 1, "k": 1, "e": np.array([1.0, 0.0]),
         "clip": "made-up.ogg", "clip_text": "made up"}
    if timed:
        v |= {"clip_start": at(T0).isoformat(), "clip_end": at(T0 + 1000).isoformat()}
    with db.tx() as tx:
        db.insert_voices(tx, [v])
    core.TENANTS[:] = [t]  # no token: no auth
    return TestClient(server.app), body  # no `with`: the lifespan does not start


@pytest.mark.parametrize("timed", [True, False])
def test_voice_clip_as_stored(tmp_path, timed):
    """A history voice's clip of a device with no offset, timed or as the history generator wrote it: its stored bytes,
    as main serves them."""
    c, body = clip_rig(tmp_path, "LR-CLIP-TEST", timed)
    r = c.get("/api/voice-clip/made-up.ogg")
    assert r.status_code == 200 and r.headers["content-type"] == "audio/ogg" and r.content == body


def test_seg_emb_digest(tmp_path, monkeypatch, capsys):
    """The segment voiceprints a final pass keeps (seg_emb on): every column but the times (now-relative here) as
    offsets from the first, hashed, printed to compare with the same test on main; gain_db 0 throughout."""
    from test_seg_emb import finalize
    finalize(tmp_path, monkeypatch)
    rows = db.conn().execute("SELECT start_ms, end_ms, part, engine_speaker, model, e, gain_db, seg FROM seg_emb "
                             "ORDER BY start_ms, part").fetchall()
    assert rows and {r[6] for r in rows} == {0.0}
    h = hashlib.sha256()
    for r in rows:
        h.update(repr((r[0] - rows[0][0], r[1] - rows[0][0], *r[2:])).encode())
    with capsys.disabled():
        print(f"\nseg_emb sha256 {h.hexdigest()} ({len(rows)} rows)")
