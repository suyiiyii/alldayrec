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


def test_s3_signs_the_host_it_sends(tmp_path):
    """An endpoint with its default port written out and a path: the request is signed for the Host header it goes
    with and the path it asks for (a signature over :443 is refused)."""
    import httpx2

    from alldayrec import sigv4
    db.open_db(tmp_path / "t.db")
    seen = []
    s = store.S3("https://s3.example.com:443/base/", "b", "AK", "SK", "", "p/",
                 transport=httpx2.MockTransport(lambda r: seen.append(r) or httpx2.Response(404)))
    assert s.head("s3:chunks/x.ogg") is None
    r = seen[0]
    assert r.headers["host"] == "s3.example.com" and r.url.raw_path == b"/base/b/p/local/chunks/x.ogg"
    want = sigv4.sign("HEAD", "s3.example.com", "/base/b/p/local/chunks/x.ogg", {}, {}, sigv4.EMPTY, "AK", "SK", "us-east-1",
                      r.headers["x-amz-date"])["authorization"]
    assert r.headers["authorization"] == want
