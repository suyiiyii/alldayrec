"""Device log and status history: a record sent again is kept once, a range brings the recorder's undated records
that sit between its dated ones, every status report is kept with the newest one at hand, and firmware updates are put
together from the app's dfu events and the recorder's DFU and BOOT records."""
import datetime as dt
import tempfile
from pathlib import Path

import anyio

from alldayrec import db
from alldayrec.routes_upload import DeviceEvent, device_log, device_log_upload, device_status_report, upgrades

T = 1_700_000_000_000  # the phone's TIME write, epoch ms (lr_sync.py's selftest)
iso = lambda ms: dt.datetime.fromtimestamp(ms / 1000).isoformat(timespec="milliseconds")


def rec(seq, at, boot, t, code, x=0, a=0, b=0):
    return DeviceEvent(src="rec", seq=seq, at=at, data=dict(boot=boot, t=t, code=code, x=x, a=a, b=b))


# lr_sync.py's selftest log: boot 7's TIME fell out of the log (undated), boot 8's frames dated by its TIME at frame 1000
LOG = [rec(5000, None, 7, 123456, 22, 200, 2048, 7),
       rec(5001, T - 7000, 8, 1500, 1 | 0x80, 205, 0x4, 0x1abcdef | 1 << 28),  # uptime record: the phone put it at frame 0
       rec(5002, None, 8, 3200, 3 | 0x80, 1, (-5) & 0xFFFFFFFF),               # one it could not
       rec(5003, T - 6000, 8, 400, 9, 0, 24, 400),
       rec(5004, T, 8, 1000, 14, 0, T & 0xFFFFFFFF, T >> 32),
       rec(5005, T + 10_000, 8, 2000, 10, 0x13)]
APP = [DeviceEvent(src="app", seq=T + 1, at=T + 1, data={"kind": "connect", "ms": 3200}),
       DeviceEvent(src="app", seq=T + 9_000, at=T + 9_000, data={"kind": "upload_fail", "text": "HTTP 502"})]


def seqs(lo, hi=None):
    got = anyio.run(device_log, "LR-X1-567E", iso(lo), hi and iso(hi))
    return sorted((e["src"], e["seq"]) for e in got["events"])


def test_device_log():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    anyio.run(device_log_upload, "LR-X1-567E", LOG[:4] + APP)
    anyio.run(device_log_upload, "LR-X1-567E", LOG[2:])  # an overlapping read: 5002, 5003 again
    anyio.run(device_log_upload, "LR-P1-BDAE", [rec(5000, T, 1, 0, 22, 190)])  # another recorder, same seq
    assert db.conn().execute("SELECT count(*) FROM device_log").fetchone()[0] == 9

    every = [("app", T + 1), ("app", T + 9_000)] + [("rec", s) for s in range(5000, 5006)]
    assert seqs(T - 60_000) == every
    assert seqs(T - 60_000, T + 60_000) == every
    assert seqs(T + 5_000) == [("app", T + 9_000), ("rec", 5005)], "dated before the range: no undated ones"
    assert seqs(T - 3_600_000, T - 6500) == [("rec", 5000), ("rec", 5001), ("rec", 5002)], \
        "the undated ones up to the first dated record after the range"
    e = next(e for e in anyio.run(device_log, "LR-X1-567E", iso(T))["events"] if e["seq"] == 5004)
    assert (e["at"], e["data"]["a"], e["data"]["b"]) == (iso(T), T & 0xFFFFFFFF, T >> 32)

    anyio.run(device_log_upload, "LR-X1-567E", [rec(5005, T + 20_000, 9, 50, 22, 180)])  # the log was erased: seq again
    assert next(e for e in anyio.run(device_log, "LR-X1-567E", iso(T))["events"] if e["seq"] == 5005)["data"]["boot"] == 9


def test_device_status():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    assert db.device_status("LR-X1-567E") is None
    lo = (dt.datetime.now() - dt.timedelta(seconds=1)).isoformat()
    for battery in (80, 79):
        anyio.run(device_status_report, "LR-X1-567E", {"battery": battery, "store": 0, "model": "LR-X1"})
    anyio.run(device_status_report, "phone", {"battery": 50})
    hist = anyio.run(device_log, "LR-X1-567E", lo)["status"]
    assert [h["battery"] for h in hist] == [80, 79] and hist[0]["at"] <= hist[1]["at"]
    assert db.device_status("LR-X1-567E") == hist[-1]


def test_upgrades():
    db.open_db(Path(tempfile.mkdtemp()) / "t.db")
    M = 60_000
    boot = lambda seq, at, fw, dirty=0: rec(seq, at, seq, 0, 1 | 0x80, 200, 0x4, int(fw, 16) | dirty << 28)
    dfu = lambda seq, at: rec(seq, at, seq - 1, 9000, 21, 0xA8)
    app = lambda at, step, **f: DeviceEvent(src="app:a", seq=at, at=at, data={"kind": "dfu", "step": step, "text": step, **f})
    old, new = "20261006.120000-1751f88", "20261007.023223-c95516b"
    anyio.run(device_log_upload, "LR-P3-116C", [
        # 10-07 10:54 with app 1.4.0, which logged nothing: the recorder's DFU and the BOOT 3 min 12 s later
        boot(1, T - 60 * M, "1751f88"), dfu(2, T), boot(3, T + 192_000, "c95516b"),
        # an app's update: done, and back on the new revision
        app(T + 10 * M, "start", **{"from": "c95516b", "to": new, "bytes": 343133}), dfu(4, T + 10 * M + 9000),
        app(T + 10 * M + 15_000, "boot", ms=15_000), app(T + 10 * M + 25_000, "send", ms=25_000),
        app(T + 10 * M + 70_000, "progress", ms=70_000, pct=25, kbps=1.9), app(T + 10 * M + 180_000, "done", ms=180_000),
        boot(5, T + 10 * M + 183_000, "c95516b", 1), app(T + 10 * M + 210_000, "back", ms=30_000, fw=new, ok=True),
        # failed midway: the bootloader gave up and ran the old firmware again
        app(T + 20 * M, "start", **{"from": new, "to": old}), dfu(6, T + 20 * M + 9000),
        app(T + 20 * M + 60_000, "fail", ms=60_000, stage="send", pct=40, code=4102, error="Device disconnected"),
        boot(7, T + 20 * M + 70_000, "c95516b"),
        # done by the library, yet back on another revision
        app(T + 30 * M, "start", **{"from": new, "to": old}), dfu(8, T + 30 * M + 9000), app(T + 30 * M + 90_000, "done", ms=90_000),
        boot(9, T + 30 * M + 92_000, "c95516b"), app(T + 30 * M + 120_000, "back", ms=30_000, fw=new, ok=False),
        # the app was killed: the recorder's BOOT tells
        app(T + 40 * M, "start", **{"from": new, "to": old}), dfu(10, T + 40 * M + 9000), boot(11, T + 40 * M + 200_000, "1751f88"),
        # under way: nothing after the DFU yet
        app(T + 50 * M, "start", **{"from": old, "to": new}), dfu(12, T + 50 * M + 9000),
    ])
    anyio.run(device_log_upload, "LR-P3-2420", [  # no app: USB flashes (UF2, 0x57) of the same and of a new build
        boot(1, T - 60 * M, "28758c4"), rec(2, T, 1, 9000, 21, 0x57), boot(3, T + 60_000, "28758c4"),
        rec(4, T + 10 * M, 3, 9000, 21, 0x57), boot(5, None, "1751f88")])  # a BOOT no phone dated
    got = anyio.run(upgrades, "LR-P3-116C", iso(T - M))
    pick = lambda u: (u["app"], u["from"], u["to"], u["ms"], u["ok"])
    assert [pick(u) for u in got] == [
        (False, "1751f88", "c95516b", 192_000, True),
        (True, "c95516b", new, 180_000, True),
        (True, new, old, 60_000, False),
        (True, new, old, 90_000, False),
        (True, new, old, 200_000, True),
        (True, old, new, None, None)]
    assert (got[0]["at"], got[0]["dfu"], got[0]["boot"]) == (iso(T), iso(T), iso(T + 192_000))
    assert (got[1]["back_ms"], got[1]["fw"]) == (30_000, new)
    assert (got[2]["why"], got[2]["stage"], got[2]["pct"]) == ("Device disconnected", "send", 40)
    assert got[3]["why"] == f"升级后固件是 {new}，不是 {old}"
    assert [pick(u) for u in anyio.run(upgrades, "LR-P3-116C", iso(T + 10 * M + 1))][:2] == [
        (False, "c95516b", "c95516b+", 174_000, True), (True, new, old, 60_000, False)], \
        "an update begun before since: its DFU stands alone (the BOOT after it from a dirty tree), its app events left out"
    assert {u["via"] for u in got if "via" in u} == {"ota"}
    assert [(u["from"], u["to"], u["ms"], u["ok"], u["why"], u["via"]) for u in anyio.run(upgrades, "LR-P3-2420", iso(T - M))] == [
        ("28758c4", "28758c4", 60_000, None, "重启后版本没变", "usb"), ("28758c4", "1751f88", None, True, None, "usb")]
    assert anyio.run(upgrades, "LR-X1-567E", iso(T - M)) == []
