"""A device's stored audio through the final layer again (device_pipeline.pipeline, as a backlog: no page watches, so
no drafts and no live analysis; summaries are queued, never written) into a store of its own, and two such runs compared
row by row: the check that a change to how pieces are finalized (#433: side by side) leaves every sentence, its time,
speaker and conversation as they were. The engine's answers are kept in a cache keyed by the audio it was sent: the
first run asks the engine, the next get the same answers (with the time the call took when --latency), so two runs
differ only by the code. A piece no run before sent (a miss) means the pieces were cut differently.

    python -I final_parity.py snapshot <live.db> <recordings> <device> <from> <to> > snap.tar    read only: pieces, people, 我
    python -I final_parity.py run <snap dir> <out dir> [--cache asr.json] [--latency]           out dir/result.json
    python -I final_parity.py compare <a result.json> <b result.json>                          exit 1 on any difference

-I (no script dir on sys.path): `uv run python -I <this file>` in another checkout runs that checkout's alldayrec.
from/to: local times (2026-11-07T15:00). run reads ENGINE, MODELS_DIR, INFER_* as the server does.
"""
import copy
import datetime as dt
import hashlib
import io
import json
import logging
import os
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

ROWS = "SELECT id, start, end, speaker, text, music, speaker_confidence, source FROM sentences ORDER BY start, id"
CONVS = "SELECT id, start, end, participants, sentences, speech_s FROM conversations ORDER BY start"


def snapshot(store: str, recordings: str, device: str, a: str, b: str):
    """The pieces of device starting in [a, b), the hour files they are in, the people bank and 我's voiceprint, as a
    tar on stdout. Reads only."""
    ms = lambda s: int(dt.datetime.fromisoformat(s).timestamp() * 1000)
    c = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    pieces = [dict(r) for r in c.execute("SELECT * FROM pieces WHERE device = ? AND start_ms >= ? AND start_ms < ? ORDER BY start_ms",
                                         (device, ms(a), ms(b)))]
    for p in pieces:
        p["env"] = None  # the envelope is for the timeline page
    people = [{"name": r["name"], "n": r["n"], "e": r["e"].hex()} for r in c.execute("SELECT * FROM people")]
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as t:
        def add(name: str, data: bytes):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        add("pieces.json", json.dumps({"device": device, "pieces": pieces}).encode())
        add("people.json", json.dumps(people).encode())
        if (vp := Path(store).parent / "owner-redimnet2.npz").exists():
            t.add(vp, "owner-redimnet2.npz")
        for path in dict.fromkeys(p["path"] for p in pieces):
            t.add(Path(recordings) / path, f"recordings/{path}")


def hwm_mb() -> float | None:
    """The process's RSS high-water mark, from /proc (Linux)."""
    f = Path("/proc/self/status")
    return next((int(line.split()[1]) / 1024 for line in f.read_text().splitlines() if line.startswith("VmHWM")), None) if f.exists() else None


def run(snap: Path, out: Path, cache_path: Path | None, latency: bool):
    import anyio
    from alldayrec import asr, core, db, device_pipeline, infer, music
    from alldayrec.diarist import load_embedder

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    meta = json.loads((snap / "pieces.json").read_text())
    device, pieces = meta["device"], meta["pieces"]
    out.mkdir(parents=True, exist_ok=True)
    store = out / "live.db"
    if store.exists():
        raise SystemExit(f"{store} exists: a run starts from an empty store")
    db.open_db(store, snap / "recordings")
    with db.tx() as t:
        cols = list(pieces[0])
        t.executemany(f"INSERT INTO pieces ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [[p[k] for k in cols] for p in pieces])
        t.executemany("INSERT INTO people (name, n, e) VALUES (?, ?, ?)",
                      [(p["name"], p["n"], bytes.fromhex(p["e"])) for p in json.loads((snap / "people.json").read_text())])
        end = max(p["start_ms"] + p["seconds"] * 1000 for p in pieces)
        db.put_kv(f"end_at:{device}", dt.datetime.fromtimestamp(end / 1000).isoformat(timespec="milliseconds"), t)  # all of it in
    if (vp := snap / "owner-redimnet2.npz").exists():
        (out / vp.name).write_bytes(vp.read_bytes())

    cache = json.loads(cache_path.read_text()) if cache_path and cache_path.exists() else {}
    misses, provider = [], asr.PROVIDERS[asr.ENGINE]

    async def cached(x, partial=None):
        key = hashlib.sha1(x.tobytes()).hexdigest()
        if key in cache:
            if latency:
                await anyio.sleep(cache[key]["s"])
            return copy.deepcopy(cache[key]["segs"])
        t = time.perf_counter()
        segs = await provider(x, partial)
        cache[key] = {"segs": segs, "s": round(time.perf_counter() - t, 3)}
        misses.append(key)
        return copy.deepcopy(segs)
    asr.PROVIDERS[asr.ENGINE] = cached
    asr.ENGINE_FALLBACK = ""  # a failure is tried again (the final layer's own retry), never answered by another engine
    infer.fetch()
    infer.check()
    core.state["embed"] = load_embedder()
    core.state["music"] = music.heard if music.ON else None
    final_to = dt.datetime.fromtimestamp(end / 1000)

    t0, c0, peak = time.monotonic(), sum(os.times()[:2]), [0.0]

    async def usage():
        """The process's CPU each second, in cores: its busiest second."""
        last, t = sum(os.times()[:2]), time.monotonic()
        while True:
            await anyio.sleep(1)
            now, t1 = sum(os.times()[:2]), time.monotonic()
            peak[0], last, t = max(peak[0], (now - last) / (t1 - t)), now, t1

    async def main():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, device)
            tg.start_soon(usage)
            while not (db.kv(f"end_at:{device}") is None and (done := db.kv(f"finalized_to:{device}"))
                       and dt.datetime.fromisoformat(done) >= final_to - dt.timedelta(milliseconds=1)):
                await anyio.sleep(0.5)
            tg.cancel_scope.cancel()
    anyio.run(main)
    c = db.conn()
    res = {"device": device, "audio_s": round(sum(p["seconds"] for p in pieces)), "wall_s": round(time.monotonic() - t0, 1),
           "cpu_s": round(sum(os.times()[:2]) - c0, 1), "cpu_peak": round(peak[0], 2), "rss_peak_mb": hwm_mb(),
           "concurrency": {k: getattr(device_pipeline, k, None) for k in ("FINAL_ASR_CONCURRENCY", "FINAL_EMBED_CONCURRENCY")},
           "pieces": c.execute("SELECT count(*) FROM perf_pieces WHERE layer = 'final'").fetchone()[0], "misses": len(misses),
           "sentences": [dict(r) for r in c.execute(ROWS)], "conversations": [dict(r) for r in c.execute(CONVS)]}
    (out / "result.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    if cache_path:
        cache_path.write_text(json.dumps(cache, ensure_ascii=False))
    print(json.dumps({k: v for k, v in res.items() if k not in ("sentences", "conversations")} |
                     {"sentences": len(res["sentences"]), "conversations": len(res["conversations"])}))


def compare(a: dict, b: dict) -> list[str]:
    """Every difference between two runs' rows and conversations, as lines; none: the same."""
    out = []
    for kind, key in (("sentences", "id"), ("conversations", "id")):
        x, y = {r[key]: r for r in a[kind]}, {r[key]: r for r in b[kind]}
        for k in sorted(x.keys() | y.keys()):
            if x.get(k) != y.get(k):
                out.append(f"{kind} {k}: {x.get(k)} != {y.get(k)}")
    return out


def demo():
    r = {"id": "f1", "start": "s", "end": "e", "speaker": "我", "text": "好", "music": None, "speaker_confidence": 0.9, "source": "d"}
    a = {"sentences": [r], "conversations": [{"id": "c1", "start": "s", "end": "e", "participants": "[]", "sentences": 1, "speech_s": 1.0}]}
    assert compare(a, copy.deepcopy(a)) == []
    b = copy.deepcopy(a)
    b["sentences"][0]["speaker"] = "S1"
    b["conversations"].append({**a["conversations"][0], "id": "c2"})
    assert len(compare(a, b)) == 2 and compare(a, b)[0].startswith("sentences f1")


if __name__ == "__main__":
    match sys.argv[1:]:
        case ["snapshot", store, recordings, device, a, b]:
            snapshot(store, recordings, device, a, b)
        case ["run", snap, out, *opts]:
            cache = Path(opts[opts.index("--cache") + 1]) if "--cache" in opts else None
            run(Path(snap), Path(out), cache, "--latency" in opts)
        case ["compare", a, b]:
            diff = compare(*(json.loads(Path(p).read_text()) for p in (a, b)))
            print("\n".join(diff) or "the same")
            ra, rb = (json.loads(Path(p).read_text()) for p in (a, b))
            print(f"{len(ra['sentences'])} / {len(rb['sentences'])} sentences, {len(ra['conversations'])} / {len(rb['conversations'])} conversations")
            sys.exit(1 if diff else 0)
        case ["demo"]:
            demo()
        case _:
            raise SystemExit(__doc__)
