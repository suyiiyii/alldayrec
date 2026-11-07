"""A question said aloud as it is said (WebSocket /api/ask/voice/ws): the page's PCM goes to the streaming session as it
comes, its words back as they grow, the final text once the page lets go; a stream that fails or never ends goes to the
fallback engine with the whole audio; a page gone closes the session at once; a minute at most; each try in the calls
table as `voice` with the seconds received. A fake streaming session stands in for Bailian's."""
import datetime as dt
import time

import anyio
import numpy as np
import pytest
from starlette.testclient import TestClient

from alldayrec import asr
from alldayrec import core
from alldayrec import db
from alldayrec import qwen_stream
from alldayrec import routes_api
from alldayrec import server
from alldayrec import tenant

SR = 16000
CHUNK = np.full(SR // 10, 3000, np.int16).tobytes()  # 0.1 s, as the page sends it


class Fake:
    """qwen_stream.Session's interface: a partial per send, two sentences once finished; the first sentence's begin_time
    moves as it grows, as Bailian's does; `mode` fail: fails after the first audio; hang: never finishes."""
    mode = "ok"
    opened: list["Fake"] = []

    async def __aenter__(self):
        self.got, self.closed, self.tx, self.rx = 0, False, *anyio.create_memory_object_stream(100)
        Fake.opened.append(self)
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def send(self, pcm: bytes):
        self.got += len(pcm)
        if Fake.mode == "fail":
            raise RuntimeError("qwen stream: InternalError")
        early = self.got < 6 * len(CHUNK)
        await self.tx.send({"start_ms": 80 if early else 120, "end_ms": 0, "text": "你好" if early else "你好，我", "final": False})

    async def finish(self):
        if Fake.mode != "hang":
            await self.tx.send({"start_ms": 0, "end_ms": 900, "text": "你好，我。", "final": True})
            await self.tx.send({"start_ms": 1000, "end_ms": 1900, "text": "Bob 呢？", "final": True})
            self.tx.close()

    async def results(self):
        async for g in self.rx:
            yield g


@pytest.fixture
def c(tmp_path, monkeypatch):
    t = tenant.Tenant("a", "", tmp_path, tmp_path)  # one tenant, no token: no auth
    tenant.call(t, db.init)
    monkeypatch.setattr(core, "TENANTS", [t])
    monkeypatch.setattr(qwen_stream, "Session", Fake)
    monkeypatch.setattr(Fake, "mode", "ok")
    monkeypatch.setattr(Fake, "opened", [])
    monkeypatch.setattr(asr, "ENGINE", "qwen")
    monkeypatch.setattr(asr, "DRAFT_ENGINE", "fake")
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "")
    heard = []

    async def engine(x, partial=None):
        heard.append(len(x) / SR)
        return [{"start_ms": 0, "end_ms": 500, "text": "整段"}, {"start_ms": 600, "end_ms": 900, "text": "听的"}]
    monkeypatch.setitem(asr.PROVIDERS, "fake", engine)
    client = TestClient(server.app)  # no `with`: the lifespan (the tenant's loops) does not start
    client.heard, client.t = heard, t
    client.lo = dt.datetime.now().isoformat()
    return client


def say(c, n: int, end=True) -> list[dict]:
    """n chunks said, then let go: every message back until the server closes."""
    got = []
    with c.websocket_connect("/api/ask/voice/ws") as w:
        for _ in range(n):
            w.send_bytes(CHUNK)
        if end:
            w.send_json({"type": "end"})
        while not got or got[-1]["type"] == "partial":
            got.append(w.receive_json())
    return got


def calls(c) -> list[tuple]:
    rows = tenant.call(c.t, lambda: db.conn().execute("SELECT kind, engine, error, audio_s FROM calls WHERE at >= ?", (c.lo,)).fetchall())
    return [tuple(r) for r in rows]


def test_stream(c):
    """Words as they grow, each sentence once though its begin_time moved, then the whole text; the session closed; one
    call, 2 s received."""
    got = say(c, 20)
    partials = [m["text"] for m in got if m["type"] == "partial"]
    assert partials[0] == "你好" and "你好，我" in partials, "the words grow while it is said"
    assert not any("你好你好" in p for p in partials), "a sentence said once is shown once"
    assert got[-1] == {"type": "final", "text": "你好，我。 Bob 呢？"}
    assert len(Fake.opened) == 1 and Fake.opened[0].got == 20 * len(CHUNK) and Fake.opened[0].closed
    assert calls(c) == [("voice", "qwen-stream", None, 2.0)] and not c.heard


def test_stream_fails(c, monkeypatch):
    """The stream failing: the fallback hears all 2 s; both tries in the table."""
    monkeypatch.setattr(Fake, "mode", "fail")
    assert say(c, 20)[-1] == {"type": "final", "text": "整段听的"}
    assert c.heard == [2.0]
    assert calls(c) == [("voice", "qwen-stream", "RuntimeError: qwen stream: InternalError", 2.0), ("voice", "fake", None, 2.0)]


def test_stream_never_ends(c, monkeypatch):
    """No end VOICE_END_S after the page let go: closed, the fallback hears it."""
    monkeypatch.setattr(Fake, "mode", "hang")
    monkeypatch.setattr(routes_api, "VOICE_END_S", 0.3)
    assert say(c, 10)[-1] == {"type": "final", "text": "整段听的"}
    assert Fake.opened[0].closed and c.heard == [1.0]
    assert [r[:3] for r in calls(c)] == [("voice", "qwen-stream", "TimeoutError: no end 0.3 s after the last audio"), ("voice", "fake", None)]


def test_dropped(c):
    """The page closes the socket (slid up, Escape, gone): the session closed at once, no fallback, no answer; why, when
    the page says it, in the table, else the close code."""
    with c.websocket_connect("/api/ask/voice/ws") as w:
        for _ in range(10):
            w.send_bytes(CHUNK)
        assert w.receive_json()["type"] == "partial"
        w.close(4000, "short 230")
    wait_closed()
    assert Fake.opened[0].closed and not c.heard
    assert calls(c) == [("voice", "qwen-stream", "cancelled: short 230", 1.0)]
    with c.websocket_connect("/api/ask/voice/ws"):
        pass
    wait_closed(1)
    assert calls(c)[1] == ("voice", "qwen-stream", "cancelled: the page went (1000)", 0.0)


def wait_closed(i=0):
    t = time.time()
    while not (len(Fake.opened) > i and Fake.opened[i].closed) and time.time() - t < 5:
        time.sleep(0.02)


def test_diag_logged(c, caplog):
    """The page's diag with the end goes to the log as one line with the length of the text heard, never the text."""
    caplog.set_level("INFO", "live")
    with c.websocket_connect("/api/ask/voice/ws") as w:
        w.send_bytes(CHUNK)
        w.send_json({"type": "end", "diag": {"held_ms": 2100, "rate": 48000, "frames": 100800, "ua": "x\ny"}})
        while w.receive_json()["type"] == "partial":
            pass
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("ask voice: "))
    assert '"held_ms": 2100' in line and '"frames": 100800' in line and "\n" not in line.replace("\\n", "")
    assert f"heard {len('你好，我。 Bob 呢？')} chars by stream" in line and "Bob" not in line


def test_a_minute_at_most(c, monkeypatch):
    """Past VOICE_MAX_S the rest is dropped and the question ends there, no end needed."""
    monkeypatch.setattr(routes_api, "VOICE_MAX_S", 1)
    assert say(c, 15, end=False)[-1]["type"] == "final"
    assert Fake.opened[0].got == SR * 2 and calls(c) == [("voice", "qwen-stream", None, 1.0)]


def test_no_bailian_engine(c, monkeypatch):
    """A server whose engines send no audio to Bailian: no stream, the whole audio heard by its engine once let go."""
    monkeypatch.setattr(asr, "ENGINE", "local")
    got = say(c, 20)
    assert got == [{"type": "final", "text": "整段听的"}] and not Fake.opened and c.heard == [2.0]
    assert calls(c) == [("voice", "fake", None, 2.0)]
    assert say(c, 0) == [{"type": "final", "text": ""}] and len(c.heard) == 1, "nothing said: nothing sent"
