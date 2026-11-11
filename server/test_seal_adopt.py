"""seal adopt (STORAGE=s3, a real MinIO: test_store_s3's): recordings from before the switch handed to seal(), which
seals them as any hour: played back the same bytes, rows outside their hour too, a file over one PUT kept local, a
crash at any step loses nothing, and never beside a trim apply or purge."""
import contextlib
import datetime as dt
import os
import sys
import time

import pytest

from alldayrec import audio, db, seal, store, tenant, trim
from alldayrec.tenant import T
from test_store_s3 import H10, HOUR, STAGED0, Crash, crash_once, keys, obj, rows, s3, same, sealed_hour, tone  # noqa: F401  (s3: the fixture)

H11 = "chunks/phone/2026-10-02/11.ogg"


def before_s3(monkeypatch, kept: dict):
    """kept (start_ms -> bytes) uploaded while STORAGE=local, long ago."""
    monkeypatch.setattr(store, "STORAGE", "local")
    for ms, body in kept.items():
        assert audio.save("phone", ms, body, "audio/ogg") > 0.9
    monkeypatch.setattr(store, "STORAGE", "s3")
    db.conn().execute("UPDATE pieces SET received_ms = start_ms")


def test_adopt_then_sealed(s3, monkeypatch):
    kept = {H10: tone(2, 300), H10 + 2000: tone(2, 400), H10 + 3600_000: tone(1, 500)}
    before_s3(monkeypatch, kept)
    now = int(time.time() * 1000)
    monkeypatch.setattr(store, "STORAGE", "local")
    audio.save("phone", now, tone(1), "audio/ogg")  # the hour now: still being written
    monkeypatch.setattr(store, "STORAGE", "s3")
    current = str(audio.hour_file("phone", now, "ogg"))
    play = lambda: audio.cut(dt.datetime.fromtimestamp(H10 / 1000), dt.datetime.fromtimestamp(H10 / 1000 + 3605), "s16le", "phone")
    before = play()
    seal.step()
    assert seal.row(HOUR) is None, "not sealed by itself"
    size = (T().recordings / HOUR).stat().st_size + (T().recordings / H11).stat().st_size
    assert seal.adopt(dry=True) == {"to adopt": 2, "bytes": size, "recent": 1, "missing": 0, "trim copy": 0, "not its hour": 0}
    assert seal.row(HOUR) is None, "dry: nothing changed"
    assert seal.adopt(limit=1)["adopted"] == 1 and seal.row(H11) is None
    assert seal.adopt()["adopted"] == 1
    o = obj(HOUR)
    assert (o["state"], o["lo_ms"], o["hi_ms"]) == ("open", H10 - 3600_000, H10 + 7200_000) and o["seal_at"] <= time.time() * 1000
    assert seal.row(current) is None
    seal.step()
    assert {p["path"] for p in rows() if p["start_ms"] < now} == {"s3:" + HOUR, "s3:" + H11}
    assert not (T().recordings / HOUR).exists() and not (T().recordings / H11).exists()
    assert (T().recordings / current).exists()
    same(kept | {now: audio.read(rows()[-1])})
    assert play() == before and len(before) > 3600 * audio.SR * 2, "played from the objects, as from the files"
    assert seal.adopt()["adopted"] == 0, "each once"


def test_adopt_leaves(s3, monkeypatch):
    """Not adopted: a past hour a piece just came to (SEAL_IDLE_S), a file gone from disk, a trim's copy its purge
    still checks."""
    before_s3(monkeypatch, {H10: tone(1), H10 + 3600_000: tone(1), H10 + 7200_000: tone(1)})
    monkeypatch.setattr(seal, "SEAL_IDLE_S", 600)
    db.conn().execute("UPDATE pieces SET received_ms = ? WHERE start_ms = ?", (int(time.time() * 1000) - 60_000, H10))
    (T().recordings / H11).unlink()
    copy = "chunks/phone/2026-10-02/12.ogg"
    db.conn().execute("INSERT INTO trims (path, copy, state, at) VALUES ('chunks/phone/2026-10-02/12.x.ogg', ?, 'trimmed', 0)", (copy,))
    assert seal.adopt() == {"adopted": 0, "bytes": 0, "recent": 1, "missing": 1, "trim copy": 1, "not its hour": 0}
    assert db.conn().execute("SELECT count(*) FROM store_objects").fetchone()[0] == 0


def test_adopt_rows_outside_their_hour(s3, monkeypatch):
    """A row in the file whose time is far from its hour (a clock set back, the time zone changed since): sealed
    with the rest, the file gone, its bytes the same."""
    kept = {H10: tone(2, 300), H10 + 2000: tone(2, 400)}
    before_s3(monkeypatch, kept)
    back = H10 - 3 * 3600_000
    db.conn().execute("UPDATE pieces SET start_ms = ? WHERE start_ms = ?", (back, H10 + 2000))
    kept[back] = kept.pop(H10 + 2000)
    assert seal.adopt()["adopted"] == 1
    assert not obj(HOUR)["lo_ms"] <= back <= obj(HOUR)["hi_ms"]
    seal.step()
    assert {p["path"] for p in rows()} == {"s3:" + HOUR} and not (T().recordings / HOUR).exists()
    same(kept)


def test_adopt_too_large(s3, monkeypatch):
    """A file over one PUT stays on local disk, its rows read from there, and is not adopted again."""
    kept = {H10: tone(1)}
    before_s3(monkeypatch, kept)
    monkeypatch.setattr(store, "PUT_MAX", 10)
    assert seal.adopt()["adopted"] == 1
    seal.step()
    assert obj(HOUR)["state"] == "local" and rows()[0]["path"] == HOUR and keys(s3) == []
    same(kept)
    assert seal.adopt()["adopted"] == 0


def crashing_commit(monkeypatch, sql: str):
    """db.tx's transactions that leave a row for sql roll back (a crash before the commit), the first time."""
    tx, calls = db.tx, []

    def failing(durable=False):
        @contextlib.contextmanager
        def cm():
            with tx(durable) as t:
                yield t
                if not calls and t.execute(sql).fetchone():
                    calls.append(1)
                    raise Crash()
        return cm()
    monkeypatch.setattr(db, "tx", failing)


@pytest.mark.parametrize("where", ["adopt", "file_sha", "write", "matches", "commit", "release"])
def test_adopt_crash(s3, monkeypatch, where):
    """A crash in the adopt or at each step of the seal after it: every row reads its bytes all along, the next run
    (adopt again, a restart: sweep) ends sealed, every row on the object, nothing local."""
    kept = {H10: tone(2, 300), H10 + 2000: tone(2, 400)}
    before_s3(monkeypatch, kept)
    if where == "adopt":
        crashing_commit(monkeypatch, "SELECT 1 FROM store_objects")
        with pytest.raises(Crash):
            seal.adopt()
        assert seal.row(HOUR) is None, "all or none"
    else:
        seal.adopt()
        if where in ("file_sha", "write"):
            crash_once(monkeypatch, store if where == "file_sha" else store.S3, where)
        elif where == "commit":
            crashing_commit(monkeypatch, "SELECT 1 FROM pieces WHERE path LIKE 's3:%'")
        else:
            crash_once(monkeypatch, seal, where)
        seal.step()
    assert (T().recordings / HOUR).exists()
    same(kept)
    T().store_retry.clear()
    seal.adopt()
    seal.sweep()
    seal.step()
    assert {p["path"] for p in rows()} == {"s3:" + HOUR} and obj(HOUR)["state"] == "sealed" and not (T().recordings / HOUR).exists()
    same(kept)


def test_adopt_and_trim_exclusive(s3, monkeypatch):
    """adopt takes trim's lock: not while a trim apply or purge of the tenant runs, and none starts while it does."""
    before_s3(monkeypatch, {H10: tone(1)})
    t = T()
    monkeypatch.setattr(tenant, "load", lambda: [t])
    monkeypatch.setattr(sys, "argv", ["seal", "adopt", "--tenant", t.id])
    with trim.lock():
        with pytest.raises(SystemExit, match="another trim apply or purge, or seal adopt"):
            seal.main()
    assert seal.row(HOUR) is None
    real = seal.adopt

    def adopt(*a):
        with pytest.raises(SystemExit):
            with trim.lock():
                pass
        return real(*a)
    monkeypatch.setattr(seal, "adopt", adopt)
    seal.main()
    assert obj(HOUR)["state"] == "open"


def test_adopt_in_the_instance_zone_only(s3, monkeypatch):
    """Run in another time zone than the files were named in: left (its hours would be off, late pieces never
    merged); in the instance's zone: adopted, lo..hi its hour."""
    zone = os.environ.get("TZ")

    def to(tz):
        if tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = tz
        time.tzset()
    try:
        to("Asia/Taipei")
        h10 = int(dt.datetime(2026, 10, 2, 10, 0).timestamp() * 1000)
        before_s3(monkeypatch, {h10: tone(1)})
        to("America/Los_Angeles")
        assert seal.adopt()["not its hour"] == 1 and seal.row(HOUR) is None
        to("Asia/Taipei")
        assert seal.adopt()["adopted"] == 1 and (obj(HOUR)["lo_ms"], obj(HOUR)["hi_ms"]) == (h10 - 3600_000, h10 + 7200_000)
    finally:
        to(zone)


def test_adopt_keeps_a_row_made_meanwhile(s3, monkeypatch):
    """A row the instance made between adopt's look and its transaction (a piece placed, the hour sealed since)
    stays as it is."""
    before_s3(monkeypatch, {H10: tone(1)})
    tx = db.tx

    def meanwhile(durable=False):
        db.conn().execute("INSERT INTO store_objects (path, lo_ms, hi_ms, state, size) VALUES (?, 1, 2, 'sealed', 7)", (HOUR,))
        monkeypatch.setattr(db, "tx", tx)
        return tx(durable)
    monkeypatch.setattr(db, "tx", meanwhile)
    assert seal.adopt()["adopted"] == 1
    o = obj(HOUR)
    assert (o["state"], o["lo_ms"], o["hi_ms"], o["size"]) == ("sealed", 1, 2, 7)


def test_adopt_leaves_staging(s3):
    """A staging file (late pieces of a sealed hour) is merge()'s, never adopted."""
    sealed_hour()
    audio.save("phone", H10 + 60_000, tone(1, 700), "audio/ogg")
    assert rows()[-1]["path"] == STAGED0 and seal.row(STAGED0) is None
    assert seal.adopt() == {"adopted": 0, "bytes": 0, "recent": 0, "missing": 0, "trim copy": 0, "not its hour": 0}


@pytest.mark.parametrize("argv", [["adopt", "--tenant", "local"], ["adopt", "--tenant", "nobody"]])
def test_cli_refuses(s3, monkeypatch, capsys, argv):
    """Not with STORAGE=local (nothing would seal them); a tenant not in tenants.json said so."""
    t = T()
    monkeypatch.setattr(tenant, "load", lambda: [t])
    if argv[-1] == "local":
        monkeypatch.setattr(store, "STORAGE", "local")
    monkeypatch.setattr(sys, "argv", ["seal", *argv])
    with pytest.raises(SystemExit) as e:
        seal.main()
    assert e.value.code == 2 and ("STORAGE is not s3" if argv[-1] == "local" else "no tenant 'nobody'") in capsys.readouterr().err
