"""STORAGE=s3 on a real S3 protocol (MinIO: CI's service, or TEST_S3_ENDPOINT): the storage interface on objects, an
hour sealed, late pieces staged and merged, reads during a merge, a crash at every step, a longer resend into a sealed
hour, the store down, imports, and tenants kept apart. Skipped without TEST_S3_ENDPOINT, except on CI (fails)."""
import datetime as dt
import hashlib
import io
import os
import subprocess
import uuid

import anyio
import httpx2
import numpy as np
import pytest

from alldayrec import audio, db, monitor, routes_audio, seal, sigv4, store, tenant
from alldayrec.tenant import T

ENDPOINT = os.environ.get("TEST_S3_ENDPOINT")
if not ENDPOINT:
    if os.environ.get("CI"):
        raise RuntimeError("CI runs these on MinIO: TEST_S3_ENDPOINT is not set")
    pytest.skip("no TEST_S3_ENDPOINT (a MinIO to test on)", allow_module_level=True)
KEY, SECRET = os.environ.get("TEST_S3_ACCESS_KEY", "minioadmin"), os.environ.get("TEST_S3_SECRET_KEY", "minioadmin")
BUCKET = os.environ.get("TEST_S3_BUCKET", "alldayrec-test")
H10 = int(dt.datetime(2026, 10, 2, 10, 0).timestamp() * 1000)  # an hour long over: sealed once idle


def call(method: str, uri: str, query: dict | None = None) -> httpx2.Response:
    """A signed request outside store.S3 (the bucket, a listing)."""
    host = httpx2.URL(ENDPOINT).netloc.decode()
    h = sigv4.sign(method, host, uri, query or {}, {}, sigv4.EMPTY, KEY, SECRET, "us-east-1",
                   dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    return httpx2.request(method, ENDPOINT.rstrip("/") + uri, params=query, headers=h, trust_env=False)


def keys(prefix: str) -> list[str]:
    import re
    return re.findall(r"<Key>([^<]+)</Key>", call("GET", f"/{BUCKET}", {"list-type": "2", "prefix": prefix}).text)


@pytest.fixture
def s3(tmp_path, monkeypatch):
    """STORAGE=s3 on MinIO under a prefix of this test's own; seal and merge as soon as they may."""
    assert call("PUT", f"/{BUCKET}").status_code in (200, 409)  # made, or there already
    prefix = f"t-{uuid.uuid4().hex[:8]}/"
    for k, v in {"S3_ENDPOINT": ENDPOINT, "S3_BUCKET": BUCKET, "S3_ACCESS_KEY": KEY, "S3_SECRET_KEY": SECRET, "S3_PREFIX": prefix}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(store, "STORAGE", "s3")
    monkeypatch.setattr(store, "_s3", None)
    monkeypatch.setattr(seal, "SEAL_IDLE_S", 0)
    monkeypatch.setattr(seal, "MERGE_IDLE_S", 0)
    db.open_db(tmp_path / "t.db", tmp_path / "rec")
    return prefix


def tone(s: float, f: int = 440) -> bytes:
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=f={f}:d={s}", "-c:a", "libopus", "-f", "ogg", "pipe:1"],
                          capture_output=True, check=True).stdout


def rows(device="phone"):
    return db.conn().execute("SELECT * FROM pieces WHERE device = ? ORDER BY start_ms", (device,)).fetchall()


def same(kept: dict):
    """Every row reads back the bytes uploaded for it (kept: start_ms -> bytes)."""
    got = {p["start_ms"]: audio.read(p) for p in rows()}
    assert got == kept, {k: (len(got.get(k, b"")), len(v)) for k, v in kept.items()}


def obj(path: str) -> dict:
    return dict(seal.row(path))


HOUR = "chunks/phone/2026-10-02/10.ogg"
STAGED0, STAGED1 = "staging/chunks/phone/2026-10-02/10.0.ogg", "staging/chunks/phone/2026-10-02/10.1.ogg"


def sealed_hour() -> dict:
    """Two pieces of 10:00 uploaded and the hour sealed."""
    kept = {H10 + i * 2000: tone(2, 300 + 100 * i) for i in range(2)}
    for ms, body in kept.items():
        assert audio.save("phone", ms, body, "audio/ogg") > 1.9
    assert {p["path"] for p in rows()} == {HOUR}, "open: the hour's local file"
    seal.step()
    return kept


def test_interface(s3):
    """store.S3 is a Storage: whole writes, ranged reads (past the end: what there is), head, delete; keys under the
    tenant's prefix, any name."""
    s = store.s3()
    path = "s3:chunks/电话 1/x.ogg"
    s.write(path, b"0123456789")
    assert s.read(path, 2, 3) == b"234" and s.read(path, 8, 10) == b"89" and s.read(path, 10, 5) == b""
    s.write(path, io.BytesIO(b"abc"))
    assert s.head(path) == (3, hashlib.sha256(b"abc").hexdigest()) and s.exists(path)
    assert keys(s3) == [f"{s3}local/chunks/电话 1/x.ogg"], "S3_PREFIX, the tenant's id, the path"
    s.delete(path)
    assert s.head(path) is None and not s.exists(path)
    with pytest.raises(store.Unreachable, match="HTTP 404"):
        s.read(path, 0, 1)


def test_seal(s3):
    kept = sealed_hour()
    assert {p["path"] for p in rows()} == {"s3:" + HOUR} and [p["pos"] for p in rows()] == [0, len(kept[H10])], "pos unchanged"
    assert not (T().recordings / HOUR).exists(), "the local file gone once its rows point at the object"
    o = obj(HOUR)
    assert o["state"] == "sealed" and o["intent"] is None and store.s3().head(HOUR) == (o["size"], o["sha256"])
    assert o["size"] == sum(map(len, kept.values()))
    same(kept)
    x = audio.cut(dt.datetime.fromtimestamp(H10 / 1000 + 0.5), dt.datetime.fromtimestamp(H10 / 1000 + 3), "s16le", "phone")
    assert len(x) > 2 * audio.SR * 2, "played from the object"


def test_not_sealed_early(s3, monkeypatch):
    """The hour now is open till it is over; an over one waits SEAL_IDLE_S after its last piece."""
    now = int(dt.datetime.now().timestamp() * 1000)
    assert audio.save("phone", now, tone(1), "audio/ogg") > 0.9
    monkeypatch.setattr(seal, "SEAL_IDLE_S", 600)
    assert audio.save("phone", H10, tone(1), "audio/ogg") > 0.9
    seal.step()
    assert {p["path"] for p in rows()} == {HOUR, str(audio.hour_file("phone", now, "ogg"))}, "neither is sealed"
    assert obj(HOUR)["seal_at"] >= now + 599_000


def test_late_merge(s3):
    """A piece for a sealed hour: staged locally, read from there, then merged at the object's end; every row reads
    the same bytes before, during and after."""
    kept = sealed_hour()
    size = obj(HOUR)["size"]
    late = H10 + 60_000
    kept[late] = tone(2, 700)
    report = {}
    assert audio.save("phone", late, kept[late], "audio/ogg", report=report) > 1.9
    assert rows()[-1]["path"] == STAGED0 and obj(HOUR)["late_bytes"] == len(kept[late])
    same(kept)
    heard = [audio.samples(p) for p in rows()]
    seal.step()
    assert [p["path"] for p in rows()] == ["s3:" + HOUR] * 3 and rows()[-1]["pos"] == size, "after the bytes there were"
    assert all(np.array_equal(a, audio.samples(p)) for a, p in zip(heard, rows())), "decoded the same from staging (its codec by extension)"
    o = obj(HOUR)
    assert (o["size"], o["late_bytes"], o["gen"], o["intent"]) == (size + len(kept[late]), 0, 1, None)
    assert not (T().recordings / STAGED0).exists()
    same(kept)


def test_read_during_merge(s3, monkeypatch):
    """While the merged object is uploaded and after, a reader with the rows as they were (staging) and one with
    the old object's rows read what was uploaded; once the staging file is gone the old row finds the moved bytes."""
    kept = sealed_hour()
    late = H10 + 60_000
    kept[late] = tone(2, 700)
    audio.save("phone", late, kept[late], "audio/ogg")
    before = {p["start_ms"]: p for p in rows()}
    write, seen = store.S3.write, []

    def during(self, path, data, sha=None):
        seen.append({ms: audio.read(p) for ms, p in before.items()})  # old object, staging file
        write(self, path, data, sha)
        seen.append({ms: audio.read(p) for ms, p in before.items()})  # new object, staging file
    monkeypatch.setattr(store.S3, "write", during)
    seal.step()
    assert seen == [kept, kept]
    assert {ms: audio.read(p) for ms, p in before.items()} == kept, "the staged row as read before: moved"
    assert len(audio.samples(before[late])) > 1.9 * audio.SR


class Crash(Exception):
    pass


def crash_once(monkeypatch, obj_, name):
    """obj_.name raises Crash the first time it is called (a process killed there), then is itself again."""
    real = getattr(obj_, name)
    calls = []

    def f(*a, **k):
        if not calls:
            calls.append(1)
            raise Crash(name)
        return real(*a, **k)
    monkeypatch.setattr(obj_, name, f)
    return calls


@pytest.mark.parametrize("where", ["file_sha", "write", "matches", "commit", "release"])
def test_seal_crash(s3, monkeypatch, where):
    """A crash at each step of a seal: the rows stay readable all along; the next run (after a restart: sweep) ends
    sealed, every row on the object, nothing local. One that crashed after its upload only commits."""
    kept = {H10: tone(2)}
    audio.save("phone", H10, kept[H10], "audio/ogg")
    puts = []
    write = store.S3.write
    monkeypatch.setattr(store.S3, "write", lambda self, *a: puts.append(1) or write(self, *a))
    if where == "file_sha":
        crash_once(monkeypatch, store, "file_sha")
    elif where == "write":
        crash_once(monkeypatch, store.S3, "write")
    elif where == "matches":  # uploaded, crashed before its check: the next run's check finds it done
        crash_once(monkeypatch, seal, "matches")
    elif where == "commit":
        tx = db.tx
        def failing(durable=False):
            import contextlib

            @contextlib.contextmanager
            def cm():
                with tx(durable) as t:
                    yield t
                    if t.execute("SELECT 1 FROM pieces WHERE path LIKE 's3:%'").fetchone():
                        raise Crash()  # rolled back: the rows moved and the row sealed in one transaction, or neither
            return cm()
        monkeypatch.setattr(db, "tx", failing)
    elif where == "release":
        crash_once(monkeypatch, seal, "release")
    seal.step()
    assert rows()[0]["path"] == ("s3:" + HOUR if where == "release" else HOUR)
    assert (T().recordings / HOUR).exists(), "the local file stays till the commit and after it till released"
    same(kept)
    if where == "commit":
        monkeypatch.setattr(db, "tx", tx)
    n = len(puts)
    T().store_retry.clear()
    seal.sweep()  # a restart
    seal.step()
    assert rows()[0]["path"] == "s3:" + HOUR and obj(HOUR)["state"] == "sealed" and not (T().recordings / HOUR).exists()
    same(kept)
    if where in ("matches", "commit"):
        assert len(puts) == n, "the object matched its intent: not uploaded again"


@pytest.mark.parametrize("where", ["download", "write", "matches", "commit", "release"])
def test_merge_crash(s3, monkeypatch, where):
    """A crash at each step of a merge, then another late piece and a restart: every row reads its bytes all along,
    the next run ends with all of them on the object and no staging file left."""
    kept = sealed_hour()
    late = H10 + 60_000
    kept[late] = tone(2, 700)
    audio.save("phone", late, kept[late], "audio/ogg")
    puts = []
    write = store.S3.write
    monkeypatch.setattr(store.S3, "write", lambda self, *a: puts.append(1) or write(self, *a))
    if where in ("download", "write"):
        crash_once(monkeypatch, store.S3, where)
    elif where == "matches":
        crash_once(monkeypatch, seal, "matches")
    elif where == "commit":
        crash_once(monkeypatch, seal, "finish")
    elif where == "release":
        crash_once(monkeypatch, seal, "release")
    seal.step()
    same(kept)
    n = len(puts)
    later = H10 + 120_000
    kept[later] = tone(1, 800)
    audio.save("phone", later, kept[later], "audio/ogg")
    same(kept)
    T().store_retry.clear()
    seal.sweep()
    seal.step()  # the merge left over (or, after release, the later piece)
    T().store_retry.clear()
    seal.step()
    assert {p["path"] for p in rows()} == {"s3:" + HOUR}
    assert not list((T().recordings / "staging").rglob("*.ogg*")), "no staging file left"
    o = obj(HOUR)
    assert store.s3().head(HOUR) == (o["size"], o["sha256"]) and o["late_bytes"] == 0 and o["intent"] is None
    same(kept)
    if where in ("matches", "commit"):
        assert len(puts) == n + 1, "the crashed merge only committed; the later piece one upload more"


def test_longer_resend_into_sealed(s3):
    """The longer copy of a piece of a sealed hour: staged, the row replaced, then merged; the longer is kept."""
    kept = sealed_hour()
    kept[H10] = tone(3, 300)
    assert audio.save("phone", H10, kept[H10], "audio/ogg") > 2.9
    assert rows()[0]["path"] == STAGED0 and rows()[0]["size"] == len(kept[H10])
    same(kept)
    seal.step()
    assert {p["path"] for p in rows()} == {"s3:" + HOUR}
    same(kept)
    assert audio.save("phone", H10, tone(1), "audio/ogg") > 2.9, "a shorter one again: the kept copy's seconds"


def test_store_down(s3, monkeypatch):
    """The object store unreachable: uploads go on (local); seals fail, back off and are counted; sealed audio
    fails to read with a clear error (503 for playback, the stream waits); all of it goes once it is back."""
    kept = sealed_hour()
    monkeypatch.setattr(store, "_s3", store.S3("http://127.0.0.1:9", BUCKET, KEY, SECRET, "", s3))  # nothing listens
    later = H10 + 3600_000
    assert audio.save("phone", later, tone(1), "audio/ogg") > 0.9, "an upload does not need the object store"
    seal.step()
    assert rows()[-1]["path"] == "chunks/phone/2026-10-02/11.ogg" and (T().recordings / rows()[-1]["path"]).exists()
    assert T().store_retry[rows()[-1]["path"]][0] == 1, "backed off"
    seal.step()
    assert T().store_retry[rows()[-1]["path"]][0] == 1, "not tried again before its time"
    m = monitor.metrics()
    assert 'lr_store_pending_seal{instance="local"} 1.0' in m and 'lr_store_errors_total{instance="local",op="seal"}' in m
    with pytest.raises(store.Unreachable, match="unreachable"):
        audio.read(rows()[0])
    with pytest.raises(Exception) as e:
        anyio.run(routes_audio.cut, dt.datetime.fromtimestamp(H10 / 1000).isoformat(),
                  dt.datetime.fromtimestamp(H10 / 1000 + 2).isoformat(), "wav", "phone")
    assert getattr(e.value, "status_code", None) == 503

    async def first_piece():
        s = audio.stream("phone", None)
        with anyio.move_on_after(1.5):
            async for t, x in s:
                return t
        await s.aclose()
    assert anyio.run(first_piece) is None, "the stream waits on it, not reading it as silence"
    monkeypatch.setattr(store, "_s3", None)
    T().store_retry.clear()
    seal.step()
    assert {p["path"] for p in rows()} == {"s3:" + HOUR, "s3:chunks/phone/2026-10-02/11.ogg"}
    same(kept | {later: audio.read(rows()[-1])})


def test_import(s3, monkeypatch):
    """A file longer than WHOLE_S: a file of its own, sealed at once (no idle wait), read by window from the object."""
    monkeypatch.setattr(seal, "SEAL_IDLE_S", 600)
    monkeypatch.setattr(audio, "WHOLE_S", 3)
    body = tone(5)
    with open(T().data / "up.ogg", "w+b") as f:
        f.write(body)
        f.flush()
        assert audio.save_file("phone", H10, f, "audio/ogg") > 4.9
    path = f"chunks/phone/2026-10-02/10.{H10}.{len(body)}.ogg"
    assert rows()[0]["path"] == path
    seal.step()
    p = rows()[0]
    assert p["path"] == "s3:" + path and audio.read(p) == body and not (T().recordings / path).exists()
    x = audio.piece_window(p, audio.SR, audio.SR)
    assert len(x) == audio.SR


def test_too_large(s3, monkeypatch):
    """A file over one PUT stays on local disk, said so, never half-uploaded; its rows read from there."""
    monkeypatch.setattr(store, "PUT_MAX", 10)
    kept = {H10: tone(1)}
    audio.save("phone", H10, kept[H10], "audio/ogg")
    seal.step()
    assert obj(HOUR)["state"] == "local" and rows()[0]["path"] == HOUR and keys(s3) == []
    seal.step()
    audio.save("phone", H10 + 5000, tone(1), "audio/ogg")
    assert rows()[-1]["path"] == HOUR, "it goes on taking pieces"
    with pytest.raises(OSError, match="over one PUT"):
        store.s3().write("s3:x", b"x" * 11)


def test_tenants_apart(s3, tmp_path):
    """Two tenants on one bucket: each one's objects under S3_PREFIX<its id>/, read only as itself."""
    a = T()
    kept = sealed_hour()
    tenant.enter(tenant.Tenant("other", "", tmp_path / "b", tmp_path / "b/rec"))
    (tmp_path / "b").mkdir()
    db.init()
    other = {H10: tone(2, 900)}
    audio.save("phone", H10, other[H10], "audio/ogg")
    seal.step()
    same(other)
    assert sorted(keys(s3)) == [f"{s3}local/{HOUR}", f"{s3}other/{HOUR}"]
    p = rows()[0]
    tenant.enter(a)
    same(kept)
    assert audio.read(p) != other[H10], "the other's row read as this tenant: this tenant's key, never the other's object"


def test_name_collision(s3):
    """Two devices whose names make one file name ("a b", "a_b"): the seal moves the rows of both before the file goes."""
    kept = {H10: tone(1, 300), H10 + 2000: tone(1, 500)}
    audio.save("a b", H10, kept[H10], "audio/ogg")
    audio.save("a_b", H10 + 2000, kept[H10 + 2000], "audio/ogg")
    seal.step()
    got = db.conn().execute("SELECT * FROM pieces ORDER BY start_ms").fetchall()
    assert {p["path"] for p in got} == {"s3:chunks/a_b/2026-10-02/10.ogg"}
    assert {p["start_ms"]: audio.read(p) for p in got} == kept


def test_seal_verified(s3, monkeypatch):
    """An object that is not what was uploaded (a store that kept other bytes) is never committed: the rows stay on
    the local file, the next run uploads again."""
    kept = {H10: tone(2)}
    audio.save("phone", H10, kept[H10], "audio/ogg")
    write = store.S3.write
    monkeypatch.setattr(store.S3, "write", lambda self, path, data, sha=None: write(self, path, b"other bytes"))
    seal.step()
    assert rows()[0]["path"] == HOUR and (T().recordings / HOUR).exists() and obj(HOUR)["state"] == "sealing"
    same(kept)
    monkeypatch.setattr(store.S3, "write", write)
    T().store_retry.clear()
    seal.step()
    assert rows()[0]["path"] == "s3:" + HOUR
    same(kept)


def test_merge_refuses_changed_object(s3):
    """The object changed since its seal (another writer on the key): no merge onto it; the late piece stays staged."""
    kept = sealed_hour()
    late = H10 + 60_000
    kept[late] = tone(2, 700)
    audio.save("phone", late, kept[late], "audio/ogg")
    store.s3().write(HOUR, b"not the sealed bytes")
    seal.step()
    assert rows()[-1]["path"].startswith("staging/chunks/phone/2026-10-02/10.") and audio.read(rows()[-1]) == kept[late]
    assert store.s3().read(HOUR, 0, 100) == b"not the sealed bytes", "not overwritten"


def test_resend_during_merge(s3, monkeypatch):
    """A longer copy of a staged piece arriving while that piece is merged: the merge leaves the new row alone (it
    goes to the next staging file and the next merge)."""
    kept = sealed_hour()
    late = H10 + 60_000
    audio.save("phone", late, tone(2, 700), "audio/ogg")
    kept[late] = tone(3, 700)
    write = store.S3.write

    def during(self, path, data, sha=None):
        audio.save("phone", late, kept[late], "audio/ogg")
        write(self, path, data, sha)
    monkeypatch.setattr(store.S3, "write", during)
    seal.step()
    assert rows()[-1]["path"] == STAGED1 and audio.read(rows()[-1]) == kept[late]
    monkeypatch.setattr(store.S3, "write", write)
    seal.step()
    assert {p["path"] for p in rows()} == {"s3:" + HOUR}
    same(kept)


def test_staged_codec(s3):
    """A staging file keeps its extension last: a staged iPhone AAC piece is cut to the samples its client wrote, as
    from its hour file and from the object."""
    aac = lambda s: subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=f=440:r=16000:d={s}", "-c:a", "aac",
                                    "-f", "adts", "pipe:1"], capture_output=True, check=True).stdout
    n = 16000
    audio.save("iphone", H10, aac(1.2), "audio/aac", n=n)
    seal.step()
    audio.save("iphone", H10 + 60_000, aac(1.2), "audio/aac", n=n)
    got = rows("iphone")
    assert [p["path"] for p in got] == ["s3:chunks/iphone/2026-10-02/10.aac", "staging/chunks/iphone/2026-10-02/10.0.aac"]
    assert [len(audio.samples(p)) for p in got] == [n, n]
    seal.step()
    assert [len(audio.samples(p)) for p in rows("iphone")] == [n, n]


def test_two_codecs_one_hour(s3):
    """A device that changed codec within an hour has two hour files (10.ogg, 10.aac): each merge takes only its own
    file's staged pieces."""
    aac = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:r=16000:d=1.2", "-c:a", "aac", "-f", "adts", "pipe:1"],
                         capture_output=True, check=True).stdout
    kept = {H10: tone(1), H10 + 5000: aac}
    audio.save("phone", H10, kept[H10], "audio/ogg")
    audio.save("phone", H10 + 5000, aac, "audio/aac", n=16000)
    seal.step()
    kept |= {H10 + 60_000: tone(1, 600), H10 + 65_000: aac}
    audio.save("phone", H10 + 60_000, kept[H10 + 60_000], "audio/ogg")
    audio.save("phone", H10 + 65_000, aac, "audio/aac", n=16000)
    seal.step()
    assert [p["path"] for p in rows()] == ["s3:" + HOUR, "s3:chunks/phone/2026-10-02/10.aac"] * 2
    same(kept)
