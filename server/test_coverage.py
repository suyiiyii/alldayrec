"""GET /api/coverage: a device's stored audio as merged spans, at most 31 days at once, from its own tenant's store only,
with first_bind: backfilled from the earlier of its first piece and its first connect event, set once and kept through
an unbind and later binds."""
import datetime as dt
import tempfile
from pathlib import Path

import anyio
import pytest
from fastapi import HTTPException

from alldayrec import db
from alldayrec import tenant
from alldayrec.routes_upload import DeviceEvent, coverage, device_log_upload

DEV = "D3200-0000"
T0 = 1_763_000_000_000  # unix ms
DAY = 86400_000


def piece(device, start_ms, seconds):
    db.conn().execute("INSERT INTO pieces (device, start_ms, seconds, path) VALUES (?, ?, ?, 'x')", (device, start_ms, seconds))


def app(seq, at, kind):
    return DeviceEvent(src="app:x", seq=seq, at=at, data={"kind": kind})


def get(device, since, until):
    return anyio.run(coverage, device, since, until)


def test_spans_merged_and_clipped():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    for s in range(10):  # 10 back-to-back 1 s pieces, a few ms apart
        piece(DEV, T0 + s * 1003, 1.0)
    piece(DEV, T0 + 10_030 + 900, 2.0)    # 0.9 s gap: the same span
    piece(DEV, T0 + 11_500, 0.5)          # inside the one before: once
    piece(DEV, T0 + 20_000, 5.0)          # 7 s gap: a span of its own
    piece(DEV, T0 - 3_000, 4.0)           # began before the range: clipped
    piece("LR-X1-0000", T0 + 1_000, 60.0)  # another device's
    got = get(DEV, T0, T0 + 24_000)
    assert got["spans"] == [[T0, T0 + 12_930], [T0 + 20_000, T0 + 24_000]]
    assert get(DEV, T0 + 13_000, T0 + 19_000)["spans"] == []


def test_range_capped():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    assert get(DEV, T0, T0 + 31 * DAY)["spans"] == []
    for since, until in ((T0, T0 + 31 * DAY + 1), (T0, T0), (T0, T0 - 1)):
        with pytest.raises(HTTPException) as e:
            get(DEV, since, until)
        assert e.value.status_code == 400


def test_first_bind_backfill_and_kept():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    piece(DEV, T0, 1.0)
    anyio.run(device_log_upload, "LR-X1-0000", [app(1, T0 - 9 * DAY, "connect")])  # another device's connect
    anyio.run(device_log_upload, DEV, [app(2, T0 - 2 * DAY, "bean_scan")])         # not a connect: no first_bind
    assert db.kv(f"first_bind:{DEV}") is None
    piece(DEV, T0 - DAY, 1.0)  # an older piece than the connect below: the earlier one wins
    anyio.run(device_log_upload, DEV, [app(3, T0 - DAY // 2, "connect")])
    assert db.kv(f"first_bind:{DEV}") == T0 - DAY
    anyio.run(device_log_upload, DEV, [app(4, T0 - 5 * DAY, "connect"), app(5, T0 + DAY, "unbind")])  # older, then an unbind
    piece(DEV, T0 - 7 * DAY, 1.0)
    assert get(DEV, T0, T0 + DAY)["first_bind"] == T0 - DAY, "set once: neither older data nor an unbind moves it"

    piece("LR-X1-0001", T0 - 3 * DAY, 1.0)  # an old device nobody asked about yet: its first piece
    assert get("LR-X1-0001", T0, T0 + DAY)["first_bind"] == T0 - 3 * DAY
    anyio.run(device_log_upload, "LR-X1-0002", [app(6, T0 - 4 * DAY, "connect")])  # bound, no audio yet: the connect
    assert get("LR-X1-0002", T0, T0 + DAY)["first_bind"] == T0 - 4 * DAY
    before = int(dt.datetime.now().timestamp() * 1000)
    assert get("LR-X1-0003", T0, T0 + DAY)["first_bind"] >= before, "nothing of it yet: now"


def test_other_tenant_sees_nothing(tmp_path):
    a, b = (tenant.Tenant(i, i, tmp_path / i, tmp_path / i) for i in "ab")
    for t in (a, b):
        t.data.mkdir()
        tenant.call(t, db.init)
    tenant.call(b, lambda: (piece(DEV, T0, 60.0), anyio.run(device_log_upload, DEV, [app(1, T0 - DAY, "connect")])))
    got = tenant.call(a, get, DEV, T0, T0 + DAY)
    assert got["spans"] == [] and got["first_bind"] > T0, "a's own: no audio, its own first bind (now)"
    assert tenant.call(b, get, DEV, T0, T0 + DAY) == {"first_bind": T0 - DAY, "spans": [[T0, T0 + 60_000]]}
