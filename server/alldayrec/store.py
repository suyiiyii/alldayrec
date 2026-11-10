"""Where recordings are kept: the one interface every read and write of audio bytes goes through (audio.py).

A path is relative to the tenant's recordings dir (a pieces row's path: chunks/<device>/<date>/<HH>.<ext>). STORAGE=local
(the default) keeps every file there, as always. STORAGE=s3: the hour being recorded stays a local file and is then
sealed into an object of an S3-compatible store (seal.py); a pieces row whose path starts with s3: is in that object,
read by range. Each tenant's objects are under S3_PREFIX<tenant id>/.
"""
import contextlib
import datetime as dt
import errno
import hashlib
import logging
import os
import shutil
import time
from pathlib import Path
from typing import BinaryIO, Protocol
from urllib.parse import quote, urlsplit

import httpx2

from alldayrec import db
from alldayrec import sigv4
from alldayrec.tenant import T

log = logging.getLogger("live")
COPY_BYTES = 1024 * 1024
STORAGE = os.environ.get("STORAGE", "local")  # local | s3
PUT_MAX = 5 * 1024 ** 3  # one PUT's limit (S3, and so most of its kind): no multipart, so no larger object


class Storage(Protocol):
    def append(self, path: str, data: bytes | BinaryIO) -> int:
        """data at the end of path; where it starts. On disk (fsync) when this returns."""

    def read(self, path: str, pos: int, size: int) -> bytes:
        """size bytes of path from pos (fewer at its end)."""

    def write(self, path: str, data: bytes | BinaryIO):
        """path made of data, whole."""

    def exists(self, path: str) -> bool: ...

    def delete(self, path: str): ...


def sync_dirs(dirs: set[Path]):
    for parent in dirs:
        d = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(d)
        finally:
            os.close(d)


class Local:
    """Files under root (the tenant's recordings dir)."""

    def __init__(self, root: Path):
        self.root = root

    def make_parents(self, f: Path) -> set[Path]:
        """Makes the directories f goes in; the directories that f, new, and those made add a name to: fsync them once
        f is written, for its name to be on disk too (a new device's or day's first piece). f's own directory always:
        a retry after a failed first write finds f there already, its name maybe not on disk yet."""
        new = [d for d in (f, *f.parents) if self.root in d.parents and not d.exists()]
        f.parent.mkdir(parents=True, exist_ok=True)
        return {d.parent for d in new} | {f.parent}

    def append(self, path, data):
        f = self.root / path
        dirs = self.make_parents(f)
        with open(f, "ab") as h:
            pos = h.seek(0, os.SEEK_END)  # a failed write before leaves bytes no row points at: harmless
            if isinstance(data, bytes):
                h.write(data)
            else:
                data.seek(0)
                shutil.copyfileobj(data, h, COPY_BYTES)
            h.flush()
            os.fsync(h.fileno())
        sync_dirs(dirs)
        return pos

    def read(self, path, pos, size):
        with open(self.root / path, "rb") as f:
            f.seek(pos)
            return f.read(size)

    def write(self, path, data):
        f = self.root / path
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("wb") as out:
            if isinstance(data, bytes):
                out.write(data)
            else:
                data.seek(0)
                shutil.copyfileobj(data, out, COPY_BYTES)

    def exists(self, path):
        return (self.root / path).exists()

    def delete(self, path):
        (self.root / path).unlink(missing_ok=True)




@contextlib.contextmanager
def timed(op: str):
    """op's time and failure, for the metrics (monitor.metrics): the tenant's store_calls."""
    c, t0 = T().store_calls.setdefault(op, [0, 0, 0.0]), time.monotonic()
    try:
        yield
    except BaseException:
        c[1] += 1
        raise
    finally:
        c[0], c[2] = c[0] + 1, c[2] + time.monotonic() - t0


class Unreachable(OSError):
    """The object store did not answer as it should (down, refused, a wrong key): sealed audio cannot be read now."""


class Conflict(OSError):
    """A conditional PUT refused (412): the key holds an object this one was not to replace (another instance's)."""


class S3:
    """An S3-compatible object store (MinIO, R2, OSS, TOS...): path-style URLs, SigV4 (sigv4.py) over httpx2. The
    tenant's objects are keys S3_PREFIX<tenant id>/<path>, the s3: of a row's path dropped."""

    def __init__(self, endpoint: str, bucket: str, access_key: str, secret_key: str, region: str = "us-east-1", prefix: str = "",
                 transport=None):
        u = urlsplit(endpoint)
        self.base, self.root, self.bucket, self.region = f"{u.scheme}://{u.netloc}", u.path.rstrip("/"), bucket, region or "us-east-1"
        self.access_key, self.secret_key = access_key, secret_key
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.http = httpx2.Client(timeout=httpx2.Timeout(60, connect=5), trust_env=False, transport=transport)

    def key(self, path: str) -> str:
        """The tenant's key of path: under the prefix its store took at its first use (kv s3_prefix: S3_PREFIX<tenant
        id>/ then), so a tenant moved into another process (tenants.sh merge) keeps reading its own objects."""
        if T().store_prefix is None:
            v = db.kv("s3_prefix")
            if v is None:
                v = f"{self.prefix}{T().id}/"
                db.put_kv("s3_prefix", v)
            elif v != f"{self.prefix}{T().id}/":
                log.warning("store: tenant %s keeps its objects under %s (its first S3_PREFIX), not %s", T().id, v, f"{self.prefix}{T().id}/")
            T().store_prefix = v
        return T().store_prefix + path.removeprefix("s3:")

    def request(self, method: str, key: str, ok: tuple, headers: dict | None = None, body=b"", sha: str = sigv4.EMPTY, stream=False):
        uri = f"{self.root}/{self.bucket}/{key}"
        request = self.http.build_request(method, self.base + quote(uri, safe="/~"), headers=headers, content=body)
        # signed for the Host header the request goes with (an endpoint's explicit default port is left out of it)
        signed = sigv4.sign(method, request.headers["host"], uri, {}, headers or {}, sha, self.access_key, self.secret_key,
                            self.region, dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        request.headers.update(signed)
        with timed({"GET": "read", "PUT": "write", "HEAD": "head", "DELETE": "delete"}[method]):
            try:
                r = self.http.send(request, stream=stream)
            except httpx2.HTTPError as e:
                raise Unreachable(f"object store unreachable: {method} {key}: {type(e).__name__}") from None
            if r.status_code not in ok:
                text = r.read()[:200].decode(errors="replace")
                r.close()
                raise (Conflict if r.status_code == 412 else Unreachable)(f"object store: {method} {key}: HTTP {r.status_code} {text}")
        return r

    def append(self, path, data):
        raise NotImplementedError("objects are written whole: an hour is appended to on local disk, then sealed")

    def read(self, path, pos, size):
        if size <= 0:
            return b""
        r = self.request("GET", self.key(path), (200, 206, 416), {"Range": f"bytes={pos}-{pos + size - 1}"})
        if r.status_code == 416:  # from past its end: as a file read there
            return b""
        return r.content if r.status_code == 206 else r.content[pos:pos + size]

    def download(self, path: str, out: BinaryIO):
        """The whole object into out, in bounded reads."""
        r = self.request("GET", self.key(path), (200,), stream=True)
        try:
            for data in r.iter_bytes(COPY_BYTES):
                out.write(data)
        finally:
            r.close()
        out.flush()

    def write(self, path, data, sha: str | None = None, replaces: str | None = None) -> str:
        """path made of data (bytes, or a file from 0 to its end); its ETag. Its SHA-256 (sha, or computed) is signed,
        so the store refuses a body that arrives otherwise, and kept with it as x-amz-meta-sha256 (head). Never over
        another's object: a new key only if there is none (If-None-Match: *), else only over the version whose ETag
        is `replaces` (If-Match); refused, it raises Conflict."""
        if isinstance(data, bytes):
            size, sha = len(data), sha or hashlib.sha256(data).hexdigest()
            body = data
        else:
            size = data.seek(0, os.SEEK_END)
            sha = sha or file_sha(data, size)
            body = chunks(data, size)
        if size > PUT_MAX:
            raise OSError(errno.EFBIG, f"{path}: {size} bytes is over one PUT's {PUT_MAX} (no multipart upload)")
        cond = {"If-Match": replaces} if replaces else {"If-None-Match": "*"}
        r = self.request("PUT", self.key(path), (200,), {"Content-Length": str(size), "x-amz-meta-sha256": sha, **cond}, body, sha)
        r.close()
        return r.headers.get("etag")

    def head(self, path: str) -> tuple[int, str | None, str | None] | None:
        """(size, x-amz-meta-sha256, ETag) of the object, None when there is none."""
        r = self.request("HEAD", self.key(path), (200, 404))
        return None if r.status_code == 404 else (int(r.headers["content-length"]), r.headers.get("x-amz-meta-sha256"), r.headers.get("etag"))

    def exists(self, path):
        return self.head(path) is not None

    def delete(self, path):
        self.request("DELETE", self.key(path), (200, 204)).close()


def chunks(f: BinaryIO, size: int):
    """f's first size bytes, a COPY_BYTES read at a time."""
    f.seek(0)
    while size > 0:
        data = f.read(min(size, COPY_BYTES))
        if not data:
            raise OSError("file ended early")
        size -= len(data)
        yield data


def file_sha(f: BinaryIO, size: int) -> str:
    h = hashlib.sha256()
    for data in chunks(f, size):
        h.update(data)
    return h.hexdigest()


_s3: S3 | None = None


def s3() -> S3:
    """The object store of STORAGE=s3, from S3_ENDPOINT, S3_BUCKET, S3_ACCESS_KEY, S3_SECRET_KEY, S3_REGION, S3_PREFIX."""
    global _s3
    if _s3 is None:
        e = os.environ
        _s3 = S3(e["S3_ENDPOINT"], e["S3_BUCKET"], e["S3_ACCESS_KEY"], e["S3_SECRET_KEY"], e.get("S3_REGION", ""), e.get("S3_PREFIX", ""))
    return _s3


def local() -> Local:
    """The tenant's recordings dir."""
    return Local(T().recordings)


def of(path: str) -> Storage:
    """The storage a pieces row's path is in."""
    return s3() if path.startswith("s3:") else local()
