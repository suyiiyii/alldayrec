"""sound.py and sounds.py: scene scores over all of a device's audio that decodes (quiet, standby, late, failed late spans alike), never
over time with no piece or a piece that will not decode; a second pass has nothing to do; minutes the same however a
stretch was cut; trim.py's gate; tenants apart; the service's /scene and its backoff of its own; off, as before.
A stand-in model (CI has no model files): its scores of a frame are of that frame's samples only."""
import datetime as dt
import logging
import time
import types

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
from test_compute import TOKEN, bound, serve
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
    parts = sounds.todo(list(models), lo, hi or int(time.time() * 1000) + 3600_000, int(time.time() * 1000))
    for device, a, b in parts:
        sounds.one(device, list(models), a, b)
    return len(parts)


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
    monkeypatch.setattr(audio, "piece_window", lambda p, o, n: decodes.append(p["start_ms"]) or window(p, o, n))
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
    assert sounds.unscened(DEVICE, db.conn().execute("SELECT * FROM pieces WHERE start_ms != ?", (ms(t0) + 130_000,)).fetchall()) == 0


def test_growing_stretch_waits_for_whole_parts(on, monkeypatch):
    """Pieces still coming at a stretch's end: only its whole 300 s parts are read; once nothing more comes, the rest."""
    t0 = dt.datetime.fromtimestamp(time.time() - 3600).replace(second=0, microsecond=0)
    for s in range(0, 360, 60):
        save(t0 + dt.timedelta(seconds=s), flac(tone(1, 60)))
    db.conn().execute("UPDATE pieces SET received_ms = ?", (int(time.time() * 1000),))
    now = int(time.time() * 1000)
    monkeypatch.setattr(sounds, "SOUND_SETTLE_S", 60)
    assert [(b - a) for _, a, b in sounds.todo(["mn10_as"], 0, now + 3600_000, now + 30_000)] == []
    db.conn().execute("UPDATE pieces SET received_ms = ? WHERE start_ms < ?", (now - 120_000, ms(t0) + 300_000))
    assert [(a - ms(t0), b - a) for _, a, b in sounds.todo(["mn10_as"], 0, now + 3600_000, now)] == [(0, 300_000)]
    db.conn().execute("UPDATE pieces SET received_ms = ?", (now - 120_000,))
    assert [(a - ms(t0), b - a) for _, a, b in sounds.todo(["mn10_as"], 0, now + 3600_000, now)] == [(0, 300_000), (300_000, 60_000)]


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
    history before SOUND_SINCE too; trimmed once it has; --allow-unscened lets it go and says how much; off, as before."""
    t0, _ = hour(tmp_path, monkeypatch)
    v, now = vad(), int(time.time() * 1000)
    assert [h["reason"] for h in trim.plan(v, now)] == [None], "sound off: as before"
    monkeypatch.setenv("SOUND_MODEL", "mn10_as,yamnet")
    monkeypatch.setenv("SOUND_SINCE", (t0 + dt.timedelta(days=1)).date().isoformat())  # the hour is history
    monkeypatch.setattr(sound, "scene", fake)
    T().sound = True
    assert [h["reason"] for h in trim.plan(v, now)] == ["no sound"]
    T().sound = False
    assert [h["reason"] for h in trim.plan(v, now)] == [None], "the tenant's sound off: as before"
    T().sound = True
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
    assert r.same_models()
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
