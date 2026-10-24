"""Bailian qwen-audio-3.1-asr-flash-streaming over a WebSocket, for the draft layer (DRAFT_ENGINE=qwen-stream): audio in
as it comes, each sentence back as it grows and once it ends. On the GT cases (2026-10-19, home case in real time) the
words came ~0.3 s behind the audio and a sentence's final text ~1.1 s after it ended (p90 2.1 s); billed by tokens,
¥0.25 an hour of audio sent.
"""
import json
import os
import uuid

import websockets

MODEL = "qwen-audio-3.1-asr-flash-streaming"
URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
FRAME = 32000  # bytes a message: 1 s of 16 kHz int16


class Session:
    """One recognition task: send(pcm) as the audio comes, finish() when no more comes; results() yields each sentence
    as it changes, {start_ms, end_ms, text, final}, ms since the first sample sent, until the task has finished.
    Raises on task-failed and on a connection closed before the task finished."""

    async def __aenter__(self):
        self.task = uuid.uuid4().hex
        self.ws = await websockets.connect(URL, additional_headers={"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"}, max_size=None)
        await self.ws.send(json.dumps({"header": {"action": "run-task", "task_id": self.task, "streaming": "duplex"}, "payload": {
            "task_group": "audio", "task": "asr", "function": "recognition", "model": MODEL,
            "parameters": {"format": "pcm", "sample_rate": 16000, "language_hints": ["zh", "en"]}, "input": {}}}))
        m = json.loads(await self.ws.recv())
        if m["header"]["event"] != "task-started":
            await self.ws.close()
            raise RuntimeError(f"qwen stream: {m['header'].get('error_code')} {m['header'].get('error_message')}")
        return self

    async def __aexit__(self, *exc):
        await self.ws.close()

    async def send(self, pcm: bytes):
        for k in range(0, len(pcm), FRAME):
            await self.ws.send(pcm[k:k + FRAME])

    async def finish(self):
        await self.ws.send(json.dumps({"header": {"action": "finish-task", "task_id": self.task, "streaming": "duplex"}, "payload": {"input": {}}}))

    async def results(self):
        async for raw in self.ws:
            m = json.loads(raw)
            event = m["header"]["event"]
            if event == "task-finished":
                return
            if event == "task-failed":
                raise RuntimeError(f"qwen stream: {m['header'].get('error_code')} {m['header'].get('error_message')}")
            s = m.get("payload", {}).get("output", {}).get("sentence") or {}
            if event != "result-generated" or s.get("heartbeat") or s.get("begin_time") is None:
                continue
            end = s.get("end_time") or (s.get("words") or [{}])[-1].get("end_time") or s["begin_time"]
            yield {"start_ms": s["begin_time"], "end_ms": end, "text": s.get("text", ""), "final": bool(s.get("sentence_end"))}
        raise RuntimeError("qwen stream: connection closed before the task finished")
