"""The VAD's input gain (infer.StreamVad.gain): quiet speech lifted to where the VAD hears it, a quiet room not. The
gain itself needs no model; the detection test runs where the model files are (MODELS_DIR; none in CI)."""
import numpy as np
import pytest
import scipy.signal as ss

from alldayrec import infer

SR = 16000


def speech(seconds: float, seed: int = 0) -> np.ndarray:
    """Vowel-like syllables at unit RMS: a pulse train at a falling pitch through two formants, ~4 a second, with
    short gaps and now and then a pause. No recording: the VAD takes it for speech at a normal level."""
    rng, out, n_all = np.random.default_rng(seed), [], 0
    vowels = [(730, 1090), (270, 2290), (300, 870), (530, 1840), (570, 840)]
    while n_all < seconds * SR:
        n = int(rng.uniform(0.15, 0.3) * SR)
        y = (np.diff(np.floor(np.cumsum(rng.uniform(110, 180) * np.linspace(1.1, 0.9, n) / SR)), prepend=0) > 0).astype(float)
        for f in vowels[rng.integers(len(vowels))]:
            y = ss.lfilter(*ss.iirpeak(f, f / 80, SR), y)
        gap = np.zeros(int((0.5 if rng.random() < 0.15 else rng.uniform(0.03, 0.12)) * SR))
        out += [y * np.hanning(n), gap]
        n_all += n + len(gap)
    x = np.concatenate(out)[:int(seconds * SR)]
    return x / np.sqrt((x * x).mean())


def at(x: np.ndarray, db: float, floor: float = -75) -> np.ndarray:
    """x at db dBFS RMS over white noise at floor dBFS, int16."""
    noise = np.random.default_rng(1).normal(size=len(x)) * 10 ** (floor / 20)
    return np.clip((x * 10 ** (db / 20) + noise) * 32768, -32768, 32767).astype(np.int16)


def chunks(v: infer.StreamVad, x: np.ndarray, f, seconds: float = 1.0) -> list:
    """f(v, chunk) over x in chunks of whole frames, the overlap kept, as Segmenter.push feeds the VAD."""
    out, step = [], int(seconds * SR)
    for i in range(0, len(x) - 400 + 1, step):
        out.append(f(v, x[i:i + step + 240]))
    return out


def test_gain():
    quiet, loud = at(speech(30), -65), at(speech(30), -30)
    g = np.concatenate(chunks(infer.StreamVad(), quiet, infer.StreamVad.gain))
    assert g[0] == 0 and g[-1] == infer.VAD_GAIN_MAX_DB == 16, "at the start as if at the target; quiet speech: the most"
    assert np.all(np.diff(g[:1000]) >= 0), "rises as the quiet frames fill the 10 s"
    assert np.array_equal(g, infer.StreamVad().gain(quiet[:len(g) * 160 + 240])), "the same in one chunk as in 1 s ones"
    assert not np.concatenate(chunks(infer.StreamVad(), loud, infer.StreamVad.gain)).any(), "loud speech: never lowered, as it came"
    v = infer.StreamVad()
    chunks(v, at(speech(20), -55), infer.StreamVad.gain)
    g = v.gain(at(speech(1, seed=2), -55))
    assert np.all((g > 4) & (g < 10)), "in between: what brings its loud frames to the target"


def test_gain_off(monkeypatch):
    monkeypatch.setattr(infer, "VAD_GAIN_MAX_DB", 0.0)
    assert not np.concatenate(chunks(infer.StreamVad(), at(speech(15), -65), infer.StreamVad.gain)).any()


@pytest.mark.skipif(not all((infer.DIR / n).is_file() for n in (infer.VAD, infer.CMVN)), reason="no VAD model files (CI)")
def test_quiet_speech_heard_quiet_room_not(monkeypatch):
    def run(x, gain=True):
        v = infer.StreamVad()
        if not gain:
            v.gain = lambda c: 0.0
        rs = [r for rs in chunks(v, x, infer.StreamVad.detect_chunk) for r in rs]
        return np.array([r.raw_prob for r in rs]), sum(r.is_speech_end for r in rs)
    s = speech(30)
    p, utt = run(at(s, -60), gain=False)
    assert utt == 0 and (p > 0.5).mean() < 0.01, "the bug: speech at -60 dBFS, no frame heard"
    p, utt = run(at(s, -60))
    assert utt >= 10 and (p > 0.5).mean() > 0.1, "lifted: heard"
    p, utt = run(at(np.zeros(60 * SR), -200, floor=-72))
    assert utt == 0 and (p > 0.5).mean() == 0, "a quiet room's noise floor lifted the most: still no speech"
    loud = at(s, -30)
    assert np.array_equal(run(loud)[0], run(loud, gain=False)[0]), "loud speech: the VAD as before"
