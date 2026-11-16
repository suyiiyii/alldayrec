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

apply() is called at audio.py's three per-piece exits (samples, piece_window, piece_blocks): every decoded piece the
pipeline (VAD, ASR, voiceprints, music, sound scenes), the late layer, the trim's VAD, playback and listening read has
it. Then come the adaptive gains (VAD_GAIN_MAX_DB, ASR_GAIN_MAX_DB: squeeze.lift), on audio already offset. Uploads'
stored levels (pieces.rms, env) and the stored bytes stay those of the audio as it came.
"""
import argparse
import datetime as dt
import math
import sys

import numpy as np

from alldayrec import db
from alldayrec.tenant import T

TARGET_DBOV = -26.0  # the wearer's own speech, ITU-T P.56 active speech level, on every device
KNEE = 0.9  # apply(): where the soft limiter starts, of full scale; a sample offset past +-1 comes out under 1
REPORTED_MAX_DB = 60.0  # a reported capture gain further from 0 than this is not a gain


def offset_db(device: str, at_ms: int) -> float | None:
    """The calibration offset of the device's audio at at_ms; None: none set (0 dB, and played() keeps PLAY_GAIN_DB)."""
    r = db.conn().execute("SELECT offset_db FROM device_gain WHERE device = ? AND since_ms <= ? AND offset_db IS NOT NULL "
                          "ORDER BY since_ms DESC LIMIT 1", (device, at_ms)).fetchone()
    return None if r is None else r[0]


def capture_db(device: str, at_ms: int) -> float | None:
    """The capture gain the device had at at_ms, as reported or written down; None: unknown."""
    r = db.conn().execute("SELECT capture_db FROM device_gain WHERE device = ? AND since_ms <= ? AND capture_db IS NOT NULL "
                          "ORDER BY since_ms DESC LIMIT 1", (device, at_ms)).fetchone()
    return None if r is None else r[0]


def apply(x: np.ndarray, device: str, at_ms: int) -> np.ndarray:
    """x (int16, a piece of the device starting at at_ms) with its offset: x itself when there is none or it is 0.
    Multiplied in float64; past KNEE a tanh soft limiter keeps it under full scale (no hard clip), and the samples that
    would have passed +-1 are counted per device (T().gain_limited: lr_gain_limited_samples_total). Back to int16, so a
    negative offset rounds."""
    g = offset_db(device, at_ms)
    if not g:
        return x
    y = x * (10 ** (g / 20) / 32768)  # float64: x times the gain exactly (/32768 is exact)
    a = np.abs(y)
    if (over := a > KNEE).any():
        if n := int(np.count_nonzero(a > 1)):
            T().gain_limited[device] = T().gain_limited.get(device, 0) + n
        y[over] = np.sign(y[over]) * (KNEE + (1 - KNEE) * np.tanh((a[over] - KNEE) / (1 - KNEE)))
    return np.clip(np.round(y * 32768), -32767, 32767).astype(np.int16)  # far past full scale tanh reaches 1: full scale


def report(device: str, at_ms: int, value) -> bool:
    """A capture gain a client says the device has from at_ms on (a recorder's mic_gain_db, a phone's capture_gain_db):
    a 'reported' row when it differs from the one known at at_ms (or none is). Not a finite number of dB, or past
    REPORTED_MAX_DB: ignored. It never changes the audio: only offset_db does."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > REPORTED_MAX_DB:
        return False
    known = capture_db(device, at_ms)
    if known is not None and abs(known - value) < 1e-6:
        return False
    db.conn().execute("INSERT OR IGNORE INTO device_gain VALUES (?, ?, ?, NULL, 'reported', NULL)", (device, at_ms, float(value)))
    return True


def rows(device: str | None = None) -> list[dict]:
    """The rows (of one device), each device's in time order."""
    return [dict(r) for r in db.conn().execute("SELECT * FROM device_gain WHERE ? IS NULL OR device = ? ORDER BY device, since_ms",
                                               (device, device))]


def put(device: str, since_ms: int, offset: float | None, capture: float | None = None, note: str | None = None):
    """A 'manual' row: what someone measured (offset) or wrote down (capture); the same device and since_ms replaced."""
    db.conn().execute("INSERT OR REPLACE INTO device_gain VALUES (?, ?, ?, ?, 'manual', ?)", (device, since_ms, capture, offset, note))


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
    if not ts:
        sys.exit(f"no tenant {a.tenant} here")
    if a.cmd == "set":
        if len(ts) > 1:
            sys.exit("set: which tenant (--tenant)")
        if not math.isfinite(a.offset_db) or abs(a.offset_db) > REPORTED_MAX_DB:
            sys.exit(f"offset_db: a finite number of dB within +-{REPORTED_MAX_DB:g}")
    now = int(dt.datetime.now().timestamp() * 1000)
    for t in ts:
        tenant.enter(t)
        if db.conn().execute("SELECT 1 FROM sqlite_master WHERE name = 'device_gain'").fetchone() is None:
            sys.exit(f"{t.id}: no device_gain table; start the server on this version first")
        if a.cmd == "set":
            put(a.device, _ms(a.since), a.offset_db, a.capture, a.note)
        for r in rows(a.device):
            print(t.id, r["device"], dt.datetime.fromtimestamp(r["since_ms"] / 1000).isoformat(timespec="seconds"),
                  f"offset={r['offset_db']}", f"capture={r['capture_db']}", r["source"], r["note"] or "", sep="\t")
        for d in sorted({r["device"] for r in rows(a.device)}):
            print(t.id, d, "now", f"offset={offset_db(d, now) or 0:g}", f"capture={capture_db(d, now)}", sep="\t")


if __name__ == "__main__":
    main()
