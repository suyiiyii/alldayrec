"""Durable queue for audio arriving behind a device's final reader. No model work here."""
import datetime as dt
import json
import os
import time

from alldayrec import db
from alldayrec.conversations import GAP_S
from alldayrec.tenant import T

LATE_SETTLE_S = float(os.environ.get("LATE_SETTLE_S", "180"))
JOIN_MS = 2000


def union(ranges):
    out = []
    for a, b in sorted(ranges):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def subtract(ranges, covered):
    """Half-open millisecond intervals, retaining holes rather than their bounding box."""
    out = []
    covers = union(covered)
    for a, b in union(ranges):
        for lo, hi in covers:
            if hi <= a:
                continue
            if lo >= b:
                break
            if lo > a:
                out.append([a, lo])
            a = max(a, hi)
            if a >= b:
                break
        if a < b:
            out.append([a, b])
    return out


def is_late(device: str, start_ms: int) -> bool:
    # The in-flight piece counts too: uploads can arrive while its samples are being fed.
    cursor = max(T().read_to.get(device, -1), db.kv(f"read_to:{device}") or -1)
    final = db.kv(f"finalized_to:{device}")
    return start_ms <= cursor or bool(final and start_ms < dt.datetime.fromisoformat(final).timestamp() * 1000)


def record(t, device: str, start_ms: int, end_ms: int, now_ms: int):
    """Called only by audio.save, before replacing its pieces row, inside that write transaction."""
    if not is_late(device, start_ms):
        return
    # All these pieces predate this save (including a shorter copy being replaced). A timestamp tie is
    # still earlier: the append lock serializes uploads. NULL receipt times predate the ledger too.
    covered = [(r[0], round(r[0] + r[1] * 1000)) for r in t.execute(
        "SELECT start_ms, seconds FROM pieces WHERE device = ? AND start_ms < ? AND start_ms + seconds * 1000 > ?",
        (device, end_ms, start_ms))]
    for a, b in subtract([[start_ms, end_ms]], covered):
        enqueue(t, device, a, b, now_ms)


def enqueue(t, device: str, a: int, b: int, now_ms: int, ready: bool = False):
    """Join waiting spans within 2 s. Processing/done intervals never grow beneath their processor."""
    hits = t.execute("SELECT * FROM late_spans WHERE device = ? AND status IN ('waiting', 'deferred') "
                     "AND start_ms <= ? AND end_ms >= ?", (device, b + JOIN_MS, a - JOIN_MS)).fetchall()
    ranges = union([[a, b]] + [x for r in hits for x in json.loads(r["ranges"])])
    first = min([now_ms] + [r["first_ms"] for r in hits])
    # An old release does not settle audio uploaded after it. A release's wall clock is persisted.
    released = db.kv(f"late_released:{device}") or 0
    ready = ready or now_ms <= released
    if hits:
        t.executemany("DELETE FROM late_spans WHERE id = ?", [(r["id"],) for r in hits])
    t.execute("INSERT INTO late_spans (device,start_ms,end_ms,first_ms,last_ms,ranges,ready) VALUES (?,?,?,?,?,?,?)",
              (device, ranges[0][0], ranges[-1][1], first, now_ms, json.dumps(ranges), int(ready)))


def release(device: str):
    now = round(time.time() * 1000)
    with db.tx() as t:
        db.put_kv(f"late_released:{device}", now, t)
        t.execute("UPDATE late_spans SET ready = 1 WHERE device = ? AND status IN ('waiting','deferred')", (device,))


def blocked(span) -> bool:
    o = db.kv(f"open_conversation:{span['device']}")
    return bool(o and dt.datetime.fromisoformat(o["start"]).timestamp() * 1000 <= span["end_ms"] + GAP_S * 1000
                and dt.datetime.fromisoformat(o["end"]).timestamp() * 1000 >= span["start_ms"] - GAP_S * 1000)


def recover():
    """A process restart interrupted any processing claim; keep its attempt count and retry budget."""
    db.conn().execute("UPDATE late_spans SET status = CASE WHEN tries >= 3 THEN 'failed' ELSE 'waiting' END, "
                      "note = CASE WHEN tries >= 3 THEN 'interrupted; attempt limit reached' ELSE 'interrupted; retry after restart' END "
                      "WHERE status = 'processing'")


def claim(now_ms: int | None = None):
    now_ms = round(time.time() * 1000) if now_ms is None else now_ms
    with db.tx() as t:
        rows = t.execute("SELECT * FROM late_spans WHERE status IN ('waiting','deferred') AND next_ms <= ? "
                         "AND (ready = 1 OR last_ms <= ?) ORDER BY start_ms, id",
                         (now_ms, now_ms - LATE_SETTLE_S * 1000)).fetchall()
        for r in rows:
            final = db.kv(f"finalized_to:{r['device']}")
            # The live reader can be ahead of its committing final pass. Wait until that writer
            # has passed the whole span, otherwise replace_sentences could erase the inserted late rows.
            final_ms = dt.datetime.fromisoformat(final).timestamp() * 1000 if final else -1
            read_end = db.kv(f"read_end:{r['device']}")
            if final_ms < r["end_ms"] and (read_end is None or final_ms < read_end):
                continue
            if blocked(r):
                t.execute("UPDATE late_spans SET status = 'deferred' WHERE id = ?", (r["id"],))
                continue
            t.execute("UPDATE late_spans SET status = 'processing', tries = tries + 1, note = NULL WHERE id = ?", (r["id"],))
            return dict(t.execute("SELECT * FROM late_spans WHERE id = ?", (r["id"],)).fetchone())


def failed(span, error: Exception, now_ms: int | None = None):
    now_ms = round(time.time() * 1000) if now_ms is None else now_ms
    # Provider exceptions may contain URLs or keys. Persist only the exception class.
    db.conn().execute("UPDATE late_spans SET status = ?, note = ?, next_ms = ? WHERE id = ?",
                      ("failed" if span["tries"] >= 3 else "waiting", type(error).__name__,
                       now_ms + 30_000 * 2 ** (span["tries"] - 1), span["id"]))


def status(device: str, now_ms: int | None = None) -> dict:
    now = dt.datetime.fromtimestamp((now_ms or time.time() * 1000) / 1000)
    midnight = round(dt.datetime.combine(now.date(), dt.time()).timestamp() * 1000)
    out = {s: {"spans": 0, "minutes": 0.0} for s in ("waiting", "deferred", "processing", "failed", "done")}
    for r in db.conn().execute("SELECT status, ranges, note FROM late_spans WHERE device = ? AND (status != 'done' OR done_ms >= ?)", (device, midnight)):
        x = out[r["status"]]
        x["spans"] += 1
        x["minutes"] += sum(b - a for a, b in json.loads(r["ranges"])) / 60_000
        if r["note"]:
            x["note"] = r["note"]
    for x in out.values():
        x["minutes"] = round(x["minutes"], 2)
    return out
