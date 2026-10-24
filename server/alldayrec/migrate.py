"""One-off: the jsonl/json files of LIVE_DATA and the uploaded pieces under RECORDINGS_DIR into a new LIVE_DATA/live.db.

    set -a; . ../.env; set +a; uv run python -m alldayrec.migrate

The old files are only read; move them away by hand once the server runs on the database.
"""
import concurrent.futures as cf
import json
import os
import subprocess
from pathlib import Path

from alldayrec import db

DATA, REC = Path(os.environ["LIVE_DATA"]), Path(os.environ["RECORDINGS_DIR"])
lines = lambda p: [json.loads(l) for l in open(p)] if p.exists() else []


def seconds(p: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    return round(float(out.stdout.strip()), 3)


def main():
    out = DATA / "live.db"
    if out.exists():
        raise SystemExit(f"{out} exists")
    if list(DATA.glob("????-??-??.jsonl")):
        raise SystemExit("first-layer <date>.jsonl files exist; the database has no place for pending rows")
    db.open_db(out, REC)
    n, finalized = {}, ""
    with db.tx() as t:
        for p in sorted(DATA.glob("*.final.jsonl")):  # each record replaces the rows starting in [from, to)
            rows = []
            for rec in lines(p):
                rows = [r for r in rows if not rec["from"] <= r["start"] < rec["to"]] + rec["rows"]
                if "renamed" not in rec:  # a rename's record ends past its conversation, not at a finalized time
                    finalized = max(finalized, rec["to"])
            db.insert_sentences(t, sorted(rows, key=lambda r: r["start"]))
            n["sentences"] = n.get("sentences", 0) + len(rows)
        convs = [c for p in sorted(DATA.glob("*.conversations.jsonl")) for c in lines(p)]
        for c in convs:
            db.insert_conversation(t, {**c, "sentences": len(c["sentences"])})
        ids = {c["id"] for c in convs}
        n["conversations"] = len(convs)
        for p in sorted((DATA / "summaries").glob("*.json")):
            s = json.loads(p.read_text())
            if s["conversation"] in ids:
                db.put_summary(s, t)
                n["summaries"] = n.get("summaries", 0) + 1
            else:
                n["summaries without a conversation (dropped)"] = n.get("summaries without a conversation (dropped)", 0) + 1
        vs = lines(DATA / "voices/history.jsonl") + lines(DATA / "voices/live.jsonl")
        db.insert_voices(t, vs)
        n["voices"] = len(vs)
        for key, grp in lines(DATA / "voices/apart.jsonl"):
            t.execute("INSERT OR IGNORE INTO apart VALUES (?, ?)", (key, grp))
        people = json.loads((DATA / "people.json").read_text()) if (DATA / "people.json").exists() else {}
        for name, v in people.items():
            t.execute("INSERT INTO people VALUES (?, ?, ?)", (name, v["n"], db.blob(v["e"])))
        n["people"] = len(people)
        g = DATA / "grouper.json"
        db.put_kv("open_conversation", json.loads(g.read_text()).get("open") if g.exists() else None, t)
        if finalized:
            db.put_kv("finalized_to", finalized, t)
        files = sorted(p for p in REC.glob("chunks/*/*.*") if p.stem.isdigit())
        with cf.ThreadPoolExecutor(16) as ex:
            secs = list(ex.map(seconds, files))
        t.executemany("INSERT INTO pieces (device, start_ms, seconds, path) VALUES (?, ?, ?, ?)",
                      [(p.parent.name, int(p.stem), s, str(p.relative_to(REC))) for p, s in zip(files, secs)])
        n["pieces"] = len(files)
    voices = db.conn().execute("SELECT count(*) FROM voices").fetchone()[0]
    print(json.dumps({**n, "voices kept (one per conversation x label)": voices, "finalized_to": finalized}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
