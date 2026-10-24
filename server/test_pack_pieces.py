"""pack_pieces on pieces stored the old way: one file each, rows without pos."""
import datetime as dt
import sqlite3
import subprocess
import tempfile
from pathlib import Path

from alldayrec import audio
from alldayrec import db
from alldayrec import pack_pieces


def test_pack():
    rec = Path(tempfile.mkdtemp())
    tone = lambda s: subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:d={s}", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                                    capture_output=True, check=True).stdout
    T = lambda h, m: int(dt.datetime(2026, 10, 2, h, m).timestamp() * 1000)
    c = sqlite3.connect(rec / "t.db")  # the table as it was before 10-04
    c.execute("CREATE TABLE pieces (device TEXT NOT NULL, start_ms INTEGER NOT NULL, seconds REAL NOT NULL, path TEXT NOT NULL, "
              "PRIMARY KEY (start_ms, device))")
    old = {}
    for h, m in ((9, 59), (10, 0), (10, 1)):
        p = Path("chunks/phone") / f"{T(h, m)}.ogg"
        (rec / p).parent.mkdir(parents=True, exist_ok=True)
        old[T(h, m)] = tone(30)
        (rec / p).write_bytes(old[T(h, m)])
        c.execute("INSERT INTO pieces VALUES (?, ?, ?, ?)", ("phone", T(h, m), 30.0, str(p)))
    c.commit()
    c.close()
    pack_pieces.pack(rec / "t.db", rec)
    assert sorted(p.relative_to(rec).as_posix() for p in (rec / "chunks").rglob("*.*")) == \
        ["chunks/phone/2026-10-02/09.ogg", "chunks/phone/2026-10-02/10.ogg"]
    assert len(list((rec / "chunks.old/phone").iterdir())) == 3
    for r in db.conn().execute("SELECT * FROM pieces"):
        with open(rec / r["path"], "rb") as f:
            f.seek(r["pos"])
            assert f.read(r["size"]) == old[r["start_ms"]]
        assert len(audio.samples(r)) > 29 * audio.SR
    pack_pieces.pack(rec / "t.db", rec)  # again: nothing left to pack


if __name__ == "__main__":
    test_pack()
    print("ok")
