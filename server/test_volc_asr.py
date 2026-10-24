"""Consumer-level ASR tests against a scripted binary WebSocket peer, without network access."""
import gzip
import json
from types import SimpleNamespace

import anyio
import numpy as np
import pytest
import websockets

from alldayrec import volc_asr as asr


def answer(payload, seq=1):
    return asr.frame(asr.FULL_SERVER, asr.POS_SEQ, seq, json.dumps(payload).encode())


def error():
    body = gzip.compress(b'"rejected"')
    return bytes([0x11, 0xF0, 0x11, 0]) + (403).to_bytes(4, "big") + len(body).to_bytes(4, "big") + body


def sentence(text, start=0, definite=True):
    return {"text": text, "start_time": start, "end_time": start + 100,
            "definite": definite, "additions": {"speaker_id": 2}}


class Peer:
    def __init__(self, replies=(), *, reject_first=False, fail_reader=False, no_audio=False):
        self.replies = replies
        self.reject_first = reject_first
        self.fail_reader = fail_reader
        self.no_audio = no_audio
        self.sent = []
        self.response = SimpleNamespace(headers={"X-Tt-Logid": "local-log"})
        self.closed = False
        self.sender_cancelled = False

    async def __aenter__(self):
        self.started = anyio.Event()
        self.finished = anyio.Event()
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def send(self, wire):
        message = ({"type": asr.AUDIO_ONLY, "seq": int.from_bytes(wire[4:8], "big", signed=True)}
                   if wire[1] >> 4 == asr.AUDIO_ONLY else asr.parse(wire))
        self.sent.append((wire, message))
        if message["type"] == asr.AUDIO_ONLY:
            self.started.set()
            if message["seq"] < 0:
                self.finished.set()
            if self.fail_reader:
                try:
                    await anyio.sleep_forever()
                finally:
                    self.sender_cancelled = True

    async def recv(self):
        assert self.sent[0][1]["type"] == asr.FULL_CLIENT
        return error() if self.reject_first else answer({"result": {"text": "ack ignored"}})

    async def __aiter__(self):
        if not self.no_audio:
            await self.started.wait()
        if self.fail_reader:
            yield error()
            return
        for payload in self.replies:
            yield answer(payload)
        if not self.no_audio:
            await self.finished.wait()
        yield answer({"result": {"text": "final", "utterances": [sentence("last", 200)]}}, -9)
        raise AssertionError("consumer read past the final sequence")


def install(monkeypatch, peer):
    monkeypatch.setenv("VOLC_ASR_KEY", "local-key")
    connections = []

    def connect(url, **kwargs):
        connections.append((url, kwargs))
        return peer

    monkeypatch.setattr(websockets, "connect", connect)
    return connections


def test_full_result_partial_callbacks_and_audio_end(monkeypatch):
    peer = Peer([{"result": {"utterances": [sentence("early")]}},
                 {"result": {"utterances": [sentence("early")]}}])
    connections = install(monkeypatch, peer)
    x = np.arange(4000, dtype=np.int16)
    partials = []

    async def run():
        return await asr.transcribe(x, pace_s=0, speakers=False, partial=partials.append)

    utts, meta = anyio.run(run)
    assert utts == [{"text": "last", "start_ms": 200, "end_ms": 300, "speaker": "SPEAKER_2"}]
    assert [[u["text"] for u in p] for p in partials] == [["early"]]
    assert meta["responses"] == 3 and meta["text"] == "final" and meta["logid"] == "local-log"
    req = peer.sent[0][1]["payload"]
    assert req["request"]["result_type"] == "full" and "enable_speaker_info" not in req["request"]
    assert req["audio"] == {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1}
    audio = peer.sent[1:]
    assert [m["seq"] for _, m in audio] == [2, -3]
    assert [w[1] & 15 for w, _ in audio] == [asr.POS_SEQ, asr.NEG_WITH_SEQ]
    assert b"".join(gzip.decompress(w[12:]) for w, _ in audio) == x.tobytes()
    url, options = connections[0]
    assert url.endswith("bigmodel_nostream")
    assert options["max_size"] is None and options["ping_interval"] is None
    assert options["additional_headers"]["X-Api-Key"] == "local-key"
    assert peer.closed


def test_stream_accumulates_definite_sentences_and_untimed(monkeypatch):
    peer = Peer([{"result": {"utterances": [sentence("draft", definite=False), sentence("first"),
                                             {"text": "untimed", "definite": True},
                                             {"text": "not definite"}]}}])
    connections = install(monkeypatch, peer)

    async def run():
        return await asr.stream(np.zeros(0, np.int16), pace_s=0, end_window_ms=450)

    utts, meta = anyio.run(run)
    assert [u["text"] for u in utts] == ["first", "last"]
    assert meta["untimed"] == ["untimed"] and meta["logid"] == "local-log"
    assert "responses" not in meta
    req = peer.sent[0][1]["payload"]["request"]
    assert req["result_type"] == "single" and req["enable_nonstream"] and req["enable_speaker_info"]
    assert req["end_window_size"] == 450
    assert connections[0][0].endswith("bigmodel_async")
    assert len(peer.sent) == 2 and peer.sent[1][1]["seq"] == -2
    assert gzip.decompress(peer.sent[1][0][12:]) == b""


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("first", [False, True])
def test_server_error_closes_session_and_cancels_sender(monkeypatch, streaming, first):
    peer = Peer(reject_first=first, fail_reader=not first)
    install(monkeypatch, peer)

    async def run():
        with anyio.fail_after(1):
            if streaming:
                await asr.stream(np.zeros(9600, np.int16), pace_s=0)
            else:
                await asr.transcribe(np.zeros(9600, np.int16), pace_s=0)

    with pytest.raises((RuntimeError, ExceptionGroup)) as exc:
        anyio.run(run)

    def leaves(e):
        return [leaf for child in e.exceptions for leaf in leaves(child)] if isinstance(e, BaseExceptionGroup) else [e]

    assert all(isinstance(e, RuntimeError) and str(e) == "403: rejected" for e in leaves(exc.value))
    assert peer.closed
    assert len(peer.sent) == (1 if first else 2)
    assert peer.sender_cancelled is (not first)


def test_full_empty_audio_keeps_no_packet_boundary(monkeypatch):
    peer = Peer(no_audio=True)
    install(monkeypatch, peer)
    utts, meta = anyio.run(asr.transcribe, np.zeros(0, np.int16))
    assert len(peer.sent) == 1
    assert [u["text"] for u in utts] == ["last"] and meta["responses"] == 1
