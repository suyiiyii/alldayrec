"""Historical imports report stored audio progress, independently of recording dates."""
import datetime as dt
import time

import anyio
import pytest

from alldayrec import db, monitor, routes_api
from alldayrec.pipeline_state import PipelineState


@pytest.mark.parametrize("device", ["import", "import-synthetic"])
def test_import_status_audio_progress(tmp_path, device):
    t = db.open_db(tmp_path / "t.db")
    start = dt.datetime(2026, 1, 1, 10)
    ms = int(start.timestamp() * 1000)
    now = time.time()
    # Two minutes, an overlapping duplicate, then one minute a day later.
    for offset, seconds in [(0, 120), (60, 60), (86400, 60)]:
        db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path, received_ms) VALUES (?, ?, ?, 'x', ?)",
                          (device, ms + offset * 1000, seconds, now * 1000))
    st = PipelineState(done_to=start + dt.timedelta(seconds=86430),
                       progress=[(now - 60, start + dt.timedelta(seconds=60)),
                                 (now, start + dt.timedelta(seconds=86430))])
    t.devices[device] = st
    d = anyio.run(routes_api.status_now)["devices"][0]
    assert not d["recording"], "an import uploaded now is still not a live recorder"
    assert d["import_progress"] == {"done_s": 150.0, "total_s": 180.0}
    assert d["left_s"] == 30.0
    assert d["rate"] == 1.5, "90 seconds of audio in 60 seconds; the day-long gap is not work"
    st.done_to = start + dt.timedelta(seconds=86460)
    d = monitor.device_status(device, st)
    assert d["import_progress"] == {"done_s": 180.0, "total_s": 180.0}
    assert d["eta_s"] is None


def test_import_eta_requires_recent_measured_audio_rate(tmp_path):
    db.open_db(tmp_path / "t.db")
    start, now = dt.datetime(2026, 1, 1, 10), time.time()
    db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path) VALUES ('import', ?, 600, 'x')",
                      (int(start.timestamp() * 1000),))
    st = PipelineState(done_to=start + dt.timedelta(seconds=120))
    assert monitor.device_status("import", st)["eta_s"] is None
    st.progress = [(now - 60, start), (now, st.done_to)]
    d = monitor.device_status("import", st)
    assert (d["rate"], d["eta_s"]) == (2.0, 240)
    st.progress = [(now - 720, start), (now - 660, st.done_to)]
    d = monitor.device_status("import", st)
    assert d["rate"] is None and d["eta_s"] is None, "a stalled import has no current estimate"


def test_live_status_keeps_delay(tmp_path):
    t = db.open_db(tmp_path / "t.db")
    now = time.time()
    start = dt.datetime.fromtimestamp(now - 12)
    db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path, received_ms) VALUES ('recorder-demo', ?, 10, 'x', ?)",
                      (int(start.timestamp() * 1000), now * 1000))
    t.devices["recorder-demo"] = PipelineState(audio_end=start + dt.timedelta(seconds=10))
    d = anyio.run(routes_api.status_now)["devices"][0]
    assert d.get("import_progress") is None
    assert d["recording"] and d["arrival"]["lag_s"] == 2.0
