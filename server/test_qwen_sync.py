"""ENGINE=qwen-sync: Bailian's synchronous model cuts the sentences where it hears the speaker change; who said each is
left to the diarist (no engine ids), so the close relabels by voice alone. Synthetic answers, no network."""
import base64
import io
import json
import wave

import anyio
import httpx2
import numpy as np

from alldayrec import asr
from alldayrec import db
from alldayrec import qwen_asr
from test_net import mock, store  # noqa: F401

w = lambda t, a, b, s, p="": {"text": t, "begin_time": a, "end_time": b, "speaker_id": s, "punctuation": p, "fixed": True}
ANSWER = {"output": {"sentences": [
    {"sentence_id": 1, "begin_time": 0, "end_time": 2100, "text": "我们走吧，对。", "speaker_id": 0,
     "words": [w("我们", 0, 500, 0), w("走吧", 600, 1200, 0, "，"), w("对", 1700, 2000, 1, "。")]},  # two speakers in one sentence
    {"sentence_id": 2, "begin_time": 2500, "end_time": 4000, "text": "好的。", "speaker_id": 0, "words": [w("好的", 2500, 3000, 0, "。")]},
    {"sentence_id": 3, "begin_time": 4100, "end_time": 4200, "text": "", "speaker_id": 1, "words": []}]},
    "usage": {"input_tokens": 50, "output_tokens": 9}}


def test_qwen_sync(store, monkeypatch):  # noqa: F811
    """One POST with the piece as a base64 wav, diarization on and the hotwords; back the sentences cut at each word
    speaker change, without speaker ids, the empty one dropped; a wrong time-cut sentence keeps its words' text."""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dk")
    sent = []

    def handler(req):
        assert req.url.path.endswith("/services/aigc/multimodal-generation/generation") and req.headers["X-DashScope-SSE"] == "disable"
        sent.append(json.loads(req.content))
        return httpx2.Response(200, json=ANSWER)
    mock(monkeypatch, handler)
    db.conn().execute("INSERT INTO terms VALUES ('灵犀', '2026-11-18')")
    x = (np.arange(16000) % 100).astype(np.int16)
    segs = anyio.run(asr.qwen_sync, x)
    p = sent[0]["parameters"]
    assert p["speaker_diarization_enabled"] is True and p["vocabulary"]["灵犀"] == qwen_asr.WEIGHT and sent[0]["model"] == qwen_asr.SYNC_MODEL
    data = sent[0]["input"]["messages"][0]["content"][0]["input_audio"]["data"]
    assert data.startswith("data:audio/wav;base64,")
    with wave.open(io.BytesIO(base64.b64decode(data.split(",", 1)[1]))) as f:
        assert f.getframerate() == 16000 and np.array_equal(np.frombuffer(f.readframes(f.getnframes()), np.int16), x)
    assert [(g["start_ms"], g["end_ms"], g["text"], g["speaker"]) for g in segs] == [
        (0, 1200, "我们走吧，", None), (1700, 2000, "对。", None), (2500, 3000, "好的。", None)]
    assert [x["text"] for x in segs[0]["words"]] == ["我们", "走吧，"], "the words, as transcript.split_speakers cuts them"


def test_qwen_sync_alone_by_voice(store, monkeypatch):  # noqa: F811
    """No speaker ids: the final layer's voices() keeps no engine_speaker, so the close's relabel groups nothing."""
    from alldayrec import transcript
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dk")
    mock(monkeypatch, lambda req: httpx2.Response(200, json=ANSWER))
    segs = anyio.run(asr.qwen_sync, np.zeros(16000 * 5, np.int16))
    transcript.voices(lambda y: np.ones(4) / 2, segs, np.zeros(16000 * 5, np.int16))
    assert all(g["engine_speaker"] is None for g in segs)


def test_qwen_sync_quiet(store, monkeypatch):  # noqa: F811
    """A piece with nothing said: the 400 that says so is no sentences, not a failure (no fallback, no hotword retry);
    another 400 raises."""
    import pytest
    monkeypatch.setenv("DASHSCOPE_API_KEY", "dk")
    n = []

    def handler(req):
        n.append(1)
        return httpx2.Response(400, json={"code": "CLIENT_ERROR", "message": "ASR_RESPONSE_HAVE_NO_WORDS"})
    mock(monkeypatch, handler)
    assert anyio.run(asr.qwen_sync, np.zeros(1600, np.int16)) == [] and len(n) == 1
    mock(monkeypatch, lambda req: httpx2.Response(400, json={"code": "AUDIO_DURATION_TOO_LONG"}))
    with pytest.raises(Exception, match="400"):
        anyio.run(qwen_asr.transcribe_sync, np.zeros(1600, np.int16))
