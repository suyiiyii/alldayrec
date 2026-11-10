"""Where recordings are kept: the one interface every read and write of audio bytes goes through (audio.py).

A path is relative to the tenant's recordings dir (a pieces row's path: chunks/<device>/<date>/<HH>.<ext>). STORAGE=local
(the default) keeps every file there, as always.
"""
import os
import shutil
from pathlib import Path
from typing import BinaryIO, Protocol

from alldayrec.tenant import T

COPY_BYTES = 1024 * 1024


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


def local() -> Local:
    """The tenant's recordings dir."""
    return Local(T().recordings)


def of(path: str) -> Storage:
    """The storage a pieces row's path is in."""
    return local()
