"""Listening in on a device (/api/listen): the newest piece first, then each newer one once, never older audio
(a backlog arriving) nor more than LISTEN_BACK_S behind the newest, one device at a time, and only with the token.
Through the app as the page calls it, auth middleware included."""
import datetime as dt
import io
import subprocess
import tempfile
import wave
from pathlib import Path

import anyio

from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import server

T = int(dt.datetime(2026, 10, 6, 10, 0).timestamp() * 1000)
tone = lambda s, fmt="ogg", codec="libopus": subprocess.run(
    ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:d={s}:r=16000", "-c:a", codec, "-f", fmt, "pipe:1"],
    capture_output=True, check=True).stdout


def get(query: str, headers: dict[str, str] | None = None) -> tuple[int, dict[str, str], bytes]:
    out = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        out.append(m)
    anyio.run(server.app, {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
                            "path": "/api/listen", "raw_path": b"/api/listen", "query_string": query.encode(), "root_path": "",
                            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
                            "client": ("127.0.0.1", 1), "server": ("test", 80)}, receive, send)
    start = next(m for m in out if m["type"] == "http.response.start")
    return start["status"], {k.decode(): v.decode() for k, v in start["headers"]}, b"".join(m.get("body", b"") for m in out)


def seconds(body: bytes) -> float:
    with wave.open(io.BytesIO(body)) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1
        return w.getnframes() / w.getframerate()


def test_listen(monkeypatch):
    t = db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    monkeypatch.setattr(core, "TENANTS", [t])  # no token: no auth
    assert get("device=phone")[0] == 204, "no audio yet"
    for i in range(3):  # 10:00:00, 01, 02: one second each, as the apps send them in front
        audio.save("phone", T + i * 1000, tone(1), "audio/ogg")

    status, h, body = get("device=phone")
    assert status == 200 and h["content-type"] == "audio/wav" and (h["x-start"], h["x-after"]) == (str(T + 2000),) * 2, h
    assert 0.9 < seconds(body) < 1.1, "first: the newest piece only"
    assert get(f"device=phone&after={T + 2000}")[0] == 204, "nothing newer"

    audio.save("phone", T + 3000, tone(1), "audio/ogg")
    audio.save("phone", T + 4000, tone(1), "audio/ogg")
    audio.save("phone", T - 600_000, tone(1), "audio/ogg")  # a backlog piece arriving late
    audio.save("other", T + 5000, tone(1), "audio/ogg")
    status, h, body = get(f"device=phone&after={T + 2000}")
    assert (h["x-start"], h["x-after"]) == (str(T + 3000), str(T + 4000)) and 1.9 < seconds(body) < 2.1, "both newer ones, back to back"

    audio.save("phone", T + 60_000, tone(20, "adts", "aac"), "audio/aac", n=20 * 16000)  # an iPhone piece, 55 s later
    status, h, body = get(f"device=phone&after={T + 4000}")
    assert h["x-start"] == str(T + 60_000) and 19.9 < seconds(body) <= 20, "the AAC piece decoded; a page far behind skips ahead"

    t.token = "s3cret"
    assert get("device=phone")[0] == 401, "no token: refused like every other API"
    assert get("device=phone", {"Authorization": "Bearer wrong"})[0] == 401
    assert get("device=phone", {"Cookie": "token=s3cret"})[0] == 200
    assert get("device=phone", {"Authorization": "Bearer s3cret"})[0] == 200
