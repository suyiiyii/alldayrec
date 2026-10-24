"""A recorder's 10 ms hum (the encoder's work in each 10 ms frame couples into the microphone) is taken out of its
pieces as they are read; speech stays, a piece with too few quiet frames and an iPhone microphone piece (SILK) are
read as decoded. Synthetic: white noise at a quiet room's floor, a fixed 160-sample waveform (the hum), 3 s of "speech"."""
import datetime as dt
import subprocess

import numpy as np

from alldayrec import audio
from alldayrec import db

T = int(dt.datetime(2026, 10, 15, 3, 0).timestamp() * 1000)
rng = np.random.default_rng(1)
db_ = lambda v: 10 * np.log10(np.mean(np.asarray(v, np.float64) ** 2) / 32768 ** 2 + 1e-20)
periodic = lambda v: db_(np.asarray(v, np.float64)[:len(v) // 160 * 160].reshape(-1, 160).mean(0))  # its part repeating every 10 ms
_t = np.arange(160) / 16000
HUM = 32768 * np.sqrt(2) * (10 ** (-80 / 20) * np.sin(2 * np.pi * 100 * _t + 1) + 10 ** (-88 / 20) * np.sin(2 * np.pi * 200 * _t + 2))  # as on battery


def room(seconds: float) -> np.ndarray:
    return rng.standard_normal(int(seconds * 16000)) * 32768 * 10 ** (-77 / 20)


def speech(seconds: float) -> np.ndarray:
    """Voiced sound at -30 dBFS: a 130 Hz pitch gliding to 180 Hz and its harmonics, in syllables of 0.2 s."""
    t = np.arange(int(seconds * 16000)) / 16000
    f0 = 130 + 50 * t / seconds
    ph = 2 * np.pi * np.cumsum(f0) / 16000
    v = sum(np.sin(k * ph) / k for k in range(1, 20)) * (0.6 + 0.4 * np.sin(2 * np.pi * 2.5 * t)) ** 2
    return v * 32768 * 10 ** (-30 / 20) / np.sqrt(np.mean(v ** 2))


def pcm(x: np.ndarray) -> np.ndarray:
    return np.clip(np.round(x), -32768, 32767).astype(np.int16)


def test_dehum():
    clean = np.concatenate([room(4), room(3) + speech(3), room(3)])
    x = pcm(clean + np.resize(HUM, len(clean)))
    y = audio.dehum(x)
    left = y.astype(np.float64) - clean  # what is left of the hum (and the rounding)
    # what stays: the template's own noise (the room over 700 frames, -106) and its rounding to whole samples (-101)
    assert periodic(x - clean) > -81 and periodic(left) < -97, f"the hum: {periodic(x - clean):.1f} -> {periodic(left):.1f} dBFS"
    s = slice(4 * 16000, 7 * 16000)
    assert periodic(left[s]) < -95, f"under the speech too: {periodic(left[s]):.1f} dBFS"
    assert db_(left[s]) < -95, f"and nothing else taken: {db_(left[s]):.1f} dBFS (the rounding)"
    assert abs(db_(y[s]) - db_(clean[s])) < 0.01, "the speech's level unchanged"
    # speech all through: no quiet frames to learn the hum from, the piece as it was
    loud = pcm(room(3) + speech(3) + np.resize(HUM, 48000))
    assert audio.dehum(loud) is loud
    # just under DEHUM_MIN quiet frames (0.49 s): as it was; at it: taken out
    edge = lambda q: pcm(np.concatenate([room(q / 100), speech(2)]))
    e = edge(audio.DEHUM_MIN - 1)
    assert audio.dehum(e) is e
    e = edge(audio.DEHUM_MIN)
    assert not np.array_equal(audio.dehum(e), e)


def opus(x: np.ndarray, *opts: str) -> list[bytes]:
    """x through one libopus encoder: its audio packets."""
    ogg = subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", "16000", "-ac", "1", "-i", "pipe:0", "-c:a", "libopus", *opts,
                          "-f", "ogg", "pipe:1"], input=x.tobytes(), capture_output=True, check=True).stdout
    return audio.packets(ogg)[2:]


def line(x: np.ndarray, f0: float) -> float:
    """The level of x's spectral line at f0 (a sine's RMS, dBFS)."""
    w = np.hanning(len(x))
    X = np.abs(np.fft.rfft(x * w)) / (w.sum() / 2)
    f = np.fft.rfftfreq(len(x), 1 / 16000)
    return 20 * np.log10(X[np.abs(f - f0) < 1.5].max() / np.sqrt(2) / 32768 + 1e-12)


def test_samples(tmp_path, monkeypatch):
    db.open_db(tmp_path / "t.db")
    clean = room(6)
    rec = opus(pcm(clean + np.resize(HUM, len(clean))), "-application", "lowdelay", "-frame_duration", "10", "-b:a", "32k")  # codec.c
    assert audio.save("LR-P3-test", T, audio.ogg(rec), "audio/ogg") > 5.9
    row = lambda d: db.conn().execute("SELECT * FROM pieces WHERE device = ?", (d,)).fetchone()
    raw, x = audio.decode(audio.read(row("LR-P3-test"))), audio.samples(row("LR-P3-test"))
    hum100 = line(np.resize(HUM, 96000), 100)
    assert line(raw, 100) > hum100 - 3 and line(x, 100) < hum100 - 15, f"100 Hz: {line(raw, 100):.1f} -> {line(x, 100):.1f} dBFS"
    assert line(x, 200) < line(raw, 200) - 10, f"200 Hz: {line(raw, 200):.1f} -> {line(x, 200):.1f} dBFS"
    assert row("LR-P3-test")["rms"] == audio.health(raw)[0], "the stored levels are of the piece as it came"
    monkeypatch.setattr(audio, "DEHUM", False)
    assert np.array_equal(audio.samples(row("LR-P3-test")), raw), "DEHUM=0: as decoded"
    monkeypatch.setattr(audio, "DEHUM", True)
    # the iPhone's own microphone: SILK, 20 ms frames, as decoded
    mic = opus(pcm(clean + np.resize(HUM, len(clean))), "-application", "voip", "-frame_duration", "20", "-b:a", "16k")
    assert audio.save("iPhone", T, audio.ogg(mic, 20), "audio/ogg") > 5.9
    assert np.array_equal(audio.samples(row("iPhone")), audio.decode(audio.read(row("iPhone"))))
