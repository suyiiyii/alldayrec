"""DRAFT_ENGINE qwen-stream: while a page watches, the draft stream's audio goes to one streaming session as it comes;
each sentence the engine ends is a draft row at once, the sentence still being said goes to the pages as `partial`; a
session the server ends starts again from the end of the last draft row, nothing drafted twice or lost. The pipeline
and audio store of test_backlog, a fake streaming engine that hears its tones. The session's audio goes uncompressed."""
import datetime as dt
import json
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
    a connection would."""
    opened: list = []

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
            await self.new.wait()
            self.new = anyio.Event()
            x = np.frombuffer(bytes(self.pcm), np.int16)
            for g in heard(x)[n:]:
                ended = self.done or len(x) / SR - g["end_ms"] / 1000 >= 0.5
                yield {"start_ms": g["start_ms"], "end_ms": g["end_ms"], "text": g["text"], "final": ended}
                if not ended:
                    break
                n += 1
                if len(FakeSession.opened) == 1:
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
