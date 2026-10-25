"""The piece lengths the apps get, from /api/client-config and with every upload's answer: the configured ones, and
while a page watches the background and screen-off ones at most SLICE_WATCHED_S (the foreground's stays)."""
import time

from starlette.testclient import TestClient

from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import server

NORMAL = {"foreground": 1, "background": 20, "screenOff": 60}
WATCHED = {"foreground": 1, "background": 2, "screenOff": 2}


def test_slices(tmp_path, monkeypatch):
    t = db.open_db(tmp_path / "live.db")  # one tenant without a token: no auth
    monkeypatch.setattr(core, "TENANTS", [t])
    monkeypatch.setattr(audio, "save", lambda *a: 2.0)  # a piece of 2 s, not decoded
    c = TestClient(server.app)  # no `with`: the lifespan does not start
    up = lambda: c.post("/api/chunks", params={"device": "phone", "start": 1_000}, content=b"x", headers={"content-type": "audio/ogg"}).json()

    assert c.get("/api/client-config").json() == {"slice": NORMAL}
    assert up() == {"seconds": 2.0, "slice": NORMAL}

    t.watch_until = time.time() + 60  # a page in front (ws.py)
    assert c.get("/api/client-config").json() == {"slice": WATCHED}
    assert up() == {"seconds": 2.0, "slice": WATCHED}

    t.watch_until = time.time() - 1  # its last watch was over a minute ago
    assert c.get("/api/client-config").json() == {"slice": NORMAL}
    assert up()["slice"] == NORMAL
