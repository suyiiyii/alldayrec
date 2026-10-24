"""StepFun stepaudio-2.5-asr for engine_segs: the pay-as-you-go SSE call (text with per-character times, no
sentences, no speakers), its characters cut into sentences, and the merge onto the sentences and speakers of a file
job on the same audio.

    uv run python -m alldayrec.stepfun_asr     the self-check (no network)
"""
import base64
import json
import os

import anyio.to_thread
import numpy as np

from alldayrec import net
from alldayrec import threads

URL = os.environ.get("STEPFUN_SSE_URL") or "https://api.stepfun.com/v1/audio/asr/sse"  # pay-as-you-go; /step_plan/v1/... is the Plan's
ACCOUNT = "https://api.stepfun.com/v1/accounts"  # the pay-as-you-go account the key draws on
GAP_MS = 600     # a pause this long between characters ends a sentence
REACH_MS = 1000  # a character this close to a file sentence joins it (the rule of replay_case.words_to_turns)
END = "。？！?!"   # ... and these end one


async def balance() -> float:
    """The pay-as-you-go balance, CNY."""
    r = await net.request("GET", ACCOUNT, 30, headers={"Authorization": f"Bearer {os.environ['STEPFUN_API_KEY']}", "User-Agent": "curl/8"})
    return float(json.loads(r.content)["balance"])


def _body(x: np.ndarray) -> bytes:
    return json.dumps({"audio": {"data": base64.b64encode(x.tobytes()).decode(), "input": {
        "transcription": {"model": "stepaudio-2.5-asr", "language": "zh", "enable_itn": True, "enable_timestamp": True},
        "format": {"type": "pcm", "codec": "pcm_s16le", "rate": 16000, "bits": 16, "channel": 1}}}}).encode()


async def sse(x: np.ndarray, on_sentence=None) -> list[dict]:
    """x: 16 kHz int16 PCM. Its deltas [{delta, start_time, end_time}] (ms into x), runs on one time spread out
    (spread: every reader, final and draft, gets them so); on_sentence(deltas so far) whenever one ends a sentence or
    starts one after a pause (see sentences), as each arrives.
    Raises on an HTTP error (402 balance, 429 rate, 451 refused) and unless transcript.text.done arrives (a cut stream)."""
    body = await anyio.to_thread.run_sync(_body, x, limiter=threads.MEDIA)  # a 5 min window is 13 MB of JSON: ~35 ms, off the event loop
    out = []
    with net.errors():
        async with net.client(60) as c, c.stream("POST", URL, content=body, headers={
                "Authorization": f"Bearer {os.environ['STEPFUN_API_KEY']}", "User-Agent": "curl/8",
                "Content-Type": "application/json", "Accept": "text/event-stream"}) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"stepfun sse HTTP {r.status_code}: {(await r.aread())[:200].decode(errors='replace')}")
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                m = json.loads(line[5:])
                if m.get("type") == "transcript.text.delta":
                    t, a = (m.get("delta") or "").rstrip(), m.get("start_time")
                    pause = bool(out) and a is not None and a - (out[-1].get("end_time") or a) > GAP_MS
                    out.append(m)
                    if on_sentence and (pause or t[-1:] and t[-1] in END):
                        on_sentence(spread(out))
                elif m.get("type") == "transcript.text.done":
                    return spread(out, len(x) / 16)  # x's ms at 16 kHz
                elif "error" in m or m.get("type", "").endswith("error"):
                    raise RuntimeError(f"stepfun sse: {str(m)[:200]}")
    raise RuntimeError(f"stepfun sse: no transcript.text.done after {len(out)} deltas")


def spread(deltas: list[dict], end_ms: float | None = None) -> list[dict]:
    """The deltas, each run of them on one time (the same start_time and end_time) spread evenly from the end of the
    delta before it (0 at the start) to the start of the one after it (end_ms, the audio's end, at the end): StepFun
    sometimes gives a stretch of speech one time (10-15: 460 deltas, 118 s, on one instant: rows of no length,
    every file sentence of the stretch dropped). A run the delta after it does not follow (it starts on or before the
    run's time: also each burst of fast speech) stays as it is. end_ms None (the stream goes on): a run at the end is
    left out until the delta after it comes. Copies; the deltas given stay as they are."""
    out, i = [], 0
    while i < len(deltas):
        j, t = i + 1, (deltas[i].get("start_time"), deltas[i].get("end_time"))
        while j < len(deltas) and (deltas[j].get("start_time"), deltas[j].get("end_time")) == t:
            j += 1
        run = [dict(x) for x in deltas[i:j]]
        if len(run) > 1 and t[0] is not None:
            if j == len(deltas) and end_ms is None:
                break
            a = next((x["end_time"] for x in reversed(deltas[:i]) if x.get("end_time") is not None), 0)
            b = next((x["start_time"] for x in deltas[j:] if x.get("start_time") is not None), end_ms)
            if b is not None and b > max(a, t[0]):
                step = (b - a) / len(run)
                for k, x in enumerate(run):
                    x["start_time"], x["end_time"] = round(a + k * step), round(a + (k + 1) * step)
        out += run
        i = j
    return out


def sentences(deltas: list[dict]) -> list[dict]:
    """Deltas -> [{text, start_ms, end_ms}]: a sentence ends after 。？！?! or where the next delta starts over GAP_MS
    after the last one ended."""
    segs, cur = [], None
    for d in deltas:
        t, a, b = d.get("delta") or "", d.get("start_time"), d.get("end_time")
        if not t:
            continue
        if cur and a is not None and a - cur["end_ms"] > GAP_MS:
            segs.append(cur)
            cur = None
        if cur is None:
            cur = {"text": "", "start_ms": a, "end_ms": b}
        cur["text"] += t
        cur["end_ms"] = b if b is not None else cur["end_ms"]
        if t.rstrip()[-1:] in END:
            segs.append(cur)
            cur = None
    if cur:
        segs.append(cur)
    return [g for g in segs if g["text"].strip() and g["start_ms"] is not None]


def merge(deltas: list[dict], file_segs: list[dict]) -> list[dict]:
    """The file job's sentences and speakers with the SSE text: each character goes to the sentence its midpoint
    falls in, or the nearest within REACH_MS; characters no sentence takes become sentences of their own, without a
    speaker (the diarist labels them one by one); a file sentence no character went to is dropped."""
    turns = sorted(({"start_ms": g["start_ms"], "end_ms": g["end_ms"], "speaker": g.get("speaker"), "text": ""} for g in file_segs),
                   key=lambda g: g["start_ms"])
    left = []
    for d in deltas:
        if not d.get("delta") or d.get("start_time") is None:
            continue
        mid = (d["start_time"] + (d.get("end_time") or d["start_time"])) / 2
        dist = lambda g: max(0, g["start_ms"] - mid, mid - g["end_ms"])
        best = min(turns, key=dist, default=None)
        if best is None or dist(best) > REACH_MS:
            left.append(d)
        else:
            best["text"] += d["delta"]
    return sorted([g for g in turns if g["text"].strip()] + sentences(left), key=lambda g: g["start_ms"])


def check():
    d = lambda t, a, b: {"delta": t, "start_time": a, "end_time": b}
    ds = [d("你", 0, 100), d("好。", 100, 300), d("在", 400, 500), d("吗", 1200, 1300), d("嗯", 9000, 9100)]
    assert [(g["text"], g["start_ms"], g["end_ms"]) for g in sentences(ds)] == [("你好。", 0, 300), ("在", 400, 500), ("吗", 1200, 1300), ("嗯", 9000, 9100)]
    assert sentences([d("", 0, 0), d("。", None, None)]) == []
    f = [{"start_ms": 0, "end_ms": 450, "speaker": "SPEAKER_0", "text": "你好"}, {"start_ms": 1000, "end_ms": 2000, "speaker": "SPEAKER_1", "text": "在吗"},
         {"start_ms": 3000, "end_ms": 4000, "speaker": "SPEAKER_0", "text": "没人说"}]
    got = merge(ds, f)
    # 在 (mid 450) sits on the first sentence's end; 吗 in the second; the third has no character: dropped; 嗯 is 5 s from any: alone
    assert [(g["text"], g["start_ms"], g.get("speaker")) for g in got] == [("你好。在", 0, "SPEAKER_0"), ("吗", 1000, "SPEAKER_1"), ("嗯", 9000, None)], got
    assert [g["text"] for g in merge(ds, [])] == ["你好。", "在", "吗", "嗯"], "no file sentences: SSE sentences, no speakers"
    assert "speaker" not in merge([d("远", 3200, 3300)], f[:2])[0], "1.2 s past the nearest sentence: out of reach"


if __name__ == "__main__":
    check()
    print("ok")
