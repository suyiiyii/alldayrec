"""A device's pieces that overlap (what its phone sent live and its card read again over it, both kept) are one
stream: a backlog of both finalized at once has every utterance once, in time order, at its own time; playback and
listening play every moment once. Real store, stream and pipeline; test_backlog's energy VAD and fake engines."""
import datetime as dt
import time

import anyio
import numpy as np

from alldayrec import audio
from alldayrec import core
from alldayrec import db
from alldayrec import device_pipeline
from alldayrec.tenant import T
from test_backlog import DEVICE, SR, flac, hear, rig, save, tone

N, SLOT = 28, 20  # tones, one 10 s into each 20 s slot, then silence: 607 s
LIVE_S, CARD_S, CARD_AT = 20, 60, 7  # the live pieces from 0 s, the card's from 7 s: every moment in two pieces


def day(tmp_path) -> tuple[np.ndarray, dt.datetime]:
    """The audio, stored as both kinds of pieces; and where it starts."""
    x = np.zeros((N * SLOT + 47) * SR, np.int16)
    for i in range(N):
        x[(SLOT * i + 10) * SR:(SLOT * i + 13) * SR] = tone(i, 3)
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 7200)
    for a, n in [(a, LIVE_S) for a in range(0, len(x) // SR - LIVE_S + 1, LIVE_S)] + \
                [(a, CARD_S) for a in range(CARD_AT, len(x) // SR - CARD_S + 1, CARD_S)]:
        save(t0 + dt.timedelta(seconds=a), flac(x[a * SR:(a + n) * SR]))
    return x, t0


def test_backlog_once_in_order(tmp_path, monkeypatch):
    rig(tmp_path, monkeypatch, hear)
    x, t0 = day(tmp_path)
    finals = lambda: db.sentences(core.iso(t0), core.iso(t0 + dt.timedelta(seconds=len(x) / SR)), source=DEVICE)

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            for _ in range(120):
                if len(finals()) >= N:
                    break
                await anyio.sleep(0.5)
            await anyio.sleep(2)  # and nothing more comes
            tg.cancel_scope.cancel()
    anyio.run(run)
    rows = finals()
    assert [r["text"] for r in rows] == [str(i) for i in range(N)], [r["text"] for r in rows]
    for i, r in enumerate(rows):
        a, b = dt.datetime.fromisoformat(r["start"]), dt.datetime.fromisoformat(r["end"])
        assert abs((a - t0).total_seconds() - (SLOT * i + 10)) < 0.5 and 2.5 < (b - a).total_seconds() < 3.5, r


def test_playback_and_listening_once(tmp_path):
    db.open_db(tmp_path / "live.db")
    x, t0 = day(tmp_path)
    at = lambda s: t0 + dt.timedelta(seconds=s)
    same = lambda got, s: any(np.array_equal(got, x[k:k + len(got)]) for k in range(int(s * SR) - 1, int(s * SR) + 2))
    for s in (5, 18, 64, 66.5, 300, 541):  # inside a piece, across a live join, across both kinds of join
        got = np.frombuffer(audio.cut(at(s), at(s + 10), "s16le", DEVICE), np.int16)
        assert len(got) == int(10.4 * SR) and same(got, s - 0.2), s
    start, _, got = audio.newer(DEVICE, int(at(540).timestamp() * 1000))  # a listener had the live piece up to 560 s
    assert start == int(at(560).timestamp() * 1000) and len(got) == 47 * SR and same(got, 560)


def test_backlog_counts_once(tmp_path):
    """What the pages and the metrics say is left: seconds and pieces, overlaps once."""
    db.open_db(tmp_path / "live.db")
    x, t0 = day(tmp_path)
    assert audio.backlog(DEVICE, None)[0] == len(x) / SR == 607
    assert audio.backlog(DEVICE, t0 + dt.timedelta(seconds=100))[0] == 507
    # handed out: the card's 10 and the live pieces reaching past each card piece's end (0 s, then 60 s, 120 s, ... 540 s)
    assert audio.queued(DEVICE) == 20, "a live piece the card's cover whole is not handed out"
    T().read_to[DEVICE] = int(t0.timestamp() * 1000) + 60_000  # its reader has had the pieces to the live one at 60 s
    assert audio.queued(DEVICE) == 9 + 8, "the card's from 67 s on, the live ones from 120 s on"
