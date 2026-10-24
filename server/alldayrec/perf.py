"""What the server spends, kept in the tenant's store (db.SCHEMA's perf_* tables) for KEEP_DAYS: the event loop's
stalls (monitor.loop_stalls) and the process each minute (monitor.metrics_loop), both in the first tenant's store; every
HTTP request (Requests); each final piece and draft pass of a device's pipeline (device_pipeline). How long things take,
never what was said. GET /api/perf?day= reads a day of it, and from a copy of a store:

    python -m alldayrec.perf export <live.db> <YYYY-MM-DD> > perf.json
    python -m alldayrec.perf import-stalls <live.db> <server.log>    the stall lines a log has (before the table), once
"""
import contextlib
import datetime as dt
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path

from alldayrec import core
from alldayrec import db
from alldayrec import tenant
from alldayrec import threads
from alldayrec.tenant import T

KEEP_DAYS = 90
SLOW_MS = 50  # a request this slow is a row of its own; every one is in its minute's row
TABLES = ("perf_stalls", "perf_samples", "perf_requests", "perf_pieces")
POOLS = {"infer": threads.INFER, "asr": threads.ASR, "media": threads.MEDIA, "db": threads.DB}


def now() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def first(f, *args):
    """f(*args) in the first tenant's store, where the process's own rows go (none: a test without tenants)."""
    if core.TENANTS:
        tenant.call(core.TENANTS[0], f, *args)


def add(acc: dict | None, **kv):
    if acc is not None:
        for k, v in kv.items():
            acc[k] = acc.get(k, 0.0) + v


@contextlib.contextmanager
def timed(acc: dict | None, key: str):
    """The block's ms added to acc[key] (none: not counted)."""
    t = time.perf_counter()
    try:
        yield
    finally:
        add(acc, **{key: (time.perf_counter() - t) * 1000})


# a device's pieces

def piece(device: str, layer: str, piece_s: float | None, sentences: int | None, acc: dict):
    """A perf_pieces row of what acc holds (timed's keys: decode_ms, vad_ms, fed_s, asr_ms, embed_ms, commit_ms),
    and acc emptied for the layer's next piece."""
    r = lambda k: round(acc[k], 1) if k in acc else None
    db.conn().execute("INSERT INTO perf_pieces VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                      (now(), device, layer, piece_s and round(piece_s, 2), sentences, r("decode_ms"), r("vad_ms"), r("fed_s"),
                       r("asr_ms"), r("embed_ms"), r("commit_ms")))
    acc.clear()


# the event loop

STALL = "INSERT INTO perf_stalls VALUES (?, ?, ?, ?)"
_minute = {"lag": [], "busy": dict.fromkeys(POOLS, 0), "waiting": dict.fromkeys(POOLS, 0)}


def stall(at: str, ms: int, frames: str, suppressed: int):
    first(lambda: db.conn().execute(STALL, (at, ms, frames, suppressed)))


def beat(lag_s: float):
    """One beat of loop_stalls' task, lag_s late: the minute's lag, and how busy each thread pool is right now."""
    _minute["lag"].append(lag_s * 1000)
    for k, lim in POOLS.items():
        s = lim.statistics()
        _minute["busy"][k] += s.borrowed_tokens
        _minute["waiting"][k] = max(_minute["waiting"][k], s.tasks_waiting)


# the process each minute

_last = {"cpu": sum(os.times()[:4]), "at": time.monotonic(), "pruned": ""}


def memory() -> tuple[float | None, float | None]:
    """RSS and its high-water mark in MB: both from /proc on Linux; on macOS the mark only (getrusage)."""
    if sys.platform == "linux":
        f = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines() if ":" in line)
        return int(f["VmRSS"].split()[0]) / 1024, int(f["VmHWM"].split()[0]) / 1024
    if sys.platform == "darwin":
        import resource
        return None, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20  # bytes there
    return None, None


def sample(queued: int, clients: int) -> dict:
    """The process since the last call, a perf_samples row in the first tenant's store; every tenant's requests of the
    minute (flush); once a day, rows older than KEEP_DAYS dropped. Returned for the metrics push."""
    cpu, at = sum(os.times()[:4]), time.monotonic()  # user, system, children's user and system
    lag, n = sorted(_minute["lag"]), len(_minute["lag"])
    rss, hwm = memory()
    s = {"at": now(), "s": round(at - _last["at"], 1), "cpu_s": round(cpu - _last["cpu"], 2),
         "rss_mb": rss and round(rss, 1), "hwm_mb": hwm and round(hwm, 1),
         "lag_p50_ms": round(lag[n // 2], 1) if n else None, "lag_p99_ms": round(lag[int(n * 0.99)], 1) if n else None,
         "lag_max_ms": round(lag[-1], 1) if n else None,
         # beats: none when LOOP_STALL_MS is 0 (no beat)
         "threads": {k: {"busy": round(_minute["busy"][k] / n, 2), "waiting": _minute["waiting"][k]} for k in POOLS} if n else None,
         "queued": queued, "clients": clients}
    _last.update(cpu=cpu, at=at)
    _minute.update(lag=[], busy=dict.fromkeys(POOLS, 0), waiting=dict.fromkeys(POOLS, 0))
    first(lambda: db.conn().execute("INSERT INTO perf_samples VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                    (*[s[k] for k in ("at", "s", "cpu_s", "rss_mb", "hwm_mb", "lag_p50_ms", "lag_p99_ms", "lag_max_ms")],
                                     s["threads"] and json.dumps(s["threads"], separators=(",", ":")), queued, clients)))
    today = dt.date.today().isoformat()
    for t in core.TENANTS:
        tenant.call(t, flush)
        if _last["pruned"] != today:
            tenant.call(t, prune)
    _last["pruned"] = today
    return s


def prune():
    lo = (dt.date.today() - dt.timedelta(days=KEEP_DAYS)).isoformat()
    for table in TABLES:
        db.conn().execute(f"DELETE FROM {table} WHERE at < ?", (lo,))


# HTTP requests

class Requests:
    """Times every HTTP request but /api/health (no tenant) to its last byte: one of SLOW_MS or more is a perf_requests
    row at once; all go into their tenant's minute (T().requests), a row per method, route and status (flush). Inside
    auth.Tenants: the tenant is known."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] == "/api/health":
            return await self.app(scope, receive, send)
        t0, out = time.perf_counter(), {"status": 500, "bytes": 0}  # no answer: the error the server sends for it

        async def sent(m):
            if m["type"] == "http.response.start":
                out["status"] = m["status"]
            elif m["type"] == "http.response.body":
                out["bytes"] += len(m.get("body", b""))
            await send(m)
        try:
            await self.app(scope, receive, sent)
        finally:
            ms = (time.perf_counter() - t0) * 1000
            route = scope.get("route")  # the template (/api/conversation/{conv_id}), set once routed: never a device or a name
            key = (scope["method"], getattr(route, "path", "?"), out["status"])
            T().requests.setdefault(key, []).append((ms, out["bytes"]))
            if ms >= SLOW_MS:
                db.conn().execute("INSERT INTO perf_requests VALUES (?, ?, ?, ?, NULL, ?, NULL, NULL, ?)", (now(), *key, round(ms, 1), out["bytes"]))


def flush():
    """The current tenant's requests since the last flush, a perf_requests row per method, route and status."""
    reqs, T().requests = T().requests, {}
    at, rows = now(), []
    for key, xs in reqs.items():
        ms = sorted(x[0] for x in xs)
        rows.append((at, *key, len(ms), round(ms[-1], 1), round(ms[len(ms) // 2], 1), round(ms[int(len(ms) * 0.95)], 1), sum(x[1] for x in xs)))
    db.conn().executemany("INSERT INTO perf_requests VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)


# reading it

def day(c: sqlite3.Connection, d: str) -> dict:
    """A day's (YYYY-MM-DD) rows of each table in a store, oldest first: {stalls, samples, requests, pieces}."""
    hi = (dt.date.fromisoformat(d) + dt.timedelta(days=1)).isoformat()
    out = {t.removeprefix("perf_"): [dict(r) for r in c.execute(f"SELECT * FROM {t} WHERE at >= ? AND at < ? ORDER BY at", (d, hi))]
           for t in TABLES}
    for r in out["samples"]:
        r["threads"] = r["threads"] and json.loads(r["threads"])
    return out


LINE = re.compile(r"(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d),(\d{3}) .*?event loop held up (\d+) ms(?: \(\+(\d+) more since the last line\))?: (.*)")


def import_stalls(lines) -> int:
    """monitor.watch's log lines (a server.log, journalctl's output) as perf_stalls rows of the current store; one
    already there is skipped: imported before, or written by the server beside its line (the same ms and frames within
    a second). Returns the rows added."""
    rows = [(f"{m[1]}T{m[2]}.{m[3]}", int(m[4]), m[6].rstrip(), int(m[5] or 0)) for line in lines if (m := LINE.search(line))]
    near = lambda at, s: (dt.datetime.fromisoformat(at) + dt.timedelta(seconds=s)).isoformat(timespec="milliseconds")
    with db.tx() as c:
        before = c.total_changes
        c.executemany(STALL.replace("VALUES (?, ?, ?, ?)", "SELECT ?, ?, ?, ? WHERE NOT EXISTS (SELECT 1 FROM perf_stalls "
                                    "WHERE ms = ? AND frames = ? AND at BETWEEN ? AND ?)"),
                      [(*r, r[1], r[2], near(r[0], -1), near(r[0], 1)) for r in rows])
        return c.total_changes - before


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["export", store, d]:
            print(json.dumps(day(db.read_only(store), d), ensure_ascii=False))
        case ["import-stalls", store, log]:
            db.open_db(Path(store))
            with open(log, errors="replace") as f:
                print(f"{import_stalls(f)} rows added to perf_stalls")
        case _:
            raise SystemExit(__doc__)
