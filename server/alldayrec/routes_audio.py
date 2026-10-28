"""The recordings: a stretch of a device's audio as WAV, and listening in on a device."""
import datetime as dt

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from alldayrec import audio
from alldayrec import threads
from alldayrec.core import NO_CACHE

router = APIRouter()


@router.get("/api/audio")
async def audio_wav(start: str, end: str, device: str | None = None):
    """WAV of [start, end] (+0.2 s each side) cut from the uploaded pieces of `device` (any when not given) covering it."""
    wav = await cut(start, end, "wav", device)
    return Response(wav, media_type="audio/wav")


@router.get("/api/listen")
async def listen(device: str, after: int | None = None):
    """Listening in on a device (the live page polls it): its audio after `after` (unix ms, the X-After of the answer
    before; none: its newest piece) as WAV, X-Start the first piece's start; 204 when there is nothing newer. Decoded
    here, as /api/audio is: pieces are Ogg Opus (Safari before 18.4 cannot decode it) or the iPhone's AAC (each with
    its encoder's delay, trimmed only by decode()); WAV plays in every browser."""
    got = await anyio.to_thread.run_sync(audio.newer, device, after, limiter=threads.MEDIA)
    if got is None:
        return Response(status_code=204)
    start, last, x = got
    return Response(audio.wav(x), media_type="audio/wav", headers={"X-Start": str(start), "X-After": str(last), **NO_CACHE})


async def cut(start: str, end: str, fmt: str, device: str | None = None) -> bytes:
    a, b = dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)
    try:
        out = await anyio.to_thread.run_sync(audio.cut, a, b, fmt, device, limiter=threads.MEDIA) if 0 < (b - a).total_seconds() <= 120 else None
    except audio.DecoderTerminated:  # a service stop: the page asks again; the speaker loops skip it as they skip a 404
        raise HTTPException(503, "Audio decoding was stopped; try again")
    if out is None:
        raise HTTPException(404, "no recording covers this time")
    return out
