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
from alldayrec.segmenter import Segmenter
from test_backlog import DEVICE, SR, flac, hear, heard, rig, save, st, tone

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


MIN = 40  # the replay's minutes, in pieces of 10 s


def replay(tmp_path, monkeypatch, t0: dt.datetime, conc: int, asr_s: float, vad_s: float):
    """A backlog of MIN min (tones 14-30 s apart, no QUIET_S pause: CHUNK_S cuts in the widest pause of its last minute,
    and with the audio fed ending in a pause, that pause may be the widest; 7 min quiet at 20 min: two conversations;
    two voices, one 我) through the pipeline, FINAL_ASR_CONCURRENCY conc, each engine call up to asr_s, each 10 s
    piece's VAD up to vad_s; its pieces, rows and conversations."""
    rng = np.random.default_rng(conc)
    monkeypatch.setattr(device_pipeline, "FINAL_ASR_CONCURRENCY", conc)
    tmp_path.mkdir()

    async def engine(x, partial=None):
        await anyio.sleep(rng.uniform(0, asr_s))
        return [{**g, "speaker": f"SPEAKER_{int(g['text']) % 2}"} for g in heard(x)]
    rig(tmp_path, monkeypatch, engine)

    def voice(x):  # tone k: voice k % 2 (我's is voice 0)
        top = float(np.abs(x).max()) * (32768 if x.dtype.kind == "f" else 1)
        return np.eye(4)[round((top - 1000) / 50) % 2]
    monkeypatch.setitem(core.state, "embed", voice)
    push = Segmenter.apush

    async def slow(self, t, x):
        await anyio.sleep(rng.uniform(0, vad_s))  # the feed behind: a loaded host
        return await push(self, t, x)
    monkeypatch.setattr(Segmenter, "apush", slow)
    x, at, k, gaps = np.zeros(MIN * 60 * SR, np.int16), 10.0, 0, np.random.default_rng(0)
    while at < MIN * 60 - 5:
        if not 20 * 60 < at < 27 * 60:
            x[int(at * SR):int(at * SR) + 2 * SR] = tone(k, 2)
            k += 1
        at += 2 + gaps.uniform(12, 28)
    for i in range(MIN * 6):
        save(t0 + dt.timedelta(seconds=10 * i), flac(x[i * 10 * SR:(i + 1) * 10 * SR]))
    db.put_kv(f"end_at:{DEVICE}", core.iso(t0 + dt.timedelta(seconds=MIN * 60)))

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            for _ in range(1200):
                if not db.kv(f"end_at:{DEVICE}"):  # all final, the conversation closed
                    break
                await anyio.sleep(0.5)
            tg.cancel_scope.cancel()
    anyio.run(run)
    c = db.conn()
    return ([tuple(r) for r in c.execute("SELECT start_ms, end_ms, layer FROM processed_audio WHERE device = ? ORDER BY start_ms", (DEVICE,))],
            [tuple(r) for r in c.execute("SELECT start, end, speaker, text FROM sentences ORDER BY start")],
            [tuple(r) for r in c.execute("SELECT start, end, participants, sentences FROM conversations ORDER BY start")], k)


def test_cut_and_labelled_as_one_at_a_time(tmp_path, monkeypatch):
    """Side by side cuts each piece from the audio one piece at a time had fed (plan waits for the feed to rest) and
    labels in its order: the same pieces, sentences, speakers and conversations, whatever the VAD and engine take."""
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 3 * 3600)
    one = replay(tmp_path / "1", monkeypatch, t0, 1, 0.3, 0)  # the feed ahead, as it mostly is
    four = replay(tmp_path / "4", monkeypatch, t0, 4, 0, 0.3)  # finalization ahead of the feed
    pieces, rows, convs, k = one
    assert [r[3] for r in rows] == [str(i) for i in range(k)] and {r[2] for r in rows} == {"我", "S1"}, rows
    assert len(convs) == 2 and len(pieces) > 12, (convs, pieces)
    assert four == one


def test_a_lagging_feed_does_not_move_a_cut(tmp_path, monkeypatch):
    """Where CHUNK_S cuts depends on the audio fed: fed to 310 s, the pause after the tone at 285 s is the widest of
    the last minute (287-310, middle 298.5); with the next tone in at 316 s, it ends past CHUNK_S (middle 301.5), and
    the cut is 276 s. The VAD stuck on 310-320 s a while (a loaded host): the piece is still cut where the feed at
    rest cuts it, not from what was fed by then."""
    rig(tmp_path, monkeypatch, hear)
    push, stuck = Segmenter.apush, []

    async def slow(self, t, x):
        if self is st().seg and round((t - t0).total_seconds()) == 310 and not stuck:  # the final layer's
            stuck.append(t)
            await anyio.sleep(3)
        return await push(self, t, x)
    monkeypatch.setattr(Segmenter, "apush", slow)
    x = np.zeros(700 * SR, np.int16)
    for k, s in enumerate([5 + 20 * i for i in range(15)] + list(range(316, 690, 20))):
        x[s * SR:(s + 2) * SR] = tone(k, 2)
    t0 = dt.datetime.fromtimestamp(round(time.time()) - 3 * 3600)
    for i in range(70):
        save(t0 + dt.timedelta(seconds=10 * i), flac(x[i * 10 * SR:(i + 1) * 10 * SR]))
    db.put_kv(f"end_at:{DEVICE}", core.iso(t0 + dt.timedelta(seconds=700)))

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            for _ in range(240):
                if not db.kv(f"end_at:{DEVICE}"):
                    break
                await anyio.sleep(0.5)
            tg.cancel_scope.cancel()
    anyio.run(run)
    ends = [(b - t0.timestamp() * 1000) / 1000 for _, b in db.processed(DEVICE, 0, 2 ** 62, "final")]
    assert stuck and abs(ends[0] - 276) < 1.5, ends
