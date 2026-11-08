"""A backlog's final pieces go to the engine side by side (FINAL_ASR_CONCURRENCY), answers coming back out of order and
one failing once, yet are labelled and committed in time order, every utterance once. Real store, stream and pipeline;
test_backlog's energy VAD and fake engines."""
import datetime as dt
import time

import anyio
import numpy as np

from alldayrec import core
from alldayrec import db
from alldayrec import device_pipeline
from test_backlog import DEVICE, SR, flac, heard, rig, save, tone

N, PIECE_S = 24, 60  # a tone 10 s into each piece


def test_side_by_side_committed_in_order(tmp_path, monkeypatch):
    calls, busy, failed = [], [0, 0], set()

    async def engine(x, partial=None):
        segs = heard(x)
        k = int(segs[0]["text"]) if segs else -1
        busy[0] += 1
        busy[1] = max(busy[1], busy[0])
        try:
            await anyio.sleep(0.4 * (3 - k % 4))  # each later one of four answers first
            if k == 5 and k not in failed:
                failed.add(k)
                raise RuntimeError("engine down")
        finally:
            busy[0] -= 1
        calls.append(k)
        return segs
    rig(tmp_path, monkeypatch, engine)
    los = []
    replace = db.replace_sentences
    monkeypatch.setattr(db, "replace_sentences", lambda t, lo, hi, rows, device: (los.append(lo), replace(t, lo, hi, rows, device)))
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 2 * N * PIECE_S)
    silence = np.zeros(PIECE_S * SR, np.int16)
    for i in range(N):
        save(t0 + dt.timedelta(seconds=PIECE_S * i), flac(np.concatenate([silence[:10 * SR], tone(i, 3), silence[13 * SR:]])))
    db.put_kv(f"end_at:{DEVICE}", core.iso(t0 + dt.timedelta(seconds=N * PIECE_S)))
    finals = lambda: db.sentences(core.iso(t0), core.iso(t0 + dt.timedelta(seconds=N * PIECE_S)), source=DEVICE)

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            for _ in range(240):
                if len(finals()) >= N:
                    break
                await anyio.sleep(0.5)
            tg.cancel_scope.cancel()
    anyio.run(run)
    assert [r["text"] for r in finals()] == [str(i) for i in range(N)]
    assert los == sorted(los) and len(los) == len(set(los)), "committed in time order, each piece once"
    assert 1 < busy[1] <= device_pipeline.FINAL_ASR_CONCURRENCY, busy
    assert calls != sorted(calls) and sorted(calls) == list(range(N)), "answered out of order, each once (5 after its retry)"
    assert failed == {5}
