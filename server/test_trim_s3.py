"""trim.py on sealed hours (STORAGE=s3, a real MinIO: test_store_s3's): the kept pieces go into an object of their
own, the original object stays till purge; a merge or a late piece meanwhile leaves the hour for the next run; a crash
after the upload, before the transaction, or in the purge, loses nothing and the next run finishes."""
import datetime as dt
import io
import json
import subprocess
import time

import pytest

from alldayrec import audio, db, infer, seal, store, trim
from test_backlog import EnergyVad
from test_trim import purge
from test_store_s3 import H10, HOUR, rows, s3, tone  # noqa: F401  (s3: the fixture)

N = 6  # 10 s pieces of 10:00: a tone in the first, silence in the rest


def silence(s: float) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=mono:d={s}", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                          capture_output=True, check=True).stdout


def sealed(monkeypatch) -> dict:
    """The hour uploaded long ago, sealed, finalized; its VAD."""
    monkeypatch.setattr(infer, "StreamVad", EnergyVad)
    for i in range(N):
        assert audio.save("phone", H10 + i * 10_000, tone(10) if i == 0 else silence(10), "audio/ogg") > 9.9
    db.conn().execute("UPDATE pieces SET received_ms = start_ms")
    seal.step()
    assert {p["path"] for p in rows()} == {"s3:" + HOUR}
    db.put_kv("finalized_to:phone", dt.datetime.fromtimestamp(H10 / 1000 + 3600).isoformat())
    out = io.StringIO()
    trim.vad((0, 1), int(time.time() * 1000), out)
    return {v["path"]: v for v in map(json.loads, out.getvalue().splitlines())}


def cut(a, b):
    return audio.cut(dt.datetime.fromtimestamp(H10 / 1000 + a), dt.datetime.fromtimestamp(H10 / 1000 + b), "s16le", "phone")


def plan(v):
    """The plan of the hour's object (not its staging file's)."""
    return [h for h in trim.plan(v, int(time.time() * 1000)) if h["path"] == "s3:" + HOUR]


def test_trim_sealed_hour(s3, monkeypatch):
    v = sealed(monkeypatch)
    before, size = cut(0, 15), seal.row(HOUR)["size"]
    (h,) = plan(v)
    k = [round((r["start_ms"] - H10) / 10_000) for r in h["kept"]]
    assert h["reason"] is None and k[:2] == [0, 1] and k[2:] in ([], [2]), k  # the tone ± 10 s (and the VAD's hangover)
    assert trim.apply_one(h)
    assert {p["path"] for p in rows()} == {"s3:" + trim.copy_of(HOUR)} and len(rows()) == len(k)
    assert cut(0, 15) == before and store.s3().head(HOUR)[0] == size, "kept plays as before; the original stays till purge"
    assert store.s3().head(trim.copy_of(HOUR))[0] == sum(r["size"] for r in h["kept"])
    assert purge() == {"purged": 1, "kept": 0, "waiting": 0}
    assert store.s3().head(HOUR) is None and seal.row(HOUR) is None and cut(0, 15) == before
    # a late piece of 10:00 after the purge: a new hour file, sealed into the key the original left
    assert audio.save("phone", H10 + 70_000, tone(2), "audio/ogg") > 1.9
    seal.step()
    assert {p["path"] for p in rows()} == {"s3:" + trim.copy_of(HOUR), "s3:" + HOUR} and seal.row(HOUR)["state"] == "sealed"


def test_staged_or_merged_meanwhile(s3, monkeypatch):
    """A late piece staged after the plan: left (being merged); merged: left too (the object is another version)."""
    v = sealed(monkeypatch)
    (h,) = plan(v)
    assert audio.save("phone", H10 + 65_000, tone(2), "audio/ogg") > 1.9  # into the staging file
    assert not trim.apply_one(h) and plan(v)[0]["reason"] == "being merged"
    seal.merge(HOUR)
    db.conn().execute("UPDATE pieces SET received_ms = start_ms")
    assert not trim.apply_one(h), "planned on the version before the merge"
    trim.leftovers()
    assert plan(v)[0]["reason"] == "late span open", "the late piece is to be transcribed first"
    db.conn().execute("UPDATE late_spans SET status = 'done'")
    (h,) = plan(v)
    assert h["reason"] is None and trim.apply_one(h) and H10 + 65_000 in {r["start_ms"] for r in rows()}, "the late piece: not heard, kept"


@pytest.mark.parametrize("next_run", ["leftovers", "apply again"])
def test_crash_after_upload(s3, monkeypatch, next_run):
    """Killed after the copy's PUT, before its transaction: the rows still point at the original (which reads as
    before); the next run deletes the copy, or writes it again over its own leftover, and trims."""
    v = sealed(monkeypatch)
    before = cut(0, 15)

    def die(rows):
        raise KeyboardInterrupt
    snapshot = trim.snapshot
    monkeypatch.setattr(trim, "snapshot", die)  # the transaction's last statement: after the rows were moved
    with pytest.raises(KeyboardInterrupt):
        trim.apply_one(plan(v)[0])
    monkeypatch.setattr(trim, "snapshot", snapshot)
    assert {p["path"] for p in rows()} == {"s3:" + HOUR} and len(rows()) == N and cut(0, 15) == before
    assert store.s3().head(trim.copy_of(HOUR)) is not None, "the copy landed"
    if next_run == "leftovers":
        trim.leftovers()
        assert store.s3().head(trim.copy_of(HOUR)) is None
    assert trim.apply_one(plan(v)[0]) and {p["path"] for p in rows()} == {"s3:" + trim.copy_of(HOUR)} and cut(0, 15) == before


def test_purge_crash(s3, monkeypatch):
    """Killed in the purge after its transaction (the original's store_objects row gone), before the DELETE: a late
    piece meanwhile cannot be sealed over the original (refused), and the next purge deletes it; then it can."""
    v = sealed(monkeypatch)
    assert trim.apply_one(plan(v)[0])
    delete = store.S3.delete

    def die(self, path):
        raise KeyboardInterrupt
    monkeypatch.setattr(store.S3, "delete", die)
    with pytest.raises(KeyboardInterrupt):
        purge()
    monkeypatch.setattr(store.S3, "delete", delete)
    assert db.conn().execute("SELECT state FROM trims").fetchone()[0] == "purging" and seal.row(HOUR) is None
    assert audio.save("phone", H10 + 70_000, tone(2), "audio/ogg") > 1.9
    original = store.s3().head(HOUR)
    seal.step()
    assert seal.row(HOUR)["state"] == "sealing" and store.s3().head(HOUR) == original, "the original is not overwritten"
    assert purge() == {"purged": 1, "kept": 0, "waiting": 0} and store.s3().head(HOUR) is None
    seal.T().store_retry.clear()
    seal.step()
    assert seal.row(HOUR)["state"] == "sealed" and store.s3().head(HOUR)[0] == seal.row(HOUR)["size"] < original[0], "the late piece's own hour file"
    assert [p["path"] for p in rows()][-1] == "s3:" + HOUR
