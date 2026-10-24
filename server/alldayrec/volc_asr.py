"""Volcengine (Ark Agent Plan) doubao-seed-asr-2.0 over its WebSocket binary protocol.

    set -a; . .env; set +a; uv run python -m alldayrec.volc_asr <audio file> [--mode nostream|async] [--pace 0]

transcribe(x, mode) sends 16 kHz int16 PCM in 200 ms packets and returns the utterances of the final
response: [{text, start_ms, end_ms, speaker?}]. nostream answers once per ~15 s of audio with the
high-accuracy result; async answers after every packet. VOLC_ASR_KEY is the Agent Plan API key.
"""
import argparse
import gzip
import json
import os
import subprocess
import sys
import time
import uuid

import anyio
import numpy as np

URL = "wss://openspeech.bytedance.com/api/v3/plan/sauc/bigmodel_{mode}"
RESOURCE = "volc.seedasr.sauc.duration"
SR = 16000
PACKET_MS = 200

FULL_CLIENT, AUDIO_ONLY, FULL_SERVER, SERVER_ERROR = 0b0001, 0b0010, 0b1001, 0b1111
POS_SEQ, NEG_WITH_SEQ = 0b0001, 0b0011
JSON_, GZIP_ = 0b0001, 0b0001


def frame(msg_type: int, flags: int, seq: int, payload: bytes) -> bytes:
    head = bytes([(0b0001 << 4) | 0b0001, (msg_type << 4) | flags, (JSON_ << 4) | GZIP_, 0])
    body = gzip.compress(payload)
    return head + seq.to_bytes(4, "big", signed=True) + len(body).to_bytes(4, "big") + body


def parse(data: bytes) -> dict:
    msg_type, flags, comp = data[1] >> 4, data[1] & 0x0F, data[2] & 0x0F
    p = 4
    out = {"type": msg_type}
    if msg_type == SERVER_ERROR:
        out["code"] = int.from_bytes(data[4:8], "big")
        p = 8
    elif flags & 0x01:
        out["seq"] = int.from_bytes(data[4:8], "big", signed=True)
        p = 8
    size = int.from_bytes(data[p:p + 4], "big")
    payload = data[p + 4:p + 4 + size]
    if comp == GZIP_:
        payload = gzip.decompress(payload)
    try:
        out["payload"] = json.loads(payload)
    except ValueError:
        out["payload"] = payload.decode(errors="replace")
    return out


def utterances(payload: dict | None) -> list[dict]:
    """The definite sentences of one answer: [{text, start_ms, end_ms, speaker?}]."""
    return [{"text": u["text"], "start_ms": u["start_time"], "end_ms": u["end_time"],
             **({"speaker": "SPEAKER_" + str(u["additions"]["speaker_id"])} if u.get("additions", {}).get("speaker_id") is not None else {})}
            for u in (payload or {}).get("result", {}).get("utterances", []) if u.get("definite", True)]


async def _session(x: np.ndarray, mode: str, pace_s: float, options: dict, consume, *, empty_packet: bool = False) -> dict:
    """Send one request while consuming answers; a reader failure cancels the sender."""
    import websockets
    headers = {"X-Api-Key": os.environ["VOLC_ASR_KEY"], "X-Api-Resource-Id": RESOURCE,
               "X-Api-Connect-Id": str(uuid.uuid4()), "X-Api-Request-Id": str(uuid.uuid4())}
    req = {"user": {"uid": "live-record"},
           "audio": {"format": "pcm", "codec": "raw", "rate": SR, "bits": 16, "channel": 1},
           "request": {"model_name": "bigmodel", "enable_itn": True, "enable_punc": True,
                       "show_utterances": True, **options}}
    step = SR * PACKET_MS // 1000
    packets = [x[i:i + step].tobytes() for i in range(0, len(x), step)]
    if empty_packet and not packets:
        packets = [b""]
    responses, t0 = 0, time.perf_counter()
    # ping_interval=None: while packets pour in faster than real time the server does not answer pings in time
    async with websockets.connect(URL.format(mode=mode), additional_headers=headers, max_size=None, ping_interval=None) as ws:
        await ws.send(frame(FULL_CLIENT, POS_SEQ, 1, json.dumps(req).encode()))
        first = parse(await ws.recv())
        if first["type"] == SERVER_ERROR:
            raise RuntimeError(f"{first['code']}: {first['payload']}")
        logid = ws.response.headers.get("X-Tt-Logid") if hasattr(ws, "response") else None

        async def reader():
            nonlocal responses
            async for data in ws:
                r = parse(data)
                responses += 1
                if r["type"] == SERVER_ERROR:
                    raise RuntimeError(f"{r['code']}: {r['payload']}")
                consume(r)
                if r.get("seq", 0) < 0:  # the answer to the last packet
                    return

        async with anyio.create_task_group() as tg:  # the reader failing stops the sending
            tg.start_soon(reader)
            for i, pk in enumerate(packets, start=2):
                end = i == len(packets) + 1
                await ws.send(frame(AUDIO_ONLY, NEG_WITH_SEQ if end else POS_SEQ, -i if end else i, pk))
                if pace_s:
                    await anyio.sleep(pace_s)
            tg.cancel_scope.deadline = anyio.current_time() + 120  # the answer to the last packet
        if tg.cancel_scope.cancelled_caught:
            raise TimeoutError  # as asyncio.wait_for did: the calls table keeps "TimeoutError: "
    return {"seconds": round(time.perf_counter() - t0, 1), "responses": responses, "logid": logid}


async def _run(x: np.ndarray, mode: str, pace_s: float, speakers: bool, log=None) -> tuple[list[dict], dict]:
    last = None

    def consume(r):
        nonlocal last
        if isinstance(r.get("payload"), dict) and "result" in r["payload"]:
            last = r["payload"]
            if log:
                log(r)

    meta = await _session(x, mode, pace_s,
                          {"result_type": "full", **({"enable_speaker_info": True} if speakers else {})}, consume)
    return utterances(last), {**meta, "text": (last or {}).get("result", {}).get("text", "")}


async def _stream(x: np.ndarray, pace_s: float, end_window_ms: int | None = None) -> tuple[list[dict], dict]:
    """bigmodel_async with two-pass recognition: the server cuts sentences (800 ms of silence) and re-reads each with
    the nostream model; only those carry definite, and result_type single sends each sentence once."""
    utts, untimed = [], []  # untimed: definite results without times (seen on 05-26), not usable

    def consume(r):
        p = r.get("payload")
        for u in (p.get("result", {}).get("utterances") or [] if isinstance(p, dict) else []):
            if u.get("definite") and "start_time" not in u:
                untimed.append(u.get("text", ""))
            elif u.get("definite"):
                utts.extend(utterances({"result": {"utterances": [u]}}))

    meta = await _session(x, "async", pace_s,
                          {"result_type": "single", "enable_nonstream": True, "enable_speaker_info": True,
                           **({"end_window_size": end_window_ms} if end_window_ms else {})},
                          consume, empty_packet=True)
    return utts, {k: meta[k] for k in ("seconds", "logid")} | {"untimed": untimed}


async def stream(x: np.ndarray, pace_s: float = 0.02, end_window_ms: int | None = None):
    """One stretch of speech through one two-pass streaming session: its definite sentences (ms from x[0])."""
    with anyio.fail_after(30 + len(x) / SR * (pace_s * 5 + 0.3)):
        return await _stream(x, pace_s, end_window_ms)


async def transcribe(x: np.ndarray, mode: str = "nostream", pace_s: float = 0.02, speakers: bool = True, partial=None):  # 0.02: 10x real time
    """Bounded: a stalled connection (send blocked, no answer) must fail so the caller retries next pass.
    partial(utts): called with the definite sentences so far each time an answer adds some (nostream answers every
    ~4 s while a window goes in at 10x, each ~15-20 s of audio further: a 5 min window's first sentences in ~3 s)."""
    budget = 30 + len(x) / SR * (pace_s * 5 + 0.3)  # sending time plus the engine's ~0.25x real time, with headroom
    seen = [0]

    def log(r):
        us = utterances(r["payload"])
        if partial and len(us) > seen[0]:
            seen[0] = len(us)
            partial(us)
    with anyio.fail_after(budget):
        return await _run(x, mode, pace_s, speakers, log)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("--mode", default="nostream", choices=["nostream", "async"])
    ap.add_argument("--pace", type=float, default=0.0, help="seconds between 200 ms packets (0.2 = real time)")
    ap.add_argument("--no-speakers", action="store_true")
    a = ap.parse_args()
    x = np.frombuffer(subprocess.run(["ffmpeg", "-v", "error", "-i", a.audio, "-f", "s16le", "-ac", "1", "-ar", str(SR), "-"],
                                     capture_output=True, check=True).stdout, np.int16)
    utts, info = anyio.run(_run, x, a.mode, a.pace, not a.no_speakers, lambda r: print(json.dumps(r["payload"], ensure_ascii=False)[:200], file=sys.stderr))
    print(json.dumps(info, ensure_ascii=False))
    for u in utts:
        print(f"{u['start_ms'] / 1000:7.1f} {u['end_ms'] / 1000:7.1f} {u.get('speaker', '-'):>3} {u['text']}")


if __name__ == "__main__":
    main()
