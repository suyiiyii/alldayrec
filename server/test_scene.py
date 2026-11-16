"""sound.py and sounds.py: scene scores over all of a device's audio that decodes (quiet, standby, late, failed late spans alike), never
over time with no piece or a piece that will not decode; a second pass has nothing to do; minutes the same however a
stretch was cut; trim.py's gate; tenants apart; the service's /scene and its backoff of its own; off, as before.
A stand-in model (CI has no model files): its scores of a frame are of that frame's samples only."""
import datetime as dt
import logging
import subprocess
import sys
import threading
import time
import types

import anyio
import numpy as np
import pytest

from alldayrec import audio
from alldayrec import late
from alldayrec import compute
from alldayrec import db
from alldayrec import infer
from alldayrec import infer_server
from alldayrec import sound
from alldayrec import sounds
from alldayrec import tenant
from alldayrec import trim
from alldayrec.tenant import T
from test_backlog import DEVICE, SR, flac, save, tone
from test_compute import TOKEN, background, bound, serve
from test_dehum import opus
from test_trim import hour, vad

SINCE = "2026-01-01"


def fake(name: str, x: np.ndarray) -> np.ndarray:
    """Per frame, class i scores the frame's mean |sample| scaled by i: a frame's scores are its own samples'."""
    c = sound.MODELS[name]["classes"]
    level = [np.abs(x[a:a + n].astype(np.float64)).mean() / 32768 if a < len(x) else 0.0 for a, n in sound.windows(name, len(x))]
    return np.clip(np.outer(level, np.linspace(1, 0, c)) * 8, 0, 1).astype(np.float32)


@pytest.fixture
def on(tmp_path, monkeypatch):
    monkeypatch.setenv("SOUND_MODEL", "mn10_as,yamnet")
    monkeypatch.setenv("SOUND_SINCE", SINCE)
    monkeypatch.setattr(sound, "scene", fake)
    monkeypatch.setattr(sounds, "SOUND_SETTLE_S", 0)
    db.open_db(tmp_path / "live.db", tmp_path / "rec")
    T().sound = True


def ms(t: dt.datetime) -> int:
    return int(t.timestamp() * 1000)


def catch_up(models=("mn10_as", "yamnet"), lo=0, hi=None) -> int:
    n = 0
    for device, a, b in sounds.pending(list(models), lo, hi or int(time.time() * 1000) + 3600_000, int(time.time() * 1000)):
        sounds.one(device, list(models), a, b)
        n += 1
    return n


def rows(name: str) -> list:
    return [list(r) for r in db.conn().execute("SELECT start_ms, end_ms, n FROM sounds WHERE model = ? ORDER BY start_ms", (sound.model_id(name),))]


def test_scores_what_decoded_and_only_that(on, monkeypatch):
    """A day of 10 s pieces: 0-60 s, nothing 60-120 s, 120-150 s with 130-140 s that will not decode; a failed late span
    and a standby stretch over them. Read once for both models; rows over what decoded, none over the gap or the bad piece (left to try again,
    counted); a second pass reads nothing."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    for s in (*range(0, 60, 10), 120, 130, 140):
        save(t0 + dt.timedelta(seconds=s), flac(tone(s, 10)))
    db.conn().execute("UPDATE pieces SET size = 20 WHERE start_ms = ?", (ms(t0) + 130_000,))  # its bytes no longer decode
    db.conn().execute("INSERT INTO late_spans (device, start_ms, end_ms, first_ms, last_ms, status, ranges) VALUES (?, ?, ?, ?, ?, 'failed', '[]')",
                      (DEVICE, ms(t0) + 120_000, ms(t0) + 150_000, 0, 0))
    db.conn().execute("INSERT INTO processed_audio VALUES (?, ?, ?, 'standby')", (DEVICE, ms(t0) + 120_000, ms(t0) + 150_000))
    decodes = []
    window = audio.piece_window
    monkeypatch.setattr(audio, "piece_window", lambda p, *a: decodes.append(p["start_ms"]) or window(p, *a))
    reads, read = [], late.read_exact
    monkeypatch.setattr(late, "read_exact", lambda *a: reads.append(a[1:3]) or read(*a))
    assert catch_up() == len(reads) == 2, "one read per stretch for both models"
    for name in sound.names():
        got = [[a - ms(t0), b - ms(t0), n] for a, b, n in rows(name)]
        assert [g[:2] for g in got] == [[0, 60_000], [120_000, 130_000], [140_000, 150_000]], got
        assert [g[2] for g in got] == [len(sound.windows(name, (b - a) * SR // 1000)) for a, b, _ in got]
        assert T().scene_stats["failed", name] == 1 and T().scene_retry[DEVICE, name][0][:2] == [ms(t0) + 130_000, ms(t0) + 140_000]
    decodes.clear()
    assert catch_up() == 0 and not decodes, "nothing left but the bad piece, waiting"
    lv = np.frombuffer(db.conn().execute("SELECT db FROM sounds WHERE model = ? ORDER BY start_ms", (sound.model_id("mn10_as"),)).fetchone()[0], np.int8)
    assert len(lv) == 6 and abs(int(lv[0]) - round(20 * np.log10(1000 / 32768))) <= 1, "each frame's level as recorded"
    assert {g for (g,) in db.conn().execute("SELECT gain_db FROM sounds")} == {0}, "no gain applied yet"
    assert sounds.unscened(DEVICE, db.conn().execute("SELECT * FROM pieces WHERE start_ms != ?", (ms(t0) + 130_000,)).fetchall()) == 0


def test_growing_stretch_waits_for_whole_parts(on, monkeypatch):
    """Pieces still coming at a stretch's end: only its whole 300 s parts are read; once nothing more comes, the rest."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3600).replace(second=0, microsecond=0)
    for s in range(0, 360, 60):
        save(t0 + dt.timedelta(seconds=s), flac(tone(1, 60)))
    db.conn().execute("UPDATE pieces SET received_ms = ?", (int(time.time() * 1000),))
    now = int(time.time() * 1000)
    monkeypatch.setattr(sounds, "SOUND_SETTLE_S", 60)
    assert [(b - a) for _, a, b in sounds.pending(["mn10_as"], 0, now + 3600_000, now + 30_000)] == []
    db.conn().execute("UPDATE pieces SET received_ms = ? WHERE start_ms < ?", (now - 120_000, ms(t0) + 300_000))
    assert [(a - ms(t0), b - a) for _, a, b in sounds.pending(["mn10_as"], 0, now + 3600_000, now)] == [(0, 300_000)]
    db.conn().execute("UPDATE pieces SET received_ms = ?", (now - 120_000,))
    assert [(a - ms(t0), b - a) for _, a, b in sounds.pending(["mn10_as"], 0, now + 3600_000, now)] == [(0, 300_000), (300_000, 60_000)]


def test_rerun_replaces(on):
    t0 = dt.datetime(2026, 3, 1, 10)
    x = tone(3, 30)
    r = sounds.row("mn10_as", ms(t0), x, fake("mn10_as", x))
    with db.tx() as c:
        sounds.write(c, DEVICE, "mn10_as", r)
        sounds.write(c, DEVICE, "mn10_as", r)
    assert len(rows("mn10_as")) == 1


def test_minutes_same_however_cut(on):
    """A stretch over four minutes as one row, or cut in two: the same minutes (frames, means, hits)."""
    t0 = dt.datetime.fromtimestamp(time.time() - 86400).replace(second=0, microsecond=0) + dt.timedelta(seconds=40)
    x = np.concatenate([tone(i, 10) * (i % 3) for i in range(15)])  # 150 s, its level changing every 10 s
    save(t0, flac(x))
    catch_up(["mn10_as"])
    once = sounds.minutes(DEVICE, "mn10_as", 0, 2 ** 62)
    db.conn().execute("DELETE FROM sounds")
    sounds.one(DEVICE, ["mn10_as"], ms(t0), ms(t0) + 50_000)
    sounds.one(DEVICE, ["mn10_as"], ms(t0) + 50_000, ms(t0) + 150_000)
    cut = sounds.minutes(DEVICE, "mn10_as", 0, 2 ** 62)
    assert len(rows("mn10_as")) == 2 and list(once) == list(cut) == [ms(t0) // 60000 + k for k in range(4)]
    for m in once:
        assert once[m][0] == cut[m][0] and (once[m][2] == cut[m][2]).all() and np.allclose(once[m][1], cut[m][1], atol=1e-3)
    assert sum(f for f, _, _ in once.values()) == 15


def test_windows():
    assert sound.windows("mn10_as", SR) == [(0, SR)]
    assert sound.windows("mn10_as", 11 * SR) == [(0, 11 * SR)]  # a last window under 2 s joins the one before
    assert sound.windows("mn10_as", 12 * SR) == [(0, 10 * SR), (10 * SR, 2 * SR)]
    assert len(sound.windows("yamnet", 100)) == 1 and len(sound.windows("yamnet", 15600)) == 1 and len(sound.windows("yamnet", 15601)) == 2


@pytest.mark.parametrize("n", [100, 15600, 7680 * 125 + 100, 7680 * 125 + 7921, 7680 * 250 + 15599, 300 * SR])
def test_yamnet_calls_give_its_frames(monkeypatch, n):
    """YAMNet in calls of 60 s: as many frames as windows() says, each the one a single call gives (with yamnet.onnx:
    the real model; without, a stand-in that frames as YAMNet does)."""
    x = (np.random.default_rng(0).standard_normal(n) * 3000).astype(np.int16)
    if (infer.DIR / infer.YAMNET).is_file():
        whole = sound._session("yamnet").run(None, {"wav": x.astype(np.float32) / 32768})[0]
    else:
        class Framer:
            def run(self, _, feed):
                f = feed["wav"]
                return [np.stack([np.full(521, np.abs(f[a:a + 15600]).mean()) for a, _ in sound.windows("yamnet", len(f))])]
        sound._session.cache_clear()
        monkeypatch.setattr(sound, "_session", lambda name: Framer())
        whole = Framer().run(None, {"wav": x.astype(np.float32) / 32768})[0]
    got = sound.scores("yamnet", x)
    assert got.shape == whole.shape == (len(sound.windows("yamnet", n)), 521) and np.abs(got - whole).max() < 1e-5


def test_gate(tmp_path, monkeypatch, caplog):
    """An hour trim would cut down: left whole ("no sound") while what it drops has no rows of the gate's model, its
    history before SOUND_SINCE too, a tenant without "sound" too; trimmed once it has; --allow-unscened lets it go and says
    how much; SOUND_MODEL empty, as before."""
    t0, _ = hour(tmp_path, monkeypatch)
    v, now = vad(), int(time.time() * 1000)
    assert [h["reason"] for h in trim.plan(v, now)] == [None], "sound off: as before"
    monkeypatch.setenv("SOUND_MODEL", "mn10_as,yamnet")
    monkeypatch.setenv("SOUND_SINCE", (t0 + dt.timedelta(days=1)).date().isoformat())  # the hour is history
    monkeypatch.setattr(sound, "scene", fake)
    T().sound = True
    assert [h["reason"] for h in trim.plan(v, now)] == ["no sound"]
    T().sound = False  # hour() is trim's --db: a tenant of its own, no "sound"
    assert [h["reason"] for h in trim.plan(v, now)] == ["no sound"], "every tenant once SOUND_MODEL is set, its sound on or not"
    with caplog.at_level(logging.WARNING, "live"):
        assert [h["reason"] for h in trim.plan(v, now, allow_unscened=True)] == [None]
    dropped = 300 - 10 * len(trim.plan(v, now, allow_unscened=True)[0]["kept"])
    assert f"{dropped} s of audio to drop has no scene scores" in caplog.text
    sounds.backfill(["yamnet"], ms(t0), ms(t0) + 3600_000)
    assert [h["reason"] for h in trim.plan(v, now)] == ["no sound"], "the gate's model is the first"
    sounds.backfill(["mn10_as"], ms(t0), ms(t0) + 3600_000)
    hours = trim.plan(v, now)
    assert [h["reason"] for h in hours] == [None] and trim.apply_one(hours[0])


def test_tenants_apart(tmp_path, monkeypatch, on):
    """A's rows are A's: B (no "sound") reads none and runs no loop; tenants.json's sound must be a bool."""
    a = T()
    save(dt.datetime.fromtimestamp(time.time() - 3600), flac(tone(2, 20)))
    catch_up(["mn10_as"])
    assert rows("mn10_as") and sounds.on()
    b = db.open_db(tmp_path / "b" / "live.db", tmp_path / "b" / "rec")
    assert not rows("mn10_as") and not sounds.minutes(DEVICE, "mn10_as", 0, 2 ** 62) and not sounds.on() and b is not a
    f = tmp_path / "tenants.json"
    f.write_text('[{"id": "a", "token": "x", "data": "/d/a", "recordings": "/r/a", "sound": true},'
                 ' {"id": "b", "token": "y", "data": "/d/b", "recordings": "/r/b"}]')
    f.chmod(0o600)
    assert [t.sound for t in tenant.load(f)] == [True, False]
    f.write_text('[{"id": "a", "token": "", "data": "/d/a", "recordings": "/r/a", "sound": "yes"}]')
    with pytest.raises(SystemExit, match="sound must be true or false"):
        tenant.load(f)


def test_settings(monkeypatch):
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    monkeypatch.delenv("SOUND_SINCE", raising=False)
    with pytest.raises(SystemExit, match="needs SOUND_SINCE"):
        sounds.check()
    monkeypatch.setenv("SOUND_MODEL", "mn10_as,panns")
    with pytest.raises(SystemExit, match="unknown panns"):
        sounds.check()
    monkeypatch.delenv("SOUND_MODEL")
    sounds.check()


def test_model_files_only_when_named(tmp_path, monkeypatch):
    """SOUND_MODEL not naming mn10_as: its file is neither downloaded nor looked for (an empty MODELS_DIR, CI)."""
    got = []
    monkeypatch.setattr(infer, "download", lambda url, dest, sha: got.append(dest.name))
    monkeypatch.delenv("SOUND_MODEL", raising=False)
    infer.fetch(tmp_path)
    with pytest.raises(SystemExit) as e:
        infer.check(tmp_path)
    assert infer.MN10 not in got and infer.MN10 not in str(e.value) and infer.YAMNET in got
    monkeypatch.setenv("SOUND_MODEL", "yamnet,mn10_as")
    got.clear()
    infer.fetch(tmp_path)
    with pytest.raises(SystemExit, match="mn10_as.onnx is missing"):
        infer.check(tmp_path)
    assert infer.MN10 in got


@pytest.fixture
def scene_service(monkeypatch):
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    monkeypatch.setitem(infer_server.state, "compute", types.SimpleNamespace(scene=fake, embed=lambda x: np.ones(compute.DIM, np.float32) / np.sqrt(compute.DIM)))
    monkeypatch.setitem(infer_server.state, "token", TOKEN)
    monkeypatch.setattr(compute, "BACKOFF_S", (5.0, 5.0))
    sock = bound()
    server = serve(sock)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True


def test_remote_scene(scene_service, monkeypatch):
    """/scene/<model>: the scores this process would make (float16); 413 over the limit, not tried again; a model the
    service does not run fails only the scene Remote: a voiceprint at once meanwhile."""
    x = tone(5, 12)
    r = compute.Remote(scene_service, TOKEN, 5.0, scene=sound.files())
    assert np.allclose(r.scene("mn10_as", x), fake("mn10_as", x), atol=1e-3)
    with pytest.raises(Exception, match="413"):
        r.scene("mn10_as", np.zeros((infer_server.MAX_S + 1) * SR, np.int16))
    monkeypatch.setenv("SOUND_MODEL", "yamnet")  # the service now runs another one
    with pytest.raises(ValueError, match="other scene models"):
        r.same_models()
    r.failed("scene/mn10_as", ValueError("down"))
    assert r.next_try > time.monotonic() + 3, "the scene Remote waits"
    t = time.monotonic()
    assert compute.Remote(scene_service, TOKEN, 5.0).embed(x).shape == (compute.DIM,) and time.monotonic() - t < 2


def test_gate_strict(tmp_path, monkeypatch):
    """The gate holds for any audio to drop the model has no scores of, however short: 0.5 s of each piece is."""
    t0, _ = hour(tmp_path, monkeypatch)
    v, now = vad(), int(time.time() * 1000)
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    monkeypatch.setenv("SOUND_SINCE", (t0 + dt.timedelta(days=1)).date().isoformat())
    h = trim.plan(v, now, allow_unscened=True)[0]
    dropped = [r for r in h["rows"] if r["start_ms"] not in {k["start_ms"] for k in h["kept"]}]
    with db.tx() as c:
        for r in dropped:
            x = tone(1, r["seconds"] - 0.5)
            sounds.write(c, DEVICE, "mn10_as", sounds.row("mn10_as", r["start_ms"], x, fake("mn10_as", x)))
    assert sounds.unscened(DEVICE, dropped) == 0.5 * len(dropped)
    assert [h["reason"] for h in trim.plan(v, now)] == ["no sound"]


def pieces(t0, *spans):
    """Pieces of tone at (start s, seconds) after t0."""
    for s, n in spans:
        save(t0 + dt.timedelta(seconds=s), flac(tone(int(s), n)))


def test_bad_piece_is_no_coverage(on):
    """A short piece that will not decode between two that do: no row over it (not even bridged), it gates; once it
    decodes, its own row, and nothing of the pieces is left."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    pieces(t0, (0, 1), (1, 0.4), (1.4, 1))
    size = db.conn().execute("SELECT size FROM pieces WHERE start_ms = ?", (ms(t0) + 1000,)).fetchone()[0]
    db.conn().execute("UPDATE pieces SET size = 20 WHERE start_ms = ?", (ms(t0) + 1000,))
    catch_up(["mn10_as"])
    assert [[a - ms(t0), b - ms(t0)] for a, b, _ in rows("mn10_as")] == [[0, 1000], [1400, 2400]]
    assert sounds.covered(DEVICE, "mn10_as", ms(t0) + 1000, ms(t0) + 1400) == []
    every = db.conn().execute("SELECT * FROM pieces").fetchall()
    assert sounds.unscened(DEVICE, every) == 0.4
    db.conn().execute("UPDATE pieces SET size = ? WHERE start_ms = ?", (size, ms(t0) + 1000))
    T().scene_retry.clear()
    catch_up(["mn10_as"])
    assert [[a - ms(t0), b - ms(t0)] for a, b, _ in rows("mn10_as")] == [[0, 1000], [1000, 1400], [1400, 2400]]
    assert sounds.unscened(DEVICE, every) == 0


def test_late_audio_into_a_gap(on):
    """Two pieces 0.3 s apart are one row (the gap zeros to the model), the gap no coverage; a piece that comes into it
    later gets its own row, the first one kept."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    pieces(t0, (0, 1), (1.3, 1))
    catch_up(["mn10_as"])
    assert [[a - ms(t0), b - ms(t0)] for a, b, _ in rows("mn10_as")] == [[0, 2300]]
    pieces(t0, (1, 0.3))
    every = db.conn().execute("SELECT * FROM pieces").fetchall()
    assert sounds.unscened(DEVICE, every) == 0.3, "the gate holds for it: a row's gaps are no coverage"
    assert catch_up(["mn10_as"]) == 1, "the late piece is read"
    assert [[a - ms(t0), b - ms(t0)] for a, b, _ in rows("mn10_as")] == [[0, 2300], [1000, 1300]]
    assert sounds.unscened(DEVICE, db.conn().execute("SELECT * FROM pieces").fetchall()) == 0
    assert catch_up(["mn10_as"]) == 0


def test_short_piece_alone(on):
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    pieces(t0, (0, 0.3))
    assert catch_up(["mn10_as"]) == 1 and [[a - ms(t0), b - ms(t0)] for a, b, _ in rows("mn10_as")] == [[0, 300]]


def test_piece_short_of_its_seconds(on):
    """A piece that decodes 0.3 s short of its seconds: that 0.3 s is a hole, it gates, and it waits out a backoff
    instead of being read again each pass."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    pieces(t0, (0, 10))
    db.conn().execute("UPDATE pieces SET seconds = 10.3")
    assert catch_up(["mn10_as"]) == 1 and T().scene_stats["failed", "mn10_as"] == 1
    assert sounds.unscened(DEVICE, db.conn().execute("SELECT * FROM pieces").fetchall()) == 0.3
    assert catch_up(["mn10_as"]) == 0


@pytest.mark.parametrize("n", [16008, 15992, 16000])
def test_odd_lengths_leave_nothing(on, n):
    """Pieces whose samples are not whole ms, at even and odd ms, back to back: nothing left over, no crumb read."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    at = 0
    for k in range(4):
        save(t0 + dt.timedelta(milliseconds=at + k), flac(tone(k, n / SR)))
        at += round(n / 16)
    catch_up(["mn10_as"])
    assert sounds.unscened(DEVICE, db.conn().execute("SELECT * FROM pieces").fetchall()) == 0
    assert catch_up(["mn10_as"]) == 0


@pytest.mark.parametrize("odd", [0, 1])
def test_seams_at_odd_ms_leave_nothing(on, odd):
    """Pieces of 16008 samples (1000.5 ms) every 1000 ms from an even or an odd ms, each starting inside the one
    before: nothing left over, no crumb at the seams."""
    at = ms(dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)) + odd
    for k in range(3):
        assert audio.save(DEVICE, at + 1000 * k, flac(tone(k, 2)[:16008]), "audio/flac")
    catch_up(["mn10_as"])
    assert sounds.unscened(DEVICE, db.conn().execute("SELECT * FROM pieces").fetchall()) == 0
    assert catch_up(["mn10_as"]) == 0


def test_long_aac_leaves_nothing(on, monkeypatch):
    """A long AAC (piece_window reads it to its seconds only) of n samples, not whole ms: nothing left over."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    body = subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0", "-c:a", "aac",
                           "-f", "adts", "pipe:1"], input=tone(1, 5.2).tobytes(), capture_output=True, check=True).stdout
    assert audio.save(DEVICE, ms(t0), body, "audio/aac", n=5 * SR + 8)
    monkeypatch.setattr(audio, "WHOLE_S", 1)  # read as a long import is
    catch_up(["mn10_as"])
    assert sounds.unscened(DEVICE, db.conn().execute("SELECT * FROM pieces").fetchall()) == 0 and not T().scene_stats["failed", "mn10_as"]
    assert catch_up(["mn10_as"]) == 0


def test_long_aac_one_sample_short_is_a_hole(on):
    """A real long AAC (over WHOLE_S: piece_window reads it) of n samples, 1/16 ms past its seconds; its last frame
    then zeroed on disk (same size, same row): it decodes one sample short, and its last ms is left a hole. Whole
    before that, and the same audio with or without filled."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    body = subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0", "-c:a", "aac",
                           "-f", "adts", "pipe:1"], input=np.tile(tone(1, 6), 51).tobytes(), capture_output=True, check=True).stdout
    last = 0
    while (size := (body[last + 3] & 3) << 11 | body[last + 4] << 3 | body[last + 5] >> 5) < len(body) - last:
        last += size  # the last ADTS frame's offset
    assert audio.save(DEVICE, ms(t0), body, "audio/aac", n=len(audio.decode(body[:last], aac=True, n=10 ** 9)) + 1)
    p = db.conn().execute("SELECT * FROM pieces").fetchone()
    assert p["seconds"] > audio.WHOLE_S and p["samples"] % 16 == 1
    tail = ms(t0) + 300_000, ms(t0) + 307_000
    filled = []
    np.testing.assert_array_equal(late.read_exact(DEVICE, *tail, [], filled), late.read_exact(DEVICE, *tail, []))  # same audio
    assert filled == [[tail[0], round(ms(t0) + p["seconds"] * 1000)]], "whole while it decodes to its samples"
    f = T().recordings / p["path"]
    data = f.read_bytes()
    f.write_bytes(data[:p["pos"] + last] + bytes(len(body) - last) + data[p["pos"] + len(body):])
    assert len(audio.samples(p)) == p["samples"] - 1
    catch_up(["mn10_as"])
    assert sounds.unscened(DEVICE, [p]) == 0.001


@pytest.mark.parametrize("codec", ["opus", "aac"])
def test_recorder_codecs_leave_nothing(on, codec):
    """A recorder's Opus pieces (slices of one encoder, primed by the piece before) and a phone's AAC ones (n samples,
    not whole ms), back to back: one row, nothing left over, a second pass reads nothing."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    if codec == "opus":
        rec = opus(tone(1, 6), "-application", "lowdelay", "-frame_duration", "10", "-b:a", "32k")
        for i in range(6):
            assert audio.save(DEVICE, ms(t0) + i * 1000, audio.ogg(rec[i * 100:(i + 1) * 100]), "audio/ogg")
    else:
        at = ms(t0)
        for i in range(3):
            body = subprocess.run(["ffmpeg", "-v", "error", "-f", "s16le", "-ar", str(SR), "-ac", "1", "-i", "pipe:0", "-c:a", "aac",
                                   "-f", "adts", "pipe:1"], input=tone(i, 5.2).tobytes(), capture_output=True, check=True).stdout
            at += round(1000 * audio.save(DEVICE, at, body, "audio/aac", n=5 * SR + 8))
    catch_up(["mn10_as"])
    every = db.conn().execute("SELECT * FROM pieces").fetchall()
    assert sounds.unscened(DEVICE, every) == 0 and len(rows("mn10_as")) == 1 and not T().scene_stats["failed", "mn10_as"]
    assert catch_up(["mn10_as"]) == 0


def test_remote_scene_checks_the_service_first(scene_service, monkeypatch, caplog):
    """sound.scene over INFER_URL: the service's scene sha256 checked before a first score is taken; another one, no
    score taken, a clear error, the call waits (until the stop). A model the Remote was not made for: refused."""
    monkeypatch.setenv("INFER_URL", scene_service)
    monkeypatch.setenv("INFER_TOKEN", TOKEN)
    sound._remote.cache_clear()
    monkeypatch.setattr(infer_server, "sound", types.SimpleNamespace(names=sound.names, encode=sound.encode,
                                                                     files=lambda: {infer.MN10: "0" * 64}))
    monkeypatch.setattr(compute, "STOP", threading.Event())
    ok = infer_server.CALLS["scene/mn10_as", "ok"]
    with caplog.at_level(logging.ERROR):
        got = background(sound.scene, "mn10_as", tone(5, 12))
        time.sleep(1)
        assert "out" not in got and infer_server.CALLS["scene/mn10_as", "ok"] == ok
        assert "other scene models" in caplog.text
        compute.STOP.set()
        got["thread"].join(5)
    assert isinstance(got.get("error"), compute.Stopping), got
    sound._remote.cache_clear()
    with pytest.raises(ValueError, match="not checked"):
        compute.Remote(scene_service, TOKEN, 5.0, scene={}).scene("mn10_as", tone(5, 12))


def backfill_cli(monkeypatch, db_path, *args):
    monkeypatch.setattr(sys, "argv", ["sounds", "backfill", "--db", str(db_path), "--since", SINCE, *args])
    sounds.main()


def test_backfill_checks_its_models(tmp_path, monkeypatch, scene_service):
    """backfill checks the model files it runs (--model's, not only SOUND_MODEL's) or the service's, first: another
    one (or none) stops it, nothing written."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    db.open_db(tmp_path / "live.db", tmp_path / "rec")
    pieces(t0, (0, 5))
    monkeypatch.setattr(sounds, "SOUND_SETTLE_S", 0)
    d = tmp_path / "models"
    d.mkdir()
    (d / infer.YAMNET).write_bytes(b"not the pinned one")
    monkeypatch.setattr(infer, "DIR", d)
    got = []
    monkeypatch.setattr(infer, "download", lambda url, dest, sha: got.append(dest.name))
    monkeypatch.delenv("INFER_URL", raising=False)
    monkeypatch.setenv("SOUND_MODEL", "")
    with pytest.raises(SystemExit, match="has another sha256"):
        backfill_cli(monkeypatch, tmp_path / "live.db", "--model", "yamnet")
    with pytest.raises(SystemExit, match="mn10_as.onnx is missing"):
        backfill_cli(monkeypatch, tmp_path / "live.db", "--model", "mn10_as")
    assert got == [infer.MN10], "--model's file fetched, only it"
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    monkeypatch.setenv("INFER_URL", scene_service)
    monkeypatch.setenv("INFER_TOKEN", TOKEN)
    sound._remote.cache_clear()
    monkeypatch.setattr(infer_server, "sound", types.SimpleNamespace(names=sound.names, encode=sound.encode,
                                                                     files=lambda: {infer.MN10: "0" * 64}))
    with pytest.raises(SystemExit, match="other scene models"):
        backfill_cli(monkeypatch, tmp_path / "live.db")
    sound._remote.cache_clear()
    db.open_db(tmp_path / "live.db", tmp_path / "rec")
    assert not db.conn().execute("SELECT count(*) FROM sounds").fetchone()[0]


def test_scan_in_batches(on, monkeypatch):
    """The scan reads a device's pieces a batch at a time (BATCH_MS from a piece on), the empty days between skipped:
    its memory is a batch's, not the history's; the same rows as one pass."""
    t0 = dt.datetime.fromtimestamp(time.time() - 5 * 86400).replace(second=0, microsecond=0)
    pieces(t0, *[(30 * k, 30) for k in range(6)], *[(2 * 86400 + 30 * k, 30) for k in range(6)])
    monkeypatch.setattr(sounds, "BATCH_MS", 60_000)
    seen, todo = [], sounds.todo
    monkeypatch.setattr(sounds, "todo", lambda models, device, lo, hi, now: seen.append(r := todo(models, device, lo, hi, now)) or r)
    catch_up(["mn10_as"])
    assert len(seen) <= 8 and all(b - a <= 60_000 for parts, _ in seen for a, b in parts), seen
    assert sounds.unscened(DEVICE, db.conn().execute("SELECT * FROM pieces").fetchall()) == 0


def uploaded(t0, decoded: int, n: int):
    """A piece at t0 that decodes `decoded` samples now and decoded n at its upload (its seconds and samples those)."""
    save(t0, flac(tone(1, 2)[:decoded]))
    db.conn().execute("UPDATE pieces SET seconds = ?, samples = ? WHERE start_ms = ?", (round(n / SR, 3), n, ms(t0)))


@pytest.mark.parametrize("decoded, n", [(15988, 16000), (15992, 16008), (16000, 16008)])
def test_short_tail_is_a_hole(on, decoded, n):
    """A piece that decodes short of its samples at upload: by 0.75 ms, by 1 ms its seconds (to 1 ms) cannot tell, by
    0.5 ms past them: a hole of its last ms all the same, it gates and waits out a backoff; no tolerance."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    uploaded(t0, decoded, n)
    assert catch_up(["mn10_as"]) == 1 and T().scene_stats["failed", "mn10_as"] == 1
    assert sounds.unscened(DEVICE, db.conn().execute("SELECT * FROM pieces").fetchall()) == 0.001
    assert catch_up(["mn10_as"]) == 0


def test_read_exact_as_before_without_filled(on):
    """Without filled (sound off, and every late reader), read_exact gives what it gave before scene coverage: the same
    audio and the same bad, field for field, for a piece that decodes 0.75 ms short with one that will not decode
    starting inside its last ms (main's: bad from 999 ms; filled given, the same)."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    uploaded(t0, 15988, 16000)
    assert audio.save(DEVICE, ms(t0) + 999, flac(tone(2, 1)), "audio/flac")
    db.conn().execute("UPDATE pieces SET size = 20 WHERE start_ms = ?", (ms(t0) + 999,))
    bad, bad2, filled = [], [], []
    x = late.read_exact(DEVICE, ms(t0), ms(t0) + 2000, bad)
    assert bad == [[ms(t0) + 999, ms(t0) + 1999]]
    assert x.dtype == np.int16 and len(x) == 32000 and np.array_equal(x[:15988], tone(1, 2)[:15988]) and not x[15988:].any()
    assert np.array_equal(late.read_exact(DEVICE, ms(t0), ms(t0) + 2000, bad2, filled), x) and bad2 == bad
    assert filled == [[ms(t0), ms(t0) + 999]]


def test_samples_column_on_an_old_store(tmp_path):
    """A store from before pieces.samples: init adds it (NULL, every other field as it was), again and again; new
    pieces get theirs."""
    db.open_db(tmp_path / "live.db", tmp_path / "rec")
    save(dt.datetime(2026, 1, 2), flac(tone(1, 1)))
    db.conn().execute("ALTER TABLE pieces DROP COLUMN samples")
    before = [tuple(r) for r in db.conn().execute("SELECT * FROM pieces")]
    db.init()
    db.init()
    after = db.conn().execute("SELECT * FROM pieces").fetchall()
    assert [tuple(r)[:-1] for r in after] == before and after[0].keys()[-1] == "samples" and after[0]["samples"] is None
    save(dt.datetime(2026, 1, 2, 0, 0, 1), flac(tone(1, 2)[:16003]))
    assert [tuple(r) for r in db.conn().execute("SELECT samples FROM pieces WHERE samples IS NOT NULL")] == [(16003,)]


class Passes(Exception):
    pass


def passes(monkeypatch, n: int):
    """n passes of sounds.loop(), every one a full one."""
    monkeypatch.setattr(sounds, "FULL_S", 0)
    left = [n]

    async def sleep(_):
        left[0] -= 1
        if not left[0]:
            raise Passes

    monkeypatch.setattr(sounds.anyio, "sleep", sleep)
    with pytest.raises(Passes):
        anyio.run(sounds.loop)


def test_backfill_from_inside_a_piece(on, monkeypatch, tmp_path):
    """--since inside a piece, none starting after it: backfill scores the piece from --since on."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    pieces(t0, (0, 60))
    monkeypatch.setattr(infer, "fetch", lambda *a: None)
    monkeypatch.setattr(infer, "check", lambda *a: None)
    monkeypatch.delenv("INFER_URL", raising=False)
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    backfill_cli(monkeypatch, tmp_path / "live.db", "--recordings", str(tmp_path / "rec"),
                 "--since", (t0 + dt.timedelta(seconds=30)).isoformat(), "--model", "mn10_as")
    assert [[a - ms(t0), b - ms(t0)] for a, b, _ in rows("mn10_as")] == [[30000, 60000]]


def test_loop_from_inside_a_piece(on, monkeypatch):
    """SOUND_SINCE inside a piece, none starting after it: the loop scores the piece from SOUND_SINCE on."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    pieces(t0, (0, 60))
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")
    monkeypatch.setenv("SOUND_SINCE", (t0 + dt.timedelta(seconds=30)).isoformat())
    passes(monkeypatch, 1)
    assert [[a - ms(t0), b - ms(t0)] for a, b, _ in rows("mn10_as")] == [[30000, 60000]]


def test_backlog_after_each_pass(on, monkeypatch):
    """lr_scene_backlog_seconds: what a pass left, failed or waiting out a backoff alike; 0 once it is done."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3 * 86400).replace(second=0, microsecond=0)
    pieces(t0, (0, 60))
    monkeypatch.setenv("SOUND_MODEL", "mn10_as")

    def broken(name, x):
        raise RuntimeError("the model failed")

    monkeypatch.setattr(sound, "scene", broken)
    passes(monkeypatch, 1)
    assert T().scene_stats["backlog", ""] == 60, "failed"
    passes(monkeypatch, 1)
    assert T().scene_stats["backlog", ""] == 60 and T().scene_stats["failed", "mn10_as"] == 1, "waiting, not tried"
    monkeypatch.setattr(sound, "scene", fake)
    T().scene_retry.clear()
    passes(monkeypatch, 1)
    assert rows("mn10_as") and T().scene_stats["backlog", ""] == 0, "done"
