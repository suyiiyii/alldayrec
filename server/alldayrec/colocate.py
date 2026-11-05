"""同处备用: two recorders of one tenant in one place transcribe once. Each recorder's level every half second
(pieces.env, audio.envelope) rises and falls with the room's; two in one place correlate. A device whose audio another
device transcribes, or will (its audio is in, it is not itself standing by), and correlates with it is that one's
standby: its audio is kept, its final pieces and drafts send nothing to the engine (processed_audio layer `standby`),
so no sentences, conversations, summaries, live analysis or drafts come of it twice. It takes over once the other's
audio stops for GONE_S, or the two no longer correlate (then what it stood by for since they parted is transcribed
late). Late audio (late.py) is left out where another device in the same place transcribed that time: first come,
first transcribed. Not judged (no envelope: older pieces, quiet, a gap): as before this, each transcribes its own.

Thresholds from envelopes of stored audio on a test instance, 0.5 s frames, 5 min windows, best lag within 6 s:
11-01, two recorders on one person 14 h: together r 0.49-0.96 (most above 0.6), apart -0.26-0.41; 603 controls
shifted 10-40 min: max 0.51. 11-05, a bean and a phone, 3.9 h both recording: in one room r 0.58-0.95, dips to
0.26-0.48 in quiet stretches; one 30 dB quieter (elsewhere or covered) -0.06-0.41; 609 controls shifted 10-40 min:
max 0.51."""
import datetime as dt
import logging
import time

import numpy as np

from alldayrec import db, late_store
from alldayrec.audio import ENV_MS
from alldayrec.late_store import subtract, union

log = logging.getLogger("live")

WINDOW_S = 300      # the correlation window: 120 s let a shifted control reach 0.76 on 11-01, 300 s 0.51
MAX_LAG_S = 6       # the two clocks may be this far apart (#233: a bean loses ~0.3 s each 10 min; 11-05 ~2 s apart)
R_TOGETHER = 0.6    # a device becomes standby above this (11-01 controls: none of 603 reached it)
R_APART = 0.3       # ... and takes over below it. Apart measured -0.26-0.41 (median 0.08), together dips to 0.26 in
                    # quiet: apart at 0.3-0.41 stands by on until a window under 0.3 (resume gives the last WINDOW_S back)
MIN_FRAMES = 0.6    # of a window's frames both have, or not judged (a gap, a device just started)
PART_S = 60         # late audio is judged by parts this long (left)
COVER = 0.9         # of a piece the other device's audio (or transcript) covers, to leave it that piece (what of it the
                    # other has: split)
GONE_S = 90         # the other device sent nothing this long after the piece: it is gone, take over

WAIT = "wait"       # decide(): not yet, look again in a moment


def ms(t: dt.datetime) -> int:
    return round(t.timestamp() * 1000)


def imported(device: str) -> bool:
    from alldayrec.monitor import imported
    return imported(device)


def levels(device: str, a: int, b: int) -> np.ndarray:
    """The device's level (-dBFS) each ENV_MS of [a, b) (unix ms), NaN where it has none."""
    out = np.full((b - a) // ENV_MS, np.nan)
    for start, env in db.conn().execute("SELECT start_ms, env FROM pieces WHERE device = ? AND start_ms < ? AND start_ms >= ? "
                                        "AND env IS NOT NULL ORDER BY start_ms", (device, b, a - 3600_000)):
        v = np.frombuffer(env, np.uint8).astype(np.float64) / 2
        v[v > 127] = np.nan  # 255: zeros from the mic
        k = round((start - a) / ENV_MS)
        lo, hi = max(0, k), min(len(out), k + len(v))
        if lo < hi:
            out[lo:hi] = v[lo - k:hi - k]
    return out


def correlation(x: str, y: str, a: int, b: int) -> float | None:
    """The two devices' levels over [a, b) correlated (Pearson), at the best of the lags within MAX_LAG_S; None when
    under MIN_FRAMES of the window has both, or one is flat."""
    lag = MAX_LAG_S * 1000 // ENV_MS
    X, Y = levels(x, a, b), levels(y, a - lag * ENV_MS, b + lag * ENV_MS)
    best = None
    for k in range(2 * lag + 1):
        y_ = Y[k:k + len(X)]
        m = ~np.isnan(X) & ~np.isnan(y_)
        if m.sum() < MIN_FRAMES * len(X) or np.ptp(X[m]) == 0 or np.ptp(y_[m]) == 0:
            continue
        r = float(np.corrcoef(X[m], y_[m])[0, 1])
        best = r if best is None else max(best, r)
    return best


def dead(y: str, x: str, a: int, b: int) -> bool:
    """y gives no usable level where x has one in [a, b): none (zeros from its mic, no envelope) in over 1 - MIN_FRAMES
    of it, or one constant level (an input stuck at a value)."""
    X, Y = levels(x, a, b), levels(y, a, b)
    Y = Y[~np.isnan(X)]
    return len(Y) > 0 and (float(np.isnan(Y).mean()) > 1 - MIN_FRAMES or np.ptp(Y[~np.isnan(Y)]) == 0)


def meet(ranges: list, covers: list) -> list:
    """The parts of ranges that covers cover."""
    return subtract(ranges, subtract(ranges, covers))


def share(ranges: list, a: int, b: int) -> float:
    """How much of [a, b) the ranges cover."""
    return 1 - sum(hi - lo for lo, hi in subtract([[a, b]], ranges)) / max(1, b - a)


def audio(device: str, a: int, b: int) -> list:
    """The device's stored audio in [a, b) (unix ms ranges)."""
    return [[s, round(s + sec * 1000)] for s, sec in db.conn().execute(
        "SELECT start_ms, seconds FROM pieces WHERE device = ? AND start_ms < ? AND start_ms >= ?", (device, b, a - 3600_000))
        if s + sec * 1000 > a]


def audio_in(device: str, a: int, b: int) -> float:
    """How much of [a, b) the device's stored audio covers."""
    return share(audio(device, a, b), a, b)


def done(device: str, a: int, b: int) -> list:
    """What of [a, b) the device has transcribed (its final and late layers, not what it stood by for)."""
    return [r for layer in ("final", "late") for r in db.processed(device, a, b, layer)]


def transcribed(device: str, a: int, b: int) -> float:
    return share(done(device, a, b), a, b)


def has(y: str, a: int, b: int) -> list:
    """What of [a, b) y has transcribed, or will: its audio, but for what it left to another, unless it stands by."""
    if standby_of(y):
        return done(y, a, b)
    return union(done(y, a, b) + subtract(audio(y, a, b), db.processed(y, a, b, "standby")))


def split(device: str, y: str, heard: list, now_ms: int | None = None) -> list:
    """Of a piece decide() left to y (heard: its ranges), what y has (returned, to record as left to it); the rest (a hole
    in y's audio, the end of a piece y transcribed before this one stood by) goes to this device's late layer."""
    skip = meet(heard, has(y, heard[0][0], heard[-1][1]))
    if rest := late_store.substantial(subtract(heard, skip)):
        with db.tx() as t:
            late_store.enqueue_ranges(t, device, rest, round(time.time() * 1000) if now_ms is None else now_ms, ready=True)
    return skip


def audio_end(device: str) -> int:
    return db.conn().execute("SELECT max(start_ms + seconds * 1000) FROM pieces WHERE device = ?", (device,)).fetchone()[0] or 0


def others(device: str, a: int, b: int) -> list[str]:
    """Recorders other than device with audio in [a, b) (imports are no recorder)."""
    return [d for d, in db.conn().execute("SELECT DISTINCT device FROM pieces WHERE start_ms < ? AND start_ms > ? AND device != ?",
                                          (b, a - 3600_000, device)) if not imported(d)]


def standby_of(device: str) -> str | None:
    return (db.kv(f"standby:{device}") or {}).get("of")


def decide(device: str, a: int, b: int, now_ms: int | None = None) -> str | None:
    """Of a final piece [a, b) (unix ms) of speech: the device it is left to (standby), WAIT, or None (transcribe it).
    Standing by: stays while the other's audio covers the piece and the window around its middle does not fall below
    R_APART (it waits for the audio after the piece: a standby is not read, it may run a few minutes behind); its
    audio missing GONE_S after the piece, apart, or no levels from it (zeros, a stuck input): takes over.
    Not standing by: leaves the piece to another whose audio covers it and whose last WINDOW_S correlate R_TOGETHER,
    that has transcribed it, or will: one that is no standby (none waits on the other) and has not left any of it to
    another (so the last of them to get to a piece transcribes it); in the window after a takeover it does not stand
    by again. Transcribing again after leaving pieces to another (but for gone): resume()."""
    now_ms = round(time.time() * 1000) if now_ms is None else now_ms
    w = WINDOW_S * 1000
    if y := standby_of(device):
        mid = (a + b) // 2
        lo, hi = min(a, mid - w // 2), max(b, mid + w // 2)
        if audio_in(y, a, b) >= COVER:
            if now_ms < hi + GONE_S * 1000 and min(audio_end(device), audio_end(y)) < hi:
                return WAIT
            r = correlation(device, y, lo, hi)
            if r is None and dead(y, device, lo, hi):
                why = "no levels from it"
            elif r is not None and r < R_APART:
                why = f"apart (r {r:.2f})"
            elif db.processed(y, a, b, "standby") and transcribed(y, a, b) < COVER:
                return None  # y left it to a third device: this one cannot tell that one will have it
            else:
                return y
        elif now_ms < b + GONE_S * 1000:
            return WAIT
        else:
            why = "gone"
        with db.tx() as t:
            db.put_kv(f"standby:{device}", None, t)
            db.put_kv(f"standby_left:{device}", b, t)
            if why != "gone":  # gone: the other's audio covered all it left, the other transcribes that
                resume(t, device, a, now_ms)
        log.info("%s takes over from %s at %s: %s", device, y, dt.datetime.fromtimestamp(a / 1000), why)
        return None
    if b - w < (db.kv(f"standby_left:{device}") or 0):
        return None
    for y in others(device, a, b):
        if audio_in(y, a, b) < COVER or (r := correlation(device, y, b - w, b)) is None or r < R_TOGETHER:
            continue
        if transcribed(y, a, b) >= COVER:
            return y  # first come: y has it already, whatever it does now
        if not standby_of(y) and not db.processed(y, a, b, "standby"):  # never to one that left it already: nobody would have it
            db.put_kv(f"standby:{device}", {"of": y, "since": a})
            log.info("%s stands by for %s from %s (r %.2f)", device, y, dt.datetime.fromtimestamp(a / 1000), r)
            return y
    if db.processed(device, a - 1000, a, "standby"):  # it left the piece before to another (first come): no longer
        with db.tx() as t:
            resume(t, device, a, now_ms)
    return None


def resume(t, device: str, a: int, now_ms: int):
    """A device transcribes again from a after leaving pieces to another: what it left in the WINDOW_S before goes to
    the late layer (the correlation that let it go shows a parting a minute or two late), which leaves out again the
    parts the other has in the same place (left) and transcribes the rest."""
    where = "FROM processed_audio WHERE device = ? AND layer = 'standby' AND start_ms < ? AND end_ms > ?"  # whole rows
    back = [list(r) for r in t.execute("SELECT start_ms, end_ms " + where, (device, a, a - WINDOW_S * 1000))]
    t.execute("DELETE " + where, (device, a, a - WINDOW_S * 1000))
    late_store.enqueue_ranges(t, device, back, now_ms, ready=True)


def left(device: str, ranges: list) -> list:
    """Of late audio (unix ms ranges), what another device in the same place transcribed: by PART_S parts, in each one
    another's transcript covers COVER of and whose WINDOW_S around it correlates R_TOGETHER, what that transcript
    covers. Reads the store: off the event loop (late.py)."""
    w, out = WINDOW_S * 1000, []
    for a, b in union(ranges):
        for lo in range(a, b, PART_S * 1000):
            hi = min(b, lo + PART_S * 1000)
            mid = (lo + hi) // 2
            for y in others(device, lo, hi):
                if transcribed(y, lo, hi) >= COVER and (r := correlation(device, y, mid - w // 2, mid + w // 2)) is not None and r >= R_TOGETHER:
                    out += meet([[lo, hi]], done(y, lo, hi))
                    break
    return out
