"""Segmenter must give the same utterances however the audio is chunked, on the recorder's clock, and must say
an utterance is still open when the audio stops inside it (a burst of queued audio can stop anywhere)."""
import datetime as dt
import os
import subprocess

import anyio
import numpy as np

from alldayrec.segmenter import CHUNK_S, QUIET_S, SR, Segmenter, next_cut, speech_in
from alldayrec.transcript import clip_turns, final_rows, merge_drafts, overlaps

CLIP = os.environ.get("TEST_CLIP", "")  # any recorder file with speech; the test skips the audio part without it


T0 = dt.datetime(2026, 9, 9, 11, 7)


def clip():
    return np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-ss", "420", "-t", "60", "-i", CLIP, "-f", "s16le",
                                         "-ac", "1", "-ar", str(SR), "-"], capture_output=True, check=True).stdout, np.int16)


def run(sizes):
    x = clip()
    seg, t0, i, out, k = Segmenter(), T0, 0, [], 0
    while i < len(x):
        n = sizes[k % len(sizes)]; k += 1
        out += [(a, b, len(u)) for a, b, u in seg.push(t0 + dt.timedelta(seconds=i / SR), x[i:i + n])]
        i += n
    assert seg.frames == (len(x) - 400) // 160 + 1, (seg.frames, len(x))
    return out


def test_final_rows():
    from alldayrec import transcript
    from alldayrec.transcript import final_rows
    transcript.TURN_GAP_S = 1.0  # the merge case below assumes the default, whatever .env says
    t0 = dt.datetime(2026, 9, 28, 22, 0)
    ms = lambda s: int(s * 1000)
    segs = [dict(start_ms=ms(a), end_ms=ms(b), speaker=k, text=t) for a, b, k, t in [
        (1.0, 2.0, "A", "被边缘截断"), (10.0, 12.0, "A", "你定的吗？"), (12.1, 12.6, "B", "什么？"), (12.7, 14.0, "A", "就是额"),
        (14.2, 15.0, "A", "度。"), (50.0, 59.0, "B", "可能被窗口尾截断")]]
    lo, hi, rows = final_rows(segs, {"A": "他人", "B": "我"}, t0, 60.0)
    assert [(r["speaker"], r["text"]) for r in rows] == [("他人", "你定的吗？"), ("我", "什么？"), ("他人", "就是额度。")], rows
    assert lo == t0 + dt.timedelta(seconds=3) and hi == t0 + dt.timedelta(seconds=50), (lo, hi)
    _, hi, rows = final_rows(segs, {"A": "他人", "B": "我"}, t0, 60.0, edge=0.0)  # a VAD window ends in a pause: all final
    assert rows[-1]["text"] == "可能被窗口尾截断" and hi == t0 + dt.timedelta(seconds=60), (hi, rows[-1])


def test_next_cut():
    """Final pieces end in known silence: QUIET_S / 2 into a long pause, else at a full piece in its widest late pause."""
    assert next_cut([(1, 10), (12, 20)], 25, speaking=False) is None, "a short pause and little audio: wait"
    assert next_cut([(1, 10)], 50, speaking=False) == 10 + QUIET_S / 2, "a long trailing silence ends the piece"
    assert next_cut([(1, 10)], 50, speaking=True) is None, "speech runs on after the pause: its start is unknown"
    assert next_cut([(1, 10), (60, 70)], 80, speaking=True) == 10 + QUIET_S / 2, "a long pause inside"
    assert next_cut([], 40, speaking=False) == QUIET_S / 2, "silence alone: a piece the clock skips"
    busy = [(i * 10.0, i * 10.0 + 9.4) for i in range(40)]  # 0.6 s pauses only
    busy[27] = (270.0, 278.0)                                # a 2 s pause at 278-280, inside the last minute
    assert next_cut(busy, CHUNK_S + 5, speaking=True) == 279.0, "a full piece ends in the widest late pause"
    assert next_cut([(0, CHUNK_S + 10)], CHUNK_S + 10, speaking=True) == CHUNK_S, "no pause at all: at CHUNK_S"
    assert next_cut([(1, 10)], 20, speaking=False, force=True) == 20, "the recorder stopped: everything left"
    assert next_cut([(1, 10)], 20, speaking=True, force=True) == 20, "even mid-speech: the recording ended there"


def test_drafts_and_live():
    """Pages show final rows, and drafts only after their device's finalized time; a live analysis follows its
    conversation by time overlap, since drafts and final rows start it at slightly different times."""
    f = [dict(start="10:00:01", source="phone", state="final")]
    d = [dict(start="10:00:00", source="phone", state="draft"), dict(start="10:05:00", source="phone", state="draft"),
         dict(start="10:01:00", source="iphone", state="draft")]
    shown = merge_drafts(f, d, {"phone": "10:04:00", "iphone": None})
    assert [(r["start"], r["state"]) for r in shown] == [("10:00:01", "final"), ("10:01:00", "draft"), ("10:05:00", "draft")], shown
    lv = {"start": "10:00:00.5", "upto": "10:20:00"}
    assert overlaps(lv, {"start": "10:00:01", "end": "10:30:00"}), "the final conversation starts a little later"
    assert not overlaps(lv, {"start": "10:40:00", "end": "10:50:00"}) and not overlaps(lv, {"start": "09:00", "end": "09:59"})


if __name__ == "__main__":
    test_final_rows()
    test_next_cut()
    test_drafts_and_live()
    print("ok: next_cut, drafts, live overlap")
    if not CLIP:
        print("ok: final_rows (set TEST_CLIP for the segmenter check)")
        raise SystemExit
    whole = run([SR])
    odd = run([977, 16001, 333, 8000, 12345])
    assert whole == odd, (whole[:3], odd[:3])
    a, b, n = whole[0]
    assert dt.datetime(2026, 9, 9, 11, 7) <= a < b and n == round((b - a).total_seconds() * SR), whole[0]
    print(f"ok: {len(whole)} utterances, first {a.time()}-{b.time()}")
    a, b, _ = max(whole, key=lambda u: u[1] - u[0])
    seg = Segmenter()
    seg.push(T0, clip()[:int(((a - T0) + (b - a) / 2).total_seconds() * SR)])
    assert seg.speaking(), "audio ending mid-utterance must leave it open"
    print("ok: an utterance cut by the end of the audio stays open")


def test_clip_turns():
    since = 100.0
    segs = [dict(start_ms=95_000, end_ms=101_000, speaker="A", text="finalized by the previous pass"),
            dict(start_ms=99_800, end_ms=104_000, speaker="B", text="the cut turn, re-cut 0.2 s early"),
            dict(start_ms=100_500, end_ms=103_000, speaker="A", text="new")]
    out = clip_turns(segs, since)
    assert [g["text"][:3] for g in out] == ["the", "new"], out
    assert out[0]["start_ms"] == 100_000


test_clip_turns()
print("ok: clip_turns")


def test_sample_at_gap():
    """A time inside the gap between two sessions maps to the first sample after the gap, not past it (09-29 17:55)."""
    s = object.__new__(Segmenter)
    T = lambda m, sec=0: dt.datetime(2026, 9, 29, 17, m, sec)
    s.anchors = [(0, T(45)), (100 * SR, T(52))]          # 100 s of audio from 17:45, then a new session at 17:52
    assert s.sample_at(T(45, 50)) == 50 * SR
    assert s.sample_at(T(50)) == 100 * SR               # in the gap: clamp, do not extrapolate to 300 s
    assert s.sample_at(T(52, 10)) == 110 * SR


def test_zero_context_keeps_first_sentence():
    """With no context the window starts 1 s before the sentence the last pass left open: it must be finalized."""
    segs = [dict(start_ms=1000, end_ms=4000, speaker="我", text="开头这句"), dict(start_ms=5000, end_ms=8000, speaker="我", text="第二句")]
    t0 = dt.datetime(2026, 9, 29, 18)
    lo, hi, rows = final_rows(segs, {"我": "我"}, t0, 20.0, since=1.0)
    assert [r["text"] for r in rows] == ["开头这句", "第二句"] and lo == t0 + dt.timedelta(seconds=1)


def test_engine_squeeze(monkeypatch):
    """The engine gets the window with the long silences cut; its sentences, the partial ones too, come back in window time."""
    import sys
    import types

    import alldayrec
    from alldayrec import asr
    from alldayrec import squeeze
    squeeze.check()
    sent = []

    async def transcribe(x, partial=None):
        sent.append(len(x) / SR)
        us = [{"text": "一", "start_ms": 1600, "end_ms": 3400}, {"text": "二", "start_ms": 5000, "end_ms": 6000}]
        if partial:
            partial(us[:1])
        return us, {}
    fake = types.SimpleNamespace(transcribe=transcribe)
    monkeypatch.setitem(sys.modules, "alldayrec.volc_asr", fake)
    monkeypatch.setattr(alldayrec, "volc_asr", fake, raising=False)  # asr.py: from alldayrec import volc_asr
    got = []
    segs = anyio.run(asr.engine_segs, np.zeros(60 * SR, np.int16), [(10, 12), (30, 31.5)], got.append, "volc")  # kept: 8.5-13.5, 28.5-33
    assert sent == [9.5]
    assert [(g["start_ms"], g["end_ms"]) for g in got[0]] == [(10100, 11900)]
    assert [(g["start_ms"], g["end_ms"], g["engine"]) for g in segs] == [(10100, 11900, "volc"), (28500, 29500, "volc")]
    monkeypatch.setattr(asr, "SQUEEZE_KEEP_S", 0.0)  # off: the window whole, times as the engine gave them
    assert anyio.run(asr.engine_segs, np.zeros(60 * SR, np.int16), [(10, 12)], None, "volc")[1]["start_ms"] == 5000 and sent[-1] == 60


def test_engine_fallback(monkeypatch):
    """A provider that fails hands the window to ENGINE_FALLBACK (its sentences say so); both failing raises, for the
    caller's retry. stepfun (H1): the SSE text on the file job's sentences, an SSE failure raising without waiting
    (the file job cancelled)."""
    import time

    import pytest

    from alldayrec import asr
    from alldayrec import stepfun_asr
    stepfun_asr.check()
    calls, x = [], np.zeros(SR, np.int16)

    async def ok(x, partial=None):
        calls.append("ok")
        if partial:
            partial([{"text": "一", "start_ms": 0, "end_ms": 500}])
        return [{"text": "一", "start_ms": 0, "end_ms": 500}, {"text": "二", "start_ms": 600, "end_ms": 900}]

    async def bad(x, partial=None):
        calls.append("bad")
        raise RuntimeError("stepfun sse HTTP 402: balance")
    monkeypatch.setattr(asr, "PROVIDERS", {**asr.PROVIDERS, "a": bad, "b": ok, "c": bad})
    monkeypatch.setattr(asr, "SQUEEZE_KEEP_S", 0.0)
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "b")
    segs = anyio.run(asr.engine_segs, x, [], None, "a")
    assert calls == ["bad", "ok"] and [g["engine"] for g in segs] == ["b", "b"] and "402" in segs[0]["fallback"]
    got = []
    assert "fallback" not in anyio.run(asr.engine_segs, x, [], got.append, "b")[0] and got[0][0]["engine"] == "b"
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "c")
    with pytest.raises(RuntimeError, match="a: .*402.*; c: .*402"):
        anyio.run(asr.engine_segs, x, [], None, "a")
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "")
    calls.clear()
    with pytest.raises(RuntimeError):
        anyio.run(asr.engine_segs, x, [], None, "a")
    assert calls == ["bad"], "no fallback configured: the error goes to the caller"

    d = lambda t, a, b: {"delta": t, "start_time": a, "end_time": b}
    async def sse(x, on):
        return [d("你好", 100, 400), d("嗯", 5000, 5100)]

    async def diarize(x):
        return [{"start_ms": 0, "end_ms": 500, "speaker": "SPEAKER_1", "text": "你号", "words": []}]
    monkeypatch.setattr(stepfun_asr, "sse", sse)
    monkeypatch.setattr(asr, "diarize", diarize)
    assert [(g["text"], g.get("speaker"), g["engine"]) for g in anyio.run(asr.engine_segs, x, [], None, "stepfun")] == \
        [("你好", "SPEAKER_1", "stepfun"), ("嗯", None, "stepfun")]

    polled = []

    async def slow(x):
        for _ in range(3):
            await anyio.sleep(1)
            polled.append(1)
        return []

    async def refused(x, on):
        await anyio.sleep(0.2)
        raise RuntimeError("stepfun sse HTTP 451")
    monkeypatch.setattr(asr, "diarize", slow)
    monkeypatch.setattr(stepfun_asr, "sse", refused)

    async def run():
        t = time.perf_counter()
        with pytest.raises(RuntimeError, match=r"^stepfun: RuntimeError\('stepfun sse HTTP 451'\)$"):  # the SSE's own error
            await asr.engine_segs(x, [], None, "stepfun")
        assert time.perf_counter() - t < 1, "the fallback need not wait for the file job"
        await anyio.sleep(1.5)
        assert polled == [], "the file job's polling stopped"
    anyio.run(run)


def test_main_settings(monkeypatch):
    """ENGINE=qwen starts on a DashScope key and the LLM settings alone (server imported without the rest, as this
    suite is); stepfun and stepfun-file, also as the fallback, need the upload bridge and the file-mode ASR service."""
    import sys

    import pytest

    from alldayrec import asr
    from alldayrec import infer
    from alldayrec import server

    class Started(Exception):
        pass

    def started(d=None):
        raise Started
    monkeypatch.setattr(infer, "fetch", started)  # past the settings, before any model download
    monkeypatch.setattr(sys, "argv", ["alldayrec"])
    for k in ("ASR_UPLOAD_BRIDGE_URL", "ASR_UPLOAD_BRIDGE_TOKEN", "ASR_SERVICE_URL", "ASR_SERVICE_BASIC_AUTH", "STEPFUN_API_KEY",
              "VOLC_ASR_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k in ("DASHSCOPE_API_KEY", "LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setattr(asr, "ENGINE", "qwen")
    monkeypatch.setattr(asr, "DRAFT_ENGINE", "qwen-stream")
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "")
    with pytest.raises(Started):
        server.main()
    monkeypatch.setattr(asr, "ENGINE_FALLBACK", "stepfun")
    monkeypatch.setenv("STEPFUN_API_KEY", "x")
    monkeypatch.setenv("ASR_SERVICE_URL", "http://asr")
    with pytest.raises(SystemExit, match=r"not set: ASR_UPLOAD_BRIDGE_URL \(for stepfun\), ASR_UPLOAD_BRIDGE_TOKEN \(for stepfun\), "
                                         r"ASR_SERVICE_BASIC_AUTH \(for stepfun\)"):
        server.main()


def test_speech_in():
    """What the engine is sent: the loose VAD's speech, in seconds into the window, and the speech still running from
    its start (a short blip that may not become speech too)."""
    from types import SimpleNamespace as NS
    s = object.__new__(Segmenter)
    s.speech = [(90 * SR, 95 * SR), (105 * SR, 108 * SR)]
    s.loose = NS(state=NS(name="SILENCE"), frame_cnt=0, speech_cnt=0, pad_start_frame=5, last_speech_start_frame=-1)
    a0, n = 80 * SR, 40 * SR
    assert speech_in(s, a0, n) == [(10, 15), (25, 28)]
    s.loose.state.name, s.loose.last_speech_start_frame = "SPEECH", 11001  # speech from frame 11001 (1-based): 110 s
    assert speech_in(s, a0, n)[-1] == (30, 40)
    s.loose.state.name, s.loose.frame_cnt, s.loose.speech_cnt = "POSSIBLE_SPEECH", 11500, 3  # 3 frames in, 5 padded
    assert speech_in(s, a0, n)[-1] == ((11500 - 3 - 5 - a0 // 160) / 100, 40)
    assert speech_in(s, a0, 20 * SR)[-1][0] > 20, "speech after the window: nothing to keep"
