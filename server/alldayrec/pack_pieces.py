"""One-off (10-04): moves the pieces stored one file each into hour files, as audio.save now keeps them. Run with the
server stopped:  python -m alldayrec.pack_pieces LIVE_DATA/live.db RECORDINGS_DIR
Each piece is appended unchanged to its device's file for its hour, in time order, and its row gets path, pos and size;
then every row is read back and compared byte for byte with its old file. Only when all match are the old files moved
to RECORDINGS_DIR/chunks.old/<device>/ (delete that after a few days). Run again after a crash: rows already packed are
skipped (bytes appended for an unfinished row stay unread).
"""
import sys
from pathlib import Path

from alldayrec import audio
from alldayrec import db


def pack(db_path: Path, rec: Path):
    db.open_db(db_path, rec)
    rows = db.conn().execute("SELECT device, start_ms, path FROM pieces WHERE pos IS NULL ORDER BY device, start_ms").fetchall()
    old = {(r["device"], r["start_ms"]): r["path"] for r in rows}
    missing = [p for p in old.values() if not (rec / p).exists()]
    if missing:
        sys.exit(f"{len(missing)} pieces have no file, e.g. {missing[:3]}: nothing done")
    for n, r in enumerate(rows, 1):
        body = (rec / r["path"]).read_bytes()
        path = audio.hour_file(r["device"], r["start_ms"], Path(r["path"]).suffix[1:])
        pos = audio.append(path, body)
        db.conn().execute("UPDATE pieces SET path = ?, pos = ?, size = ? WHERE device = ? AND start_ms = ?",
                          (str(path), pos, len(body), r["device"], r["start_ms"]))
        if n % 5000 == 0:
            print(f"{n} of {len(rows)}")
    bad = []
    for (device, start_ms), p in old.items():
        r = db.conn().execute("SELECT * FROM pieces WHERE device = ? AND start_ms = ?", (device, start_ms)).fetchone()
        with open(rec / r["path"], "rb") as f:
            f.seek(r["pos"])
            if f.read(r["size"]) != (rec / p).read_bytes():
                bad.append(p)
    if bad:
        sys.exit(f"{len(bad)} pieces read back differently, e.g. {bad[:3]}: old files left in place")
    for p in old.values():
        to = rec / "chunks.old" / Path(p).relative_to("chunks")
        to.parent.mkdir(parents=True, exist_ok=True)
        (rec / p).rename(to)
    print(f"packed {len(old)} pieces; old files in {rec / 'chunks.old'}")


if __name__ == "__main__":
    pack(Path(sys.argv[1]), Path(sys.argv[2]))
