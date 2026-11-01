"""The answer to a question said to a device, read aloud (ask.live): its text as it streams, cut into sentences and sent
to Bailian's Qwen realtime TTS, the audio pushed on /ws to the device that asked.

On /ws: {"type": "ask", "device", "id", "stage": "speak"} before the first audio, then binary frames
`<id>\\n<PCM>` (16 kHz mono int16 little-endian, at most FRAME bytes of PCM a frame, sent at most LEAD_S ahead of
playback), then {"stage": "spoken"} after the last (also when the speech broke off). Billed by characters read
(¥1 per 10 000, 2026-11).
"""
import base64
import json
import logging
import math
import os
import re
import time
import uuid

import anyio
import websockets

from alldayrec import ws
from alldayrec.tenant import T

log = logging.getLogger(__name__)

MODEL = "qwen3-tts-flash-realtime"
VOICE = "Cherry"   # a bright young woman's voice
SR = 16000         # asked of the TTS as is: what the device's player takes
FRAME = 3200       # PCM bytes a /ws frame: 0.1 s, well under the device's TLS record
LEAD_S = 1.0       # audio ahead of the device's playback at most (its buffer holds 3 s)
MIN_CHARS = 5      # a shorter sentence goes with the next one
BREAK = re.compile(r"[。！？；，,!?;\n]")
CLEAN = re.compile(r"\[c[^\]]*(?:\]|$)|[*#`]")  # sources ([c20261101103000]) and markdown are not read


def split(buf: str, end: bool = False) -> tuple[list[str], str]:
    """The sentences of buf ready to be read (sources out, short ones joined to the next) and what is held back: the
    words after the last break, and a `[` that may be a source cut in two by the stream. end: all of it."""
    tail = ""
    if not end and (m := re.search(r"\[[^\]]*$", buf)):
        buf, tail = buf[:m.start()], buf[m.start():]
    text, out, at = CLEAN.sub("", buf), [], 0
    for m in BREAK.finditer(text):
        if len(text[at:m.end()].strip()) >= MIN_CHARS:
            out.append(text[at:m.end()].strip())
            at = m.end()
    rest = text[at:] + tail
    if end:
        if re.search(r"\w", rest):
            out.append(rest.strip())
        rest = ""
    return out, rest


def connect():
    return websockets.connect(f"wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model={MODEL}", max_size=None,
                              additional_headers={"Authorization": f"Bearer {os.environ['DASHSCOPE_TTS_API_KEY']}"})


class Speech:
    """One question's answer read aloud: feed() it each message ask.live pushes. The TTS session opens at once (while
    the question is heard and the model thinks); the answer's deltas go in by the sentence, the words before a round
    of tools as they are, an error's reason too; done or error ends it. Off without DASHSCOPE_TTS_API_KEY (a key of
    its own, for TTS alone) or the tenant's tasks."""

    def __init__(self, device: str, id: str):
        self.device, self.id, self.buf, self.t0 = device, id, "", None
        self.tx, self.rx = anyio.create_memory_object_stream(math.inf)
        self.on = bool(os.environ.get("DASHSCOPE_TTS_API_KEY") and T().tg)
        if self.on:
            T().tg.start_soon(self.run, name=f"tts:{id}")

    def feed(self, m: dict):
        if not self.on:
            return
        stage = m.get("stage")
        self.buf += m.get("delta") or (m.get("error") or "" if stage == "error" else "")
        if self.t0 is None and self.buf:
            self.t0 = time.monotonic()
        out, self.buf = split(self.buf, stage in ("searching", "done", "error"))
        for s in out:
            self.tx.send_nowait(s)
        if stage in ("done", "error"):
            self.on = False
            self.tx.close()

    async def send(self, m: dict | bytes):
        await ws.broadcast(m if isinstance(m, bytes) else {"type": "ask", "device": self.device, "id": self.id, **m})

    async def run(self):
        started, sent_s, first = False, 0.0, None
        try:
            async with connect() as c:
                ev = lambda **m: c.send(json.dumps({"event_id": "event_" + uuid.uuid4().hex, **m}))
                await ev(type="session.update", session={"voice": VOICE, "mode": "commit", "response_format": "pcm",
                                                         "sample_rate": SR, "language_type": "Chinese"})

                async def play():
                    nonlocal started, sent_s, first
                    pcm, ahead = b"", time.monotonic()  # ahead: when the device has played all sent so far
                    async for raw in c:
                        m = json.loads(raw)
                        if m["type"] == "error":
                            raise RuntimeError(m.get("error"))
                        done = m["type"] == "session.finished"
                        if m["type"] == "response.audio.delta":
                            pcm += base64.b64decode(m["delta"])
                        while len(pcm) >= FRAME or (done and pcm):
                            if not started:
                                started, first = True, time.monotonic() - (self.t0 or time.monotonic())
                                await self.send({"stage": "speak"})
                            if (wait := ahead - time.monotonic() - LEAD_S) > 0:
                                await anyio.sleep(wait)
                            frame, pcm = pcm[:FRAME], pcm[FRAME:]
                            await self.send(self.id.encode() + b"\n" + frame)
                            ahead = max(ahead, time.monotonic()) + len(frame) / (2 * SR)
                            sent_s += len(frame) / (2 * SR)
                        if done:  # spoken now: closing the TTS's socket can take seconds
                            started = False
                            return await self.send({"stage": "spoken"})

                async with anyio.create_task_group() as tg:
                    tg.start_soon(play)
                    async for s in self.rx:
                        await ev(type="input_text_buffer.append", text=s)
                        await ev(type="input_text_buffer.commit")
                    await ev(type="session.finish")
            log.info("tts %s: first audio %.2f s after the first words, %.1f s read", self.id, first or 0, sent_s)
        except Exception as e:  # no connection, the TTS's error: the answer is still on the screen
            log.warning("tts %s failed after %.1f s of audio: %r", self.id, sent_s, e)
        finally:
            self.on = False
            self.rx.close()
            if started:  # broke off
                await self.send({"stage": "spoken"})
