"""DRAFT_ENGINE qwen-stream: while a page watches, the draft stream's audio goes to one streaming session as it comes;
each sentence the engine ends is a draft row at once, the sentence still being said goes to the pages as `partial`; a
session the server ends starts again from the end of the last draft row, nothing drafted twice or lost. A session opens
only with audio to send and is finished once none came for STREAM_IDLE_S: a phone with its screen off sends a minute
at once, and the service fails a session given no audio for 23 s. The pipeline and audio store of test_backlog, a fake
streaming engine that hears its tones. The session's audio goes uncompressed."""
import datetime as dt
import json
import math
import time

import anyio
import numpy as np
import websockets

from alldayrec import asr
from alldayrec import db
from alldayrec import device_pipeline
from alldayrec import qwen_stream
from alldayrec import ws
from test_backlog import DEVICE, SR, flac, heard, rig, save, st, tone


class FakeSession:
    """Every tone in the audio sent is a sentence: partial while the audio still ends in it, final once 0.5 s of quiet
    follows it (or the task is finished). The first session ends after its first final sentence, as a server closing
    a connection would (ends_first); a session given no audio for idle_s fails, as the service's do after 23 s."""
    opened: list = []
    ends_first, idle_s = True, math.inf

    async def __aenter__(self):
        self.pcm, self.done, self.new = bytearray(), False, anyio.Event()
        FakeSession.opened.append(self)
        return self

    async def __aexit__(self, *exc):
        pass

    async def send(self, pcm: bytes):
        self.pcm += pcm
        self.new.set()

    async def finish(self):
        self.done = True
        self.new.set()

    async def results(self):
        n = 0
        while True:
            with anyio.move_on_after(self.idle_s) as idle:
                await self.new.wait()
            if idle.cancelled_caught:
                raise RuntimeError(f"qwen stream: CLIENT_ERROR request timeout after {self.idle_s} seconds.")
            self.new = anyio.Event()
            x = np.frombuffer(bytes(self.pcm), np.int16)
            for g in heard(x)[n:]:
                ended = self.done or len(x) / SR - g["end_ms"] / 1000 >= 0.5
                yield {"start_ms": g["start_ms"], "end_ms": g["end_ms"], "text": g["text"], "final": ended}
                if not ended:
                    break
                n += 1
                if self.ends_first and len(FakeSession.opened) == 1:
                    return
            if self.done:
                return


def test_stream_drafts(tmp_path, monkeypatch):
    async def never(x, partial=None):  # the final layer stays out of the way: drafts only
        await anyio.sleep_forever()
    rig(tmp_path, monkeypatch, never)
    monkeypatch.setattr(asr, "DRAFT_ENGINE", "qwen-stream")
    monkeypatch.setattr(qwen_stream, "Session", FakeSession)
    FakeSession.opened = []
    pushed = []

    async def broadcast(msg):
        pushed.append(msg)
    monkeypatch.setattr(ws, "broadcast", broadcast)
    n = 4  # tones 7..10, 2 s each with 3 s of quiet after, in 1 s pieces as an app in front sends them
    now = np.concatenate([np.concatenate([tone(7 + j, 2), np.zeros(3 * SR, np.int16)]) for j in range(n)])
    pieces = [flac(now[k * SR:(k + 1) * SR]) for k in range(len(now) // SR)]
    live0 = dt.datetime.fromtimestamp(round(time.time()) + 2)

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            for k, body in enumerate(pieces):
                await anyio.sleep(live0.timestamp() + k + 1 - time.time())
                await anyio.to_thread.run_sync(save, live0 + dt.timedelta(seconds=k), body)
            for _ in range(20):
                if len(st().drafts) >= n:
                    break
                await anyio.sleep(0.5)
            drafts = db.drafts("", None, DEVICE)
            assert [r["text"] for r in drafts] == [str(7 + j) for j in range(n)], drafts
            assert all(r["engine"] == "qwen-stream" and r["state"] == "draft" for r in drafts)
            starts = [dt.datetime.fromisoformat(r["start"]) for r in drafts]
            assert all(abs((s - live0).total_seconds() - 5 * j) < 0.3 for j, s in enumerate(starts)), starts  # on the audio's clock
            assert len(FakeSession.opened) >= 2, "the session the server ended was opened again"
            partial = [m["row"]["text"] for m in pushed if m["type"] == "partial" and m["row"]]
            assert set(partial) <= {str(7 + j) for j in range(n)} and partial, pushed
            tg.cancel_scope.cancel()

    anyio.run(run)


def test_stream_idle(tmp_path, monkeypatch):
    """Time at a tenth (a session finished after 3 s without audio, the service's limit 5.5 s, a phone with its screen
    off sending 8 s at once, a device recording for 12 s after its newest audio): 1 s pieces in front go to one session;
    then each burst to a session of its own that we finish once it is heard, with only the burst in it; no session
    while no audio comes; none fails; every tone a draft row."""
    async def never(x, partial=None):
        await anyio.sleep_forever()
    rig(tmp_path, monkeypatch, never)
    monkeypatch.setattr(asr, "DRAFT_ENGINE", "qwen-stream")
    monkeypatch.setattr(qwen_stream, "Session", FakeSession)
    monkeypatch.setattr(FakeSession, "ends_first", False)
    monkeypatch.setattr(FakeSession, "idle_s", 5.5)
    monkeypatch.setattr(device_pipeline, "STREAM_IDLE_S", 3)
    monkeypatch.setattr(device_pipeline, "LIVE_AUDIO_S", 12)
    FakeSession.opened = []

    async def broadcast(msg):
        pass
    monkeypatch.setattr(ws, "broadcast", broadcast)
    quiet = lambda s: np.zeros(int(s * SR), np.int16)
    front = np.concatenate([tone(7, 2), quiet(1), tone(8, 2), quiet(1)])
    live0 = dt.datetime.fromtimestamp(round(time.time()) + 2)
    pieces = [(k, k + 1, flac(front[k * SR:(k + 1) * SR])) for k in range(6)]  # (audio start, arrival, body) in s from live0
    pieces += [(6 + 8 * j, 14 + 8 * j, flac(np.concatenate([quiet(2), tone(9 + j, 2), quiet(4)]))) for j in range(2)]

    async def run():
        async with anyio.create_task_group() as tg:
            tg.start_soon(device_pipeline.pipeline, DEVICE)
            for start, at, body in pieces:
                await anyio.sleep(live0.timestamp() + at - time.time())
                await anyio.to_thread.run_sync(save, live0 + dt.timedelta(seconds=start), body)
            await anyio.sleep(live0.timestamp() + 22 + 9 - time.time())
            assert st().drafting, "the device still records: a session could open"
            await anyio.sleep(live0.timestamp() + 22 + 12 + 1.5 - time.time())
            assert [r["text"] for r in db.drafts("", None, DEVICE)] == ["7", "8", "9", "10"]
            assert [len(s.pcm) / 2 / SR for s in FakeSession.opened] == [6, 8, 8]
            assert all(s.done for s in FakeSession.opened)
            calls = db.conn().execute("SELECT error FROM calls WHERE kind = 'draft'").fetchall()
            assert [c["error"] for c in calls] == [None] * 3, calls
            assert len(st().latency) == 4, "counted over the watch, not per session"
            tg.cancel_scope.cancel()

    anyio.run(run)


def test_session_no_deflate(monkeypatch):
    """No permessage-deflate on the session: deflating a page's first DRAFT_BACK_S of PCM in one send held the event
    loop up to 0.76 s (10-24), for 13-40 % fewer bytes."""
    got = {}

    class Peer:
        async def send(self, m):
            pass

        async def recv(self):
            return json.dumps({"header": {"event": "task-started"}})

        async def close(self):
            pass

    async def connect(url, **kw):
        got.update(kw)
        return Peer()
    monkeypatch.setattr(websockets, "connect", connect)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "k")

    async def run():
        async with qwen_stream.Session():
            pass
    anyio.run(run)
    assert "compression" in got and got["compression"] is None, got
