"""How a device's audio sounded and arrived: /api/device-log brings it by the minute, the status the last 10 min
and the newest piece's lag, and an upload that cannot be decoded is logged and its bytes kept before the 400."""
import datetime as dt
import logging
import subprocess
import tempfile
from pathlib import Path

import anyio
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import audio
import db
from monitor import sound
from routes_upload import chunk, device_log
from tenant import T


def test_sound(caplog):
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    tone = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=30", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                          capture_output=True, check=True).stdout
    t0 = dt.datetime.now().replace(second=0, microsecond=0) - dt.timedelta(minutes=30)
    ms = lambda t: int(t.timestamp() * 1000)
    for m in (0, 1, 20):
        assert audio.save("LR-P3-92D2", ms(t0 + dt.timedelta(minutes=m)), tone, "audio/ogg") > 29
    db.conn().execute("UPDATE pieces SET zero_ms = 2500, full = 7 WHERE start_ms = ?", (ms(t0 + dt.timedelta(minutes=20)),))

    got = anyio.run(device_log, "LR-P3-92D2", t0.isoformat())["audio"]
    assert [r["t"] for r in got] == [(t0 + dt.timedelta(minutes=m)).isoformat() for m in (0, 1, 20)], got
    assert got[2]["zero_s"] == 2.5 and got[2]["full"] == 7 and got[0]["s"] > 29 and got[0]["lag_s"] > 60, got
    assert anyio.run(device_log, "LR-P3-92D2", t0.isoformat(), (t0 + dt.timedelta(minutes=1)).isoformat())["audio"] == got[:1]

    end = t0 + dt.timedelta(minutes=20, seconds=30)
    assert sound("LR-P3-92D2", end) == {"s": 30.0, "zero_s": 2.5, "full": 7}, "the last 10 min of its audio only"
    assert sound("LR-P3-92D2", None) is None and sound("nobody", end) is None

    async def body():
        return {"type": "http.request", "body": b"not audio", "more_body": False}
    req = Request({"type": "http", "method": "POST", "headers": [(b"content-type", b"audio/ogg")], "query_string": b""}, body)
    with caplog.at_level(logging.WARNING, "live"), pytest.raises(HTTPException) as e:
        anyio.run(chunk, req, "LR-P3-92D2", ms(end))
    assert e.value.status_code == 400 and "could not be decoded" in caplog.text
    assert (T().recordings / f"rejected/LR-P3-92D2/{ms(end)}.ogg").read_bytes() == b"not audio"
