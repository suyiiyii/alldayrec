"""local_asr without sherpa-onnx or its model (none in CI): a fake recognizer stands in. The pieces a window is cut
into, token times into sentence times, sentences cut at punctuation and pauses, token text joined, nothing from
silence, and fetch(): nothing when the pinned files are there, the files out of the pinned tarball otherwise."""
import hashlib
import io
import tarfile

import numpy as np
import pytest

from alldayrec import infer
from alldayrec import local_asr
from alldayrec.local_asr import SR


class Rec:
    """Decodes by table: the piece's length in seconds -> (tokens, timestamps)."""

    def __init__(self, table):
        self.table, self.seen = table, []

    def create_stream(self):
        return self

    def accept_waveform(self, sr, xf):
        self.seen.append(len(xf) / sr)

    def decode_stream(self, st):
        toks, ts = self.table[round(self.seen[-1])]
        self.result = type("R", (), {"tokens": toks, "timestamps": ts})


def test_sentences_and_times(monkeypatch):
    # 。 ends the first; ， the second (以 ends where ， starts); 在 starts 2 s after ， (a pause); ？ sits in
    # the pause after 吗 and joins it; the last token ends TOKEN_MS after it starts (at most at the piece's end)
    toks = [" 今", " 天", " 好", " 。", " 还", " 可", " 以", " ，", " 在", " 吗", " ？", " G", "it", "H", "ub", " 上"]
    rec = Rec({8: (toks, [0.5, 0.7, 0.9, 1.1, 1.3, 1.5, 1.7, 1.9, 3.9, 4.1, 4.8, 5.0, 5.1, 5.2, 5.3, 5.5])})
    monkeypatch.setattr(local_asr, "recognizer", lambda: rec)
    assert local_asr._transcribe(np.zeros(8 * SR, np.int16)) == [
        {"text": "今天好。", "start_ms": 500, "end_ms": 1100}, {"text": "还可以，", "start_ms": 1300, "end_ms": 1900},
        {"text": "在吗？", "start_ms": 3900, "end_ms": 4400}, {"text": "GitHub 上", "start_ms": 5000, "end_ms": 5800}]


def test_pieces_and_offsets(monkeypatch):
    # 45 s: a dip of silence at 15 s and at 35 s, the rest noise; a piece ends in the first dip past 10 s
    x = (np.random.default_rng(0).integers(-3000, 3000, 45 * SR)).astype(np.int16)
    x[15 * SR:int(15.3 * SR)] = 0
    x[35 * SR:int(35.3 * SR)] = 0
    ps = local_asr.pieces(x)
    assert [round(b / SR, 2) for a, b in ps] == [15.05, 35.0, 45.0] and all(b - a <= 20 * SR for a, b in ps)
    rec = Rec({15: ([" 一", " 。"], [1.0, 2.0]), 20: ([" 二"], [0.5]), 10: ([], [])})
    monkeypatch.setattr(local_asr, "recognizer", lambda: rec)
    got = local_asr._transcribe(x)
    assert got == [{"text": "一。", "start_ms": 1000, "end_ms": 1300}, {"text": "二", "start_ms": 15550, "end_ms": 15850}]
    assert rec.seen == [15.05, 19.95, 10.0]


def test_empty(monkeypatch):
    monkeypatch.setattr(local_asr, "recognizer", lambda: Rec({3: ([], []), 2: ([" ，"], [0.5])}))
    assert local_asr._transcribe(np.zeros(3 * SR, np.int16)) == []
    assert local_asr._transcribe(np.zeros(2 * SR, np.int16)) == [], "punctuation alone: nothing"
    assert local_asr.pieces(np.zeros(0, np.int16)) == [(0, 0)]


def test_fetch(tmp_path, monkeypatch):
    files = {"tokens.txt": b"<blk> 0\n", "bpe.model": b"bpe"}
    monkeypatch.setattr(local_asr, "FILES", {n: hashlib.sha256(b).hexdigest() for n, b in files.items()})
    tar = io.BytesIO()
    with tarfile.open(fileobj=tar, mode="w:bz2") as t:
        for n, b in {**files, "README.md": b"x"}.items():
            i = tarfile.TarInfo(f"{local_asr.TAR}/{n}")
            i.size = len(b)
            t.addfile(i, io.BytesIO(b))
    monkeypatch.setattr(local_asr, "TAR_SHA", hashlib.sha256(tar.getvalue()).hexdigest())
    calls = []
    monkeypatch.setattr(infer.urllib.request, "urlopen", lambda url, timeout: (calls.append(url), io.BytesIO(tar.getvalue()))[1])
    d = tmp_path / local_asr.NAME
    local_asr.fetch(d)
    assert calls == [local_asr.URL] and {p.name: p.read_bytes() for p in d.iterdir()} == files
    local_asr.fetch(d)
    assert len(calls) == 1, "there and pinned: no download"
    (d / "tokens.txt").write_bytes(b"other")
    local_asr.fetch(d)
    assert len(calls) == 2 and (d / "tokens.txt").read_bytes() == files["tokens.txt"], "another sha256: fetched again"
    monkeypatch.setattr(local_asr, "TAR_SHA", "0" * 64)  # the tarball is not the pinned one: refused, nothing left behind
    (d / "tokens.txt").unlink()
    with pytest.raises(RuntimeError, match="not the pinned"):
        local_asr.fetch(d)
    assert sorted(p.name for p in d.iterdir()) == ["bpe.model"]
