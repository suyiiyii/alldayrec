"""A piece sent again (same device and start): the longer copy is kept, so no retry loses audio."""
import datetime as dt
import subprocess
import tempfile
from pathlib import Path

from alldayrec import audio
from alldayrec import db
from alldayrec.tenant import T


def test_resend():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    tone = lambda s: subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:d={s}", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                                    capture_output=True, check=True).stdout
    start = int(dt.datetime(2026, 10, 2, 10, 0).timestamp() * 1000)
    hour = T().recordings / "chunks/phone/2026-10-02/10.ogg"
    row = lambda: db.conn().execute("SELECT * FROM pieces WHERE device = 'phone' AND start_ms = ?", (start,)).fetchone()
    first = tone(60)
    assert audio.save("phone", start, first, "audio/ogg") > 59
    size, kept = hour.stat().st_size, dict(row())

    assert audio.save("phone", start, first, "audio/ogg") > 59, "the same bytes again: a resend"
    assert audio.save("phone", start, tone(30), "audio/ogg") > 59, "shorter: the kept copy's seconds"
    assert hour.stat().st_size == size and dict(row()) == kept, "neither is stored: the hour file and the row stay"

    longer = tone(90)  # an Android batch retried with more pieces after the answer to the first try was lost
    assert audio.save("phone", start, longer, "audio/ogg") > 89
    assert hour.stat().st_size == size + len(longer), "appended once"
    assert row()["pos"] == size and row()["size"] == len(longer) and len(audio.samples(row())) > 89 * audio.SR, \
        "the row points at the longer copy"
    assert db.conn().execute("SELECT count(*) FROM pieces").fetchone()[0] == 1


if __name__ == "__main__":
    test_resend()
    print("ok")
