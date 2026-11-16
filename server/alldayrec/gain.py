"""Each device's gain, one fixed number per device and time: what every reader of its audio (the models and the people
listening) gets, the stored audio left as it came.

Every device is brought to one standard level: the wearer's own speech at TARGET_DBOV active speech level (ITU-T P.56).
Two steps:
1. capture gain, on the device: a recorder's PDM gain, a phone's gain before its encoder; as high as the loudest real
   sound allows without clipping. capture_db is what the device says it set (mic_gain_db in a recorder's status,
   capture_gain_db with a phone's piece: source 'reported') or what someone wrote down ('manual');
2. calibration offset, here: offset_db, a float gain on the decoded audio, lossless, that maps the device's level to
   TARGET_DBOV. Set by hand (python -m alldayrec.gain, server/gain.sh), never by a client.

Rows in device_gain (live.db, the tenant's own) hold from since_ms on: for (device, t) each column is its newest
non-NULL value with since_ms <= t, so a reported capture gain never hides an offset set by hand. No row: offset 0.
Each sample takes the offset of its own time: a piece (or a cut, a listening poll) over a since_ms is split there.

apply() is called at audio.py's three per-piece exits (samples, piece_window, piece_blocks): every decoded piece the
pipeline (VAD, ASR, voiceprints, music, sound scenes), the late layer, the trim's VAD, playback and listening read has
it. With an offset it hands out float32 (int16's scale, not rounded, not limited), which goes on as float32 to every
model (VAD, voiceprints, music, segmentation, the infer service as f32le); it becomes int16 once, in to16() (soft
limited past KNEE, counted), only where int16 is needed: a played WAV and what an ASR engine is sent (after
squeeze.lift). Then come the adaptive gains (VAD_GAIN_MAX_DB, ASR_GAIN_MAX_DB: squeeze.lift), on audio already
offset. Uploads' stored levels (pieces.rms, env) and the stored bytes stay those of the audio as it came. With no
offset every exit is int16, as before this table.
"""
import argparse
import datetime as dt
import logging
import math
import sys

import numpy as np

from alldayrec import db
from alldayrec.tenant import T

log = logging.getLogger("live")
TARGET_DBOV = -26.0  # the wearer's own speech, ITU-T P.56 active speech level, on every device
KNEE = 0.9  # to16(): where the soft limiter starts, of full scale; under it every sample is only rounded
MAX_DB = 60.0  # a gain (reported, set or written down) further from 0 than this is not a gain
MAX_MS = int(dt.datetime(9999, 1, 1).timestamp() * 1000)  # a since_ms the list can still print
SR = 16000  # audio.SR (audio imports this module)


def offset_db(device: str, at_ms: float) -> float | None:
    """The calibration offset of the device's audio at at_ms; None: none set (0 dB, and played() keeps PLAY_GAIN_DB)."""
    r = db.conn().execute("SELECT offset_db FROM device_gain WHERE device = ? AND since_ms <= ? AND offset_db IS NOT NULL "
                          "ORDER BY since_ms DESC LIMIT 1", (device, math.floor(at_ms))).fetchone()
    return None if r is None else r[0]


def only_db(device: str) -> float | None:
    """The device's offset when it is one value for all time (its first offset row from since 0, every offset the same):
    what a clip of no time (a history voice's, clip_start NULL) takes; None: none set, or not one value."""
    r = db.conn().execute("SELECT MIN(since_ms) <= 0 AND COUNT(DISTINCT offset_db) = 1, MAX(offset_db) FROM device_gain "
                          "WHERE device = ? AND offset_db IS NOT NULL", (device,)).fetchone()
    return r[1] if r[0] else None


def capture_db(device: str, at_ms: int) -> float | None:
    """The capture gain the device had at at_ms, as reported or written down (at the same since_ms: the manual one);
    None: unknown."""
    r = db.conn().execute("SELECT capture_db FROM device_gain WHERE device = ? AND since_ms <= ? AND capture_db IS NOT NULL "
                          "ORDER BY since_ms DESC, source = 'manual' DESC LIMIT 1", (device, at_ms)).fetchone()
    return None if r is None else r[0]


def segments(device: str, at_ms: float, n: int) -> list[tuple[int, int, float | None]]:
    """n samples of the device from at_ms (unix ms, sample i at at_ms + i / 16 ms) split where its offset changes:
    (from, to, offset or None), each sample at the offset of its own time (since_ms <= its time)."""
    out, i, g = [], 0, offset_db(device, at_ms)
    for since, h in db.conn().execute("SELECT since_ms, offset_db FROM device_gain WHERE device = ? AND since_ms > ? AND "
                                      "since_ms < ? AND offset_db IS NOT NULL ORDER BY since_ms",
                                      (device, math.floor(at_ms), at_ms + n * 1000 / SR)):
        k = min(n, math.ceil((since - at_ms) * SR / 1000 - 1e-6))  # its first sample at or after since
        if k > i:
            out.append((i, k, g))
        i, g = max(i, k), h
    return out + [(i, n, g)] if n > i or not out else out


def apply(x: np.ndarray, device: str, at_ms: float) -> np.ndarray:
    """x (int16, the device's from at_ms) with its offset, each sample at its own time's (segments): x itself where
    none is set or all are 0, else float32 at int16's scale (x * 10^(dB/20), not rounded, not limited: past full scale
    too), which every model takes as it is; to16() makes it int16 where int16 is needed (a WAV, an ASR engine)."""
    segs = [s for s in segments(device, at_ms, len(x)) if s[2]]
    if not segs:
        return x
    y = x.astype(np.float32)
    for a, b, g in segs:
        y[a:b] *= np.float32(10 ** (g / 20))
    return y


def to16(y: np.ndarray, device: str | None = None) -> np.ndarray:
    """y as int16: an int16 y itself; a float one (apply's) rounded, its samples past KNEE of full scale through a
    tanh soft limiter that keeps them under full scale (no hard clip) and counted per device (T().gain_limited:
    lr_gain_limited_samples_total, samples that went into the limiter on the way to int16). Under KNEE: only rounded."""
    if y.dtype == np.int16:
        return y
    z = y.astype(np.float64) / 32768  # exact: /32768 and *32768 below do not move a sample
    a = np.abs(z)
    if (over := a > KNEE).any():
        if device is not None:
            T().gain_limited[device] = T().gain_limited.get(device, 0) + int(np.count_nonzero(over))
        z[over] = np.sign(z[over]) * (KNEE + (1 - KNEE) * np.tanh((a[over] - KNEE) / (1 - KNEE)))
    return np.clip(np.round(z * 32768), -32767, 32767).astype(np.int16)  # far past full scale tanh reaches 1: full scale


def db_value(value, what: str) -> float | None:
    """value as dB when it is a finite number within +-MAX_DB, else None (logged): never a row of its."""
    ok = not isinstance(value, bool) and isinstance(value, (int, float)) and (
        abs(value) <= MAX_DB if isinstance(value, int) else math.isfinite(value) and abs(value) <= MAX_DB)  # a huge int: no float
    if not ok:
        log.warning("gain: %s %.40r is not a gain (a finite number within +-%g dB), not kept", what, value, MAX_DB)
        return None
    return float(value)


def report(device: str, at_ms: int, value) -> bool:
    """A capture gain a client says the device has from at_ms on (a recorder's mic_gain_db, a phone's capture_gain_db):
    a 'reported' row when it differs from the one known at at_ms (or none is); a second report at the same ms replaces
    the first. Not a gain (db_value): ignored. It never changes the audio: only offset_db does."""
    if value is None or (value := db_value(value, f"{device} reported capture gain")) is None:
        return False
    known = capture_db(device, at_ms)
    if known is not None and abs(known - value) < 1e-6:
        return False
    db.conn().execute("INSERT OR REPLACE INTO device_gain VALUES (?, ?, ?, NULL, 'reported', NULL)", (device, at_ms, value))
    return True


def rows(device: str | None = None) -> list[dict]:
    """The rows (of one device), each device's in time order."""
    return [dict(r) for r in db.conn().execute("SELECT * FROM device_gain WHERE ? IS NULL OR device = ? ORDER BY device, since_ms",
                                               (device, device))]


def put(device: str, since_ms: int, offset: float | None, capture: float | None = None, note: str | None = None):
    """A 'manual' row: what someone measured (offset) or wrote down (capture); the same device and since_ms replaced.
    ValueError (nothing written) when a value is not a gain (db_value) or since_ms no time."""
    for v, what in ((offset, "offset"), (capture, "capture gain")):
        if v is not None and db_value(v, f"{device} {what}") is None:
            raise ValueError(f"{what} {v!r}: a finite number of dB within +-{MAX_DB:g}")
    if isinstance(since_ms, bool) or not isinstance(since_ms, int) or not 0 <= since_ms <= MAX_MS:
        raise ValueError(f"since {since_ms!r}: unix ms from 0 to {MAX_MS}")
    db.conn().execute("INSERT OR REPLACE INTO device_gain VALUES (?, ?, ?, ?, 'manual', ?)",
                      (device, since_ms, None if capture is None else float(capture), None if offset is None else float(offset), note))


def _ms(s: str) -> int:
    return int(s) if s.lstrip("-").isdigit() else int(dt.datetime.fromisoformat(s).timestamp() * 1000)


def main(argv=None):
    """python -m alldayrec.gain [--tenant ID] list [DEVICE] | set DEVICE OFFSET_DB [--since T] [--capture DB] [--note TEXT],
    on the tenants of DATA_DIR/tenants.json (every one for list without --tenant), against the store a running server
    already brought up to date. server/gain.sh runs it on the deploy host."""
    from alldayrec import tenant
    ap = argparse.ArgumentParser(prog="python -m alldayrec.gain", description=main.__doc__)
    ap.add_argument("--tenant")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="every row, and each device's offset and capture gain now")
    ls.add_argument("device", nargs="?")
    st = sub.add_parser("set", help="a device's calibration offset from --since on")
    st.add_argument("device")
    st.add_argument("offset_db", type=float)
    st.add_argument("--since", default="0", help="ISO time or unix ms (default 0: all of its audio)")
    st.add_argument("--capture", type=float, help="the capture gain it was measured at, written down")
    st.add_argument("--note")
    a = ap.parse_args(argv)
    ts = [t for t in tenant.load(tenant.FILE) if a.tenant in (None, t.id)]
    if not ts:  # exit 3: gain.sh tells it from a failure (1)
        print(f"no tenant {a.tenant} here", file=sys.stderr)
        sys.exit(3)
    if a.cmd == "set":
        if len(ts) > 1:
            sys.exit("set: which tenant (--tenant)")
        try:
            since = _ms(a.since)
        except (ValueError, OverflowError, OSError):
            sys.exit(f"--since {a.since}: an ISO time or unix ms")
    now = int(dt.datetime.now().timestamp() * 1000)
    for t in ts:
        tenant.enter(t)
        if db.conn().execute("SELECT 1 FROM sqlite_master WHERE name = 'device_gain'").fetchone() is None:
            sys.exit(f"{t.id}: no device_gain table; start the server on this version first")
        if a.cmd == "set":
            try:
                put(a.device, since, a.offset_db, a.capture, a.note)
            except ValueError as e:
                sys.exit(f"set: {e}")
        for r in rows(a.device):
            print(t.id, r["device"], dt.datetime.fromtimestamp(r["since_ms"] / 1000).isoformat(timespec="seconds"),
                  f"offset={r['offset_db']}", f"capture={r['capture_db']}", r["source"], r["note"] or "", sep="\t")
        for d in sorted({r["device"] for r in rows(a.device)}):
            print(t.id, d, "now", f"offset={offset_db(d, now) or 0:g}", f"capture={capture_db(d, now)}", sep="\t")


if __name__ == "__main__":
    main()
