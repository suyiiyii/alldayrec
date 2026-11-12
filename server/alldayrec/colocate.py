"""同处备用: two recorders of one tenant in one place transcribe once. Each recorder's level every half second
(pieces.env, audio.envelope) rises and falls with the room's; two in one place correlate. A device whose audio another
device transcribes, or will (its audio is in, it is not itself standing by), and correlates with it is that one's
standby, unless it hears better (quality: then the other stands by for it, take): its audio is kept, its final pieces
and drafts send nothing to the engine (processed_audio layer `standby`), so no sentences, conversations, summaries,
live analysis or drafts come of it twice. Drafts come before a piece is judged: none either while another recorder in
the same place that hears as well drafts (drafted_by). It takes over once the other's audio stops for GONE_S, the two
no longer correlate (then what it stood by for since they parted is transcribed late), or it hears better by
SWITCH_DB over two windows on end. A recorder just started waits up to FIRST_WAIT_S for a window to be judged on,
rather than transcribe its first minutes again. Late audio (late.py) is left out where another device in the same
place transcribed that time: first come, first transcribed. Not judged (no envelope: older pieces, quiet, a gap): as
before this, each transcribes its own.

Thresholds from envelopes of stored audio on a test instance, 0.5 s frames, 5 min windows, best lag within 6 s:
11-01, two recorders on one person 14 h: together r 0.49-0.96 (most above 0.6), apart -0.26-0.41; 603 controls
shifted 10-40 min: max 0.51. 11-05, a bean and a phone, 3.9 h both recording: in one room r 0.58-0.95, dips to
0.26-0.48 in quiet stretches; one 30 dB quieter (elsewhere or covered) -0.06-0.41; 609 controls shifted 10-40 min:
max 0.51.
quality(), 11-12, a recorder and one 27 dB quieter (its gain) on one person 1.4 h: their levels over the floor alike
(17-24 dB), the quiet one's speech under QUIET_DB; in the 37 windows together the louder 10-32 dB better (median 29),
and transcribed 289 characters where the quiet one 54 in the same 6 min."""
import datetime as dt
import logging
import time

import numpy as np

from alldayrec import db, late_store, squeeze
from alldayrec.audio import ENV_MS, devices
from alldayrec.late_store import subtract, union
from alldayrec.tenant import T

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
QUIET_DB = squeeze.LIFT_TARGET_DB - squeeze.LIFT_MAX_DB  # quality(): speech under this reaches the engine too quiet
LOUD_DB = -12       # quality(): a frame's level over this clips (speech peaks some 12 dB over its mean)
BAD_DB = 30         # quality(): what a window all missing or clipped costs (a tenth of it: 3 dB)
GONE_S = 90         # the other device sent nothing this long after the piece: it is gone, take over

SWITCH_DB = 6       # a standby that hears better by this over two WINDOW_S transcribes (11-05/06, a bean and a
                    # recorder 10 h together: 6 dB over one window switched back a minute after; over two, 7 switches,
                    # 14 min apart at least, each where the bean clipped or the talk got loud)
FIRST_WAIT_S = 180  # a device just started waits this long after a piece at most for the window that judges it
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


def quality(device: str, a: int, b: int) -> float | None:
    """How well the device hears [a, b), in dB: the loud part of its levels (90th percentile: speech) over its floor
    (10th: the room, its own noise, mains hum), less what that falls short of QUIET_DB, less BAD_DB for all of the
    window missing (no audio, zeros from the mic) or clipped (frames over LOUD_DB). None: no level in it."""
    v = -levels(device, a, b)
    ok = v[~np.isnan(v)]
    if not len(ok):
        return None
    lo, hi = np.percentile(ok, [10, 90])
    return float(hi - lo - max(0.0, QUIET_DB - hi) - BAD_DB * (1 - (ok < LOUD_DB).sum() / len(v)))


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


def started(device: str, lo: int, hi: int) -> int:
    """Where the device's levels start in [lo, hi): lo, or later when it started in it (under MIN_FRAMES has levels)."""
    v = ~np.isnan(levels(device, lo, hi))
    return lo if v.mean() >= MIN_FRAMES or not v.any() else lo + int(np.argmax(v)) * ENV_MS


def lead(x: str, y: str, end: int, windows: int = 1) -> float | None:
    """How much better x hears than y (quality, dB) in the WINDOW_S before end, the least of `windows` such back to
    back; None: one has no level in one, or (more than one: a switch) levels in under MIN_FRAMES of one (11-06: the
    window before the last was from before a recorder came back from a gap, it was switched back 13 s after)."""
    w, d = WINDOW_S * 1000, []
    for e in range(end, end - windows * w, -w):
        if windows > 1 and any((~np.isnan(levels(z, e - w, e))).mean() < MIN_FRAMES for z in (x, y)):
            return None
        if (qx := quality(x, e - w, e)) is None or (qy := quality(y, e - w, e)) is None:
            return None
        d.append(qx - qy)
    return min(d)


def stood_for(y: str) -> list[str]:
    """The devices standing by for y."""
    return [k[len("standby:"):] for k, in db.conn().execute(
        "SELECT key FROM kv WHERE key LIKE 'standby:%' AND json_extract(value, '$.of') = ?", (y,))]


def busy(y: str, a: int, b: int) -> bool:
    """y's final layer has a piece of [a, b) on its way (cut, at the engine, not committed): it commits it as y's."""
    st = T().devices.get(y)
    return bool(st and st.cut_to and st.done_to and st.done_to < st.cut_to and ms(st.done_to) < b and ms(st.cut_to) > a)


def take(device: str, y: str, a: int, by: float):
    """device transcribes from a, y stands by for it (y's own decide() goes on from there)."""
    with db.tx() as t:
        db.put_kv(f"standby:{y}", {"of": device, "since": a}, t)
        db.put_kv(f"standby:{device}", None, t)
    log.info("%s transcribes for %s from %s: hears it better (%.1f dB)", device, y,
             dt.datetime.fromtimestamp(a / 1000), by)


def decide(device: str, a: int, b: int, now_ms: int | None = None) -> str | None:
    """Of a final piece [a, b) (unix ms) of speech: the device it is left to (standby), WAIT, or None (transcribe it).
    Of two in one place the one that hears better (quality) transcribes; the other stands by.
    Standing by: stays while the other's audio covers the piece and the window around its middle does not fall below
    R_APART (it waits for the audio after the piece: a standby is not read, it may run a few minutes behind); its
    audio missing GONE_S after the piece, apart, or no levels from it (zeros, a stuck input): takes over. Hearing
    better by SWITCH_DB over each of the last two WINDOW_S (over one, it switched back a minute later): it transcribes
    and the other stands by for it (take), when no third stands by for that one. Its pieces before the take (since:
    the other may take it ahead of where it is) are judged as if not standing by.
    Not standing by: leaves the piece to another whose audio covers it and whose last WINDOW_S correlate R_TOGETHER,
    that has transcribed it, or will: one that hears as well or better, is no standby (none waits on the other) and
    has not left any of it to another (so the last of them to get to a piece transcribes it); in the window after a
    takeover it does not stand by again. One that hears worse stands by for this one (take): of the piece it has
    transcribed, that is left to it, the rest goes to the late layer (split); a piece of it at the engine: WAIT
    (busy). Not one that took over in the last window (apart, gone: its own call). Just started (under MIN_FRAMES of
    its levels in the window): judged on the WINDOW_S from its first level, waiting for the audio up to FIRST_WAIT_S
    after the piece. Transcribing again after leaving pieces to another (but for gone): resume()."""
    now_ms = round(time.time() * 1000) if now_ms is None else now_ms
    w = WINDOW_S * 1000
    sb = db.kv(f"standby:{device}") or {}
    if (y := sb.get("of")) and b > sb.get("since", 0):  # before since (taken ahead of it: take) as before
        why = None
        mid = (a + b) // 2
        lo, hi = min(a, mid - w // 2), max(b, mid + w // 2)
        if (s := max(started(device, lo, hi), started(y, lo, hi))) > lo:  # one just started: the window from there
            lo, hi = s, max(b, s + w)
        if audio_in(y, a, b) >= COVER:
            if now_ms < hi + GONE_S * 1000 and min(audio_end(device), audio_end(y)) < hi:
                return WAIT
            r = correlation(device, y, lo, hi)
            if r is None and dead(y, device, lo, hi):
                why = "no levels from it"
            elif r is not None and r < R_APART:
                why = f"apart (r {r:.2f})"
            elif ((by := lead(device, y, b, 2)) or 0) >= SWITCH_DB and not standby_of(y) and stood_for(y) == [device]:
                take(device, y, a, by)  # on as one transcribing: what y has of this piece stays y's
            elif db.processed(y, a, b, "standby") and transcribed(y, a, b) < COVER:
                # y left (some of) it to a third device, or had it taken (take, ahead of this one): this one cannot tell
                # that one will have it; what y will have is still left to y (split)
                return y if meet([[a, b]], has(y, a, b)) else None
            else:
                return y
        elif now_ms < b + GONE_S * 1000:
            return WAIT
        else:
            why = "gone"
        if why:
            with db.tx() as t:
                db.put_kv(f"standby:{device}", None, t)
                db.put_kv(f"standby_left:{device}", b, t)
                if why != "gone":  # gone: the other's audio covered all it left, the other transcribes that
                    resume(t, device, a, now_ms)
            log.info("%s takes over from %s at %s: %s", device, y, dt.datetime.fromtimestamp(a / 1000), why)
            return None
    if b - w < (db.kv(f"standby_left:{device}") or 0):
        return None
    together = False
    for y in others(device, a, b):
        if audio_in(y, a, b) < COVER:
            continue
        end = b
        if (s := started(device, b - w, b)) > b - w:  # just started: the window from its start
            end = s + w
            if min(audio_end(device), audio_end(y)) < s + MIN_FRAMES * w and now_ms < b + FIRST_WAIT_S * 1000:
                return WAIT
        if (r := correlation(device, y, end - w, end)) is None or r < R_TOGETHER:
            continue
        together = True
        by = lead(device, y, end)
        if (by is not None and by > 0 and not standby_of(y) and not stood_for(y)
                and (db.kv(f"standby_left:{y}") or 0) <= b - w):  # not in the window y took over in (apart: its own call)
            take(device, y, a, by)
        if transcribed(y, a, b) >= COVER:
            return y  # first come: y has it already, whatever it does now
        if standby_of(y):
            if busy(y, a, b):
                return WAIT  # taken while its piece was at the engine: that piece is y's
            if transcribed(y, a, b) > 0:
                return y  # what y transcribed before it stood by stays y's, the rest goes to the late layer (split)
            continue
        if (by is None or by <= 0) and not db.processed(y, a, b, "standby"):  # never to one that left it: nobody has it
            db.put_kv(f"standby:{device}", {"of": y, "since": a})
            log.info("%s stands by for %s from %s (r %.2f)", device, y, dt.datetime.fromtimestamp(a / 1000), r)
            return y
    # it left the piece before to another (first come), no longer: the newest it did in the window is leaving, not
    # transcribing (the end of that piece may have gone to its late layer: split). Not while one in the same place
    # stands by for it (take): that is no parting, what it left the other has
    sb, fin = (max((r[1] for r in db.processed(device, a - w, a, layer)), default=0) for layer in ("standby", "final"))
    if sb > fin and not together:
        with db.tx() as t:
            resume(t, device, a, now_ms)
    return None


def drafted_by(device: str, now_ms: int | None = None) -> str | None:
    """Another recorder drafting now (device_pipeline: drafting()), no standby, whose last WINDOW_S correlate with
    device's R_TOGETHER, unless device hears better by SWITCH_DB (quality, as a standby switches in decide(); by
    less, of two drafting the first to look still stops: no turns as the scores wander): device's drafts would be
    its sentences again. Not left to decide(): it judges a piece once the piece is cut, a minute or more after its drafts (11-12: a
    recorder's first speech after its first window was drafted twice for 90 s before it stood by)."""
    now_ms = round(time.time() * 1000) if now_ms is None else now_ms
    w = WINDOW_S * 1000
    return next((y for y, st in T().devices.items() if y != device and st.drafting and not st.standby
                 and (r := correlation(device, y, now_ms - w, now_ms)) is not None and r >= R_TOGETHER
                 and (lead(device, y, now_ms) or 0) < SWITCH_DB), None)


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


def stood_by(device: str, start: str, end: str) -> list[str]:
    """Recorders other than device that stood by somewhere in [start, end] (iso) in the same place as device (their
    levels over WINDOW_S around that correlate R_TOGETHER: not one that stood by for a third device elsewhere): their audio of it is kept,
    to listen to."""
    a, b = (ms(dt.datetime.fromisoformat(x)) for x in (start, end))
    out = []
    for d in devices():
        if d != device and (rows := db.processed(d, a, b, "standby")):
            lo, hi = max(a, min(r[0] for r in rows)), min(b, max(r[1] for r in rows))
            mid, w = (lo + hi) // 2, WINDOW_S * 1000  # a short conversation: still WINDOW_S around it (as decide)
            if (r := correlation(device, d, min(lo, mid - w // 2), max(hi, mid + w // 2))) is not None and r >= R_TOGETHER:
                out.append(d)
    return out
