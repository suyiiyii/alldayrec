"""firmware.py: uploads need the firmware token, a clean version and notes for people; each model has its own newest
package and its own list of versions, whose notes can be replaced."""
import io
import tempfile
import zipfile
from pathlib import Path

import anyio
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from alldayrec import firmware

C1, C2 = "a" * 40, "b" * 40


NOTES = "开关要拨稳 0.5 秒才算数"


def req(body: bytes, token: str) -> Request:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    return Request({"type": "http", "headers": [(b"x-firmware-token", token.encode())]}, receive)


def pkg() -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("manifest.json", "{}")
    return b.getvalue()


def up(model, version, commit, body=None, token="t", notes=NOTES):
    return anyio.run(firmware.upload, req(pkg() if body is None else body, token), model, version, commit,
                                       "2026-10-04T00:00:00Z", notes)


def edit(model, version, notes, token="t"):
    return anyio.run(firmware.edit, req(b"", token), model, version, notes)


def status(f) -> int:
    with pytest.raises(HTTPException) as e:
        f()
    return e.value.status_code


def test_firmware():
    firmware.DIR, firmware.TOKEN = Path(tempfile.mkdtemp()), "t"
    assert status(lambda: up("LR-X1", "20261004.010203-aaaaaaa", C1, token="x")) == 403
    assert status(lambda: up("LR-X1", "20261004-aaaaaaa", C1)) == 400          # the old revision format
    assert status(lambda: up("LR-X1", "20261004.010203-aaaaaaa+", C1)) == 400  # a dirty tree
    assert status(lambda: up("../x", "20261004.010203-aaaaaaa", C1)) == 400
    assert status(lambda: up("LR-X1", "20261004.010203-aaaaaaa", C2)) == 400   # commit not the version's
    assert status(lambda: up("LR-X1", "20261004.010203-aaaaaaa", C1, b"not a zip")) == 400
    assert status(lambda: up("LR-X1", "20261004.010203-aaaaaaa", C1, notes=" ")) == 400
    assert status(lambda: up("LR-X1", "20261004.010203-aaaaaaa", C1, notes="开关防抖 (#160)")) == 400  # an issue number
    assert anyio.run(firmware.versions, "LR-X1") == []
    assert status(lambda: anyio.run(firmware.latest, "LR-X1")) == 404
    up("LR-X1", "20261004.120000-bbbbbbb", C2)
    up("LR-X1", "20261004.010203-aaaaaaa", C1)  # uploaded later, older commit: not the newest
    assert status(lambda: up("LR-X1", "20261004.010203-aaaaaaa", C1)) == 409
    up("LR-P1", "20261005.000000-aaaaaaa", C1)
    m = anyio.run(firmware.latest, "LR-X1")
    assert (m["model"], m["version"], m["size"]) == ("LR-X1", "20261004.120000-bbbbbbb", len(pkg()))
    assert anyio.run(firmware.latest, "LR-P1")["version"] == "20261005.000000-aaaaaaa"
    assert Path(anyio.run(firmware.download, "LR-X1", m["version"]).path).read_bytes() == pkg()
    assert status(lambda: anyio.run(firmware.download, "LR-P1", m["version"])) == 404  # not the other model's
    vs = anyio.run(firmware.versions, "LR-X1")  # newest first, each with its notes
    assert [(v["version"], v["notes"]) for v in vs] == [("20261004.120000-bbbbbbb", NOTES), ("20261004.010203-aaaaaaa", NOTES)]
    assert [v["version"] for v in anyio.run(firmware.versions, "LR-P1")] == ["20261005.000000-aaaaaaa"]
    assert edit("LR-X1", "20261004.010203-aaaaaaa", "灯的含义改了")["notes"] == "灯的含义改了"
    assert anyio.run(firmware.versions, "LR-X1")[1]["notes"] == "灯的含义改了"
    assert anyio.run(firmware.versions, "LR-X1")[1]["sha256"] == vs[1]["sha256"]  # the package itself unchanged
    assert status(lambda: edit("LR-X1", "20261004.010203-aaaaaaa", "x", token="x")) == 403
    assert status(lambda: edit("LR-X1", "20261004.010203-aaaaaaa", "见 #148")) == 400
    assert status(lambda: edit("LR-X1", "20261009.000000-aaaaaaa", "x")) == 404
    firmware.TOKEN = ""
    assert status(lambda: up("LR-X1", "20261006.000000-aaaaaaa", C1, token="")) == 403  # no token set: no uploads
