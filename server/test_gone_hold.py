"""A stored file gone (a recordings directory briefly unavailable, a restore in progress): the final layer's reader
holds on the first piece it has not read yet, and goes on once the file is back. Meanwhile the final layer cuts and
closes what came before; a piece already covered, and the draft reader, go on; a file gone while it is read is held
too. Real store and stream; test_backlog's pipeline rig."""
import contextlib
import datetime as dt

import anyio
import numpy as np
import pytest

from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import device_pipeline
from alldayrec.conversations import Grouper
from alldayrec.tenant import T
from test_backlog import DEVICE, SR, flac, hear, rig, save, tone


def hour_ago(h: int) -> dt.datetime:
    """The start of the local hour h hours ago (stored files are per hour)."""
    return dt.datetime.now().replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=h)


def away(start: dt.datetime) -> tuple:
    """Move the stored file of the piece at `start` away; a callable that puts it back."""
    p = db.conn().execute("SELECT path FROM pieces WHERE device = ? AND start_ms = ?", (DEVICE, int(start.timestamp() * 1000))).fetchone()
    f = T().recordings / p["path"]
    f.rename(f.with_suffix(".away"))
    return lambda: f.with_suffix(".away").rename(f)


@contextlib.asynccontextmanager
async def reading(**kw):
    """The start times (to the second) stream(DEVICE) hands out, as they come; run as its own task (cancelling a wait
    inside the generator would end it)."""
    send, got = anyio.create_memory_object_stream(1000)

    async def feed():
        s = audio.stream(DEVICE, None, **kw)
        try:
            async for t, x in s:
                await send.send(t.replace(microsecond=0))
        finally:
            await s.aclose()
    async with anyio.create_task_group() as tg:
        tg.start_soon(feed)
        yield got
        tg.cancel_scope.cancel()


async def handed(got, n: int, wait: float, to: dt.datetime | None = None) -> list[dt.datetime]:
    """Up to n of them within `wait` seconds, or until the one at `to`."""
    out = []
    with anyio.move_on_after(wait):
        while len(out) < n and (not out or out[-1] != to):
            out.append(await got.receive())
    return out


def test_held_piece_lets_the_final_layer_cut(tmp_path, monkeypatch):
    """The 3 pieces before the gone ones are cut, committed and their conversation closed by grouper.tick while the
    reader holds (it is idle meanwhile: settled()); once the file is back the rest is final and end_at closes."""
    rig(tmp_path, monkeypatch, hear)
    monkeypatch.setattr(audio, "GONE_RETRY_S", 0.3)
    monkeypatch.setattr(device_pipeline, "Grouper", lambda **kw: Grouper(idle_s=3, **kw))
    t0 = hour_ago(3) - dt.timedelta(seconds=180)  # pieces 0-2 in one hour's file, 3-5 in the next
    silence = np.zeros(60 * SR, np.int16)
    for i in range(6):
        save(t0 + dt.timedelta(seconds=60 * i), flac(np.concatenate([silence[:10 * SR], tone(i, 5), silence[15 * SR:]])))
    back = away(t0 + dt.timedelta(seconds=180))
    db.put_kv(f"end_at:{DEVICE}", core.iso(t0 + dt.timedelta(seconds=360)))
    texts = lambda: [r["text"] for r in db.sentences(core.iso(t0), core.iso(t0 + dt.timedelta(seconds=360)), source=DEVICE)]
    convs = lambda: db.conn().execute("SELECT start FROM conversations ORDER BY start").fetchall()

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            for _ in range(120):
                if len(convs()) == 1:
                    break
                await anyio.sleep(0.5)
            assert texts() == ["0", "1", "2"] and len(convs()) == 1, (texts(), convs())
            assert db.kv(f"end_at:{DEVICE}"), "its audio is not all final yet"
            back()
            for _ in range(120):
                if not db.kv(f"end_at:{DEVICE}"):
                    break
                await anyio.sleep(0.5)
            tg.cancel_scope.cancel()
    anyio.run(run)
    assert texts() == [str(i) for i in range(6)] and len(convs()) == 2, (texts(), convs())


@pytest.mark.parametrize("whole_s", [audio.WHOLE_S, 15])  # read whole (fresh), or as a long file (piece_blocks)
def test_covered_piece_gone_is_passed(tmp_path, monkeypatch, whole_s):
    """A piece another one covers adds nothing: its file gone does not hold the reader."""
    db.open_db(tmp_path / "t.db")
    monkeypatch.setattr(audio, "GONE_RETRY_S", 100)
    monkeypatch.setattr(audio, "WHOLE_S", whole_s)
    h = hour_ago(5)
    a, b, c = h - dt.timedelta(seconds=60), h, h + dt.timedelta(hours=1)
    save(a, flac(tone(0, 120)))  # its 2nd minute covers b
    save(b, flac(tone(1, 20)))
    save(c, flac(tone(2, 10)))
    away(b)

    async def run():
        async with reading() as s:
            got = await handed(s, 1000, 5, to=c)
        assert got[0] == a and got[-1] == c, got
    anyio.run(run)


def test_gone_while_read_is_held(tmp_path, monkeypatch):
    """The file goes between the reader picking the piece and reading it: held, not passed over as silence."""
    db.open_db(tmp_path / "t.db")
    monkeypatch.setattr(audio, "GONE_RETRY_S", 0.3)
    a, b = hour_ago(5), hour_ago(4)
    save(a, flac(tone(0, 10)))
    save(b, flac(tone(1, 10)))
    samples, reads, back = audio.samples, [], []

    def vanish(p):  # b's file goes just before its first read and is back for its third: held twice meanwhile
        if p["start_ms"] == int(b.timestamp() * 1000):
            reads.append(p)
            if len(reads) == 1:
                back.append(away(b))
            elif len(reads) == 3:
                back[0]()  # in the reader's own read, not on the clock: a file back between a read and its check is passed (L5)
        return samples(p)
    monkeypatch.setattr(audio, "samples", vanish)

    async def run():
        async with reading() as s:
            assert await handed(s, 2, 60) == [a, b], "b is not read as silence: handed once its file is back"
    anyio.run(run)
    assert len(reads) == 3


def test_long_file_gone_is_held(tmp_path, monkeypatch):
    """A long file (piece_blocks) not yet read whose file is gone: held too, read once it is back."""
    db.open_db(tmp_path / "t.db")
    monkeypatch.setattr(audio, "GONE_RETRY_S", 0.3)
    monkeypatch.setattr(audio, "WHOLE_S", 5)
    a, b = hour_ago(5), hour_ago(4)
    save(a, flac(tone(0, 10)))
    save(b, flac(tone(1, 10)))
    back, checks, gone = away(b), [], audio.gone

    def checked(p):  # the file is back at the reader's third look at it: held twice meanwhile
        if p["start_ms"] == int(b.timestamp() * 1000):
            checks.append(p)
            if len(checks) == 3:
                back()
        return gone(p)
    monkeypatch.setattr(audio, "gone", checked)

    async def run():
        async with reading() as s:
            got = await handed(s, 1000, 60, to=b)
        assert got[0] == a and got[-1] == b, got
    anyio.run(run)


@pytest.mark.parametrize("whole_s", [audio.WHOLE_S, 5])
def test_draft_reader_passes_over_a_gone_file(tmp_path, monkeypatch, whole_s):
    """The draft (edge) reader does not wait on a gone file: what is said now goes on; the final layer has the piece."""
    db.open_db(tmp_path / "t.db")
    monkeypatch.setattr(audio, "GONE_RETRY_S", 100)
    monkeypatch.setattr(audio, "WHOLE_S", whole_s)
    a, b = hour_ago(2), hour_ago(1)
    save(a, flac(tone(0, 10)))
    save(b, flac(tone(1, 10)))
    away(a)

    async def run():
        async with reading(edge_s=10 * 3600) as s:
            assert await handed(s, 1, 5) == [b]
    anyio.run(run)
