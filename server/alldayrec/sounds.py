"""Scene scores of all of a device's audio, talk or not, kept as the model gave them (the sounds table), before trim.py
can drop the stretches no one spoke in (docs: reference/data, sounds; explanation/privacy, 环境声音). The models:
sound.py.

A tenant takes part with "sound": true in tenants.json and SOUND_MODEL set (on()). SOUND_SINCE (ISO, the instance's
wall clock; required with SOUND_MODEL) is where its loop starts: the loop never goes over the audio before it, nor
does a new model file (another model id), by itself; history is `python -m alldayrec.sounds backfill`.

Each tenant's loop() wakes every SOUND_EVERY_S: a device's pieces SOUND_SETTLE_S after they came, as stretches (pieces
at most TOL_MS apart), less what each model already covered (its rows), are its holes. A hole is read in parts of at
most CHUNK_MS (late.read_exact; a stretch still growing only in whole parts), the model runs over what of it decoded
(filled), and one row per stretch of that goes in: per frame its level and top K classes, per wall-clock minute the
mean of every class and the frames at HIT or more. A piece that will not decode, or a part that fails (the store, the
service), writes nothing: it stays a hole, tried again after a backoff (lr_scene_failed_total). Holes are looked for
in the last RECENT_S each pass, in all since SOUND_SINCE every FULL_S (late pieces behind). trim.py's gate: unscened.
"""
import argparse
import datetime as dt
import json
import logging
import math
import os
import sys
import time
import zlib

import anyio
import numpy as np

from alldayrec import db, late, sound, tenant, threads
from alldayrec.late_store import subtract, union
from alldayrec.sound import MODELS, SR, model_id, names, windows
from alldayrec.tenant import T

log = logging.getLogger("live")
K, HIT = 10, 0.2  # each frame's top K classes; a minute counts the frames at HIT or more per class
TOL_MS = 500  # pieces this close are one stretch (the zeros between them read as audio), what is left of a hole is none
CHUNK_MS = 300_000
SOUND_EVERY_S = float(os.environ.get("SOUND_EVERY_S", "60"))
SOUND_SETTLE_S = float(os.environ.get("SOUND_SETTLE_S", "120"))
RECENT_S, FULL_S = 6 * 3600, 3600
RETRY_S = (60, 3600)  # a failed part's wait, doubling up to the last


def since_ms() -> int:
    return int(dt.datetime.fromisoformat(os.environ["SOUND_SINCE"]).timestamp() * 1000)


def check():
    """SystemExit on a SOUND_MODEL or SOUND_SINCE that cannot work (at the server's start)."""
    sound.check()
    if names():
        try:
            since_ms()
        except (KeyError, ValueError):
            raise SystemExit("SOUND_MODEL needs SOUND_SINCE, where the scene loop starts (ISO, e.g. 2026-11-17): "
                             "it never goes back before it by itself (history: python -m alldayrec.sounds backfill)") from None


def on() -> bool:
    """The current tenant's audio gets scene scores (and trim.py's gate holds for it)."""
    return bool(names()) and T().sound


TOP = np.dtype([("c", "<u2"), ("s", "<f2")])


def minute_type(c: int) -> np.dtype:
    return np.dtype([("minute", "<i4"), ("frames", "<u2"), ("mean", "<f2", (c,)), ("hits", "u1", (c,))])


def row(name: str, start_ms: int, x: np.ndarray, v: np.ndarray) -> tuple:
    """A sounds row of the model's scores v over x (from start_ms): levels, top K, minutes by each frame's middle."""
    w, c = windows(name, len(x)), MODELS[name]["classes"]
    end_ms = start_ms + round(len(x) * 1000 / SR)
    lv = [10 * np.log10(np.mean(np.square(x[a:a + n], dtype=np.float64)) / 32768 ** 2 + 1e-13) if n > 0 and a < len(x) else -127
          for a, n in w]
    top = np.empty((len(w), K), TOP)
    top["c"] = np.argsort(-v, axis=1, kind="stable")[:, :K]
    top["s"] = np.take_along_axis(v, top["c"].astype(np.int64), 1)
    at = [min(start_ms + round((a + min(n, len(x) - a) / 2) * 1000 / SR), end_ms - 1) // 60000 for a, n in w]
    ms = sorted(set(at))
    out = np.zeros(len(ms), minute_type(c))
    for k, m in enumerate(ms):
        sel = v[[i for i, x_ in enumerate(at) if x_ == m]]
        out[k] = (m, len(sel), sel.mean(0), (sel >= HIT).sum(0))
    return (start_ms, end_ms, MODELS[name]["hop"] * 1000 // SR, MODELS[name]["window"] * 1000 // SR, len(w),
            np.clip(np.round(lv), -127, 0).astype(np.int8).tobytes(), zlib.compress(top.tobytes()), zlib.compress(out.tobytes()))


def write(c, device: str, name: str, r: tuple):
    c.execute("INSERT OR REPLACE INTO sounds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
              (device, model_id(name), *r, int(time.time() * 1000)))


def minutes(device: str, name: str, a: int, b: int) -> dict[int, tuple]:
    """Unix minute -> (frames, mean of each class, frames at HIT or more) over [a, b): the rows' minutes, a minute two
    rows share by their frames."""
    c, out = MODELS[name]["classes"], {}
    for (blob,) in db.conn().execute("SELECT minutes FROM sounds WHERE device = ? AND model = ? AND end_ms > ? AND start_ms < ?",
                                     (device, model_id(name), a, b)):
        for m in np.frombuffer(zlib.decompress(blob), minute_type(c)):
            if a // 60000 <= m["minute"] <= (b - 1) // 60000:
                n0, s0, h0 = out.get(int(m["minute"]), (0, 0.0, 0))
                n = n0 + int(m["frames"])
                out[int(m["minute"])] = (n, (s0 * n0 + m["mean"].astype(np.float64) * int(m["frames"])) / n, h0 + m["hits"].astype(np.int64))
    return out


def covered(device: str, name: str, a: int, b: int) -> list:
    return [[s, e] for s, e in db.conn().execute("SELECT start_ms, end_ms FROM sounds WHERE device = ? AND model = ? AND end_ms > ? AND start_ms < ?",
                                                  (device, model_id(name), a, b))]


def stretches(ranges) -> list:
    """Ranges at most TOL_MS apart, as one."""
    return [[a, b - TOL_MS] for a, b in union([[a, b + TOL_MS] for a, b in ranges])]


def left(ranges, device: str, name: str) -> list:
    """What of ranges the model has not covered, but bits of TOL_MS or less."""
    lo, hi = min((a for a, _ in ranges), default=0), max((b for _, b in ranges), default=0)
    return [h for h in subtract(ranges, covered(device, name, lo, hi)) if h[1] - h[0] > TOL_MS]


def unscened(device: str, rows) -> float:
    """Seconds of these pieces rows (trim.py's to drop) that the gate's model (the first of SOUND_MODEL) has no rows for."""
    return sum(b - a for a, b in left([[r["start_ms"], r["start_ms"] + r["seconds"] * 1000] for r in rows], device, names()[0])) / 1000


def todo(models: list, lo: int, hi: int, now_ms: int, every=True) -> list[tuple]:
    """(device, a, b): the parts to read now, oldest first: every device's settled pieces in [lo, hi) as stretches,
    less what each model has covered or waits out after a failure (the models' holes together: one read for all); a
    stretch still growing (a fresh piece at its end) only in whole CHUNK_MS. every=False: only the devices with a piece
    starting after lo (a pass over the recent hours)."""
    c, out = db.conn(), []
    devices = ({d for (d,) in c.execute("SELECT DISTINCT device FROM pieces")} if every else  # (device, ...) index: 0.2 s for 2M pieces
               {d for (d,) in c.execute("SELECT device FROM pieces WHERE start_ms > ?", (lo,))})  # the primary key's range
    for device in sorted(devices):
        got = c.execute("SELECT start_ms, seconds, received_ms FROM pieces WHERE device = ? AND start_ms + seconds * 1000 > ? AND start_ms < ?",
                        (device, lo, hi)).fetchall()
        ok = lambda s, sec, rec: (rec or s + sec * 1000) <= now_ms - SOUND_SETTLE_S * 1000
        fresh = [s for s, sec, rec in got if not ok(s, sec, rec)]
        have = stretches([[max(s, lo), min(s + sec * 1000, hi)] for s, sec, rec in got if ok(s, sec, rec)])
        for a, b in union([h for name in models for h in need(have, device, name, now_ms)]):
            a, b = round(a), round(b)
            growing = any(b - TOL_MS <= s <= b + TOL_MS for s in fresh)
            while b - a >= CHUNK_MS or (b > a and not growing):
                out.append((device, a, min(b, a + CHUNK_MS)))
                a += CHUNK_MS
    return sorted(out, key=lambda t: t[1])


def need(ranges, device: str, name: str, now_ms: int) -> list:
    """What of ranges the model has yet to do: not covered, not waiting out a failure."""
    return left(subtract(ranges, [[a, b] for a, b, until, _ in T().scene_retry.get((device, name), []) if until > now_ms]), device, name)


def failed(device: str, name: str, ranges: list, now_ms: int):
    retry = T().scene_retry.setdefault((device, name), [])
    for a, b in ranges:
        tries = max((t for x, y, _, t in retry if x < b and a < y), default=0) + 1
        retry[:] = [r for r in retry if not (r[0] < b and a < r[1])] + [[a, b, now_ms + min(RETRY_S[0] * 2 ** (tries - 1), RETRY_S[1]) * 1000, tries]]
        T().scene_stats["failed", name] += 1


def one(device: str, models: list, a: int, b: int) -> float:
    """[a, b) of device's audio, read once, through each model that has yet to do some of it, what decoded of it as
    one row per stretch; seconds of audio scored (all models)."""
    now_ms, bad, filled = int(time.time() * 1000), [], []
    todo_ = {name: n for name in models if (n := need([[a, b]], device, name, now_ms))}
    if not todo_:
        return 0.0
    t = time.perf_counter()
    try:
        x = late.read_exact(device, a, b, bad, filled)
    except Exception as e:  # the store unreachable, a decode that raised: all of it again later
        log.warning("scene of %s at %s: %r", device, a, e)
        for name, n in todo_.items():
            failed(device, name, n, now_ms)
        return 0.0
    T().scene_stats["decode", ""] += time.perf_counter() - t
    done, got = 0.0, stretches([[max(p, a), min(q, b)] for p, q in filled])
    for name, n in todo_.items():
        rows = []
        for s, e in subtract(got, subtract([[a, b]], n)):
            y = x[round((s - a) * SR / 1000):round((e - a) * SR / 1000)]
            if not len(y):
                continue
            t = time.perf_counter()
            try:
                v = sound.scene(name, y)
            except Exception as e_:  # a 413, a model that failed: this stretch again later
                log.warning("scene %s of %s at %s: %r", name, device, s, e_)
                failed(device, name, [[s, e]], now_ms)
                continue
            T().scene_stats["compute", name] += time.perf_counter() - t
            rows.append(row(name, s, y, v))
        with db.tx() as c:
            for r in rows:
                write(c, device, name, r)
        if bad:
            failed(device, name, bad, now_ms)
        scored = sum(r[1] - r[0] for r in rows) / 1000
        T().scene_stats["audio", name] += scored
        done += scored
    return done


def expire(now_ms: int):
    """Frames past their model's keep_days lose their top K (the levels and minutes stay)."""
    for name, m in MODELS.items():
        if m["keep_days"] is not None:
            with db.tx() as c:
                c.execute("UPDATE sounds SET top = NULL WHERE model LIKE ? AND end_ms < ? AND top IS NOT NULL",
                          (name + "@%", now_ms - m["keep_days"] * 86400_000))


async def loop():
    """The tenant's scene scores caught up, for good (server.run_tenant, when on())."""
    full_at = -math.inf
    while True:
        now_ms, full = int(time.time() * 1000), time.monotonic() - full_at >= FULL_S
        lo = since_ms() if full else max(since_ms(), now_ms - RECENT_S * 1000)
        if full:
            full_at = time.monotonic()
            await anyio.to_thread.run_sync(expire, now_ms, limiter=threads.DB)
        parts = await anyio.to_thread.run_sync(todo, names(), lo, now_ms, now_ms, full, limiter=threads.DB)
        if full:
            T().scene_stats["backlog", ""] = sum(b - a for _, a, b in parts) / 1000
        for device, a, b in parts:
            await anyio.to_thread.run_sync(one, device, names(), a, b, limiter=threads.SOUND)
        await anyio.sleep(SOUND_EVERY_S)


def backfill(models: list, lo: int, hi: int) -> float:
    """Every hole of the models in [lo, hi) (SOUND_SINCE does not count here), in this process; seconds done."""
    done = 0.0
    for device, a, b in todo(models, lo, hi, int(time.time() * 1000)):
        done += one(device, models, a, b)
        print(json.dumps({"device": device, "at": dt.datetime.fromtimestamp(a / 1000).isoformat(), "done_s": round(done)}),
              file=sys.stderr, flush=True)
    return done


def main():
    """python -m alldayrec.sounds backfill (--tenant ID | --db live.db --recordings DIR) --since ISO [--until ISO] [--model NAME]"""
    a = argparse.ArgumentParser(description="Scene scores of the audio before SOUND_SINCE (or any span): heavy, one core; "
                                "run it where the instance's compute may go (docs: reference/configuration, environmental sound)")
    a.add_argument("cmd", choices=("backfill",))
    a.add_argument("--tenant")
    a.add_argument("--db")
    a.add_argument("--recordings")
    a.add_argument("--since", required=True, help="ISO, the instance's wall clock")
    a.add_argument("--until", help="ISO (default: now)")
    a.add_argument("--model", action="append", help="one of MODELS (again for more; default: SOUND_MODEL's)")
    o = a.parse_args()
    if bool(o.tenant) == bool(o.db):
        a.error("--tenant or --db (with --recordings)")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stderr)
    if o.tenant:
        tenant.enter(next(t for t in tenant.load() if t.id == o.tenant))
        db.init()
    else:
        from pathlib import Path
        db.open_db(Path(o.db), Path(o.recordings) if o.recordings else None)
    ms = lambda s: int(dt.datetime.fromisoformat(s).timestamp() * 1000)
    models = o.model or names()
    if not models or any(n not in MODELS for n in models):
        a.error(f"--model or SOUND_MODEL: one or more of {', '.join(MODELS)}")
    print(json.dumps({"models": [model_id(n) for n in models],
                      "done_s": round(backfill(models, ms(o.since), ms(o.until) if o.until else int(time.time() * 1000)))}))


if __name__ == "__main__":
    main()
