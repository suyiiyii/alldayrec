"""The recordings: a stretch of a device's audio as WAV, and listening in on a device."""
import datetime as dt
import logging

import anyio.to_thread
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from alldayrec import audio
from alldayrec import gain
from alldayrec import store
from alldayrec import threads
from alldayrec.core import NO_CACHE

router = APIRouter()
log = logging.getLogger("live")


@router.get("/api/audio")
async def audio_wav(start: str, end: str, device: str | None = None):
    """WAV of [start, end] (+0.2 s each side) cut from the uploaded pieces of `device` (any when not given) covering it;
    X-Gain-Db (with a device): the gain its first sample of audio has over the stored audio (audio.play_db); a later
    since_ms of the device's offset inside the WAV changes it from there on, which this one number does not say."""
    placed = {}
    wav = await cut(start, end, "wav", device, placed)
    return Response(wav, media_type="audio/wav", headers={"X-Gain-Db": f"{audio.play_db(device, placed['first_ms']):g}"} if device else None)


@router.get("/api/listen")
async def listen(device: str, after: int | None = None):
    """Listening in on a device (the live page polls it): its audio after `after` (unix ms, the X-After of the answer
    before; none: its newest piece) as WAV, X-Start the first piece's start (X-Gain-Db: its gain, as /api/audio's); 204 when there is nothing newer. Decoded
    here, as /api/audio is: pieces are Ogg Opus (Safari before 18.4 cannot decode it) or the iPhone's AAC (each with
    its encoder's delay, trimmed only by decode()); WAV plays in every browser."""
    got = await anyio.to_thread.run_sync(audio.newer, device, after, limiter=threads.MEDIA)
    if got is None:
        return Response(status_code=204)
    start, last, x = got
    return Response(audio.wav(x), media_type="audio/wav", headers={"X-Start": str(start), "X-After": str(last),
                                                                   "X-Gain-Db": f"{audio.play_db(device, start):g}", **NO_CACHE})


@router.get("/api/device-gain")
async def device_gain():
    """Each device's gain rows (gain.py: since_ms on, capture_db, offset_db, source, note) and the level they calibrate
    to (target_dbov); read-only, set with server/gain.sh."""
    return {"target_dbov": gain.TARGET_DBOV, "rows": await anyio.to_thread.run_sync(gain.rows, limiter=threads.DB)}


async def cut(start: str, end: str, fmt: str, device: str | None = None, placed: dict | None = None):
    """audio.cut in a thread: bytes, or the samples for fmt pcm; HTTPException when there is none."""
    a, b = dt.datetime.fromisoformat(start), dt.datetime.fromisoformat(end)
    try:
        out = await anyio.to_thread.run_sync(audio.cut, a, b, fmt, device, placed, limiter=threads.MEDIA) if 0 < (b - a).total_seconds() <= 120 else None
    except audio.DecoderTerminated:  # a service stop: the page asks again; the speaker loops skip it as they skip a 404
        raise HTTPException(503, "Audio decoding was stopped; try again")
    except store.Unreachable as e:  # STORAGE=s3, the object store down: sealed audio cannot be read now
        log.warning("audio %s..%s: %s", start, end, e)  # the key and the store's answer: in the log, not to the client
        raise HTTPException(503, "Object storage unreachable, try again later")
    if out is None:
        raise HTTPException(404, "no recording covers this time")
    return out
