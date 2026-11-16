"""Per-device gain (gain.py): rows hold by time, each column on its own; one offset at every decoded exit, once, for
the models and the people listening alike (a recorder's PLAY_GAIN_DB then off); soft-limited past full scale and
counted; the stored bytes and levels as they came; reported capture gains kept only when they change; each tenant its
own; the schema made once however often it starts; the CLI."""
import datetime as dt
import json
import os
from pathlib import Path

import anyio
import numpy as np
import pytest
from starlette.testclient import TestClient

from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import gain
from alldayrec import late
from alldayrec import server
from alldayrec import tenant
from alldayrec.tenant import T
from test_gain_parity import T0, at, pcm, speech, streamed

SR = audio.SR
up = lambda x, g: np.round(x * 10 ** (g / 20)).astype(np.int16)  # +g dB, under the soft limiter's knee


def test_time_and_columns(tmp_path):
    db.open_db(tmp_path / "t.db")
    d = "LR-G1-TEST"
    assert gain.offset_db(d, T0) is None and gain.capture_db(d, T0) is None, "no row: none (0 dB)"
    gain.put(d, T0, 6.0, note="cal 1")
    gain.put(d, T0 + 60_000, -3.0, capture=4.0)
    assert gain.report(d, T0 + 30_000, 8)
    assert gain.offset_db(d, T0 - 1) is None and gain.offset_db(d, T0) == 6 and gain.offset_db(d, T0 + 59_999) == 6
    assert gain.offset_db(d, T0 + 30_000) == 6, "a reported capture gain hides no offset"
    assert gain.offset_db(d, T0 + 60_000) == -3 and gain.offset_db(d, 1 << 50) == -3
    assert gain.capture_db(d, T0 + 29_999) is None and gain.capture_db(d, T0 + 30_000) == 8 and gain.capture_db(d, T0 + 60_000) == 4
    assert gain.offset_db("LR-OTHER", T0 + 60_000) is None, "each device its own"
    gain.put(d, T0, 7.0)
    assert gain.offset_db(d, T0) == 7 and len(gain.rows(d)) == 3, "set again at the same time: replaced"


def test_reported(tmp_path, monkeypatch):
    t = db.open_db(tmp_path / "t.db")
    monkeypatch.setattr(core, "TENANTS", [t])  # no token: no auth
    c = TestClient(server.app)  # no `with`: the lifespan does not start
    d = "LR-G2-TEST"
    for hw in ({"battery": 50}, {"battery": 50, "mic_gain_db": None}, {"mic_gain_db": "6"}, {"mic_gain_db": True},
               {"mic_gain_db": 1e9}):
        assert c.post("/api/device-status", params={"device": d}, json=hw).status_code == 200
    assert gain.rows(d) == [], "absent, null or not a gain: nothing"
    for v in (6.5, 6.5, 0, 0, 6.5):
        assert c.post("/api/device-status", params={"device": d}, json={"battery": 50, "mic_gain_db": v}).status_code == 200
    assert [(r["capture_db"], r["offset_db"], r["source"]) for r in gain.rows(d)] == [(6.5, None, "reported"), (0, None, "reported"),
                                                                                   (6.5, None, "reported")], "a row each change"
    assert not gain.report(d, T0, float("nan")) and not gain.report(d, T0, float("inf"))
    phone = "phone-g2"
    for i, v in enumerate((None, 3.0, 3.0, -2.0)):  # a phone's pieces, the last two with another gain
        q = {"device": phone, "start": T0 + i * 1000} | ({} if v is None else {"capture_gain_db": v})
        r = c.post("/api/chunks", params=q, content=audio.wav(speech(1, i)), headers={"content-type": "audio/wav"})
        assert r.status_code == 200, r.text
    assert [(r["since_ms"], r["capture_db"]) for r in gain.rows(phone)] == [(T0 + 1000, 3.0), (T0 + 3000, -2.0)]
    assert gain.offset_db(phone, T0 + 3000) is None, "a client's report never changes the audio"
    got = c.get("/api/device-gain").json()
    assert got["target_dbov"] == -26 and len(got["rows"]) == 5


def test_every_exit_once(tmp_path, monkeypatch):
    """+6 dB at samples, fresh, the stream, the late read, cut s16le and wav, listening: each exactly +6 dB, never
    twice; a recorder with an offset plays at it alone (no PLAY_GAIN_DB); X-Gain-Db says which. Long imports too."""
    db.open_db(tmp_path / "t.db")
    x = {d: speech(12, k) for k, d in enumerate(("phone-g3", "LR-G3-TEST", "import-g3"))}
    monkeypatch.setattr(audio, "WHOLE_S", 10)  # import-g3's 12 s: a long import (piece_window, piece_blocks)
    for d, y in x.items():
        assert audio.save(d, T0, audio.wav(y), "audio/wav") == 12
    for d in x:
        gain.put(d, 0, 6.0)
    a, b = T0 + 1000, T0 + 4000
    for d, y in x.items():
        g = up(y, 6)
        p = db.conn().execute("SELECT * FROM pieces WHERE device = ?", (d,)).fetchone()
        if d != "import-g3":
            assert np.array_equal(audio.samples(p), g) and np.array_equal(audio.fresh(p, T0 + 500)[1], g[SR // 2:]), d
        assert np.array_equal(audio.piece_window(p, SR, 2 * SR), g[SR:3 * SR]), d
        assert np.array_equal(anyio.run(streamed, d, len(y))[:len(y)], g), d
        assert np.array_equal(late.read_exact(d, a, b), g[SR:4 * SR]), d
        s16 = np.frombuffer(audio.cut(at(a), at(b), "s16le", d), np.int16)
        assert np.array_equal(s16, g[SR - SR // 5:4 * SR + SR // 5]), d
        assert np.array_equal(pcm(audio.cut(at(a), at(b), "wav", d)), s16), f"{d}: played as the models hear it"
        assert np.array_equal(audio.newer(d, None)[2], g), d
        wav = anyio.run(audio_route, a, b, d)
        assert wav.headers["x-gain-db"] == "6", d
    monkeypatch.setattr(audio, "WHOLE_S", 300)
    assert audio.play_db("LR-NONE", T0) == audio.PLAY_GAIN_DB and audio.play_db("phone-none", T0) == 0, "no offset: as on main"


async def audio_route(a, b, d):
    from alldayrec import routes_audio
    return await routes_audio.audio_wav(at(a).isoformat(), at(b).isoformat(), d)


def test_soft_limit_and_raw_kept(tmp_path):
    t = db.open_db(tmp_path / "t.db")
    d = "LR-G4-TEST"
    y = speech(3, 4, -20)  # loud: +12 dB takes its peaks past full scale
    body = audio.wav(y)
    assert audio.save(d, T0, body, "audio/wav") == 3
    p = db.conn().execute("SELECT * FROM pieces WHERE device = ?", (d,)).fetchone()
    stored = (p["rms"], p["hum"], p["zero_ms"], p["full"], p["env"])
    gain.put(d, 0, 12.0)
    z = audio.samples(p)
    f = y.astype(np.float64) * 10 ** (12 / 20)
    assert t.gain_limited[d] == np.count_nonzero(np.abs(f) > 32768) > 0, "counted: the samples past +-1"
    assert np.abs(z.astype(int)).max() <= 32767 and np.count_nonzero(np.abs(z.astype(int)) == 32767) < t.gain_limited[d] / 10, \
        "soft: few reach full scale"
    over = np.abs(f) > gain.KNEE * 32768
    assert np.all(np.abs(z[over].astype(int)) <= np.abs(f[over]) + 0.5), "past the knee: never above the plain gain"
    small = np.abs(f) <= gain.KNEE * 32768
    assert np.array_equal(z[small], np.round(f[small]).astype(np.int16)), "under the knee: the plain gain"
    order = np.argsort(f)
    assert np.all(np.diff(z[order].astype(int)) >= 0), "monotonic: louder stays louder"
    assert audio.read(p) == body, "the stored bytes as uploaded"
    q = db.conn().execute("SELECT * FROM pieces WHERE device = ?", (d,)).fetchone()
    assert (q["rms"], q["hum"], q["zero_ms"], q["full"], q["env"]) == stored
    gain.put(d, 0, -6.0)
    assert np.array_equal(audio.samples(p), up(y, -6)) and audio.read(p) == body, "lower: rounded, the bytes the same"
    gain.put(d, 0, 0.0)
    x0 = audio.decode(body)
    assert np.array_equal(audio.samples(p), x0), "0 dB: as decoded"
    assert gain.apply(x0, d, T0) is x0, "0 dB: the very array"


def test_tenants_and_init(tmp_path):
    a, b = (tenant.Tenant(i, f"t{i}", tmp_path / i, tmp_path / i) for i in "ab")
    for t in (a, b):
        t.data.mkdir()
        tenant.call(t, db.init)
    tenant.call(a, gain.put, "LR-SAME", 0, 9.0)
    x = speech(1, 5)
    assert tenant.call(b, gain.offset_db, "LR-SAME", T0) is None and tenant.call(b, gain.apply, x, "LR-SAME", T0) is x
    assert np.array_equal(tenant.call(a, gain.apply, x, "LR-SAME", T0), up(x, 9))
    for _ in range(2):
        tenant.call(a, db.init)
    assert tenant.call(a, gain.rows) == [{"device": "LR-SAME", "since_ms": 0, "capture_db": None, "offset_db": 9.0, "source": "manual",
                                          "note": None}], "init again: the table and its rows as they were"


def test_cli(tmp_path, monkeypatch, capsys):
    ts = [{"id": i, "token": f"t{i}", "data": str(tmp_path / i), "recordings": str(tmp_path / i)} for i in "ab"]
    f = tmp_path / "tenants.json"
    f.write_text(json.dumps(ts))
    os.chmod(f, 0o600)
    monkeypatch.setattr(tenant, "FILE", f)
    for t in tenant.load(f):
        t.data.mkdir()
        tenant.call(t, db.init)
    gain.main(["--tenant", "a", "set", "LR-CLI", "7.5", "--since", dt.datetime.fromtimestamp(T0 / 1000).isoformat(), "--note", "made up"])
    gain.main(["list"])
    out = capsys.readouterr().out
    assert "a\tLR-CLI\t" in out and "offset=7.5" in out and "made up" in out and "b\t" not in out, out
    a = tenant.load(f)[0]
    assert tenant.call(a, gain.offset_db, "LR-CLI", T0) == 7.5 and tenant.call(a, gain.offset_db, "LR-CLI", T0 - 1) is None
    with pytest.raises(SystemExit):
        gain.main(["set", "LR-CLI", "3"])  # which tenant
    with pytest.raises(SystemExit):
        gain.main(["--tenant", "a", "set", "LR-CLI", "nan"])
    with pytest.raises(SystemExit):
        gain.main(["--tenant", "zz", "list"])
