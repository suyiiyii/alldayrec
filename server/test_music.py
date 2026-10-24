"""Music detection (music.py): sentences it hears as music get no voiceprint and keep music 1 in the store; the model
itself on numpy-made audio, only where yamnet.onnx is (not in CI)."""
import datetime as dt
import sqlite3

import numpy as np
import pytest
from scipy.signal import lfilter

from alldayrec import db, infer, music
from alldayrec.transcript import final_rows, label_sentences

SR = 16000


class Dia:
    def __init__(self):
        self.n = 0

    def add(self, e, dur):
        self.n += 1
        return "S1", 0.9

    def mark(self):
        return (self.n,)


def test_music_sentences_get_no_voiceprint(monkeypatch):
    """A sentence heard as music: S?, no embed call, no diarist mark, music 1 on its row; it never joins a neighbour's
    row (a song's S? next to a short S? sentence of a person)."""
    from alldayrec import transcript
    monkeypatch.setattr(transcript, "TURN_GAP_S", 1.0)  # the two S? rows would join without the music flag
    x = np.zeros(10 * SR, np.int16)
    x[3 * SR:6 * SR] = 1000  # "music" here
    new = lambda: [dict(start_ms=a, end_ms=b, speaker="0", text=t) for a, b, t in
                   [(0, 2000, "你好"), (3000, 5500, "啦啦啦"), (5600, 5800, "嗯"), (6500, 9000, "我们走吧")]]
    segs, embedded = new(), []
    marks = label_sentences(Dia(), lambda y: embedded.append(len(y)) or np.ones(3), segs, x, lambda y: bool(y.any()))
    assert [g["speaker"] for g in segs] == ["S1", "S?", "S?", "S1"] and [g.get("music") for g in segs] == [None, 1, None, None]
    assert marks == [(1,), None, None, (2,)] and len(embedded) == 2
    _, _, rows = final_rows(segs, {g["speaker"]: g["speaker"] for g in segs}, dt.datetime(2026, 10, 25, 9), 10.0, since=0.0, edge=0.0)
    assert [(r["text"], r.get("music")) for r in rows] == [("你好", None), ("啦啦啦", 1), ("嗯", None), ("我们走吧", None)], rows
    label_sentences(Dia(), lambda y: np.ones(3), segs := new(), x)  # no music callable (the drafts, MUSIC_DETECT=0): as before
    assert [g["speaker"] for g in segs] == ["S1", "S1", "S?", "S1"]


def test_music_column(tmp_path):
    """A store from before 10-25 gains sentences.music at the start; a music row reads back with music 1, the others
    without the key; drafts have no such column."""
    with sqlite3.connect(tmp_path / "old.db") as c:
        c.execute("CREATE TABLE sentences (id TEXT NOT NULL, start TEXT NOT NULL, end TEXT NOT NULL, speaker TEXT, speaker_confidence REAL,"
                  " text TEXT, source TEXT, engine TEXT, shown_at TEXT, dictation TEXT)")
        c.execute("INSERT INTO sentences (id, start, end, speaker, text) VALUES ('f1', '2026-10-24T10:00:00', '2026-10-24T10:00:02', '我', '旧的')")
    db.open_db(tmp_path / "old.db")
    db.init()
    assert [r[1] for r in db.conn().execute("PRAGMA table_info(sentences)")] == list(db.S_COLS)
    row = lambda i, s, **k: dict(id=i, start=f"2026-10-24T10:00:0{s}", end=f"2026-10-24T10:00:0{s + 1}", speaker="S?", text="啦", source="d", **k)
    with db.tx() as c:
        db.insert_sentences(c, [row("f2", 3, music=1), row("f3", 5)])
        db.insert_drafts(c, [row("d1", 7, music=1)])
    assert [r.get("music") for r in db.sentences("2026-10-24", "2026-10-25")] == [None, 1, None]
    assert "music" not in db.drafts("2026-10-24")[0]


def voice(sec: float, rng) -> np.ndarray:
    """Vowel-like syllables: a pulse train at 110-180 Hz through three formants, with pauses."""
    out, V = [], [(730, 1090, 2440), (270, 2290, 3010), (300, 870, 2240), (530, 1840, 2480)]
    while sum(map(len, out)) < sec * SR:
        for _ in range(rng.integers(3, 8)):
            n = int(rng.uniform(0.12, 0.28) * SR)
            f0 = rng.uniform(110, 180) * (1 + 0.15 * np.sin(np.linspace(0, np.pi, n)))
            y = lfilter([1], [1, -0.97], (np.diff(np.floor(np.cumsum(f0 / SR)), prepend=0) > 0) + 0.02 * rng.standard_normal(n))
            for f, bw in zip(V[rng.integers(len(V))], (80, 100, 120)):
                r = np.exp(-np.pi * bw / SR)
                y = lfilter([1 - r], [1, -2 * r * np.cos(2 * np.pi * f / SR), r * r], y)
            out.append(y * np.hanning(n) ** 0.5)
        out.append(np.zeros(int(rng.uniform(0.15, 0.5) * SR)))
    return np.concatenate(out)[:int(sec * SR)]


def band(sec: float) -> np.ndarray:
    """Chords (I-V-vi-IV), a bass line and a kick on each beat at 100 bpm."""
    t, beat = np.arange(int(sec * SR)) / SR, 0.6
    x = np.zeros_like(t)
    for k, root in enumerate([261.6, 392.0, 440.0, 349.2] * 3):
        m = (t >= k * 4 * beat) & (t < (k + 1) * 4 * beat)
        x[m] += sum(a * np.sin(2 * np.pi * f * h * t[m]) for f in (root, root * 1.26, root * 1.5) for h, a in ((1, 1), (2, 0.4), (3, 0.2)))
        x[m] += 1.5 * np.sin(2 * np.pi * root / 4 * t[m])
    k = np.arange(int(0.15 * SR))
    for b in np.arange(0, sec, beat):
        i = int(b * SR)
        x[i:i + len(k)] += (4 * np.sin(2 * np.pi * 60 * k / SR) * np.exp(-k / (0.04 * SR)))[:len(x) - i]
    return x


@pytest.mark.skipif(not (infer.DIR / infer.YAMNET).is_file(), reason="needs yamnet.onnx in MODELS_DIR")
def test_score():
    """A band, and a voice over it at the same level, are music; the voice alone and noise are not. Quiet or loud, the
    same (score() levels the audio first)."""
    rng = np.random.default_rng(0)
    v, b = voice(6, rng), band(6)
    unit = lambda y: y / np.sqrt(np.mean(y ** 2))
    pcm = lambda y, db: np.clip(unit(y) * 32768 * 10 ** (db / 20), -32768, 32767).astype(np.int16)
    for db_ in (-20, -60):
        assert music.heard(pcm(b, db_)) and music.heard(pcm(unit(v) + unit(b), db_))
        assert not music.heard(pcm(v, db_)) and not music.heard(pcm(rng.standard_normal(6 * SR), db_))


def test_enroll_skips_music(tmp_path, monkeypatch):
    """A rename or a one-sentence fix teaches a name from its rows: never from one heard as music."""
    import anyio
    from alldayrec import core, routes_speakers
    db.open_db(tmp_path / "t.db")
    cut = []

    async def fake_cut(a, b, fmt, device=None):
        cut.append(a)
        return np.zeros(SR, np.int16).tobytes()
    monkeypatch.setattr(routes_speakers.routes_audio, "cut", fake_cut)
    monkeypatch.setitem(core.state, "embed", lambda x: np.ones(4) / 2)
    row = lambda s, **k: dict(start=f"2026-10-25T10:00:0{s}", end=f"2026-10-25T10:00:0{s + 2}", **k)
    anyio.run(routes_speakers.enroll, "甲", [row(1, music=1), row(4)])
    assert cut == ["2026-10-25T10:00:04"] and db.people()["甲"][0] == 1
