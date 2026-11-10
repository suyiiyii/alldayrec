"""The storage interface (store.py): what audio.py relies on of every implementation."""
import io

import numpy as np

from alldayrec import audio, db, seal, store


def test_local(tmp_path):
    db.open_db(tmp_path / "t.db")
    s = store.local()
    assert s.append("chunks/d/2026-10-02/10.ogg", b"abc") == 0
    assert s.append("chunks/d/2026-10-02/10.ogg", io.BytesIO(b"defg")) == 3, "a file's bytes go on where the last ended"
    assert s.read("chunks/d/2026-10-02/10.ogg", 3, 4) == b"defg" and s.read("chunks/d/2026-10-02/10.ogg", 5, 99) == b"fg"
    s.write("rejected/d/1.bin", io.BytesIO(b"xyz"))
    assert (tmp_path / "rejected/d/1.bin").read_bytes() == b"xyz" and s.exists("rejected/d/1.bin")
    s.delete("rejected/d/1.bin")
    s.delete("rejected/d/1.bin")
    assert not s.exists("rejected/d/1.bin"), "deleting what is not there is no error"


def test_local_seals_nothing(tmp_path):
    """STORAGE=local: no store_objects rows, nothing sealed, the hour file stays."""
    db.open_db(tmp_path / "t.db")
    audio.save("phone", 1759370400000, audio.wav(np.zeros(audio.SR, np.int16)), "audio/wav")
    seal.step()
    assert db.conn().execute("SELECT path FROM pieces").fetchone()[0].startswith("chunks/phone/")
    assert db.conn().execute("SELECT count(*) FROM store_objects").fetchone()[0] == 0
