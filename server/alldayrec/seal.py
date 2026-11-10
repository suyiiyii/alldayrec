"""STORAGE=s3: an hour file's life, from local disk into an object (store.py; docs: explanation/architecture).

1. Open: the hour being recorded is a local file, appended to and fsynced as with STORAGE=local (audio.save).
2. Sealed: once its hour is over and no piece has come for SEAL_IDLE_S, it is uploaded whole, verified (size and the
   SHA-256 the PUT signed), its rows pointed at the object in one transaction, and only then deleted. An import (a piece
   longer than audio.WHOLE_S) is a file of its own, sealed at once.
3. Late: a piece for an hour already sealed is appended to a local staging file (staging/.../<HH>.<gen>.<ext>) and
   answered at once; merge() later puts the object's bytes and the staged pieces after them into a new version of the
   same key. The bytes before stay as they were, so every row's pos stays valid and a reader of the old version reads
   the same prefix.

Crash-safe: before an upload, its row in store_objects says what the object is to be (intent: size, SHA-256, the rows
to move); after a crash an object that matches it only needs the transaction, one that does not is uploaded again.
Local and staging bytes are deleted only after that transaction and only once no row points at them. One seal or
merge per object at a time (lock()). The object store down: uploads go on as ever (local only), sealing and merging
back off and retry, local disk grows (lr_store_local_bytes, lr_store_pending_seal).
"""
import contextlib
import datetime as dt
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import PurePosixPath

import anyio
import anyio.to_thread

from alldayrec import audio
from alldayrec import db
from alldayrec import store
from alldayrec.tenant import T

log = logging.getLogger("live")
SEAL_IDLE_S = int(os.environ.get("SEAL_IDLE_S", "600"))  # an hour file is sealed this long after its last piece (and its hour's end)
MERGE_IDLE_S = int(os.environ.get("MERGE_IDLE_S", "90"))  # an hour's late pieces are merged once none has come for this long,
MERGE_MAX_BYTES = int(os.environ.get("MERGE_MAX_BYTES", str(32 * 1024 * 1024)))  # or they add up to this,
MERGE_MAX_S = int(os.environ.get("MERGE_MAX_S", "1800"))  # or the first of them came this long ago
EVERY_S = 30  # loop(): how often it looks
RETRY_MAX_S = 600  # a failed seal or merge is tried again 30 s, 60 s, ... up to this later

_locks = threading.Lock()  # T().store_locks


def on() -> bool:
    return store.STORAGE == "s3"


def lock(path: str) -> threading.Lock:
    """The one seal or merge of path at a time, in this process."""
    with _locks:
        return T().store_locks.setdefault(path, threading.Lock())


def row(path: str):
    return db.conn().execute("SELECT * FROM store_objects WHERE path = ?", (path,)).fetchone()


def hour_start(ms: int) -> int:
    return int(dt.datetime.fromtimestamp(ms / 1000).replace(minute=0, second=0, microsecond=0).timestamp() * 1000)


def staging(path: str, gen: int) -> str:
    """The staging file of gen for the hour file path: staging/chunks/<device>/<date>/<HH>.<gen>.<ext>, its extension
    last (readers tell the codec by it)."""
    p = PurePosixPath(path)
    return f"staging/{p.with_suffix('')}.{gen}{p.suffix}"


def unstaged(path: str) -> tuple[str, int | None]:
    """(the hour file, the gen) of a staging file."""
    p = PurePosixPath(path.removeprefix("staging/"))
    stem, _, gen = p.stem.rpartition(".")
    return str(p.with_name(stem + p.suffix)), int(gen) if gen.isdigit() else None


def place(path: str, start_ms: int, size: int, whole: bool) -> str:
    """Where a piece of `path` (its hour file) goes; under audio._append. STORAGE=local: there. s3: an import (whole)
    to a file of its own; a piece of an hour sealed (or being sealed) to its staging file; else its hour file."""
    if not on():
        return path
    if whole:
        p = PurePosixPath(path)
        return str(p.with_name(f"{p.stem}.{start_ms}.{size}{p.suffix}"))  # a longer resend: another file, not a merge
    r = row(path)
    return path if r is None or r["state"] in ("open", "local") else staging(path, r["gen"])


def placed(t, path: str, start_ms: int, size: int, received: int, whole: bool):
    """The bookkeeping of a piece put at path (place), in the save's transaction t."""
    if not on():
        return
    if path.startswith("staging/"):
        t.execute("UPDATE store_objects SET late_first = coalesce(late_first, ?), late_last = ?, late_bytes = late_bytes + ? "
                  "WHERE path = ?", (received, received, size, unstaged(path)[0]))
        return
    h = hour_start(start_ms)
    seal_at = received if whole else max(h + 3600_000, received + SEAL_IDLE_S * 1000)
    # lo..hi: where the start of every row in it is (an hour either side: a clock set back, a DST hour twice)
    t.execute("INSERT INTO store_objects (path, lo_ms, hi_ms, state, seal_at) VALUES (?, ?, ?, 'open', ?) "
              "ON CONFLICT (path) DO UPDATE SET seal_at = max(seal_at, excluded.seal_at) WHERE state = 'open'",
              (path, h - 3600_000, h + 7200_000, seal_at))


INTENTS = 5  # the uploads under way kept: one may land after the next began (a PUT answered too late)


def matches(path: str, it: dict, h=False) -> str | None:
    """The object's ETag when it is what the intent says (its size, and the SHA-256 its PUT was signed with); h: its
    HEAD, already asked. A store that gives no ETag cannot be used (a merge replaces only the version it read)."""
    h = store.s3().head(path) if h is False else h
    if h is None or h[:2] != (it["size"], it["sha256"]):
        return None
    if not h[2]:
        raise store.NoETag(f"{path}: the object store gives no ETag (HEAD): merges could not be kept off others' objects")
    return h[2]


def landed(r) -> tuple[dict, str] | None:
    """The upload under way (r's intents, the newest first) the object turned out to be, and its ETag; None when
    the object is none of them (no upload got there: the object is as before)."""
    intents = json.loads(r["intent"]) if r["intent"] else []
    h = store.s3().head(r["path"]) if intents else None
    for it in reversed(intents):
        if etag := matches(r["path"], it, h):
            return it, etag
    return None


def add_intent(path: str, it: dict):
    """What the upload about to start makes the object, kept beside those still under way (INTENTS of them)."""
    with db.tx(durable=True) as t:
        v = t.execute("SELECT intent FROM store_objects WHERE path = ?", (path,)).fetchone()[0]
        t.execute("UPDATE store_objects SET intent = ? WHERE path = ?", (json.dumps(((json.loads(v) if v else []) + [it])[-INTENTS:]), path))


def release(path: str, sealed: tuple | None = None):
    """Deletes the local file path once no row points at it. sealed: (size, sha256) of the object it was sealed into:
    only a file that is exactly those bytes goes, its rows left on it (one outside lo..hi) pointed at the object
    first; a file made again since (STORAGE=local for a while: late pieces appended to a new hour file from 0) is
    kept, rows and all."""
    if sealed is not None:
        with open(T().recordings / path, "rb") as h:
            size = h.seek(0, os.SEEK_END)
            if (size, store.file_sha(h, size)) != tuple(sealed):
                log.error("store: %s is not the object it was sealed into (written again since): kept on local disk", path)
                return
    if db.conn().execute("SELECT 1 FROM pieces WHERE path = ? LIMIT 1", (path,)).fetchone():  # a scan: once per file
        if sealed is None:
            log.warning("store: %s kept: rows still point at it", path)
            return
        with db.tx(durable=True) as t:
            t.execute("UPDATE pieces SET path = ? WHERE path = ?", ("s3:" + path, path))
    store.local().delete(path)


def seal(path: str):
    """Seals the open hour file `path` once due (placed's seal_at), or finishes one a crash left sealing."""
    with lock(path):
        with audio._append:  # from here no piece goes into it (place: its staging file)
            r = row(path)
            if r is None or r["state"] not in ("open", "sealing") or r["state"] == "open" and r["seal_at"] > time.time() * 1000:
                return
            if r["state"] == "open":
                with db.tx(durable=True) as t:
                    t.execute("UPDATE store_objects SET state = 'sealing' WHERE path = ?", (path,))
        got = landed(r)
        if got:
            it, etag = got
        else:
            f = T().recordings / path
            size = f.stat().st_size
            if size > store.PUT_MAX:
                with db.tx(durable=True) as t:
                    t.execute("UPDATE store_objects SET state = 'local', intent = NULL WHERE path = ?", (path,))
                log.error("store: %s is %d bytes, over one PUT's %d: kept on local disk", path, size, store.PUT_MAX)
                return
            with open(f, "rb") as h:
                it = {"size": size, "sha256": store.file_sha(h, size)}
                add_intent(path, it)
                store.s3().write(path, h, it["sha256"])  # only a new key: never over another instance's object
            if not (etag := matches(path, it)):
                raise store.Unreachable(f"{path}: the object does not match what was uploaded")
        with db.tx(durable=True) as t:
            # only rows the object holds: one appended after the upload (STORAGE=local meanwhile) stays on the file,
            # and the file with it (release: not the object's bytes)
            t.execute("UPDATE pieces SET path = ? WHERE start_ms BETWEEN ? AND ? AND path = ? AND pos + size <= ?",
                      ("s3:" + path, r["lo_ms"], r["hi_ms"], path, it["size"]))
            t.execute("UPDATE store_objects SET state = 'sealed', size = ?, sha256 = ?, etag = ?, intent = NULL, seal_at = NULL WHERE path = ?",
                      (it["size"], it["sha256"], etag, path))
        release(path, (it["size"], it["sha256"]))


def sweep():
    """At start: local files a crash left after their transaction (sealed hour files, merged staging files) deleted."""
    root = T().recordings
    for top in ("chunks", "staging"):
        for f in (root / top).rglob("*") if (root / top).is_dir() else ():
            if not f.is_file():
                continue
            path = f.relative_to(root).as_posix()
            if top == "chunks":
                r = row(path)
                if r is not None and r["state"] == "sealed":
                    release(path, (r["size"], r["sha256"]))
            else:
                hour, gen = unstaged(path)
                r = row(hour)
                if r is not None and gen is not None and gen < r["gen"]:
                    release(path)


def due() -> list[tuple[str, str]]:
    """(what, path) to do now: seals due, then merges: an intent a crash left, or late pieces none has followed for
    MERGE_IDLE_S, adding up to MERGE_MAX_BYTES, or the first of them MERGE_MAX_S old."""
    now, c = int(time.time() * 1000), db.conn()
    return [("seal", r[0]) for r in c.execute(
        "SELECT path FROM store_objects WHERE state = 'sealing' OR state = 'open' AND seal_at <= ? ORDER BY seal_at", (now,))] + [
        ("merge", r[0]) for r in c.execute(
            "SELECT path FROM store_objects WHERE state = 'sealed' AND (intent IS NOT NULL OR late_bytes > 0 AND "
            "(late_last <= ? OR late_bytes >= ? OR late_first <= ?)) ORDER BY late_first",
            (now - MERGE_IDLE_S * 1000, MERGE_MAX_BYTES, now - MERGE_MAX_S * 1000))]


def step():
    """One look: every seal (and merge) due, each failure backed off on its own."""
    retry = T().store_retry
    for what, path in due():
        if retry.get(path, (0, 0))[1] > time.monotonic():
            continue
        try:
            with store.timed(what):
                seal(path) if what == "seal" else merge(path)
            retry.pop(path, None)
        except Exception as e:
            n = retry.get(path, (0, 0))[0] + 1
            retry[path] = (n, time.monotonic() + min(RETRY_MAX_S, 30 * 2 ** (n - 1)))
            # refused or unusable (the key taken, no ETag): no retry mends it, unlike the store down
            log.log(logging.ERROR if store.kind(e) in ("conflict", "no_etag") else logging.WARNING,
                    "store: %s %s failed (%d): %s", what, path, n, e)


def staged(r, below: int | None = None) -> list:
    """The rows of r's hour in its staging files (of a gen below `below`), in the order they were staged."""
    prefix = f"staging/{PurePosixPath(r['path']).with_suffix('')}."
    rows = db.conn().execute("SELECT device, start_ms, path, pos, size, received_ms FROM pieces WHERE "
                             "start_ms BETWEEN ? AND ? AND substr(path, 1, ?) = ?",  # any device's (two names, one file)
                             (r["lo_ms"], r["hi_ms"], len(prefix), prefix)).fetchall()
    rows = [(unstaged(x["path"]), x) for x in rows]  # 10.ogg's, not 10.aac's (a device that changed codec)
    return [x for (hour, gen), x in sorted(rows, key=lambda y: (y[0][1], y[1]["pos"])) if hour == r["path"] and (below is None or gen < below)]


def merge(path: str):
    """Puts the staged late pieces of the sealed `path` at the end of its object (a new version of the same key), or
    finishes the merge a crash left (its intent)."""
    with lock(path):
        r = row(path)
        if r is None or r["state"] != "sealed":
            return
        got = landed(r)
        if got:  # one under way got there (perhaps not the newest): only its transaction is left
            it, etag = got
        else:  # no upload got there (the object is as before): from the start
            with audio._append:  # the pieces still to come go to the next staging file
                with db.tx(durable=True) as t:
                    t.execute("UPDATE store_objects SET gen = gen + 1 WHERE path = ?", (path,))
            r = row(path)
            rows = staged(r, r["gen"])
            if not rows:
                return finish(r, [])
            with tempfile.TemporaryFile() as f:
                store.s3().download(path, f)
                if (f.tell(), store.file_sha(f, f.tell())) != (r["size"], r["sha256"]):
                    raise store.Unreachable(f"{path}: the object is not the one sealed ({r['size']} bytes, {r['sha256']})")
                f.seek(0, os.SEEK_END)
                moves = []
                for x in rows:
                    moves.append([x["device"], x["start_ms"], x["path"], x["pos"], f.tell()])
                    data = store.local().read(x["path"], x["pos"], x["size"])
                    if len(data) != x["size"]:
                        raise OSError(f"{x['path']}: staged audio ended early")
                    f.write(data)
                size = f.tell()
                it = {"size": size, "sha256": store.file_sha(f, size), "moves": moves}
                add_intent(path, it)
                store.s3().write(path, f, it["sha256"], replaces=r["etag"])  # only over the version it was made from
            if not (etag := matches(path, it)):
                raise store.Unreachable(f"{path}: the object does not match what was uploaded")
        finish(row(path), it["moves"], it | {"etag": etag})


def finish(r, moves: list, it: dict | None = None):
    """A merge's transaction: the staged rows moved into the object (those a longer resend replaced meanwhile left
    alone), its size and SHA-256, the late counters of what was staged since; then the staging files done with."""
    path = r["path"]
    with db.tx(durable=True) as t:
        for device, start_ms, old, pos, new in moves:
            t.execute("UPDATE pieces SET path = ?, pos = ? WHERE device = ? AND start_ms = ? AND path = ? AND pos = ?",
                      ("s3:" + path, new, device, start_ms, old, pos))
        later = staged(r)
        t.execute("UPDATE store_objects SET size = ?, sha256 = ?, etag = ?, intent = NULL, late_first = ?, late_last = ?, late_bytes = ? WHERE path = ?",
                  (*((it["size"], it["sha256"], it["etag"]) if it else (r["size"], r["sha256"], r["etag"])), min((x["received_ms"] for x in later), default=None),
                   max((x["received_ms"] for x in later), default=None), sum(x["size"] for x in later), path))
    root = T().recordings
    for f in sorted((root / "staging" / path).parent.glob(f"{PurePosixPath(path).stem}.*")):
        hour, gen = unstaged(f.relative_to(root).as_posix())
        if hour == path and gen is not None and gen < r["gen"]:
            release(f.relative_to(root).as_posix())


def pending() -> dict:
    """The store's state for the metrics: bytes on local disk waiting for the object store (hour files open, being
    sealed or too large for one PUT, and staging files; recordings from before STORAGE=s3 not counted), seals due,
    merges waiting."""
    now, c = int(time.time() * 1000), db.conn()
    root, local = T().recordings, 0
    for (path,) in c.execute("SELECT path FROM store_objects WHERE state IN ('open', 'sealing', 'local')"):
        with contextlib.suppress(FileNotFoundError):
            local += (root / path).stat().st_size
    for f in (root / "staging").rglob("*") if (root / "staging").is_dir() else ():
        local += f.stat().st_size if f.is_file() else 0
    return {"local_bytes": local,
            "pending_seal": c.execute("SELECT count(*) FROM store_objects WHERE state = 'sealing' OR state = 'open' AND seal_at <= ?", (now,)).fetchone()[0],
            "pending_merge": c.execute("SELECT count(*) FROM store_objects WHERE state = 'sealed' AND late_bytes > 0").fetchone()[0]}


async def loop():
    """The tenant's sealing and merging, every EVERY_S (STORAGE=s3 only: server.run_tenant)."""
    await anyio.to_thread.run_sync(sweep)
    while True:
        await anyio.to_thread.run_sync(step)
        await anyio.sleep(EVERY_S)
