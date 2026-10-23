"""infer.py without its model files (none in CI): the CMVN reader, and check() naming every missing or different file."""
import struct

import numpy as np
import pytest

import infer


def test_cmvn_and_check(tmp_path):
    stats = np.array([[2.0, 4.0, 2.0], [6.0, 20.0, 0.0]])  # count 2: means 1, 2; variances 2, 6
    (tmp_path / infer.CMVN).write_bytes(b"\0BDM \4" + struct.pack("<i", 2) + b"\4" + struct.pack("<i", 3) + stats.astype("<f8").tobytes())
    mean, istd = infer.cmvn(tmp_path / infer.CMVN)
    assert np.allclose(mean, [1, 2]) and np.allclose(istd, [1 / np.sqrt(2), 1 / np.sqrt(6)])
    with pytest.raises(SystemExit) as e:
        infer.check(tmp_path)
    assert all(n in str(e.value) for n in infer.MODELS) and "another sha256" in str(e.value) and "is missing" in str(e.value)
    assert all(u in str(e.value) for u in infer.SOURCES.values()) and "delete a wrong one and restart" in str(e.value), "where the pinned one is"


def test_fetch(tmp_path, monkeypatch):
    """fetch(): a missing file is downloaded and kept only with the pinned sha256; one already there is left to check();
    a download that fails stops the start once, saying where the file is."""
    import hashlib
    import io
    import urllib.error
    body = {"http://m/a": b"model a", "http://m/b": b"not b"}

    def urlopen(url, timeout):
        if url not in body:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        return io.BytesIO(body[url])
    monkeypatch.setattr(infer.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(infer, "MODELS", {"a": hashlib.sha256(b"model a").hexdigest(), "b": "0" * 64, "c": "1" * 64, "d": "2" * 64})
    (tmp_path / "m").mkdir()
    (tmp_path / "m/c").write_bytes(b"there")
    monkeypatch.setattr(infer, "SOURCES", {"a": "http://m/a", "c": "http://m/c"})
    infer.fetch(tmp_path / "m")
    assert sorted(p.name for p in (tmp_path / "m").iterdir()) == ["a", "c"] and (tmp_path / "m/a").read_bytes() == b"model a"
    monkeypatch.setattr(infer, "SOURCES", {"b": "http://m/b"})
    with pytest.raises(SystemExit, match="http://m/b has sha256"):
        infer.fetch(tmp_path / "m")
    monkeypatch.setattr(infer, "SOURCES", {"d": "http://m/d"})
    with pytest.raises(SystemExit, match=r"downloading d from http://m/d failed: HTTP Error 404.*only the file at its URL.*put it in"):
        infer.fetch(tmp_path / "m")
    assert sorted(p.name for p in (tmp_path / "m").iterdir()) == ["a", "c"], "nothing kept of a bad or failed download"
