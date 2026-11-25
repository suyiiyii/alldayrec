"""Speech quality by device and day (docs: reference/configuration, 音质日报): DNSMOS P.808 (Microsoft's DNS-Challenge,
infer.DNSMOS; its raw MOS kept as the model gave it) and the clipped share of every ~9 s of a device's speech, in a
background loop that is off by default (QUALITY=1), on onnxruntime with one thread, one call at a time (threads.QUALITY).

What is scored: the final sentences' spans of each device (sentences.source), an hour at a time once the device's final
layer is past the hour (finalized_to:<device>), from QUALITY_SINCE (ISO, the instance's wall clock; default: the day the
loop first ran, kept) on; the next hour to do is kept as quality_to:<device> (kv). The hour's speech (what decoded of it), in
time order with 10 ms fades at the joins, is cut into windows of WIN samples (9.01 s, the model's input), no overlap; a
last one of MIN_S or more is repeated up to WIN (as DNSMOS's own script does), a shorter one is left out. Per window, a
quality row: its P.808 MOS at -26 dBFS (P.56 practice: the level is a number of its own, rms_db), and the share of its
samples at |x| >= 32700 as recorded (the calibration offset, gain.py, undone).
shortcut: late-layer sentences that come into an hour already scored are not scored; to redo a device, delete its
quality_to:<device> and its rows, when that starts to matter.

The report (report(), GET /api/quality): per day, all speech and apart media played aloud (the summaries' media_spans and
broadcast_spans, summary.not_live: 外放) from the rest: P.808's median, the share of speech in windows under QUALITY_LOW
(provisional: set on 35 clips, not settled), the minutes of speech in windows clipped over CLIP. A window is media when
half or more of its sentences' time is in those spans; a day with no summaries yet has no media.
"""
import datetime as dt
import logging
import os
import time
from functools import cache

import anyio
import numpy as np

from alldayrec import db, gain, infer, late, summary, threads
from alldayrec.late_store import subtract, union

log = logging.getLogger("live")
SR = 16000
ON = os.environ.get("QUALITY", "0") == "1"
LOW = float(os.environ.get("QUALITY_LOW", "2.5"))  # provisional: 3.0 put 69 % of the owner's 35 rated clips (mean 3.8) low
CLIP = 0.01  # a window over 1 % clipped: the one clip the owner called 爆音 had 5 %, two at 0.26 % were fine
EVERY_S = float(os.environ.get("QUALITY_EVERY_S", "600"))
WIN = 144160  # 9.01 s, DNSMOS's input
MIN_S = 3.0  # a last window shorter than this is left out: mostly repeats
FULL = 32700  # a sample this loud or louder counts as clipped
CHUNK_MS = 300_000  # one read of a device's audio at most (late.read_exact)
H = 3600_000
MODEL = f"dnsmos-p808@{infer.MODELS[infer.DNSMOS][:12]}"


def since_ms() -> int:
    """QUALITY_SINCE, or the midnight before the loop first ran (kept as quality_since: a device's backlog of yesterday
    is still scored today), on an hour."""
    if s := os.environ.get("QUALITY_SINCE"):
        return db.ms(s) // H * H
    if (v := db.kv("quality_since")) is None:
        db.put_kv("quality_since", v := int(dt.datetime.combine(dt.date.today(), dt.time()).timestamp() * 1000))
    return v


def ms(t: str) -> int:
    return round(dt.datetime.fromisoformat(t).timestamp() * 1000)


def iso(t: int) -> str:
    return dt.datetime.fromtimestamp(t / 1000).isoformat(timespec="milliseconds")


@cache
def _mel_basis() -> np.ndarray:
    """librosa.filters.mel(sr=16000, n_fft=321, n_mels=120) (Slaney's scale and norm), what DNSMOS was trained on."""
    def mel(f):
        f = np.asarray(f, np.float64)
        return np.where(f >= 1000, 15 + np.log(np.maximum(f, 1e-10) / 1000) / (np.log(6.4) / 27), f * 3 / 200)

    def hz(m):
        return np.where(m >= 15, 1000 * np.exp(np.log(6.4) / 27 * (m - 15)), m * 200 / 3)
    f = hz(np.linspace(mel(0), mel(SR / 2), 122))
    fft = np.fft.rfftfreq(321, 1 / SR)
    ramps = f[:, None] - fft[None, :]
    w = np.maximum(0, np.minimum(-ramps[:-2] / np.diff(f)[:-1, None], ramps[2:] / np.diff(f)[1:, None]))
    return (w * (2 / (f[2:] - f[:-2]))[:, None]).astype(np.float32)


def features(y: np.ndarray) -> np.ndarray:
    """DNSMOS's P.808 input of WIN samples: librosa's melspectrogram(n_fft=321, hop 160, n_mels=120) of all but the last
    160 samples, power_to_db(ref=max, top_db 80), (+40)/40; frames x 120."""
    x = np.pad(y[:-160].astype(np.float32), 160)  # center=True, zero padded (librosa >= 0.10)
    n = 1 + (len(x) - 321) // 160
    frames = np.lib.stride_tricks.as_strided(x, (n, 321), (x.strides[0] * 160, x.strides[0]))
    win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(321) / 321)).astype(np.float32)  # periodic Hann
    s = _mel_basis() @ (np.abs(np.fft.rfft(frames * win, axis=1)) ** 2).T.astype(np.float32)
    d = 10 * np.log10(np.maximum(s, 1e-10)) - 10 * np.log10(max(float(s.max()), 1e-10))
    return ((np.maximum(d, d.max() - 80) + 40) / 40).T.astype(np.float32)


@cache
def _session():
    return infer.session(infer.DNSMOS, 1)


def p808(y: np.ndarray) -> float:
    """The model's MOS of WIN samples (any scale; it hears them at -26 dBFS)."""
    y = y.astype(np.float64) / 32768
    r = np.sqrt(np.mean(y ** 2))
    y = y * (10 ** (-26 / 20) / r) if r > 0 else y
    return float(_session().run(None, {"input_1": features(y)[None]})[0][0][0])


def windows(x: np.ndarray, joins: list[int]):
    """(first sample, speech samples, WIN samples) of each window of x (raw speech, concatenated; joins: where a part
    not right after the one before starts), one at a time, with 10 ms fades at the joins; the last one repeated up to
    WIN, if MIN_S or more."""
    joins = np.array([j for j in joins if 160 <= j <= len(x) - 160], np.int64)
    for i in range(0, len(x), WIN):
        n = min(WIN, len(x) - i)
        if n < WIN and n < MIN_S * SR:
            break
        w, p = x[i:i + n].astype(np.float32), np.arange(i, i + n)
        for j in joins[np.searchsorted(joins, i - 160):np.searchsorted(joins, i + n + 160)]:
            m = (p >= j - 160) & (p < j + 160)
            w[m] *= np.where(p[m] >= j, p[m] - j, j - 1 - p[m]) / 159  # linspace(0, 1, 160) out and in
        yield i, n, np.resize(w, WIN)  # np.resize repeats it


def speech(device: str, spans: list) -> tuple[np.ndarray, list[tuple[int, float]]]:
    """The decoded audio of spans (ms, in order) as recorded (the offset undone), concatenated, int16; and each part's
    (first sample in it, its ms)."""
    x, parts, n = [], [], 0
    for a0, b0 in spans:
        for a in range(a0, b0, CHUNK_MS):
            b, filled = min(b0, a + CHUNK_MS), []
            y = late.read_exact(device, a, b, [], filled)
            for i, j, g in gain.segments(device, a, len(y)):
                if g:
                    y = y.astype(np.float32)
                    y[i:j] /= 10 ** (g / 20)
            for s, e in subtract([[a, b]], subtract([[a, b]], union(filled))):
                i, j = round((s - a) * SR / 1000), round((e - a) * SR / 1000)
                x.append(np.round(y[i:j]).astype(np.int16))
                parts.append((n, s))
                n += j - i
    return (np.concatenate(x) if x else np.zeros(0, np.int16)), parts


def score(device: str, a: int) -> int:
    """The quality rows of the sentences of device's hour from a (unix ms; a window may start after it, a sentence
    over the hour's end is its), each in place of one with its start; how many."""
    t = time.thread_time()  # this thread's: ffmpeg's decoding is not in it
    spans = union([[ms(r["start"]), ms(r["end"])] for r in db.sentences(iso(a), iso(a + H), source=device) if r["end"] > r["start"]])
    x, parts = speech(device, spans)
    first = np.array([i for i, _ in parts])

    def at(k: int) -> float:  # sample k's time, ms
        p = int(np.searchsorted(first, k, "right")) - 1
        return parts[p][1] + (k - parts[p][0]) * 1000 / SR
    joins = [i for (i, s), (i0, s0) in zip(parts[1:], parts) if abs(s - (s0 + (i - i0) * 1000 / SR)) > 1]  # not one stretch
    rows = [(device, round(at(i)), round(at(i + n - 1) + 1000 / SR), round(n * 1000 / SR), MODEL,
             p808(w), float(np.mean(np.abs(x[i:i + n].astype(np.int32)) >= FULL)),  # int16 abs(-32768) wraps
             float(20 * np.log10(np.sqrt(np.mean(np.square(x[i:i + n], dtype=np.float64))) / 32768 + 1e-9)), int(time.time() * 1000))
            for i, n, w in windows(x, joins)]
    with db.tx() as c:
        c.executemany("INSERT OR REPLACE INTO quality VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        db.put_kv(f"quality_to:{device}", a + H, c)
    if rows:
        log.info("quality of %s at %s: %d windows, %.1f CPU s", device, iso(a), len(rows), time.thread_time() - t)
    return len(rows)


def todo() -> list[tuple[str, int]]:
    """(device, hour) for each device with an hour to score: the final layer past its end."""
    out, since = [], since_ms()
    for (key,) in db.conn().execute("SELECT key FROM kv WHERE key LIKE 'finalized_to:%'").fetchall():
        device = key.split(":", 1)[1]
        a = max(db.kv(f"quality_to:{device}") or 0, since)
        if (v := db.kv(key)) and a + H <= ms(v):  # null: not yet
            out.append((device, a))
    return out


async def loop():
    """The tenant's quality rows caught up, for good (server.run_tenant, when ON)."""
    while True:
        while hours := await anyio.to_thread.run_sync(todo, limiter=threads.DB):
            for device, a in hours:
                await anyio.to_thread.run_sync(score, device, a, limiter=threads.QUALITY)
        await anyio.sleep(EVERY_S)


def stats(rows: list[tuple]) -> dict | None:
    """(speech_ms, p808, clip) rows -> the day's numbers; None: no rows."""
    if not rows:
        return None
    sp, p, c = (np.array(v, np.float64) for v in zip(*rows))
    return {"windows": len(rows), "speech_min": round(sp.sum() / 60000, 1), "p808": round(float(np.median(p)), 2),
            "low": round(float(sp[p < LOW].sum() / sp.sum()), 3), "clip_min": round(float(sp[c > CLIP].sum()) / 60000, 1)}


def report(device: str, day: dt.date) -> dict | None:
    """One device's day (the instance's wall clock): all, live (not media) and media's stats(); None: no rows."""
    lo = int(dt.datetime.combine(day, dt.time()).timestamp() * 1000)
    hi = int(dt.datetime.combine(day + dt.timedelta(days=1), dt.time()).timestamp() * 1000)
    rows = db.conn().execute("SELECT start_ms, end_ms, speech_ms, p808, clip FROM quality WHERE device = ? AND start_ms >= ? "
                             "AND start_ms < ? ORDER BY start_ms", (device, lo, hi)).fetchall()
    if not rows:
        return None
    spans = [s for c in db.conversations(iso(lo - 86400_000), iso(hi)) if c.get("device") == device for s in summary.not_live(c["summary"])]
    sent = db.sentences(iso(lo - H), iso(hi), source=device)
    a = np.array([ms(r["start"]) for r in sent], np.float64)
    b = np.array([ms(r["end"]) for r in sent], np.float64)
    m = np.array([summary.in_media(r, spans) for r in sent], bool)
    live, media = [], []
    for s, e, *v in rows:
        o = np.clip(np.minimum(b, e) - np.maximum(a, s), 0, None)
        (media if o.sum() and o[m].sum() >= o.sum() / 2 else live).append(v)
    return {"all": stats([v for _, _, *v in rows]), "live": stats(live), "media": stats(media)}


def check():
    """Self-check, no model: the mel basis is librosa's (its row sums at n_mels=120, n_fft=321), windows and stats."""
    w = _mel_basis()
    assert w.shape == (120, 161) and abs(float(w[0].sum()) - 2.127e-5) < 1e-8 and (w >= 0).all(), w[0].sum()
    assert features(np.random.default_rng(0).standard_normal(WIN)).shape == (900, 120)
    x = np.arange(2 * WIN + 3 * SR, dtype=np.float32)
    got = list(windows(x, []))
    assert [(i, n) for i, n, _ in got] == [(0, WIN), (WIN, WIN), (2 * WIN, 3 * SR)] and got[2][2][3 * SR] == x[2 * WIN]
    assert len(list(windows(x[:WIN + SR], []))) == 1
    y = list(windows(np.full(WIN, 1000, np.int16), [500]))[0][2]
    assert y[339] == 1000 and y[340] == 1000 and y[499] == 0 and y[500] == 0 and y[659] == 1000 and y[660] == 1000
    assert stats([(9000, 2.0, 0.0), (6000, 3.5, 0.02), (3000, 4.0, 0.0)]) == \
        {"windows": 3, "speech_min": 0.3, "p808": 3.5, "low": 0.5, "clip_min": 0.1}
