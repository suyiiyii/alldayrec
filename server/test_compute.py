"""compute.py and infer_server.py: the same voiceprints and music through the service as in this process; a service
that fails (down, hung, a wrong token, another model, a wrong answer) is waited for, never computed here, and no
sentence is lost meanwhile; the token; and a service that holds nothing of a tenant's. With the model files in
MODELS_DIR the real models run on both sides; without them (CI) a stand-in model does, the HTTP path is the same."""
import datetime as dt
import socket
import subprocess
import sys
import threading
import time
import types

import anyio
import anyio.to_thread
import httpx2
import numpy as np
import pytest
import uvicorn

from alldayrec import audio
from alldayrec import compute
from alldayrec import core
from alldayrec import db
from alldayrec import device_pipeline
from alldayrec import infer
from alldayrec import infer_server
from test_backlog import DEVICE, SR, flac, hear, rig, tone

TOKEN = "test-token"
MODELS = all((infer.DIR / n).is_file() for n in (infer.EMB, infer.YAMNET))


class Stub:
    """A stand-in model: a fixed projection of the samples, unit; music when the mean is positive."""
    delay = 0.0

    def embed(self, x):
        time.sleep(self.delay)
        y = np.resize(x.astype(np.float64), 192 * 50).reshape(192, 50) @ np.linspace(1, 2, 50) + 1
        return (y / np.linalg.norm(y)).astype(np.float32)

    def music(self, x):
        time.sleep(self.delay)
        return bool(x.mean() > 0)


def serve(sock: socket.socket) -> uvicorn.Server:
    """infer_server on sock (bound), in a thread; returns once it answers."""
    server = uvicorn.Server(uvicorn.Config(infer_server.app, log_level="warning"))
    threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True).start()
    while not server.started:
        time.sleep(0.01)
    return server


def bound(port: int = 0) -> socket.socket:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    return sock


def down() -> tuple[int, str]:
    """A free port with nothing on it (a connection is refused; bound and not listening, macOS drops it instead) and its
    URL: serve(bound(port)) brings the service up there."""
    sock = bound()
    port = sock.getsockname()[1]
    sock.close()
    return port, f"http://127.0.0.1:{port}"


@pytest.fixture
def service(monkeypatch):
    """infer_server on a free port in a thread; yields its URL. Its model: the real one when present."""
    monkeypatch.setitem(infer_server.state, "compute", compute.Local() if MODELS else Stub())
    monkeypatch.setitem(infer_server.state, "token", TOKEN)
    monkeypatch.setattr(compute, "CALLS", type(compute.CALLS)())
    monkeypatch.setattr(compute, "BACKOFF_S", (0.2, 0.4))
    sock = bound()
    server = serve(sock)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True


def speech(seconds=3.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * 16000)) / 16000
    x = 3000 * np.sin(2 * np.pi * 180 * t * (1 + 0.1 * np.sin(2 * np.pi * 3 * t))) + 300 * rng.standard_normal(len(t))
    return x.astype(np.int16)


def counted(r) -> list:
    """r.http.post, counting the tries."""
    tries, post = [], r.http.post
    r.http.post = lambda *a, **k: tries.append(time.monotonic()) or post(*a, **k)
    return tries


def background(f, *args) -> dict:
    """f(*args) in a thread; the dict gets "out" when it returns."""
    got = {}
    t = threading.Thread(target=lambda: got.update(out=f(*args)), daemon=True)
    t.start()
    got["thread"] = t
    return got


def test_remote_same_as_local(service):
    local = infer_server.state["compute"]
    r = compute.Remote(service, TOKEN, 5.0)
    for seed, s in enumerate((0.4, 3.0, 15.0)):
        x = speech(s, seed)
        a, b = local.embed(x), r.embed(x)
        assert b.dtype == np.float32 and b.shape == a.shape and float(a @ b) >= 0.9999
        assert r.music(x) == local.music(x) and r.music(-x) == local.music(-x)
    assert compute.CALLS["embed", "remote"] == 3 and compute.CALLS["embed", "error"] == 0
    assert r.down_seconds() == 0


def test_waits_for_a_service_down(monkeypatch, caplog):
    """Refused for a while: the call waits, trying again after the backoff, and gets the service's answer when it is
    back; nothing is computed here; one error line, the down time while it lasts."""
    monkeypatch.setitem(infer_server.state, "compute", Stub())
    monkeypatch.setitem(infer_server.state, "token", TOKEN)
    monkeypatch.setattr(compute, "CALLS", type(compute.CALLS)())
    monkeypatch.setattr(compute, "BACKOFF_S", (0.2, 0.4))
    monkeypatch.setattr(compute, "Local", lambda: pytest.fail("computed here"))
    port, url = down()
    r, x = compute.Remote(url, TOKEN, 5.0), speech()
    tries = counted(r)
    got = background(r.embed, x)
    time.sleep(1.5)
    assert "out" not in got and r.down_seconds() > 1, "waiting"
    server = serve(bound(port))
    got["thread"].join(5)
    server.should_exit = True
    assert float(got["out"] @ Stub().embed(x)) >= 0.9999
    gaps = np.diff(tries)
    assert len(tries) >= 4 and gaps[0] >= 0.18 and gaps[-1] >= 0.38, gaps  # 0.2 s, then 0.4 s between tries
    assert compute.CALLS["embed", "retried"] == 1 and compute.CALLS["embed", "error"] == len(tries) - 1
    assert r.down_seconds() == 0 and sum("infer service embed" in m for m in caplog.messages) == 1, caplog.messages


def test_hung_service_one_try_a_backoff(monkeypatch):
    """Taken, never answered: the first call waits out its timeout once; a call made meanwhile sleeps to the next try
    instead of waiting out a timeout of its own; both answered once the service answers."""
    monkeypatch.setitem(infer_server.state, "compute", Stub())
    monkeypatch.setitem(infer_server.state, "token", TOKEN)
    monkeypatch.setattr(compute, "CALLS", type(compute.CALLS)())
    monkeypatch.setattr(compute, "BACKOFF_S", (2.0, 2.0))
    sock = bound()
    sock.listen()  # the kernel takes the connections, nobody reads them
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    r, x = compute.Remote(url, TOKEN, 0.5), speech()
    tries = counted(r)
    a = background(r.embed, x)
    time.sleep(1.0)  # a has failed (0.5 s), its next try at 2.5 s
    b = background(r.music, x)
    time.sleep(1.0)
    assert len(tries) == 1 and "out" not in a and "out" not in b, "b sleeps to a's next try"
    server = serve(sock)
    a["thread"].join(5)
    b["thread"].join(5)
    server.should_exit = True
    assert float(a["out"] @ Stub().embed(x)) >= 0.9999 and b["out"] == Stub().music(x)
    assert len(tries) == 3 and tries[1] - tries[0] >= 1.9, np.diff(tries)


def test_wrong_answer_is_tried_again(service, monkeypatch):
    """Not a unit 192-float voiceprint (another model's, a broken one): never taken, tried again."""
    x, bad = speech(), [np.ones(191, np.float32) / np.sqrt(191), np.ones(192, np.float32)]
    stub = Stub()
    real = stub.embed
    stub.embed = lambda x: bad.pop() if bad else real(x)
    monkeypatch.setitem(infer_server.state, "compute", stub)
    r = compute.Remote(service, TOKEN, 5.0)
    assert float(r.embed(x) @ Stub().embed(x)) >= 0.9999
    assert compute.CALLS["embed", "error"] == 2 and compute.CALLS["embed", "retried"] == 1


def test_token(service, monkeypatch, caplog):
    x = speech().tobytes()
    for h in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
        assert httpx2.post(f"{service}/embed", content=x, headers=h).status_code == 401
        assert httpx2.get(f"{service}/metrics", headers=h).status_code == 401
    assert httpx2.get(f"{service}/health").json() == {"ok": True}
    assert httpx2.get(f"{service}/health", headers={"Authorization": "Bearer wrong"}).status_code == 401
    ok = {"Authorization": f"Bearer {TOKEN}"}
    assert httpx2.get(f"{service}/health", headers=ok).json() == {"ok": True, "models": compute.models(), "dim": 192}
    assert httpx2.post(f"{service}/embed", content=x[:3], headers=ok).status_code == 400
    big = b"\0" * (infer_server.MAX_S * 32000 + 2)
    assert httpx2.post(f"{service}/music", content=big, headers=ok).status_code == 413
    assert httpx2.post(f"{service}/music", content=iter([big]), headers=ok).status_code == 413, "chunked, no length"
    m = httpx2.get(f"{service}/metrics", headers=ok).text
    assert 'lr_infer_server_calls_total{op="embed",result="401"} 3' in m and 'result="413"} 2' in m

    # a wrong token is a setting to fix: tried again and again, one error line a minute naming it
    r = compute.Remote(service, "wrong", 5.0)
    tries = counted(r)
    got = background(r.embed, speech())
    time.sleep(1.5)
    assert "out" not in got and len(tries) >= 3
    assert [m for m in caplog.messages if "INFER_TOKEN" in m] and sum("infer service" in m for m in caplog.messages) == 1
    r.http.headers["Authorization"] = f"Bearer {TOKEN}"  # fixed
    got["thread"].join(5)
    assert got["out"].shape == (192,) and r.down_seconds() == 0


def test_service_holds_nothing_of_a_tenant(tmp_path):
    """Pure computation: the service loads no database, audio, tenant-pipeline or server module, and its only routes
    are these. (alldayrec.tenant comes in through infer.py, for MODELS_DIR's default path only.)"""
    out = subprocess.run([sys.executable, "-c", "import sys, alldayrec.infer_server as s; "
                          "print(sorted(m for m in sys.modules if m.startswith('alldayrec.'))); "
                          "print(sorted(r.path for r in s.app.routes))"],
                         capture_output=True, text=True, check=True, env={"DATA_DIR": str(tmp_path / "data"), "PATH": ""}).stdout
    mods, routes = out.splitlines()
    assert eval(routes) == ["/embed", "/health", "/metrics", "/music"]
    assert not {"alldayrec.db", "alldayrec.audio", "alldayrec.core", "alldayrec.bank", "alldayrec.server"} & set(eval(mods))
    assert not (tmp_path / "data").exists()


def test_load(service, monkeypatch, caplog, tmp_path):
    monkeypatch.delenv("INFER_URL", raising=False)
    monkeypatch.setattr(compute, "Local", Stub)
    monkeypatch.setattr(compute, "REMOTE", None)
    assert isinstance(compute.load(), Stub) and not compute.served(), "INFER_URL unset: in this process, as before"
    monkeypatch.setenv("INFER_URL", service)
    assert compute.served() == {infer.EMB, infer.YAMNET}, "nor downloaded or checked at start"
    infer.fetch(tmp_path, skip=set(infer.MODELS))  # nothing to download, nothing missing
    infer.check(tmp_path, skip=set(infer.MODELS))
    with pytest.raises(SystemExit, match=infer.VAD):
        infer.check(tmp_path, skip=compute.served())
    monkeypatch.delenv("INFER_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        compute.load()
    monkeypatch.setenv("INFER_TOKEN", TOKEN)
    monkeypatch.setattr(compute, "Local", lambda: pytest.fail("computed here"))
    r = compute.load()
    assert isinstance(r, compute.Remote) and compute.REMOTE is r and r.ok and r.down_seconds() == 0, "its models are ours"

    monkeypatch.setenv("INFER_TOKEN", "wrong")
    r = compute.load()
    assert not r.ok and r.down_seconds() > 0 and "INFER_TOKEN is not the service's" in caplog.text, "said at start"

    monkeypatch.setenv("INFER_TOKEN", TOKEN)
    other = types.SimpleNamespace(models=lambda: {infer.EMB: "other"}, DIM=192)
    monkeypatch.setattr(infer_server, "compute", other)
    r = compute.load()
    assert not r.ok and "other models" in caplog.text
    x = speech()
    got = background(r.embed, x)
    time.sleep(1.0)
    assert "out" not in got, "another model's voiceprints are never taken"
    monkeypatch.setattr(infer_server, "compute", compute)  # the service updated
    got["thread"].join(5)
    assert got["out"].shape == (192,) and r.ok


def test_pipeline_waits_for_the_service(tmp_path, monkeypatch):
    """The service down for a while: uploads are taken and stored meanwhile, the event loop goes on, the final layer
    waits; once it is back every sentence of the time it was down gets its voiceprint from it, once, in order (the
first one alone is S? as with a local model: the diarist's, a lone short sentence)."""
    rig(tmp_path, monkeypatch, hear)
    unit = lambda i: np.eye(192, dtype=np.float32)[i]
    np.savez(tmp_path / "owner-redimnet2.npz", owner=unit(0))
    called = []
    model = types.SimpleNamespace(embed=lambda x: called.append(1) or unit(1), music=lambda x: False)
    monkeypatch.setitem(infer_server.state, "compute", model)
    monkeypatch.setitem(infer_server.state, "token", TOKEN)
    monkeypatch.setattr(compute, "CALLS", type(compute.CALLS)())
    monkeypatch.setattr(compute, "BACKOFF_S", (0.5, 1.0))
    port, url = down()
    r = compute.Remote(url, TOKEN, 5.0)
    monkeypatch.setitem(core.state, "embed", r.embed)
    monkeypatch.setitem(core.state, "music", r.music)
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 600)
    x = np.concatenate([np.concatenate([tone(i, 2), np.zeros(3 * SR, np.int16)]) for i in range(4)])  # 20 s, 4 sentences
    finals = lambda: db.sentences(core.iso(t0), core.iso(t0 + dt.timedelta(seconds=60)), source=DEVICE)
    stored = lambda: db.conn().execute("SELECT COUNT(*) FROM pieces WHERE device = ?", (DEVICE,)).fetchone()[0]

    up = []  # the service, once brought up

    async def checks():
        took = []
        for k in range(20):  # 1 s pieces, as an app sends them, the service down
            t = time.perf_counter()
            await anyio.to_thread.run_sync(audio.save, DEVICE, int((t0 + dt.timedelta(seconds=k)).timestamp() * 1000),
                                           flac(x[k * SR:(k + 1) * SR]), "audio/flac")
            took.append(time.perf_counter() - t)
        db.put_kv(f"end_at:{DEVICE}", core.iso(t0 + dt.timedelta(seconds=20)))
        lag = 0.0
        for _ in range(30):  # 3 s down after the last upload: the loop is free, the final layer waits
            t = time.perf_counter()
            await anyio.sleep(0.1)
            lag = max(lag, time.perf_counter() - t - 0.1)
        assert stored() == 20 and max(took) < 1.0 and lag < 0.3, (stored(), took, lag)
        assert finals() == [] and r.down_seconds() > 0, "waiting for the service, nothing skipped"
        up.append(await anyio.to_thread.run_sync(serve, bound(port)))
        for _ in range(60):
            if len(finals()) >= 4:
                break
            await anyio.sleep(0.5)
        rows = finals()
        assert [s["text"] for s in rows] == ["0", "1", "2", "3"], rows
        assert len(called) == 4 and rows[-1]["speaker"] == "S1", (len(called), rows)  # each sentence's voiceprint, once
        assert sum(n for (_, k), n in compute.CALLS.items() if k == "error") > 0, "it was down"

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            try:
                await checks()
            finally:  # the service up before the cancel: it waits for a call in a thread, which waits for the service
                if not up:
                    up.append(await anyio.to_thread.run_sync(serve, bound(port)))
                tg.cancel_scope.cancel()
        up[0].should_exit = True

    anyio.run(run)
