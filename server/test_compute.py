"""compute.py and infer_server.py: the same voiceprints and music through the service as in this process, the
fallback when the service fails, the token, and a service that holds nothing of a tenant's. With the model files in
MODELS_DIR the real models run on both sides; without them (CI) a stand-in model does, the HTTP path is the same."""
import socket
import subprocess
import sys
import threading
import time

import httpx2
import numpy as np
import pytest
import uvicorn

from alldayrec import compute
from alldayrec import infer
from alldayrec import infer_server

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

    def segment(self, x):
        raise NotImplementedError


@pytest.fixture
def service(monkeypatch):
    """infer_server on a free port in a thread; yields its URL. Its model: the real one when present."""
    monkeypatch.setitem(infer_server.state, "compute", compute.Local() if MODELS else Stub())
    monkeypatch.setitem(infer_server.state, "token", TOKEN)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(infer_server.app, log_level="warning"))
    t = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    t.start()
    while not server.started:
        time.sleep(0.01)
    yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    server.should_exit = True
    t.join(5)


def speech(seconds=3.0, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(int(seconds * 16000)) / 16000
    x = 3000 * np.sin(2 * np.pi * 180 * t * (1 + 0.1 * np.sin(2 * np.pi * 3 * t))) + 300 * rng.standard_normal(len(t))
    return x.astype(np.int16)


def remote(url, fallback="local", timeout=5.0, token=TOKEN):
    r = compute.Remote(url, token, timeout, fallback)
    r._local = infer_server.state["compute"]  # the fallback: this process's model, the same as the service's
    return r


def test_remote_same_as_local(service, monkeypatch):
    monkeypatch.setattr(compute, "CALLS", type(compute.CALLS)())
    local = infer_server.state["compute"]
    r = remote(service)
    for seed, s in enumerate((0.4, 3.0, 15.0)):
        x = speech(s, seed)
        a, b = local.embed(x), r.embed(x)
        assert b.dtype == np.float32 and b.shape == a.shape and float(a @ b) >= 0.9999
        assert r.music(x) == local.music(x) and r.music(-x) == local.music(-x)
    assert compute.CALLS["embed", "remote"] >= 3 and compute.CALLS["embed", "local"] == 0


def test_fallback_on_timeout_and_error(service, monkeypatch, caplog):
    monkeypatch.setattr(compute, "CALLS", type(compute.CALLS)())
    x = speech()
    want = infer_server.state["compute"].embed(x)
    stub = Stub()
    stub.delay = 1.0
    monkeypatch.setitem(infer_server.state, "compute", stub)  # the service answers too late
    r = remote(service, timeout=0.2)
    r._local = Stub() if not MODELS else compute.Local()
    t = time.perf_counter()
    assert float(r.embed(x) @ want) >= 0.9999 and time.perf_counter() - t < 1.5, "two tries of 0.2 s, then here"
    assert compute.CALLS["embed", "local"] == 1 and "computed here" in caplog.text and TOKEN not in caplog.text

    class Broken(Stub):
        def music(self, x):
            raise RuntimeError("model gone")
    monkeypatch.setitem(infer_server.state, "compute", Broken())  # a 500
    assert r.music(x) == r._local.music(x) and compute.CALLS["music", "local"] == 1
    with pytest.raises(RuntimeError, match="infer service music"):
        remote(service, fallback="none").music(x)
    assert compute.CALLS["music", "failed"] == 1

    dead = socket.socket()
    dead.bind(("127.0.0.1", 0))
    port = dead.getsockname()[1]
    dead.close()  # nothing there: refused
    assert float(remote(f"http://127.0.0.1:{port}", timeout=0.5).embed(x) @ want) >= 0.9999
    assert compute.CALLS["embed", "local"] == 2


def test_token(service, monkeypatch):
    x = speech().tobytes()
    for h in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}):
        assert httpx2.post(f"{service}/embed", content=x, headers=h).status_code == 401
        assert httpx2.get(f"{service}/metrics", headers=h).status_code == 401
    assert httpx2.get(f"{service}/health").json() == {"ok": True}
    ok = {"Authorization": f"Bearer {TOKEN}"}
    assert httpx2.post(f"{service}/embed", content=x[:3], headers=ok).status_code == 400
    assert httpx2.post(f"{service}/music", content=b"\0" * (infer_server.MAX_S * 32000 + 2), headers=ok).status_code == 413
    m = httpx2.get(f"{service}/metrics", headers=ok).text
    assert 'lr_infer_server_calls_total{op="embed",result="401"} 3' in m and 'result="413"' in m

    monkeypatch.setattr(compute, "CALLS", type(compute.CALLS)())
    seen = []
    monkeypatch.setattr(infer_server, "authorized", lambda req: seen.append(1) and False)
    r = remote(service, token="wrong")
    r.embed(speech())
    assert len(seen) == 1 and compute.CALLS["embed", "local"] == 1, "a 401 is not tried again"


def test_service_holds_nothing_of_a_tenant(tmp_path):
    """Pure computation: the service loads no tenant, database or audio module, and its only routes are these."""
    out = subprocess.run([sys.executable, "-c", "import sys, alldayrec.infer_server as s; "
                          "print(sorted(m for m in sys.modules if m.startswith('alldayrec.'))); "
                          "print(sorted(r.path for r in s.app.routes))"],
                         capture_output=True, text=True, check=True, env={"DATA_DIR": str(tmp_path / "data"), "PATH": ""}).stdout
    mods, routes = out.splitlines()
    assert eval(routes) == ["/embed", "/health", "/metrics", "/music"]
    assert not {"alldayrec.db", "alldayrec.audio", "alldayrec.core", "alldayrec.bank", "alldayrec.server"} & set(eval(mods))
    assert not (tmp_path / "data").exists()


def test_load(monkeypatch):
    monkeypatch.delenv("INFER_URL", raising=False)
    monkeypatch.setattr(compute, "Local", Stub)
    assert isinstance(compute.load(), Stub), "INFER_URL unset: in this process, as before"
    monkeypatch.setenv("INFER_URL", "https://infer.example.com")
    monkeypatch.delenv("INFER_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        compute.load()
    monkeypatch.setenv("INFER_TOKEN", TOKEN)
    monkeypatch.setenv("INFER_FALLBACK", "maybe")
    with pytest.raises(SystemExit):
        compute.load()
    monkeypatch.setenv("INFER_FALLBACK", "none")
    r = compute.load()
    assert isinstance(r, compute.Remote) and r.fallback == "none" and r._local is None, "no local model until a fallback"
