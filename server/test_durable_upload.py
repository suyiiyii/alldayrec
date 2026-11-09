"""An upload is answered only once it survives an OS crash: audio bytes fsynced, its pieces row committed with FULL."""
import os
import sqlite3
import stat
import tempfile
import wave

import numpy as np
import pytest

from alldayrec import audio, db
from alldayrec.tenant import T


def piece():
    with tempfile.NamedTemporaryFile(suffix=".wav") as f:
        with wave.open(f.name, "wb") as w:
            w.setparams((1, 2, audio.SR, 0, "NONE", ""))
            w.writeframes((np.sin(np.arange(audio.SR * 3 // 2) / 5) * 3000).astype(np.int16).tobytes())
        return open(f.name, "rb").read()


def test_upload_is_durable_before_the_answer(tmp_path, monkeypatch):
    db.open_db(tmp_path / "t.db")
    synced, fsync = [], os.fsync
    def spy(fd):
        st = os.fstat(fd)
        synced.append(st.st_ino if stat.S_ISREG(st.st_mode) else None)
        fsync(fd)
    monkeypatch.setattr(audio.os, "fsync", spy)
    statements, at_insert = [], []
    def trace(sql):
        statements.append(sql)
        if sql.startswith("INSERT OR REPLACE INTO pieces"):
            at_insert.append(list(synced))
    db.conn().set_trace_callback(trace)
    body = piece()
    for save in (audio.save, audio.save_file):
        device = save.__name__
        if save is audio.save:
            assert audio.save(device, 1700000000000, body, "audio/wav") == 1.5
        else:
            with tempfile.NamedTemporaryFile() as source:
                source.write(body)
                source.flush()
                assert audio.save_file(device, 1700000000000, source, "audio/wav") == 1.5
        row = db.conn().execute("SELECT path FROM pieces WHERE device = ?", (device,)).fetchone()
        assert os.stat(T().recordings / row["path"]).st_ino in at_insert.pop(), f"{device}: audio fsynced before its row"
        insert = max(i for i, s in enumerate(statements) if s.startswith("INSERT OR REPLACE INTO pieces"))
        commit = statements.index("COMMIT", insert)
        mode = next((s for s in reversed(statements[:commit]) if s.startswith("PRAGMA synchronous")), None)
        assert mode == "PRAGMA synchronous=FULL", f"{device}: the row's commit runs under FULL"
        assert statements[commit + 1] == "PRAGMA synchronous=NORMAL"
        synced.clear()
    db.conn().set_trace_callback(None)
    assert db.conn().execute("PRAGMA synchronous").fetchone()[0] == 1, "other writes stay NORMAL"


def test_new_directories_are_durable(tmp_path, monkeypatch):
    """A new device's first piece: the names of its file and of the directories made for it are fsynced too."""
    db.open_db(tmp_path / "t.db")
    synced, fsync = [], os.fsync
    monkeypatch.setattr(audio.os, "fsync", lambda fd: synced.append(os.fstat(fd).st_ino) or fsync(fd))
    rec = T().recordings
    assert audio.save("dev-a", 1700000000000, piece(), "audio/wav") == 1.5
    f = rec / db.conn().execute("SELECT path FROM pieces").fetchone()["path"]
    made = [d for d in f.parents if rec in d.parents]  # the hour, device and chunks directories
    assert {os.stat(d.parent).st_ino for d in [f, *made]} <= set(synced)


def test_failed_commit_rolls_back(tmp_path):
    """A COMMIT that fails is rolled back and raises its own error; the connection is back to NORMAL."""
    db.open_db(tmp_path / "t.db")
    c = db.conn()
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE child (p INTEGER REFERENCES parent (id) DEFERRABLE INITIALLY DEFERRED)")  # checked at COMMIT
    try:
        with pytest.raises(sqlite3.IntegrityError):
            with db.tx(durable=True) as t:
                t.execute("INSERT INTO child VALUES (1)")
        assert not c.in_transaction
        assert c.execute("PRAGMA synchronous").fetchone()[0] == 1
    finally:
        c.execute("PRAGMA foreign_keys=OFF")
