"""perf.py keeps what the server spends: each HTTP request in its minute's row per route (a slow one in its own row
too), the pipelines' pieces, a day of it read back by GET /api/perf and `python -m alldayrec.perf export`, and the stall
lines of an old log imported once. (The stall rows and the minute's sample: test_metrics.py.)"""
import datetime as dt
import json
import subprocess
import sys
import time

from starlette.testclient import TestClient

from alldayrec import core
from alldayrec import db
from alldayrec import perf
from alldayrec import routes_api
from alldayrec import server
from alldayrec.tenant import T


def test_requests(tmp_path, monkeypatch):
    t = db.open_db(tmp_path / "live.db")  # one tenant without a token: no auth
    monkeypatch.setattr(core, "TENANTS", [t])
    monkeypatch.setattr(perf, "SLOW_MS", 300)  # far from what a fast route takes on a slow runner
    monkeypatch.setattr(routes_api, "status", lambda: (time.sleep(0.35), {"x": 1})[1])
    c = TestClient(server.app)  # no `with`: the lifespan does not start
    for _ in range(3):
        assert c.get("/api/lang").status_code == 200
    assert c.get("/api/status").json() == {"x": 1}
    assert c.get("/api/conversation/c20261024120000").status_code == 404
    assert c.get("/api/health").status_code == 200
    rows = lambda: [dict(r) for r in db.conn().execute("SELECT * FROM perf_requests ORDER BY rowid")]
    (slow,) = rows()
    assert (slow["method"], slow["route"], slow["status"], slow["n"], slow["bytes"]) == ("GET", "/api/status", 200, None, len('{"x":1}'))
    assert slow["ms"] >= perf.SLOW_MS, slow

    perf.flush()
    assert T().requests == {}
    got = {(r["method"], r["route"], r["status"]): r for r in rows()[1:]}
    assert set(got) == {("GET", "/api/lang", 200), ("GET", "/api/status", 200), ("GET", "/api/conversation/{conv_id}", 404)}, \
        "by the route's template, never the path; no /api/health"
    lang = got[("GET", "/api/lang", 200)]
    assert lang["n"] == 3 and lang["p50"] <= lang["p95"] <= lang["ms"] < perf.SLOW_MS and lang["bytes"] == 3 * len('{"lang":"zh"}'), lang
    assert got[("GET", "/api/status", 200)]["n"] == 1


def test_pieces_and_read_out(tmp_path, monkeypatch):
    t = db.open_db(tmp_path / "live.db")
    monkeypatch.setattr(core, "TENANTS", [t])
    acc = {}
    with perf.timed(acc, "vad_ms"):
        time.sleep(0.01)
    perf.add(acc, fed_s=20.0, decode_ms=30.0)
    perf.piece("LR-P3-TEST", "final", 15.0, 2, acc)
    assert acc == {}, "emptied for the layer's next piece"
    old = (dt.date.today() - dt.timedelta(days=perf.KEEP_DAYS + 1)).isoformat()
    db.conn().execute("INSERT INTO perf_pieces (at, device, layer) VALUES (?, 'LR-P3-TEST', 'final')", (old + "T12:00:00.000",))
    monkeypatch.setitem(perf._last, "pruned", "")
    perf.sample(0, 0)  # the minute: its row, and once a day the rows of KEEP_DAYS ago dropped

    c = TestClient(server.app)
    assert c.get("/api/perf", params={"day": "24.10."}).status_code == 400
    got = c.get("/api/perf", params={"day": dt.date.today().isoformat()}).json()
    assert set(got) == {"stalls", "samples", "requests", "pieces"}
    (p,) = got["pieces"]
    assert (p["device"], p["layer"], p["piece_s"], p["sentences"], p["fed_s"], p["decode_ms"], p["asr_ms"]) == ("LR-P3-TEST", "final", 15.0, 2, 20.0, 30.0, None)
    assert 10 <= p["vad_ms"] < 100
    (s,) = got["samples"]
    assert s["cpu_s"] > 0 and s["threads"] is None, "no beats: no lag, no pools"
    assert c.get("/api/perf", params={"day": old}).json()["pieces"] == [], f"older than {perf.KEEP_DAYS} days: dropped"


LOG = """2026-10-24 22:22:33,315 live WARNING event loop held up 348 ms: server.py:203 <module> > socket.py:232 __init__
2026-10-24 22:22:34,379 live WARNING [main] event loop held up 217 ms (+2 more since the last line): device_pipeline.py:72 feed > infer.py:150 detect_chunk
Oct 24 22:23:01 box python[1]: 2026-10-24 22:23:01,002 live WARNING event loop held up 120 ms: selectors.py:566 select
2026-10-24 22:23:02,000 live INFO final LR-P3-TEST: {'ms': 3000}
2026-10-25 00:00:01,000 live WARNING event loop held up 101 ms: audio.py:1 f
"""


def test_import_and_export(tmp_path):
    db.open_db(tmp_path / "live.db")
    db.conn().execute(perf.STALL, ("2026-10-24T22:22:34.380", 217, "device_pipeline.py:72 feed > infer.py:150 detect_chunk", 2))  # the server's row of a line
    (tmp_path / "server.log").write_text(LOG)
    run = lambda *a: subprocess.run([sys.executable, "-m", "alldayrec.perf", *map(str, a)], capture_output=True, text=True, check=True).stdout
    assert run("import-stalls", tmp_path / "live.db", tmp_path / "server.log") == "3 rows added to perf_stalls\n"
    assert run("import-stalls", tmp_path / "live.db", tmp_path / "server.log") == "0 rows added to perf_stalls\n", "once"
    out = json.loads(run("export", tmp_path / "live.db", "2026-10-24"))
    assert {k: len(v) for k, v in out.items()} == {"stalls": 3, "samples": 0, "requests": 0, "pieces": 0}
    assert [(r["at"], r["ms"], r["suppressed"]) for r in out["stalls"]] == [
        ("2026-10-24T22:22:33.315", 348, 0), ("2026-10-24T22:22:34.380", 217, 2), ("2026-10-24T22:23:01.002", 120, 0)]
    assert out["stalls"][1]["frames"] == "device_pipeline.py:72 feed > infer.py:150 detect_chunk"
