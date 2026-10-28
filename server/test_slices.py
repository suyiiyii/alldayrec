"""The piece lengths the apps get, from /api/client-config and with every upload's answer: the configured ones, and
while the live page is open the background and screen-off ones at most SLICE_WATCHED_S (the foreground's stays) and
`watched` true.
今天's live card watches too (drafts, the live analysis) but leaves the lengths as they are."""
import time

from starlette.testclient import TestClient

from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import server

NORMAL = {"foreground": 1, "background": 20, "screenOff": 60, "watched": False}
WATCHED = {"foreground": 1, "background": 2, "screenOff": 2, "watched": True}


def test_slices(tmp_path, monkeypatch):
    t = db.open_db(tmp_path / "live.db")  # one tenant without a token: no auth
    monkeypatch.setattr(core, "TENANTS", [t])
    monkeypatch.setattr(audio, "save_file", lambda *a: 2.0)  # a piece of 2 s, not decoded
    c = TestClient(server.app)  # no `with`: the lifespan does not start
    up = lambda: c.post("/api/chunks", params={"device": "phone", "start": 1_000}, content=b"x", headers={"content-type": "audio/ogg"}).json()
    cfg = lambda: c.get("/api/client-config").json()["slice"]

    def until(cond):  # the WebSocket's messages are handled on its own loop
        end = time.time() + 5
        while not cond() and time.time() < end:
            time.sleep(0.01)
        assert cond()

    assert cfg() == NORMAL and up() == {"seconds": 2.0, "slice": NORMAL}
    with c.websocket_connect("/ws") as w:
        w.send_json({"type": "watch", "wake": True, "live": False})  # 今天's live card
        until(lambda: t.watch_until > time.time())
        assert cfg() == NORMAL and up()["slice"] == NORMAL, "drafts run, the pieces stay as they are"

        w.send_json({"type": "watch", "wake": False, "live": True})  # the live page
        until(lambda: t.live_until > time.time())
        assert cfg() == WATCHED and up() == {"seconds": 2.0, "slice": WATCHED}

    t.live_until = time.time() - 1  # its last watch was over a minute ago
    assert cfg() == NORMAL and up()["slice"] == NORMAL
