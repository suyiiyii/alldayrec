"""Silence squeezed out of the audio the ASR engine gets: the engine bills by audio seconds, and a final piece goes
whole when it has any speech. Each VAD silence longer than `keep` is cut down to `keep` (keep/2 after the speech before
it, keep/2 before the speech after it; at the window's edges keep/2 next to the speech). unsqueeze maps the engine's
times back to the window, so nothing after the engine sees the cut. lift then brings quiet audio up to a level the
engines hear."""
import os

import numpy as np

SR = 16000
MS = SR // 1000  # samples per ms: spans lie on whole ms, so whole-ms engine times map back exactly
FADE = 10 * MS   # ramps at each splice: no click
LIFT_TARGET_DB = -30.0  # lift: the 90th percentile of the 10 ms block levels of what is sent is brought up to this
LIFT_MAX_DB = float(os.environ.get("ASR_GAIN_MAX_DB", "16"))  # ... by at most this; 0: sent as it came
LIFT_PEAK = 0.89 * 32768  # ... and never past -1 dBFS at the peaks next to a block
BLOCK = 10 * MS


def squeeze(x: np.ndarray, speech: list[tuple[float, float]], keep: float) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """x: int16 PCM; speech: (start, end) seconds into x. Returns the audio to send and the kept spans of x (ms) for
    unsqueeze. keep 0 or no speech in x: x whole."""
    n = len(x) / MS
    spans = []
    for a, b in sorted(speech):
        a, b = max(0, round(a * 1000 - keep * 500)), min(n, round(b * 1000 + keep * 500))
        if b <= a:
            continue
        if spans and a <= spans[-1][1]:
            spans[-1][1] = max(spans[-1][1], b)
        else:
            spans.append([a, b])
    if keep <= 0 or not spans:
        return x, [(0, n)]
    parts = []
    for a, b in spans:
        p = x[a * MS:round(b * MS)].astype(np.float32)
        f = min(FADE, len(p) // 2)
        if a > 0:
            p[:f] *= np.linspace(0, 1, f)
        if b < n:
            p[len(p) - f:] *= np.linspace(1, 0, f)
        parts.append(p)
    return np.concatenate(parts).astype(np.int16), [tuple(s) for s in spans]


def at(ms: float, spans: list[tuple[float, float]], end: bool = False) -> float:
    """A time in the sent audio (ms) → ms into x. A sentence's end on a splice stays in the span before it, a start
    goes to the span after it."""
    off = 0
    for k, (a, b) in enumerate(spans):
        if ms < off + b - a or (end and ms == off + b - a) or k == len(spans) - 1:
            return ms - off + a
        off += b - a


def unsqueeze(segs: list[dict], spans: list[tuple[float, float]]) -> list[dict]:
    """The engine's sentences (and file-mode words) with times in x, in place; a sentence over a splice maps its start
    and end each on its own side."""
    for g in segs:
        for u in [g] + (g.get("words") or []):
            u["start_ms"], u["end_ms"] = at(u["start_ms"], spans), at(u["end_ms"], spans, end=True)
    return segs


def lift(x: np.ndarray) -> np.ndarray:
    """x as an engine is sent it, louder when it is quiet: StepFun hears level. 10-18 it wrote 31 and 81 characters of
    a lecture minute at -47 and -53 dBFS (90th percentile of the frame levels), 218 and 209 with 16 dB more; on the
    GT cases a quiet phone recording went from 42.7 to 35.6 % CER, the others stayed. One gain for all of x (the
    90th percentile of its 10 ms block levels to LIFT_TARGET_DB, 0 to LIFT_MAX_DB): a gain that followed the level
    every 10 s lifted the quiet stretches of a loud conversation too, CER 8.8 -> 14.2 %. Lower only where a block's
    peaks or its neighbours' would pass -1 dBFS, ramped from block to block; never below x, the same length: times do
    not move. x itself when nothing is lifted."""
    n = len(x) // BLOCK
    if n == 0 or LIFT_MAX_DB <= 0:
        return x
    b = x[:n * BLOCK].astype(np.float64).reshape(n, BLOCK)
    g = np.clip(LIFT_TARGET_DB - np.percentile(10 * np.log10((b * b).mean(1) / 32768 ** 2 + 1e-12), 90), 0, LIFT_MAX_DB)
    peak = np.abs(b).max(1)
    peak = np.maximum(peak, np.maximum(np.r_[peak[1:], 0], np.r_[0, peak[:-1]]))
    g = np.clip(np.minimum(g, 20 * np.log10(LIFT_PEAK / np.maximum(peak, 1))), 0, None)
    if not g.any():
        return x
    ramp = np.interp(np.arange(len(x)), np.arange(n) * BLOCK + BLOCK / 2, 10 ** (g / 20))
    return np.clip(np.round(x * ramp), -32768, 32767).astype(np.int16)


def check():
    rng = np.random.default_rng(0)
    for _ in range(300):  # random speech: a sent sample (off the fades) is the sample of x its time maps back to
        x = (np.arange(int(rng.uniform(0.5, 300) * SR)) % 30011).astype(np.int16)
        n = len(x) / MS
        sp = sorted((a, a + rng.exponential(2)) for a in rng.uniform(-2, n / 1000 + 1, rng.integers(0, 12)))
        y, spans = squeeze(x, sp, 3.0)
        assert len(y) == sum(round(b * MS) - a * MS for a, b in spans)
        splices = np.cumsum([0] + [b - a for a, b in spans])
        for t in rng.integers(0, len(y) // MS, 100):
            if min(abs(t - s) for s in splices) * MS > FADE:
                assert y[t * MS] == x[int(at(t, spans)) * MS], (t, spans)
        pad = [(max(0, round(a * 1000 - 1500)), min(n, round(b * 1000 + 1500))) for a, b in sp]
        pad = [(a, b) for a, b in pad if b > a]  # speech within keep/2 of x
        for a, b in pad:  # all of it goes, with keep/2 around it
            assert any(lo <= a and b <= hi for lo, hi in spans), (a, b, spans)
        if pad:  # and nothing else: every span edge is a padded speech edge, cut silences are over keep
            assert all(lo in {a for a, _ in pad} and hi in {b for _, b in pad} for lo, hi in spans), (pad, spans)
            assert all(a2 > b1 for (_, b1), (a2, _) in zip(spans, spans[1:]))
    x = np.zeros(60 * SR, np.int16)
    y, spans = squeeze(x, [(10, 12), (30, 31.5)], 3.0)  # 0-8.5 cut, 13.5-28.5 cut, 33-60 cut
    assert spans == [(8500, 13500), (28500, 33000)] and len(y) == 9.5 * SR, spans
    assert at(0, spans) == 8500 and at(1500, spans) == 10000 and at(5000, spans) == 28500 and at(5000, spans, True) == 13500
    assert at(9500, spans, True) == 33000 and at(9999, spans) == 33499, "past the end: on from the last span"
    g = unsqueeze([{"start_ms": 1000, "end_ms": 6000, "words": [{"start_ms": 4900, "end_ms": 5000}, {"start_ms": 5000, "end_ms": 5100}]}], spans)[0]
    assert (g["start_ms"], g["end_ms"]) == (9500, 29500), "a sentence over the splice: start and end on their own sides"
    assert [(w["start_ms"], w["end_ms"]) for w in g["words"]] == [(13400, 13500), (28500, 28600)]
    for sp in ([], [(-5, -2), (62, 80)]):  # no speech within keep/2 of x: sent whole, times unchanged
        y, spans = squeeze(x, sp, 3.0)
        assert y is x and at(12345, spans) == 12345
    y, spans = squeeze(x, [(0.5, 59.9)], 3.0)  # speech throughout: whole
    assert y is not x and np.array_equal(y, x) and spans == [(0, 60000)] and at(777, spans, True) == 777
    y, spans = squeeze(x, [(10, 12)], 0)  # keep 0: off
    assert y is x and spans == [(0, 60000)]
    y, _ = squeeze(np.full(20 * SR, 1000, np.int16), [(3, 4), (12, 13)], 3.0)  # splices fade to 0 and back
    assert len(y) == 8 * SR and y[0] == y[4 * SR - 1] == y[4 * SR] == y[-1] == 0 and y[4 * SR + FADE] == y[2 * SR] == 1000

    rng = np.random.default_rng(1)
    talk = lambda db, s: (rng.normal(size=s * SR) * np.repeat(rng.random(s * 4) < 0.5, SR // 4) * 32768 * 10 ** (db / 20)).astype(np.int16)
    quiet, loud = talk(-55, 30), talk(-25, 30)  # bursts of noise, half the quarter seconds: p90 a little over the burst RMS
    assert lift(loud) is loud, "loud: as it came"
    y = lift(quiet)
    assert len(y) == len(quiet) and np.corrcoef(y.astype(float), quiet.astype(float))[0, 1] > 0.999, "the same audio, louder"
    gain = 20 * np.log10(np.sqrt((y.astype(float) ** 2).mean() / (quiet.astype(float) ** 2).mean()))
    assert abs(gain - LIFT_MAX_DB) < 0.5, gain
    mixed = np.concatenate([talk(-25, 20), talk(-50, 20)])  # a loud speaker, then a quiet one: the loud one sets the gain
    assert lift(mixed) is mixed
    clicks = quiet.copy()
    clicks[5 * SR + 37] = 20000  # a peak in quiet audio: not lifted past -1 dBFS
    assert np.abs(lift(clicks).astype(int)).max() <= LIFT_PEAK + 1
    short = talk(-55, 1)
    assert len(lift(short)) == len(short) and lift(short[:100]) is not None


if __name__ == "__main__":
    check()
    print("ok")
