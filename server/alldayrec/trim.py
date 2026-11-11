"""Keeping only the audio near speech: an hour file, once old enough, cut down to its pieces near what was said
(docs: explanation/architecture, 录音存在哪里).

    cd server && uv run python -m alldayrec.trim vad   (--tenant ID | --db live.db --recordings DIR) [--shard i/k] > vad.jsonl
    cd server && uv run python -m alldayrec.trim plan  (--tenant ID | --db ... --recordings ...) --vad vad.jsonl  # changes nothing
    cd server && uv run python -m alldayrec.trim apply --tenant ID --vad vad.jsonl [--limit N]
    cd server && uv run python -m alldayrec.trim purge --tenant ID --confirm-backup WHEN [--yes]  # without --yes: what it would delete

Run it with the instance's environment (STORAGE, S3_*) and time zone: sentences and hour file names are in its wall
clock, and nothing checks it (another zone: no sentence is found near a piece, only the VAD keeps it). --tenant reads
DATA_DIR/tenants.json; apply and purge take only that (a --db of one tenant with another's --recordings would copy and
delete the wrong files). `vad` only reads (heavy: run it on a copy of the store and its recordings); its times are unix
ms. One apply or purge of a tenant at a time (a lock file beside its live.db): two at once could take each other's copy.

An hour file (chunks/<device>/<date>/<HH>.<ext>, on local disk or sealed into an object) may be trimmed once its
newest piece came TRIM_AFTER_DAYS ago (the nightly backup has the original by then), its device is finalized past it
with no late span still open over it, and, sealed, nothing of it is staged or being merged. A piece is kept when it
is within TRIM_PAD_S of the speech the VAD heard (FireRed Stream-VAD with the Segmenter's gain, at TRIM_VAD: well
below the SQUEEZE_VAD the engine is sent by) or of a sentence of its device, or when the VAD did not hear its time;
the others go. A piece is a whole upload, decoded on its own (audio.samples): the kept ones are copied byte for byte,
in order, to <HH>.trim.<ext> beside it (a file, or an object of its own), checked, and one transaction points the kept
rows at it, deletes the others and marks the hour done in trims, with the rows as they were (a restore from the
backup puts them back). The original is left as it is till purge, which deletes it only when told which backup has
it (--confirm-backup: the time a backup began that was checked to hold the recordings; trims done before it), at
least TRIM_PURGE_AFTER_H after the trim, while the copy is still the bytes written, no row points at the original and
it is still the bytes trimmed from (sealed: its store_objects row first, so a late piece then makes a new hour file;
a local file under the lock every append takes, store.appending, so a late piece then makes a new one).
Crash-safe: a trim is in trims ('writing') before its copy is written; a copy with no transaction after it is written
again, or deleted, by the next apply. Playback puts each piece at its own time (audio.cut), so a dropped stretch is
silence there and the kept audio does not move. What is kept decodes as before, but for a recorder's piece right
after one a trim dropped (in cold_starts; in this hour file or the next): its decoder is no longer primed by the piece
before, so its first 100 ms are muted and faded in (audio.warmed) and the rest of it may differ by a few LSB. That
start is TRIM_PAD_S or more from any speech the VAD heard and any sentence (else the dropped piece would be kept).
A piece after a real gap (a reconnect) decodes cold as it always did, unchanged.
The VAD runs per device and day, over the hours that may be trimmed only: a piece next to midnight or to an hour left
whole has the VAD's pad on one side only (a sentence's still counts).
"""
import argparse
import contextlib
import datetime as dt
import fcntl
import json
import logging
import os
import re
import sys
import tempfile
import time
import zlib
from pathlib import Path, PurePosixPath

from alldayrec import audio
from alldayrec import db
from alldayrec import seal
from alldayrec import store
from alldayrec import tenant
from alldayrec.late_store import union
from alldayrec.tenant import T

log = logging.getLogger("live")
TRIM_AFTER_DAYS = float(os.environ.get("TRIM_AFTER_DAYS", "2"))
TRIM_PAD_S = float(os.environ.get("TRIM_PAD_S", "10"))
TRIM_VAD = float(os.environ.get("TRIM_VAD", "0.1"))
TRIM_PURGE_AFTER_H = float(os.environ.get("TRIM_PURGE_AFTER_H", "24"))  # purge: no original deleted sooner after its trim
PURGE_AFTER_MIN_H = 24  # TRIM_PURGE_AFTER_H is never less: purge refuses to run
HOUR = re.compile(r"(s3:)?chunks/[^/]+/\d{4}-\d\d-\d\d/\d\d\.\w+")  # an hour file: not an import (<HH>.<start>.<size>.<ext>), not a trim's copy


def copy_of(path: str) -> str:
    """Where the kept pieces of `path` go: <HH>.trim.<ext> beside it, in the same store; its extension last."""
    p = PurePosixPath(path)
    return str(p.with_name(f"{p.stem}.trim{p.suffix}"))


def ms(iso: str) -> int:
    return int(dt.datetime.fromisoformat(iso).timestamp() * 1000)


@contextlib.contextmanager
def lock():
    """The tenant's one apply or purge (or seal adopt) at a time (another process's: leftovers() would take the copy
    of a trim under way), or SystemExit."""
    with open(T().db.with_name("trim.lock"), "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("another trim apply or purge, or seal adopt, of this tenant is running") from None
        yield


def candidates(now_ms: int) -> dict[str, dict]:
    """Every hour file with its rows and why it may not be trimmed now (reason None: it may)."""
    c, out = db.conn(), {}
    for r in c.execute("SELECT device, start_ms, seconds, path, pos, size, n, received_ms FROM pieces ORDER BY path, pos"):
        out.setdefault(r["path"], {"path": r["path"], "rows": []})["rows"].append(r)
    done = {r[0] for r in c.execute("SELECT path FROM trims WHERE state NOT IN ('writing', 'abandoned')")}  # a crash's: done again
    finalized = {k.removeprefix("finalized_to:"): ms(at) for k, v in c.execute("SELECT key, value FROM kv WHERE key LIKE 'finalized_to:%'")
                 if (at := json.loads(v))}  # null: not yet
    for path, h in out.items():
        rows = h["rows"]
        h["device"] = rows[0]["device"]
        h["lo"], h["hi"] = min(r["start_ms"] for r in rows), max(r["start_ms"] + r["seconds"] * 1000 for r in rows)
        h["got"] = max(r["received_ms"] or r["start_ms"] + r["seconds"] * 1000 for r in rows)
        h["bytes"] = sum(r["size"] for r in rows)
        h["reason"] = reason(c, h, now_ms, done, finalized)
    return out


def reason(c, h: dict, now_ms: int, done: set, finalized: dict) -> str | None:
    path = h["path"]
    if path in done:
        return "trimmed"
    if not HOUR.fullmatch(path) or any(r["seconds"] > audio.WHOLE_S for r in h["rows"]):
        return "not an hour file"  # staging, an import, a trim's copy
    if len({r["device"] for r in h["rows"]}) > 1:
        return "two devices"  # shortcut: two names made one file name (audio._name); left whole, none seen so far
    if h["got"] > now_ms - TRIM_AFTER_DAYS * 86400_000:
        return "recent"
    if finalized.get(h["device"], 0) < h["hi"]:
        return "not transcribed"
    if c.execute("SELECT 1 FROM late_spans WHERE device = ? AND status NOT IN ('done', 'failed') AND start_ms < ? AND end_ms > ?",
                 (h["device"], h["hi"], h["lo"])).fetchone():
        return "late span open"
    r = seal.row(path.removeprefix("s3:"))
    if path.startswith("s3:"):
        if r is None or r["state"] != "sealed" or r["intent"] or r["late_bytes"]:
            return "being merged"
    elif r is not None:
        return "being sealed"  # STORAGE=s3, a late piece into a local hour: it is on its way into an object
    return None


def keep(h: dict, vad: dict) -> list:
    """The rows of h to keep: near speech or a sentence, where the VAD heard nothing, or not among the pieces the VAD
    was given (one came, or a longer resend replaced one, since: its audio may differ)."""
    pad = TRIM_PAD_S * 1000
    lo, hi = h["lo"] - pad, h["hi"] + pad
    said = [[ms(a), ms(b)] for a, b in db.conn().execute(
        "SELECT start, \"end\" FROM sentences WHERE source = ? AND start < ? AND \"end\" > ?",
        (h["device"], dt.datetime.fromtimestamp(hi / 1000).isoformat(), dt.datetime.fromtimestamp(lo / 1000).isoformat()))]
    near = union([[a - pad, b + pad] for a, b in vad["speech"] + said])
    fed, given = union(vad["fed"]), {tuple(p) for p in vad["pieces"]}
    covered = lambda a, b: any(x <= a and b <= y + 1 for x, y in fed)  # +1: fed is in whole ms, seconds rounded to 1 ms
    return [r for r in h["rows"] if (r["start_ms"], r["size"]) not in given
            or not covered(a := r["start_ms"], b := r["start_ms"] + r["seconds"] * 1000) or any(x < b and a < y for x, y in near)]


def load_vad(path: str) -> dict:
    out = {}
    for line in open(path):
        v = json.loads(line)
        out[v["path"]] = v
    return out


def plan(vads: dict, now_ms: int) -> list[dict]:
    """Every hour file: what a trim would keep, or why it is left."""
    hours = list(candidates(now_ms).values())
    for h in hours:
        if h["reason"] is None:
            v = vads.get(h["path"])
            if v is None or "pieces" not in v:  # none, or from before vad() said which pieces it was given
                h["reason"] = "no VAD"
            elif v["thr"] != TRIM_VAD:
                h["reason"] = "VAD of another TRIM_VAD"
            else:
                h["kept"] = kept = keep(h, v)
                h["kept_bytes"] = sum(r["size"] for r in kept)
    return hours


def source(path: str):
    """The original's bytes as a file (an object downloaded and checked against what was sealed), its size and SHA-256."""
    f = tempfile.TemporaryFile()
    if path.startswith("s3:"):
        r = seal.row(path.removeprefix("s3:"))
        store.s3().download(path, f)
        size = f.tell()
        if (size, store.file_sha(f, size)) != (r["size"], r["sha256"]):
            raise store.Unreachable(f"{path}: the object is not the one sealed")
        return f, size, r["sha256"]
    with open(T().recordings / path, "rb") as h:
        while data := h.read(store.COPY_BYTES):
            f.write(data)
    size = f.tell()
    return f, size, store.file_sha(f, size)


def write_copy(to: str, f, size: int, sha: str):
    """The copy at `to`, checked: a file on disk (written beside it, fsync'd and renamed over it), or an object with
    that SHA-256. One a crash left (no row points at it: its trim never committed) is replaced."""
    if to.startswith("s3:"):
        it = {"size": size, "sha256": sha}
        try:
            store.s3().write(to, f, sha)
        except store.Conflict:
            store.s3().write(to, f, sha, replaces=store.s3().head(to)[2])
        if not seal.matches(to, it):
            raise store.Unreachable(f"{to}: the object does not match what was uploaded")
        return
    part = to + ".part"
    store.local().delete(part)
    store.local().append(part, f)
    with open(T().recordings / part, "rb") as h:
        if (h.seek(0, os.SEEK_END), store.file_sha(h, size)) != (size, sha):
            raise OSError(f"{to}: the copy on disk is not what was written")
    os.replace(T().recordings / part, T().recordings / to)
    store.sync_dirs({(T().recordings / to).parent})


def copy_ok(r) -> bool:
    """trims row r's copy is still the bytes its trim wrote (none: nothing was kept)."""
    c, want = r["copy"], (r["kept_size"], r["kept_sha256"])
    if not r["kept_size"]:
        return True
    if c.startswith("s3:"):
        got = store.s3().head(c)
        return got is not None and got[:2] == want
    try:
        with open(T().recordings / c, "rb") as h:
            size = h.seek(0, os.SEEK_END)
            return size == want[0] and store.file_sha(h, size) == want[1]
    except FileNotFoundError:
        return False


def done_already(t, path: str) -> bool:
    """path was trimmed (or left whole) since it was planned: an apply with an older plan leaves its trims row (the
    rows as they were) and its copy alone."""
    r = t.execute("SELECT state FROM trims WHERE path = ?", (path,)).fetchone()
    if r is not None and r[0] != "writing":
        log.warning("trim: %s is %s already: left", path, r[0])
        return True
    return False


def snapshot(rows) -> bytes:
    """The rows as they were, for a restore: every column (env as hex), zlib'd JSON."""
    return zlib.compress(json.dumps([{k: (r[k].hex() if isinstance(r[k], bytes) else r[k]) for k in r.keys()} for r in rows]).encode())


def apply_one(h: dict) -> bool:
    """Trims one hour file as planned (h: plan()'s); False when it changed meanwhile (left for the next run)."""
    path, to = h["path"], copy_of(h["path"])
    if len(h["kept"]) == len(h["rows"]):  # speech throughout: nothing to cut, never looked at again
        with db.tx(durable=True) as t:
            if done_already(t, path):
                return False
            t.execute("INSERT OR REPLACE INTO trims (path, copy, state, at, device, size, kept_size, pieces, kept) VALUES (?, ?, 'whole', ?, ?, ?, ?, ?, ?)",
                      (path, path, int(time.time() * 1000), h["device"], h["bytes"], h["bytes"], len(h["rows"]), len(h["rows"])))
        return True
    with db.tx(durable=True) as t:
        if done_already(t, path):
            return False
        t.execute("INSERT OR REPLACE INTO trims (path, copy, state, at) VALUES (?, ?, 'writing', ?)", (path, to, int(time.time() * 1000)))
    f, size, sha = source(path)
    with f, tempfile.TemporaryFile() as out:
        moves = []
        for r in h["kept"]:
            moves.append((out.tell(), r))
            f.seek(r["pos"])
            data = f.read(r["size"])
            if len(data) != r["size"]:
                raise OSError(f"{path}: a piece's bytes end early")
            out.write(data)
        new_size = out.tell()
        new_sha = store.file_sha(out, new_size)
        if new_size:
            write_copy(to, out, new_size, new_sha)
    kept = {(r["device"], r["start_ms"]) for r in h["kept"]}
    with db.tx(durable=True) as t:
        # nothing changed since the plan (BEGIN IMMEDIATE: no seal, merge or upload of another process meanwhile):
        # the same rows in it, and no seal or merge of it begun (an object's still the version read)
        rows = t.execute("SELECT * FROM pieces WHERE path = ? ORDER BY pos", (path,)).fetchall()
        o = seal.row(path.removeprefix("s3:"))
        mine = t.execute("SELECT state FROM trims WHERE path = ?", (path,)).fetchone()
        if ([(r["device"], r["start_ms"], r["pos"], r["size"]) for r in rows] != [(r["device"], r["start_ms"], r["pos"], r["size"]) for r in h["rows"]]
                or mine is None or mine[0] != "writing"
                or (o is None or o["state"] != "sealed" or o["intent"] or o["late_bytes"] or (o["size"], o["sha256"]) != (size, sha)
                    if path.startswith("s3:") else o is not None)):
            log.warning("trim: %s changed since it was planned: left for the next run", path)
            return False
        for pos, r in moves:
            t.execute("UPDATE pieces SET path = ?, pos = ? WHERE device = ? AND start_ms = ? AND path = ? AND pos = ?",
                      (to, pos, r["device"], r["start_ms"], path, r["pos"]))
        dropped = [r for r in rows if (r["device"], r["start_ms"]) not in kept]
        t.executemany("DELETE FROM pieces WHERE device = ? AND start_ms = ? AND path = ? AND pos = ?",
                      [(r["device"], r["start_ms"], path, r["pos"]) for r in dropped])
        for r in dropped:  # the piece that went on from it (audio.before), here or in the next hour, now starts cold
            n = t.execute("SELECT start_ms, path FROM pieces WHERE device = ? AND start_ms > ? ORDER BY start_ms LIMIT 1",
                          (r["device"], r["start_ms"])).fetchone()
            if n is not None and n["path"].endswith(".ogg") and abs(r["start_ms"] + r["seconds"] * 1000 - n["start_ms"]) < 10:
                t.execute("INSERT OR IGNORE INTO cold_starts (device, start_ms) VALUES (?, ?)", (r["device"], n["start_ms"]))
        t.execute("UPDATE trims SET state = 'trimmed', at = ?, device = ?, size = ?, sha256 = ?, kept_size = ?, kept_sha256 = ?, "
                  "pieces = ?, kept = ?, seconds = ?, kept_seconds = ?, rows = ? WHERE path = ?",
                  (int(time.time() * 1000), h["device"], size, sha, new_size, new_sha, len(rows), len(h["kept"]),
                   sum(r["seconds"] for r in rows), sum(r["seconds"] for r in h["kept"]), snapshot(rows), path))
    log.info("trim: %s: kept %d of %d pieces, %d of %d bytes", path, len(h["kept"]), len(rows), new_size, size)
    return True


def leftovers():
    """Trims a crash stopped before their transaction: abandoned first (a trim still under way then can no longer
    commit: its rows never point at a copy deleted here), then the copy deleted, then the trim forgotten."""
    for r in db.conn().execute("SELECT path, copy FROM trims WHERE state IN ('writing', 'abandoned')").fetchall():
        with db.tx(durable=True) as t:
            t.execute("UPDATE trims SET state = 'abandoned' WHERE path = ? AND state IN ('writing', 'abandoned')", (r["path"],))
        if db.conn().execute("SELECT 1 FROM pieces WHERE path = ? LIMIT 1", (r["copy"],)).fetchone():  # never: one transaction
            log.error("trim: %s: rows point at the copy of an abandoned trim: left", r["copy"])
            continue
        for c in (r["copy"], *([] if r["copy"].startswith("s3:") else [r["copy"] + ".part"])):
            store.of(c).delete(c)
        with db.tx(durable=True) as t:
            t.execute("DELETE FROM trims WHERE path = ? AND state = 'abandoned'", (r["path"],))


def purge(now_ms: int, backup_ms: int | None, yes: bool = False) -> dict:
    """Deletes the originals of trimmed hours (yes; else counts what it would) that the backup begun at backup_ms has
    (trimmed before it) and trimmed TRIM_PURGE_AFTER_H ago, once their copy is still the bytes written, nothing points
    at them and they are the bytes trimmed from."""
    if TRIM_PURGE_AFTER_H < PURGE_AFTER_MIN_H:
        raise SystemExit(f"trim: TRIM_PURGE_AFTER_H is {TRIM_PURGE_AFTER_H:g}, at least {PURGE_AFTER_MIN_H}")
    n = {"purged" if yes else "to purge": 0, "kept": 0, "waiting": 0}
    for r in db.conn().execute("SELECT * FROM trims WHERE state IN ('trimmed', 'purging')").fetchall():
        path = r["path"]
        if r["state"] == "trimmed" and (backup_ms is None or r["at"] >= backup_ms or r["at"] > now_ms - TRIM_PURGE_AFTER_H * 3600_000):
            n["waiting"] += 1  # not in that backup, or too soon
            continue
        if db.conn().execute("SELECT 1 FROM pieces WHERE path = ? LIMIT 1", (path,)).fetchone():
            log.warning("trim: %s kept: rows point at it again (a late piece; or one in a new file of that name, after a purge cut short)", path)
            n["kept"] += 1
            continue
        if not copy_ok(r):
            log.error("trim: %s kept: its copy %s is missing or not the bytes written", path, r["copy"])
            n["kept"] += 1
            continue
        if not yes:
            log.info("trim: %s: would be deleted", path)
            n["to purge"] += 1
            continue
        if path.startswith("s3:"):
            key = path.removeprefix("s3:")
            if r["state"] == "trimmed":
                with db.tx(durable=True) as t:  # no more merges into it: a late piece now starts a new hour file
                    o = seal.row(key)
                    if o is not None and (o["state"] != "sealed" or o["intent"] or o["late_bytes"] or (o["size"], o["sha256"]) != (r["size"], r["sha256"])):
                        log.warning("trim: %s kept: its object changed or is being merged", path)
                        n["kept"] += 1
                        continue
                    t.execute("DELETE FROM store_objects WHERE path = ?", (key,))
                    t.execute("UPDATE trims SET state = 'purging', backup = ? WHERE path = ?", (backup_ms, path))
            got = store.s3().head(path)
            if got is not None and got[:2] == (r["size"], r["sha256"]):  # not a new hour file sealed there since
                store.s3().delete(path)
        else:
            try:
                h = open(T().recordings / path, "rb")
            except FileNotFoundError:
                h = None
            if h is not None:
                with h:
                    fcntl.flock(h, fcntl.LOCK_EX)  # every append's (store.appending): none between the check and the delete
                    size = h.seek(0, os.SEEK_END)
                    if (size, store.file_sha(h, size) if size == r["size"] else None) != (r["size"], r["sha256"]):
                        log.warning("trim: %s kept: written again since it was trimmed", path)
                        n["kept"] += 1
                        continue
                    store.local().delete(path)
        with db.tx(durable=True) as t:
            t.execute("UPDATE trims SET state = 'purged', purged = ?, backup = ? WHERE path = ?", (int(time.time() * 1000), backup_ms, path))
        n["purged"] += 1
        log.info("trim: %s: original deleted", path)
    return n


def vad(shard: tuple[int, int], now_ms: int, out=sys.stdout, days: set | None = None):
    """The speech (TRIM_VAD) and the time heard of every hour file that may be trimmed, a JSON line each; each
    device's day as one stream (the server's: each moment once, pieces in time order, a recorder's decoder primed),
    shard i of k of the (device, day)s (days: only these, <device>/<date>)."""
    from alldayrec import segmenter
    hours = [h for h in candidates(now_ms).values() if h["reason"] is None]
    groups = {}
    for h in hours:
        key = (h["device"], PurePosixPath(h["path"]).parent.name)
        if days is None or "/".join(key) in days:
            groups.setdefault(key, []).append(h)
    for g, (key, hs) in enumerate(sorted(groups.items())):
        if g % shard[1] != shard[0]:
            continue
        t0, seg, seen, end, heard = time.time(), segmenter.Segmenter(), set(), 0.0, {}
        seg.loose.speech_threshold = TRIM_VAD
        speech = []
        for p in sorted((r for h in hs for r in h["rows"]), key=lambda r: r["start_ms"]):
            got = audio.fresh(p, end)
            if got is None:
                continue
            s, x = got
            end = s + len(x) * 1000 / audio.SR
            heard.setdefault(p["path"], []).append([int(s), int(end)])
            seg.push(dt.datetime.fromtimestamp(s / 1000), x)
            for a, b in seg.speech:
                if (a, b) not in seen:
                    seen.add((a, b))
                    speech.append([int(seg.time_at(a).timestamp() * 1000), int(seg.time_at(b).timestamp() * 1000)])
        if (o := seg.open_speech()) is not None:
            speech.append([int(seg.time_at(o).timestamp() * 1000), int(seg.time_at(seg.samples).timestamp() * 1000)])
        for h in hs:
            lo, hi = h["lo"] - max(TRIM_PAD_S, 60) * 1000, h["hi"] + max(TRIM_PAD_S, 60) * 1000  # room for a plan's wider pad
            print(json.dumps({"device": h["device"], "path": h["path"], "thr": TRIM_VAD, "fed": union(heard.get(h["path"], [])),
                              "pieces": [[r["start_ms"], r["size"]] for r in h["rows"]],
                              "speech": [v for v in speech if v[0] < hi and v[1] > lo]}), file=out, flush=True)
        print(f"vad {key[0]} {key[1]}: {len(hs)} hours, {sum(len(h['rows']) for h in hs)} pieces, {time.time() - t0:.0f} s", file=sys.stderr, flush=True)


def report(hours: list[dict]) -> dict:
    """plan()'s totals: by reason, and by device and day what would be kept."""
    out = {"reasons": {}, "days": {}}
    for h in hours:
        why = h["reason"] or "trim"
        out["reasons"][why] = [a + b for a, b in zip(out["reasons"].get(why, [0, 0]), [1, h["bytes"]])]
        if h["reason"] is None:
            day = f"{h['device']} {PurePosixPath(h['path']).parent.name}"
            out["days"][day] = [a + b for a, b in zip(out["days"].get(day, [0, 0, 0, 0, 0, 0]), [
                1, h["bytes"], h["kept_bytes"], len(h["rows"]), len(h["kept"]),
                sum(r["seconds"] for r in h["rows"]) - sum(r["seconds"] for r in h["kept"])])]
    return out


def main():
    a = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    a.add_argument("cmd", choices=("vad", "plan", "apply", "purge"))
    a.add_argument("--tenant", help="a tenant of DATA_DIR/tenants.json")
    a.add_argument("--db", help="a live.db (with --recordings), not a tenant's")
    a.add_argument("--recordings")
    a.add_argument("--vad", help="vad's output")
    a.add_argument("--now", help="vad, plan: as if it were then (ISO, the instance's wall clock): what was trimmable then")
    a.add_argument("--shard", default="0/1", help="vad: i/k, the i-th of k parts")
    a.add_argument("--limit", type=int, help="apply: at most this many hour files")
    a.add_argument("--day", action="append", help="vad: only this <device>/<date> (again for more)")
    a.add_argument("--confirm-backup", metavar="WHEN", help="purge: when (ISO, the instance's wall clock) a backup began "
                   "that was checked to hold the recordings: only originals trimmed before it are deleted")
    a.add_argument("--yes", action="store_true", help="purge: delete (without: only say what would go)")
    o = a.parse_args()
    if o.cmd in ("apply", "purge") and (not o.tenant or o.db or o.recordings):
        a.error(f"{o.cmd} takes --tenant only")
    if o.cmd in ("apply", "purge") and o.now:
        a.error(f"{o.cmd} runs now: --now is for vad and plan")
    if o.cmd == "purge" and not o.confirm_backup:
        a.error("purge needs --confirm-backup")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stderr)
    if o.tenant:
        tenant.enter(next(t for t in tenant.load() if t.id == o.tenant))
        db.init()
    else:
        db.open_db(Path(o.db), Path(o.recordings))
    now_ms = ms(o.now) if o.now else int(time.time() * 1000)
    if o.cmd == "vad":
        return vad(tuple(map(int, o.shard.split("/"))), now_ms, days=set(o.day) if o.day else None)
    if o.cmd == "plan":
        return print(json.dumps(report(plan(load_vad(o.vad), now_ms)), ensure_ascii=False))
    with lock():
        if o.cmd == "purge":
            backup = ms(o.confirm_backup)
            if backup > time.time() * 1000:
                a.error("--confirm-backup is in the future")
            return print(json.dumps(purge(int(time.time() * 1000), backup, o.yes)))
        leftovers()
        hours = plan(load_vad(o.vad), now_ms)
        done = 0
        for h in [h for h in hours if h["reason"] is None][:o.limit]:
            done += apply_one(h)
        print(json.dumps({"trimmed": done}))
        print(json.dumps(report(hours), ensure_ascii=False))


if __name__ == "__main__":
    main()
