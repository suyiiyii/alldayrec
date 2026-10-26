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
MIN_LATE_MS = 500  # timestamp jitter and sub-sentence slivers are not a new ASR job
MAX_LATE_AUDIO_MS = 30 * 60 * 1000  # each part commits independently of later timeouts


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
    # Persisted read-ahead belongs to the previous reader until this process starts a new one.
    cursor = max(T().read_to[device], db.kv(f"read_to:{device}") or -1) if device in T().read_to else -1
    final = db.kv(f"finalized_to:{device}")
    return start_ms <= cursor or bool(final and start_ms < dt.datetime.fromisoformat(final).timestamp() * 1000)


def record(t, device: str, start_ms: int, end_ms: int, now_ms: int):
    """Called by both audio save paths before replacing pieces, inside that write transaction."""
    if not is_late(device, start_ms):
        return False
    # All these pieces predate this save (including a shorter copy being replaced). A timestamp tie is
    # still earlier: the append lock serializes uploads. NULL receipt times predate the ledger too.
    covered = [(r[0], round(r[0] + r[1] * 1000)) for r in t.execute(
        "SELECT start_ms, seconds FROM pieces WHERE device = ? AND start_ms < ? AND start_ms + seconds * 1000 > ?",
        (device, end_ms, start_ms))]
    ranges = substantial(subtract([[start_ms, end_ms]], covered))
    for a, b in ranges:
        enqueue(t, device, a, b, now_ms)
    return bool(ranges)


def substantial(ranges):
    return [[a, b] for a, b in ranges if b - a >= MIN_LATE_MS]


def parts(ranges):
    """Bound total audio per job, preserving half-open coverage and holes at every seam."""
    out, part, used = [], [], 0
    for a, b in union(ranges):
        while a < b:
            room = MAX_LATE_AUDIO_MS - used
            if room < MIN_LATE_MS:
                out.append(part)
                part, used = [], 0
                room = MAX_LATE_AUDIO_MS
            end = min(b, a + room)
            # Do not create a sub-jitter tail that substantial() would later discard.
            if 0 < b - end < MIN_LATE_MS:
                end = b - MIN_LATE_MS
                if end - a < MIN_LATE_MS:
                    out.append(part)
                    part, used = [], 0
                    continue
            part.append([a, end])
            used += end - a
            a = end
    if part:
        out.append(part)
    return out


def reset_reader(device: str, after_ms: int):
    """A restarted live reader owns audio after its final checkpoint. Invalidate in-flight late snapshots."""
    with db.tx() as t:
        db.put_kv(f"read_to:{device}", after_ms - 1, t)
        db.put_kv(f"read_end:{device}", after_ms, t)
        T().read_to[device] = after_ms - 1
        for r in t.execute("SELECT * FROM late_spans WHERE device=? AND status != 'done'", (device,)).fetchall():
            ranges = substantial([[a, min(b, after_ms)] for a, b in json.loads(r["ranges"]) if a < after_ms])
            if ranges == json.loads(r["ranges"]):
                continue
            if not ranges:
                t.execute("DELETE FROM late_spans WHERE id=?", (r["id"],))
            else:
                t.execute("UPDATE late_spans SET start_ms=?,end_ms=?,ranges=?,status=?,tries=? WHERE id=?",
                          (ranges[0][0], ranges[-1][1], json.dumps(ranges),
                           "waiting" if r["status"] == "processing" else r["status"],
                           max(0, r["tries"] - (r["status"] == "processing")), r["id"]))


def enqueue(t, device: str, a: int, b: int, now_ms: int, ready: bool = False):
    return enqueue_ranges(t, device, [[a, b]], now_ms, ready)


def enqueue_ranges(t, device: str, ranges: list, now_ms: int, ready: bool = False, join: bool = True):
    """Join waiting spans within 2 s, then cap their audio. Processing/done spans never grow."""
    ranges = substantial(union(ranges))
    if not ranges:
        return None
    a, b = ranges[0][0], ranges[-1][1]
    hits = t.execute("SELECT * FROM late_spans WHERE device = ? AND status IN ('waiting', 'deferred') "
                     "AND start_ms <= ? AND end_ms >= ?", (device, b + JOIN_MS, a - JOIN_MS)).fetchall() if join else []
    ranges = union(ranges + [x for r in hits for x in json.loads(r["ranges"])])
    first = min([now_ms] + [r["first_ms"] for r in hits])
    # An old release does not settle audio uploaded after it. A release's wall clock is persisted.
    released = db.kv(f"late_released:{device}") or 0
    ready = ready or now_ms <= released
    if hits:
        t.executemany("DELETE FROM late_spans WHERE id = ?", [(r["id"],) for r in hits])
    ids = [t.execute("INSERT INTO late_spans (device,start_ms,end_ms,first_ms,last_ms,ranges,ready) VALUES (?,?,?,?,?,?,?)",
                     (device, p[0][0], p[-1][1], first, now_ms, json.dumps(p), int(ready))).lastrowid
           for p in parts(ranges)]
    return ids[0]


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


def final_passed(span):
    final = db.kv(f"finalized_to:{span['device']}")
    final_ms = dt.datetime.fromisoformat(final).timestamp() * 1000 if final else -1
    read_end = db.kv(f"read_end:{span['device']}")
    return final_ms >= span["end_ms"] or (read_end is not None and final_ms >= read_end)


def claim_changes(r):
    ranges = json.loads(r["ranges"])
    remaining = substantial(subtract(ranges, db.processed(r["device"], r["start_ms"], r["end_ms"])))
    if remaining != ranges or not remaining or len(parts(ranges)) > 1:
        return True  # cleanup/splitting is useful even before the final reader catches up
    return final_passed(r) and (not blocked(r) or r["status"] != "deferred")


def claim(now_ms: int | None = None):
    now_ms = round(time.time() * 1000) if now_ms is None else now_ms
    query = ("SELECT * FROM late_spans WHERE status IN ('waiting','deferred') AND next_ms <= ? "
             "AND (ready = 1 OR last_ms <= ?) ORDER BY start_ms, id")
    args = (now_ms, now_ms - LATE_SETTLE_S * 1000)
    # Idle polling must not reserve SQLite's writer on the event loop. Recheck inside the transaction.
    if not any(claim_changes(r) for r in db.conn().execute(query, args).fetchall()):
        return None
    with db.tx() as t:
        # Older ledgers may already contain hours in one row. Partition before taking an ASR snapshot,
        # retaining readiness/backoff/attempt counts so a restart never resets a retry budget.
        for r in t.execute(query, args).fetchall():
            ps = parts(json.loads(r["ranges"]))
            if len(ps) <= 1:
                continue
            p = ps[0]
            t.execute("UPDATE late_spans SET start_ms=?,end_ms=?,ranges=? WHERE id=?",
                      (p[0][0], p[-1][1], json.dumps(p), r["id"]))
            for p in ps[1:]:
                t.execute("INSERT INTO late_spans (device,start_ms,end_ms,first_ms,last_ms,ranges,ready,status,tries,next_ms,note) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                          (r["device"], p[0][0], p[-1][1], r["first_ms"], r["last_ms"], json.dumps(p),
                           r["ready"], r["status"], r["tries"], r["next_ms"], r["note"]))
        rows = t.execute(query, args).fetchall()
        for r in rows:
            remaining = substantial(subtract(json.loads(r["ranges"]), db.processed(r["device"], r["start_ms"], r["end_ms"])))
            if not remaining:
                t.execute("UPDATE late_spans SET status='done',done_ms=?,note=NULL WHERE id=?", (now_ms, r["id"]))
                continue
            if remaining != json.loads(r["ranges"]):
                t.execute("UPDATE late_spans SET ranges=?,start_ms=?,end_ms=? WHERE id=?",
                          (json.dumps(remaining), remaining[0][0], remaining[-1][1], r["id"]))
                r = t.execute("SELECT * FROM late_spans WHERE id=?", (r["id"],)).fetchone()
            # The live reader can be ahead of its committing final pass. Wait until that writer
            # has passed the whole span, otherwise replace_sentences could erase the inserted late rows.
            if not final_passed(r):
                continue
            if blocked(r):
                if r["status"] != "deferred":
                    t.execute("UPDATE late_spans SET status = 'deferred' WHERE id = ?", (r["id"],))
                continue
            t.execute("UPDATE late_spans SET status = 'processing', tries = tries + 1, note = NULL WHERE id = ?", (r["id"],))
            return dict(t.execute("SELECT * FROM late_spans WHERE id = ?", (r["id"],)).fetchone())


def move_seam(span, cut):
    """Return the untranscribed suffix to the next job, atomically and without resetting retries."""
    with db.tx() as t:
        current = t.execute("SELECT * FROM late_spans WHERE id=?", (span["id"],)).fetchone()
        if not current or any(current[k] != span[k] for k in ("status", "tries", "ranges")):
            return False  # a reader restart invalidated the lookahead snapshot
        ranges = json.loads(span["ranges"])
        tail = [[cut, ranges[-1][1]]]
        ranges[-1][1] = cut
        following = t.execute("SELECT * FROM late_spans WHERE device=? AND start_ms=? "
                              "AND status IN ('waiting','deferred') ORDER BY id LIMIT 1",
                              (span["device"], span["end_ms"])).fetchone()
        # The current claim has not sent this suffix to ASR. Its prior attempts still count.
        meta = dict(following or span)
        meta["tries"] = max(meta["tries"] if following else 0, span["tries"] - 1)
        if following:
            tail += json.loads(following["ranges"])
            meta.update(first_ms=min(meta["first_ms"], span["first_ms"]),
                        last_ms=max(meta["last_ms"], span["last_ms"]))
            t.execute("DELETE FROM late_spans WHERE id=?", (following["id"],))
        for p in parts(tail):
            t.execute("INSERT INTO late_spans (device,start_ms,end_ms,first_ms,last_ms,ranges,ready,status,tries,next_ms,note) "
                      "VALUES (?,?,?,?,?,?,?,'waiting',?,?,?)",
                      (span["device"], p[0][0], p[-1][1], meta["first_ms"], meta["last_ms"], json.dumps(p),
                       meta["ready"], meta["tries"], meta["next_ms"], meta["note"]))
        encoded = json.dumps(ranges)
        t.execute("UPDATE late_spans SET end_ms=?,ranges=?,seam_ms=? WHERE id=?", (cut, encoded, cut, span["id"]))
        span.update(end_ms=cut, ranges=encoded, seam_ms=cut)
    return True


def failed(span, error: Exception, now_ms: int | None = None):
    now_ms = round(time.time() * 1000) if now_ms is None else now_ms
    # Provider exceptions may contain URLs or keys. Persist only the exception class.
    db.conn().execute("UPDATE late_spans SET status = ?, note = ?, next_ms = ? WHERE id = ? AND status='processing'",
                      ("failed" if span["tries"] >= 3 else "waiting", type(error).__name__,
                       now_ms + 30_000 * 2 ** (span["tries"] - 1), span["id"]))


def ready_work(device: str, now_ms: int | None = None) -> bool:
    now_ms = round(time.time() * 1000) if now_ms is None else now_ms
    return db.conn().execute("SELECT 1 FROM late_spans WHERE device=? AND status IN ('waiting','processing') "
                             "AND (status='processing' OR ready=1 OR last_ms<=?) LIMIT 1",
                             (device, now_ms - LATE_SETTLE_S * 1000)).fetchone() is not None


def queue_summaries():
    """Persistent dirty markers survive a restart; one queued revision after a device's ready batch drains."""
    from alldayrec.core import enqueue_summary
    ids = {db.resolve_conversation(k.split(":", 1)[1]) for k, in db.conn().execute("SELECT key FROM kv WHERE key LIKE 'late_summary:%' AND value='true'")}
    for cid in ids:
        c = db.conversation(cid)
        if c and cid not in T().late_queued and not ready_work(c.get("device")):
            T().late_queued.add(cid)
            enqueue_summary({**c, "_late": True, "_revision": db.kv(f"conversation_revision:{cid}")})


def summary_done(cid: str, t):
    for key, in t.execute("SELECT key FROM kv WHERE key LIKE 'late_summary:%' AND value='true'").fetchall():
        if db.resolve_conversation(key.split(":", 1)[1]) == cid:
            db.put_kv(key, None, t)


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
