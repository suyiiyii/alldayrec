"""Late reads share import decoding bounds and retain failed audio for retry."""
import subprocess

import anyio
import numpy as np
import pytest

from alldayrec import audio, db, infer, late, late_backfill, late_store
from alldayrec.tenant import T
from test_backlog import EnergyVad
from test_import_memory import client, wav_file
from test_late_audio import D, iso, ms, store  # noqa: F401


def upload_late(store, seconds):
    T().read_to[D] = ms(seconds + 100)
    db.put_kv(f"finalized_to:{D}", iso(seconds + 100))
    path = store / "late.wav"
    one_second = wav_file(path, seconds)
    with path.open("rb") as f:
        response = client(T()).post(
            f"/api/chunks?device={D}&start={ms(0)}",
            content=iter(lambda: f.read(65536), b""),
            headers={"content-type": "audio/wav"})
    assert response.status_code == 200 and response.json()["seconds"] == seconds
    late_store.release(D)
    assert db.conn().execute("SELECT count(*) FROM late_spans").fetchone()[0] == 1
    return one_second


@pytest.mark.parametrize("windows", [1, 20])
def test_long_late_import_never_uses_whole_file_decode(store, monkeypatch, windows):
    one_second = upload_late(store, 601)
    monkeypatch.setattr(audio, "decode", lambda *a, **kw: pytest.fail("whole long file decoded"))
    reads = []
    piece_window = audio.piece_window
    def window(p, offset, count):
        reads.append((offset, count))
        return piece_window(p, offset, count)
    monkeypatch.setattr(audio, "piece_window", window)
    for a in range(0, windows * 30, 30):
        x = late.read_exact(D, ms(a), ms(a + 30))
        np.testing.assert_array_equal(x, np.tile(one_second, 30))
    assert reads == [(a * audio.SR, 30 * audio.SR) for a in range(0, windows * 30, 30)]


def test_long_backfill_preview_never_uses_whole_file_decode(store, monkeypatch):
    upload_late(store, 601)
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    monkeypatch.setattr(audio, "decode", lambda *a, **kw: pytest.fail("whole long file decoded"))
    monkeypatch.setattr(late_backfill, "candidates", lambda days: ({D: [[[ms(0), ms(601)]]]}, {}))
    before = list(db.conn().iterdump())
    result = anyio.run(late_backfill.scan, 1, True)
    assert result["dry_run"] and result["enqueued"] == 0
    assert result["days"][0]["minutes"] == 10.02
    assert result["days"][0]["speech_minutes"] > 0
    assert list(db.conn().iterdump()) == before


def test_late_window_preserves_overlaps_holes_and_short_piece_tail(store, monkeypatch):
    one_second = upload_late(store, 601)
    for start, seconds, value in [(599.5, 1, 111), (600, 3, 222), (604, 1, 333)]:
        audio.save(D, ms(start), audio.wav(np.full(seconds * audio.SR, value, np.int16)), "audio/wav")
    samples = audio.samples
    decoded = []
    def short_samples(p):
        assert p["seconds"] <= audio.WHOLE_S
        decoded.append(p["start_ms"])
        return samples(p)
    monkeypatch.setattr(audio, "samples", short_samples)
    x = late.read_exact(D, ms(599), ms(606))
    expected = np.concatenate([np.tile(one_second, 2), np.full(2 * audio.SR, 222, np.int16),
                               np.zeros(audio.SR, np.int16), np.full(audio.SR, 333, np.int16),
                               np.zeros(audio.SR, np.int16)])
    np.testing.assert_array_equal(x, expected)
    assert decoded == [ms(600), ms(604)], "fully covered pieces must not be decoded"


@pytest.mark.parametrize("seconds", [2, 601])
@pytest.mark.parametrize("failure", ["none", "timeout"])
def test_late_decode_failure_keeps_audio_until_retry_limit(store, monkeypatch, seconds, failure):
    upload_late(store, seconds)
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    async def no_model(*args, **kwargs):
        pytest.fail("decode failure must stop before ASR")
    monkeypatch.setattr(late.asr, "engine_segs", no_model)
    def fail(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("ffmpeg", 1)
        return None if seconds <= audio.WHOLE_S else 1
    monkeypatch.setattr(audio, "decode" if seconds <= audio.WHOLE_S else "decode_process", fail)
    async def stop_when_idle(seconds):
        raise RuntimeError("test idle")
    monkeypatch.setattr(late.anyio, "sleep", stop_when_idle)
    # Exercise the real processor's exception handler and durable backoff first.
    with pytest.raises(RuntimeError, match="test idle"):
        anyio.run(late.processor)
    error = ValueError if failure == "none" else subprocess.TimeoutExpired
    for attempt in range(1, 4):
        saved = dict(db.conn().execute("SELECT * FROM late_spans").fetchone())
        assert saved["tries"] == attempt
        assert saved["status"] == ("failed" if attempt == 3 else "waiting")
        assert saved["note"] == error.__name__ and saved["done_ms"] is None
        assert not db.processed(D, ms(0), ms(seconds))
        assert not db.sentences("", "z")
        assert not T().dias
        assert late_store.claim(saved["next_ms"] - 1) is None
        claimed = late_store.claim(saved["next_ms"])
        if attempt == 3:
            assert claimed is None
        else:
            with pytest.raises(error) as exc:
                anyio.run(late.process, claimed)
            late_store.failed(claimed, exc.value, saved["next_ms"])
