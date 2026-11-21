"""quality.py: P.808 windows of a device's final sentences, an hour at a time once the final layer is past it (never
again), the clipped share as recorded; a day's report with media played aloud apart; GET /api/quality; the model file
only with QUALITY=1. A stand-in model (CI has no model file), but test_model_range."""
import datetime as dt

import anyio
import numpy as np
import pytest
from fastapi import HTTPException

from alldayrec import audio, db, infer, quality, routes_upload
from test_backlog import flac

SR, DEV, DAY = 16000, "sample-recorder", "2026-11-20"


def ms(t: str) -> int:
    return round(dt.datetime.fromisoformat(f"{DAY}T{t}").timestamp() * 1000)


def square(a: int, s: float) -> np.ndarray:
    return np.where(np.arange(round(s * SR)) % 32 < 16, a, -a).astype(np.int16)


@pytest.fixture
def store(tmp_path, monkeypatch):
    """10:00:00-10:01:00: a piece, 30 s at amplitude 1000 then 30 s at full scale; sentences 10:00:00-10:00:20 and
    10:00:30-10:00:50, and one at 11:00:05 after the final layer's end (11:00:00.5). The stand-in scores a loud window 4,
    any other 2."""
    db.open_db(tmp_path / "live.db", tmp_path / "rec")
    monkeypatch.setenv("QUALITY_SINCE", DAY)
    monkeypatch.setattr(quality, "p808", lambda w: 4.0 if np.abs(w).mean() > 10000 else 2.0)
    audio.save(DEV, ms("10:00:00"), flac(np.concatenate([square(1000, 30), square(32767, 30)])), "audio/flac")
    with db.tx() as c:
        db.insert_sentences(c, [dict(id=f"s{i}", start=f"{DAY}T{a}.000", end=f"{DAY}T{b}.000", speaker="S1", text="x", source=DEV)
                                for i, (a, b) in enumerate([("10:00:00", "10:00:20"), ("10:00:30", "10:00:50"), ("11:00:05", "11:00:10")])])
    db.put_kv(f"finalized_to:{DEV}", f"{DAY}T11:00:00.500000")


def drain() -> int:
    n = 0
    while hours := quality.todo():
        n += sum(quality.score(d, a) for d, a in hours)
    return n


def rows() -> list:
    return db.conn().execute("SELECT start_ms, end_ms, speech_ms, p808, clip FROM quality ORDER BY start_ms").fetchall()


def test_self_check():
    quality.check()


def test_windows_of_an_hour(store):
    assert drain() == 5  # 40 s of speech: four of 9.01 s, the last 3.96 s repeated
    assert drain() == 0 and db.kv(f"quality_to:{DEV}") == ms("11:00:00")  # done once; 11:00 waits for the final layer
    r = rows()
    assert [x[2] for x in r] == [9010, 9010, 9010, 9010, 3960]
    assert r[0][0] == ms("10:00:00") and r[2][0] == ms("10:00:18.020") and abs(r[2][1] - ms("10:00:37.030")) <= 1
    assert [x[3] for x in r] == [2.0, 2.0, 4.0, 4.0, 4.0]
    assert r[0][4] == 0 and abs(r[2][4] - 7.03 / 9.01) < 1e-3 and r[3][4] == r[4][4] == 1.0  # the full-scale half, as recorded
    db.put_kv(f"finalized_to:{DEV}", f"{DAY}T12:00:00.000000")
    assert drain() == 0 and db.kv(f"quality_to:{DEV}") == ms("12:00:00")  # 11:00:05 has no piece: no window of zeros


def test_report_media_apart(store):
    drain()
    with db.tx() as c:
        db.insert_conversation(c, dict(id="c1", start=f"{DAY}T10:00:00.000", end=f"{DAY}T10:00:50.000", device=DEV,
                                       participants=["S1"], sentences=2, speech_s=40))
        db.put_summary({"conversation": "c1", "status": "done", "media_spans": [{"from": f"{DAY}T10:00:30.000", "to": f"{DAY}T10:00:30.000"}]}, c)
    r = quality.report(DEV, dt.date.fromisoformat(DAY))
    assert r["all"] == {"windows": 5, "speech_min": 0.7, "p808": 4.0, "low": 0.451, "clip_min": 0.4}
    assert r["live"] == {"windows": 2, "speech_min": 0.3, "p808": 2.0, "low": 1.0, "clip_min": 0.0}
    assert r["media"]["windows"] == 3 and r["media"]["low"] == 0.0  # the window over the join is mostly the second sentence
    assert quality.report(DEV, dt.date.fromisoformat("2026-11-19")) is None


def test_api(store):
    drain()
    got = anyio.run(routes_upload.quality_days, DEV, "2026-11-18", DAY)
    assert got["low"] == quality.LOW and [d["day"] for d in got["days"]] == [DAY] and got["days"][0]["media"] is None
    for a, b in (("2026-11-21", DAY), ("2026-10-01", DAY), ("x", DAY)):
        with pytest.raises(HTTPException):
            anyio.run(routes_upload.quality_days, DEV, a, b)


def test_model_file_only_with_quality(monkeypatch):
    monkeypatch.delenv("QUALITY", raising=False)
    assert infer.DNSMOS in infer.unused()
    monkeypatch.setenv("QUALITY", "1")
    assert infer.DNSMOS not in infer.unused()


@pytest.mark.skipif(not (infer.DIR / infer.DNSMOS).is_file(), reason="needs dnsmos-p808.onnx in MODELS_DIR")
def test_model_range():
    """The real model on 9.01 s of synthetic sound: a MOS in 1-5, the same at any level."""
    t = np.arange(quality.WIN) / SR
    voice = sum(np.sin(2 * np.pi * f * t) / k for k, f in enumerate((140, 280, 420, 560), 1)) * (1 + np.sin(2 * np.pi * 4 * t))
    x = voice * 3000 + np.random.default_rng(0).standard_normal(quality.WIN) * 300
    p = quality.p808(x)
    assert 1 <= p <= 5 and abs(quality.p808(x / 10) - p) < 1e-3, p
