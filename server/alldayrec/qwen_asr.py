"""Alibaba Bailian (DashScope) qwen-audio-3.1-asr-flash-filetrans for engine_segs: text, sentences and speakers from one
async file job. On the five GT cases (2026-10-19, the same ≤ 5 min blocks) its text beat StepFun's SSE on four (home
11.6% CER vs 13.6%), its speakers about as StepFun's file job; billed by tokens, ~¥0.05 an hour of audio.

The window goes up lossless (16 kHz wav: StepFun's file job lost ~3 points CER on 16 kbps Opus) into Bailian's own
temporary storage (an oss:// URL for 48 h, bound to the model; free) rather than the upload bridge: 10-20 the bridge
through the proxy timed out for hours, and Bailian fetching from it took 2–5 s more a job. The docs say the storage is
not for production (100 QPS of policies per account and model); a window it fails goes to ENGINE_FALLBACK like any
other failure. The built-in sensitive-word masking (words turned into ***) is off.
"""
import base64
import io
import itertools
import json
import os
import time
import urllib.error
import wave

import anyio
import numpy as np

from alldayrec import gain
from alldayrec import net

MODEL = os.environ.get("QWEN_ASR_MODEL") or "qwen-audio-3.1-asr-flash-filetrans"
BASE = "https://dashscope.aliyuncs.com/api/v1"
POLL_S = 2
WAIT_S = 900
# hotwords (parameters.vocabulary, free, no list made beforehand): at most 2000 a request, weight 1-5 (4 the docs' start);
# one with a non-ASCII character up to 15 characters, an ASCII one up to 7 words (help.aliyun.com/en/model-studio/improve-asr-accuracy)
HOTWORDS, WEIGHT = 2000, 4
NOTHING_SAID = ("ASR_RESPONSE_HAVE_NO_WORDS", "SUCCESS_WITH_NO_VALID_FRAGMENT")  # 10-19: a quiet window; one under a second


def fits(w: str) -> bool:
    """A hotword Bailian takes."""
    return len(w.split()) <= 7 if w.isascii() else len(w) <= 15


def wav(x: np.ndarray) -> bytes:
    """16 kHz int16 mono PCM as a wav file."""
    f = io.BytesIO()
    with wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(gain.to16(x).tobytes())
    return f.getvalue()


def sentences(transcripts: list[dict]) -> list[dict]:
    """A transcription's sentences as engine_segs takes them, speaker ids as SPEAKER_<n> (None without one), with their
    words (each its text and punctuation; their texts join to the sentence's): transcript.split_speakers cuts a sentence
    at a word where the voice changes. A sentence ends where its last word does: the engine's end can run on past it
    (#419: 0.4% of the rows, 2.3% of the time, a 192 s row of 13 characters)."""
    out = []
    for t in transcripts[:1]:
        for s in t.get("sentences", []):
            if not s.get("text", "").strip():
                continue
            words = [{"start_ms": w["begin_time"], "end_ms": w["end_time"], "text": w.get("text", "") + (w.get("punctuation") or "")}
                     for w in s.get("words") or []]
            out.append({"start_ms": s["begin_time"], "end_ms": min(s["end_time"], words[-1]["end_ms"]) if words else s["end_time"],
                        "text": s["text"], "speaker": None if s.get("speaker_id") is None else f"SPEAKER_{s['speaker_id']}",
                        **({"words": words} if words else {})})
    return out


async def upload(data: bytes, auth: dict) -> str:
    """A wav into Bailian's temporary storage for MODEL (a policy, then a form POST to its OSS): its oss:// URL."""
    p = json.loads((await net.request("GET", f"{BASE}/uploads", 60, params={"action": "getPolicy", "model": MODEL},
                                      headers=auth)).content)["data"]
    key = f"{p['upload_dir']}/live.wav"
    await net.request("POST", p["upload_host"], 60, files={"file": ("live.wav", data, "audio/wav")}, data={
        "OSSAccessKeyId": p["oss_access_key_id"], "Signature": p["signature"], "policy": p["policy"], "key": key,
        "x-oss-object-acl": p["x_oss_object_acl"], "x-oss-forbid-overwrite": p["x_oss_forbid_overwrite"],
        "success_action_status": "200"})
    return f"oss://{key}"


async def transcribe(x: np.ndarray, words=()) -> list[dict]:
    """x: 16 kHz int16 PCM; words: hotwords (fits, at most HOTWORDS) to listen for. Raises on an HTTP error, a failed
    task, or one still running after WAIT_S."""
    auth = {"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"}
    url = await upload(wav(x), auth)
    auth = {**auth, "Content-Type": "application/json"}
    body = {"model": MODEL, "input": {"file_urls": [url]}, "parameters": {
        "channel_id": [0], "language_hints": ["zh", "en"], "diarization_enabled": True,
        "special_word_filter": json.dumps({"system_reserved_filter": False}),
        **({"vocabulary": dict.fromkeys(words, WEIGHT)} if words else {})}}
    r = await net.request("POST", f"{BASE}/services/audio/asr/transcription", 60, content=json.dumps(body).encode(),
                          headers={**auth, "X-DashScope-Async": "enable", "X-DashScope-OssResourceResolve": "enable"})
    task = json.loads(r.content)["output"]["task_id"]
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        await anyio.sleep(POLL_S)
        o = json.loads((await net.request("GET", f"{BASE}/tasks/{task}", 60, headers=auth)).content)["output"]
        if o["task_status"] in ("PENDING", "RUNNING"):
            continue
        res = (o.get("results") or [{}])[0]
        if (res.get("code") or o.get("code")) in NOTHING_SAID:  # a failed task, no sentences
            return []
        if o["task_status"] != "SUCCEEDED" or res.get("subtask_status", "SUCCEEDED") != "SUCCEEDED":
            raise RuntimeError(f"qwen asr {o['task_status']}: {res.get('code') or o.get('code')} {res.get('message') or o.get('message')}"[:200])
        return sentences(json.loads((await net.request("GET", res["transcription_url"], 60)).content).get("transcripts", []))
    raise RuntimeError(f"qwen asr task {task} still running after {WAIT_S} s")


# ENGINE=qwen-sync: the same family's synchronous model, one request a piece (<= 5 min, the docs' limit; CHUNK_S is 300 s)
SYNC_MODEL = os.environ.get("QWEN_SYNC_MODEL") or "qwen-audio-3.1-asr-flash"
SYNC_WAIT_S = 180


def by_turns(sents: list[dict]) -> list[dict]:
    """The sync model's sentences (its JSON) cut where their words' speaker ids change: a sentence may hold two."""
    out = []
    for s in sents:
        runs = [list(r) for _, r in itertools.groupby(s.get("words") or [], key=lambda w: w.get("speaker_id"))]
        out += [s] if len(runs) < 2 else [
            {**s, "begin_time": r[0]["begin_time"], "end_time": r[-1]["end_time"], "speaker_id": r[0].get("speaker_id"), "words": r,
             "text": "".join(w.get("text", "") + (w.get("punctuation") or "") for w in r)} for r in runs]
    return out


async def transcribe_sync(x: np.ndarray, words=()) -> list[dict]:
    """x: 16 kHz int16 PCM, at most 5 min; words: hotwords. One synchronous request (base64 wav in the body): its
    sentences with words, cut at its speaker changes (by_turns), without speaker ids; none for a piece it hears nothing
    in. Raises on an HTTP error."""
    body = {"model": SYNC_MODEL, "input": {"messages": [{"role": "user", "content": [
        {"type": "input_audio", "input_audio": {"data": "data:audio/wav;base64," + base64.b64encode(wav(x)).decode()}}]}]},
        "parameters": {"format": "wav", "sample_rate": "16000", "speaker_diarization_enabled": True,
                       **({"vocabulary": dict.fromkeys(words, WEIGHT)} if words else {})}}
    try:
        r = await net.request("POST", f"{BASE}/services/aigc/multimodal-generation/generation", SYNC_WAIT_S, content=json.dumps(body).encode(),
                              headers={"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}", "Content-Type": "application/json",
                                       "X-DashScope-SSE": "disable"})
    except urllib.error.HTTPError as e:  # 11-18: a quiet piece is a 400 whose message says so, not a task code
        if e.code == 400 and any(c in e.read().decode(errors="replace") for c in NOTHING_SAID):
            return []
        raise
    # its ids hold within one request only, and a short turn's voiceprint can't tell whether an id is right: they only
    # cut the sentences, who said each is the diarist's alone (no speaker: no engine groups at relabel)
    return [{**g, "speaker": None} for g in sentences([{"sentences": by_turns((json.loads(r.content).get("output") or {}).get("sentences") or [])}])]
