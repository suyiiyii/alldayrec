"""An imported .m4a, its index (moov) after the audio as ffmpeg and many apps write it: decoded all the same, on upload
and when read back (from a pipe it came out empty, 10-26)."""
import datetime as dt
import subprocess
import tempfile
from pathlib import Path

from alldayrec import audio
from alldayrec import db


def test_m4a_moov_at_end():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    f = Path(tempfile.mkdtemp()) / "rec.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=120", "-c:a", "aac", "-b:a", "24k", str(f)], check=True)
    body = f.read_bytes()
    assert body.find(b"moov") > body.find(b"mdat") > 0, "the index after the audio"
    start = int(dt.datetime(2026, 10, 20, 9, 30).timestamp() * 1000)
    assert 119.9 < audio.save("import-test", start, body, "audio/mp4") < 120.1
    row = db.conn().execute("SELECT * FROM pieces WHERE device = 'import-test'").fetchone()
    assert abs(len(audio.samples(row)) - 120 * audio.SR) < audio.SR // 10, "read back whole"


if __name__ == "__main__":
    test_m4a_moov_at_end()
    print("ok")
