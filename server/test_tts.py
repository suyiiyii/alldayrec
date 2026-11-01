"""A device question's answer read aloud (tts): sentences out of the streamed text, sources out, the audio on /ws in
order between speak and spoken, tagged with the question's id."""
import base64
import json
import math

import anyio

from alldayrec import db
from alldayrec import tts
from alldayrec import ws
from alldayrec.tenant import T


def test_split():
    assert tts.split("好的，") == ([], "好的，"), "too short: waits for the next"
    assert tts.split("好的，今天聊了三件事。上午") == (["好的，今天聊了三件事。"], "上午")
    assert tts.split("定了按小时存 [c2026110") == ([], "定了按小时存 [c2026110"), "a source cut in two waits for its ]"
    assert tts.split("定了按小时存 [c20261101103000]。**下周**再说") == (["定了按小时存 。"], "下周再说")
    assert tts.split("我查一下", end=True) == (["我查一下"], "")
    assert tts.split("[c20261101103000", end=True) == ([], "") and tts.split("。", end=True) == ([], "")


class FakeTTS:
    """Qwen realtime TTS: each commit answers with its text's length x 1000 bytes of PCM, in two deltas."""

    def __init__(self):
        self.events, (self.tx, self.rx) = [], anyio.create_memory_object_stream(math.inf)
        self.text = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def send(self, raw):
        m = json.loads(raw)
        self.events.append(m)
        if m["type"] == "input_text_buffer.append":
            self.text += m["text"]
        elif m["type"] == "input_text_buffer.commit":
            pcm, self.text = bytes(range(256)) * (len(self.text) * 1000 // 256) + b"\0" * (len(self.text) * 1000 % 256), ""
            for half in (pcm[:len(pcm) // 2], pcm[len(pcm) // 2:]):
                self.tx.send_nowait(json.dumps({"type": "response.audio.delta", "delta": base64.b64encode(half).decode()}))
        elif m["type"] == "session.finish":
            self.tx.send_nowait(json.dumps({"type": "session.finished"}))

    def __aiter__(self):
        return self.rx.__aiter__()


def test_speech(tmp_path, monkeypatch):
    db.open_db(tmp_path / "t.db")  # a tenant
    fake, sent = FakeTTS(), []
    async def broadcast(m):
        sent.append(m)
    monkeypatch.setattr(ws, "broadcast", broadcast)
    monkeypatch.setattr(tts, "connect", lambda: fake)
    monkeypatch.setattr(tts, "LEAD_S", 100)  # no pacing here
    monkeypatch.setenv("DASHSCOPE_TTS_API_KEY", "k")

    async def main():
        async with anyio.create_task_group() as tg:
            T().tg = tg
            try:
                say = tts.Speech("dev", "a1")
                for m in [{"stage": "heard", "q": "今天聊了什么"}, {"delta": "我查一"}, {"delta": "下"}, {"stage": "searching"},
                          {"delta": "今天聊了存储 [c2026"}, {"delta": "1101100000]，还有"}, {"delta": "午饭。"}, {"stage": "done", "answer": "x"}]:
                    say.feed(m)
            finally:
                T().tg = None
    anyio.run(main)
    texts = [m["text"] for m in fake.events if m["type"] == "input_text_buffer.append"]
    assert texts == ["我查一下", "今天聊了存储 ，", "还有午饭。"], texts
    assert fake.events[0]["type"] == "session.update" and fake.events[0]["session"]["sample_rate"] == 16000
    assert fake.events[-1]["type"] == "session.finish"
    assert sent[0] == {"type": "ask", "device": "dev", "id": "a1", "stage": "speak"}
    assert sent[-1] == {"type": "ask", "device": "dev", "id": "a1", "stage": "spoken"}
    frames = sent[1:-1]
    assert all(isinstance(f, bytes) and f.startswith(b"a1\n") and len(f) - 3 <= tts.FRAME for f in frames)
    pcm = b"".join(f[3:] for f in frames)
    want = b"".join(bytes(range(256)) * (len(t) * 1000 // 256) + b"\0" * (len(t) * 1000 % 256) for t in texts)
    assert pcm == want, "every byte, in order"


def test_off_without_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_TTS_API_KEY", raising=False)
    say = tts.Speech("dev", "a1")
    say.feed({"delta": "今天聊了三件事。"})
    assert not say.on
