"""metrics() is Prometheus text VictoriaMetrics takes (names, escaped labels, numbers), names no phone and says nothing
said; metrics_loop pushes it with basic auth, and sends nothing without VM_INGEST_URL. loop_stalls logs a route that
blocks the event loop."""
import base64
import contextlib
import datetime as dt
import logging
import re
import tempfile
import time
from pathlib import Path

import anyio
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from alldayrec import asr
from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import monitor
from alldayrec import pipeline_state
from alldayrec.tenant import T

LINE = re.compile(r'[a-zA-Z_:][a-zA-Z0-9_:]*\{[a-zA-Z_]\w*="(?:[^"\\\n]|\\[\\"n])*"(?:,[a-zA-Z_]\w*="(?:[^"\\\n]|\\[\\"n])*")*\} '
                  r'-?\d+(?:\.\d+)?(?:e[-+]?\d+)?')


def test_status_latency():
    """The status line says how far behind the newest sentence came up (a page's drafts while it watches, else the final
    rows'), the median of the last 10 beside it: a catch-up's late rows after a restart no longer hold it up (10-24: 54.5 s
    of a 50-row median while the drafts came 4.5 s after)."""
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    st = pipeline_state.PipelineState(latency=[54.5, 50.0, 30.0, 4.5], final_latency=[70.0, 12.0], drafting=True)
    d = monitor.device_status("LR-P3-116C", st)
    assert (d["latency_s"], d["latency_med_s"], d["final_latency_s"]) == (4.5, 50.0, 12.0)
    st.drafting = False  # no page watches: no drafts, the final rows'
    d = monitor.device_status("LR-P3-116C", st)
    assert (d["latency_s"], d["latency_med_s"], d["final_latency_s"]) == (12.0, 70.0, 12.0)


def test_metrics(monkeypatch):
    t = db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    t.id = "main"
    monkeypatch.setattr(core, "TENANTS", [t])
    monkeypatch.setattr(monitor, "STARTED", (dt.datetime.now() - dt.timedelta(seconds=1)).isoformat())
    db.add_call("final", "stepfun", "LR-P3-116C", 4000, None, 300.0, 200.0)
    db.add_call("final", "stepfun", "LR-P3-116C", 9000, "RuntimeError: stepfun sse HTTP 402: balance", 300.0, 200.0)
    db.add_call("summary", "m", None, 2000, "TimeoutError: timed out")
    now = dt.datetime.now()
    ms = int(now.timestamp() * 1000)
    db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path, received_ms, rms, hum, zero_ms, full) "
                      "VALUES ('LR-P3-116C', ?, 60, 'x', ?, -30, -60, 500, 3)", (ms - 61_500, ms))
    db.add_device_status("LR-P3-116C", (now - dt.timedelta(seconds=30)).isoformat(timespec="milliseconds"),
                         {"battery": 80, "charge": "charging", "fw": 'a"b\\c\nd'})
    audio.hold("某人的 iPhone", ms)
    st = pipeline_state.PipelineState(audio_end=now - dt.timedelta(seconds=2), final_latency=[4.0])
    T().devices.update({"LR-P3-116C": st, "某人的 iPhone": pipeline_state.PipelineState()})
    monkeypatch.setitem(core.state, "commit", "abc1234")
    for h, cny in ((24, 27.3), (0, 24.1)):  # ¥3.2 a day: 7.5 days left
        db.conn().execute("INSERT INTO balances VALUES (?, 'stepfun', ?)", ((now - dt.timedelta(hours=h)).isoformat(timespec="seconds"), cny))
    text = monitor.metrics()
    lines = text.splitlines()
    assert text.endswith("\n") and all(LINE.fullmatch(x) for x in lines), [x for x in lines if not LINE.fullmatch(x)]
    assert all('instance="main"' in x for x in lines)
    got = {x.rsplit(" ", 1)[0]: float(x.rsplit(" ", 1)[1]) for x in lines}
    assert got['lr_up{instance="main",commit="abc1234"}'] == 1
    dev = lambda name, d="LR-P3-116C": got.get(f'lr_{name}{{instance="main",device="{d}"}}')
    assert (dev("behind_seconds") // 1, dev("latency_seconds"), dev("backlog_seconds"), dev("queued_pieces")) == (2, 4, 60, 1)
    assert (dev("hold_active"), dev("recording"), dev("arrival_lag_seconds")) == (0, 1, 1.5)
    assert (dev("sound_seconds"), dev("sound_zero_seconds"), dev("sound_full_samples")) == (60, 0.5, 3)
    assert (dev("device_battery_percent"), dev("device_charge")) == (80, 1) and 25 < dev("device_last_seen_seconds") < 60
    assert got['lr_device_info{instance="main",device="LR-P3-116C",firmware="a\\"b\\\\c\\nd"}'] == 1, "escaped"
    assert "iPhone" not in text and "某人" not in text, "a phone by a hash of its name"
    phone = re.search(r'device="(phone-[0-9a-f]{8})"', text)[1]
    assert dev("hold_active", phone) == 1 and dev("behind_seconds", phone) is None and dev("device_battery_percent", phone) is None
    assert got['lr_summaries_pending{instance="main"}'] == 0
    assert got['lr_calls_total{instance="main",kind="final",engine="stepfun",result="ok"}'] == 1
    assert got['lr_calls_total{instance="main",kind="final",engine="stepfun",result="402"}'] == 1
    assert got['lr_calls_total{instance="main",kind="summary",engine="m",result="ok"}'] == 0
    assert got['lr_calls_total{instance="main",kind="summary",engine="m",result="TimeoutError"}'] == 1
    assert got['lr_call_audio_seconds_total{instance="main",kind="final",engine="stepfun"}'] == pytest.approx(400, abs=36)
    assert got['lr_call_latency_seconds{instance="main",kind="final",engine="stepfun",quantile="0.5"}'] == 4
    assert (got['lr_stepfun_balance_cny{instance="main"}'], got['lr_stepfun_days_left{instance="main"}']) == (24.1, 7.5)

    sent = []
    async def http(url, data, headers):
        sent.append((url, data, headers))
    monkeypatch.setattr(asr, "_http", http)

    async def once(url):  # the loop's first minute
        monkeypatch.setattr(monitor, "VM_INGEST_URL", url)
        with anyio.move_on_after(0.5) as scope:
            await monitor.metrics_loop()
        assert scope.cancelled_caught, "the loop goes on"
    anyio.run(once, "")
    assert sent == [], "no VM_INGEST_URL: nothing sent"
    monkeypatch.setenv("VM_INGEST_USER", "lr")
    monkeypatch.setenv("VM_INGEST_PASSWORD", "pw")
    anyio.run(once, "http://vm/api/v1/import/prometheus")
    (url, data, headers), = sent
    assert url == "http://vm/api/v1/import/prometheus" and data.startswith(b"lr_up{")
    assert headers["Authorization"] == "Basic " + base64.b64encode(b"lr:pw").decode()


def test_loop_stalls(monkeypatch, caplog):
    """LOOP_STALL_MS set: a route that holds the event loop 0.3 s is one line, how long and where (its own frame)."""
    monkeypatch.setenv("LOOP_STALL_MS", "100")

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with anyio.create_task_group() as tg:
            tg.start_soon(monitor.loop_stalls)
            yield
            tg.cancel_scope.cancel()
    app = FastAPI(lifespan=lifespan)

    @app.get("/slow")
    async def slow():
        time.sleep(0.3)
        return {}
    with caplog.at_level(logging.WARNING, "live"), TestClient(app) as c:
        for _ in range(2):  # a line a second: another stall just before (the start, a busy runner) would hide one
            time.sleep(1.1)
            assert c.get("/slow").status_code == 200
        time.sleep(0.2)  # written once the loop beats again
    lines = [r.getMessage() for r in caplog.records if " slow" in r.getMessage()]
    assert lines and all(re.search(r"held up \d+ ms.*: .*test_metrics.py:\d+ slow$", x) for x in lines), lines
    assert all(270 <= int(re.search(r"held up (\d+) ms", x)[1]) < 3000 for x in lines), lines
