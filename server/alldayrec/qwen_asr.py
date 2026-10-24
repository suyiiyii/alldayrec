"""Alibaba Bailian (DashScope) qwen-audio-3.1-asr-flash-filetrans for engine_segs: text, sentences and speakers from one
async file job. On the five GT cases (2026-10-19, the same ≤ 5 min blocks) its text beat StepFun's SSE on four (home
11.6% CER vs 13.6%), its speakers about as StepFun's file job; billed by tokens, ~¥0.05 an hour of audio.

The window goes up lossless (16 kHz wav: StepFun's file job lost ~3 points CER on 16 kbps Opus) into Bailian's own
temporary storage (an oss:// URL for 48 h, bound to the model; free) rather than the upload bridge: 10-20 the bridge
through the proxy timed out for hours, and Bailian fetching from it took 2–5 s more a job. The docs say the storage is
not for production (100 QPS of policies per account and model); a window it fails goes to ENGINE_FALLBACK like any
other failure. The built-in sensitive-word masking (words turned into ***) is off.
"""
import io
import json
import os
import time
import wave

import anyio
import numpy as np

from alldayrec import net

MODEL = os.environ.get("QWEN_ASR_MODEL") or "qwen-audio-3.1-asr-flash-filetrans"
BASE = "https://dashscope.aliyuncs.com/api/v1"
POLL_S = 2
WAIT_S = 900
NOTHING_SAID = ("ASR_RESPONSE_HAVE_NO_WORDS", "SUCCESS_WITH_NO_VALID_FRAGMENT")  # 10-19: a quiet window; one under a second


def wav(x: np.ndarray) -> bytes:
    """16 kHz int16 mono PCM as a wav file."""
    f = io.BytesIO()
    with wave.open(f, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(x.astype(np.int16).tobytes())
    return f.getvalue()


def sentences(transcripts: list[dict]) -> list[dict]:
    """A transcription's sentences as engine_segs takes them, speaker ids as SPEAKER_<n> (None without one)."""
    return [{"start_ms": s["begin_time"], "end_ms": s["end_time"], "text": s["text"],
             "speaker": None if s.get("speaker_id") is None else f"SPEAKER_{s['speaker_id']}"}
            for t in transcripts[:1] for s in t.get("sentences", []) if s.get("text", "").strip()]


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


async def transcribe(x: np.ndarray) -> list[dict]:
    """x: 16 kHz int16 PCM. Raises on an HTTP error, a failed task, or one still running after WAIT_S."""
    auth = {"Authorization": f"Bearer {os.environ['DASHSCOPE_API_KEY']}"}
    url = await upload(wav(x), auth)
    auth = {**auth, "Content-Type": "application/json"}
    body = {"model": MODEL, "input": {"file_urls": [url]}, "parameters": {
        "channel_id": [0], "language_hints": ["zh", "en"], "diarization_enabled": True,
        "special_word_filter": json.dumps({"system_reserved_filter": False})}}
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
